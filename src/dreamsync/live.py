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

from dreamsync.audio.ring import AudioBlockRing, PcmFrameBuffer
from dreamsync.audio.system_input import _require_sounddevice
from dreamsync.director import Director, DirectorConfig, EffectMode
from dreamsync.dsp.features import _estimate_bpm, _estimate_bpm_from_beats, _smooth_signal
from dreamsync.dsp.harmonic import (
    LiveBarChordHistory,
    LiveChordHistory,
    LiveHarmonicAnalyzer,
    LiveHarmonicState,
    chord_tones,
    detected_non_chord_tones,
)
from dreamsync.dsp.meter import (
    LiveMeterState,
    LiveMeterTracker,
    ManualBeatRegistration,
)
from dreamsync.dsp.structure import LiveStructureEvent, LiveStructureTracker
from dreamsync.prediction.runtime import (
    LivePredictiveRuntime,
    PredictiveRuntimeConfig,
)
from dreamsync.effects import EFFECTS, EffectCycler, EffectCyclerConfig
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

_SPATIAL_LAYER_ROUTE_KEYS: tuple[str, ...] = (
    "effect_layer",
    "layer_category",
    "trigger_mode",
    "falloff",
    "radius",
    "speed_units_per_second",
    "intensity_scale",
    "time_offset_s",
    "duration_s",
    "layer_priority",
)


@dataclass(frozen=True)
class LiveBeatAccent:
    """The metrical role of a detected live beat."""

    beat: bool
    downbeat: bool
    beat_in_bar: int | None
    strength: float


@dataclass(frozen=True)
class LiveStructureConfig:
    """Live-only harmonic and metrical structure settings."""

    harmonic_structure_enabled: bool = False
    beats_per_bar: int = 4
    bars_per_phrase: int = 4
    harmonic_frame_size: int = 4096
    harmonic_hop_multiplier: int = 1
    sensitivity: float = 0.5
    downbeat_min_confidence: float = 0.22
    debug_harmonics: bool = False
    predictive_analysis_enabled: bool = False
    predictive_diagnostics_enabled: bool = False
    predictive_shadow_mode: bool = True
    predictive_cues_enabled: bool = False
    predictive_high_impact_cues_enabled: bool = False
    predictive_cue_prepare_threshold: float = 0.54
    predictive_cue_schedule_threshold: float = 0.68
    predictive_cue_high_impact_threshold: float = 0.80
    predictive_maximum_anticipatory_intensity: float = 0.28
    predictive_cue_cooldown_seconds: float = 2.0
    predictive_allowed_cue_classes: tuple[str, ...] = (
        "chord_accent",
        "resolution_bloom",
        "phrase_reset",
        "section_recall",
        "chorus_lift",
    )
    structure_similarity_enabled: bool = False
    structure_similarity_diagnostics: bool = False
    structure_similarity_shadow_mode: bool = True
    structure_bar_actions_enabled: bool = False
    structure_phrase_actions_enabled: bool = False
    structure_section_actions_enabled: bool = False
    structure_allow_secondary_beat_modulation: bool = False
    structure_use_tonal_sidecar: bool = False
    structure_memory_bars: int = 256
    structure_min_meter_confidence: float = 0.22
    structure_phrase_threshold: float = 0.48
    structure_section_threshold: float = 0.62
    structure_large_action_threshold: float = 0.80

    def __post_init__(self) -> None:
        if (
            self.harmonic_structure_enabled
            and self.structure_similarity_enabled
        ):
            raise ValueError(
                "legacy harmonic structure and structure similarity are mutually exclusive"
            )
        if self.beats_per_bar < 2:
            raise ValueError("beats_per_bar must be at least 2")
        if self.bars_per_phrase < 1:
            raise ValueError("bars_per_phrase must be at least 1")
        if (
            self.harmonic_frame_size <= 0
            or self.harmonic_frame_size
            & (self.harmonic_frame_size - 1)
        ):
            raise ValueError("harmonic_frame_size must be a power of two")
        if self.harmonic_hop_multiplier < 1:
            raise ValueError("harmonic_hop_multiplier must be positive")
        if not 0.0 <= self.sensitivity <= 1.0:
            raise ValueError("sensitivity must be between 0 and 1")
        if not 0.0 < self.downbeat_min_confidence < 1.0:
            raise ValueError("downbeat_min_confidence must be between 0 and 1")
        if not (
            0.0
            <= self.predictive_cue_prepare_threshold
            <= self.predictive_cue_schedule_threshold
            <= self.predictive_cue_high_impact_threshold
            <= 1.0
        ):
            raise ValueError("predictive cue thresholds must be ordered in [0, 1]")
        if not 0.0 <= self.predictive_maximum_anticipatory_intensity <= 1.0:
            raise ValueError(
                "predictive maximum anticipatory intensity must be in [0, 1]"
            )
        if self.predictive_cue_cooldown_seconds < 0.0:
            raise ValueError("predictive cue cooldown must be non-negative")
        if not 16 <= self.structure_memory_bars <= 2048:
            raise ValueError("structure_memory_bars must be between 16 and 2048")
        if not 0.0 < self.structure_min_meter_confidence < 1.0:
            raise ValueError("structure_min_meter_confidence must be between 0 and 1")
        for name in (
            "structure_phrase_threshold",
            "structure_section_threshold",
            "structure_large_action_threshold",
        ):
            if not 0.0 <= float(getattr(self, name)) <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")


class LiveBeatSequencer:
    """Turn a beat stream into stable bar accents for live rendering.

    Live capture does not have an offline beat grid to identify absolute bar
    starts. The first detected beat after startup (or a song boundary) is
    therefore used as a stable bar anchor. It keeps light sequencing musical
    and predictable without changing beat detection itself.
    """

    def __init__(self, beats_per_bar: int = 4, secondary_strength: float = 0.35) -> None:
        if beats_per_bar < 2:
            raise ValueError("beats_per_bar must be at least 2")
        self.beats_per_bar = beats_per_bar
        self.secondary_strength = max(0.0, min(1.0, float(secondary_strength)))
        self._next_beat_in_bar = 0

    def reset(self) -> None:
        """Start the next detected beat as a new bar anchor."""
        self._next_beat_in_bar = 0

    def update(self, beat: bool) -> LiveBeatAccent:
        if not beat:
            return LiveBeatAccent(False, False, None, 0.0)

        beat_in_bar = self._next_beat_in_bar + 1
        downbeat = self._next_beat_in_bar == 0
        self._next_beat_in_bar = (self._next_beat_in_bar + 1) % self.beats_per_bar
        return LiveBeatAccent(
            beat=True,
            downbeat=downbeat,
            beat_in_bar=beat_in_bar,
            strength=1.0 if downbeat else self.secondary_strength,
        )


class LiveCycleTempoOverride:
    """Force the detector grid onto a half- or double-time subdivision."""

    _ALLOWED = (0.5, 1.0, 2.0)

    def __init__(self) -> None:
        self.multiplier = 1.0
        self._half_emit_next = True
        self._pending_double_t: float | None = None

    def set_multiplier(self, multiplier: float) -> None:
        value = float(multiplier)
        if value not in self._ALLOWED:
            raise ValueError("cycle tempo multiplier must be 0.5, 1, or 2")
        if value == self.multiplier:
            return
        self.multiplier = value
        self._half_emit_next = True
        self._pending_double_t = None

    def reset(self) -> None:
        self._half_emit_next = True
        self._pending_double_t = None

    def update(
        self,
        *,
        t: float,
        detected_bpm: float,
        detected_beat: bool,
    ) -> tuple[float, bool]:
        bpm = max(0.0, float(detected_bpm))
        if bpm <= 0.0:
            self._pending_double_t = None
            return 0.0, False
        if self.multiplier == 1.0:
            return bpm, bool(detected_beat)
        if self.multiplier == 0.5:
            if not detected_beat:
                return bpm * 0.5, False
            emit = self._half_emit_next
            self._half_emit_next = not self._half_emit_next
            return bpm * 0.5, emit

        period = 60.0 / bpm
        subdivision = False
        if (
            self._pending_double_t is not None
            and float(t) >= self._pending_double_t
        ):
            subdivision = True
            self._pending_double_t = None
        if detected_beat:
            self._pending_double_t = float(t) + (period * 0.5)
            return bpm * 2.0, True
        return bpm * 2.0, subdivision


def _meter_beat_accent(
    beat: bool,
    meter_state: LiveMeterState,
    *,
    secondary_strength: float = 0.35,
) -> LiveBeatAccent:
    """Translate a confidence-gated meter result into rendering semantics."""

    if not beat:
        return LiveBeatAccent(False, False, None, 0.0)
    beat_in_bar = (
        meter_state.bar_phase + 1
        if meter_state.meter_confident and meter_state.bar_phase is not None
        else None
    )
    return LiveBeatAccent(
        beat=True,
        downbeat=bool(meter_state.downbeat),
        beat_in_bar=beat_in_bar,
        strength=1.0 if meter_state.downbeat else secondary_strength,
    )


def _qualifies_harmonic_accent(state: LiveHarmonicState) -> bool:
    """Return whether a harmonic event is safe to expose to lighting."""

    return bool(
        state.harmonic_change
        and state.tonal_confidence >= 0.40
        and state.chord_confidence >= 0.30
    )


def _harmonic_accent_strength(
    now: float,
    started_at: float | None,
    *,
    duration: float = 0.24,
    maximum: float = 0.12,
) -> float:
    """Small linear-decay intensity boost for a local harmonic change."""

    if started_at is None or duration <= 0.0 or maximum <= 0.0:
        return 0.0
    age = max(0.0, float(now) - float(started_at))
    if age >= duration:
        return 0.0
    return max(0.0, min(float(maximum), maximum * (1.0 - (age / duration))))


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


class CyclicBeatGridTracker:
    """Fit a persistent beat grid to recurring onset *patterns*.

    A transient is not necessarily a downbeat: syncopated bass and kick
    parts often put their strongest onset consistently between beats.  This
    tracker retains a longer onset history and scores each candidate period
    by how well the complete pattern repeats after one or more cycles.  It
    then schedules a regular grid from the dominant phase instead of firing
    directly on each candidate onset.
    """

    def __init__(
        self,
        retention_seconds: float = 24.0,
        min_onsets: int = 8,
        min_pattern_seconds: float = 6.0,
    ) -> None:
        self.retention_seconds = max(8.0, retention_seconds)
        self.min_onsets = max(4, min_onsets)
        self.min_pattern_seconds = max(2.0, min_pattern_seconds)
        self._onsets: deque[tuple[float, float]] = deque()
        self._period = 0.0
        self._phase_anchor = 0.0
        self._next_beat_t: float | None = None
        self.confidence = 0.0
        self._last_candidate_t = -1e9

    @property
    def active(self) -> bool:
        return self._period > 0.0 and self.confidence >= 0.35

    @property
    def bpm(self) -> float:
        return 60.0 / self._period if self._period > 0.0 else 0.0

    def observe(self, t: float, strength: float = 1.0) -> None:
        """Record a thresholded onset candidate without declaring a beat."""
        if not math.isfinite(t):
            return
        strength = max(0.01, float(strength))
        # IOI onset detection already de-duplicates candidates.  Keep a
        # small guard here too so a broad transient cannot dominate a cycle.
        if t - self._last_candidate_t < 0.08:
            if self._onsets and strength > self._onsets[-1][1]:
                self._onsets[-1] = (t, strength)
            return
        self._onsets.append((t, strength))
        self._last_candidate_t = t
        self._trim(t)

    def update(self, candidate_bpms: tuple[float, ...], t: float) -> float:
        """Refit the grid and return its BPM, or ``0`` until it is reliable."""
        self._trim(t)
        if (
            len(self._onsets) < self.min_onsets
            or self._onsets[-1][0] - self._onsets[0][0] < self.min_pattern_seconds
        ):
            self.confidence *= 0.92
            return 0.0

        # Evaluate a small neighbourhood around each independent estimator.
        # The period matcher works on onset-to-onset recurrence, so it can
        # keep a meter even when the strongest onset is consistently offbeat.
        periods: set[float] = set()
        for bpm in candidate_bpms:
            if not math.isfinite(bpm) or bpm <= 0.0:
                continue
            base_period = 60.0 / bpm
            if not 0.25 <= base_period <= 1.5:
                continue
            for percent in range(-8, 9, 2):
                period = base_period * (1.0 + percent / 100.0)
                if 0.25 <= period <= 1.5:
                    periods.add(round(period, 5))

        if not periods:
            self.confidence *= 0.92
            return 0.0

        best: tuple[float, float, float] | None = None
        for period in periods:
            support, refined_period = self._score_period(period)
            if best is None or support > best[0]:
                best = (support, refined_period, period)

        if best is None:
            return 0.0
        support, refined_period, _ = best
        # Random transient pairs have approximately 10–15% accidental
        # alignment with this tolerance.  Require a substantially stronger
        # repeating pattern before allowing it to drive the metronome.
        if support < 0.35:
            self.confidence *= 0.85
            return 0.0

        self._period = refined_period
        self._phase_anchor = self._dominant_phase_anchor(refined_period)
        self.confidence = (
            support
            if self.confidence <= 1e-6
            else 0.65 * self.confidence + 0.35 * support
        )
        if self.confidence < 0.35:
            return 0.0

        proposed_next = self._phase_anchor + math.ceil(
            (t - self._phase_anchor) / self._period
        ) * self._period
        if proposed_next <= t + 1e-6:
            proposed_next += self._period
        # Re-anchoring only happens on the 0.5 s analysis cadence.  Do not
        # make a large phase jump from one short-lived onset cluster.
        if self._next_beat_t is None or abs(proposed_next - self._next_beat_t) > self._period * 0.35:
            self._next_beat_t = proposed_next
        return self.bpm

    def advance(self, t: float) -> bool:
        """Return True once for each scheduled grid beat that has elapsed."""
        if not self.active or self._next_beat_t is None:
            return False
        if t + 1e-6 < self._next_beat_t:
            return False
        while self._next_beat_t <= t + 1e-6:
            self._next_beat_t += self._period
        return True

    def phase_at(self, t: float) -> float:
        if not self.active:
            return 0.0
        return ((t - self._phase_anchor) / self._period) % 1.0

    def reset(self) -> None:
        self._onsets.clear()
        self._period = 0.0
        self._phase_anchor = 0.0
        self._next_beat_t = None
        self.confidence = 0.0
        self._last_candidate_t = -1e9

    def _trim(self, t: float) -> None:
        cutoff = t - self.retention_seconds
        while self._onsets and self._onsets[0][0] < cutoff:
            self._onsets.popleft()

    def _score_period(self, period: float) -> tuple[float, float]:
        """Score recurrence at ``period`` and return (support, refinement)."""
        weighted_fit = 0.0
        total_weight = 0.0
        refined_total = 0.0
        refined_weight = 0.0
        tolerance = max(0.018, period * 0.07)
        onsets = tuple(self._onsets)
        for index, (start_t, start_strength) in enumerate(onsets[:-1]):
            for end_t, end_strength in onsets[index + 1:]:
                delta = end_t - start_t
                cycles = max(1, round(delta / period))
                # Very distant pairs add little phase information and can
                # accidentally match a tempo change at the edge of retention.
                if cycles > 48:
                    continue
                residual = abs(delta - cycles * period)
                weight = min(start_strength, end_strength)
                total_weight += weight
                fit = math.exp(-0.5 * (residual / tolerance) ** 2)
                weighted_fit += weight * fit
                if fit >= 0.45:
                    refined_total += weight * fit * (delta / cycles)
                    refined_weight += weight * fit
        if total_weight <= 1e-9 or refined_weight <= 1e-9:
            return 0.0, period
        return weighted_fit / total_weight, refined_total / refined_weight

    def _dominant_phase_anchor(self, period: float) -> float:
        # Choose the densest recurring phase instead of assuming that phase
        # zero is a downbeat.  That makes an offbeat ostinato useful evidence
        # for the beat cycle without emitting a pulse for every raw transient.
        bins = 32
        histogram = [0.0] * bins
        phases: list[tuple[float, float]] = []
        for onset_t, strength in self._onsets:
            phase = (onset_t % period) / period
            phases.append((phase, strength))
            histogram[min(bins - 1, int(phase * bins))] += strength
        best_bin = max(range(bins), key=histogram.__getitem__)
        center = (best_bin + 0.5) / bins
        weighted_phase = 0.0
        total_weight = 0.0
        for phase, strength in phases:
            distance = ((phase - center + 0.5) % 1.0) - 0.5
            if abs(distance) <= 2.0 / bins:
                weighted_phase += (center + distance) * strength
                total_weight += strength
        phase = (weighted_phase / total_weight) % 1.0 if total_weight else center
        return phase * period


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
        self._eq_activity = 0.0
        self._wf_activity = 0.0
        self._hybrid_source = "bass"  # current active source in hybrid mode
        self.last_eq_onset = 0.0
        # Spectral template matching
        self._beat_template = SpectralBeatTemplate()
        # Onset candidates retain a longer window than the short-term onset
        # envelope.  The cyclic tracker treats their repeated pattern as
        # evidence for a metrical grid instead of treating every onset as a
        # beat/downbeat.
        self._ioi_histogram = IOIHistogram(buffer_seconds=24.0, bin_width_ms=5.0)
        self._cyclic_grid = CyclicBeatGridTracker(retention_seconds=24.0)
        self.last_cyclic_bpm = 0.0
        self.last_cyclic_confidence = 0.0
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
        self._eq_activity = 0.0
        self._wf_activity = 0.0
        self._hybrid_source = "bass"
        self.last_eq_onset = 0.0
        self._beat_template.reset()
        self._ioi_histogram.reset()
        self._cyclic_grid.reset()
        self.last_cyclic_bpm = 0.0
        self.last_cyclic_confidence = 0.0
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
        eq_band_fluxes: tuple[float, ...] = (),
        mag: np.ndarray | None = None,
    ) -> tuple[float, bool]:
        bass_onset = max(0.0, energy - self.prev_rms)
        self.prev_rms = energy
        eq_onset = self._eq_band_onset(eq_band_fluxes)
        self.last_eq_onset = eq_onset
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
                eq_onset=eq_onset,
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

        if self._ioi_histogram.feed(onset, t):
            # Candidate onsets can be syncopated.  They are intentionally
            # only evidence for the long-window cyclic model, not direct beat
            # triggers for the effects engine.
            self._cyclic_grid.observe(t, strength=onset)

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
                bpm_from_ioi = self._ioi_histogram.estimate_bpm()
                if bpm_from_beats > 0 and bpm_from_corr > 0:
                    bpm = 0.7 * bpm_from_beats + 0.3 * bpm_from_corr
                else:
                    bpm = bpm_from_beats if bpm_from_beats > 0 else bpm_from_corr
                cyclic_bpm = self._cyclic_grid.update(
                    (
                        bpm_from_beats,
                        bpm_from_corr,
                        bpm_from_ioi,
                        self.last_bpm,
                    ),
                    t,
                )
                self.last_cyclic_bpm = cyclic_bpm
                self.last_cyclic_confidence = self._cyclic_grid.confidence
                if cyclic_bpm > 0.0:
                    # The short window can still respond to real tempo
                    # changes.  A stable long-window cycle damps only enough
                    # of that estimate to prevent syncopated transients from
                    # pulling the metronome around.
                    cycle_weight = min(
                        0.55,
                        max(0.0, (self.last_cyclic_confidence - 0.35) / 0.65),
                    )
                    cyclic_bpm = self._normalize_bpm(cyclic_bpm)
                    bpm = (
                        (1.0 - cycle_weight) * bpm + cycle_weight * cyclic_bpm
                        if bpm > 0.0
                        else cyclic_bpm
                    )
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

        if self._cyclic_grid.active:
            beat = self._cyclic_grid.advance(t)
            self._beat_phase = self._cyclic_grid.phase_at(t)
        else:
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
        eq_onset: float = 0.0,
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
        self._eq_activity = alpha * eq_onset + (1.0 - alpha) * self._eq_activity
        self._wf_activity = alpha * whitened_flux + (1.0 - alpha) * self._wf_activity

        primary = self._bass_activity + self._kick_activity + self._eq_activity

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

        # The full EQ breakdown supplies an additional low-frequency onset
        # candidate.  It is especially useful when a kick straddles band
        # edges or a syncopated bass transient is clearer than the RMS rise.
        # It is still only a candidate; the cyclic grid decides when a pulse
        # belongs on the recurring beat cycle.
        if self._eq_activity > 0 and self._eq_activity > self._bass_activity * 1.25:
            self._hybrid_source = "eq_low"
            return eq_onset

        self._hybrid_source = "bass"
        return bass_onset

    @staticmethod
    def _eq_band_onset(band_fluxes: tuple[float, ...]) -> float:
        """Combine the EQ-band fluxes into a beat candidate.

        Kick and bass get the largest weights, while the upper bands make a
        small contribution for percussion whose attack is not entirely low
        frequency.  The result is deliberately fed through the adaptive onset
        threshold and cyclic grid instead of directly producing a beat.
        """
        values = tuple(max(0.0, float(value)) for value in band_fluxes)
        if not values:
            return 0.0
        padded = values + (0.0,) * max(0, 7 - len(values))
        sub, kick, bass, low_mid, mid, presence, air = padded[:7]
        return (
            0.16 * sub
            + 0.68 * kick
            + 0.34 * bass
            + 0.11 * low_mid
            + 0.06 * mid
            + 0.03 * presence
            + 0.01 * air
        )

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
    downbeat: bool = False,
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
        "downbeat": bool(downbeat),
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
            *_SPATIAL_LAYER_ROUTE_KEYS,
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
                *_SPATIAL_LAYER_ROUTE_KEYS,
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


def _live_render_interval(multi_adapter: MultiGoveeLanAdapter) -> float:
    """Return a conservative shared live-render interval."""

    fps_values: list[float] = []
    for adapter, *_rest in getattr(multi_adapter, "devices", ()):
        try:
            fps = float(getattr(getattr(adapter, "config", None), "fps", 0.0))
        except (TypeError, ValueError):
            continue
        if fps > 0.0:
            fps_values.append(fps)
    target_fps = min(fps_values) if fps_values else 30.0
    return 1.0 / max(1.0, min(60.0, target_fps))


def _advance_deadline(deadline: float, interval: float, now: float) -> float:
    """Advance a periodic deadline without scheduling catch-up bursts."""

    if interval <= 0.0:
        return now
    if deadline > now:
        return deadline
    missed = int((now - deadline) // interval)
    return deadline + ((missed + 1) * interval)


def _nearest_downbeat_target(
    request_t: float,
    last_beat_t: float,
    beat_period: float,
) -> str:
    """Choose the closest causal beat: the last detection or the next one."""

    period = max(1e-6, float(beat_period))
    previous_distance = abs(float(request_t) - float(last_beat_t))
    next_distance = abs(
        (float(last_beat_t) + period) - float(request_t)
    )
    return "previous" if previous_distance <= next_distance else "next"


def _p95(values: deque[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1))
    return float(ordered[index])


def resolve_live_render_mode(
    intent_mode: EffectMode,
    *,
    policy: str = "adaptive",
    configured_mode: str = "scroll",
    preset_mode: RenderMode | None = None,
) -> str:
    """Resolve the base renderer mode for a live frame.

    ``fixed`` makes an operator-selected renderer authoritative. ``adaptive``
    preserves the CLI's existing Director/profile-driven mode selection.
    Explicit runtime-control overrides are applied later and may replace either
    result.
    """
    normalized_policy = str(policy).strip().lower()
    if normalized_policy not in {"adaptive", "fixed"}:
        raise ValueError("render_mode_policy must be 'adaptive' or 'fixed'")
    if normalized_policy == "fixed":
        return RenderMode(str(configured_mode)).value
    if preset_mode is not None:
        return preset_mode.value
    if intent_mode == EffectMode.RIPPLE:
        return "ripple"
    if intent_mode == EffectMode.MOTION:
        return "wave"
    if intent_mode == EffectMode.PULSE:
        return "pulse"
    return "solid"


def _predictive_enabled_effects(
    effect_cycler: EffectCycler | None,
    *,
    configured_render_mode: str,
) -> tuple[str, ...]:
    """Return only effects the active reactive bank may select."""

    enabled = {str(configured_render_mode)}
    if effect_cycler is None:
        return tuple(sorted(enabled))
    profile = effect_cycler.profile
    if profile is None:
        enabled.update(EFFECTS)
    else:
        for mood_config in getattr(profile, "moods", {}).values():
            enabled.update(
                str(getattr(effect, "name", ""))
                for effect in getattr(mood_config, "effects", ())
                if str(getattr(effect, "name", ""))
            )
    if effect_cycler.current_effect:
        enabled.add(effect_cycler.current_effect)
    return tuple(sorted(enabled))


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
    master_brightness: float = 1.0,
    auto_cycle: bool = True,
    cycle_interval: float = 16.0,
    debug_mood: bool = False,
    stop_event: threading.Event | None = None,
    telemetry_dir: Path | None = None,
    crossfade_detect: bool = False,
    profile: Any | None = None,
    show_palette_cycle: tuple[str, ...] = (),
    effect_cycler_override: "EffectCycler | None" = None,
    profile_rotation: Any | None = None,
    profile_chain: Any | None = None,
    profile_switch_on_song_change: bool = False,
    runtime_control_getter: Any | None = None,
    predictive_cues_enabled_getter: Any | None = None,
    structure_action_controls_getter: Any | None = None,
    downbeat_nudge_revision_getter: Any | None = None,
    downbeat_nudge_request_getter: Any | None = None,
    cycle_tempo_multiplier_getter: Any | None = None,
    state_callback: Any | None = None,
    render_mode_policy: str = "adaptive",
    render_mode: str = "scroll",
    legacy_cyclic_downbeats: bool = False,
    structure_config: LiveStructureConfig | None = None,
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
    if not 0.05 <= float(master_brightness) <= 1.0:
        raise ValueError("master_brightness must be between 0.05 and 1.0")
    if frame_size <= 0:
        raise ValueError("frame_size must be > 0")
    if hop_size <= 0:
        raise ValueError("hop_size must be > 0")
    if blocksize <= 0:
        raise ValueError("blocksize must be > 0")
    live_structure = structure_config or LiveStructureConfig()
    structure_similarity_controls_output = bool(
        live_structure.structure_similarity_enabled
        and not live_structure.structure_similarity_shadow_mode
    )
    if live_structure.harmonic_frame_size < frame_size:
        raise ValueError(
            "harmonic_frame_size must be greater than or equal to frame_size"
        )
    normalized_render_mode_policy = str(render_mode_policy).strip().lower()
    if normalized_render_mode_policy not in {"adaptive", "fixed"}:
        raise ValueError("render_mode_policy must be 'adaptive' or 'fixed'")
    configured_render_mode = RenderMode(str(render_mode)).value

    from dreamsync.telemetry import SongTelemetryWriter

    sd = _require_sounddevice()
    harmonic_analysis_enabled = bool(
        live_structure.harmonic_structure_enabled
        or live_structure.debug_harmonics
        or live_structure.predictive_analysis_enabled
        or live_structure.structure_similarity_enabled
    )
    audio_ring = AudioBlockRing(
        capacity=8,
        blocksize=blocksize,
        channels=channels,
    )
    pcm_buffer = PcmFrameBuffer(
        capacity_samples=max(frame_size * 4, frame_size + (blocksize * 8)),
        channels=channels,
    )
    harmonic_hop_size = hop_size * live_structure.harmonic_hop_multiplier
    harmonic_pcm_buffer = (
        PcmFrameBuffer(
            capacity_samples=max(
                live_structure.harmonic_frame_size * 3,
                live_structure.harmonic_frame_size + (blocksize * 8),
            ),
            channels=channels,
        )
        if harmonic_analysis_enabled
        else None
    )
    logs: deque[dict[str, Any]] = deque(maxlen=2048)
    frame_log_rows = 0
    telemetry: SongTelemetryWriter | None = SongTelemetryWriter(telemetry_dir) if telemetry_dir else None
    bpm_estimator = LiveBpmEstimator(
        sample_rate=sample_rate, hop_size=hop_size, half_time=half_time,
    )
    cycle_tempo = LiveCycleTempoOverride()
    current_detected_bpm = 0.0
    current_cycle_bpm = 0.0
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
        effect_cycler.set_show_palette_cycle(show_palette_cycle)
    elif auto_cycle or show_palette_cycle:
        effect_cycler = EffectCycler(
            EffectCyclerConfig(cycle_interval=cycle_interval),
            profile=profile,
            show_palette_cycle=show_palette_cycle,
        )
    else:
        effect_cycler = None
    if effect_cycler is not None and effect_cycler.show_palette_colors:
        director.set_colors(effect_cycler.show_palette_colors)
    current_params: dict | None = None
    live_eq_tracker = LiveEqStateTracker()
    last_live_eq_state: LiveEqState | None = None
    live_instrument_tracker = LiveInstrumentStateTracker()
    last_live_instrument_state: LiveInstrumentState | None = None
    beat_sequencer = LiveBeatSequencer() if legacy_cyclic_downbeats else None
    meter_tracker = LiveMeterTracker(
        beats_per_bar=live_structure.beats_per_bar,
        min_confidence=live_structure.downbeat_min_confidence,
    )
    meter_state = LiveMeterState()
    harmonic_analyzer = (
        LiveHarmonicAnalyzer(
            sample_rate=sample_rate,
            frame_size=live_structure.harmonic_frame_size,
            sensitivity=live_structure.sensitivity,
            debug_spectrum=live_structure.debug_harmonics,
        )
        if harmonic_analysis_enabled
        else None
    )
    harmonic_state = LiveHarmonicState()
    chord_history = LiveChordHistory()
    bar_chord_history = LiveBarChordHistory()
    predictive_runtime = LivePredictiveRuntime(
        PredictiveRuntimeConfig(
            analysis_enabled=live_structure.predictive_analysis_enabled,
            diagnostics_enabled=(
                live_structure.predictive_diagnostics_enabled
                or live_structure.structure_similarity_diagnostics
            ),
            shadow_mode=live_structure.predictive_shadow_mode,
            cues_enabled=(
                live_structure.predictive_cues_enabled
                or live_structure.structure_bar_actions_enabled
                or live_structure.structure_phrase_actions_enabled
                or live_structure.structure_section_actions_enabled
            ),
            high_impact_cues_enabled=(
                live_structure.predictive_high_impact_cues_enabled
                or live_structure.structure_section_actions_enabled
            ),
            cue_prepare_threshold=(
                live_structure.predictive_cue_prepare_threshold
            ),
            cue_schedule_threshold=(
                live_structure.predictive_cue_schedule_threshold
            ),
            cue_high_impact_threshold=(
                live_structure.predictive_cue_high_impact_threshold
            ),
            maximum_anticipatory_intensity=(
                live_structure.predictive_maximum_anticipatory_intensity
            ),
            cue_cooldown_seconds=(
                live_structure.predictive_cue_cooldown_seconds
            ),
            beats_per_bar=live_structure.beats_per_bar,
            sample_rate=sample_rate,
            spectral_fft_size=frame_size,
            structure_similarity_enabled=(
                live_structure.structure_similarity_enabled
            ),
            structure_similarity_shadow_mode=(
                live_structure.structure_similarity_shadow_mode
            ),
            structure_memory_bars=live_structure.structure_memory_bars,
            structure_min_meter_confidence=(
                live_structure.structure_min_meter_confidence
            ),
            structure_bar_actions_enabled=(
                live_structure.structure_bar_actions_enabled
            ),
            structure_phrase_actions_enabled=(
                live_structure.structure_phrase_actions_enabled
            ),
            structure_section_actions_enabled=(
                live_structure.structure_section_actions_enabled
            ),
            structure_phrase_threshold=(
                live_structure.structure_phrase_threshold
            ),
            structure_section_threshold=(
                live_structure.structure_section_threshold
            ),
            allowed_cue_classes=(
                (
                    "bar_marker",
                    "phrase_reset",
                    "section_recall",
                    "section_transition",
                )
                if live_structure.structure_similarity_enabled
                else live_structure.predictive_allowed_cue_classes
            ),
        )
    )
    predictive_update_times_ms: deque[float] = deque(maxlen=2048)
    structure_tracker = (
        LiveStructureTracker(
            beats_per_bar=live_structure.beats_per_bar,
            bars_per_phrase=live_structure.bars_per_phrase,
            sensitivity=live_structure.sensitivity,
            minimum_phase_confidence=live_structure.downbeat_min_confidence,
        )
        if (
            live_structure.harmonic_structure_enabled
            and not live_structure.structure_similarity_enabled
        )
        else None
    )
    last_structure_event: LiveStructureEvent | None = None
    macro_change_count = 0
    beat_count = 0
    sent_count = 0
    dropped_blocks = 0
    captured_samples = 0
    stereo_chunks_captured = 0
    mono_fallback_chunks = 0
    live_stereo_preserved = False
    # The GUI needs a direct signal that the operating system is actually
    # delivering input, not merely that the reactive thread was started.
    last_input_callback_at = 0.0
    waveform_window_seconds = 10.0
    # The diagnostic is deliberately low-rate.  It must never contend with
    # the audio callback or the beat detector for CPU time.
    waveform_points_per_second = 30
    waveform_samples_per_point = max(1, int(sample_rate / waveform_points_per_second))
    waveform_points: deque[tuple[float, float]] = deque(
        maxlen=int(waveform_window_seconds * waveform_points_per_second)
    )
    waveform_accumulated_samples = 0
    waveform_accumulated_peak = 0.0
    waveform_emitted_samples = 0
    detected_beat_times: deque[float] = deque(
        maxlen=max(32, int(waveform_window_seconds * 8))
    )
    detected_downbeat_times: deque[float] = deque(
        maxlen=max(16, int(waveform_window_seconds * 4))
    )
    # The detector consumes all EQ-band fluxes, but the GUI only needs the two
    # beat-priority lanes.  Sending every band at analysis-frame rate was a
    # costly cross-thread copy with no practical diagnostic benefit.
    diagnostic_eq_bands = ("kick", "bass")
    eq_band_points: dict[str, deque[tuple[float, float]]] = {
        name: deque(maxlen=int(waveform_window_seconds * waveform_points_per_second))
        for name in diagnostic_eq_bands
    }
    last_eq_diagnostic_t = -1e9
    analysis_frame_times_ms: deque[float] = deque(maxlen=512)
    harmonic_frame_times_ms: deque[float] = deque(maxlen=256)
    analyzed_through_sample = 0
    last_audio_sequence: int | None = None
    analysis_discontinuities = 0

    # Seed with an initial intent so we always have something to render
    last_intent = director.update({"t": 0.0, "rms": 0.0, "zcr": 0.0, "bpm": 120.0, "beat": False, "bass": 0.0})

    def _runtime_params_for_frame(
        intent: Any,
    ) -> tuple[dict[str, Any] | None, list[dict[str, object]], list[dict[str, object]]]:
        runtime_params: dict[str, Any] = dict(current_params or {})
        resolved_render_mode = resolve_live_render_mode(
            intent.mode,
            policy=normalized_render_mode_policy,
            configured_mode=configured_render_mode,
            preset_mode=preset.render_mode if preset is not None else None,
        )
        if normalized_render_mode_policy == "fixed":
            runtime_params["_render_mode"] = resolved_render_mode
        else:
            runtime_params.setdefault("_render_mode", resolved_render_mode)

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
            for key in _SPATIAL_LAYER_ROUTE_KEYS:
                value = _first_route_value(all_active_routes, key)
                if value is not None and key not in runtime_params:
                    runtime_params[key] = value
            route_render_mode = _first_route_value(all_active_routes, "render_mode")
            if route_render_mode is not None and normalized_render_mode_policy == "adaptive":
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

    def _record_waveform_chunk(mono_chunk: np.ndarray) -> None:
        nonlocal waveform_accumulated_samples, waveform_accumulated_peak
        nonlocal waveform_emitted_samples
        offset = 0
        total = int(mono_chunk.shape[0])
        while offset < total:
            needed = waveform_samples_per_point - waveform_accumulated_samples
            take = min(needed, total - offset)
            segment = mono_chunk[offset : offset + take]
            if segment.size:
                waveform_accumulated_peak = max(
                    waveform_accumulated_peak,
                    float(np.max(np.abs(segment))),
                )
            waveform_accumulated_samples += take
            offset += take
            if waveform_accumulated_samples == waveform_samples_per_point:
                waveform_points.append(
                    (
                        (
                            waveform_emitted_samples
                            + (waveform_samples_per_point / 2)
                        )
                        / float(sample_rate),
                        waveform_accumulated_peak,
                    )
                )
                waveform_emitted_samples += waveform_samples_per_point
                waveform_accumulated_samples = 0
                waveform_accumulated_peak = 0.0

    def _callback(indata, frames, time_info, status) -> None:
        nonlocal dropped_blocks, captured_samples, last_input_callback_at
        if status and getattr(status, "input_overflow", False):
            dropped_blocks += 1
        frame_count = int(frames)
        capture_sample_index = captured_samples
        captured_samples += frame_count
        callback_at = time.monotonic()
        last_input_callback_at = callback_at
        adc_time = getattr(time_info, "inputBufferAdcTime", None)
        audio_ring.write_from_callback(
            indata,
            frames=frame_count,
            capture_sample_index=capture_sample_index,
            adc_time=float(adc_time) if adc_time is not None else None,
            callback_monotonic=callback_at,
        )

    stream_t = 0.0
    started_at = time.monotonic()
    next_telemetry = started_at + max(0.1, telemetry_interval_seconds)
    render_interval = _live_render_interval(multi_adapter)
    next_render_at = started_at
    # Publish diagnostics at display cadence so the chord visualizer does not
    # add another 100 ms after analysis has already completed.
    state_interval = 1.0 / 30.0
    next_state_at = started_at
    telemetry_frame_interval = 0.05
    next_frame_telemetry_at = started_at
    last_print = started_at
    pending_render_beat = False
    pending_render_downbeat = False
    pending_render_beat_strength = 0.0
    pending_render_beat_in_bar: int | None = None
    pending_state_beat = False
    pending_state_downbeat = False
    pending_state_beat_in_bar: int | None = None
    pending_render_harmonic_change = False
    pending_state_harmonic_change = False
    harmonic_accent_started_at: float | None = None
    pending_render_macro_candidate = False
    pending_render_macro_change = False
    pending_state_macro_candidate = False
    pending_state_macro_change = False
    pending_macro_transition = False
    pending_effect_macro_transition = False
    pending_structure_harmonic_state: LiveHarmonicState | None = None
    pending_predictive_chord_change = False
    pending_downbeat_nudge_revision = 0
    pending_downbeat_nudge_requested_at = 0.0
    pending_manual_beat_kind = "downbeat"
    pending_detection_reset = False
    applied_downbeat_nudge_revision = 0
    manual_downbeat_nudge_count = 0
    manual_beat_latch_count = 0
    manual_detection_reset_count = 0
    manual_downbeat_nudge_last_t: float | None = None
    manual_downbeat_nudge_previous_phase: int | None = None
    manual_downbeat_nudge_target = ""
    manual_meter_beats_per_bar = live_structure.beats_per_bar
    manual_beat_registration = ManualBeatRegistration(
        beats_per_bar=live_structure.beats_per_bar
    )
    last_detected_beat_stream_t: float | None = None
    last_detected_beat_period = 0.5
    last_runtime_params: dict[str, Any] | None = None
    last_active_live_eq_routes: list[dict[str, object]] = []
    last_active_live_instrument_routes: list[dict[str, object]] = []
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
    last_features: dict[str, float | bool] | None = None
    last_sf: SpectralFeatures | None = None
    last_wf = 0.0
    perc = 0.0
    last_pan = LivePanFrame()

    def _apply_manual_downbeat_nudge(
        *,
        revision: int,
        applied_t: float,
        target: str,
    ) -> None:
        nonlocal meter_state
        nonlocal last_structure_event
        nonlocal pending_render_macro_candidate
        nonlocal pending_render_macro_change
        nonlocal pending_state_macro_candidate
        nonlocal pending_state_macro_change
        nonlocal pending_macro_transition
        nonlocal pending_effect_macro_transition
        nonlocal pending_structure_harmonic_state
        nonlocal pending_predictive_chord_change
        nonlocal pending_render_beat
        nonlocal pending_render_downbeat
        nonlocal pending_render_beat_strength
        nonlocal pending_render_beat_in_bar
        nonlocal pending_state_beat
        nonlocal pending_state_downbeat
        nonlocal pending_state_beat_in_bar
        nonlocal applied_downbeat_nudge_revision
        nonlocal manual_downbeat_nudge_count
        nonlocal manual_downbeat_nudge_last_t
        nonlocal manual_downbeat_nudge_previous_phase
        nonlocal manual_downbeat_nudge_target

        manual_downbeat_nudge_previous_phase = meter_state.bar_phase
        meter_state = meter_tracker.nudge_downbeat(t=applied_t)
        if beat_sequencer is not None:
            beat_sequencer.reset()
        predictive_runtime.reset()
        bar_chord_history.reset()
        if structure_tracker is not None:
            structure_tracker.reset()
        last_structure_event = None
        pending_render_macro_candidate = False
        pending_render_macro_change = False
        pending_state_macro_candidate = False
        pending_state_macro_change = False
        pending_macro_transition = False
        pending_effect_macro_transition = False
        pending_structure_harmonic_state = None
        pending_predictive_chord_change = False
        pending_render_beat = False
        pending_render_downbeat = False
        pending_render_beat_strength = 0.0
        pending_render_beat_in_bar = None
        pending_state_beat = False
        pending_state_downbeat = False
        pending_state_beat_in_bar = None
        applied_downbeat_nudge_revision = revision
        manual_downbeat_nudge_count += 1
        manual_downbeat_nudge_last_t = applied_t
        manual_downbeat_nudge_target = target
        logs.append(
            {
                "kind": "manual_downbeat_nudge",
                "t": round(applied_t, 4),
                "revision": revision,
                "target": target,
                "previous_phase": manual_downbeat_nudge_previous_phase,
                "bar_phase": 0,
            }
        )

    def _set_manual_meter(beats_per_bar: int) -> None:
        nonlocal meter_state
        nonlocal beat_sequencer
        nonlocal structure_tracker
        nonlocal manual_meter_beats_per_bar

        value = int(beats_per_bar)
        meter_state = meter_tracker.set_beats_per_bar(value)
        predictive_runtime.set_beats_per_bar(value)
        if beat_sequencer is not None:
            beat_sequencer = LiveBeatSequencer(value)
        if structure_tracker is not None:
            structure_tracker = LiveStructureTracker(
                beats_per_bar=value,
                bars_per_phrase=live_structure.bars_per_phrase,
                sensitivity=live_structure.sensitivity,
                minimum_phase_confidence=(
                    live_structure.downbeat_min_confidence
                ),
            )
        manual_meter_beats_per_bar = value

    def _register_manual_beat(
        *,
        revision: int,
        applied_t: float,
        target: str,
        kind: str,
    ) -> None:
        nonlocal applied_downbeat_nudge_revision
        nonlocal manual_beat_latch_count
        nonlocal manual_downbeat_nudge_last_t
        nonlocal manual_downbeat_nudge_target

        beat_index = meter_tracker.beat_index
        normalized_kind = (
            "downbeat" if kind == "downbeat" else "beat"
        )
        inferred_meter = manual_beat_registration.register(
            t=applied_t,
            kind=normalized_kind,
            beat_index=beat_index,
        )
        manual_beat_latch_count += 1
        if normalized_kind == "downbeat":
            if inferred_meter is not None:
                _set_manual_meter(inferred_meter)
            _apply_manual_downbeat_nudge(
                revision=revision,
                applied_t=applied_t,
                target=target,
            )
        else:
            applied_downbeat_nudge_revision = revision
            manual_downbeat_nudge_last_t = applied_t
            manual_downbeat_nudge_target = target

        logs.append(
            {
                "kind": "manual_beat_latch",
                "t": round(applied_t, 4),
                "revision": revision,
                "target": target,
                "beat_kind": normalized_kind,
                "beat_index": beat_index,
                "inferred_beats_per_bar": inferred_meter,
            }
        )

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
            if predictive_cues_enabled_getter is not None:
                predictive_runtime.set_cues_enabled(
                    bool(predictive_cues_enabled_getter())
                )
            if cycle_tempo_multiplier_getter is not None:
                selected_cycle_multiplier = float(
                    cycle_tempo_multiplier_getter() or 1.0
                )
                if selected_cycle_multiplier != cycle_tempo.multiplier:
                    cycle_tempo.set_multiplier(
                        selected_cycle_multiplier
                    )
                    meter_tracker.reset()
                    predictive_runtime.reset()
                    bar_chord_history.reset()
                    if structure_tracker is not None:
                        structure_tracker.reset()
                    detected_beat_times.clear()
                    detected_downbeat_times.clear()
                    manual_beat_registration.reset(
                        beats_per_bar=manual_meter_beats_per_bar
                    )
                    manual_downbeat_nudge_count = 0
                    manual_beat_latch_count = 0
                    manual_downbeat_nudge_last_t = None
                    manual_downbeat_nudge_previous_phase = None
                    manual_downbeat_nudge_target = ""
                    last_detected_beat_stream_t = None
                    last_detected_beat_period = (
                        60.0 / current_cycle_bpm
                        if current_cycle_bpm > 0.0
                        else 0.5
                    )
            if downbeat_nudge_revision_getter is not None:
                request_revision = int(
                    downbeat_nudge_revision_getter() or 0
                )
                if request_revision > pending_downbeat_nudge_revision:
                    pending_downbeat_nudge_revision = request_revision
                    pending_downbeat_nudge_requested_at = now
                    pending_manual_beat_kind = "downbeat"
            if downbeat_nudge_request_getter is not None:
                request = downbeat_nudge_request_getter()
                request_revision = int(request[0] or 0)
                if request_revision > pending_downbeat_nudge_revision:
                    pending_downbeat_nudge_revision = request_revision
                    pending_downbeat_nudge_requested_at = float(
                        request[1] or now
                    )
                    pending_manual_beat_kind = (
                        str(request[2])
                        if len(request) > 2
                        else "downbeat"
                    )
                    if pending_manual_beat_kind == "reset":
                        pending_detection_reset = True
            if (
                pending_downbeat_nudge_revision
                > applied_downbeat_nudge_revision
                and last_detected_beat_stream_t is not None
                and pending_manual_beat_kind != "reset"
            ):
                request_stream_t = (
                    captured_samples / float(sample_rate)
                ) - max(0.0, now - pending_downbeat_nudge_requested_at)
                if _nearest_downbeat_target(
                    request_stream_t,
                    last_detected_beat_stream_t,
                    last_detected_beat_period,
                ) == "previous":
                    _register_manual_beat(
                        revision=pending_downbeat_nudge_revision,
                        applied_t=last_detected_beat_stream_t,
                        target="previous",
                        kind=pending_manual_beat_kind,
                    )
            if (
                structure_action_controls_getter is not None
                and live_structure.structure_similarity_enabled
            ):
                controls = structure_action_controls_getter()
                structure_similarity_controls_output = not bool(controls[0])
                predictive_runtime.set_structure_action_controls(
                    shadow_mode=bool(controls[0]),
                    bar_actions=bool(controls[1]),
                    phrase_actions=bool(controls[2]),
                    section_actions=bool(controls[3]),
                )
            if duration_seconds is not None and elapsed >= duration_seconds:
                break

            while True:
                audio_block = audio_ring.read()
                if audio_block is None:
                    break
                sequence_gap = (
                    last_audio_sequence is not None
                    and audio_block.sequence != last_audio_sequence + 1
                )
                last_audio_sequence = audio_block.sequence
                buffer_gap = pcm_buffer.append(
                    audio_block.data,
                    capture_sample_index=audio_block.capture_sample_index,
                )
                harmonic_buffer_gap = (
                    harmonic_pcm_buffer.append(
                        audio_block.data,
                        capture_sample_index=audio_block.capture_sample_index,
                    )
                    if harmonic_pcm_buffer is not None
                    else False
                )
                if sequence_gap or buffer_gap or harmonic_buffer_gap:
                    analysis_discontinuities += 1
                    prev_mag = None
                    spectral_mean = None
                    prev_whitened_mag = None
                    noise_estimator.reset()
                    percussive_tracker.reset()
                    if harmonic_analyzer is not None:
                        harmonic_analyzer.reset()
                        harmonic_state = LiveHarmonicState()
                        chord_history.reset()
                        bar_chord_history.reset()
                        predictive_runtime.reset()
                        harmonic_accent_started_at = None
                        pending_render_harmonic_change = False
                        pending_state_harmonic_change = False
                        if structure_tracker is not None:
                            structure_tracker.reset()
                        last_structure_event = None
                        pending_render_macro_candidate = False
                        pending_render_macro_change = False
                        pending_state_macro_candidate = False
                        pending_state_macro_change = False
                        pending_macro_transition = False
                        pending_effect_macro_transition = False
                        pending_structure_harmonic_state = None
                        pending_predictive_chord_change = False

                mono_chunk, stereo_chunk, stereo_preserved = _prepare_live_audio_chunk(
                    audio_block.data
                )
                _record_waveform_chunk(mono_chunk)
                if stereo_preserved and stereo_chunk is not None:
                    stereo_chunks_captured += 1
                    live_stereo_preserved = True
                else:
                    mono_fallback_chunks += 1

            # Process audio frames for beat detection + feature extraction
            beat_this_tick = False
            while pcm_buffer.available_samples >= frame_size:
                frame_started_at = time.perf_counter()
                raw_frame, frame_sample_index = pcm_buffer.read_frame(
                    frame_size=frame_size,
                    hop_size=hop_size,
                )
                frame, stereo_frame, _stereo_preserved = _prepare_live_audio_chunk(raw_frame)
                stream_t = float(frame_sample_index) / float(sample_rate)
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
                if stream_t - last_eq_diagnostic_t >= 1.0 / waveform_points_per_second:
                    for band_name in diagnostic_eq_bands:
                        points = eq_band_points[band_name]
                        points.append((
                            stream_t,
                            float(sf.band_fluxes[_EQ_BAND_INDEX[band_name]]),
                        ))
                        while points and points[0][0] < stream_t - waveform_window_seconds:
                            points.popleft()
                    last_eq_diagnostic_t = stream_t
                detected_bpm, detected_beat = bpm_estimator.update(
                    sf.bass, stream_t,
                    spectral_flux=sf.spectral_flux,
                    kick_spectral_flux=sf.kick_spectral_flux,
                    whitened_flux=wf,
                    percussive_onset=perc,
                    eq_band_fluxes=sf.band_fluxes,
                    mag=sf.mag,
                )
                if half_time and detected_bpm > 0:
                    detected_bpm *= 0.5
                current_detected_bpm = detected_bpm
                bpm, beat = cycle_tempo.update(
                    t=stream_t,
                    detected_bpm=detected_bpm,
                    detected_beat=detected_beat,
                )
                current_cycle_bpm = bpm

                # Crossfade boundary detection (parallel to silence)
                crossfade_boundary = False
                if crossfade_detector is not None:
                    crossfade_boundary = crossfade_detector.update(
                        bpm=detected_bpm,
                        centroid=sf.centroid,
                        bass_ratio=sf.bass_ratio,
                        energy=director.energy,
                        onset_strength=bpm_estimator.last_onset,
                        beat=detected_beat,
                        t=stream_t,
                    )

                boundary_type: str | None = None
                if pending_detection_reset:
                    boundary_type = "manual"
                elif silence_boundary:
                    boundary_type = "silence"
                elif crossfade_boundary:
                    boundary_type = "crossfade"

                if boundary_type is not None:
                    # Song boundary: reset all state
                    bpm_estimator.reset()
                    cycle_tempo.reset()
                    current_detected_bpm = 0.0
                    current_cycle_bpm = 0.0
                    director.reset()
                    if beat_sequencer is not None:
                        beat_sequencer.reset()
                    meter_tracker = LiveMeterTracker(
                        beats_per_bar=live_structure.beats_per_bar,
                        min_confidence=(
                            live_structure.downbeat_min_confidence
                        ),
                    )
                    predictive_runtime.set_beats_per_bar(
                        live_structure.beats_per_bar
                    )
                    meter_state = LiveMeterState()
                    if harmonic_analyzer is not None:
                        harmonic_analyzer.reset()
                        harmonic_state = LiveHarmonicState()
                        chord_history.reset()
                        bar_chord_history.reset()
                        harmonic_accent_started_at = None
                        pending_render_harmonic_change = False
                        pending_state_harmonic_change = False
                        if structure_tracker is not None:
                            structure_tracker = LiveStructureTracker(
                                beats_per_bar=live_structure.beats_per_bar,
                                bars_per_phrase=live_structure.bars_per_phrase,
                                sensitivity=live_structure.sensitivity,
                                minimum_phase_confidence=(
                                    live_structure.downbeat_min_confidence
                                ),
                            )
                        last_structure_event = None
                        pending_render_macro_candidate = False
                        pending_render_macro_change = False
                        pending_state_macro_candidate = False
                        pending_state_macro_change = False
                        pending_macro_transition = False
                        pending_effect_macro_transition = False
                        pending_structure_harmonic_state = None
                        pending_predictive_chord_change = False
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
                    detected_beat_times.clear()
                    detected_downbeat_times.clear()
                    manual_beat_registration.reset(
                        beats_per_bar=live_structure.beats_per_bar
                    )
                    manual_meter_beats_per_bar = (
                        live_structure.beats_per_bar
                    )
                    manual_downbeat_nudge_count = 0
                    manual_beat_latch_count = 0
                    manual_downbeat_nudge_last_t = None
                    manual_downbeat_nudge_previous_phase = None
                    manual_downbeat_nudge_target = ""
                    last_detected_beat_stream_t = None
                    last_detected_beat_period = 0.5
                    if boundary_type == "manual":
                        manual_detection_reset_count += 1
                        applied_downbeat_nudge_revision = (
                            pending_downbeat_nudge_revision
                        )
                        pending_detection_reset = False
                        pending_manual_beat_kind = "downbeat"
                        beat = False
                        bpm = 0.0
                    if (
                        boundary_type != "manual"
                        and profile_switch_on_song_change
                        and effect_cycler is not None
                    ):
                        if profile_chain is not None and hasattr(profile_chain, "force_switch"):
                            new_profile = profile_chain.force_switch(stream_t)
                        elif profile_rotation is not None and hasattr(profile_rotation, "force_switch"):
                            new_profile = profile_rotation.force_switch(stream_t)
                        else:
                            new_profile = None
                        if new_profile is not None:
                            effect_cycler.set_profile(new_profile)
                            if debug_mood:
                                print(
                                    f"[song change] switched to profile: "
                                    f"{getattr(new_profile, 'name', 'unnamed')}"
                                )
                    if (
                        boundary_type != "manual"
                        and effect_cycler is not None
                    ):
                        next_palette = effect_cycler.advance_show_palette()
                        if next_palette is not None:
                            director.set_colors(effect_cycler.show_palette_colors)
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

                if beat:
                    meter_state = meter_tracker.observe_beat(
                        t=stream_t,
                        bpm=bpm,
                        low_frequency=sf.kick_energy,
                        onset_strength=bpm_estimator.last_onset,
                        energy=rms,
                        harmonic_novelty=(
                            harmonic_state.novelty
                            if live_structure.harmonic_structure_enabled
                            else 0.0
                        ),
                        harmonic_confidence=(
                            harmonic_state.tonal_confidence
                            if live_structure.harmonic_structure_enabled
                            else 0.0
                        ),
                    )
                    if (
                        pending_downbeat_nudge_revision
                        > applied_downbeat_nudge_revision
                    ):
                        _register_manual_beat(
                            revision=pending_downbeat_nudge_revision,
                            applied_t=stream_t,
                            target="next",
                            kind=pending_manual_beat_kind,
                        )
                    last_detected_beat_stream_t = stream_t
                    last_detected_beat_period = (
                        60.0 / bpm
                        if bpm > 0.0
                        else last_detected_beat_period
                    )
                    if structure_tracker is not None:
                        structure_tracker.observe_beat(
                            meter_state,
                            energy=rms,
                            centroid=sf.centroid,
                            onset=bpm_estimator.last_onset,
                        )
                    committed_on_beat = chord_history.observe_beat(
                        meter_state,
                        bpm=bpm,
                    )
                    committed_since_last_beat = bool(
                        committed_on_beat
                        or pending_predictive_chord_change
                    )
                    bar_chord_history.observe_beat(
                        meter_state,
                        chord=chord_history.current,
                        chord_change=committed_since_last_beat,
                    )
                    if (
                        committed_on_beat
                        and live_structure.harmonic_structure_enabled
                        and chord_history.last_committed_state is not None
                        and _qualifies_harmonic_accent(
                            chord_history.last_committed_state
                        )
                    ):
                        harmonic_accent_started_at = now
                        pending_render_harmonic_change = True
                        pending_state_harmonic_change = True
                    if (
                        committed_on_beat
                        and chord_history.last_committed_state is not None
                        and chord_history.last_change is not None
                    ):
                        pending_structure_harmonic_state = (
                            dataclasses.replace(
                                chord_history.last_committed_state,
                                t=chord_history.last_change.t,
                                harmonic_change=True,
                            )
                        )
                beat_accent = (
                    beat_sequencer.update(beat)
                    if beat_sequencer is not None
                    else _meter_beat_accent(beat, meter_state)
                )
                last_features = _feature_row_from_frame(
                    frame, rms, stream_t, bpm, beat,
                    downbeat=beat_accent.downbeat,
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
                last_features["cycle_tempo_override"] = (
                    cycle_tempo.multiplier != 1.0
                )
                last_features["detected_bpm"] = detected_bpm
                last_live_eq_state = live_eq_tracker.update(sf, stream_t)
                last_live_instrument_state = live_instrument_tracker.update(
                    sf,
                    last_features,
                    percussive_onset=perc,
                    t=stream_t,
                )
                if (
                    live_structure.harmonic_structure_enabled
                    or structure_similarity_controls_output
                ):
                    last_features["structure_controlled"] = True
                    last_features["structure_event"] = (
                        "macro_change" if pending_macro_transition else ""
                    )
                last_intent = director.update(last_features)
                if pending_macro_transition:
                    pending_macro_transition = False

                if beat:
                    beat_count += 1
                    beat_this_tick = True
                    detected_beat_times.append(stream_t)
                    if beat_accent.downbeat:
                        detected_downbeat_times.append(stream_t)
                    pending_render_beat = True
                    pending_render_downbeat = (
                        pending_render_downbeat or beat_accent.downbeat
                    )
                    pending_render_beat_strength = max(
                        pending_render_beat_strength,
                        beat_accent.strength,
                    )
                    pending_render_beat_in_bar = beat_accent.beat_in_bar
                    pending_state_beat = True
                    pending_state_downbeat = (
                        pending_state_downbeat or beat_accent.downbeat
                    )
                    pending_state_beat_in_bar = beat_accent.beat_in_bar
                    if (
                        live_structure.predictive_analysis_enabled
                        or live_structure.structure_similarity_enabled
                    ):
                        predictive_started_at = time.perf_counter()
                        predictive_runtime.observe_committed(
                            t=stream_t,
                            meter_state=meter_state,
                            harmonic_state=(
                                chord_history.last_committed_state
                                or harmonic_state
                            ),
                            absolute_chord=chord_history.current,
                            chord_change=committed_since_last_beat,
                            energy=rms,
                            onset_density=bpm_estimator.last_onset,
                            spectral_centroid=sf.centroid,
                            bpm=bpm,
                            enabled_effects=_predictive_enabled_effects(
                                effect_cycler,
                                configured_render_mode=configured_render_mode,
                            ),
                            brightness_limit=master_brightness,
                            magnitude=sf.mag,
                            band_ratios=sf.band_ratios,
                            band_fluxes=sf.band_fluxes,
                            bass_ratio=sf.bass_ratio,
                            harmonic_novelty=harmonic_state.novelty,
                        )
                        predictive_update_times_ms.append(
                            (time.perf_counter() - predictive_started_at)
                            * 1000.0
                        )
                    pending_predictive_chord_change = False
                analyzed_through_sample = frame_sample_index + frame_size
                stream_t = float(frame_sample_index + hop_size) / float(sample_rate)
                analysis_frame_times_ms.append(
                    (time.perf_counter() - frame_started_at) * 1000.0
                )

            # Harmonic analysis has its own longer, lower-rate trailing window.
            # In Phase D this state is diagnostics-only: it does not alter the
            # director, beat accents, renderer, or output schedule.
            while (
                harmonic_pcm_buffer is not None
                and harmonic_analyzer is not None
                and harmonic_pcm_buffer.available_samples
                >= live_structure.harmonic_frame_size
            ):
                harmonic_started_at = time.perf_counter()
                harmonic_raw, harmonic_sample_index = (
                    harmonic_pcm_buffer.read_frame(
                        frame_size=live_structure.harmonic_frame_size,
                        hop_size=harmonic_hop_size,
                    )
                )
                harmonic_frame, _harmonic_stereo, _harmonic_preserved = (
                    _prepare_live_audio_chunk(harmonic_raw)
                )
                harmonic_t = (
                    harmonic_sample_index + live_structure.harmonic_frame_size
                ) / float(sample_rate)
                previous_harmonic_chord = harmonic_state.chord
                harmonic_state = harmonic_analyzer.update(
                    harmonic_frame,
                    t=harmonic_t,
                )
                if (
                    state_callback is not None
                    and harmonic_state.chord != previous_harmonic_chord
                ):
                    # Publish chord transitions (including silence) on this
                    # worker tick instead of waiting for the periodic deadline.
                    next_state_at = min(next_state_at, now)
                committed_harmonic_change = chord_history.observe(
                    harmonic_state,
                    meter=meter_state,
                    bpm=bpm_estimator.last_bpm,
                )
                pending_predictive_chord_change = bool(
                    pending_predictive_chord_change
                    or committed_harmonic_change
                )
                if (
                    live_structure.harmonic_structure_enabled
                    and committed_harmonic_change
                    and chord_history.last_committed_state is not None
                    and _qualifies_harmonic_accent(
                        chord_history.last_committed_state
                    )
                ):
                    harmonic_accent_started_at = now
                    pending_render_harmonic_change = True
                    pending_state_harmonic_change = True
                if structure_tracker is not None:
                    if pending_structure_harmonic_state is not None:
                        structure_harmonic_state = (
                            pending_structure_harmonic_state
                        )
                        pending_structure_harmonic_state = None
                    elif (
                        committed_harmonic_change
                        and chord_history.last_committed_state is not None
                        and chord_history.last_change is not None
                    ):
                        structure_harmonic_state = dataclasses.replace(
                            chord_history.last_committed_state,
                            t=chord_history.last_change.t,
                            harmonic_change=True,
                        )
                    else:
                        structure_harmonic_state = dataclasses.replace(
                            harmonic_state,
                            harmonic_change=False,
                        )
                    structure_events = structure_tracker.observe_harmonic(
                        structure_harmonic_state,
                        energy=director.energy,
                        centroid=last_sf.centroid if last_sf else 0.0,
                        onset=(
                            float(last_features.get("onset_strength", 0.0))
                            if last_features
                            else 0.0
                        ),
                    )
                    for structure_event in structure_events:
                        last_structure_event = structure_event
                        logs.append({
                            "kind": "structure_event",
                            "event": structure_event.kind,
                            "t": round(structure_event.t, 4),
                            "confidence": structure_event.confidence,
                            "harmonic_novelty": (
                                structure_event.harmonic_novelty
                            ),
                            "phase_confidence": (
                                structure_event.phase_confidence
                            ),
                            "bar_index": structure_event.bar_index,
                            "phrase_index": structure_event.phrase_index,
                            "chord_before": structure_event.chord_before,
                            "chord_after": structure_event.chord_after,
                        })
                        if structure_event.kind == "macro_candidate":
                            pending_render_macro_candidate = True
                            pending_state_macro_candidate = True
                        elif structure_event.kind == "macro_change":
                            macro_change_count += 1
                            chord_history.start_section(
                                t=structure_event.t
                            )
                            pending_render_macro_change = True
                            pending_state_macro_change = True
                            pending_macro_transition = True
                            pending_effect_macro_transition = True
                            if live_structure.predictive_analysis_enabled:
                                predictive_runtime.engine.complete_phrase(
                                    bars=live_structure.bars_per_phrase
                                )
                                predictive_runtime.engine.complete_section()
                harmonic_frame_times_ms.append(
                    (time.perf_counter() - harmonic_started_at) * 1000.0
                )
                if (
                    live_structure.debug_harmonics
                    and harmonic_state.harmonic_change
                ):
                    print(
                        "[harmonic shadow] "
                        f"t={harmonic_state.t:.3f} "
                        f"chord={harmonic_state.chord} "
                        f"novelty={harmonic_state.novelty:.3f}"
                    )

            # --- Profile chain / rotation ---
            if (
                profile_chain is not None
                and effect_cycler is not None
                and not structure_similarity_controls_output
            ):
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
            elif (
                profile_rotation is not None
                and effect_cycler is not None
                and not profile_switch_on_song_change
                and not structure_similarity_controls_output
            ):
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
                    current_cycle_bpm, director.energy,
                    structure_event=(
                        "macro_change"
                        if pending_effect_macro_transition
                        else None
                    ),
                    structure_controlled=(
                        live_structure.harmonic_structure_enabled
                        or structure_similarity_controls_output
                    ),
                )
                # Fixed GUI selections remain authoritative; adaptive CLI
                # sessions preserve profile-driven renderer swaps.
                if normalized_render_mode_policy == "adaptive":
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
            pending_effect_macro_transition = False

            # Render at the output cadence. Beat/downbeat events are latched
            # until a rendered frame consumes them.
            if last_intent is not None and now >= next_render_at:
                render_beat = pending_render_beat
                render_downbeat = pending_render_downbeat
                render_beat_strength = pending_render_beat_strength
                render_beat_in_bar = pending_render_beat_in_bar
                render_harmonic_change = pending_render_harmonic_change
                render_macro_candidate = pending_render_macro_candidate
                render_macro_change = pending_render_macro_change
                harmonic_accent = _harmonic_accent_strength(
                    now,
                    harmonic_accent_started_at,
                )
                frame_intent = last_intent
                structure_observation = predictive_runtime.structure_observation
                newly_committed_predictive_cues = (
                    predictive_runtime.commit_due_cues(
                        now_t=stream_t,
                        beat_index=(
                            structure_observation.beat_index
                            if structure_observation is not None
                            else None
                        ),
                        bar_index=(
                            structure_observation.bar_index
                            if structure_observation is not None
                            else None
                        ),
                        downbeat=render_downbeat,
                        meter_confident=meter_state.meter_confident,
                    )
                    if predictive_runtime.cue_policy.config.cues_enabled
                    else ()
                )
                committed_predictive_cues = (
                    predictive_runtime.active_committed_cues(
                        now_t=stream_t
                    )
                    if predictive_runtime.cue_policy.config.cues_enabled
                    else ()
                )
                predictive_cue_intensity = max(
                    (cue.intensity for cue in committed_predictive_cues),
                    default=0.0,
                )
                structural_action_results = []
                if (
                    newly_committed_predictive_cues
                    and live_structure.structure_similarity_enabled
                ):
                    enabled_structural_effects = _predictive_enabled_effects(
                        effect_cycler,
                        configured_render_mode=configured_render_mode,
                    )
                    for cue in newly_committed_predictive_cues:
                        result = predictive_runtime.structural_actuator.apply(
                            cue,
                            now_t=stream_t,
                            beat_index=(
                                structure_observation.beat_index
                                if structure_observation is not None
                                else None
                            ),
                            bar_index=(
                                structure_observation.bar_index
                                if structure_observation is not None
                                else None
                            ),
                            downbeat=render_downbeat,
                            meter_confident=meter_state.meter_confident,
                            effect_cycler=effect_cycler,
                            enabled_effects=enabled_structural_effects,
                        )
                        structural_action_results.append(result)
                        if result.outcome == "applied" and result.preset is not None:
                            preset = result.preset
                            if normalized_render_mode_policy == "adaptive":
                                for _, renderer, *_ in multi_adapter.devices:
                                    renderer.mode = preset.render_mode
                            director.set_colors(preset.color_palette)
                            current_params = preset.params
                if (
                    newly_committed_predictive_cues
                    and not live_structure.structure_similarity_enabled
                    and effect_cycler is not None
                    and effect_cycler.show_palette_queue
                    and any(
                        cue.color_action == "advance_approved_palette"
                        for cue in newly_committed_predictive_cues
                    )
                ):
                    next_palette = effect_cycler.advance_show_palette()
                    if next_palette is not None:
                        director.set_colors(
                            effect_cycler.show_palette_colors
                        )
                runtime_params, active_live_eq_routes, active_live_instrument_routes = _runtime_params_for_frame(frame_intent)
                if structural_action_results:
                    runtime_params = dict(runtime_params or {})
                    runtime_params["structural_actions"] = tuple(
                        {
                            "cue_id": result.cue_id,
                            "outcome": result.outcome,
                            "requested_effect": result.requested_effect,
                            "applied_effect": result.applied_effect,
                            "requested_palette_action": (
                                result.requested_palette_action
                            ),
                            "applied_palette": result.applied_palette,
                            "target_bar": result.target_bar,
                            "committed_beat": result.committed_beat,
                            "reason": result.reason,
                        }
                        for result in structural_action_results
                    )
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
                frame_intent = dataclasses.replace(
                    frame_intent,
                    intensity=_clamp01(
                        frame_intent.intensity
                        * (
                            1.0
                            + harmonic_accent
                            + predictive_cue_intensity
                        )
                        * float(master_brightness)
                    ),
                )
                if harmonic_accent > 0.0 or render_harmonic_change:
                    runtime_params = dict(runtime_params or {})
                    runtime_params["harmonic_accent"] = round(
                        harmonic_accent,
                        4,
                    )
                    runtime_params["harmonic_change"] = bool(
                        render_harmonic_change
                    )
                    if chord_history.last_change is not None:
                        runtime_params["detected_chord"] = (
                            chord_history.last_change.chord
                        )
                        runtime_params["chord_change_t"] = (
                            chord_history.last_change.t
                        )
                        runtime_params["chord_change_anchor"] = (
                            chord_history.last_change.anchor
                        )
                chord_prediction = chord_history.prediction
                if chord_prediction is not None:
                    runtime_params = dict(runtime_params or {})
                    runtime_params["predicted_chord"] = (
                        chord_prediction.chord
                    )
                    runtime_params["predicted_chord_t"] = (
                        chord_prediction.t
                    )
                    runtime_params["chord_prediction_confidence"] = (
                        chord_prediction.confidence
                    )
                prediction_mismatch = (
                    chord_history.last_prediction_mismatch
                )
                if (
                    prediction_mismatch is not None
                    and stream_t - prediction_mismatch.actual_t <= 1.5
                ):
                    runtime_params = dict(runtime_params or {})
                    runtime_params["chord_prediction_mismatch"] = (
                        dataclasses.asdict(prediction_mismatch)
                    )
                if render_macro_candidate or render_macro_change:
                    runtime_params = dict(runtime_params or {})
                    runtime_params["structure_event"] = (
                        "macro_change"
                        if render_macro_change
                        else "macro_candidate"
                    )
                    runtime_params["macro_candidate"] = bool(
                        render_macro_candidate
                    )
                    runtime_params["macro_change"] = bool(render_macro_change)
                if render_beat:
                    runtime_params = dict(runtime_params or {})
                    runtime_params["beat_accent"] = render_beat_strength
                if committed_predictive_cues:
                    runtime_params = dict(runtime_params or {})
                    runtime_params["predictive_cues"] = tuple(
                        {
                            "cue_id": cue.cue_id,
                            "cue_class": cue.cue_class,
                            "effect": (
                                cue.effect_candidates[0]
                                if cue.effect_candidates
                                else ""
                            ),
                            "intensity": cue.intensity,
                            "state": cue.state,
                        }
                        for cue in committed_predictive_cues
                    )
                sent = multi_adapter.send_frame(
                    elapsed,
                    frame_intent,
                    beat=render_beat,
                    params=runtime_params,
                )
                next_render_at = _advance_deadline(next_render_at, render_interval, now)
                pending_render_beat = False
                pending_render_downbeat = False
                pending_render_beat_strength = 0.0
                pending_render_beat_in_bar = None
                pending_render_harmonic_change = False
                pending_render_macro_candidate = False
                pending_render_macro_change = False
                last_runtime_params = runtime_params
                last_active_live_eq_routes = active_live_eq_routes
                last_active_live_instrument_routes = active_live_instrument_routes
                if sent:
                    sent_count += 1
                log_row: dict[str, Any] = {
                    "kind": "frame",
                    "t": round(stream_t, 4),
                    "elapsed_wall": round(elapsed, 4),
                    "bpm": round(float(bpm_estimator.last_bpm), 2),
                    "beat": bool(render_beat),
                    "downbeat": bool(render_downbeat),
                    "beat_in_bar": render_beat_in_bar,
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
                log_row["meter_downbeat"] = bool(meter_state.downbeat)
                log_row["meter_bar_phase"] = meter_state.bar_phase
                log_row["meter_confidence"] = meter_state.phase_confidence
                log_row["harmonic_enabled"] = bool(
                    live_structure.harmonic_structure_enabled
                )
                log_row["harmonic_chord"] = harmonic_state.chord
                log_row["harmonic_confidence"] = (
                    harmonic_state.tonal_confidence
                )
                log_row["harmonic_novelty"] = harmonic_state.novelty
                log_row["harmonic_change"] = bool(render_harmonic_change)
                log_row["harmonic_accent"] = round(harmonic_accent, 4)
                log_row["macro_candidate"] = bool(render_macro_candidate)
                log_row["macro_change"] = bool(render_macro_change)
                log_row["structure_confidence"] = (
                    last_structure_event.confidence
                    if last_structure_event is not None
                    else 0.0
                )
                log_row["bar_index"] = (
                    last_structure_event.bar_index
                    if last_structure_event is not None
                    else None
                )
                log_row["phrase_index"] = (
                    last_structure_event.phrase_index
                    if last_structure_event is not None
                    else None
                )
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
                frame_log_rows += 1

                if (
                    telemetry
                    and mood_classifier is not None
                    and now >= next_frame_telemetry_at
                ):
                    telemetry.write_frame({
                        "t": round(stream_t, 4),
                        "bpm": round(float(bpm_estimator.last_bpm), 2),
                        "beat": bool(render_beat),
                        "downbeat": bool(render_downbeat),
                        "beat_in_bar": render_beat_in_bar,
                        "meter_downbeat": bool(meter_state.downbeat),
                        "meter_bar_phase": meter_state.bar_phase,
                        "meter_relative_phase": meter_state.relative_phase,
                        "meter_confidence": meter_state.phase_confidence,
                        "meter_confident": meter_state.meter_confident,
                        "meter_evidence": meter_state.evidence,
                        "meter_inferred_missing_beats": (
                            meter_state.inferred_missing_beats
                        ),
                        "manual_downbeat_nudge_pending": bool(
                            pending_downbeat_nudge_revision
                            > applied_downbeat_nudge_revision
                        ),
                        "manual_downbeat_nudge_count": (
                            manual_downbeat_nudge_count
                        ),
                        "manual_downbeat_nudge_last_t": (
                            manual_downbeat_nudge_last_t
                        ),
                        "manual_downbeat_nudge_previous_phase": (
                            manual_downbeat_nudge_previous_phase
                        ),
                        "manual_downbeat_nudge_revision": (
                            applied_downbeat_nudge_revision
                        ),
                        "manual_downbeat_nudge_target": (
                            manual_downbeat_nudge_target
                        ),
                        "manual_beat_latch_count": manual_beat_latch_count,
                        "manual_detection_reset_count": (
                            manual_detection_reset_count
                        ),
                        "manual_beat_latch_pending_kind": (
                            pending_manual_beat_kind
                            if pending_downbeat_nudge_revision
                            > applied_downbeat_nudge_revision
                            else ""
                        ),
                        "manual_beat_markers": tuple(
                            dataclasses.asdict(marker)
                            for marker in manual_beat_registration.markers
                        ),
                        "manual_meter_beats_per_bar": (
                            manual_meter_beats_per_bar
                        ),
                        "meter_time_signature": (
                            manual_meter_beats_per_bar,
                            4,
                        ),
                        "harmonic_enabled": bool(
                            live_structure.harmonic_structure_enabled
                        ),
                        "harmonic_chord": harmonic_state.chord,
                        "harmonic_confidence": harmonic_state.tonal_confidence,
                        "harmonic_chord_confidence": (
                            harmonic_state.chord_confidence
                        ),
                        "harmonic_novelty": harmonic_state.novelty,
                        "harmonic_novelty_threshold": (
                            harmonic_state.novelty_threshold
                        ),
                        "harmonic_change": bool(render_harmonic_change),
                        "harmonic_accent": round(harmonic_accent, 4),
                        "macro_candidate": bool(render_macro_candidate),
                        "macro_change": bool(render_macro_change),
                        "structure_confidence": (
                            last_structure_event.confidence
                            if last_structure_event is not None
                            else 0.0
                        ),
                        "structure_bar_index": (
                            last_structure_event.bar_index
                            if last_structure_event is not None
                            else None
                        ),
                        "structure_phrase_index": (
                            last_structure_event.phrase_index
                            if last_structure_event is not None
                            else None
                        ),
                        "harmonic_chroma": harmonic_state.chroma,
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
                        "eq_beat_onset": round(float(bpm_estimator.last_eq_onset), 4),
                        "beat_phase": round(float(bpm_estimator._beat_phase), 4),
                        # Template matching
                        "template_similarity": round(bpm_estimator._beat_template._prev_similarity, 4),
                        "template_ready": bpm_estimator._beat_template.ready,
                        "template_selectivity": bpm_estimator._beat_template.has_selectivity,
                        "autocorr_confidence": round(bpm_estimator._last_autocorr_confidence, 4),
                        "onset_activity": round(bpm_estimator._last_onset_activity, 4),
                    })
                    next_frame_telemetry_at = _advance_deadline(
                        next_frame_telemetry_at,
                        telemetry_frame_interval,
                        now,
                    )

                if state_callback is not None and now >= next_state_at:
                    effective_bpm = current_cycle_bpm
                    while (
                        detected_beat_times
                        and detected_beat_times[0] < stream_t - waveform_window_seconds
                    ):
                        detected_beat_times.popleft()
                    while (
                        detected_downbeat_times
                        and detected_downbeat_times[0]
                        < stream_t - waveform_window_seconds
                    ):
                        detected_downbeat_times.popleft()
                    manual_beat_registration.prune_before(
                        stream_t - waveform_window_seconds
                    )
                    chord_history.prune_changes_before(
                        stream_t - waveform_window_seconds
                    )
                    active_profile = (
                        effect_cycler.profile if effect_cycler is not None else profile
                    )
                    active_palette_name = (
                        effect_cycler.current_palette if effect_cycler is not None else None
                    )
                    active_palette_colors = (
                        effect_cycler.show_palette_colors
                        if effect_cycler is not None and effect_cycler.current_show_palette
                        else tuple(preset.color_palette) if preset is not None else ()
                    )
                    palette_queue = (
                        effect_cycler.show_palette_queue
                        if effect_cycler is not None else ()
                    )
                    palette_cycle_mode = (
                        "song_detection" if palette_queue
                        else "timed" if auto_cycle and effect_cycler is not None
                        else "manual"
                    )
                    palette_seconds_until_next = (
                        effect_cycler.seconds_until_next_cycle(stream_t)
                        if palette_cycle_mode == "timed" and effect_cycler is not None
                        else None
                    )
                    live_state = {
                        "stream_t": round(float(stream_t), 6),
                        "listening": bool(
                            last_input_callback_at
                            and now - last_input_callback_at <= 1.0
                        ),
                        "last_input_callback_at": last_input_callback_at,
                        "captured_samples": int(captured_samples),
                        "input_overflows": int(dropped_blocks),
                        "bpm": round(effective_bpm, 2),
                        "detected_bpm": round(current_detected_bpm, 2),
                        "cycle_bpm": round(current_cycle_bpm, 2),
                        "cycle_tempo_multiplier": (
                            cycle_tempo.multiplier
                        ),
                        "beat": bool(pending_state_beat),
                        "downbeat": bool(pending_state_downbeat),
                        "beat_in_bar": pending_state_beat_in_bar,
                        "meter_downbeat": bool(meter_state.downbeat),
                        "meter_bar_phase": meter_state.bar_phase,
                        "meter_relative_phase": meter_state.relative_phase,
                        "meter_confidence": meter_state.phase_confidence,
                        "meter_confident": meter_state.meter_confident,
                        "meter_evidence": meter_state.evidence,
                        "meter_inferred_missing_beats": (
                            meter_state.inferred_missing_beats
                        ),
                        "manual_downbeat_nudge_pending": bool(
                            pending_downbeat_nudge_revision
                            > applied_downbeat_nudge_revision
                        ),
                        "manual_downbeat_nudge_count": (
                            manual_downbeat_nudge_count
                        ),
                        "manual_downbeat_nudge_last_t": (
                            manual_downbeat_nudge_last_t
                        ),
                        "manual_downbeat_nudge_previous_phase": (
                            manual_downbeat_nudge_previous_phase
                        ),
                        "manual_downbeat_nudge_revision": (
                            applied_downbeat_nudge_revision
                        ),
                        "manual_downbeat_nudge_target": (
                            manual_downbeat_nudge_target
                        ),
                        "manual_beat_latch_count": manual_beat_latch_count,
                        "manual_detection_reset_count": (
                            manual_detection_reset_count
                        ),
                        "manual_beat_latch_pending_kind": (
                            pending_manual_beat_kind
                            if pending_downbeat_nudge_revision
                            > applied_downbeat_nudge_revision
                            else ""
                        ),
                        "manual_beat_markers": tuple(
                            dataclasses.asdict(marker)
                            for marker in manual_beat_registration.markers
                        ),
                        "manual_meter_beats_per_bar": (
                            manual_meter_beats_per_bar
                        ),
                        "meter_time_signature": (
                            manual_meter_beats_per_bar,
                            4,
                        ),
                        "harmonic_enabled": bool(
                            live_structure.harmonic_structure_enabled
                        ),
                        "harmonic_window_ms": round(
                            (
                                live_structure.harmonic_frame_size
                                / float(sample_rate)
                            )
                            * 1000.0,
                            3,
                        ),
                        "harmonic_hop_ms": round(
                            (harmonic_hop_size / float(sample_rate)) * 1000.0,
                            3,
                        ),
                        "state_publish_interval_ms": round(
                            state_interval * 1000.0,
                            3,
                        ),
                        **predictive_runtime.diagnostics(now_t=stream_t),
                        "harmonic_chord": harmonic_state.chord,
                        "detected_chord": chord_history.current,
                        "detected_chord_history": chord_history.chords,
                        "detected_bar_chord_history": (
                            bar_chord_history.previous_three
                        ),
                        "detected_chord_changes": chord_history.changes,
                        "detected_chord_change": (
                            dataclasses.asdict(chord_history.last_change)
                            if chord_history.last_change is not None
                            else {}
                        ),
                        "detected_chord_prediction": (
                            dataclasses.asdict(chord_history.prediction)
                            if chord_history.prediction is not None
                            else {}
                        ),
                        "detected_chord_prediction_seconds": (
                            max(
                                0.0,
                                chord_history.prediction.t - stream_t,
                            )
                            if chord_history.prediction is not None
                            else None
                        ),
                        "detected_chord_prediction_mismatch": (
                            dataclasses.asdict(
                                chord_history.last_prediction_mismatch
                            )
                            if chord_history.last_prediction_mismatch
                            is not None
                            else {}
                        ),
                        "detected_chord_prediction_mismatch_active": bool(
                            chord_history.last_prediction_mismatch
                            is not None
                            and stream_t
                            - chord_history.last_prediction_mismatch.actual_t
                            <= 1.5
                        ),
                        "harmonic_debug_enabled": bool(
                            live_structure.debug_harmonics
                        ),
                        "harmonic_debug_spectrum": (
                            harmonic_analyzer.debug_spectrum
                            if harmonic_analyzer is not None
                            else ()
                        ),
                        "harmonic_debug_chroma": harmonic_state.chroma,
                        "harmonic_debug_chord": harmonic_state.chord,
                        "harmonic_debug_chord_tones": chord_tones(
                            harmonic_state.chord
                        ),
                        "harmonic_debug_root_note": (
                            chord_tones(harmonic_state.chord)[0]
                            if chord_tones(harmonic_state.chord)
                            else ""
                        ),
                        "harmonic_debug_non_chord_tones": (
                            detected_non_chord_tones(
                                harmonic_state.chroma,
                                harmonic_state.chord,
                            )
                        ),
                        "harmonic_confidence": harmonic_state.tonal_confidence,
                        "harmonic_chord_confidence": (
                            harmonic_state.chord_confidence
                        ),
                        "harmonic_novelty": harmonic_state.novelty,
                        "harmonic_novelty_threshold": (
                            harmonic_state.novelty_threshold
                        ),
                        "harmonic_change": bool(
                            pending_state_harmonic_change
                        ),
                        "harmonic_accent": round(
                            _harmonic_accent_strength(
                                now,
                                harmonic_accent_started_at,
                            ),
                            4,
                        ),
                        "macro_candidate": bool(
                            pending_state_macro_candidate
                        ),
                        "macro_change": bool(pending_state_macro_change),
                        "structure_event": (
                            last_structure_event.kind
                            if last_structure_event is not None
                            else ""
                        ),
                        "structure_confidence": (
                            last_structure_event.confidence
                            if last_structure_event is not None
                            else 0.0
                        ),
                        "structure_bar_index": (
                            last_structure_event.bar_index
                            if last_structure_event is not None
                            else None
                        ),
                        "structure_phrase_index": (
                            last_structure_event.phrase_index
                            if last_structure_event is not None
                            else None
                        ),
                        "beat_phase": round(float(bpm_estimator._beat_phase), 4),
                        "stability": round(float(director.stability), 4),
                        "cyclic_grid_bpm": round(float(bpm_estimator.last_cyclic_bpm), 2),
                        "cyclic_grid_confidence": round(
                            float(bpm_estimator.last_cyclic_confidence), 4
                        ),
                        "song_boundaries": int(
                            song_detector.boundary_count
                            + (crossfade_detector.boundary_count if crossfade_detector else 0)
                        ),
                        "render_mode": str((last_runtime_params or {}).get("_render_mode", "")),
                        "current_palette": active_palette_colors,
                        "active_palette_name": active_palette_name or "",
                        "palette_queue": palette_queue,
                        "palette_cycle_mode": palette_cycle_mode,
                        "palette_seconds_until_next": palette_seconds_until_next,
                        "active_profile_name": str(getattr(active_profile, "name", "")),
                        "profile_cycle_mode": (
                            "song_change" if profile_switch_on_song_change else ""
                        ),
                        "dominant_band": last_live_eq_state.dominant_band if last_live_eq_state else "",
                        "dominant_proxy": last_live_instrument_state.dominant_proxy if last_live_instrument_state else "",
                        "pan_center": round(float(last_features["pan_center"]), 4) if last_features else 0.0,
                        "pan_width": round(float(last_features["pan_width"]), 4) if last_features else 0.0,
                        "active_eq_routes": [dict(route) for route in last_active_live_eq_routes],
                        "active_instrument_routes": [dict(route) for route in last_active_live_instrument_routes],
                        "active_scene_layers": [
                            dict(layer)
                            for layer in ((last_runtime_params or {}).get("scene_layers") or [])
                            if isinstance(layer, dict)
                        ],
                        "runtime_control": (
                            dict((last_runtime_params or {}).get("runtime_control", {}))
                            if isinstance((last_runtime_params or {}).get("runtime_control"), dict)
                            else {}
                        ),
                    }
                    ring_stats = audio_ring.snapshot()
                    audio_lag_samples = max(
                        0,
                        int(captured_samples) - int(analyzed_through_sample),
                    )
                    live_state.update({
                        "audio_ring_capacity": ring_stats.capacity,
                        "audio_ring_depth": ring_stats.depth,
                        "audio_ring_fill": round(
                            ring_stats.depth / max(1, ring_stats.capacity),
                            4,
                        ),
                        "analysis_dropped_blocks": ring_stats.dropped_blocks,
                        "analysis_discontinuities": int(analysis_discontinuities),
                        "audio_lag_ms": round(
                            (audio_lag_samples / float(sample_rate)) * 1000.0,
                            3,
                        ),
                        "analysis_frame_ms_p95": round(_p95(analysis_frame_times_ms), 3),
                        "harmonic_frame_ms_p95": round(
                            _p95(harmonic_frame_times_ms),
                            3,
                        ),
                        "waveform_points": tuple(waveform_points),
                        "eq_band_points": {
                            name: tuple(points)
                            for name, points in eq_band_points.items()
                        },
                        "detected_beat_times": tuple(detected_beat_times),
                        "detected_downbeat_times": tuple(
                            detected_downbeat_times
                        ),
                        "waveform_window_seconds": waveform_window_seconds,
                    })
                    state_callback(live_state)
                    pending_state_beat = False
                    pending_state_downbeat = False
                    pending_state_beat_in_bar = None
                    pending_state_harmonic_change = False
                    pending_state_macro_candidate = False
                    pending_state_macro_change = False
                    next_state_at = _advance_deadline(next_state_at, state_interval, now)

            if now >= next_telemetry:
                ring_stats = audio_ring.snapshot()
                logs.append(
                    {
                        "kind": "telemetry",
                        "elapsed_wall": round(elapsed, 3),
                        "samples_captured": int(captured_samples),
                        "dropped_blocks": int(dropped_blocks),
                        "analysis_dropped_blocks": ring_stats.dropped_blocks,
                        "queue_chunks": ring_stats.depth,
                        "analysis_discontinuities": int(analysis_discontinuities),
                        "harmonic_enabled": bool(
                            live_structure.harmonic_structure_enabled
                        ),
                        "harmonic_frame_ms_p95": round(
                            _p95(harmonic_frame_times_ms),
                            3,
                        ),
                    }
                )
                next_telemetry = _advance_deadline(
                    next_telemetry,
                    max(0.1, telemetry_interval_seconds),
                    now,
                )
                if now - last_print >= 1.0:
                    beat_rate = beat_count / elapsed if elapsed > 1.0 else 0.0
                    eff_bpm = current_cycle_bpm
                    expected_rate = eff_bpm / 60.0
                    last_print = now
                    print(
                        f"govee t={elapsed:.1f}s "
                        f"beats={beat_count} ({beat_rate:.1f}/s, expect {expected_rate:.1f}/s) "
                        f"sent={sent_count} dropped={dropped_blocks} "
                        f"analysis_dropped={ring_stats.dropped_blocks} "
                        f"bpm={eff_bpm:.1f} "
                        f"(detected={current_detected_bpm:.1f}, "
                        f"cycle={cycle_tempo.multiplier:g}x) "
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
    ring_stats = audio_ring.snapshot()
    summary = {
        "duration_seconds": float(duration_seconds) if duration_seconds is not None else round(actual_duration, 3),
        "sample_rate": int(sample_rate),
        "channels": int(channels),
        "device": device,
        "frame_size": int(frame_size),
        "hop_size": int(hop_size),
        "harmonic_enabled": bool(live_structure.harmonic_structure_enabled),
        "harmonic_frame_size": int(live_structure.harmonic_frame_size),
        "harmonic_hop_size": int(harmonic_hop_size),
        "harmonic_window_ms": round(
            (
                live_structure.harmonic_frame_size
                / float(sample_rate)
            )
            * 1000.0,
            3,
        ),
        "harmonic_hop_ms": round(
            (harmonic_hop_size / float(sample_rate)) * 1000.0,
            3,
        ),
        "state_publish_interval_ms": round(
            state_interval * 1000.0,
            3,
        ),
        "harmonic_frame_ms_p95": round(_p95(harmonic_frame_times_ms), 3),
        "predictive_analysis_enabled": bool(
            live_structure.predictive_analysis_enabled
        ),
        "predictive_shadow_mode": bool(
            live_structure.predictive_shadow_mode
        ),
        "predictive_update_ms_p95": round(
            _p95(predictive_update_times_ms),
            3,
        ),
        "macro_changes": int(macro_change_count),
        "samples_captured": int(captured_samples),
        "dropped_blocks": int(dropped_blocks),
        "analysis_dropped_blocks": ring_stats.dropped_blocks,
        "analysis_discontinuities": int(analysis_discontinuities),
        "audio_ring_capacity": ring_stats.capacity,
        "max_returned_log_rows": int(logs.maxlen or 0),
        "rows": int(frame_log_rows),
        "returned_rows": len(logs),
        "sent": int(sent_count),
        "beats": int(beat_count),
        "manual_downbeat_nudges": int(manual_downbeat_nudge_count),
        "manual_beat_latches": int(manual_beat_latch_count),
        "manual_detection_resets": int(manual_detection_reset_count),
        "meter_time_signature": (manual_meter_beats_per_bar, 4),
        "detected_bpm": round(current_detected_bpm, 2),
        "cycle_bpm": round(current_cycle_bpm, 2),
        "cycle_tempo_multiplier": cycle_tempo.multiplier,
        "device_count": len(multi_adapter.devices),
        "ble_followers": ble_count,
        "song_boundaries": song_detector.boundary_count,
        "stereo_preserved": bool(live_stereo_preserved),
        "stereo_chunks_captured": int(stereo_chunks_captured),
        "mono_fallback_chunks": int(mono_fallback_chunks),
    }
    return list(logs), summary
