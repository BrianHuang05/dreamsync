from __future__ import annotations

import dataclasses
import math
import statistics
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
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
        # Hybrid onset mode state: EMA of bass onset activity vs kick flux activity
        self._bass_activity = 0.0
        self._kick_activity = 0.0
        self._wf_activity = 0.0
        self._hybrid_source = "bass"  # current active source in hybrid mode
        # Spectral template matching
        self._beat_template = SpectralBeatTemplate()

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
        self._wf_activity = 0.0
        self._hybrid_source = "bass"
        self._beat_template.reset()

    def update(
        self,
        energy: float,
        t: float,
        spectral_flux: float = 0.0,
        kick_spectral_flux: float = 0.0,
        whitened_flux: float = 0.0,
        percussive_onset: float = 0.0,
        mag: np.ndarray | None = None,
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
            onset = self._hybrid_onset(
                bass_onset, kick_spectral_flux,
                whitened_flux=whitened_flux,
                percussive_onset=percussive_onset,
            )
        else:
            onset = bass_onset
        self.last_onset = onset

        # Spectral template scoring
        if mag is not None:
            beat_from_phase = self._beat_phase > 0.9
            similarity = self._beat_template.update(mag, beat_from_phase)
            if self._beat_template.ready:
                gate = self._similarity_gate(similarity)
                onset = onset * gate
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
            # Scale-only normalization: divide by std to make amplitude-
            # invariant, but preserve the zero baseline (no mean subtraction).
            # Zero-mean normalization would push sparse signals negative
            # and create negative adaptive thresholds.
            onset_std = float(onset_arr.std())
            if onset_std > 1e-8:
                onset_arr = onset_arr / onset_std
            if self.threshold_mode == "adaptive":
                beat_idx = self._detect_beats_adaptive(onset_arr)
            else:
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

    def _hybrid_onset(
        self,
        bass_onset: float,
        kick_flux: float,
        whitened_flux: float = 0.0,
        percussive_onset: float = 0.0,
        alpha: float = 0.05,
        switch_ratio: float = 3.0,
        wf_ratio: float = 10.0,
    ) -> float:
        """Choose between bass_diff, kick_flux, percussive, and whitened_flux.

        Uses bass_diff (best noise rejection) as the primary source.
        Switches to kick_flux when bass onset activity is very low
        relative to kick flux activity.  Falls back to a noisy-environment
        signal when both bass and kick are weak relative to whitened flux.

        In the noisy fallback, prefers *percussive_onset* (HPSS-based)
        over whitened flux when available, because HPSS has per-frame
        selectivity (beat frames >> non-beat frames) while whitened flux
        produces similar values for all active frames.
        """
        self._bass_activity = alpha * bass_onset + (1.0 - alpha) * self._bass_activity
        self._kick_activity = alpha * kick_flux + (1.0 - alpha) * self._kick_activity
        self._wf_activity = alpha * whitened_flux + (1.0 - alpha) * self._wf_activity

        primary = self._bass_activity + self._kick_activity

        if self._wf_activity > wf_ratio * primary and self._wf_activity > 0.01:
            # Noisy environment: primary signals are dead.
            # Prefer percussive onset (HPSS) — it has per-frame selectivity
            # that whitened flux lacks.
            if percussive_onset > 0:
                self._hybrid_source = "percussive"
                return percussive_onset
            self._hybrid_source = "whitened"
            return math.log1p(whitened_flux)

        if self._bass_activity > 0 and self._kick_activity / (self._bass_activity + 1e-12) > switch_ratio:
            self._hybrid_source = "kick"
            return kick_flux

        self._hybrid_source = "bass"
        return bass_onset

    def _similarity_gate(self, similarity: float) -> float:
        """Convert similarity [0,1] into an onset multiplier.

        Maps similarity through a soft gate:
        - similarity >= 0.7  -> multiplier = 1.0 (full pass)
        - similarity ~= 0.5  -> multiplier ~= 0.7
        - similarity <= 0.3  -> multiplier = 0.3 (floor, don't fully mute)
        """
        floor = 0.3
        if similarity >= 0.7:
            return 1.0
        if similarity <= floor:
            return floor
        return floor + (similarity - floor) / (0.7 - floor) * (1.0 - floor)

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
        silence_threshold_rms: float = 0.015,
        min_silence_seconds: float = 2.0,
        min_song_seconds: float = 120.0,
        cooldown_seconds: float = 90.0,
        hop_size: int = 512,
        sample_rate: int = 44100,
    ) -> None:
        self.silence_threshold_rms = silence_threshold_rms
        self.min_silence_frames = int(min_silence_seconds * sample_rate / hop_size)
        self.min_song_frames = int(min_song_seconds * sample_rate / hop_size)
        self._cooldown_frames = int(cooldown_seconds * sample_rate / hop_size)
        self._silent_frames = 0
        self._frames_since_reset = 0
        self._boundary_count = 0
        self._confirm_frames = 0
        self._confirm_required = 3
        self._silence_armed = False

    def update(self, rms: float) -> bool:
        """Feed one frame's RMS. Returns True on song boundary detection."""
        self._frames_since_reset += 1
        if rms < self.silence_threshold_rms:
            self._silent_frames += 1
            # If we were confirming a boundary, reset — silence resumed
            self._confirm_frames = 0
            if (
                self._silent_frames >= self.min_silence_frames
                and self._frames_since_reset >= self.min_song_frames
                and self._frames_since_reset >= self._cooldown_frames
            ):
                self._silence_armed = True
        else:
            if self._silence_armed:
                self._confirm_frames += 1
                if self._confirm_frames >= self._confirm_required:
                    # Sustained energy after silence — this is a real boundary
                    self._silent_frames = 0
                    self._frames_since_reset = 0
                    self._boundary_count += 1
                    self._silence_armed = False
                    self._confirm_frames = 0
                    return True
            else:
                self._silent_frames = 0
                self._confirm_frames = 0
        return False

    @property
    def boundary_count(self) -> int:
        return self._boundary_count


@dataclass
class CrossfadeConfig:
    """Tunable parameters for crossfade-aware song boundary detection."""

    # Voting weights
    w_bpm: float = 0.35
    w_centroid: float = 0.25
    w_bass: float = 0.15
    w_energy: float = 0.10
    w_onset: float = 0.15
    trigger_threshold: float = 0.50

    # Per-signal thresholds
    bpm_jump_threshold: float = 12.0       # BPM
    centroid_shift_threshold: float = 0.30  # relative
    bass_shift_threshold: float = 0.15     # absolute
    energy_range_threshold: float = 0.30   # absolute
    ioi_std_threshold: float = 0.05        # seconds

    # Timing
    window_seconds: float = 10.0           # lookback for prior stats
    recent_seconds: float = 3.0            # lookback for recent stats
    min_song_seconds: float = 60.0         # min time between crossfade boundaries
    confirm_frames: int = 5                # sustain vote for N frames before firing

    # Centroid EMA
    centroid_ema_alpha: float = 0.10


class CrossfadeBoundaryDetector:
    """Detects song transitions during crossfaded playback via weighted voting.

    Runs alongside SongBoundaryDetector.  When multiple audio features
    shift simultaneously (BPM, spectral centroid, bass ratio, energy
    envelope, onset pattern) the detector fires a boundary.
    """

    def __init__(
        self,
        config: CrossfadeConfig | None = None,
        hop_size: int = 512,
        sample_rate: int = 44100,
    ) -> None:
        self.config = config or CrossfadeConfig()
        self._hop_size = hop_size
        self._sample_rate = sample_rate
        self._seconds_per_frame = hop_size / sample_rate

        # Rolling window sizes in frames
        self._window_frames = int(self.config.window_seconds / self._seconds_per_frame)
        self._recent_frames = int(self.config.recent_seconds / self._seconds_per_frame)
        self._min_song_frames = int(self.config.min_song_seconds / self._seconds_per_frame)

        self._boundary_count = 0
        self.reset()

    def reset(self) -> None:
        """Clear all rolling state (called after any boundary fires)."""
        cfg = self.config
        max_len = int(cfg.window_seconds / self._seconds_per_frame) + 1
        self._bpm_buf: deque[float] = deque(maxlen=max_len)
        self._centroid_buf: deque[float] = deque(maxlen=max_len)
        self._bass_buf: deque[float] = deque(maxlen=max_len)
        self._energy_buf: deque[float] = deque(maxlen=max_len)
        self._onset_times: deque[float] = deque(maxlen=max_len)
        self._centroid_ema: float = 0.0
        self._centroid_ema_init: bool = False
        self._confirm_count: int = 0
        self._frames_since_reset: int = 0

    def update(
        self,
        bpm: float,
        centroid: float,
        bass_ratio: float,
        energy: float,
        onset_strength: float,
        beat: bool,
        t: float,
    ) -> bool:
        """Feed one frame of features. Returns True on crossfade boundary."""
        self._frames_since_reset += 1

        # Accumulate rolling buffers
        self._bpm_buf.append(bpm)
        self._bass_buf.append(bass_ratio)
        self._energy_buf.append(energy)

        # Track centroid with EMA for delta detection
        alpha = self.config.centroid_ema_alpha
        if not self._centroid_ema_init:
            self._centroid_ema = centroid
            self._centroid_ema_init = True
        else:
            self._centroid_ema = alpha * centroid + (1.0 - alpha) * self._centroid_ema
        self._centroid_buf.append(centroid)

        # Track onset times for IOI analysis
        if beat:
            self._onset_times.append(t)

        # Not enough data yet
        if len(self._bpm_buf) < self._recent_frames + 1:
            return False

        # Cooldown: respect min_song_seconds
        if self._frames_since_reset < self._min_song_frames:
            return False

        # Compute per-signal votes
        score = self._compute_score()

        if score >= self.config.trigger_threshold:
            self._confirm_count += 1
            if self._confirm_count >= self.config.confirm_frames:
                self._boundary_count += 1
                return True
        else:
            self._confirm_count = 0
        return False

    @property
    def boundary_count(self) -> int:
        return self._boundary_count

    # ------------------------------------------------------------------
    # Signal scoring helpers
    # ------------------------------------------------------------------

    def _compute_score(self) -> float:
        cfg = self.config
        score = 0.0

        if self._bpm_discontinuity():
            score += cfg.w_bpm
        if self._centroid_discontinuity():
            score += cfg.w_centroid
        if self._bass_discontinuity():
            score += cfg.w_bass
        if self._energy_envelope_break():
            score += cfg.w_energy
        if self._onset_pattern_break():
            score += cfg.w_onset

        return score

    def _split_recent_prior(self, buf: deque) -> tuple[list, list]:
        """Split buffer into recent (last N frames) and prior (the rest)."""
        items = list(buf)
        n = min(self._recent_frames, len(items) - 1)
        recent = items[-n:]
        prior = items[:-n]
        return recent, prior

    def _bpm_discontinuity(self) -> bool:
        recent, prior = self._split_recent_prior(self._bpm_buf)
        if not prior or not recent:
            return False
        # Filter out zero BPM values (estimator not locked yet)
        recent_valid = [b for b in recent if b > 0]
        prior_valid = [b for b in prior if b > 0]
        if not recent_valid or not prior_valid:
            return False
        recent_med = float(sorted(recent_valid)[len(recent_valid) // 2])
        prior_med = float(sorted(prior_valid)[len(prior_valid) // 2])
        return abs(recent_med - prior_med) > self.config.bpm_jump_threshold

    def _centroid_discontinuity(self) -> bool:
        recent, prior = self._split_recent_prior(self._centroid_buf)
        if not prior or not recent:
            return False
        recent_mean = sum(recent) / len(recent)
        prior_mean = sum(prior) / len(prior)
        if prior_mean < 1e-8:
            return False
        relative_shift = abs(recent_mean - prior_mean) / prior_mean
        return relative_shift > self.config.centroid_shift_threshold

    def _bass_discontinuity(self) -> bool:
        recent, prior = self._split_recent_prior(self._bass_buf)
        if not prior or not recent:
            return False
        recent_mean = sum(recent) / len(recent)
        prior_mean = sum(prior) / len(prior)
        return abs(recent_mean - prior_mean) > self.config.bass_shift_threshold

    def _energy_envelope_break(self) -> bool:
        # Look at the last ~2 seconds of energy for a spike/dip
        n_frames = int(2.0 / self._seconds_per_frame)
        if len(self._energy_buf) < n_frames:
            return False
        recent = list(self._energy_buf)[-n_frames:]
        return (max(recent) - min(recent)) > self.config.energy_range_threshold

    def _onset_pattern_break(self) -> bool:
        times = list(self._onset_times)
        if len(times) < 4:
            return False
        iois = [times[i + 1] - times[i] for i in range(len(times) - 1)]
        n = min(self._recent_frames, len(iois) - 1)
        if n < 2:
            return False
        # Split IOIs into recent and prior
        recent_iois = iois[-n:]
        prior_iois = iois[:-n]
        if len(prior_iois) < 2 or len(recent_iois) < 2:
            return False
        recent_std = statistics.pstdev(recent_iois)
        prior_std = statistics.pstdev(prior_iois)
        return abs(recent_std - prior_std) > self.config.ioi_std_threshold


class SpectralBeatTemplate:
    """Learn and match against the spectral shape of beat frames.

    During the bootstrap phase (first ~5s), collects magnitude spectra at
    detected beat positions.  Once enough beats are collected, builds a
    template via averaging and scores each new frame by cosine similarity
    against the template.
    """

    def __init__(
        self,
        min_beats_for_template: int = 8,
        ema_alpha: float = 0.08,
        similarity_floor: float = 0.3,
    ):
        self.min_beats = min_beats_for_template
        self.ema_alpha = ema_alpha
        self.similarity_floor = similarity_floor

        # Bootstrap collection
        self._beat_mags: list[np.ndarray] = []
        self._template: np.ndarray | None = None
        self._template_ready = False

        # Frame state
        self._prev_similarity = 0.0

    # --- public API ---

    def update(self, mag: np.ndarray, is_beat: bool) -> float:
        """Score current frame against the beat template.

        Args:
            mag: magnitude spectrum from rfft (shape: n_bins,)
            is_beat: whether the current frame is a detected beat

        Returns:
            similarity: 0.0-1.0, how much this frame looks like a beat.
                        Returns 0.0 during bootstrap.
        """
        if not self._template_ready:
            if is_beat:
                self._beat_mags.append(mag.copy())
                if len(self._beat_mags) >= self.min_beats:
                    self._build_template()
            return 0.0

        similarity = self._cosine_similarity(mag)

        # Adapt template with confirmed beat frames (high-similarity beats)
        if is_beat and similarity > 0.5:
            norm_mag = mag / (np.linalg.norm(mag) + 1e-10)
            self._template = (
                self.ema_alpha * norm_mag
                + (1.0 - self.ema_alpha) * self._template
            )
            self._template /= np.linalg.norm(self._template) + 1e-10

        self._prev_similarity = similarity
        return similarity

    @property
    def ready(self) -> bool:
        return self._template_ready

    def reset(self) -> None:
        """Clear template on song boundary."""
        self._beat_mags.clear()
        self._template = None
        self._template_ready = False
        self._prev_similarity = 0.0

    # --- internals ---

    def _build_template(self) -> None:
        """Average collected beat spectra into a template."""
        stack = np.stack(self._beat_mags)
        mean_mag = stack.mean(axis=0)
        self._template = mean_mag / (np.linalg.norm(mean_mag) + 1e-10)
        self._template_ready = True
        self._beat_mags.clear()

    def _cosine_similarity(self, mag: np.ndarray) -> float:
        """Cosine similarity between frame spectrum and template."""
        norm_mag = np.linalg.norm(mag)
        if norm_mag < 1e-10:
            return 0.0
        sim = float(np.dot(mag, self._template) / (norm_mag + 1e-10))
        return max(0.0, sim)


class NoiseFloorEstimator:
    """Per-frequency-bin minimum-statistics noise floor estimation.

    Tracks the minimum magnitude per bin over a sliding window.
    The noise floor is what's always present (ambient noise); music
    adds energy on top of this minimum.
    """

    _MIN_READY_FRAMES = 86  # ~1 second at 86 fps before subtraction activates

    def __init__(self, n_bins: int, window_frames: int = 200) -> None:
        self._ring: deque[np.ndarray] = deque(maxlen=window_frames)
        self._n_bins = n_bins
        self._noise_floor: np.ndarray | None = None

    def update(self, mag: np.ndarray) -> np.ndarray:
        """Feed one frame's magnitude spectrum, return current noise floor estimate."""
        self._ring.append(mag.copy())
        self._noise_floor = np.min(np.stack(list(self._ring)), axis=0)
        return self._noise_floor

    @property
    def noise_floor(self) -> np.ndarray | None:
        return self._noise_floor

    @property
    def ready(self) -> bool:
        return len(self._ring) >= self._MIN_READY_FRAMES

    def reset(self) -> None:
        self._ring.clear()
        self._noise_floor = None


class PercussiveOnsetTracker:
    """HPSS-based percussive onset detection for noisy environments.

    Maintains a rolling buffer of active-frame magnitude spectra and
    computes the per-bin time-direction median as the *harmonic* estimate
    (sustained ambient noise).  The percussive component is what exceeds
    this stable spectral shape — transient events like kicks and snares.

    Unlike whitened flux (which normalizes then computes frame-to-frame
    differences), this returns per-frame *percussive energy* — each frame
    is independently scored against the learned ambient model.  This
    avoids the selectivity problem where all active frames produce
    similar whitened flux values.

    Only active frames (above *energy_gate*) are buffered and scored.
    Silent/dropout frames return 0.0 without polluting the buffer.
    """

    _MIN_READY_FRAMES = 15  # need this many active frames to estimate median

    def __init__(
        self,
        n_bins: int,
        kernel_size: int = 43,
        energy_gate: float = 1.0,
    ) -> None:
        self._n_bins = n_bins
        self._kernel_size = kernel_size
        self._energy_gate = energy_gate
        self._mag_buffer: deque[np.ndarray] = deque(maxlen=kernel_size)

    @property
    def ready(self) -> bool:
        return len(self._mag_buffer) >= min(self._MIN_READY_FRAMES, self._kernel_size)

    def update(self, mag: np.ndarray) -> float:
        """Feed one frame's magnitude spectrum.  Returns percussive onset energy.

        Returns 0.0 for silent frames and during warmup.
        """
        frame_energy = float(mag.sum())
        if self._energy_gate > 0 and frame_energy <= self._energy_gate:
            return 0.0

        self._mag_buffer.append(mag.copy())

        if not self.ready:
            return 0.0

        # Time-median per frequency bin = harmonic (sustained) estimate.
        # Crowd noise dominates the median; transients appear in < 50%
        # of buffered frames so they don't affect the median.
        S = np.stack(list(self._mag_buffer))
        harmonic = np.median(S, axis=0)

        # Percussive = energy that exceeds the sustained ambient shape
        percussive = np.maximum(0.0, mag - harmonic)

        return float(percussive.sum())

    def reset(self) -> None:
        self._mag_buffer.clear()


@dataclass
class SpectralFeatures:
    bass: float
    bass_ratio: float
    spectral_flux: float
    kick_energy: float
    kick_ratio: float
    kick_spectral_flux: float
    centroid: float
    mag: np.ndarray
    raw_mag: np.ndarray | None = None  # pre-subtraction mag, set when noise_floor is used


def _prepare_bass_window(
    frame_size: int,
    sample_rate: int,
    kick_low: float = 50.0,
    kick_high: float = 130.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return (hanning_window, bass_mask, kick_mask, freqs).

    bass_mask: freqs <= 200 Hz (broadband bass).
    kick_mask: kick_low <= freqs <= kick_high (narrow kick band).
    freqs: frequency bin centers for spectral centroid computation.
    """
    window = np.hanning(frame_size).astype(np.float32)
    freqs = np.fft.rfftfreq(frame_size, d=1.0 / sample_rate)
    bass_mask = freqs <= 200.0
    kick_mask = (freqs >= kick_low) & (freqs <= kick_high)
    return window, bass_mask, kick_mask, freqs


def _spectral_features(
    frame: np.ndarray,
    window: np.ndarray,
    mask: np.ndarray,
    prev_mag: np.ndarray | None,
    kick_mask: np.ndarray | None = None,
    freqs: np.ndarray | None = None,
    noise_floor: np.ndarray | None = None,
) -> SpectralFeatures:
    spectrum = np.fft.rfft(frame * window)
    raw_mag = np.abs(spectrum)

    # Apply spectral subtraction if noise floor is provided
    if noise_floor is not None:
        mag = np.maximum(0.0, raw_mag - noise_floor)
    else:
        mag = raw_mag

    bass = float(mag[mask].sum())
    total = float(mag.sum()) + 1e-8
    bass_ratio = float(mag[mask].sum()) / total
    if freqs is not None:
        centroid = float(np.sum(freqs * mag) / (total))
    else:
        centroid = 0.0
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
        kick_spectral_flux=kick_spectral_flux, centroid=centroid, mag=mag,
        raw_mag=raw_mag if noise_floor is not None else None,
    )


def _compute_whitened_flux(
    mag: np.ndarray,
    spectral_mean: np.ndarray | None,
    prev_whitened_mag: np.ndarray | None,
    alpha: float = 0.05,
    energy_gate: float = 0.0,
) -> tuple[float, np.ndarray, np.ndarray]:
    """Compute spectral flux on a pre-whitened (normalized) spectrum.

    When *energy_gate* > 0, only frames with ``mag.sum() > energy_gate``
    update the spectral mean and contribute to flux.  This prevents
    silence frames (85 %+ in a noisy bar) from diluting the mean,
    which would make ANY audio frame produce huge whitened flux.

    The third return value is the last *active* frame's whitened mag
    (unchanged for silent frames), so flux measures spectral change
    between consecutive audio captures, not silence→audio transitions.

    Returns (whitened_flux, updated_spectral_mean, last_active_whitened_mag).
    """
    frame_energy = float(mag.sum())
    is_active = energy_gate <= 0 or frame_energy > energy_gate

    # Initialize spectral mean from first active frame
    if spectral_mean is None:
        if is_active:
            spectral_mean = mag.copy()
        return 0.0, spectral_mean, prev_whitened_mag

    # Only update spectral mean from active frames
    if is_active:
        spectral_mean = alpha * mag + (1.0 - alpha) * spectral_mean

    whitened_mag = mag / (spectral_mean + 1e-8)

    # Compute flux only between two active frames
    if is_active and prev_whitened_mag is not None:
        whitened_flux = float(np.maximum(0.0, whitened_mag - prev_whitened_mag).sum())
    else:
        whitened_flux = 0.0

    # Return current whitened mag only if active (preserve last active for silent frames)
    return whitened_flux, spectral_mean, whitened_mag if is_active else prev_whitened_mag


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
    centroid: float = 0.0,
) -> dict[str, float | bool]:
    signs = np.sign(frame)
    zcr = float(np.mean(np.abs(np.diff(signs)) > 0))
    return {
        "t": t,
        "rms": rms,
        "zcr": zcr,
        "centroid": float(centroid),
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
    telemetry_dir: Path | None = None,
    crossfade_detect: bool = False,
    profile: Any | None = None,
    effect_cycler_override: "EffectCycler | None" = None,
    profile_rotation: Any | None = None,
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

    from dreamsync.telemetry import SongTelemetryWriter

    sd = _require_sounddevice()
    audio_queue: deque[np.ndarray] = deque()
    logs: list[dict[str, Any]] = []
    telemetry: SongTelemetryWriter | None = SongTelemetryWriter(telemetry_dir) if telemetry_dir else None
    bpm_estimator = LiveBpmEstimator(
        sample_rate=sample_rate, hop_size=hop_size, half_time=half_time,
    )
    song_detector = SongBoundaryDetector(
        hop_size=hop_size, sample_rate=sample_rate,
    )
    crossfade_detector: CrossfadeBoundaryDetector | None = (
        CrossfadeBoundaryDetector(hop_size=hop_size, sample_rate=sample_rate)
        if crossfade_detect else None
    )
    director = Director(director_config)
    mood_classifier = MoodClassifier() if auto_cycle else None
    if effect_cycler_override is not None:
        effect_cycler = effect_cycler_override
    elif auto_cycle:
        effect_cycler = EffectCycler(
            EffectCyclerConfig(cycle_interval=cycle_interval), profile=profile,
        )
    else:
        effect_cycler = None
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
    window, bass_mask, kick_mask, freqs = _prepare_bass_window(frame_size, sample_rate)
    n_bins = frame_size // 2 + 1
    noise_estimator = NoiseFloorEstimator(n_bins=n_bins)
    percussive_tracker = PercussiveOnsetTracker(n_bins=n_bins, energy_gate=1.0)
    prev_mag: np.ndarray | None = None
    spectral_mean: np.ndarray | None = None
    prev_whitened_mag: np.ndarray | None = None
    preset = None

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
            last_sf: SpectralFeatures | None = None
            last_wf: float = 0.0
            perc: float = 0.0
            while buffer.shape[0] >= frame_size:
                frame = buffer[:frame_size]
                buffer = buffer[hop_size:]
                rms = float(np.sqrt(np.mean(frame**2)))
                silence_boundary = song_detector.update(rms)

                nf = noise_estimator.noise_floor if noise_estimator.ready else None
                sf = _spectral_features(frame, window, bass_mask, prev_mag, kick_mask=kick_mask, freqs=freqs, noise_floor=nf)
                # Use raw mag for noise floor estimation; cleaned mag for features
                raw_mag = sf.raw_mag if sf.raw_mag is not None else sf.mag
                noise_estimator.update(raw_mag)
                prev_mag = sf.mag
                wf, spectral_mean, prev_whitened_mag = _compute_whitened_flux(
                    raw_mag, spectral_mean, prev_whitened_mag,
                    energy_gate=1.0,
                )
                last_sf = sf
                last_wf = wf
                perc = percussive_tracker.update(raw_mag)
                bpm, beat = bpm_estimator.update(
                    sf.bass, stream_t,
                    spectral_flux=sf.spectral_flux,
                    kick_spectral_flux=sf.kick_spectral_flux,
                    whitened_flux=wf,
                    percussive_onset=perc,
                    mag=sf.mag,
                )
                if half_time and bpm > 0:
                    bpm *= 0.5

                # Crossfade boundary detection (parallel to silence)
                crossfade_boundary = False
                if crossfade_detector is not None:
                    crossfade_boundary = crossfade_detector.update(
                        bpm=bpm,
                        centroid=sf.centroid,
                        bass_ratio=sf.bass_ratio,
                        energy=director.energy,
                        onset_strength=bpm_estimator.last_onset,
                        beat=beat,
                        t=stream_t,
                    )

                boundary_type: str | None = None
                if silence_boundary:
                    boundary_type = "silence"
                elif crossfade_boundary:
                    boundary_type = "crossfade"

                if boundary_type is not None:
                    # Song boundary: reset all state
                    bpm_estimator.reset()
                    director.reset()
                    if mood_classifier is not None:
                        mood_classifier.reset(stream_t)
                    if effect_cycler is not None:
                        effect_cycler.reset()
                    if crossfade_detector is not None:
                        crossfade_detector.reset()
                    prev_mag = None
                    spectral_mean = None
                    prev_whitened_mag = None
                    noise_estimator.reset()
                    percussive_tracker.reset()
                    boundary_idx = song_detector.boundary_count + (
                        crossfade_detector.boundary_count if crossfade_detector else 0
                    )
                    if telemetry:
                        telemetry.on_boundary(boundary_idx, stream_t, boundary_type=boundary_type)
                    if debug_mood:
                        print(
                            f"*** Song boundary detected [{boundary_type}] "
                            f"(#{boundary_idx}) "
                            f"— state reset ***"
                        )

                last_features = _feature_row_from_frame(
                    frame, rms, stream_t, bpm, beat,
                    bass=sf.bass,
                    bass_ratio=sf.bass_ratio,
                    spectral_flux=sf.spectral_flux,
                    onset_strength=bpm_estimator.last_onset,
                    centroid=sf.centroid,
                )
                last_intent = director.update(last_features)
                if beat:
                    beat_count += 1
                    beat_this_tick = True
                stream_t += float(hop_size) / float(sample_rate)

            # --- Profile rotation ---
            if profile_rotation is not None and effect_cycler is not None:
                new_profile = profile_rotation.update(stream_t)
                if new_profile is not None:
                    effect_cycler.set_profile(new_profile)
                    if debug_mood:
                        print(f"[rotation] switched to profile: {new_profile.name}")

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

                if telemetry and mood_classifier is not None:
                    telemetry.write_frame({
                        "t": round(stream_t, 4),
                        "bpm": round(float(bpm_estimator.last_bpm), 2),
                        "beat": bool(beat_this_tick),
                        "rms": round(float(last_features["rms"]), 5) if last_features else 0.0,
                        "energy": round(director.energy, 4),
                        "stability": round(director.stability, 4),
                        "mood": mood_classifier.mood.value,
                        "effect": effect_cycler.current_effect if effect_cycler else None,
                        "palette": effect_cycler.current_palette if effect_cycler else None,
                        "render_mode": preset.render_mode.value if preset else None,
                        # Spectral analysis
                        "bass_ratio": round(float(last_features["bass_ratio"]), 4) if last_features else 0.0,
                        "spectral_flux": round(float(last_features["spectral_flux"]), 4) if last_features else 0.0,
                        "kick_flux": round(float(last_sf.kick_spectral_flux), 4) if last_sf else 0.0,
                        "whitened_flux": round(float(last_wf), 4),
                        "percussive_onset": round(float(perc), 4),
                        "centroid": round(float(last_sf.centroid), 2) if last_sf else 0.0,
                        # Onset detection internals
                        "onset_strength": round(float(last_features.get("onset_strength", 0)), 4) if last_features else 0.0,
                        "onset_thresh": round(float(bpm_estimator.last_thresh), 4),
                        "onset_mean": round(float(bpm_estimator.last_onset_mean), 4),
                        "onset_std": round(float(bpm_estimator.last_onset_std), 4),
                        "hybrid_source": bpm_estimator._hybrid_source,
                        "beat_phase": round(float(bpm_estimator._beat_phase), 4),
                        # Template matching
                        "template_similarity": round(bpm_estimator._beat_template._prev_similarity, 4),
                        "template_ready": bpm_estimator._beat_template.ready,
                    })

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

    if telemetry:
        telemetry.close()

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
