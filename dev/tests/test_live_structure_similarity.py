from __future__ import annotations

import dataclasses
import time

import numpy as np
import pytest

from dreamsync.prediction.similarity import (
    MultiFeatureSimilarityMemory,
    compare_bars,
)
from dreamsync.prediction.structure_models import LiveBarFingerprint


def _bar(
    index: int,
    roots: tuple[int, ...] = (0, 2, 4, 5),
    *,
    mfcc_offset: float = 0.0,
    onset: tuple[float, ...] = (0.2, 1.0, 0.3, 0.7),
    energy: tuple[float, ...] = (-1.0, -0.2, 0.4, 0.8),
    completeness: float = 1.0,
) -> LiveBarFingerprint:
    beat_chroma = []
    for root in roots:
        chroma = [0.0] * 12
        chroma[root % 12] = 1.0
        beat_chroma.append(tuple(chroma))
    profile = tuple(
        sum(chroma[pitch] for chroma in beat_chroma) / len(beat_chroma)
        for pitch in range(12)
    )
    deltas = tuple(
        0.0 if left == right else 1.0
        for left, right in zip(roots, roots[1:])
    )
    return LiveBarFingerprint(
        bar_index=index,
        start_t=index * 2.0,
        end_t=(index + 1) * 2.0,
        beats=4,
        meter=(4, 4),
        meter_confidence=0.9,
        completeness=completeness,
        beat_chroma=tuple(beat_chroma),
        chroma_profile=profile,
        chroma_delta_shape=deltas,
        chroma_confidence=0.9,
        mfcc_mean=tuple(mfcc_offset + value * 0.1 for value in range(13)),
        mfcc_std=(0.1,) * 13,
        band_profile=(0.2, 0.3, 0.5),
        band_flux_shape=onset,
        onset_shape=onset,
        energy_shape=energy,
        bass_shape=(0.2, 0.5, 0.3, 0.7),
        centroid_shape=(-0.4, -0.1, 0.2, 0.5),
    )


def test_same_and_transposed_harmonic_patterns_use_distinct_channels() -> None:
    original = _bar(0)
    same = _bar(1)
    transposed = _bar(2, (5, 7, 9, 10))
    same_score = compare_bars(same, original)
    transposed_score = compare_bars(transposed, original)
    assert same_score.harmonic_absolute > 0.95
    assert same_score.harmonic_transposed > 0.95
    assert transposed_score.harmonic_absolute < 0.5
    assert transposed_score.harmonic_transposed > 0.95
    assert transposed_score.best_pitch_shift in {5, 7}


def test_feature_family_changes_are_explainable_and_loudness_shape_survives() -> None:
    original = _bar(0)
    timbre_change = _bar(1, mfcc_offset=4.0)
    rhythm_change = _bar(2, onset=(1.0, 0.0, 1.0, 0.0))
    louder_same_shape = dataclasses.replace(
        _bar(3),
        energy_shape=tuple(value * 0.8 for value in original.energy_shape),
    )
    assert compare_bars(timbre_change, original).timbre < 0.8
    assert compare_bars(rhythm_change, original).rhythm < 0.95
    assert compare_bars(louder_same_shape, original).dynamics > 0.85


def test_low_chroma_confidence_redistributes_group_weights() -> None:
    low_chroma = dataclasses.replace(_bar(0), chroma_confidence=0.0)
    score = compare_bars(_bar(1), low_chroma)
    weights = dict(score.active_weights)
    assert "harmonic_absolute" not in weights
    assert sum(weights.values()) == pytest.approx(1.0)
    assert weights["timbre"] > 0.25


def test_sequence_uses_one_shift_and_tolerates_one_bad_bar() -> None:
    memory = MultiFeatureSimilarityMemory(max_bars=32)
    first = tuple(_bar(index, (0, 2, 4, 5)) for index in range(8))
    returned = tuple(
        _bar(
            index + 8,
            (5, 7, 9, 10) if index != 4 else (1, 6, 8, 11),
        )
        for index in range(8)
    )
    for bar in first + returned:
        memory.add(bar)
    matches = memory.top_matches(horizon=8)
    assert matches
    match = matches[0]
    assert match.combined > 0.8
    assert match.substituted_bars == 1
    assert match.pitch_shift in {5, 7}


def test_memory_and_rows_remain_bounded() -> None:
    memory = MultiFeatureSimilarityMemory(max_bars=3)
    for index in range(8):
        memory.add(_bar(index))
    assert memory.bar_ids == (5, 6, 7)
    assert len(memory.fingerprints) == 3


def test_completed_bar_update_meets_bounded_runtime_budget() -> None:
    memory = MultiFeatureSimilarityMemory(max_bars=256)
    timings = []
    for index in range(256):
        bar = _bar(
            index,
            tuple((root + (index % 4)) % 12 for root in (0, 2, 4, 5)),
        )
        started = time.perf_counter()
        memory.add(bar)
        timings.append((time.perf_counter() - started) * 1000.0)
    assert float(np.percentile(timings, 95)) < 5.0
    assert float(np.percentile(timings, 99)) < 10.0
