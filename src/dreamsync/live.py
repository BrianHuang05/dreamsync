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
from dreamsync.director import Director, DirectorConfig, EffectMode
from dreamsync.dsp.features import _estimate_bpm, _estimate_bpm_from_beats, _smooth_signal
from dreamsync.effects import EffectCycler, EffectCyclerConfig
from dreamsync.mood import MoodClassifier
from dreamsync.output.govee_lan import GoveeLanAdapter, MultiGoveeLanAdapter
from dreamsync.render import RenderMode, SegmentRenderer
from dreamsync.show.runtime_control import apply_runtime_control_to_intent_params


HARMONIC_RATIOS = {
    "1x":   1.0,
    "1/2x": 0.5,
    "2x":   2.0,
    "2/3x": 2.0 / 3.0,
    "3/2x": 3.0 / 2.0,
    "3/4x": 0.75,
    "4/3x": 4.0 / 3.0,
}


class IOIHistogram:
    """Inter-Onset Interval histogram for BPM estimation (Layer 1).

    Tracks onset timestamps and computes pairwise intervals to find
    the dominant beat period.  Working in the time domain avoids the
    BPM-range normalization that introduces octave ambiguity.
    """

    def __init__(
        self,
        buffer_seconds: float = 8.0,
        bin_width_ms: float = 5.0,
        min_period_ms: float = 300.0,   # 200 BPM
        max_period_ms: float = 1500.0,  # 40 BPM
        min_onset_gap: float = 0.12,
        thresh_window: int = 200,
    ) -> None:
        self.buffer_seconds = buffer_seconds
        self.bin_width_ms = bin_width_ms
        self.min_period_ms = min_period_ms
        self.max_period_ms = max_period_ms
        self.min_onset_gap = min_onset_gap
        self._onset_times: deque[float] = deque()
        self._recent_vals: deque[float] = deque(maxlen=thresh_window)
        self._last_onset_t = -1.0
        self._prev_val = 0.0

    def feed(self, onset_val: float, t: float) -> bool:
        """Feed a per-frame onset value.  Returns True if an onset was detected."""
        self._recent_vals.append(onset_val)
        detected = False

        if len(self._recent_vals) >= 20:
            arr = np.asarray(self._recent_vals)
            thresh = float(np.percentile(arr, 85))
            if thresh < 1e-8:
                thresh = float(arr.max()) * 0.3

            if (
                onset_val > thresh
                and onset_val > self._prev_val
                and (t - self._last_onset_t) >= self.min_onset_gap
            ):
                self._onset_times.append(t)
                self._last_onset_t = t
                detected = True

        self._prev_val = onset_val

        # Trim old timestamps
        cutoff = t - self.buffer_seconds
        while self._onset_times and self._onset_times[0] < cutoff:
            self._onset_times.popleft()

        return detected

    def add_onset(self, t: float) -> None:
        """Record an onset at time *t* directly (for testing or external use)."""
        self._onset_times.append(t)
        cutoff = t - self.buffer_seconds
        while self._onset_times and self._onset_times[0] < cutoff:
            self._onset_times.popleft()

    def estimate_bpm(self) -> float:
        """Return the dominant BPM from the IOI histogram, or 0.0."""
        times = list(self._onset_times)
        if len(times) < 4:
            return 0.0

        min_s = self.min_period_ms / 1000.0
        max_s = self.max_period_ms / 1000.0

        # Compute pairwise intervals within the valid period range
        intervals_ms: list[float] = []
        for i in range(len(times)):
            for j in range(i + 1, len(times)):
                dt = times[j] - times[i]
                if dt > max_s:
                    break  # times are sorted; later j only larger
                if dt >= min_s:
                    intervals_ms.append(dt * 1000.0)

        if len(intervals_ms) < 4:
            return 0.0

        intervals_arr = np.asarray(intervals_ms)

        # Build histogram
        n_bins = int((self.max_period_ms - self.min_period_ms) / self.bin_width_ms) + 1
        bin_edges = np.linspace(self.min_period_ms, self.max_period_ms, n_bins + 1)
        counts, _ = np.histogram(intervals_arr, bins=bin_edges)

        if counts.max() == 0:
            return 0.0

        bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2.0

        # Smooth histogram to handle onset-timing jitter
        if len(counts) >= 5:
            kernel = np.array([1, 2, 3, 2, 1], dtype=float)
            kernel /= kernel.sum()
            counts_smooth = np.convolve(counts.astype(float), kernel, mode="same")
        else:
            counts_smooth = counts.astype(float)

        # Require a clear peak (minimum absolute count)
        if counts_smooth.max() < 3:
            return 0.0

        peak_idx = int(np.argmax(counts_smooth))

        # Refine peak position with weighted centroid of ±2 bins
        lo = max(0, peak_idx - 2)
        hi = min(len(counts_smooth), peak_idx + 3)
        weights = counts_smooth[lo:hi]
        centers = bin_centers[lo:hi]
        w_sum = float(weights.sum())
        if w_sum > 0:
            peak_period_ms = float(np.average(centers, weights=weights))
        else:
            peak_period_ms = float(bin_centers[peak_idx])

        return 60000.0 / peak_period_ms

    def set_onsets(self, times: list[float]) -> None:
        """Replace the onset buffer with sorted beat timestamps."""
        self._onset_times.clear()
        if not times:
            return
        cutoff = times[-1] - self.buffer_seconds
        for t in times:
            if t >= cutoff:
                self._onset_times.append(t)

    @property
    def onset_count(self) -> int:
        return len(self._onset_times)

    def reset(self) -> None:
        self._onset_times.clear()
        self._recent_vals.clear()
        self._last_onset_t = -1.0
        self._prev_val = 0.0


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
        harmonic_confirm_count: int = 12,
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
        self.harmonic_confirm_count = max(1, harmonic_confirm_count)
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
        self._pending_harmonic_label: str | None = None
        self._harmonic_confirm = 0
        self._last_onset_beat_t = -1e9
        self._prev_onset = 0.0
        # Hybrid onset mode state: EMA of bass onset activity vs kick flux activity
        self._bass_activity = 0.0
        self._kick_activity = 0.0
        self._wf_activity = 0.0
        self._hybrid_source = "bass"  # current active source in hybrid mode
        # Spectral template matching
        self._beat_template = SpectralBeatTemplate()
        # IOI histogram (Layer 1) — primary BPM estimator
        self._ioi_histogram = IOIHistogram(buffer_seconds=8.0, bin_width_ms=5.0)
        # Zero-estimate decay: when both BPM methods return 0 for several
        # consecutive updates, decay last_bpm to avoid holding stale values.
        self._zero_estimate_count = 0
        self._last_autocorr_confidence = 0.0
        self._last_onset_activity = 0.0

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
        self._pending_harmonic_label = None
        self._harmonic_confirm = 0
        self._last_onset_beat_t = -1e9
        self._prev_onset = 0.0
        self._bass_activity = 0.0
        self._kick_activity = 0.0
        self._wf_activity = 0.0
        self._hybrid_source = "bass"
        self._beat_template.reset()
        self._ioi_histogram.reset()
        self._zero_estimate_count = 0
        self._last_autocorr_confidence = 0.0
        self._last_onset_activity = 0.0

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
            similarity = self._beat_template.update(
                mag, beat_from_phase, frame_energy=energy,
            )
            if self._beat_template.ready and self._beat_template.has_selectivity:
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

            # Onset activity gate: check if the recent window has enough
            # non-zero onset frames.  In a bar, quiet/noise sections produce
            # almost all-zero onset envelopes — don't try to estimate BPM
            # from silence.  Use the last ~2 seconds of the envelope.
            recent_n = min(len(onset_arr), int(2.0 * self.sample_rate / self.hop_size))
            recent = onset_arr[-recent_n:]
            # "active" = above 1% of the recent max (accounts for varying amplitude)
            recent_max = float(recent.max())
            if recent_max > 1e-8:
                active_frac = float(np.sum(recent > 0.01 * recent_max)) / recent_n
            else:
                active_frac = 0.0
            self._last_onset_activity = active_frac

            if active_frac < 0.05:
                # Onset is dead — no signal to estimate BPM from.
                # Increment zero counter; BPM decays after threshold.
                self._zero_estimate_count += 1
                if self._zero_estimate_count >= 6:
                    self.last_bpm = 0.0
                self.last_update_t = t
            else:
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
                bpm_from_corr, self._last_autocorr_confidence = _estimate_bpm(
                    onset_arr, self.hop_size, self.sample_rate
                )
                if bpm_from_beats > 0 and bpm_from_corr > 0:
                    bpm = 0.7 * bpm_from_beats + 0.3 * bpm_from_corr
                else:
                    bpm = bpm_from_beats if bpm_from_beats > 0 else bpm_from_corr
                if bpm > 0.0:
                    bpm = self._normalize_bpm(bpm)
                    bpm = self._snap_to_last(bpm)
                    self.last_bpm = self._apply_inertia(bpm)
                    self._zero_estimate_count = 0
                else:
                    self._zero_estimate_count += 1
                    if self._zero_estimate_count >= 6:
                        self.last_bpm = 0.0

                self.last_update_t = t
                if beat_idx.size > 0:
                    self.last_beat_idx = int(beat_idx[-1])

        beat = self._advance_beat_phase()
        return self.last_bpm, beat

    def _classify_harmonic(self, raw_bpm: float) -> tuple[str, float]:
        """Return (ratio_label, mapped_bpm) for the best-matching harmonic.

        mapped_bpm is raw_bpm divided by the ratio, i.e. what the BPM would be
        in the locked octave.  For "1x" the mapped value equals raw_bpm.
        """
        if self.last_bpm <= 0:
            return ("1x", raw_bpm)

        best_label = "1x"
        best_mapped = raw_bpm
        best_error = abs(raw_bpm - self.last_bpm)

        for label, ratio in HARMONIC_RATIOS.items():
            mapped = raw_bpm / ratio
            error = abs(mapped - self.last_bpm)
            if error < best_error:
                best_error = error
                best_label = label
                best_mapped = mapped

        # Only accept a harmonic classification if the mapped value is
        # close to last_bpm (within 15%).  Prevents spurious ratios when
        # the raw estimate is just noisy (not a true harmonic).
        if best_label != "1x" and best_error > self.last_bpm * 0.15:
            return ("1x", raw_bpm)

        return (best_label, best_mapped)

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
        # Subharmonic correction: in noisy environments the autocorrelation
        # picks up 2/3, 3/4, 4/5 of the true period.  Only apply for
        # DOWNWARD drift (bpm < last_bpm * 0.9) to prevent noise from
        # pushing BPM to a lower subharmonic.  Upward changes use standard
        # octave snapping so the system can escape a wrong subharmonic lock.
        if bpm < self.last_bpm * 0.9:
            for ratio in (1.5, 2.0 / 3.0, 4.0 / 3.0, 0.75, 1.25, 0.8):
                mapped = self._normalize_bpm(bpm * ratio)
                if abs(mapped - self.last_bpm) <= self.max_jump_bpm:
                    candidates.append(mapped)
        candidates = [self._normalize_bpm(c) for c in candidates]
        best = min(candidates, key=lambda v: abs(v - self.last_bpm))
        return best

    def _apply_inertia(self, bpm: float) -> float:
        if self.last_bpm <= 0.0:
            self._candidate_bpm = 0.0
            self._candidate_hits = 0
            self._pending_harmonic_label = None
            self._harmonic_confirm = 0
            return bpm

        label, mapped = self._classify_harmonic(bpm)

        if label == "1x":
            # Non-harmonic path: use existing inertia logic
            self._pending_harmonic_label = None
            self._harmonic_confirm = 0
            if abs(bpm - self.last_bpm) <= self.max_jump_bpm:
                self._candidate_bpm = 0.0
                self._candidate_hits = 0
                # EMA smooth small changes to reduce frame-to-frame jitter
                return 0.3 * bpm + 0.7 * self.last_bpm
            # Require a few consistent updates before accepting a big jump
            if (
                self._candidate_bpm <= 0.0
                or abs(bpm - self._candidate_bpm) > self.max_jump_bpm
            ):
                self._candidate_bpm = bpm
                self._candidate_hits = 1
                return self.last_bpm
            self._candidate_hits += 1
            if self._candidate_hits >= self.confirm_updates:
                self._candidate_bpm = 0.0
                self._candidate_hits = 0
                return bpm
            return self.last_bpm
        else:
            # Harmonic jump: require many confirmations at the SAME ratio
            self._candidate_bpm = 0.0
            self._candidate_hits = 0
            if label != self._pending_harmonic_label:
                self._pending_harmonic_label = label
                self._harmonic_confirm = 0

            self._harmonic_confirm += 1

            if self._harmonic_confirm >= self.harmonic_confirm_count:
                # Genuine tempo change — accept the raw bpm
                self._pending_harmonic_label = None
                self._harmonic_confirm = 0
                return bpm
            else:
                # Reject — hold the locked BPM.  Returning mapped
                # caused feedback drift; returning last_bpm keeps
                # the output stable during momentary confusion.
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
        energy_threshold: float = 0.005,
    ):
        self.min_beats = min_beats_for_template
        self.ema_alpha = ema_alpha
        self.similarity_floor = similarity_floor
        self._energy_threshold = energy_threshold

        # Bootstrap collection
        self._beat_mags: list[np.ndarray] = []
        self._template: np.ndarray | None = None
        self._template_ready = False

        # Frame state
        self._prev_similarity = 0.0

        # Selectivity monitor: track similarity variance to detect noise-lock
        self._sim_buffer: deque[float] = deque(maxlen=200)
        self._no_selectivity_count = 0
        _NO_SELECTIVITY_RESET = 350  # ~4s at 86 fps → reset template
        self._no_selectivity_reset_threshold = _NO_SELECTIVITY_RESET

    # --- public API ---

    def update(self, mag: np.ndarray, is_beat: bool, frame_energy: float = 0.0) -> float:
        """Score current frame against the beat template.

        Args:
            mag: magnitude spectrum from rfft (shape: n_bins,)
            is_beat: whether the current frame is a detected beat
            frame_energy: RMS energy of the frame; low-energy frames are
                skipped to prevent noise from training the template.

        Returns:
            similarity: 0.0-1.0, how much this frame looks like a beat.
                        Returns 0.0 during bootstrap.
        """
        # Energy gate: skip noise-dominated frames
        if frame_energy < self._energy_threshold:
            return self._prev_similarity if self._template_ready else 0.0

        if not self._template_ready:
            if is_beat:
                self._beat_mags.append(mag.copy())
                if len(self._beat_mags) >= self.min_beats:
                    self._build_template()
            return 0.0

        similarity = self._cosine_similarity(mag)

        # Track selectivity: append to buffer and check variance
        self._sim_buffer.append(similarity)
        if not self.has_selectivity:
            self._no_selectivity_count += 1
            if self._no_selectivity_count >= self._no_selectivity_reset_threshold:
                self.reset()
                return 0.0
        else:
            self._no_selectivity_count = 0

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

    @property
    def has_selectivity(self) -> bool:
        """True if the template discriminates between beat and non-beat frames."""
        if len(self._sim_buffer) < 50:
            return True  # not enough data yet, assume OK
        return float(np.std(list(self._sim_buffer))) > 0.05

    def reset(self) -> None:
        """Clear template on song boundary."""
        self._beat_mags.clear()
        self._template = None
        self._template_ready = False
        self._prev_similarity = 0.0
        self._sim_buffer.clear()
        self._no_selectivity_count = 0

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
        freq_mask: np.ndarray | None = None,
    ) -> None:
        self._n_bins = n_bins
        self._kernel_size = kernel_size
        self._energy_gate = energy_gate
        self._freq_mask = freq_mask  # restrict onset to these bins (e.g. bass only)
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

        # Restrict to frequency mask (e.g. bass only) to filter out
        # speech, glass clinks, and other high-frequency bar noise.
        if self._freq_mask is not None:
            percussive = percussive[self._freq_mask]

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
    band_energies: tuple[float, ...] = ()
    band_ratios: tuple[float, ...] = ()
    band_fluxes: tuple[float, ...] = ()
    raw_mag: np.ndarray | None = None  # pre-subtraction mag, set when noise_floor is used


EQ_BAND_LIMITS_HZ: tuple[tuple[str, float, float], ...] = (
    ("sub", 20.0, 60.0),
    ("kick", 50.0, 130.0),
    ("bass", 60.0, 200.0),
    ("low_mid", 200.0, 600.0),
    ("mid", 600.0, 2000.0),
    ("presence", 2000.0, 5000.0),
    ("air", 5000.0, 12000.0),
)
EQ_BAND_NAMES: tuple[str, ...] = tuple(name for name, _lo, _hi in EQ_BAND_LIMITS_HZ)
_ZERO_EQ_BANDS: tuple[float, ...] = (0.0,) * len(EQ_BAND_NAMES)
_EQ_BAND_INDEX = {name: idx for idx, name in enumerate(EQ_BAND_NAMES)}
INSTRUMENT_PROXY_NAMES: tuple[str, ...] = (
    "drums",
    "bass",
    "vocals",
    "harmonic",
    "percussive",
)

_LIVE_EQ_ROUTE_DEFAULTS: dict[tuple[str, str], dict[str, object]] = {
    ("kick", "enter"): {
        "band": "kick",
        "when": "enter",
        "color_bias": "#ff5033",
        "render_mode": "pulse",
        "spatial_preset": "ripple_from_center",
        "intensity_boost": 0.15,
    },
    ("sub", "dominant"): {
        "band": "sub",
        "when": "dominant",
        "color_bias": "#ff6b3d",
        "render_mode": "pulse",
        "spatial_preset": "flash_floor_only",
        "intensity_boost": 0.08,
    },
    ("sub", "enter"): {
        "band": "sub",
        "when": "enter",
        "color_bias": "#ff6b3d",
        "render_mode": "pulse",
        "spatial_preset": "flash_floor_only",
        "intensity_boost": 0.14,
    },
    ("bass", "dominant"): {
        "band": "bass",
        "when": "dominant",
        "color_bias": "#ff8a3d",
        "render_mode": "pulse",
        "spatial_preset": "flash_floor_only",
        "intensity_boost": 0.06,
    },
    ("bass", "enter"): {
        "band": "bass",
        "when": "enter",
        "color_bias": "#ff8a3d",
        "render_mode": "pulse",
        "spatial_preset": "flash_floor_only",
        "intensity_boost": 0.15,
    },
    ("bass", "drop"): {
        "band": "bass",
        "when": "drop",
        "color_bias": "#ffb24d",
        "render_mode": "pulse",
        "spatial_preset": "ripple_from_center",
        "intensity_boost": 0.18,
    },
    ("presence", "dominant"): {
        "band": "presence",
        "when": "dominant",
        "color_bias": "#66ccff",
        "render_mode": "gradient",
        "spatial_preset": "flash_top_only",
        "intensity_boost": 0.05,
    },
    ("presence", "lift"): {
        "band": "presence",
        "when": "lift",
        "color_bias": "#66ccff",
        "render_mode": "gradient",
        "spatial_preset": "flash_top_only",
        "intensity_boost": 0.10,
    },
    ("air", "dominant"): {
        "band": "air",
        "when": "dominant",
        "color_bias": "#dff6ff",
        "render_mode": "gradient",
        "spatial_preset": "blend_left_to_right",
        "intensity_boost": 0.04,
    },
    ("air", "swell"): {
        "band": "air",
        "when": "swell",
        "color_bias": "#dff6ff",
        "render_mode": "gradient",
        "spatial_preset": "blend_front_to_back",
        "intensity_boost": 0.08,
    },
}

_LIVE_INSTRUMENT_ROUTE_DEFAULTS: dict[tuple[str, str], dict[str, object]] = {
    ("drums", "dominant"): {
        "instrument": "drums",
        "when": "dominant",
        "color_bias": "#ff5a36",
        "render_mode": "pulse",
        "spatial_preset": "ripple_from_center",
        "confidence_min": 0.45,
        "intensity_boost": 0.12,
    },
    ("drums", "enter"): {
        "instrument": "drums",
        "when": "enter",
        "color_bias": "#ff5a36",
        "render_mode": "pulse",
        "spatial_preset": "ripple_from_center",
        "confidence_min": 0.45,
        "intensity_boost": 0.18,
    },
    ("bass", "dominant"): {
        "instrument": "bass",
        "when": "dominant",
        "color_bias": "#ff8a3d",
        "render_mode": "pulse",
        "spatial_preset": "flash_floor_only",
        "confidence_min": 0.42,
        "intensity_boost": 0.10,
    },
    ("bass", "enter"): {
        "instrument": "bass",
        "when": "enter",
        "color_bias": "#ff8a3d",
        "render_mode": "pulse",
        "spatial_preset": "flash_floor_only",
        "confidence_min": 0.42,
        "intensity_boost": 0.16,
    },
    ("vocals", "dominant"): {
        "instrument": "vocals",
        "when": "dominant",
        "color_bias": "#cceeff",
        "render_mode": "gradient",
        "spatial_preset": "blend_left_to_right",
        "confidence_min": 0.48,
        "intensity_boost": 0.08,
    },
    ("vocals", "present"): {
        "instrument": "vocals",
        "when": "present",
        "color_bias": "#cceeff",
        "render_mode": "gradient",
        "spatial_preset": "blend_front_to_back",
        "confidence_min": 0.48,
        "intensity_boost": 0.04,
    },
    ("harmonic", "present"): {
        "instrument": "harmonic",
        "when": "present",
        "color_bias": "#b38cff",
        "render_mode": "wave",
        "spatial_preset": "blend_front_to_back",
        "confidence_min": 0.45,
        "intensity_boost": 0.04,
    },
    ("percussive", "present"): {
        "instrument": "percussive",
        "when": "present",
        "color_bias": "#ffd07a",
        "render_mode": "pulse",
        "spatial_preset": "ripple_from_center",
        "confidence_min": 0.48,
        "intensity_boost": 0.06,
    },
}


@dataclass(frozen=True)
class LiveEqState:
    dominant_band: str | None = None
    dominant_ratio: float = 0.0
    band_ratios: tuple[float, ...] = _ZERO_EQ_BANDS
    band_fluxes: tuple[float, ...] = _ZERO_EQ_BANDS
    events: tuple[str, ...] = ()
    trigger_keys: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class LivePanFrame:
    pan_center: float = 0.0
    pan_width: float = 0.0
    left_energy: float = 0.0
    right_energy: float = 0.0
    band_pan_centers: tuple[float, ...] = _ZERO_EQ_BANDS
    stereo_preserved: bool = False


@dataclass(frozen=True)
class LiveInstrumentState:
    dominant_proxy: str = ""
    drums: float = 0.0
    bass: float = 0.0
    vocals: float = 0.0
    harmonic: float = 0.0
    percussive: float = 0.0
    pan_center: float = 0.0
    pan_width: float = 0.0
    band_pan_centers: tuple[float, ...] = _ZERO_EQ_BANDS
    events: tuple[str, ...] = ()
    trigger_keys: tuple[tuple[str, str], ...] = ()


class LiveEqStateTracker:
    """Track smoothed live EQ state and fire lightweight band events."""

    def __init__(
        self,
        *,
        smoothing: float = 0.35,
        dominant_threshold: float = 0.14,
        enter_threshold: float = 0.17,
        exit_threshold: float = 0.08,
        presence_lift_threshold: float = 0.34,
        air_swell_threshold: float = 0.32,
        event_cooldown_seconds: float = 0.75,
    ) -> None:
        self.smoothing = smoothing
        self.dominant_threshold = dominant_threshold
        self.enter_threshold = enter_threshold
        self.exit_threshold = exit_threshold
        self.presence_lift_threshold = presence_lift_threshold
        self.air_swell_threshold = air_swell_threshold
        self.event_cooldown_seconds = event_cooldown_seconds
        self.reset()

    def reset(self) -> None:
        self._ready = False
        self._smoothed_ratios = np.zeros(len(EQ_BAND_NAMES), dtype=np.float32)
        self._smoothed_fluxes = np.zeros(len(EQ_BAND_NAMES), dtype=np.float32)
        self._last_event_t: dict[tuple[str, str], float] = {}

    def update(self, spectral: SpectralFeatures, t: float) -> LiveEqState:
        ratios = np.asarray(spectral.band_ratios or _ZERO_EQ_BANDS, dtype=np.float32)
        raw_fluxes = np.asarray(spectral.band_fluxes or _ZERO_EQ_BANDS, dtype=np.float32)
        flux_total = float(raw_fluxes.sum())
        if flux_total > 1e-8:
            fluxes = raw_fluxes / flux_total
        else:
            fluxes = np.zeros(len(EQ_BAND_NAMES), dtype=np.float32)

        prev_ratios = self._smoothed_ratios.copy()
        prev_fluxes = self._smoothed_fluxes.copy()
        if not self._ready:
            self._smoothed_ratios = ratios.copy()
            self._smoothed_fluxes = fluxes.copy()
            self._ready = True
        else:
            alpha = float(self.smoothing)
            self._smoothed_ratios = (alpha * ratios) + ((1.0 - alpha) * self._smoothed_ratios)
            self._smoothed_fluxes = (alpha * fluxes) + ((1.0 - alpha) * self._smoothed_fluxes)

        events: list[str] = []
        trigger_keys: list[tuple[str, str]] = []

        for band_name in ("sub", "kick", "bass"):
            idx = _EQ_BAND_INDEX[band_name]
            prev_val = float(prev_ratios[idx])
            curr_val = float(self._smoothed_ratios[idx])
            if prev_val < self.enter_threshold <= curr_val and self._allow_event(band_name, "enter", t):
                trigger_keys.append((band_name, "enter"))
                events.append(f"{band_name}_enter")
            elif band_name == "bass" and prev_val >= self.enter_threshold and curr_val < self.exit_threshold and self._allow_event("bass", "drop", t):
                trigger_keys.append(("bass", "drop"))
                events.append("bass_drop")

        if self._band_flux_event("presence", prev_fluxes, self.presence_lift_threshold, t, "lift"):
            trigger_keys.append(("presence", "lift"))
            events.append("presence_lift")
        if self._band_flux_event("air", prev_fluxes, self.air_swell_threshold, t, "swell"):
            trigger_keys.append(("air", "swell"))
            events.append("air_swell")

        dominant_idx = int(np.argmax(self._smoothed_ratios))
        dominant_ratio = float(self._smoothed_ratios[dominant_idx])
        dominant_band = EQ_BAND_NAMES[dominant_idx] if dominant_ratio >= self.dominant_threshold else None
        if dominant_band is not None and (dominant_band, "dominant") not in trigger_keys:
            trigger_keys.append((dominant_band, "dominant"))

        return LiveEqState(
            dominant_band=dominant_band,
            dominant_ratio=dominant_ratio if dominant_band is not None else 0.0,
            band_ratios=tuple(float(value) for value in self._smoothed_ratios),
            band_fluxes=tuple(float(value) for value in self._smoothed_fluxes),
            events=tuple(events),
            trigger_keys=tuple(trigger_keys),
        )

    def _band_flux_event(
        self,
        band_name: str,
        prev_fluxes: np.ndarray,
        threshold: float,
        t: float,
        when: str,
    ) -> bool:
        idx = _EQ_BAND_INDEX[band_name]
        ratio_floor = 0.06 if band_name == "presence" else 0.03
        prev_val = float(prev_fluxes[idx])
        curr_val = float(self._smoothed_fluxes[idx])
        if prev_val < threshold <= curr_val and float(self._smoothed_ratios[idx]) >= ratio_floor:
            return self._allow_event(band_name, when, t)
        return False

    def _allow_event(self, band_name: str, when: str, t: float) -> bool:
        key = (band_name, when)
        last_t = self._last_event_t.get(key, -1e9)
        if (t - last_t) < self.event_cooldown_seconds:
            return False
        self._last_event_t[key] = t
        return True


class LiveInstrumentStateTracker:
    """Track smoothed mixed-source instrument proxies for live routing."""

    def __init__(
        self,
        *,
        smoothing: float = 0.35,
        dominant_threshold: float = 0.20,
        present_threshold: float = 0.18,
        exit_threshold: float = 0.12,
        dominant_hold_seconds: float = 0.65,
        dominant_margin: float = 0.06,
        event_cooldown_seconds: float = 0.75,
    ) -> None:
        self.smoothing = smoothing
        self.dominant_threshold = dominant_threshold
        self.present_threshold = present_threshold
        self.exit_threshold = exit_threshold
        self.dominant_hold_seconds = dominant_hold_seconds
        self.dominant_margin = dominant_margin
        self.event_cooldown_seconds = event_cooldown_seconds
        self.reset()

    def reset(self) -> None:
        self._ready = False
        self._scores = {name: 0.0 for name in INSTRUMENT_PROXY_NAMES}
        self._present = {name: False for name in INSTRUMENT_PROXY_NAMES}
        self._dominant_proxy = ""
        self._dominant_since = -1e9
        self._last_event_t: dict[tuple[str, str], float] = {}

    def update(
        self,
        spectral: SpectralFeatures,
        frame_features: dict[str, float | bool] | None,
        *,
        percussive_onset: float,
        t: float,
    ) -> LiveInstrumentState:
        current_scores = self._current_scores(spectral, frame_features, percussive_onset)
        previous_scores = dict(self._scores)

        if not self._ready:
            self._scores = current_scores
            self._ready = True
        else:
            alpha = float(self.smoothing)
            for name in INSTRUMENT_PROXY_NAMES:
                self._scores[name] = (alpha * current_scores[name]) + ((1.0 - alpha) * self._scores[name])

        events: list[str] = []
        trigger_keys: list[tuple[str, str]] = []
        present_now: dict[str, bool] = {}
        for name in INSTRUMENT_PROXY_NAMES:
            prev_score = float(previous_scores.get(name, 0.0))
            curr_score = float(self._scores[name])
            was_present = bool(self._present.get(name, False))
            is_present = curr_score >= (self.exit_threshold if was_present else self.present_threshold)
            present_now[name] = is_present
            if is_present:
                trigger_keys.append((name, "present"))
            if not was_present and is_present and self._allow_event(name, "enter", t):
                events.append(f"{name}_enter")
                trigger_keys.append((name, "enter"))
            elif was_present and not is_present and self._allow_event(name, "drop", t):
                events.append(f"{name}_drop")
                trigger_keys.append((name, "drop"))
            elif prev_score < self.present_threshold <= curr_score and self._allow_event(name, "enter", t):
                events.append(f"{name}_enter")
                if (name, "enter") not in trigger_keys:
                    trigger_keys.append((name, "enter"))
        self._present = present_now

        dominant_proxy = self._select_dominant_proxy(t, previous_scores)
        self._dominant_proxy = dominant_proxy
        if dominant_proxy and (dominant_proxy, "dominant") not in trigger_keys:
            trigger_keys.insert(0, (dominant_proxy, "dominant"))

        band_pan_centers = tuple(
            float(value) for value in frame_features.get("band_pan_centers", _ZERO_EQ_BANDS)
        ) if frame_features else _ZERO_EQ_BANDS
        return LiveInstrumentState(
            dominant_proxy=dominant_proxy,
            drums=round(float(self._scores["drums"]), 4),
            bass=round(float(self._scores["bass"]), 4),
            vocals=round(float(self._scores["vocals"]), 4),
            harmonic=round(float(self._scores["harmonic"]), 4),
            percussive=round(float(self._scores["percussive"]), 4),
            pan_center=round(float(frame_features.get("pan_center", 0.0)) if frame_features else 0.0, 4),
            pan_width=round(float(frame_features.get("pan_width", 0.0)) if frame_features else 0.0, 4),
            band_pan_centers=tuple(round(float(value), 4) for value in band_pan_centers),
            events=tuple(events),
            trigger_keys=tuple(trigger_keys),
        )

    def _current_scores(
        self,
        spectral: SpectralFeatures,
        frame_features: dict[str, float | bool] | None,
        percussive_onset: float,
    ) -> dict[str, float]:
        band_ratios = tuple(float(value) for value in (spectral.band_ratios or _ZERO_EQ_BANDS))
        raw_fluxes = np.asarray(spectral.band_fluxes or _ZERO_EQ_BANDS, dtype=np.float32)
        flux_total = float(raw_fluxes.sum()) + 1e-8
        flux_ratios = tuple(float(value / flux_total) for value in raw_fluxes)

        onset_strength = self._compress_feature(float(frame_features.get("onset_strength", 0.0)) if frame_features else 0.0, scale=8.0)
        spectral_flux = self._compress_feature(float(frame_features.get("spectral_flux", 0.0)) if frame_features else 0.0, scale=35.0)
        kick_flux = self._compress_feature(float(spectral.kick_spectral_flux), scale=20.0)
        percussive_level = self._compress_feature(float(percussive_onset), scale=10.0)
        pan_center = float(frame_features.get("pan_center", 0.0)) if frame_features else 0.0
        pan_width = max(0.0, min(1.0, float(frame_features.get("pan_width", 0.0)) if frame_features else 0.0))
        center_bias = 1.0 - min(1.0, abs(pan_center))

        drums = _clamp01(
            (0.32 * kick_flux)
            + (0.28 * percussive_level)
            + (0.22 * onset_strength)
            + (0.18 * flux_ratios[_EQ_BAND_INDEX["kick"]])
        )
        bass = _clamp01(
            (0.36 * float(spectral.bass_ratio))
            + (0.32 * band_ratios[_EQ_BAND_INDEX["bass"]])
            + (0.18 * band_ratios[_EQ_BAND_INDEX["sub"]])
            + (0.08 * (1.0 - min(1.0, pan_width * 0.7)))
            + (0.06 * self._compress_feature(float(spectral.bass), scale=90.0))
        )
        percussive = _clamp01(
            (0.28 * onset_strength)
            + (0.24 * spectral_flux)
            + (0.24 * percussive_level)
            + (0.24 * flux_ratios[_EQ_BAND_INDEX["kick"]])
        )
        harmonic = _clamp01(
            (0.34 * band_ratios[_EQ_BAND_INDEX["low_mid"]])
            + (0.30 * band_ratios[_EQ_BAND_INDEX["mid"]])
            + (0.18 * band_ratios[_EQ_BAND_INDEX["presence"]])
            + (0.10 * pan_width)
            + (0.08 * (1.0 - percussive))
        )
        vocals = _clamp01(
            (0.38 * band_ratios[_EQ_BAND_INDEX["presence"]])
            + (0.26 * band_ratios[_EQ_BAND_INDEX["mid"]])
            + (0.10 * flux_ratios[_EQ_BAND_INDEX["presence"]])
            + (0.08 * center_bias)
            + (0.05 * (1.0 - pan_width))
            + (0.04 * (1.0 - percussive))
        )
        return {
            "drums": drums,
            "bass": bass,
            "vocals": vocals,
            "harmonic": harmonic,
            "percussive": percussive,
        }

    def _select_dominant_proxy(self, t: float, previous_scores: dict[str, float]) -> str:
        best_name = ""
        best_score = 0.0
        for name in INSTRUMENT_PROXY_NAMES:
            score = float(self._scores[name])
            if score > best_score:
                best_name = name
                best_score = score
        if best_score < self.dominant_threshold:
            if self._dominant_proxy and (t - self._dominant_since) < self.dominant_hold_seconds:
                held_score = max(
                    float(previous_scores.get(self._dominant_proxy, 0.0)),
                    float(self._scores.get(self._dominant_proxy, 0.0)),
                )
                if held_score >= self.present_threshold:
                    return self._dominant_proxy
            self._dominant_since = -1e9
            return ""
        if not self._dominant_proxy:
            self._dominant_since = t
            return best_name
        if best_name == self._dominant_proxy:
            if self._dominant_since < 0:
                self._dominant_since = t
            return best_name
        held_score = max(
            float(previous_scores.get(self._dominant_proxy, 0.0)),
            float(self._scores.get(self._dominant_proxy, 0.0)),
        )
        if (t - self._dominant_since) < self.dominant_hold_seconds and held_score >= self.present_threshold:
            return self._dominant_proxy
        if best_score < (held_score + self.dominant_margin):
            return self._dominant_proxy
        self._dominant_since = t
        return best_name

    def _allow_event(self, instrument: str, when: str, t: float) -> bool:
        key = (instrument, when)
        last_t = self._last_event_t.get(key, -1e9)
        if (t - last_t) < self.event_cooldown_seconds:
            return False
        self._last_event_t[key] = t
        return True

    @staticmethod
    def _compress_feature(value: float, *, scale: float) -> float:
        value = max(0.0, float(value))
        if scale <= 1e-8:
            return _clamp01(value)
        return value / (value + scale)


def _build_eq_band_masks(freqs: np.ndarray) -> tuple[np.ndarray, ...]:
    """Build boolean masks for the named EQ bands."""
    return tuple(
        (freqs >= low_hz) & (freqs <= high_hz)
        for _name, low_hz, high_hz in EQ_BAND_LIMITS_HZ
    )


def _compute_eq_band_features(
    mag: np.ndarray,
    prev_mag: np.ndarray | None,
    band_masks: tuple[np.ndarray, ...],
) -> tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]]:
    """Compute named band energies, ratios, and positive-delta flux."""
    total = float(mag.sum()) + 1e-8
    band_energies = tuple(float(mag[mask].sum()) for mask in band_masks)
    band_ratios = tuple(energy / total for energy in band_energies)
    if prev_mag is None:
        band_fluxes = _ZERO_EQ_BANDS
    else:
        band_fluxes = tuple(
            float(np.maximum(0.0, mag[mask] - prev_mag[mask]).sum())
            for mask in band_masks
        )
    return band_energies, band_ratios, band_fluxes


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

    if freqs is not None:
        band_masks = _build_eq_band_masks(freqs)
        band_energies, band_ratios, band_fluxes = _compute_eq_band_features(
            mag, prev_mag, band_masks,
        )
    else:
        band_energies = _ZERO_EQ_BANDS
        band_ratios = _ZERO_EQ_BANDS
        band_fluxes = _ZERO_EQ_BANDS

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
        band_energies=band_energies,
        band_ratios=band_ratios,
        band_fluxes=band_fluxes,
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
    pan_center: float = 0.0,
    pan_width: float = 0.0,
    left_energy: float = 0.0,
    right_energy: float = 0.0,
    band_pan_centers: tuple[float, ...] = _ZERO_EQ_BANDS,
    stereo_preserved: bool = False,
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
        "pan_center": float(pan_center),
        "pan_width": float(pan_width),
        "left_energy": float(left_energy),
        "right_energy": float(right_energy),
        "band_pan_centers": tuple(float(value) for value in band_pan_centers),
        "stereo_preserved": bool(stereo_preserved),
    }


def _prepare_live_audio_chunk(
    indata: np.ndarray,
) -> tuple[np.ndarray, np.ndarray | None, bool]:
    arr = np.asarray(indata, dtype=np.float32)
    if arr.ndim == 1:
        mono = arr.copy()
        return mono, None, False
    if arr.ndim != 2 or arr.shape[0] == 0:
        return np.zeros(0, dtype=np.float32), None, False
    if arr.shape[1] >= 2:
        stereo = np.asarray(arr[:, :2], dtype=np.float32).copy()
        mono = stereo.mean(axis=1, dtype=np.float32)
        return mono, stereo, True
    mono = np.asarray(arr[:, 0], dtype=np.float32).copy()
    return mono, None, False


def _stereo_pan_features_live(
    stereo_frame: np.ndarray | None,
    window: np.ndarray,
    band_masks: tuple[np.ndarray, ...],
) -> LivePanFrame:
    if stereo_frame is None or stereo_frame.ndim != 2 or stereo_frame.shape[1] < 2:
        return LivePanFrame()

    left = np.asarray(stereo_frame[:, 0], dtype=np.float32)
    right = np.asarray(stereo_frame[:, 1], dtype=np.float32)
    left_energy = float(np.mean(left ** 2))
    right_energy = float(np.mean(right ** 2))
    total_energy = left_energy + right_energy + 1e-8
    pan_center = float((right_energy - left_energy) / total_energy)
    width_num = float(np.mean(np.abs(left - right)))
    width_den = float(np.mean(np.abs(left) + np.abs(right))) + 1e-8
    pan_width = max(0.0, min(1.0, width_num / width_den))

    left_mag = np.abs(np.fft.rfft(left * window))
    right_mag = np.abs(np.fft.rfft(right * window))
    left_band_energies, _left_ratios, _left_fluxes = _compute_eq_band_features(
        left_mag,
        None,
        band_masks,
    )
    right_band_energies, _right_ratios, _right_fluxes = _compute_eq_band_features(
        right_mag,
        None,
        band_masks,
    )
    band_pan_centers = tuple(
        float((right_band - left_band) / (right_band + left_band + 1e-8))
        for left_band, right_band in zip(left_band_energies, right_band_energies, strict=False)
    )
    return LivePanFrame(
        pan_center=pan_center,
        pan_width=pan_width,
        left_energy=left_energy,
        right_energy=right_energy,
        band_pan_centers=band_pan_centers,
        stereo_preserved=True,
    )


def _normalize_eq_routes(raw: object) -> list[dict[str, object]]:
    if not isinstance(raw, list):
        return []
    routes: list[dict[str, object]] = []
    for route in raw:
        if isinstance(route, dict):
            routes.append(dict(route))
    return routes


def _normalize_instrument_routes(raw: object) -> list[dict[str, object]]:
    if not isinstance(raw, list):
        return []
    routes: list[dict[str, object]] = []
    for route in raw:
        if isinstance(route, dict):
            routes.append(dict(route))
    return routes


def _merge_eq_routes(base_routes: list[dict[str, object]], override_routes: list[dict[str, object]]) -> list[dict[str, object]]:
    merged: dict[tuple[str, str], dict[str, object]] = {
        (str(route.get("band", "")), str(route.get("when", ""))): dict(route)
        for route in base_routes
    }
    order = [
        (str(route.get("band", "")), str(route.get("when", "")))
        for route in base_routes
    ]
    for route in override_routes:
        key = (str(route.get("band", "")), str(route.get("when", "")))
        if key not in merged:
            order.append(key)
        existing = dict(merged.get(key, {}))
        existing.update(route)
        merged[key] = existing
    return [merged[key] for key in order if key in merged]


def _merge_instrument_routes(
    base_routes: list[dict[str, object]],
    override_routes: list[dict[str, object]],
) -> list[dict[str, object]]:
    merged: dict[tuple[str, str], dict[str, object]] = {
        (str(route.get("instrument", "")), str(route.get("when", ""))): dict(route)
        for route in base_routes
    }
    order = [
        (str(route.get("instrument", "")), str(route.get("when", "")))
        for route in base_routes
    ]
    for route in override_routes:
        key = (str(route.get("instrument", "")), str(route.get("when", "")))
        if key not in merged:
            order.append(key)
        existing = dict(merged.get(key, {}))
        existing.update(route)
        merged[key] = existing
    return [merged[key] for key in order if key in merged]


def _resolve_live_eq_routes(state: LiveEqState | None, params: dict[str, object] | None) -> list[dict[str, object]]:
    if state is None or not state.trigger_keys:
        return []
    configured_routes = _normalize_eq_routes((params or {}).get("eq_routes"))
    default_routes = [
        dict(_LIVE_EQ_ROUTE_DEFAULTS[key])
        for key in state.trigger_keys
        if key in _LIVE_EQ_ROUTE_DEFAULTS
    ]
    matching_configured = [
        route for route in configured_routes
        if (str(route.get("band", "")), str(route.get("when", ""))) in state.trigger_keys
    ]
    return _sort_active_routes(_merge_eq_routes(default_routes, matching_configured))


def _resolve_live_instrument_routes(
    state: LiveInstrumentState | None,
    params: dict[str, object] | None,
) -> list[dict[str, object]]:
    if state is None or not state.trigger_keys:
        return []
    configured_routes = _normalize_instrument_routes((params or {}).get("instrument_routes"))
    default_routes = [
        dict(_LIVE_INSTRUMENT_ROUTE_DEFAULTS[key])
        for key in state.trigger_keys
        if key in _LIVE_INSTRUMENT_ROUTE_DEFAULTS
    ]
    matching_configured = [
        route for route in configured_routes
        if (str(route.get("instrument", "")), str(route.get("when", ""))) in state.trigger_keys
    ]
    merged_routes = _merge_instrument_routes(default_routes, matching_configured)
    active_routes = [
        _enrich_live_instrument_route(route, state)
        for route in merged_routes
        if _instrument_route_is_active(route, state)
    ]
    dominant_instruments = {
        str(route.get("instrument", ""))
        for route in active_routes
        if str(route.get("when", "")) == "dominant"
    }
    if not dominant_instruments:
        return _sort_active_routes(active_routes)
    return _sort_active_routes([
        route for route in active_routes
        if not (
            str(route.get("when", "")) == "present"
            and str(route.get("instrument", "")) in dominant_instruments
        )
    ])


def _build_scene_layers(routes: list[dict[str, object]]) -> list[dict[str, object]]:
    layers: list[dict[str, object]] = []
    for route in routes:
        layer: dict[str, object] = {
            "when": str(route.get("when", "")),
            "layer_blend": str(route.get("layer_blend", "max") or "max"),
            "layer_weight": round(
                max(0.15, min(1.0, 0.65 + float(route.get("intensity_boost", 0.0) or 0.0))),
                3,
            ),
        }
        if "band" in route:
            layer["band"] = str(route.get("band", ""))
        if "instrument" in route:
            layer["instrument"] = str(route.get("instrument", ""))
        for key in (
            "color_bias",
            "spatial_preset",
            "spatial_mode",
            "spatial_origin",
            "spatial_direction",
            "spatial_width",
            "spatial_blend",
            "spatial_extent",
            "spatial_delay_ms",
            "spatial_axis",
            "spatial_focus",
            "spatial_zone",
            "pan_follow",
            "width_scale",
            "confidence_min",
            "pan_center",
            "pan_width",
            "band_pan_centers",
        ):
            if key in route:
                layer[key] = route[key]
        if any(
            key in layer
            for key in (
                "color_bias",
                "spatial_preset",
                "spatial_mode",
                "spatial_origin",
                "spatial_direction",
                "spatial_width",
                "spatial_blend",
                "spatial_extent",
                "spatial_delay_ms",
                "spatial_axis",
                "spatial_focus",
                "spatial_zone",
                "pan_follow",
                "width_scale",
            )
        ):
                layers.append(layer)
    return layers


def _build_eq_layers(routes: list[dict[str, object]]) -> list[dict[str, object]]:
    """Backward-compatible alias for the legacy layer name."""

    return _build_scene_layers(routes)


def _first_route_value(routes: list[dict[str, object]], key: str) -> object | None:
    for route in routes:
        value = route.get(key)
        if value is not None:
            return value
    return None


def _route_priority(route: dict[str, object]) -> tuple[int, float]:
    when = str(route.get("when", "")).strip().lower()
    is_instrument = bool(str(route.get("instrument", "")).strip())
    if is_instrument:
        priority = {
            "dominant": 0,
            "enter": 1,
            "present": 2,
            "drop": 3,
        }.get(when, 4)
    else:
        priority = {
            "dominant": 5,
            "enter": 6,
            "lift": 7,
            "swell": 8,
            "drop": 9,
        }.get(when, 10)
    return priority, -float(route.get("intensity_boost", 0.0) or 0.0)


def _sort_active_routes(routes: list[dict[str, object]]) -> list[dict[str, object]]:
    return sorted((dict(route) for route in routes), key=_route_priority)


def _instrument_score(state: LiveInstrumentState, instrument: str) -> float:
    if instrument not in INSTRUMENT_PROXY_NAMES:
        return 0.0
    return float(getattr(state, instrument, 0.0))


def _spatial_origin_for_zone(zone: str) -> dict[str, float] | None:
    if not zone:
        return None
    alias = zone.strip().lower()
    if alias in {"left", "right", "top", "bottom", "front", "back", "center", "balanced"}:
        return {
            "left": {"x": -1.0, "y": 0.0, "z": 0.0},
            "right": {"x": 1.0, "y": 0.0, "z": 0.0},
            "top": {"x": 0.0, "y": 1.0, "z": 0.0},
            "bottom": {"x": 0.0, "y": -1.0, "z": 0.0},
            "front": {"x": 0.0, "y": 0.0, "z": -1.0},
            "back": {"x": 0.0, "y": 0.0, "z": 1.0},
            "center": {"x": 0.0, "y": 0.0, "z": 0.0},
            "balanced": {"x": 0.0, "y": 0.0, "z": 0.0},
        }[alias].copy()

    parts = alias.replace("-", "_").split("_")
    x = 0.0
    y = 0.0
    z = 0.0
    matched = False
    for part in parts:
        if part == "left":
            x = -1.0
            matched = True
        elif part == "right":
            x = 1.0
            matched = True
        elif part in {"top", "ceiling", "high"}:
            y = 1.0
            matched = True
        elif part in {"bottom", "floor", "low"}:
            y = -1.0
            matched = True
        elif part == "front":
            z = -1.0
            matched = True
        elif part == "back":
            z = 1.0
            matched = True
        elif part in {"center", "mid", "middle"}:
            matched = True
    if not matched:
        return None
    return {"x": x, "y": y, "z": z}


def _spatial_focus_for_zone(zone: str) -> str | None:
    if not zone:
        return None
    alias = zone.strip().lower()
    if alias in {"left", "right", "top", "bottom", "front", "back", "center", "balanced"}:
        return alias
    return None


def _instrument_route_is_active(route: dict[str, object], state: LiveInstrumentState) -> bool:
    instrument = str(route.get("instrument", "")).strip().lower()
    when = str(route.get("when", "dominant")).strip().lower()
    if instrument not in INSTRUMENT_PROXY_NAMES:
        return False
    if (instrument, when) not in state.trigger_keys:
        return False
    current_score = _instrument_score(state, instrument)
    threshold = float(route.get("confidence_min", 0.45) or 0.45)
    if when == "drop":
        return True
    if when == "dominant":
        return state.dominant_proxy == instrument and current_score >= threshold
    if when in {"present", "enter"}:
        return current_score >= threshold
    return False


def _enrich_live_instrument_route(
    route: dict[str, object],
    state: LiveInstrumentState,
) -> dict[str, object]:
    enriched = dict(route)
    pan_follow = max(0.0, min(1.0, float(route.get("pan_follow", 0.0) or 0.0)))
    width_scale = max(0.0, float(route.get("width_scale", 1.0) or 1.0))
    pan_center = max(-1.0, min(1.0, float(state.pan_center)))
    pan_width = max(0.0, min(1.0, float(state.pan_width)))
    enriched["pan_center"] = round(pan_center, 4)
    enriched["pan_width"] = round(pan_width, 4)
    if any(abs(value) > 1e-6 for value in state.band_pan_centers):
        enriched["band_pan_centers"] = tuple(round(float(value), 4) for value in state.band_pan_centers)

    origin = _spatial_origin_for_zone(str(route.get("spatial_zone", "") or ""))
    if origin is None:
        origin = {"x": 0.0, "y": 0.0, "z": 0.0}
    origin["x"] = round(max(-1.0, min(1.0, float(origin["x"]) + (pan_center * pan_follow))), 4)
    enriched["spatial_origin"] = origin
    if "spatial_width" not in enriched:
        enriched["spatial_width"] = round(
            max(0.12, min(1.5, 0.18 + (pan_width * width_scale))),
            4,
        )
    focus = _spatial_focus_for_zone(str(route.get("spatial_zone", "") or ""))
    if focus is not None and "spatial_focus" not in enriched:
        enriched["spatial_focus"] = focus
    return enriched


def _apply_live_routes_to_intent(intent: Any, routes: list[dict[str, object]]) -> Any:
    if not routes:
        return intent
    color_bias = _first_route_value(routes, "color_bias")
    intensity_boost = sum(float(route.get("intensity_boost", 0.0) or 0.0) for route in routes)
    return dataclasses.replace(
        intent,
        intensity=min(1.0, max(0.0, float(intent.intensity) + intensity_boost)),
        color=str(color_bias) if color_bias is not None else intent.color,
    )


def _apply_live_eq_to_intent(intent: Any, routes: list[dict[str, object]]) -> Any:
    return _apply_live_routes_to_intent(intent, routes)


def _band_dict(names: tuple[str, ...], values: tuple[float, ...]) -> dict[str, float]:
    return {
        name: round(float(value), 4)
        for name, value in zip(names, values, strict=False)
    }


def _instrument_score_dict(state: LiveInstrumentState | None) -> dict[str, float]:
    if state is None:
        return {name: 0.0 for name in INSTRUMENT_PROXY_NAMES}
    return {
        "drums": round(float(state.drums), 4),
        "bass": round(float(state.bass), 4),
        "vocals": round(float(state.vocals), 4),
        "harmonic": round(float(state.harmonic), 4),
        "percussive": round(float(state.percussive), 4),
    }


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


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
    profile_chain: Any | None = None,
    runtime_control_getter: Any | None = None,
    state_callback: Any | None = None,
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
    audio_queue: deque[tuple[np.ndarray, np.ndarray | None]] = deque()
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
    live_eq_tracker = LiveEqStateTracker()
    last_live_eq_state: LiveEqState | None = None
    live_instrument_tracker = LiveInstrumentStateTracker()
    last_live_instrument_state: LiveInstrumentState | None = None
    beat_count = 0
    sent_count = 0
    dropped_blocks = 0
    captured_samples = 0
    stereo_chunks_captured = 0
    mono_fallback_chunks = 0
    live_stereo_preserved = False

    # Seed with an initial intent so we always have something to render
    last_intent = director.update({"t": 0.0, "rms": 0.0, "zcr": 0.0, "bpm": 120.0, "beat": False, "bass": 0.0})

    def _runtime_params_for_frame(
        intent: Any,
    ) -> tuple[dict[str, Any] | None, list[dict[str, object]], list[dict[str, object]]]:
        runtime_params: dict[str, Any] = dict(current_params or {})
        if preset is not None:
            runtime_params.setdefault("_render_mode", preset.render_mode.value)
        elif intent.mode == EffectMode.RIPPLE:
            runtime_params.setdefault("_render_mode", "ripple")
        elif intent.mode == EffectMode.MOTION:
            runtime_params.setdefault("_render_mode", "wave")
        elif intent.mode == EffectMode.PULSE:
            runtime_params.setdefault("_render_mode", "pulse")
        else:
            runtime_params.setdefault("_render_mode", "solid")

        if intent.mode == EffectMode.RIPPLE:
            runtime_params.setdefault("spatial_preset", "ripple_from_center")

        active_live_eq_routes = _resolve_live_eq_routes(last_live_eq_state, runtime_params)
        if active_live_eq_routes:
            configured_routes = _normalize_eq_routes(runtime_params.get("eq_routes"))
            active_keys = {
                (str(route.get("band", "")), str(route.get("when", "")))
                for route in active_live_eq_routes
            }
            passthrough_routes = [
                route for route in configured_routes
                if (str(route.get("band", "")), str(route.get("when", ""))) not in active_keys
            ]
            runtime_params["eq_routes"] = active_live_eq_routes + passthrough_routes
            runtime_params["active_eq_routes"] = [dict(route) for route in active_live_eq_routes]

        active_live_instrument_routes = _resolve_live_instrument_routes(last_live_instrument_state, runtime_params)
        if active_live_instrument_routes:
            configured_instrument_routes = _normalize_instrument_routes(runtime_params.get("instrument_routes"))
            active_keys = {
                (str(route.get("instrument", "")), str(route.get("when", "")))
                for route in active_live_instrument_routes
            }
            passthrough_routes = [
                route for route in configured_instrument_routes
                if (str(route.get("instrument", "")), str(route.get("when", ""))) not in active_keys
            ]
            runtime_params["instrument_routes"] = active_live_instrument_routes + passthrough_routes
            runtime_params["active_instrument_routes"] = [dict(route) for route in active_live_instrument_routes]

        all_active_routes = active_live_instrument_routes + active_live_eq_routes
        if all_active_routes:
            route_layers = _build_scene_layers(all_active_routes)
            if route_layers:
                runtime_params["scene_layers"] = route_layers
                runtime_params["eq_layers"] = route_layers
            route_spatial_preset = _first_route_value(all_active_routes, "spatial_preset")
            if route_spatial_preset is not None and "spatial_preset" not in runtime_params:
                runtime_params["spatial_preset"] = route_spatial_preset
            route_render_mode = _first_route_value(all_active_routes, "render_mode")
            if route_render_mode is not None:
                runtime_params["_render_mode"] = str(route_render_mode)

        if last_live_eq_state is not None:
            runtime_params["dominant_band"] = last_live_eq_state.dominant_band
            runtime_params["dominant_band_ratio"] = round(last_live_eq_state.dominant_ratio, 4)
            runtime_params["eq_events"] = list(last_live_eq_state.events)
        if last_live_instrument_state is not None:
            runtime_params["dominant_proxy"] = last_live_instrument_state.dominant_proxy
            runtime_params["instrument_events"] = list(last_live_instrument_state.events)
            runtime_params["instrument_proxy"] = {
                **_instrument_score_dict(last_live_instrument_state),
                "dominant_proxy": last_live_instrument_state.dominant_proxy,
                "pan_center": last_live_instrument_state.pan_center,
                "pan_width": last_live_instrument_state.pan_width,
                "band_pan_centers": tuple(last_live_instrument_state.band_pan_centers),
            }

        return runtime_params or None, active_live_eq_routes, active_live_instrument_routes

    def _callback(indata, frames, time_info, status) -> None:
        del frames, time_info
        nonlocal dropped_blocks, captured_samples, stereo_chunks_captured, mono_fallback_chunks
        if status and getattr(status, "input_overflow", False):
            dropped_blocks += 1
        mono_chunk, stereo_chunk, stereo_preserved = _prepare_live_audio_chunk(indata)
        captured_samples += int(mono_chunk.shape[0])
        if stereo_preserved and stereo_chunk is not None:
            stereo_chunks_captured += 1
        else:
            mono_fallback_chunks += 1
        audio_queue.append((mono_chunk, stereo_chunk))

    stream_t = 0.0
    buffer = np.zeros(0, dtype=np.float32)
    stereo_buffer: np.ndarray | None = None
    started_at = time.monotonic()
    next_telemetry = started_at + max(0.1, telemetry_interval_seconds)
    last_print = started_at
    window, bass_mask, kick_mask, freqs = _prepare_bass_window(frame_size, sample_rate)
    band_masks = _build_eq_band_masks(freqs)
    n_bins = frame_size // 2 + 1
    # Bass-frequency mask for percussive onset: restrict to < 300 Hz to
    # filter out speech, glass clinks, and other high-frequency bar noise.
    perc_freq_mask = freqs <= 300.0
    noise_estimator = NoiseFloorEstimator(n_bins=n_bins)
    percussive_tracker = PercussiveOnsetTracker(
        n_bins=n_bins, energy_gate=1.0, freq_mask=perc_freq_mask,
    )
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
                mono_chunk, stereo_chunk = audio_queue.popleft()
                buffer = np.concatenate([buffer, mono_chunk])
                if stereo_chunk is not None:
                    live_stereo_preserved = True
                    if stereo_buffer is None:
                        stereo_buffer = stereo_chunk
                    else:
                        stereo_buffer = np.concatenate([stereo_buffer, stereo_chunk], axis=0)

            # Process audio frames for beat detection + feature extraction
            beat_this_tick = False
            last_features: dict[str, float | bool] | None = None
            last_sf: SpectralFeatures | None = None
            last_wf: float = 0.0
            perc: float = 0.0
            last_pan = LivePanFrame(stereo_preserved=live_stereo_preserved)
            while buffer.shape[0] >= frame_size:
                frame = buffer[:frame_size]
                buffer = buffer[hop_size:]
                stereo_frame = None
                if stereo_buffer is not None and stereo_buffer.shape[0] >= frame_size:
                    stereo_frame = stereo_buffer[:frame_size]
                    stereo_buffer = stereo_buffer[hop_size:]
                last_pan = _stereo_pan_features_live(stereo_frame, window, band_masks)
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
                    live_eq_tracker.reset()
                    last_live_eq_state = None
                    live_instrument_tracker.reset()
                    last_live_instrument_state = None
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
                    pan_center=last_pan.pan_center,
                    pan_width=last_pan.pan_width,
                    left_energy=last_pan.left_energy,
                    right_energy=last_pan.right_energy,
                    band_pan_centers=last_pan.band_pan_centers,
                    stereo_preserved=last_pan.stereo_preserved,
                )
                last_live_eq_state = live_eq_tracker.update(sf, stream_t)
                last_live_instrument_state = live_instrument_tracker.update(
                    sf,
                    last_features,
                    percussive_onset=perc,
                    t=stream_t,
                )
                last_intent = director.update(last_features)

                if beat:
                    beat_count += 1
                    beat_this_tick = True
                stream_t += float(hop_size) / float(sample_rate)

            # --- Profile chain / rotation ---
            if profile_chain is not None and effect_cycler is not None:
                _current_mood = mood_classifier.mood.value if mood_classifier else "chill"
                new_profile = profile_chain.update(stream_t, _current_mood)
                if new_profile is not None:
                    effect_cycler.set_profile(new_profile)
                    if debug_mood:
                        from dreamsync.profile_chain import _get_tag
                        _p = new_profile
                        _pri = _get_tag(_p, "primary:") or ""
                        _sec = _get_tag(_p, "secondary:") or ""
                        _tmp = next((t for t in ("warm", "cool", "neutral") if t in _p.tags), "")
                        _sat = next((t for t in ("muted", "medium", "vivid") if t in _p.tags), "")
                        _tag_info = f" [primary:{_pri} secondary:{_sec} {_tmp}/{_sat}]" if _pri else ""
                        _seed_str = f" (seed={profile_chain.seed}" if profile_chain.seed is not None else " ("
                        _idx = profile_chain.pool_index_of(_p)
                        _idx_str = f" index={_idx})" if _idx is not None else ")"
                        if profile_chain.is_blending:
                            print(f"[chain] blend -> {_p.name}{_tag_info}{_seed_str}{_idx_str}")
                        else:
                            print(f"[chain] profile: {_p.name}{_tag_info}{_seed_str}{_idx_str}")
            elif profile_rotation is not None and effect_cycler is not None:
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
                for _, renderer, *_ in multi_adapter.devices:
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
                runtime_params, active_live_eq_routes, active_live_instrument_routes = _runtime_params_for_frame(frame_intent)
                frame_intent = _apply_live_routes_to_intent(
                    frame_intent,
                    active_live_instrument_routes + active_live_eq_routes,
                )
                runtime_control_state = runtime_control_getter() if runtime_control_getter is not None else None
                frame_intent, runtime_params = apply_runtime_control_to_intent_params(
                    frame_intent,
                    runtime_params,
                    runtime_control_state,
                )
                if max_brightness:
                    frame_intent = dataclasses.replace(frame_intent, intensity=1.0)
                sent = multi_adapter.send_frame(
                    elapsed,
                    frame_intent,
                    beat=beat_this_tick,
                    params=runtime_params,
                )
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
                    log_row["pan_center"] = round(float(last_features["pan_center"]), 4)
                    log_row["pan_width"] = round(float(last_features["pan_width"]), 4)
                    log_row["stereo_preserved"] = bool(last_features["stereo_preserved"])
                if auto_cycle and mood_classifier is not None and effect_cycler is not None:
                    log_row["mood"] = mood_classifier.mood.value
                    log_row["effect"] = effect_cycler.current_effect
                if last_live_eq_state is not None:
                    log_row["dominant_band"] = last_live_eq_state.dominant_band
                    log_row["eq_events"] = list(last_live_eq_state.events)
                    log_row["active_eq_bands"] = [str(route.get("band", "")) for route in active_live_eq_routes]
                if last_live_instrument_state is not None:
                    log_row["dominant_proxy"] = last_live_instrument_state.dominant_proxy
                    log_row["instrument_events"] = list(last_live_instrument_state.events)
                    log_row["active_instruments"] = [str(route.get("instrument", "")) for route in active_live_instrument_routes]
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
                        "input_channels": int(channels),
                        "stereo_preserved": bool(last_features["stereo_preserved"]) if last_features else False,
                        "pan_center": round(float(last_features["pan_center"]), 4) if last_features else 0.0,
                        "pan_width": round(float(last_features["pan_width"]), 4) if last_features else 0.0,
                        "left_energy": round(float(last_features["left_energy"]), 6) if last_features else 0.0,
                        "right_energy": round(float(last_features["right_energy"]), 6) if last_features else 0.0,
                        "band_pan_centers": _band_dict(EQ_BAND_NAMES, tuple(last_features["band_pan_centers"])) if last_features else _band_dict(EQ_BAND_NAMES, _ZERO_EQ_BANDS),
                        "dominant_band": last_live_eq_state.dominant_band if last_live_eq_state else None,
                        "dominant_band_ratio": round(float(last_live_eq_state.dominant_ratio), 4) if last_live_eq_state else 0.0,
                        "eq_events": list(last_live_eq_state.events) if last_live_eq_state else [],
                        "active_eq_bands": [str(route.get("band", "")) for route in active_live_eq_routes],
                        "active_eq_routes": [dict(route) for route in active_live_eq_routes],
                        "band_ratios": _band_dict(EQ_BAND_NAMES, last_live_eq_state.band_ratios) if last_live_eq_state else _band_dict(EQ_BAND_NAMES, _ZERO_EQ_BANDS),
                        "band_fluxes": _band_dict(EQ_BAND_NAMES, last_live_eq_state.band_fluxes) if last_live_eq_state else _band_dict(EQ_BAND_NAMES, _ZERO_EQ_BANDS),
                        "dominant_proxy": last_live_instrument_state.dominant_proxy if last_live_instrument_state else "",
                        "instrument_events": list(last_live_instrument_state.events) if last_live_instrument_state else [],
                        "instrument_scores": _instrument_score_dict(last_live_instrument_state),
                        "instrument_pan_center": round(float(last_live_instrument_state.pan_center), 4) if last_live_instrument_state else 0.0,
                        "instrument_pan_width": round(float(last_live_instrument_state.pan_width), 4) if last_live_instrument_state else 0.0,
                        "active_instrument_routes": [dict(route) for route in active_live_instrument_routes],
                        "active_instruments": [str(route.get("instrument", "")) for route in active_live_instrument_routes],
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
                        "template_selectivity": bpm_estimator._beat_template.has_selectivity,
                        "autocorr_confidence": round(bpm_estimator._last_autocorr_confidence, 4),
                        "onset_activity": round(bpm_estimator._last_onset_activity, 4),
                    })
                if state_callback is not None:
                    state_callback({
                        "render_mode": str((runtime_params or {}).get("_render_mode", "")),
                        "current_palette": tuple(preset.color_palette) if preset is not None else (),
                        "dominant_band": last_live_eq_state.dominant_band if last_live_eq_state else "",
                        "dominant_proxy": last_live_instrument_state.dominant_proxy if last_live_instrument_state else "",
                        "pan_center": round(float(last_features["pan_center"]), 4) if last_features else 0.0,
                        "pan_width": round(float(last_features["pan_width"]), 4) if last_features else 0.0,
                        "active_eq_routes": [dict(route) for route in active_live_eq_routes],
                        "active_instrument_routes": [dict(route) for route in active_live_instrument_routes],
                        "active_scene_layers": [
                            dict(layer)
                            for layer in ((runtime_params or {}).get("scene_layers") or [])
                            if isinstance(layer, dict)
                        ],
                        "runtime_control": (
                            dict((runtime_params or {}).get("runtime_control", {}))
                            if isinstance((runtime_params or {}).get("runtime_control"), dict)
                            else {}
                        ),
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
        "stereo_preserved": bool(live_stereo_preserved),
        "stereo_chunks_captured": int(stereo_chunks_captured),
        "mono_fallback_chunks": int(mono_fallback_chunks),
    }
    return logs, summary
