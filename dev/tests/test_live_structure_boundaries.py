from __future__ import annotations

from dreamsync.prediction.boundary import OnlineStructureTracker
from dreamsync.prediction.similarity import MultiFeatureSimilarityMemory
from dreamsync.prediction.structure_models import LiveBarFingerprint


def _bar(
    index: int,
    *,
    roots: tuple[int, ...] = (0, 2, 4, 5),
    timbre: float = 0.0,
    rhythm: tuple[float, ...] = (0.2, 0.8, 0.2, 0.8),
) -> LiveBarFingerprint:
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
        meter_confidence=0.9,
        completeness=1.0,
        beat_chroma=tuple(chromas),
        chroma_profile=tuple(
            sum(row[pitch] for row in chromas) / 4.0 for pitch in range(12)
        ),
        chroma_delta_shape=(1.0, 1.0, 1.0),
        chroma_confidence=0.9,
        mfcc_mean=tuple(timbre + (index * 0.01) for _ in range(13)),
        mfcc_std=(0.1,) * 13,
        band_profile=(0.7, 0.2, 0.1) if timbre else (0.2, 0.3, 0.5),
        band_flux_shape=rhythm,
        onset_shape=rhythm,
        energy_shape=(0.0, 0.0, 0.0, 0.0),
        bass_shape=(0.3, 0.3, 0.3, 0.3),
        centroid_shape=(0.0, 0.0, 0.0, 0.0),
    )


def _observe(
    tracker: OnlineStructureTracker,
    memory: MultiFeatureSimilarityMemory,
    bar: LiveBarFingerprint,
):
    row = memory.add(bar)
    sequence = tuple(
        match
        for horizon in (2, 4, 8, 12, 16)
        for match in memory.top_matches(horizon=horizon)
    )
    return tracker.observe(bar, similarities=row, sequence_matches=sequence)


def test_same_chord_timbre_transition_can_confirm_boundary_and_reset_position() -> None:
    tracker = OnlineStructureTracker(section_threshold=0.52)
    memory = MultiFeatureSimilarityMemory()
    _observe(tracker, memory, _bar(0))
    _observe(tracker, memory, _bar(1))
    snapshot = _observe(
        tracker,
        memory,
        _bar(2, timbre=7.0, rhythm=(1.0, 0.0, 1.0, 0.0)),
    )
    assert snapshot.observed_section_boundary
    assert snapshot.observed is not None
    assert snapshot.observed.evidence[1].contribution > 0.5
    assert snapshot.phrase_hypotheses[0].phrase_start_bar == 2


def test_chord_change_inside_stable_texture_is_not_automatically_section_boundary() -> None:
    tracker = OnlineStructureTracker(section_threshold=0.52)
    memory = MultiFeatureSimilarityMemory()
    _observe(tracker, memory, _bar(0))
    _observe(tracker, memory, _bar(1))
    snapshot = _observe(tracker, memory, _bar(2, roots=(1, 3, 5, 6)))
    assert not snapshot.observed_section_boundary
    assert snapshot.observed is not None
    assert snapshot.observed.section_probability < tracker.section_threshold


def test_duration_prior_is_not_presented_as_learned_recurrence() -> None:
    tracker = OnlineStructureTracker()
    memory = MultiFeatureSimilarityMemory()
    snapshot = _observe(tracker, memory, _bar(0))
    assert snapshot.phrase_hypotheses
    assert all(
        item.source == "duration_prior" for item in snapshot.phrase_hypotheses
    )


def test_first_phrase_target_does_not_drift_when_start_bar_is_zero() -> None:
    tracker = OnlineStructureTracker(
        phrase_threshold=1.1,
        section_threshold=1.1,
    )
    memory = MultiFeatureSimilarityMemory()

    first = _observe(tracker, memory, _bar(0))
    second = _observe(tracker, memory, _bar(1))
    third = _observe(tracker, memory, _bar(2))

    assert first.phrase_hypotheses[0].phrase_start_bar == 0
    assert second.phrase_hypotheses[0].phrase_start_bar == 0
    assert third.phrase_hypotheses[0].phrase_start_bar == 0
    assert second.phrase_hypotheses[0].current_position == 2
    assert third.phrase_hypotheses[0].current_position == 3
    assert first.upcoming[0].target_bar == 4
    assert second.upcoming[0].target_bar == 4
    assert third.upcoming[0].target_bar == 4
