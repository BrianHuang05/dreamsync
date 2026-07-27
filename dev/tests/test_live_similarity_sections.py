from __future__ import annotations

from dreamsync.prediction.section import StructureSectionMemory
from dreamsync.prediction.structure_models import LiveBarFingerprint


def _bar(
    index: int,
    *,
    roots: tuple[int, ...],
    timbre: float,
) -> LiveBarFingerprint:
    chromas = []
    for root in roots:
        row = [0.0] * 12
        row[root % 12] = 1.0
        chromas.append(tuple(row))
    return LiveBarFingerprint(
        bar_index=index,
        start_t=float(index),
        end_t=float(index + 1),
        beats=4,
        meter=(4, 4),
        meter_confidence=0.9,
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
        band_flux_shape=(0.2, 0.8, 0.2, 0.8),
        onset_shape=(0.2, 0.8, 0.2, 0.8),
        energy_shape=(-1.0, -0.2, 0.4, 0.8),
        bass_shape=(0.2, 0.4, 0.3, 0.5),
        centroid_shape=(-0.2, 0.0, 0.1, 0.3),
    )


def _section(
    start: int,
    *,
    timbre: float,
    transpose: int = 0,
) -> tuple[LiveBarFingerprint, ...]:
    return tuple(
        _bar(
            start + offset,
            roots=tuple((root + transpose) % 12 for root in (0, 2, 4, 5)),
            timbre=timbre,
        )
        for offset in range(2)
    )


def test_anonymous_abab_order_and_successor_are_learned_without_labels() -> None:
    memory = StructureSectionMemory()
    assert memory.observe_completed(_section(0, timbre=0.0)) == "A"
    assert memory.observe_completed(_section(2, timbre=6.0)) == "B"
    assert memory.observe_completed(_section(4, timbre=0.0)) == "A"
    assert memory.observe_completed(_section(6, timbre=6.0)) == "B"
    assert memory.order == ("A", "B", "A", "B")
    successors = memory.predict_successor("A")
    assert successors and successors[0].section_id == "B"
    assert successors[0].probability == 1.0


def test_transposed_and_energy_equivalent_a_matches_existing_identity() -> None:
    memory = StructureSectionMemory()
    assert memory.observe_completed(_section(0, timbre=0.0)) == "A"
    transposed = _section(2, timbre=0.0, transpose=5)
    assert memory.observe_completed(transposed) == "A"
    hypothesis = memory.match_prefix((transposed[0],), start_bar=8)[0]
    assert hypothesis.section_id == "A"
    assert hypothesis.expected_end_bar == 10
    assert hypothesis.recurrence_count == 2
