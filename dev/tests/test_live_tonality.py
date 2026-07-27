from __future__ import annotations

import numpy as np

from dreamsync.dsp.tonality import CausalKeyTracker, KeyTrackerConfig


def _major_chroma(tonic: int) -> tuple[float, ...]:
    values = np.zeros(12)
    for interval, weight in ((0, 1.0), (4, 0.8), (7, 0.9)):
        values[(tonic + interval) % 12] = weight
    return tuple(values)


def test_key_posterior_is_normalized_and_keeps_alternatives() -> None:
    tracker = CausalKeyTracker()
    hypotheses = ()
    for _ in range(12):
        hypotheses = tracker.observe(
            chroma=_major_chroma(0),
            chord="C",
            chroma_confidence=0.95,
            chord_confidence=0.95,
        )
    assert len(hypotheses) == 3
    assert sum(item.probability for item in hypotheses) == 1.0
    assert hypotheses[0].tonic_pc == 0
    assert hypotheses[0].mode == "major"


def test_isolated_out_of_key_chord_does_not_force_modulation() -> None:
    tracker = CausalKeyTracker(KeyTrackerConfig(minimum_residence_beats=6))
    for _ in range(10):
        tracker.observe(
            chroma=_major_chroma(0),
            chord="C",
            chroma_confidence=1.0,
            chord_confidence=1.0,
        )
    before = tracker.hypotheses[0]
    tracker.observe(
        chroma=_major_chroma(6),
        chord="F#",
        chroma_confidence=1.0,
        chord_confidence=1.0,
    )
    assert tracker.hypotheses[0].tonic_pc == before.tonic_pc


def test_sustained_key_change_eventually_switches() -> None:
    tracker = CausalKeyTracker(
        KeyTrackerConfig(
            smoothing=0.55,
            minimum_residence_beats=4,
            transition_penalty=0.02,
            change_margin=0.01,
        )
    )
    for _ in range(8):
        tracker.observe(
            chroma=_major_chroma(0),
            chord="C",
            chroma_confidence=1.0,
            chord_confidence=1.0,
        )
    for _ in range(16):
        tracker.observe(
            chroma=_major_chroma(7),
            chord="G",
            chroma_confidence=1.0,
            chord_confidence=1.0,
        )
    assert tracker.hypotheses[0].tonic_pc == 7
