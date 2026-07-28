"""Immutable contracts for chord-optional live structure analysis."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .models import PredictionEvidence


def _validate_probability(name: str, value: float) -> None:
    if not math.isfinite(float(value)) or not 0.0 <= float(value) <= 1.0:
        raise ValueError(f"{name} must be finite and between 0 and 1")


def _validate_vector(
    name: str,
    values: tuple[float, ...],
    *,
    length: int | None = None,
) -> None:
    if length is not None and len(values) != length:
        raise ValueError(f"{name} must contain {length} values")
    if not all(math.isfinite(float(value)) for value in values):
        raise ValueError(f"{name} must contain only finite values")


@dataclass(frozen=True)
class LiveBeatStructureObservation:
    """One causal beat-synchronous observation with optional tonal labels."""

    t: float
    beat_index: int
    bar_index: int | None
    beat_in_bar: int | None
    meter: tuple[int, int] | None
    meter_confidence: float
    downbeat: bool
    beat_period: float | None

    chroma: tuple[float, ...]
    chroma_confidence: float
    harmonic_novelty: float

    mfcc: tuple[float, ...]
    band_ratios: tuple[float, ...]
    band_fluxes: tuple[float, ...]
    bass_ratio: float
    energy: float
    onset_strength: float
    spectral_centroid: float

    chord_label: str | None = None
    chord_confidence: float = 0.0
    key_label: str | None = None
    key_confidence: float = 0.0

    def __post_init__(self) -> None:
        if not math.isfinite(float(self.t)):
            raise ValueError("t must be finite")
        if self.beat_index < 0:
            raise ValueError("beat_index must be non-negative")
        if self.bar_index is not None and self.bar_index < 0:
            raise ValueError("bar_index must be non-negative")
        if self.beat_in_bar is not None and self.beat_in_bar < 0:
            raise ValueError("beat_in_bar must be non-negative")
        if self.meter is not None and (
            len(self.meter) != 2 or self.meter[0] < 1 or self.meter[1] < 1
        ):
            raise ValueError("meter must contain positive numerator and denominator")
        for name in (
            "meter_confidence",
            "chroma_confidence",
            "chord_confidence",
            "key_confidence",
        ):
            _validate_probability(name, getattr(self, name))
        _validate_vector("chroma", self.chroma, length=12)
        _validate_vector("mfcc", self.mfcc, length=13)
        _validate_vector("band_ratios", self.band_ratios)
        _validate_vector("band_fluxes", self.band_fluxes)
        if len(self.band_ratios) != len(self.band_fluxes):
            raise ValueError("band_ratios and band_fluxes must have equal length")
        for name in (
            "harmonic_novelty",
            "bass_ratio",
            "energy",
            "onset_strength",
            "spectral_centroid",
        ):
            if not math.isfinite(float(getattr(self, name))):
                raise ValueError(f"{name} must be finite")
        if self.beat_period is not None and (
            not math.isfinite(float(self.beat_period)) or self.beat_period <= 0.0
        ):
            raise ValueError("beat_period must be positive and finite")

    def without_tonal_labels(self) -> "LiveBeatStructureObservation":
        """Return the exact same structure evidence with label sidecars cleared."""

        from dataclasses import replace

        return replace(
            self,
            chord_label=None,
            chord_confidence=0.0,
            key_label=None,
            key_confidence=0.0,
        )


@dataclass(frozen=True)
class LiveBarFingerprint:
    """One immutable multi-feature summary of a completed live bar."""

    bar_index: int
    start_t: float
    end_t: float
    beats: int
    meter: tuple[int, int] | None
    meter_confidence: float
    completeness: float

    beat_chroma: tuple[tuple[float, ...], ...]
    chroma_profile: tuple[float, ...]
    chroma_delta_shape: tuple[float, ...]
    chroma_confidence: float

    mfcc_mean: tuple[float, ...]
    mfcc_std: tuple[float, ...]
    band_profile: tuple[float, ...]
    band_flux_shape: tuple[float, ...]

    onset_shape: tuple[float, ...]
    energy_shape: tuple[float, ...]
    bass_shape: tuple[float, ...]
    centroid_shape: tuple[float, ...]

    tonal_label: str | None = None

    def __post_init__(self) -> None:
        if self.bar_index < 0:
            raise ValueError("bar_index must be non-negative")
        if self.beats < 1:
            raise ValueError("beats must be positive")
        if self.end_t < self.start_t:
            raise ValueError("end_t must not precede start_t")
        _validate_probability("meter_confidence", self.meter_confidence)
        _validate_probability("completeness", self.completeness)
        _validate_probability("chroma_confidence", self.chroma_confidence)
        _validate_vector("chroma_profile", self.chroma_profile, length=12)
        _validate_vector("mfcc_mean", self.mfcc_mean, length=13)
        _validate_vector("mfcc_std", self.mfcc_std, length=13)
        for index, chroma in enumerate(self.beat_chroma):
            _validate_vector(f"beat_chroma[{index}]", chroma, length=12)
        for name in (
            "chroma_delta_shape",
            "band_profile",
            "band_flux_shape",
            "onset_shape",
            "energy_shape",
            "bass_shape",
            "centroid_shape",
        ):
            _validate_vector(name, getattr(self, name))

    @property
    def reliability(self) -> float:
        timing = (0.55 * self.completeness) + (0.45 * self.meter_confidence)
        content = 0.75 + (0.25 * self.chroma_confidence)
        return max(0.0, min(1.0, timing * content))


@dataclass(frozen=True)
class BarSimilarity:
    left_bar: int
    right_bar: int
    combined: float
    harmonic_absolute: float
    harmonic_transposed: float
    best_pitch_shift: int
    timbre: float
    rhythm: float
    dynamics: float
    reliability: float
    pitch_shift_gap: float = 0.0
    active_weights: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True)
class SequenceMatch:
    horizon: int
    current_start_bar: int
    earlier_start_bar: int
    combined: float
    reliability: float
    pitch_shift: int
    substituted_bars: int = 0


@dataclass(frozen=True)
class BoundaryEvidence:
    target_bar: int
    observed_probability: float
    predicted_probability: float
    phrase_probability: float
    section_probability: float
    recurrence_probability: float
    novelty_probability: float
    homogeneity_probability: float
    regularity_probability: float
    trajectory_probability: float
    cold_start: bool
    evidence: tuple["PredictionEvidence", ...] = ()


@dataclass(frozen=True)
class CompactBarDescriptor:
    bar_index: int
    signature: tuple[float, ...]
    reliability: float


@dataclass(frozen=True)
class StructurePhraseFingerprint:
    bars: int
    bar_descriptors: tuple[CompactBarDescriptor, ...]
    combined_signature: tuple[float, ...]
    entrance_signature: tuple[float, ...]
    exit_signature: tuple[float, ...]
    harmonic_shift_tolerance: bool
    energy_shape: tuple[int, ...]
    onset_shape: tuple[int, ...]
    tonal_diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class LiveSectionHypothesis:
    section_id: str
    start_bar: int
    expected_end_bar: int | None
    duration_distribution: tuple[tuple[int, float], ...]
    prefix_match: float
    recurrence_count: int
    likely_successors: tuple[tuple[str, float], ...]
    probability: float
    semantic_role: str | None = None
    semantic_probability: float = 0.0


@dataclass(frozen=True)
class StructuralCueTarget:
    target_beat_index: int
    target_bar_index: int
    target_t: float
    requires_downbeat: bool
    timing_sigma: float

    def __post_init__(self) -> None:
        if self.target_beat_index < 0 or self.target_bar_index < 0:
            raise ValueError("structural cue indices must be non-negative")
        if not math.isfinite(self.target_t):
            raise ValueError("target_t must be finite")
        if not math.isfinite(self.timing_sigma) or self.timing_sigma < 0.0:
            raise ValueError("timing_sigma must be finite and non-negative")


StructuralActionOutcome = Literal["applied", "degraded", "rejected"]


@dataclass(frozen=True)
class StructuralActionResult:
    cue_id: str
    outcome: StructuralActionOutcome
    requested_effect: str | None
    applied_effect: str | None
    requested_palette_action: str | None
    applied_palette: str | None
    target_bar: int | None
    committed_beat: int | None
    reason: str
    preset: object | None = None
    cue_class: str = ""
    target_beat: int | None = None
    commit_t: float | None = None
    commit_bar: int | None = None
    downbeat: bool = False
    meter_confident: bool = False
    before_effect: str | None = None
    before_palette: str | None = None
    after_effect: str | None = None
    after_palette: str | None = None
