from __future__ import annotations

import dataclasses
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

import numpy as np

from dreamsync.audio.system_input import _require_sounddevice
from dreamsync.director import Director, DirectorConfig
from dreamsync.dsp.features import _estimate_bpm, _estimate_bpm_from_beats, _smooth_signal
from dreamsync.effects import EffectCycler, EffectCyclerConfig
from dreamsync.mood import MoodClassifier
from dreamsync.output.govee_lan import GoveeLanAdapter, MultiGoveeLanAdapter
from dreamsync.render import RenderMode, SegmentRenderer


class LiveBpmEstimator:
    def __init__(
        self,
        sample_rate: int,
        hop_size: int,
        window_seconds: float = 20.0,
        min_update_interval: float = 0.5,
        beat_threshold_percentile: float = 65.0,
        beat_threshold_std_mult: float = 0.15,
        min_bpm: float = 80.0,
        max_bpm: float = 200.0,
        max_jump_bpm: float = 6.0,
        confirm_updates: int = 6,
        half_time: bool = False,
        onset_mode: str = "hybrid",
        threshold_mode: str = "adaptive",
    ) -> None:
        self.sample_rate = sample_rate
        self.hop_size = hop_size
        self.window_seconds = window_seconds
        self.min_update_interval = min_update_interval
        self.beat_threshold_percentile = beat_threshold_percentile
        self.beat_threshold_std_mult = beat_threshold_std_mult
        self.min_bpm = min_bpm
        self.max_bpm = max_bpm
        self.max_jump_bpm = max_jump_bpm
        self.confirm_updates = max(1, confirm_updates)
        self.half_time = half_time
        _valid_onset_modes = ("spectral_flux", "bass_diff", "kick_flux", "whitened_flux", "hybrid")
        if onset_mode not in _valid_onset_modes:
            raise ValueError(f"onset_mode must be one of {_valid_onset_modes}, got {onset_mode!r}")
        self.onset_mode = onset_mode
        if threshold_mode not in ("adaptive", "global"):
            raise ValueError(f"threshold_mode must be 'adaptive' or 'global', got {threshold_mode!r}")
        self.threshold_mode = threshold_mode
        self.max_frames = max(8, int(window_seconds * sample_rate / hop_size))
        self.min_frames = max(8, int(5.0 * sample_rate / hop_size))
        self.onset_env: deque[float] = deque()
        self.prev_rms = 0.0
        self.last_bpm = 0.0
        self.last_update_t = -1e9
        self.last_beat_idx = -1
        self.last_onset_mean = 0.0
        self.last_onset_std = 0.0
        self.last_thresh = 0.0
        self._beat_phase = 0.0
        self._candidate_bpm = 0.0
        self._candidate_hits = 0
        self._last_onset_beat_t = -1e9
        self._prev_onset = 0.0
        # Hybrid onset mode state: EMA of bass onset activity vs kick flux activity
        self._bass_activity = 0.0
        self._kick_activity = 0.0
        self._hybrid_source = "bass"  # current active source in hybrid mode

    def reset(self) -> None:
        """Clear accumulated state for a new song."""
        self.onset_env.clear()
        self.prev_rms = 0.0
        self.last_bpm = 0.0
        self.last_update_t = -1e9
        self.last_beat_idx = -1
        self.last_onset_mean = 0.0
        self.last_onset_std = 0.0
        self.last_thresh = 0.0
        self._beat_phase = 0.0
        self._candidate_bpm = 0.0
        self._candidate_hits = 0
        self._last_onset_beat_t = -1e9
        self._prev_onset = 0.0
        self._bass_activity = 0.0
        self._kick_activity = 0.0
        self._hybrid_source = "bass"

    def update(
        self,
        energy: float,
        t: float,
        spectral_flux: float = 0.0,
        kick_spectral_flux: float = 0.0,
        whitened_flux: float = 0.0,
    ) -> tuple[float, bool]:
        bass_onset = max(0.0, energy - self.prev_rms)
        self.prev_rms = energy
        if self.onset_mode == "spectral_flux":
            onset = spectral_flux
        elif self.onset_mode == "kick_flux":
            onset = kick_spectral_flux if kick_spectral_flux > 0 else spectral_flux
        elif self.onset_mode == "whitened_flux":
            onset = whitened_flux
        elif self.onset_mode == "hybrid":
            onset = self._hybrid_onset(bass_onset, kick_spectral_flux)
        else:
            onset = bass_onset
        self.last_onset = onset
        self.onset_env.append(onset)
        while len(self.onset_env) > self.max_frames:
            self.onset_env.popleft()
            if self.last_beat_idx >= 0:
                self.last_beat_idx -= 1
                if self.last_beat_idx < 0:
                    self.last_beat_idx = -1

        if len(self.onset_env) >= self.min_frames and (
            t - self.last_update_t >= self.min_update_interval
        ):
            onset_arr = np.asarray(self.onset_env, dtype=np.float32)
            onset_arr = _smooth_signal(onset_arr, width=5)
            if self.threshold_mode == "adaptive":
                beat_idx = self._detect_beats_adaptive(onset_arr)
            else:
                beat_idx = self._detect_beats_live(onset_arr)
            bpm_from_beats = _estimate_bpm_from_beats(
                beat_idx, self.hop_size, self.sample_rate
            )
            bpm_from_corr = _estimate_bpm(onset_arr, self.hop_size, self.sample_rate)
            if bpm_from_beats > 0 and bpm_from_corr > 0:
                # Weight beat-based estimate by number of detected beats.
                # Fewer than 4 beats → low confidence, lean on autocorrelation.
                # 8+ beats → high confidence, trust beat intervals.
                beat_confidence = min(1.0, beat_idx.size / 8.0)
                w_beats = 0.5 + 0.4 * beat_confidence   # 0.5–0.9
                w_corr = 1.0 - w_beats                   # 0.1–0.5
                bpm = w_beats * bpm_from_beats + w_corr * bpm_from_corr
            else:
                bpm = bpm_from_beats if bpm_from_beats > 0 else bpm_from_corr
            if bpm > 0.0:
                bpm = self._normalize_bpm(bpm)
                bpm = self._snap_to_last(bpm)
                self.last_bpm = self._apply_inertia(bpm)
            self.last_update_t = t
            if beat_idx.size > 0:
                self.last_beat_idx = int(beat_idx[-1])

        beat = self._advance_beat_phase()
        return self.last_bpm, beat

    def _normalize_bpm(self, bpm: float) -> float:
        if bpm <= 0.0:
            return 0.0
        while bpm < self.min_bpm:
            bpm *= 2.0
        while bpm > self.max_bpm:
            bpm *= 0.5
        return bpm

    def _snap_to_last(self, bpm: float) -> float:
        if self.last_bpm <= 0.0 or bpm <= 0.0:
            return bpm
        candidates = [bpm, bpm * 2.0, bpm * 0.5]
        candidates = [self._normalize_bpm(c) for c in candidates]
        best = min(candidates, key=lambda v: abs(v - self.last_bpm))
        return best

    def _apply_inertia(self, bpm: float) -> float:
        if self.last_bpm <= 0.0:
            self._candidate_bpm = 0.0
            self._candidate_hits = 0
            return bpm
        if abs(bpm - self.last_bpm) <= self.max_jump_bpm:
            self._candidate_bpm = 0.0
            self._candidate_hits = 0
            # EMA smooth small changes to prevent oscillation.
            # Persistent changes converge within ~5 updates (~2.5s).
            return self.last_bpm * 0.7 + bpm * 0.3
        # Require a few consistent updates before accepting a big jump.
        if self._candidate_bpm <= 0.0 or abs(bpm - self._candidate_bpm) > self.max_jump_bpm:
            self._candidate_bpm = bpm
            self._candidate_hits = 1
            return self.last_bpm
        self._candidate_hits += 1
        # Scale confirmation requirement by jump magnitude:
        # 6 BPM jump → base confirms, 30+ BPM jump → 2x confirms
        jump_ratio = min(2.0, abs(bpm - self.last_bpm) / (self.max_jump_bpm * 3))
        required = int(self.confirm_updates * (1.0 + jump_ratio))
        if self._candidate_hits >= required:
            self._candidate_bpm = 0.0
            self._candidate_hits = 0
            return bpm
        return self.last_bpm

    def _is_onset_beat(self, onset: float, t: float) -> bool:
        """Detect a beat from an actual energy spike, not a synthetic phase.

        Fires when the current onset exceeds the adaptive threshold AND
        the onset is rising (current > previous), with a minimum gap between
        beats to avoid double-triggers.
        """
        is_beat = False
        # Need a valid threshold from at least one BPM analysis pass
        if self.last_thresh > 0 and onset > self.last_thresh:
            # Must be a rising edge (onset bigger than previous frame)
            if onset > self._prev_onset:
                # Minimum gap: 60% of a beat period, or 150ms floor
                if self.last_bpm > 0:
                    min_gap = max(0.15, (60.0 / self.last_bpm) * 0.6)
                else:
                    min_gap = 0.2
                if (t - self._last_onset_beat_t) >= min_gap:
                    self._last_onset_beat_t = t
                    is_beat = True
        self._prev_onset = onset
        return is_beat

    def _hybrid_onset(
        self,
        bass_onset: float,
        kick_flux: float,
        alpha: float = 0.05,
        switch_ratio: float = 3.0,
    ) -> float:
        """Choose between bass_diff and kick_flux based on signal activity.

        Uses bass_diff (best noise rejection) as the primary source.
        Switches to kick_flux only when bass onset activity is very low
        relative to kick flux activity — meaning bass_diff isn't picking
        up the beats but the kick band still has rhythmic content.
        """
        self._bass_activity = alpha * bass_onset + (1.0 - alpha) * self._bass_activity
        self._kick_activity = alpha * kick_flux + (1.0 - alpha) * self._kick_activity

        if self._bass_activity > 0 and self._kick_activity / (self._bass_activity + 1e-12) > switch_ratio:
            # Kick band has much more activity than broadband bass diff —
            # bass_diff is probably missing beats, use kick_flux
            self._hybrid_source = "kick"
            return kick_flux
        else:
            self._hybrid_source = "bass"
            return bass_onset

    def _advance_beat_phase(self) -> bool:
        if self.last_bpm <= 0.0:
            return False
        effective_bpm = self.last_bpm * 0.5 if self.half_time else self.last_bpm
        beat_inc = (self.hop_size / self.sample_rate) * (effective_bpm / 60.0)
        self._beat_phase += beat_inc
        if self._beat_phase >= 1.0:
            self._beat_phase -= 1.0
            return True
        return False

    def _detect_beats_live(self, onset_env: np.ndarray) -> np.ndarray:
        if onset_env.size < 3:
            return np.array([], dtype=int)
        mean = float(onset_env.mean())
        std = float(onset_env.std())
        thresh = float(np.percentile(onset_env, self.beat_threshold_percentile)) + (
            self.beat_threshold_std_mult * std
        )
        peaks = (onset_env[1:-1] > onset_env[:-2]) & (onset_env[1:-1] >= onset_env[2:])
        peaks &= onset_env[1:-1] > thresh
        peak_idx = (np.where(peaks)[0] + 1).tolist()
        if not peak_idx:
            self.last_onset_mean = mean
            self.last_onset_std = std
            self.last_thresh = thresh
            return np.array([], dtype=int)

        min_gap_frames = max(1, int((self.sample_rate * 0.15) / self.hop_size))
        deduped: list[int] = [peak_idx[0]]
        for idx in peak_idx[1:]:
            if idx - deduped[-1] >= min_gap_frames:
                deduped.append(idx)

        self.last_onset_mean = mean
        self.last_onset_std = std
        self.last_thresh = thresh
        return np.array(deduped, dtype=int)

    def _detect_beats_adaptive(
        self,
        onset_env: np.ndarray,
        window_seconds: float = 0.5,
        multiplier: float = 1.8,
        offset: float = 0.0,
    ) -> np.ndarray:
        """Detect beats using a local adaptive threshold (median of surrounding frames)."""
        if onset_env.size < 3:
            return np.array([], dtype=int)

        n = onset_env.size
        w = max(1, int(window_seconds * self.sample_rate / self.hop_size))

        # Compute local median threshold for each frame
        local_thresh = np.empty(n, dtype=np.float32)
        for i in range(n):
            lo = max(0, i - w)
            hi = min(n, i + w + 1)
            local_thresh[i] = float(np.median(onset_env[lo:hi])) * multiplier + offset

        # Find local maxima that exceed adaptive threshold
        peaks = (onset_env[1:-1] > onset_env[:-2]) & (onset_env[1:-1] >= onset_env[2:])
        peaks &= onset_env[1:-1] > local_thresh[1:-1]
        peak_idx = (np.where(peaks)[0] + 1).tolist()

        if not peak_idx:
            self.last_onset_mean = float(onset_env.mean())
            self.last_onset_std = float(onset_env.std())
            self.last_thresh = float(local_thresh.mean())
            return np.array([], dtype=int)

        # De-duplicate with minimum gap
        min_gap_frames = max(1, int((self.sample_rate * 0.15) / self.hop_size))
        deduped: list[int] = [peak_idx[0]]
        for idx in peak_idx[1:]:
            if idx - deduped[-1] >= min_gap_frames:
                deduped.append(idx)

        self.last_onset_mean = float(onset_env.mean())
        self.last_onset_std = float(onset_env.std())
        self.last_thresh = float(local_thresh.mean())
        return np.array(deduped, dtype=int)


class SongBoundaryDetector:
    """Detects silence gaps between songs in continuous audio playback."""

    def __init__(
        self,
        silence_threshold_rms: float = 0.005,
        min_silence_seconds: float = 0.8,
        min_song_seconds: float = 45.0,
        hop_size: int = 512,
        sample_rate: int = 44100,
    ) -> None:
        self.silence_threshold_rms = silence_threshold_rms
        self.min_silence_frames = int(min_silence_seconds * sample_rate / hop_size)
        self.min_song_frames = int(min_song_seconds * sample_rate / hop_size)
        self._silent_frames = 0
        self._frames_since_reset = 0
        self._boundary_count = 0

    def update(self, rms: float) -> bool:
        """Feed one frame's RMS. Returns True on song boundary detection."""
        self._frames_since_reset += 1
        if rms < self.silence_threshold_rms:
            self._silent_frames += 1
        else:
            if (
                self._silent_frames >= self.min_silence_frames
                and self._frames_since_reset >= self.min_song_frames
            ):
                # Silence gap ended — this is the start of a new song
                self._silent_frames = 0
                self._frames_since_reset = 0
                self._boundary_count += 1
                return True
            self._silent_frames = 0
        return False

    @property
    def boundary_count(self) -> int:
        return self._boundary_count


@dataclass
class SpectralFeatures:
    bass: float
    bass_ratio: float
    spectral_flux: float
    kick_energy: float
    kick_ratio: float
    kick_spectral_flux: float
    mag: np.ndarray


def _prepare_bass_window(
    frame_size: int,
    sample_rate: int,
    kick_low: float = 50.0,
    kick_high: float = 130.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (hanning_window, bass_mask, kick_mask).

    bass_mask: freqs <= 200 Hz (broadband bass).
    kick_mask: kick_low <= freqs <= kick_high (narrow kick band).
    """
    window = np.hanning(frame_size).astype(np.float32)
    freqs = np.fft.rfftfreq(frame_size, d=1.0 / sample_rate)
    bass_mask = freqs <= 200.0
    kick_mask = (freqs >= kick_low) & (freqs <= kick_high)
    return window, bass_mask, kick_mask


def _spectral_features(
    frame: np.ndarray,
    window: np.ndarray,
    mask: np.ndarray,
    prev_mag: np.ndarray | None,
    kick_mask: np.ndarray | None = None,
) -> SpectralFeatures:
    spectrum = np.fft.rfft(frame * window)
    mag = np.abs(spectrum)
    bass = float(mag[mask].sum())
    total = float(mag.sum()) + 1e-8
    bass_ratio = float(mag[mask].sum()) / total
    if prev_mag is not None:
        spectral_flux = float(np.maximum(0.0, mag - prev_mag).sum())
    else:
        spectral_flux = 0.0

    # Kick-band features
    if kick_mask is not None:
        kick_energy = float(mag[kick_mask].sum())
        kick_ratio = kick_energy / (bass + 1e-8)
        if prev_mag is not None:
            kick_spectral_flux = float(np.maximum(0.0, mag[kick_mask] - prev_mag[kick_mask]).sum())
        else:
            kick_spectral_flux = 0.0
    else:
        kick_energy = 0.0
        kick_ratio = 0.0
        kick_spectral_flux = 0.0

    return SpectralFeatures(
        bass=bass, bass_ratio=bass_ratio, spectral_flux=spectral_flux,
        kick_energy=kick_energy, kick_ratio=kick_ratio,
        kick_spectral_flux=kick_spectral_flux, mag=mag,
    )


def _compute_whitened_flux(
    mag: np.ndarray,
    spectral_mean: np.ndarray | None,
    prev_whitened_mag: np.ndarray | None,
    alpha: float = 0.05,
) -> tuple[float, np.ndarray, np.ndarray]:
    """Compute spectral flux on a pre-whitened (normalized) spectrum.

    Returns (whitened_flux, updated_spectral_mean, whitened_mag).
    """
    if spectral_mean is None:
        spectral_mean = mag.copy()
    else:
        spectral_mean = alpha * mag + (1.0 - alpha) * spectral_mean

    whitened_mag = mag / (spectral_mean + 1e-8)

    if prev_whitened_mag is not None:
        whitened_flux = float(np.maximum(0.0, whitened_mag - prev_whitened_mag).sum())
    else:
        whitened_flux = 0.0

    return whitened_flux, spectral_mean, whitened_mag


def _feature_row_from_frame(
    frame: np.ndarray,
    rms: float,
    t: float,
    bpm: float,
    beat: bool,
    bass: float = 0.0,
    bass_ratio: float = 0.0,
    spectral_flux: float = 0.0,
    onset_strength: float = 0.0,
) -> dict[str, float | bool]:
    signs = np.sign(frame)
    zcr = float(np.mean(np.abs(np.diff(signs)) > 0))
    return {
        "t": t,
        "rms": rms,
        "zcr": zcr,
        "centroid": 0.0,
        "bass": float(bass),
        "bass_ratio": float(bass_ratio),
        "spectral_flux": float(spectral_flux),
        "onset_strength": float(onset_strength),
        "beat": bool(beat),
        "bpm": float(bpm),
    }


def run_live_to_govee(
    multi_adapter: MultiGoveeLanAdapter,
    duration_seconds: float | None,
    sample_rate: int = 44100,
    channels: int = 1,
    device: int | None = None,
    frame_size: int = 2048,
    hop_size: int = 512,
    telemetry_interval_seconds: float = 1.0,
    blocksize: int = 1024,
    director_config: DirectorConfig | None = None,
    half_time: bool = False,
    max_brightness: bool = False,
    auto_cycle: bool = True,
    cycle_interval: float = 16.0,
    debug_mood: bool = False,
    stop_event: threading.Event | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Audio capture → beat detection → renderer → Govee UDP streaming.

    Drives one or more Govee devices via a MultiGoveeLanAdapter.
    Each device has its own renderer and role for independent rendering.

    If *duration_seconds* is None the loop runs until *stop_event* is set
    (or forever if no stop_event is provided either).
    """
    if duration_seconds is not None and duration_seconds <= 0:
        raise ValueError("duration_seconds must be > 0")
    if sample_rate <= 0:
        raise ValueError("sample_rate must be > 0")
    if channels <= 0:
        raise ValueError("channels must be > 0")
    if frame_size <= 0:
        raise ValueError("frame_size must be > 0")
    if hop_size <= 0:
        raise ValueError("hop_size must be > 0")

    sd = _require_sounddevice()
    audio_queue: deque[np.ndarray] = deque()
    logs: list[dict[str, Any]] = []
    bpm_estimator = LiveBpmEstimator(
        sample_rate=sample_rate, hop_size=hop_size, half_time=half_time,
    )
    song_detector = SongBoundaryDetector(
        hop_size=hop_size, sample_rate=sample_rate,
    )
    director = Director(director_config)
    mood_classifier = MoodClassifier() if auto_cycle else None
    effect_cycler = EffectCycler(EffectCyclerConfig(cycle_interval=cycle_interval)) if auto_cycle else None
    current_params: dict | None = None
    beat_count = 0
    sent_count = 0
    dropped_blocks = 0
    captured_samples = 0

    # Seed with an initial intent so we always have something to render
    last_intent = director.update({"t": 0.0, "rms": 0.0, "zcr": 0.0, "bpm": 120.0, "beat": False, "bass": 0.0})

    def _callback(indata, frames, time_info, status) -> None:
        del frames, time_info
        nonlocal dropped_blocks, captured_samples
        if status and getattr(status, "input_overflow", False):
            dropped_blocks += 1
        mono = indata.mean(axis=1) if indata.ndim == 2 else indata.reshape(-1)
        arr = np.asarray(mono, dtype=np.float32).copy()
        captured_samples += int(arr.shape[0])
        audio_queue.append(arr)

    stream_t = 0.0
    buffer = np.zeros(0, dtype=np.float32)
    started_at = time.monotonic()
    next_telemetry = started_at + max(0.1, telemetry_interval_seconds)
    last_print = started_at
    window, bass_mask, kick_mask = _prepare_bass_window(frame_size, sample_rate)
    prev_mag: np.ndarray | None = None
    spectral_mean: np.ndarray | None = None
    prev_whitened_mag: np.ndarray | None = None

    # Activate all devices (activate() includes its own delays)
    multi_adapter.activate(brightness=100)

    with sd.InputStream(
        samplerate=sample_rate,
        channels=channels,
        device=device,
        dtype="float32",
        blocksize=blocksize,
        callback=_callback,
    ):
        while True:
            if stop_event is not None and stop_event.is_set():
                break
            now = time.monotonic()
            elapsed = now - started_at
            if duration_seconds is not None and elapsed >= duration_seconds:
                break

            while audio_queue:
                chunk = audio_queue.popleft()
                buffer = np.concatenate([buffer, chunk])

            # Process audio frames for beat detection + feature extraction
            beat_this_tick = False
            last_features: dict[str, float | bool] | None = None
            while buffer.shape[0] >= frame_size:
                frame = buffer[:frame_size]
                buffer = buffer[hop_size:]
                rms = float(np.sqrt(np.mean(frame**2)))
                if song_detector.update(rms):
                    # Song boundary: reset all state
                    bpm_estimator.reset()
                    director.reset()
                    if mood_classifier is not None:
                        mood_classifier.reset()
                    if effect_cycler is not None:
                        effect_cycler.reset()
                    prev_mag = None
                    spectral_mean = None
                    prev_whitened_mag = None
                    if debug_mood:
                        print(
                            f"*** Song boundary detected "
                            f"(#{song_detector.boundary_count}) "
                            f"— state reset ***"
                        )
                sf = _spectral_features(frame, window, bass_mask, prev_mag, kick_mask=kick_mask)
                prev_mag = sf.mag
                wf, spectral_mean, prev_whitened_mag = _compute_whitened_flux(
                    sf.mag, spectral_mean, prev_whitened_mag,
                )
                bpm, beat = bpm_estimator.update(
                    sf.bass, stream_t,
                    spectral_flux=sf.spectral_flux,
                    kick_spectral_flux=sf.kick_spectral_flux,
                    whitened_flux=wf,
                )
                if half_time and bpm > 0:
                    bpm *= 0.5
                last_features = _feature_row_from_frame(
                    frame, rms, stream_t, bpm, beat,
                    bass=sf.bass,
                    bass_ratio=sf.bass_ratio,
                    spectral_flux=sf.spectral_flux,
                    onset_strength=bpm_estimator.last_onset,
                )
                last_intent = director.update(last_features)
                if beat:
                    beat_count += 1
                    beat_this_tick = True
                stream_t += float(hop_size) / float(sample_rate)

            # --- Effect cycling (mood → preset → palette/mode swap) ---
            if auto_cycle and mood_classifier is not None and effect_cycler is not None and last_features is not None:
                mood = mood_classifier.update(
                    director.energy, director.stability,
                    director.effective_bpm, stream_t,
                )
                preset = effect_cycler.update(
                    mood, stream_t, beat_this_tick,
                    bpm_estimator.last_bpm, director.energy,
                )
                # Swap render mode on all devices
                for _, renderer, _ in multi_adapter.devices:
                    renderer.mode = preset.render_mode
                # Swap director palette
                director.set_colors(preset.color_palette)
                current_params = preset.params
                if debug_mood:
                    print(
                        f"mood={mood.value} effect={preset.name} "
                        f"palette={effect_cycler.current_palette} "
                        f"mode={preset.render_mode.value} "
                        f"energy={director.energy:.4f} "
                        f"stability={director.stability:.4f} "
                        f"bpm={director.effective_bpm:.1f}"
                    )

            # Render and send a frame on every tick (animation-driven)
            if last_intent is not None:
                frame_intent = last_intent
                if max_brightness:
                    frame_intent = dataclasses.replace(frame_intent, intensity=1.0)
                sent = multi_adapter.send_frame(elapsed, frame_intent, beat=beat_this_tick, params=current_params)
                if sent:
                    sent_count += 1
                log_row: dict[str, Any] = {
                    "kind": "frame",
                    "t": round(stream_t, 4),
                    "elapsed_wall": round(elapsed, 4),
                    "bpm": round(float(bpm_estimator.last_bpm), 2),
                    "beat": bool(beat_this_tick),
                    "sent": bool(sent),
                    "color": last_intent.color,
                    "mode": last_intent.mode.value,
                    "devices": len(multi_adapter.devices),
                }
                if last_features is not None:
                    log_row["rms"] = round(float(last_features["rms"]), 5)
                    log_row["zcr"] = round(float(last_features["zcr"]), 5)
                    log_row["bass"] = round(float(last_features["bass"]), 5)
                if auto_cycle and mood_classifier is not None and effect_cycler is not None:
                    log_row["mood"] = mood_classifier.mood.value
                    log_row["effect"] = effect_cycler.current_effect
                logs.append(log_row)

            if now >= next_telemetry:
                logs.append(
                    {
                        "kind": "telemetry",
                        "elapsed_wall": round(elapsed, 3),
                        "samples_captured": int(captured_samples),
                        "dropped_blocks": int(dropped_blocks),
                        "queue_chunks": int(len(audio_queue)),
                    }
                )
                next_telemetry += max(0.1, telemetry_interval_seconds)
                if now - last_print >= 1.0:
                    beat_rate = beat_count / elapsed if elapsed > 1.0 else 0.0
                    eff_bpm = bpm_estimator.last_bpm * (0.5 if half_time else 1.0)
                    expected_rate = eff_bpm / 60.0
                    last_print = now
                    print(
                        f"govee t={elapsed:.1f}s "
                        f"beats={beat_count} ({beat_rate:.1f}/s, expect {expected_rate:.1f}/s) "
                        f"sent={sent_count} dropped={dropped_blocks} "
                        f"bpm={eff_bpm:.1f} (raw={bpm_estimator.last_bpm:.1f}) "
                        f"mode={director.mode.value} "
                        f"devices={len(multi_adapter.devices)} "
                        f"color={last_intent.color if last_intent else '-'}"
                    )
            time.sleep(0.005)  # ~200Hz tick for responsive rendering

    # Stop BLE follower threads
    multi_adapter.deactivate()

    actual_duration = time.monotonic() - started_at
    ble_count = len(getattr(multi_adapter, "_ble_followers", []))
    summary = {
        "duration_seconds": float(duration_seconds) if duration_seconds is not None else round(actual_duration, 3),
        "sample_rate": int(sample_rate),
        "channels": int(channels),
        "device": device,
        "frame_size": int(frame_size),
        "hop_size": int(hop_size),
        "samples_captured": int(captured_samples),
        "dropped_blocks": int(dropped_blocks),
        "rows": int(sum(1 for row in logs if row["kind"] == "frame")),
        "sent": int(sent_count),
        "beats": int(beat_count),
        "device_count": len(multi_adapter.devices),
        "ble_followers": ble_count,
        "song_boundaries": song_detector.boundary_count,
    }
    return logs, summary
