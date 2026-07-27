"""Per-event probability calibration and reporting."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Iterable

from .metrics import calibration_error
from .models import PredictedMusicalEvent, PredictionAlternative


@dataclass(frozen=True)
class CalibrationParameters:
    slope: float = 1.0
    intercept: float = 0.0
    minimum_probability: float = 0.42


DEFAULT_CALIBRATION: dict[str, CalibrationParameters] = {
    "bar_marker": CalibrationParameters(1.0, 0.0, 0.42),
    "chord_change": CalibrationParameters(1.0, -0.05, 0.40),
    "harmonic_resolution": CalibrationParameters(1.12, -0.18, 0.58),
    "phrase_boundary": CalibrationParameters(1.05, -0.12, 0.52),
    "section_entrance": CalibrationParameters(1.08, -0.20, 0.60),
    "section_repeat": CalibrationParameters(1.05, -0.12, 0.58),
    "section_transition": CalibrationParameters(1.08, -0.20, 0.60),
    "build_release": CalibrationParameters(1.0, -0.15, 0.58),
    "chorus_entrance": CalibrationParameters(1.18, -0.30, 0.72),
    "verse_repeat": CalibrationParameters(1.10, -0.22, 0.66),
    "mood_change": CalibrationParameters(0.92, -0.18, 0.58),
}


class PredictionCalibrator:
    def __init__(
        self,
        parameters: dict[str, CalibrationParameters] | None = None,
    ) -> None:
        self.parameters = dict(DEFAULT_CALIBRATION)
        if parameters:
            self.parameters.update(parameters)

    def calibrate(
        self, event: PredictedMusicalEvent
    ) -> PredictedMusicalEvent | None:
        params = self.parameters[event.event_type]
        probability = _logistic_calibrate(
            event.probability, params.slope, params.intercept
        )
        if probability < params.minimum_probability:
            return None
        alternatives = _reweight_alternatives(
            event.alternatives,
            event.target_function or event.target_section or event.direction,
            probability,
        )
        return replace(
            event,
            probability=probability,
            alternatives=alternatives,
            raw_probability=(
                event.probability
                if event.raw_probability is None
                else event.raw_probability
            ),
        )


@dataclass(frozen=True)
class CalibrationReport:
    event_type: str
    count: int
    root_mean_squared_calibration_error: float


def report_calibration(
    rows: Iterable[tuple[str, float, bool]],
) -> tuple[CalibrationReport, ...]:
    grouped: dict[str, list[tuple[float, bool]]] = {}
    for event_type, probability, actual in rows:
        grouped.setdefault(event_type, []).append((probability, actual))
    return tuple(
        CalibrationReport(
            event_type=event_type,
            count=len(values),
            root_mean_squared_calibration_error=calibration_error(values),
        )
        for event_type, values in sorted(grouped.items())
    )


def _logistic_calibrate(probability: float, slope: float, intercept: float) -> float:
    p = max(1e-6, min(1.0 - 1e-6, float(probability)))
    logit = math.log(p / (1.0 - p))
    return 1.0 / (1.0 + math.exp(-((slope * logit) + intercept)))


def _reweight_alternatives(
    alternatives: tuple[PredictionAlternative, ...],
    target: str | None,
    target_probability: float,
) -> tuple[PredictionAlternative, ...]:
    if not alternatives:
        return ()
    selected_index = next(
        (
            index
            for index, alternative in enumerate(alternatives)
            if alternative.value == target
        ),
        0,
    )
    other_total = sum(
        alternative.probability
        for index, alternative in enumerate(alternatives)
        if index != selected_index
    )
    rows: list[PredictionAlternative] = []
    for index, alternative in enumerate(alternatives):
        if index == selected_index:
            probability = target_probability
        elif other_total > 0.0:
            probability = (
                alternative.probability / other_total
            ) * (1.0 - target_probability)
        else:
            probability = 0.0
        rows.append(replace(alternative, probability=probability))
    return tuple(rows)
