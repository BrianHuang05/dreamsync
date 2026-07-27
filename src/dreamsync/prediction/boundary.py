"""Causal structure evidence, anonymous memory, and future bar predictions."""

from __future__ import annotations

import dataclasses
import math
from collections import deque
from dataclasses import dataclass

import numpy as np

from .models import (
    PredictedMusicalEvent,
    PredictionAlternative,
    PredictionEvidence,
)
from .recurrence import StructurePhraseRecurrenceMemory
from .section import StructureSectionMemory
from .similarity import MultiFeatureSimilarityMemory, compare_bars
from .structure_models import (
    BarSimilarity,
    BoundaryEvidence,
    LiveBarFingerprint,
    LiveSectionHypothesis,
    SequenceMatch,
    StructuralCueTarget,
)


@dataclass(frozen=True)
class StructurePhraseLengthHypothesis:
    bars: int | None
    probability: float
    phrase_start_bar: int
    current_position: int
    source: str


@dataclass(frozen=True)
class OnlineStructureSnapshot:
    latest_bar: int | None = None
    observed: BoundaryEvidence | None = None
    upcoming: tuple[BoundaryEvidence, ...] = ()
    phrase_hypotheses: tuple[StructurePhraseLengthHypothesis, ...] = ()
    section_hypotheses: tuple[LiveSectionHypothesis, ...] = ()
    current_section_id: str | None = None
    observed_phrase_boundary: bool = False
    observed_section_boundary: bool = False
    matched_earlier_bar: int | None = None


class StructureEvidenceFusion:
    """Fuse causal novelty, recurrence, homogeneity, regularity, and trajectory."""

    def __init__(self, *, recent_bars: int = 8) -> None:
        self._recent_similarity: deque[float] = deque(
            maxlen=max(2, int(recent_bars))
        )
        self._previous: LiveBarFingerprint | None = None

    def reset(self) -> None:
        self._recent_similarity.clear()
        self._previous = None

    def observe(
        self,
        fingerprint: LiveBarFingerprint,
        *,
        similarities: tuple[BarSimilarity, ...],
        sequence_matches: tuple[SequenceMatch, ...],
        elapsed_phrase_bars: int,
    ) -> BoundaryEvidence:
        adjacent = (
            compare_bars(fingerprint, self._previous)
            if self._previous is not None
            else None
        )
        if adjacent is None:
            harmonic_novelty = 0.0
            timbre_novelty = 0.0
            rhythm_novelty = 0.0
            dynamics_novelty = 0.0
        else:
            harmonic_novelty = 1.0 - adjacent.harmonic_absolute
            timbre_novelty = 1.0 - adjacent.timbre
            rhythm_novelty = 1.0 - adjacent.rhythm
            dynamics_novelty = 1.0 - adjacent.dynamics
        prior_homogeneity = (
            float(np.mean(self._recent_similarity))
            if self._recent_similarity
            else 0.5
        )
        if adjacent is not None:
            self._recent_similarity.append(adjacent.combined)
        recurrence = max(
            (
                match.combined * match.reliability
                for match in sequence_matches
            ),
            default=max(
                (
                    match.combined * match.reliability
                    for match in similarities[:3]
                ),
                default=0.0,
            ),
        )
        regularity = _regularity(elapsed_phrase_bars)
        trajectory = _trajectory_probability(fingerprint)
        non_harmonic = max(
            timbre_novelty,
            rhythm_novelty,
            dynamics_novelty,
        )
        mean_novelty = float(
            np.mean(
                (
                    harmonic_novelty,
                    timbre_novelty,
                    rhythm_novelty,
                    dynamics_novelty,
                )
            )
        )
        novelty = _clamp(
            (0.36 * non_harmonic)
            + (0.22 * harmonic_novelty)
            + (0.22 * mean_novelty)
            + (0.12 * trajectory)
            + (0.08 * prior_homogeneity)
        )
        section_probability = _clamp(
            0.11
            + (0.52 * novelty)
            + (0.16 * recurrence)
            + (0.14 * prior_homogeneity * novelty)
            + (0.10 * trajectory)
        )
        phrase_probability = _clamp(
            0.06
            + (0.30 * regularity)
            + (0.30 * novelty)
            + (0.22 * recurrence)
            + (0.12 * trajectory)
        )
        cold_start = recurrence < 0.55
        evidence = (
            PredictionEvidence(
                "harmonic_novelty",
                harmonic_novelty,
            ),
            PredictionEvidence("timbre_novelty", timbre_novelty),
            PredictionEvidence("rhythm_novelty", rhythm_novelty),
            PredictionEvidence("dynamics_novelty", dynamics_novelty),
            PredictionEvidence("song_local_recurrence", recurrence),
            PredictionEvidence("rolling_homogeneity", prior_homogeneity),
            PredictionEvidence("duration_regularity", regularity),
            PredictionEvidence("trajectory", trajectory),
        )
        self._previous = fingerprint
        return BoundaryEvidence(
            target_bar=fingerprint.bar_index,
            observed_probability=max(phrase_probability, section_probability),
            predicted_probability=0.0,
            phrase_probability=phrase_probability,
            section_probability=section_probability,
            recurrence_probability=recurrence,
            novelty_probability=novelty,
            homogeneity_probability=prior_homogeneity,
            regularity_probability=regularity,
            trajectory_probability=trajectory,
            cold_start=cold_start,
            evidence=evidence,
        )


class OnlineStructureTracker:
    """Learn phrase boundaries and anonymous sections from completed bars."""

    def __init__(
        self,
        *,
        phrase_lengths: tuple[int, ...] = (2, 4, 8, 12, 16),
        phrase_threshold: float = 0.57,
        section_threshold: float = 0.60,
        max_recent_bars: int = 32,
    ) -> None:
        self.phrase_lengths = tuple(sorted(set(phrase_lengths)))
        self.phrase_threshold = float(phrase_threshold)
        self.section_threshold = float(section_threshold)
        self.max_recent_bars = max(16, int(max_recent_bars))
        self.fusion = StructureEvidenceFusion()
        self.phrases = StructurePhraseRecurrenceMemory()
        self.sections = StructureSectionMemory()
        self.reset()

    def reset(self) -> None:
        self.fusion.reset()
        self.phrases.reset()
        self.sections.reset()
        self._bars: deque[LiveBarFingerprint] = deque(maxlen=self.max_recent_bars)
        self._phrase_start: int | None = None
        self._section_start: int | None = None
        self._phrase_bars: list[LiveBarFingerprint] = []
        self._section_bars: list[LiveBarFingerprint] = []
        self._current_section_id: str | None = None
        self._snapshot = OnlineStructureSnapshot()

    @property
    def snapshot(self) -> OnlineStructureSnapshot:
        return self._snapshot

    def observe(
        self,
        fingerprint: LiveBarFingerprint,
        *,
        similarities: tuple[BarSimilarity, ...],
        sequence_matches: tuple[SequenceMatch, ...],
    ) -> OnlineStructureSnapshot:
        if self._phrase_start is None:
            self._phrase_start = fingerprint.bar_index
        if self._section_start is None:
            self._section_start = fingerprint.bar_index
        elapsed_phrase = fingerprint.bar_index - self._phrase_start + 1
        observed = self.fusion.observe(
            fingerprint,
            similarities=similarities,
            sequence_matches=sequence_matches,
            elapsed_phrase_bars=elapsed_phrase,
        )
        phrase_boundary = (
            elapsed_phrase >= 2
            and observed.phrase_probability >= self.phrase_threshold
            and (
                observed.novelty_probability >= 0.38
                or observed.recurrence_probability >= 0.68
            )
        )
        section_boundary = (
            len(self._section_bars) >= 2
            and observed.section_probability >= self.section_threshold
            and observed.novelty_probability >= 0.38
        )
        if phrase_boundary and self._phrase_bars:
            self.phrases.remember(tuple(self._phrase_bars))
            self._phrase_bars = []
            self._phrase_start = fingerprint.bar_index
        if section_boundary and self._section_bars:
            self._current_section_id = self.sections.observe_completed(
                tuple(self._section_bars)
            )
            self._section_bars = []
            self._section_start = fingerprint.bar_index

        self._bars.append(fingerprint)
        self._phrase_bars.append(fingerprint)
        self._section_bars.append(fingerprint)
        phrase_matches = self.phrases.match_prefix(tuple(self._phrase_bars))
        section_hypotheses = self.sections.match_prefix(
            tuple(self._section_bars),
            start_bar=int(self._section_start),
        )
        phrase_hypotheses = self._phrase_hypotheses(
            fingerprint.bar_index,
            phrase_matches[0].score if phrase_matches else 0.0,
            phrase_matches[0].fingerprint.bars if phrase_matches else None,
        )
        upcoming = self._upcoming(
            fingerprint.bar_index,
            phrase_hypotheses,
            section_hypotheses,
            observed,
        )
        matched_earlier = (
            similarities[0].right_bar if similarities else None
        )
        self._snapshot = OnlineStructureSnapshot(
            latest_bar=fingerprint.bar_index,
            observed=observed,
            upcoming=upcoming,
            phrase_hypotheses=phrase_hypotheses,
            section_hypotheses=section_hypotheses,
            current_section_id=(
                section_hypotheses[0].section_id
                if section_hypotheses
                else self._current_section_id
            ),
            observed_phrase_boundary=phrase_boundary,
            observed_section_boundary=section_boundary,
            matched_earlier_bar=matched_earlier,
        )
        return self._snapshot

    def _phrase_hypotheses(
        self,
        current_bar: int,
        recurrence_score: float,
        recurrence_length: int | None,
    ) -> tuple[StructurePhraseLengthHypothesis, ...]:
        elapsed = current_bar - int(self._phrase_start or current_bar) + 1
        weights: dict[int | None, float] = {
            2: 0.10,
            4: 0.34,
            8: 0.26,
            12: 0.10,
            16: 0.10,
            None: 0.10,
        }
        if recurrence_length in weights:
            weights[recurrence_length] += 0.65 * recurrence_score
        for length in self.phrase_lengths:
            if elapsed > length:
                weights[length] *= max(0.12, math.exp(-0.30 * (elapsed - length)))
        total = sum(weights.values()) or 1.0
        return tuple(
            StructurePhraseLengthHypothesis(
                bars=length,
                probability=value / total,
                phrase_start_bar=int(self._phrase_start or current_bar),
                current_position=elapsed,
                source=(
                    "song_local_recurrence"
                    if length == recurrence_length and recurrence_score >= 0.55
                    else "duration_prior"
                ),
            )
            for length, value in sorted(
                weights.items(),
                key=lambda item: (-item[1], item[0] is None, item[0] or 0),
            )
        )

    def _upcoming(
        self,
        current_bar: int,
        phrase_hypotheses: tuple[StructurePhraseLengthHypothesis, ...],
        section_hypotheses: tuple[LiveSectionHypothesis, ...],
        observed: BoundaryEvidence,
    ) -> tuple[BoundaryEvidence, ...]:
        by_target: dict[int, BoundaryEvidence] = {}
        phrase = next(
            (
                item
                for item in phrase_hypotheses
                if item.bars is not None
                and item.phrase_start_bar + item.bars > current_bar + 1
            ),
            None,
        )
        if phrase is not None:
            target = phrase.phrase_start_bar + int(phrase.bars)
            song_local = phrase.source == "song_local_recurrence"
            probability = _clamp(
                phrase.probability
                + (0.30 * observed.recurrence_probability if song_local else 0.0)
                + (0.08 * observed.trajectory_probability)
            )
            by_target[target] = dataclasses.replace(
                observed,
                target_bar=target,
                observed_probability=0.0,
                predicted_probability=probability,
                phrase_probability=probability,
                section_probability=0.0,
                cold_start=not song_local,
            )
        if section_hypotheses:
            section = section_hypotheses[0]
            if (
                section.expected_end_bar is not None
                and section.expected_end_bar > current_bar + 1
            ):
                successor = max(
                    (probability for _, probability in section.likely_successors),
                    default=0.0,
                )
                probability = _clamp(
                    (0.72 * section.probability)
                    + (0.18 * successor)
                    + (0.10 * observed.trajectory_probability)
                )
                existing = by_target.get(section.expected_end_bar)
                by_target[section.expected_end_bar] = dataclasses.replace(
                    observed,
                    target_bar=section.expected_end_bar,
                    observed_probability=0.0,
                    predicted_probability=max(
                        probability,
                        existing.predicted_probability if existing else 0.0,
                    ),
                    phrase_probability=(
                        existing.phrase_probability if existing else 0.0
                    ),
                    section_probability=probability,
                    recurrence_probability=max(
                        observed.recurrence_probability,
                        section.prefix_match,
                    ),
                    cold_start=False,
                )
        return tuple(
            sorted(
                by_target.values(),
                key=lambda item: (item.target_bar, -item.predicted_probability),
            )
        )


class StructurePredictionEngine:
    """Convert online tracker state into bar-indexed structural events."""

    def __init__(
        self,
        *,
        beats_per_bar: int = 4,
        phrase_event_threshold: float = 0.48,
        section_event_threshold: float = 0.62,
        model_version: str = "dreamsync-structure-v1",
    ) -> None:
        self.beats_per_bar = max(1, int(beats_per_bar))
        self.phrase_event_threshold = float(phrase_event_threshold)
        self.section_event_threshold = float(section_event_threshold)
        self.model_version = model_version
        self.tracker = OnlineStructureTracker(
            section_threshold=min(0.56, self.section_event_threshold),
        )
        self._sequence = 0

    def reset(self) -> None:
        self.tracker.reset()
        self._sequence = 0

    def observe(
        self,
        fingerprint: LiveBarFingerprint,
        *,
        similarities: tuple[BarSimilarity, ...],
        sequence_matches: tuple[SequenceMatch, ...],
        current_beat_index: int,
        current_bar_index: int,
        downbeat_t: float,
        beat_period: float,
    ) -> tuple[PredictedMusicalEvent, ...]:
        snapshot = self.tracker.observe(
            fingerprint,
            similarities=similarities,
            sequence_matches=sequence_matches,
        )
        events: list[PredictedMusicalEvent] = []
        events.append(
            self._event(
                created_t=downbeat_t,
                target_bar=current_bar_index + 1,
                current_bar=current_bar_index,
                current_beat=current_beat_index,
                beat_period=beat_period,
                event_type="bar_marker",
                probability=min(
                    0.95,
                    0.55 + (0.40 * fingerprint.meter_confidence),
                ),
                cold_start=False,
                evidence=(
                    PredictionEvidence("meter", fingerprint.meter_confidence),
                ),
            )
        )
        section_hypothesis = (
            snapshot.section_hypotheses[0]
            if snapshot.section_hypotheses
            else None
        )
        for boundary in snapshot.upcoming:
            if (
                boundary.section_probability >= self.section_event_threshold
                and not boundary.cold_start
            ):
                event_type = (
                    "section_repeat"
                    if section_hypothesis is not None
                    and section_hypothesis.recurrence_count > 1
                    else "section_transition"
                )
                target_section = (
                    section_hypothesis.likely_successors[0][0]
                    if section_hypothesis
                    and section_hypothesis.likely_successors
                    else section_hypothesis.section_id
                    if section_hypothesis is not None
                    else None
                )
                alternatives = (
                    tuple(
                        PredictionAlternative(value, probability)
                        for value, probability in section_hypothesis.likely_successors
                    )
                    if section_hypothesis is not None
                    else ()
                )
            elif boundary.phrase_probability >= self.phrase_event_threshold:
                event_type = "phrase_boundary"
                target_section = None
                alternatives = ()
            else:
                continue
            events.append(
                self._event(
                    created_t=downbeat_t,
                    target_bar=boundary.target_bar,
                    current_bar=current_bar_index,
                    current_beat=current_beat_index,
                    beat_period=beat_period,
                    event_type=event_type,
                    probability=boundary.predicted_probability,
                    cold_start=boundary.cold_start,
                    evidence=boundary.evidence,
                    target_section=target_section,
                    alternatives=alternatives,
                    matched_earlier_bar=snapshot.matched_earlier_bar,
                    expected_duration_bars=(
                        boundary.target_bar
                        - (
                            section_hypothesis.start_bar
                            if section_hypothesis is not None
                            else current_bar_index
                        )
                    ),
                )
            )
        return tuple(events)

    def _event(
        self,
        *,
        created_t: float,
        target_bar: int,
        current_bar: int,
        current_beat: int,
        beat_period: float,
        event_type: str,
        probability: float,
        cold_start: bool,
        evidence: tuple[PredictionEvidence, ...],
        target_section: str | None = None,
        alternatives: tuple[PredictionAlternative, ...] = (),
        matched_earlier_bar: int | None = None,
        expected_duration_bars: int | None = None,
    ) -> PredictedMusicalEvent:
        self._sequence += 1
        delta_bars = max(1, target_bar - current_bar)
        target_beat = current_beat + (delta_bars * self.beats_per_bar)
        target_t = created_t + (
            delta_bars * self.beats_per_bar * max(1e-3, beat_period)
        )
        timing_sigma = max(0.01, beat_period * 0.18)
        target = StructuralCueTarget(
            target_beat_index=target_beat,
            target_bar_index=target_bar,
            target_t=target_t,
            requires_downbeat=True,
            timing_sigma=timing_sigma,
        )
        return PredictedMusicalEvent(
            prediction_id=f"structure-{self._sequence}",
            created_t=created_t,
            target_t=target_t,
            timing_sigma=timing_sigma,
            event_type=event_type,
            target_function=None,
            target_section=target_section,
            direction=None,
            probability=_clamp(probability),
            alternatives=alternatives,
            evidence=evidence,
            model_version=self.model_version,
            raw_probability=probability,
            target=target,
            cold_start=cold_start,
            matched_earlier_bar=matched_earlier_bar,
            expected_duration_bars=expected_duration_bars,
        )


def _regularity(elapsed_bars: int) -> float:
    if elapsed_bars < 1:
        return 0.0
    return max(
        math.exp(-0.75 * abs(elapsed_bars - length))
        for length in (2, 4, 8, 12, 16)
    )


def _trajectory_probability(fingerprint: LiveBarFingerprint) -> float:
    def slope(values: tuple[float, ...]) -> float:
        return abs(values[-1] - values[0]) if len(values) >= 2 else 0.0

    return _clamp(
        (0.45 * slope(fingerprint.energy_shape))
        + (0.35 * slope(fingerprint.onset_shape))
        + (0.20 * slope(fingerprint.bass_shape))
    )


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
