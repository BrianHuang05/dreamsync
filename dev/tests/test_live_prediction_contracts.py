from __future__ import annotations

import dataclasses

import pytest

from dreamsync.prediction.event_log import PredictionEventLog, SCHEMA_VERSION
from dreamsync.prediction.models import LiveMusicalObservation


def _observation(t: float = 0.0) -> LiveMusicalObservation:
    return LiveMusicalObservation(
        t=t,
        beat_index=int(t),
        bar_index=0,
        beat_in_bar=0,
        meter=(4, 4),
        meter_confidence=0.8,
        downbeat=True,
        absolute_chord="C",
        chord_confidence=0.9,
        chord_change=True,
        chord_duration_beats=4.0,
        chroma=(1.0,) + (0.0,) * 11,
        tonal_confidence=0.9,
        energy=0.5,
        energy_delta=0.1,
        onset_density=0.4,
        onset_density_delta=0.0,
        spectral_centroid=1200.0,
        centroid_delta=10.0,
        harmonic_rhythm=0.25,
        beat_period=0.5,
    )


def test_observation_is_immutable_and_validated() -> None:
    observation = _observation()
    with pytest.raises(dataclasses.FrozenInstanceError):
        observation.energy = 1.0  # type: ignore[misc]
    with pytest.raises(ValueError, match="12"):
        dataclasses.replace(observation, chroma=(1.0,))


def test_prediction_log_is_bounded_schema_versioned_and_contains_no_pcm() -> None:
    log = PredictionEventLog(max_events=2)
    for index in range(3):
        log.append("observation", t=float(index), payload=_observation(float(index)))
    rows = log.snapshot()
    assert len(rows) == 2
    assert {row["schema_version"] for row in rows} == {SCHEMA_VERSION}
    assert all("pcm" not in repr(row).lower() for row in rows)
