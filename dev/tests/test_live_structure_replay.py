from __future__ import annotations

import dataclasses

import pytest

from dreamsync.prediction.structure_models import LiveBeatStructureObservation
from dreamsync.prediction.structure_replay import replay_structure_observations


def _fixture() -> tuple[LiveBeatStructureObservation, ...]:
    rows = []
    sections = ("A", "A", "B", "B", "A", "A", "B")
    for beat_index in range(len(sections) * 4):
        bar = beat_index // 4
        position = beat_index % 4
        section = sections[bar]
        rows.append(
            LiveBeatStructureObservation(
                t=beat_index * 0.5,
                beat_index=beat_index,
                bar_index=bar,
                beat_in_bar=position,
                meter=(4, 4),
                meter_confidence=0.95,
                downbeat=position == 0,
                beat_period=0.5,
                chroma=(1.0,) + (0.0,) * 11,
                chroma_confidence=0.9,
                harmonic_novelty=0.0,
                mfcc=((0.0,) * 13 if section == "A" else (6.0,) * 13),
                band_ratios=(
                    (0.2, 0.3, 0.5)
                    if section == "A"
                    else (0.75, 0.15, 0.10)
                ),
                band_fluxes=(
                    (0.2, 0.8, 0.2)
                    if section == "A"
                    else (1.0, 0.0, 1.0)
                ),
                bass_ratio=0.3,
                energy=0.4,
                onset_strength=(
                    (0.2, 0.8, 0.2, 0.8)[position]
                    if section == "A"
                    else (1.0, 0.0, 1.0, 0.0)[position]
                ),
                spectral_centroid=900.0 if section == "A" else 4200.0,
                chord_label="C",
                chord_confidence=0.9,
                key_label="C major",
                key_confidence=0.9,
            )
        )
    return tuple(rows)


def test_replay_is_deterministic_and_label_independent() -> None:
    labelled = replay_structure_observations(_fixture())
    cleared_rows = tuple(row.without_tonal_labels() for row in _fixture())
    cleared = replay_structure_observations(cleared_rows)
    repeated = replay_structure_observations(_fixture())
    assert labelled.deterministic_signature() == cleared.deterministic_signature()
    assert labelled.deterministic_signature() == repeated.deterministic_signature()
    assert labelled.memory_bars <= 256


def test_replay_rejects_noncausal_observation_order() -> None:
    rows = list(_fixture()[:3])
    rows[1] = dataclasses.replace(rows[1], t=-1.0)
    with pytest.raises(ValueError, match="causal"):
        replay_structure_observations(tuple(rows))
