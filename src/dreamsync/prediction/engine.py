"""Hierarchical causal predictive-structure engine."""

from __future__ import annotations

from dataclasses import dataclass

from dreamsync.dsp.tonality import CausalKeyTracker

from .function import functional_hypotheses
from .memory import SongLocalMemory
from .models import (
    LiveMusicalObservation,
    PhraseFingerprint,
    PredictedMusicalEvent,
    PredictionAlternative,
    PredictionEvidence,
)
from .momentum import MomentumTrajectoryPredictor
from .online_sequence import FunctionalToken, token_function
from .phrase import CompetingPhraseTracker, PredictiveBarBuilder
from .priors import cadence_distribution, next_function_distribution
from .section import SectionOrderMemory


@dataclass(frozen=True)
class PredictionEngineConfig:
    minimum_event_probability: float = 0.42
    phrase_boundary_threshold: float = 0.50
    section_prefix_threshold: float = 0.62
    model_version: str = "dreamsync-hybrid-v1"


@dataclass(frozen=True)
class PredictionSnapshot:
    key_hypotheses: tuple = ()
    functional_hypotheses: tuple = ()
    phrase_hypotheses: tuple = ()
    predictions: tuple[PredictedMusicalEvent, ...] = ()
    section_alternatives: tuple = ()
    momentum: tuple = ()


class PredictiveStructureEngine:
    def __init__(self, config: PredictionEngineConfig | None = None) -> None:
        self.config = config or PredictionEngineConfig()
        self.key_tracker = CausalKeyTracker()
        self.memory = SongLocalMemory()
        self.bar_builder = PredictiveBarBuilder()
        self.phrase_tracker = CompetingPhraseTracker()
        self.sections = SectionOrderMemory()
        self.momentum = MomentumTrajectoryPredictor()
        self.reset()

    def reset(self) -> None:
        self.key_tracker.reset()
        self.memory.reset()
        self.bar_builder.reset()
        self.phrase_tracker.reset()
        self.sections.reset()
        self.momentum.reset()
        self._functions: list[str] = []
        self._durations: list[int] = []
        self._completed_phrases: list[PhraseFingerprint] = []
        self._section_energy: list[int] = []
        self._section_onsets: list[int] = []
        self._sequence = 0
        self._snapshot = PredictionSnapshot()

    @property
    def snapshot(self) -> PredictionSnapshot:
        return self._snapshot

    def observe(
        self, observation: LiveMusicalObservation
    ) -> tuple[PredictedMusicalEvent, ...]:
        keys = self.key_tracker.observe(
            chroma=observation.chroma,
            chord=observation.absolute_chord,
            chroma_confidence=observation.tonal_confidence,
            chord_confidence=observation.chord_confidence,
            duration_beats=observation.chord_duration_beats or 1.0,
        )
        functions = functional_hypotheses(
            observation.absolute_chord,
            keys,
            chord_confidence=observation.chord_confidence,
            tonal_confidence=observation.tonal_confidence,
        )
        function = functions[0].numeral if functions else None
        if observation.chord_change and function:
            token = FunctionalToken(
                function,
                observation.beat_in_bar,
                observation.chord_duration_beats,
            )
            self.memory.sequence.observe(
                token,
                probability=functions[0].probability,
            )
            self._functions.append(function)
            self._durations.append(
                max(1, int(round(observation.chord_duration_beats or 1.0)))
            )
            self._functions = self._functions[-128:]
            self._durations = self._durations[-128:]

        completed_bar = self.bar_builder.observe(
            observation,
            function=function,
            keys=keys,
        )
        recurrence = self.memory.phrases.match_partial(
            tuple(self._functions[-16:]),
            duration_pattern=tuple(self._durations[-16:]),
        )
        cadence = cadence_distribution(tuple(self._functions[-3:])).as_dict()
        cadence_support = 1.0 - cadence.get("continuation", 0.0) - cadence.get("unknown", 0.0)
        if completed_bar is not None:
            phrase_result = self.phrase_tracker.observe_bar(
                completed_bar,
                cadence_support=cadence_support,
                recurrence_lengths=tuple(
                    match.fingerprint.bars for match in recurrence[:3]
                ),
                momentum_boundary_support=min(
                    1.0,
                    abs(completed_bar.energy_slope)
                    + abs(completed_bar.onset_slope),
                ),
            )
            self._section_energy.append(int(round(completed_bar.energy * 4.0)))
            self._section_onsets.append(
                int(round(completed_bar.onset_density * 4.0))
            )
        else:
            phrase_result = self.phrase_tracker.result

        trajectories = self.momentum.observe(
            observation,
            function=function,
            key_stability=keys[0].stability if keys else 0.0,
        )
        predictions = self._predict(
            observation,
            function=function,
            functions=functions,
            keys=keys,
            phrase_result=phrase_result,
            recurrence=recurrence,
            trajectories=trajectories,
        )
        section_alternatives = self.sections.match_prefix(
            tuple(self._functions[-8:])
        )
        self._snapshot = PredictionSnapshot(
            key_hypotheses=keys,
            functional_hypotheses=functions,
            phrase_hypotheses=phrase_result.hypotheses,
            predictions=predictions,
            section_alternatives=section_alternatives[:3],
            momentum=trajectories[:5],
        )
        return predictions

    def complete_phrase(self, *, bars: int, cadence_class: str = "unknown") -> None:
        count = max(1, min(len(self._functions), int(bars)))
        fingerprint = PhraseFingerprint(
            bars=int(bars),
            numerals=tuple(self._functions[-count:]),
            chord_duration_pattern=tuple(self._durations[-count:]),
            cadence_class=cadence_class,
            harmonic_rhythm_pattern=(1,) * count,
            energy_shape=tuple(self._section_energy[-int(bars) :]),
            onset_shape=tuple(self._section_onsets[-int(bars) :]),
            key_path=tuple(
                f"{key.tonic_pc}:{key.mode}"
                for key in self.key_tracker.hypotheses[:2]
            ),
        )
        self.memory.phrases.remember(fingerprint)
        self._completed_phrases.append(fingerprint)

    def complete_section(self) -> str | None:
        if not self._completed_phrases:
            return None
        section = self.sections.observe_completed(
            phrases=tuple(self._completed_phrases),
            duration_bars=sum(item.bars for item in self._completed_phrases),
            entrance_function=(
                self._completed_phrases[0].numerals[0]
                if self._completed_phrases[0].numerals
                else None
            ),
            exit_function=(
                self._completed_phrases[-1].numerals[-1]
                if self._completed_phrases[-1].numerals
                else None
            ),
            energy_envelope=tuple(self._section_energy),
            onset_envelope=tuple(self._section_onsets),
        )
        self._completed_phrases.clear()
        self._section_energy.clear()
        self._section_onsets.clear()
        return section.section_id

    def _predict(
        self,
        observation,
        *,
        function,
        functions,
        keys,
        phrase_result,
        recurrence,
        trajectories,
    ) -> tuple[PredictedMusicalEvent, ...]:
        events: list[PredictedMusicalEvent] = []
        generic = next_function_distribution(tuple(self._functions[-3:])).as_dict()
        local_prediction = self.memory.sequence.predict()
        local: dict[str, float] = {}
        if local_prediction:
            for compact, probability in local_prediction.distribution:
                local[token_function(compact)] = local.get(
                    token_function(compact), 0.0
                ) + probability
        recurrence_distribution: dict[str, float] = {}
        if recurrence and recurrence[0].continuation:
            recurrence_distribution[recurrence[0].continuation] = recurrence[0].score
        candidates = set(generic) | set(local) | set(recurrence_distribution)
        candidates.discard("unknown")
        local_weight = min(0.62, self.memory.sequence.observations / 16.0)
        scores = {
            candidate: (
                ((0.45 - (0.25 * local_weight)) * generic.get(candidate, 0.0))
                + (local_weight * local.get(candidate, 0.0))
                + (0.35 * recurrence_distribution.get(candidate, 0.0))
            )
            for candidate in candidates
        }
        alternatives = _normalize_alternatives(scores)
        target_t = _next_downbeat_t(observation)
        known_alternatives = tuple(
            item for item in alternatives if item.value != "unknown"
        )
        if (
            known_alternatives
            and known_alternatives[0].probability
            >= self.config.minimum_event_probability
        ):
            top = known_alternatives[0]
            evidence = [
                PredictionEvidence("musical_prior", generic.get(top.value, 0.0)),
                PredictionEvidence("song_local", local.get(top.value, 0.0)),
                PredictionEvidence(
                    "phrase_recurrence",
                    recurrence_distribution.get(top.value, 0.0),
                ),
            ]
            events.append(
                self._event(
                    observation.t,
                    target_t,
                    "chord_change",
                    top.probability,
                    target_function=top.value,
                    alternatives=alternatives,
                    evidence=tuple(evidence),
                    timing_sigma=_timing_sigma(observation),
                )
            )
            if (
                function == "V"
                and top.value == "I"
                and phrase_result.precise_timing
            ):
                resolution_probability = min(
                    0.96,
                    0.50
                    + (0.32 * top.probability)
                    + (0.06 * phrase_result.boundary_probability)
                    + (0.08 * observation.meter_confidence)
                    + (
                        0.04
                        * (functions[0].probability if functions else 0.0)
                    ),
                )
                events.append(
                    self._event(
                        observation.t,
                        target_t,
                        "harmonic_resolution",
                        resolution_probability,
                        target_function="I",
                        alternatives=alternatives,
                        evidence=(
                            PredictionEvidence("dominant_resolution", top.probability),
                            PredictionEvidence(
                                "phrase_position",
                                phrase_result.boundary_probability,
                            ),
                            PredictionEvidence(
                                "meter", observation.meter_confidence
                            ),
                            PredictionEvidence(
                                "key", keys[0].probability if keys else 0.0
                            ),
                        ),
                        timing_sigma=_timing_sigma(observation),
                    )
                )
        if (
            phrase_result.boundary_probability
            >= self.config.phrase_boundary_threshold
        ):
            events.append(
                self._event(
                    observation.t,
                    target_t,
                    "phrase_boundary",
                    phrase_result.boundary_probability,
                    alternatives=tuple(
                        PredictionAlternative(
                            str(item.expected_bars or "irregular"),
                            item.probability,
                        )
                        for item in phrase_result.hypotheses[:4]
                    ),
                    evidence=(
                        PredictionEvidence(
                            "phrase_hazard",
                            phrase_result.boundary_probability,
                        ),
                    ),
                    timing_sigma=_timing_sigma(observation),
                )
            )
        section_matches = self.sections.match_prefix(tuple(self._functions[-8:]))
        if (
            section_matches
            and section_matches[0].probability
            >= self.config.section_prefix_threshold
        ):
            match = section_matches[0]
            events.append(
                self._event(
                    observation.t,
                    target_t,
                    "section_repeat",
                    match.probability,
                    target_section=match.section_id,
                    alternatives=tuple(
                        PredictionAlternative(item.section_id, item.probability)
                        for item in section_matches[:3]
                    ),
                    evidence=(
                        PredictionEvidence("section_prefix", match.probability),
                    ),
                    timing_sigma=_timing_sigma(observation),
                )
            )
        if trajectories and trajectories[0].probability >= 0.62:
            trajectory = trajectories[0]
            events.append(
                self._event(
                    observation.t,
                    observation.t
                    + ((observation.beat_period or 0.5) * trajectory.horizon_beats),
                    "mood_change",
                    trajectory.probability,
                    direction=f"{trajectory.axis}:{trajectory.direction}",
                    alternatives=(
                        PredictionAlternative(
                            trajectory.direction, trajectory.probability
                        ),
                        PredictionAlternative(
                            "stable", 1.0 - trajectory.probability
                        ),
                    ),
                    evidence=(
                        PredictionEvidence(
                            f"momentum_{trajectory.axis}",
                            trajectory.probability,
                            f"slope={trajectory.slope:.4f}",
                        ),
                    ),
                    timing_sigma=(observation.beat_period or 0.5) * 2.0,
                )
            )
        return tuple(events)

    def _event(
        self,
        created_t: float,
        target_t: float,
        event_type: str,
        probability: float,
        *,
        target_function: str | None = None,
        target_section: str | None = None,
        direction: str | None = None,
        alternatives: tuple[PredictionAlternative, ...],
        evidence: tuple[PredictionEvidence, ...],
        timing_sigma: float,
    ) -> PredictedMusicalEvent:
        self._sequence += 1
        return PredictedMusicalEvent(
            prediction_id=f"hybrid-{self._sequence}",
            created_t=created_t,
            target_t=target_t,
            timing_sigma=timing_sigma,
            event_type=event_type,
            target_function=target_function,
            target_section=target_section,
            direction=direction,
            probability=max(0.0, min(1.0, probability)),
            alternatives=alternatives,
            evidence=evidence,
            model_version=self.config.model_version,
            raw_probability=probability,
        )


def _normalize_alternatives(
    scores: dict[str, float],
) -> tuple[PredictionAlternative, ...]:
    positive = {key: max(0.0, value) for key, value in scores.items()}
    unknown = max(0.05, 1.0 - sum(positive.values()))
    positive["unknown"] = unknown
    total = sum(positive.values()) or 1.0
    return tuple(
        PredictionAlternative(value, score / total)
        for value, score in sorted(
            positive.items(), key=lambda item: (-item[1], item[0])
        )
    )


def _next_downbeat_t(observation: LiveMusicalObservation) -> float:
    beat_period = observation.beat_period or 0.5
    if observation.meter and observation.beat_in_bar is not None:
        beats = observation.meter[0] - observation.beat_in_bar
        return observation.t + (max(1, beats) * beat_period)
    return observation.t + beat_period


def _timing_sigma(observation: LiveMusicalObservation) -> float:
    period = observation.beat_period or 0.5
    return max(0.04, period * (0.12 + (0.55 * (1.0 - observation.meter_confidence))))
