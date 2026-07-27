"""Interpretable causal trajectory prediction over bounded recent history."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .models import LiveMusicalObservation


@dataclass(frozen=True)
class TrajectoryPrediction:
    axis: str
    direction: str
    probability: float
    horizon_beats: int
    slope: float


class MomentumTrajectoryPredictor:
    AXES = ("energy", "brightness", "tension", "density", "harmonic_stability")

    def __init__(self, history_beats: int = 16) -> None:
        self.history_beats = max(4, int(history_beats))
        self.reset()

    def reset(self) -> None:
        self._history: deque[dict[str, float]] = deque(maxlen=self.history_beats)

    def observe(
        self,
        observation: LiveMusicalObservation,
        *,
        function: str | None,
        key_stability: float,
    ) -> tuple[TrajectoryPrediction, ...]:
        tension = 1.0 if function in {"V", "vii°", "ii°"} else 0.35 if function else 0.5
        self._history.append(
            {
                "energy": observation.energy,
                "brightness": max(0.0, observation.spectral_centroid),
                "tension": tension,
                "density": observation.onset_density,
                "harmonic_stability": key_stability,
            }
        )
        if len(self._history) < 4:
            return ()
        results: list[TrajectoryPrediction] = []
        for axis in self.AXES:
            values = [row[axis] for row in self._history]
            scale = max(1e-6, max(values) - min(values), abs(values[-1]) * 0.1)
            slope = (values[-1] - values[0]) / max(1, len(values) - 1)
            normalized = slope / scale
            if abs(normalized) < 0.035:
                continue
            direction = "rising" if normalized > 0.0 else "falling"
            confidence = min(0.9, 0.45 + (abs(normalized) * 2.4))
            results.append(
                TrajectoryPrediction(
                    axis=axis,
                    direction=direction,
                    probability=confidence,
                    horizon_beats=min(8, len(values)),
                    slope=slope,
                )
            )
        return tuple(sorted(results, key=lambda item: item.probability, reverse=True))
