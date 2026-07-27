"""Immutable contracts shared by live prediction, replay, and cue policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .structure_models import StructuralCueTarget


EventType = Literal[
    "bar_marker",
    "chord_change",
    "harmonic_resolution",
    "phrase_boundary",
    "section_entrance",
    "section_repeat",
    "section_transition",
    "build_release",
    "chorus_entrance",
    "verse_repeat",
    "mood_change",
]
CueState = Literal[
    "proposed",
    "armed",
    "scheduled",
    "committed",
    "confirmed",
    "cancelled",
    "degraded",
]


@dataclass(frozen=True)
class LiveMusicalObservation:
    """One causal, beat-synchronous snapshot of the reactive analysis state."""

    t: float
    beat_index: int
    bar_index: int | None
    beat_in_bar: int | None
    meter: tuple[int, int] | None
    meter_confidence: float
    downbeat: bool
    absolute_chord: str | None
    chord_confidence: float
    chord_change: bool
    chord_duration_beats: float | None
    chroma: tuple[float, ...]
    tonal_confidence: float
    energy: float
    energy_delta: float
    onset_density: float
    onset_density_delta: float
    spectral_centroid: float
    centroid_delta: float
    harmonic_rhythm: float
    beat_period: float | None = None

    def __post_init__(self) -> None:
        if len(self.chroma) != 12:
            raise ValueError("chroma must contain 12 pitch-class values")
        if self.beat_index < 0:
            raise ValueError("beat_index must be non-negative")
        for name in (
            "meter_confidence",
            "chord_confidence",
            "tonal_confidence",
        ):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")


@dataclass(frozen=True)
class KeyHypothesis:
    tonic_pc: int
    mode: Literal["major", "minor"]
    probability: float
    stability: float
    age_beats: int
    chroma_evidence: float = 0.0
    chord_evidence: float = 0.0
    transition_evidence: float = 0.0


@dataclass(frozen=True)
class FunctionalChordHypothesis:
    key: KeyHypothesis
    degree: int
    numeral: str
    quality: str
    inversion: int | None
    borrowed: bool
    applied_target: int | None
    probability: float


@dataclass(frozen=True)
class PredictiveBarSummary:
    start_t: float
    end_t: float
    bar_index: int
    meter_confidence: float
    functional_chords: tuple[str, ...]
    chord_durations_beats: tuple[float, ...]
    key_hypotheses: tuple[KeyHypothesis, ...]
    harmonic_rhythm: float
    cadence_features: tuple[float, ...]
    energy: float
    energy_slope: float
    onset_density: float
    onset_slope: float
    texture: tuple[float, ...]


@dataclass(frozen=True)
class PhraseFingerprint:
    bars: int
    numerals: tuple[str, ...]
    chord_duration_pattern: tuple[int, ...]
    cadence_class: str
    harmonic_rhythm_pattern: tuple[int, ...]
    energy_shape: tuple[int, ...]
    onset_shape: tuple[int, ...]
    key_path: tuple[str, ...]


@dataclass(frozen=True)
class SectionFingerprint:
    section_id: str
    phrases: tuple[PhraseFingerprint, ...]
    duration_bars: int
    entrance_function: str | None
    exit_function: str | None
    energy_envelope: tuple[int, ...]
    onset_envelope: tuple[int, ...]
    recurrence_count: int = 1
    semantic_role: str | None = None
    semantic_probability: float = 0.0


@dataclass(frozen=True)
class PredictionAlternative:
    value: str
    probability: float
    target_t: float | None = None
    target_bar_index: int | None = None


@dataclass(frozen=True)
class PredictionEvidence:
    source: str
    contribution: float
    detail: str = ""


@dataclass(frozen=True)
class PredictedMusicalEvent:
    prediction_id: str
    created_t: float
    target_t: float
    timing_sigma: float
    event_type: EventType
    target_function: str | None
    target_section: str | None
    direction: str | None
    probability: float
    alternatives: tuple[PredictionAlternative, ...]
    evidence: tuple[PredictionEvidence, ...]
    model_version: str
    raw_probability: float | None = None
    target: StructuralCueTarget | None = None
    cold_start: bool = False
    matched_earlier_bar: int | None = None
    expected_duration_bars: int | None = None
    abstention_reason: str | None = None


@dataclass(frozen=True)
class PredictionOutcome:
    prediction_id: str
    observed_t: float
    matched: bool
    actual_value: str | None
    timing_error: float | None
    reason: str = ""


@dataclass(frozen=True)
class CueProposal:
    cue_id: str
    prediction_id: str
    created_t: float
    execute_t: float
    expires_t: float
    cue_class: str
    effect_candidates: tuple[str, ...]
    color_action: str | None
    intensity: float
    confidence: float
    state: CueState
    explanation: str
    target: StructuralCueTarget | None = None
    requested_effect: str | None = None
    applied_effect: str | None = None
    section_id: str | None = None
