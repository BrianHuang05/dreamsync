from __future__ import annotations

import dataclasses
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
        window_seconds: float = 12.0,
        min_update_interval: float = 0.5,
        beat_threshold_percentile: float = 65.0,
        beat_threshold_std_mult: float = 0.15,
        min_bpm: float = 80.0,
        max_bpm: float = 200.0,
        max_jump_bpm: float = 6.0,
        confirm_updates: int = 4,
        half_time: bool = False,
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
        self.max_frames = max(8, int(window_seconds * sample_rate / hop_size))
        self.min_frames = max(8, int(3.0 * sample_rate / hop_size))
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

    def update(self, energy: float, t: float) -> tuple[float, bool]:
        onset = max(0.0, energy - self.prev_rms)
        self.last_onset = onset
        self.prev_rms = energy
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
            beat_idx = self._detect_beats_live(onset_arr)
            bpm_from_beats = _estimate_bpm_from_beats(
                beat_idx, self.hop_size, self.sample_rate
            )
            bpm_from_corr = _estimate_bpm(onset_arr, self.hop_size, self.sample_rate)
            if bpm_from_beats > 0 and bpm_from_corr > 0:
                bpm = 0.7 * bpm_from_beats + 0.3 * bpm_from_corr
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
            return bpm
        # Require a few consistent updates before accepting a big jump.
        if self._candidate_bpm <= 0.0 or abs(bpm - self._candidate_bpm) > self.max_jump_bpm:
            self._candidate_bpm = bpm
            self._candidate_hits = 1
            return self.last_bpm
        self._candidate_hits += 1
        if self._candidate_hits >= self.confirm_updates:
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


@dataclass
class SpectralFeatures:
    bass: float
    bass_ratio: float
    spectral_flux: float
    mag: np.ndarray


def _prepare_bass_window(frame_size: int, sample_rate: int) -> tuple[np.ndarray, np.ndarray]:
    window = np.hanning(frame_size).astype(np.float32)
    freqs = np.fft.rfftfreq(frame_size, d=1.0 / sample_rate)
    mask = freqs <= 200.0
    return window, mask


def _spectral_features(
    frame: np.ndarray,
    window: np.ndarray,
    mask: np.ndarray,
    prev_mag: np.ndarray | None,
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
    return SpectralFeatures(bass=bass, bass_ratio=bass_ratio, spectral_flux=spectral_flux, mag=mag)


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
    duration_seconds: float,
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
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Audio capture → beat detection → renderer → Govee UDP streaming.

    Drives one or more Govee devices via a MultiGoveeLanAdapter.
    Each device has its own renderer and role for independent rendering.
    """
    if duration_seconds <= 0:
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
    window, bass_mask = _prepare_bass_window(frame_size, sample_rate)
    prev_mag: np.ndarray | None = None

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
            now = time.monotonic()
            elapsed = now - started_at
            if elapsed >= duration_seconds:
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
                sf = _spectral_features(frame, window, bass_mask, prev_mag)
                prev_mag = sf.mag
                bpm, beat = bpm_estimator.update(sf.bass, stream_t)
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

    summary = {
        "duration_seconds": float(duration_seconds),
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
    }
    return logs, summary
