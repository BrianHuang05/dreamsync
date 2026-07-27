from __future__ import annotations

from dreamsync.prediction.boundary import StructurePredictionEngine
from dreamsync.prediction.similarity import MultiFeatureSimilarityMemory
from dreamsync.prediction.structure_models import LiveBarFingerprint


def _bar(
    index: int,
    *,
    timbre: float,
    rhythm: tuple[float, ...],
) -> LiveBarFingerprint:
    roots = (0, 2, 4, 5)
    chromas = []
    for root in roots:
        row = [0.0] * 12
        row[root] = 1.0
        chromas.append(tuple(row))
    return LiveBarFingerprint(
        bar_index=index,
        start_t=index * 2.0,
        end_t=(index + 1) * 2.0,
        beats=4,
        meter=(4, 4),
        meter_confidence=0.95,
        completeness=1.0,
        beat_chroma=tuple(chromas),
        chroma_profile=tuple(
            sum(row[pitch] for row in chromas) / 4.0 for pitch in range(12)
        ),
        chroma_delta_shape=(1.0, 1.0, 1.0),
        chroma_confidence=0.9,
        mfcc_mean=(timbre,) * 13,
        mfcc_std=(0.1,) * 13,
        band_profile=(0.2, 0.3, 0.5) if timbre < 3.0 else (0.7, 0.2, 0.1),
        band_flux_shape=rhythm,
        onset_shape=rhythm,
        energy_shape=(0.0, 0.0, 0.0, 0.0),
        bass_shape=(0.3, 0.3, 0.3, 0.3),
        centroid_shape=(0.0, 0.0, 0.0, 0.0),
    )


def test_similarity_recurrence_produces_stable_future_bar_target() -> None:
    engine = StructurePredictionEngine(section_event_threshold=0.50)
    memory = MultiFeatureSimilarityMemory()
    emitted = []
    sequence = (
        (0.0, (0.2, 0.8, 0.2, 0.8)),
        (0.0, (0.2, 0.8, 0.2, 0.8)),
        (6.0, (1.0, 0.0, 1.0, 0.0)),
        (6.0, (1.0, 0.0, 1.0, 0.0)),
        (0.0, (0.2, 0.8, 0.2, 0.8)),
    )
    for index, (timbre, rhythm) in enumerate(sequence):
        bar = _bar(index, timbre=timbre, rhythm=rhythm)
        row = memory.add(bar)
        sequences = tuple(
            match
            for horizon in (2, 4)
            for match in memory.top_matches(horizon=horizon)
        )
        events = engine.observe(
            bar,
            similarities=row,
            sequence_matches=sequences,
            current_beat_index=(index + 1) * 4,
            current_bar_index=index + 1,
            downbeat_t=(index + 1) * 2.0,
            beat_period=0.5,
        )
        emitted.extend(events)
    structural = [
        event
        for event in emitted
        if event.event_type in {"section_repeat", "section_transition"}
    ]
    assert structural
    prediction = structural[-1]
    assert prediction.target is not None
    assert prediction.target.target_bar_index == 6
    assert prediction.target.target_beat_index == 24
    assert prediction.target.target_t == 12.0
    assert not prediction.cold_start
    assert prediction.matched_earlier_bar is not None
