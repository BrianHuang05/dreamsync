"""Tests for confidence-gated four-bar live structure inference."""

from __future__ import annotations

from dreamsync.dsp.harmonic import LiveHarmonicState
from dreamsync.dsp.meter import LiveMeterState
from dreamsync.dsp.structure import LiveStructureTracker
from dreamsync.director import Director
from dreamsync.effects import EffectCycler, EffectCyclerConfig
from dreamsync.mood import Mood


_C = (1.0,) + (0.0,) * 11
_F = (0.0,) * 5 + (1.0,) + (0.0,) * 6


def _harmonic(
    t: float,
    *,
    chroma: tuple[float, ...] = _C,
    chord: str = "C",
    change: bool = False,
    novelty: float = 0.0,
) -> LiveHarmonicState:
    return LiveHarmonicState(
        t=t,
        chroma=chroma,
        tonal_confidence=0.85,
        chord=chord,
        chord_confidence=0.85,
        novelty=novelty,
        novelty_threshold=0.18,
        harmonic_change=change,
    )


def _meter(t: float, beat_index: int, *, confident: bool = True) -> LiveMeterState:
    phase = beat_index % 4
    return LiveMeterState(
        t=t,
        beat=True,
        downbeat=bool(confident and phase == 0),
        bar_phase=phase if confident else None,
        relative_phase=phase,
        phase_confidence=0.8 if confident else 0.05,
        meter_confident=confident,
        evidence=0.8,
    )


def _feed_beats(
    tracker: LiveStructureTracker,
    count: int,
    *,
    confident: bool = True,
) -> float:
    last_t = 0.0
    for beat_index in range(count):
        last_t = beat_index * 0.5
        tracker.observe_harmonic(
            _harmonic(max(0.0, last_t - 0.02)),
            energy=0.2,
            centroid=900.0,
            onset=0.2,
        )
        tracker.observe_beat(
            _meter(last_t, beat_index, confident=confident),
            energy=0.2,
            centroid=900.0,
            onset=0.2,
        )
    return last_t


def test_four_bar_shift_emits_candidate_then_one_macro_change() -> None:
    tracker = LiveStructureTracker()
    boundary_t = _feed_beats(tracker, 17)
    assert tracker.completed_bars == 4

    candidate_events = tracker.observe_harmonic(
        _harmonic(
            boundary_t + 0.05,
            chroma=_F,
            chord="F",
            change=True,
            novelty=0.8,
        ),
        energy=0.8,
        centroid=1700.0,
        onset=0.7,
    )
    confirmed_events = tracker.observe_harmonic(
        _harmonic(boundary_t + 0.19, chroma=_F, chord="F"),
        energy=0.8,
        centroid=1700.0,
        onset=0.7,
    )
    sustained_events = tracker.observe_harmonic(
        _harmonic(boundary_t + 0.25, chroma=_F, chord="F"),
        energy=0.8,
        centroid=1700.0,
        onset=0.7,
    )

    assert [event.kind for event in candidate_events] == [
        "harmonic_change",
        "macro_candidate",
    ]
    assert [event.kind for event in confirmed_events] == ["macro_change"]
    assert sustained_events == ()


def test_ordinary_one_bar_chord_change_is_not_macro() -> None:
    tracker = LiveStructureTracker()
    boundary_t = _feed_beats(tracker, 5)

    events = tracker.observe_harmonic(
        _harmonic(
            boundary_t + 0.05,
            chroma=_F,
            chord="F",
            change=True,
            novelty=0.9,
        ),
        energy=0.8,
        centroid=1700.0,
        onset=0.8,
    )

    assert [event.kind for event in events] == ["harmonic_change"]


def test_harmonic_change_far_from_downbeat_is_not_macro() -> None:
    tracker = LiveStructureTracker()
    boundary_t = _feed_beats(tracker, 17)

    events = tracker.observe_harmonic(
        _harmonic(
            boundary_t + 0.6,
            chroma=_F,
            chord="F",
            change=True,
            novelty=0.9,
        ),
        energy=0.8,
        centroid=1700.0,
        onset=0.8,
    )

    assert [event.kind for event in events] == ["harmonic_change"]


def test_low_meter_confidence_suppresses_phrase_claims() -> None:
    tracker = LiveStructureTracker()
    boundary_t = _feed_beats(tracker, 17, confident=False)

    events = tracker.observe_harmonic(
        _harmonic(
            boundary_t + 0.05,
            chroma=_F,
            chord="F",
            change=True,
            novelty=0.9,
        ),
        energy=0.8,
        centroid=1700.0,
        onset=0.8,
    )

    assert [event.kind for event in events] == ["harmonic_change"]
    assert tracker.completed_bars == 0


def test_song_reset_clears_phrase_and_candidate_state() -> None:
    tracker = LiveStructureTracker()
    _feed_beats(tracker, 17)
    tracker.reset()

    assert tracker.completed_bars == 0
    assert tracker.last_event is None


def test_structure_controlled_director_changes_color_only_on_macro() -> None:
    director = Director()
    director.set_colors(("#110000", "#001100", "#000011"))
    common = {
        "t": 1.0,
        "rms": 0.1,
        "zcr": 0.1,
        "bpm": 120.0,
        "beat": True,
        "downbeat": True,
        "structure_controlled": True,
    }

    downbeat_intent = director.update({**common, "structure_event": ""})
    macro_intent = director.update({
        **common,
        "t": 1.01,
        "structure_event": "macro_change",
    })

    assert downbeat_intent.color == "#110000"
    assert macro_intent.color == "#001100"
    assert director.last_beat_event


def test_structure_controlled_effect_cycler_waits_for_macro() -> None:
    cycler = EffectCycler(EffectCyclerConfig(cycle_interval=1.0), seed=7)
    cycler.update(Mood.CHILL, 0.0, False, 120.0, 0.2)
    initial_effect = cycler.current_effect

    cycler.update(
        Mood.CHILL,
        10.0,
        False,
        120.0,
        0.2,
        structure_controlled=True,
    )
    assert cycler.current_effect == initial_effect

    cycler.update(
        Mood.CHILL,
        10.1,
        False,
        120.0,
        0.2,
        structure_event="macro_change",
        structure_controlled=True,
    )
    assert cycler.current_effect != initial_effect
