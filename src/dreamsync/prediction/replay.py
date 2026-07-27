"""Deterministic replay of derived observations (never raw audio)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from .models import LiveMusicalObservation, PredictedMusicalEvent


class ObservationPredictor(Protocol):
    def reset(self) -> None: ...

    def observe(
        self, observation: LiveMusicalObservation
    ) -> tuple[PredictedMusicalEvent, ...]: ...


@dataclass(frozen=True)
class ReplayResult:
    observations: int
    predictions: tuple[PredictedMusicalEvent, ...]


class DeterministicPredictionReplay:
    def __init__(self, predictor: ObservationPredictor) -> None:
        self.predictor = predictor

    def run(
        self,
        observations: Sequence[LiveMusicalObservation],
        *,
        through_t: float | None = None,
    ) -> ReplayResult:
        self.predictor.reset()
        predictions: list[PredictedMusicalEvent] = []
        count = 0
        previous_t = float("-inf")
        for observation in observations:
            if observation.t < previous_t:
                raise ValueError("replay observations must be timestamp ordered")
            if through_t is not None and observation.t > through_t:
                break
            previous_t = observation.t
            count += 1
            predictions.extend(self.predictor.observe(observation))
        return ReplayResult(count, tuple(predictions))


def assert_prefix_causal(
    predictor: ObservationPredictor,
    observations: Sequence[LiveMusicalObservation],
) -> None:
    """Raise if any replay prefix changes after future observations are added."""

    replay = DeterministicPredictionReplay(predictor)
    for end in range(1, len(observations) + 1):
        prefix = replay.run(observations[:end]).predictions
        through = observations[end - 1].t
        full_to_same_time = replay.run(observations, through_t=through).predictions
        if prefix != full_to_same_time:
            raise AssertionError(f"future observation leaked into prefix {end}")
