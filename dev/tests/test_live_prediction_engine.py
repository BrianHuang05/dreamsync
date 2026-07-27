from __future__ import annotations

from dreamsync.prediction.engine import PredictiveStructureEngine
from dreamsync.prediction.models import LiveMusicalObservation


_TRIADS = {
    "C": (0, 4, 7),
    "Am": (9, 0, 4),
    "F": (5, 9, 0),
    "G": (7, 11, 2),
}


def _observation(
    index: int,
    chord: str,
    *,
    t: float | None = None,
) -> LiveMusicalObservation:
    chroma = [0.0] * 12
    for pitch_class in _TRIADS[chord]:
        chroma[pitch_class] = 1.0
    return LiveMusicalObservation(
        t=float(index * 0.5 if t is None else t),
        beat_index=index,
        bar_index=index,
        beat_in_bar=0,
        meter=(4, 4),
        meter_confidence=0.95,
        downbeat=True,
        absolute_chord=chord,
        chord_confidence=0.95,
        chord_change=True,
        chord_duration_beats=4.0,
        chroma=tuple(chroma),
        tonal_confidence=0.95,
        energy=0.5,
        energy_delta=0.0,
        onset_density=0.5,
        onset_density_delta=0.0,
        spectral_centroid=1200.0,
        centroid_delta=0.0,
        harmonic_rhythm=0.25,
        beat_period=0.5,
    )


def test_stable_c_major_vi_iv_v_predicts_tonic_resolution_at_downbeat() -> None:
    engine = PredictiveStructureEngine()
    # Establish the stated stable C-major context before the acceptance phrase.
    for index in range(8):
        engine.observe(_observation(index, "C"))
    engine.observe(_observation(8, "Am"))
    engine.observe(_observation(9, "F"))
    predictions = engine.observe(_observation(10, "G"))
    resolution = next(
        event
        for event in predictions
        if event.event_type == "harmonic_resolution"
    )
    assert resolution.target_function == "I"
    assert resolution.target_t == 7.0
    assert resolution.probability >= 0.75
    assert {item.value for item in resolution.alternatives} >= {
        "I",
        "vi",
        "unknown",
    }
    assert {item.source for item in resolution.evidence} >= {
        "dominant_resolution",
        "phrase_position",
        "meter",
        "key",
    }


def test_unknown_candidate_causes_abstention_not_unknown_target() -> None:
    engine = PredictiveStructureEngine()
    predictions = engine.observe(_observation(0, "Am"))
    assert all(event.target_function != "unknown" for event in predictions)
