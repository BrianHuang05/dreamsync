from __future__ import annotations

from dreamsync.prediction.baseline import BaselinePredictionAdapter
from dreamsync.prediction.models import LiveMusicalObservation
from dreamsync.prediction.replay import (
    DeterministicPredictionReplay,
    assert_prefix_causal,
)


def _sequence() -> tuple[LiveMusicalObservation, ...]:
    chords = ("C", "F", "G", "C", "F", "G")
    return tuple(
        LiveMusicalObservation(
            t=float(index),
            beat_index=index,
            bar_index=index,
            beat_in_bar=0,
            meter=(4, 4),
            meter_confidence=0.9,
            downbeat=True,
            absolute_chord=chord,
            chord_confidence=0.9,
            chord_change=True,
            chord_duration_beats=4.0,
            chroma=(0.0,) * 12,
            tonal_confidence=0.8,
            energy=0.5,
            energy_delta=0.0,
            onset_density=0.5,
            onset_density_delta=0.0,
            spectral_centroid=1000.0,
            centroid_delta=0.0,
            harmonic_rhythm=0.25,
            beat_period=1.0,
        )
        for index, chord in enumerate(chords)
    )


def test_replay_is_deterministic_and_prefix_causal() -> None:
    predictor = BaselinePredictionAdapter()
    replay = DeterministicPredictionReplay(predictor)
    first = replay.run(_sequence())
    second = replay.run(_sequence())
    assert first == second
    assert first.predictions
    assert_prefix_causal(predictor, _sequence())
