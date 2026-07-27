"""Tests for evidence-based live meter inference."""

from __future__ import annotations

from dreamsync.dsp.meter import (
    LiveMeterState,
    LiveMeterTracker,
    ManualBeatRegistration,
)
from dreamsync.live import _meter_beat_accent


def _observe_pattern(
    tracker: LiveMeterTracker,
    *,
    beats: int,
    accented_phase: int | None,
    bpm: float = 120.0,
) -> list:
    states = []
    for index in range(beats):
        accented = accented_phase is not None and index % 4 == accented_phase
        states.append(
            tracker.observe_beat(
                t=index * (60.0 / bpm),
                bpm=bpm,
                low_frequency=8.0 if accented else 1.0,
                onset_strength=4.0 if accented else 0.5,
                energy=0.8 if accented else 0.2,
            )
        )
    return states


def test_first_observed_beat_is_not_declared_downbeat() -> None:
    state = _observe_pattern(
        LiveMeterTracker(),
        beats=1,
        accented_phase=0,
    )[0]

    assert state.beat is True
    assert state.downbeat is False
    assert state.bar_phase is None
    assert state.meter_confident is False
    assert state.relative_phase == 0


def test_recurring_accent_converges_on_correct_four_four_phase() -> None:
    states = _observe_pattern(
        LiveMeterTracker(min_confidence=0.12),
        beats=24,
        accented_phase=2,
    )
    confident = [state for state in states if state.meter_confident]

    assert confident
    assert states[-1].meter_confident is True
    for index, state in enumerate(states[-8:], start=16):
        assert state.bar_phase == (index - 2) % 4
        assert state.downbeat is (index % 4 == 2)


def test_isolated_offbeat_accent_does_not_flip_established_phase() -> None:
    tracker = LiveMeterTracker(min_confidence=0.12)
    states = _observe_pattern(tracker, beats=24, accented_phase=0)
    assert states[-1].meter_confident

    tracker.observe_beat(
        t=12.0,
        bpm=120.0,
        low_frequency=8.0,
        onset_strength=4.0,
        energy=0.8,
    )
    state = tracker.observe_beat(
        t=12.5,
        bpm=120.0,
        low_frequency=20.0,
        onset_strength=10.0,
        energy=1.0,
    )

    assert state.meter_confident is True
    assert state.bar_phase == 1
    assert state.downbeat is False


def test_equal_accents_remain_ambiguous() -> None:
    tracker = LiveMeterTracker()
    states = []
    for index in range(24):
        states.append(
            tracker.observe_beat(
                t=index * 0.5,
                bpm=120.0,
                low_frequency=1.0,
                onset_strength=1.0,
                energy=0.2,
            )
        )

    assert states[-1].meter_confident is False
    assert states[-1].bar_phase is None
    assert not any(state.downbeat for state in states)


def test_missed_beat_advances_phase_and_lowers_confidence() -> None:
    tracker = LiveMeterTracker(min_confidence=0.12)
    states = _observe_pattern(tracker, beats=24, accented_phase=0)
    prior_confidence = states[-1].phase_confidence

    # The next observation arrives two beat periods later.
    state = tracker.observe_beat(
        t=13.0,
        bpm=120.0,
        low_frequency=1.0,
        onset_strength=0.5,
        energy=0.2,
    )

    assert state.inferred_missing_beats == 2
    assert state.relative_phase == 2
    assert state.phase_confidence <= prior_confidence


def test_reset_removes_phase_confidence() -> None:
    tracker = LiveMeterTracker(min_confidence=0.12)
    _observe_pattern(tracker, beats=24, accented_phase=0)
    tracker.reset()

    assert tracker.state.meter_confident is False
    assert tracker.state.bar_phase is None


def test_manual_nudge_makes_current_beat_persistent_downbeat_anchor() -> None:
    tracker = LiveMeterTracker(min_confidence=0.12)
    states = _observe_pattern(tracker, beats=24, accented_phase=0)
    assert states[-1].bar_phase == 3

    nudged = tracker.nudge_downbeat()

    assert nudged.downbeat is True
    assert nudged.bar_phase == 0
    assert nudged.meter_confident is True
    assert nudged.phase_confidence == 1.0

    following = []
    for offset in range(1, 5):
        following.append(
            tracker.observe_beat(
                t=(23 + offset) * 0.5,
                bpm=120.0,
                low_frequency=8.0 if offset == 1 else 1.0,
                onset_strength=4.0 if offset == 1 else 0.5,
                energy=0.8 if offset == 1 else 0.2,
            )
        )

    assert [state.bar_phase for state in following] == [1, 2, 3, 0]
    assert [state.downbeat for state in following] == [
        False,
        False,
        False,
        True,
    ]


def test_reset_clears_manual_downbeat_anchor() -> None:
    tracker = LiveMeterTracker(min_confidence=0.12)
    _observe_pattern(tracker, beats=8, accented_phase=0)
    tracker.nudge_downbeat()

    tracker.reset()

    state = tracker.observe_beat(
        t=10.0,
        bpm=120.0,
        low_frequency=1.0,
        onset_strength=1.0,
        energy=0.2,
    )
    assert state.meter_confident is False
    assert state.downbeat is False


def test_d_s_s_s_d_registration_infers_four_four_and_keeps_markers():
    registration = ManualBeatRegistration(beats_per_bar=3)

    assert registration.register(t=0.0, kind="downbeat", beat_index=10) is None
    assert registration.register(t=0.5, kind="beat", beat_index=12) is None
    assert registration.register(t=1.0, kind="beat", beat_index=15) is None
    assert registration.register(t=1.5, kind="beat", beat_index=20) is None
    assert registration.register(t=2.0, kind="downbeat", beat_index=25) == 4

    assert registration.beats_per_bar == 4
    assert [marker.kind for marker in registration.markers] == [
        "downbeat",
        "beat",
        "beat",
        "beat",
        "downbeat",
    ]
    assert [marker.t for marker in registration.markers] == [
        0.0,
        0.5,
        1.0,
        1.5,
        2.0,
    ]


def test_manual_meter_change_keeps_current_beat_as_downbeat():
    tracker = LiveMeterTracker(beats_per_bar=3)
    _observe_pattern(tracker, beats=5, accented_phase=None)

    state = tracker.set_beats_per_bar(4)

    assert tracker.beats_per_bar == 4
    assert state.downbeat is True
    assert state.bar_phase == 0
    following = tracker.observe_beat(
        t=2.5,
        bpm=120.0,
        low_frequency=1.0,
        onset_strength=1.0,
        energy=0.2,
    )
    assert following.bar_phase == 1


def test_duplicate_manual_beat_on_same_detection_does_not_expand_meter():
    registration = ManualBeatRegistration()
    registration.register(t=0.0, kind="downbeat", beat_index=40)
    registration.register(t=0.5, kind="beat", beat_index=41)
    registration.register(t=0.5, kind="beat", beat_index=41)
    registration.register(t=1.0, kind="beat", beat_index=42)

    assert (
        registration.register(
            t=1.5,
            kind="downbeat",
            beat_index=43,
        )
        == 3
    )


def test_manual_registration_reset_clears_markers_and_meter_history():
    registration = ManualBeatRegistration(beats_per_bar=4)
    registration.register(t=0.0, kind="downbeat", beat_index=1)
    registration.register(t=0.5, kind="beat", beat_index=2)

    registration.reset(beats_per_bar=3)

    assert registration.markers == ()
    assert registration.count == 0
    assert registration.beats_per_bar == 3
    assert registration.register(
        t=1.0,
        kind="downbeat",
        beat_index=10,
    ) is None


def test_meter_accent_abstains_when_phase_is_uncertain() -> None:
    accent = _meter_beat_accent(
        True,
        LiveMeterState(
            beat=True,
            downbeat=False,
            bar_phase=None,
            meter_confident=False,
        ),
    )

    assert accent.beat is True
    assert accent.downbeat is False
    assert accent.beat_in_bar is None
    assert accent.strength == 0.35


def test_meter_accent_exposes_confident_downbeat() -> None:
    accent = _meter_beat_accent(
        True,
        LiveMeterState(
            beat=True,
            downbeat=True,
            bar_phase=0,
            meter_confident=True,
        ),
    )

    assert accent.downbeat is True
    assert accent.beat_in_bar == 1
    assert accent.strength == 1.0
