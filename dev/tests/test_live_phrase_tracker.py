from __future__ import annotations

import pytest

from dreamsync.prediction.models import PredictiveBarSummary
from dreamsync.prediction.phrase import CompetingPhraseTracker


def _bar(index: int, meter_confidence: float = 0.9) -> PredictiveBarSummary:
    return PredictiveBarSummary(
        start_t=index * 2.0,
        end_t=(index + 1) * 2.0,
        bar_index=index,
        meter_confidence=meter_confidence,
        functional_chords=("I",),
        chord_durations_beats=(4.0,),
        key_hypotheses=(),
        harmonic_rhythm=0.25,
        cadence_features=(),
        energy=0.5,
        energy_slope=0.0,
        onset_density=0.5,
        onset_slope=0.0,
        texture=(),
    )


@pytest.mark.parametrize("length", (4, 8, 12, 16))
def test_recurrence_support_retains_correct_phrase_length(length: int) -> None:
    tracker = CompetingPhraseTracker()
    result = None
    for index in range(length):
        result = tracker.observe_bar(
            _bar(index),
            cadence_support=0.8 if index == length - 1 else 0.0,
            recurrence_lengths=(length,),
        )
    assert result is not None
    assert result.hypotheses[0].expected_bars == length


def test_cadence_raises_probability_but_does_not_force_boundary() -> None:
    plain = CompetingPhraseTracker().observe_bar(_bar(0))
    cadence = CompetingPhraseTracker().observe_bar(_bar(0), cadence_support=1.0)
    assert cadence.boundary_probability > plain.boundary_probability
    assert cadence.boundary_probability < 0.8


def test_irregular_and_meter_loss_are_representable() -> None:
    tracker = CompetingPhraseTracker()
    result = tracker.observe_bar(_bar(0, meter_confidence=0.1))
    assert any(item.expected_bars is None for item in result.hypotheses)
    assert result.precise_timing is False
