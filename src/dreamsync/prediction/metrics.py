"""Small dependency-free metrics for replay evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Iterable


@dataclass(frozen=True)
class PredictionMetrics:
    count: int
    accuracy: float
    brier_score: float
    mean_timing_error: float
    abstention_rate: float


def score_binary_predictions(
    rows: Iterable[tuple[float | None, bool, float | None]],
) -> PredictionMetrics:
    materialized = tuple(rows)
    predicted = tuple(row for row in materialized if row[0] is not None)
    if not materialized:
        return PredictionMetrics(0, 0.0, 0.0, 0.0, 0.0)
    if not predicted:
        return PredictionMetrics(
            len(materialized), 0.0, 0.0, 0.0, 1.0
        )
    correct = sum((float(probability) >= 0.5) == actual for probability, actual, _ in predicted)
    brier = sum(
        (float(probability) - float(actual)) ** 2
        for probability, actual, _ in predicted
    ) / len(predicted)
    timing = [
        abs(float(error))
        for _probability, _actual, error in predicted
        if error is not None
    ]
    return PredictionMetrics(
        count=len(materialized),
        accuracy=correct / len(predicted),
        brier_score=brier,
        mean_timing_error=sum(timing) / len(timing) if timing else 0.0,
        abstention_rate=(len(materialized) - len(predicted)) / len(materialized),
    )


def calibration_error(rows: Iterable[tuple[float, bool]], bins: int = 10) -> float:
    grouped: list[list[tuple[float, bool]]] = [[] for _ in range(max(1, bins))]
    for probability, actual in rows:
        p = max(0.0, min(1.0, float(probability)))
        grouped[min(len(grouped) - 1, int(p * len(grouped)))].append((p, actual))
    total = sum(len(group) for group in grouped)
    if not total:
        return 0.0
    return sqrt(
        sum(
            len(group)
            * (
                sum(p for p, _ in group) / len(group)
                - sum(float(actual) for _, actual in group) / len(group)
            )
            ** 2
            for group in grouped
            if group
        )
        / total
    )
