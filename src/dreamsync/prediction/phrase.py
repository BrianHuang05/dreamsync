"""Bar summaries and competing causal phrase-length hypotheses."""

from __future__ import annotations

from dataclasses import dataclass
from math import exp

from .models import KeyHypothesis, LiveMusicalObservation, PredictiveBarSummary
from .priors import phrase_length_distribution


@dataclass(frozen=True)
class PhraseLengthHypothesis:
    expected_bars: int | None
    phrase_start_bar: int
    current_position: int
    probability: float
    cadence_support: float
    recurrence_support: float
    survival_probability: float


@dataclass(frozen=True)
class PhraseTrackerResult:
    hypotheses: tuple[PhraseLengthHypothesis, ...]
    boundary_probability: float
    precise_timing: bool


class PredictiveBarBuilder:
    """Create immutable bar summaries from already-committed observations."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._bar_index: int | None = None
        self._observations: list[LiveMusicalObservation] = []
        self._functions: list[str] = []
        self._keys: tuple[KeyHypothesis, ...] = ()

    def observe(
        self,
        observation: LiveMusicalObservation,
        *,
        function: str | None,
        keys: tuple[KeyHypothesis, ...],
    ) -> PredictiveBarSummary | None:
        if observation.bar_index is None:
            return None
        completed = None
        if (
            self._bar_index is not None
            and observation.bar_index != self._bar_index
            and self._observations
        ):
            completed = self._build()
            self._observations = []
            self._functions = []
        self._bar_index = observation.bar_index
        self._observations.append(observation)
        if function and (not self._functions or self._functions[-1] != function):
            self._functions.append(function)
        self._keys = keys
        return completed

    def flush(self) -> PredictiveBarSummary | None:
        return self._build() if self._observations else None

    def _build(self) -> PredictiveBarSummary:
        observations = tuple(self._observations)
        first, last = observations[0], observations[-1]
        durations = tuple(
            float(item.chord_duration_beats or 1.0)
            for item in observations
            if item.chord_change
        )
        return PredictiveBarSummary(
            start_t=first.t,
            end_t=last.t + float(last.beat_period or 0.0),
            bar_index=int(self._bar_index or 0),
            meter_confidence=sum(item.meter_confidence for item in observations)
            / len(observations),
            functional_chords=tuple(self._functions),
            chord_durations_beats=durations,
            key_hypotheses=self._keys,
            harmonic_rhythm=sum(float(item.chord_change) for item in observations)
            / max(1, len(observations)),
            cadence_features=(),
            energy=sum(item.energy for item in observations) / len(observations),
            energy_slope=last.energy - first.energy,
            onset_density=sum(item.onset_density for item in observations)
            / len(observations),
            onset_slope=last.onset_density - first.onset_density,
            texture=(last.spectral_centroid, last.centroid_delta),
        )


class CompetingPhraseTracker:
    """Maintain length alternatives with a soft boundary hazard."""

    def __init__(
        self,
        *,
        lengths: tuple[int, ...] = (2, 4, 8, 12, 16),
        minimum_meter_confidence: float = 0.22,
    ) -> None:
        if any(length < 2 for length in lengths):
            raise ValueError("phrase lengths must be at least two bars")
        self.lengths = tuple(sorted(set(int(length) for length in lengths)))
        self.minimum_meter_confidence = float(minimum_meter_confidence)
        self.reset()

    def reset(self) -> None:
        self._start_bar: int | None = None
        self._weights: dict[int | None, float] = {}
        self._last = PhraseTrackerResult((), 0.0, False)

    @property
    def result(self) -> PhraseTrackerResult:
        return self._last

    def observe_bar(
        self,
        summary: PredictiveBarSummary,
        *,
        meter: tuple[int, int] | None = (4, 4),
        cadence_support: float = 0.0,
        recurrence_lengths: tuple[int, ...] = (),
        momentum_boundary_support: float = 0.0,
        boundary_observed: bool = False,
    ) -> PhraseTrackerResult:
        if self._start_bar is None:
            self._start_bar = summary.bar_index
            priors = phrase_length_distribution(meter)
            self._weights = {
                length: priors.get(length, 0.01) for length in self.lengths
            }
            self._weights[None] = priors.get(None, 0.1)
        elapsed = max(1, summary.bar_index - self._start_bar + 1)
        cadence = _clamp01(cadence_support)
        momentum = _clamp01(momentum_boundary_support)
        recurrence_set = set(recurrence_lengths)
        adjusted: dict[int | None, float] = {}
        for length, weight in self._weights.items():
            if length is None:
                boundary_fit = 0.38
                survival = 0.95
                recurrence = 0.0
            else:
                distance = abs(elapsed - length)
                boundary_fit = exp(-0.85 * distance)
                # Passing an expected boundary weakens but never kills it.
                survival = 1.0 if elapsed <= length else max(
                    0.12, exp(-0.32 * (elapsed - length))
                )
                recurrence = 1.0 if length in recurrence_set else 0.0
            likelihood = survival
            if boundary_observed:
                likelihood *= 0.25 + (0.75 * boundary_fit)
            else:
                likelihood *= 1.0 - (0.20 * boundary_fit * max(cadence, momentum))
            likelihood *= 1.0 + (0.8 * recurrence)
            adjusted[length] = max(1e-9, weight * likelihood)
        self._weights = _normalize(adjusted)

        boundary_fit = sum(
            probability
            * (
                0.35
                if length is None
                else exp(-0.85 * abs(elapsed - length))
            )
            for length, probability in self._weights.items()
        )
        boundary_probability = _clamp01(
            0.08
            + (0.42 * boundary_fit)
            + (0.22 * cadence)
            + (0.18 * momentum)
        )
        hypotheses = tuple(
            PhraseLengthHypothesis(
                expected_bars=length,
                phrase_start_bar=self._start_bar,
                current_position=elapsed,
                probability=probability,
                cadence_support=cadence,
                recurrence_support=1.0 if length in recurrence_set else 0.0,
                survival_probability=(
                    0.95
                    if length is None
                    else 1.0
                    if elapsed <= length
                    else max(0.12, exp(-0.32 * (elapsed - length)))
                ),
            )
            for length, probability in sorted(
                self._weights.items(),
                key=lambda item: item[1],
                reverse=True,
            )
        )
        self._last = PhraseTrackerResult(
            hypotheses=hypotheses,
            boundary_probability=boundary_probability,
            precise_timing=summary.meter_confidence >= self.minimum_meter_confidence,
        )
        if boundary_observed and boundary_probability >= 0.45:
            self._start_bar = summary.bar_index + 1
            priors = phrase_length_distribution(meter)
            self._weights = {
                **{length: priors.get(length, 0.01) for length in self.lengths},
                None: priors.get(None, 0.1),
            }
        return self._last


def _normalize(weights: dict[int | None, float]) -> dict[int | None, float]:
    total = sum(weights.values()) or 1.0
    return {key: value / total for key, value in weights.items()}


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
