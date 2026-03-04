"""Tests for dreamsync.compiler.transitions — Transition Planner."""

from dreamsync.analyzer.sections import Section
from dreamsync.analyzer.bpm import BeatGrid
from dreamsync.compiler.transitions import TransitionPlan, TransitionPlanner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_section(
    label: str = "verse",
    energy_mean: float = 0.5,
    mood: str = "groove",
    bpm: float = 120.0,
    start_t: float = 0.0,
    end_t: float = 30.0,
    section_id: str = "A",
) -> Section:
    return Section(
        start_t=start_t,
        end_t=end_t,
        label=label,
        energy_mean=energy_mean,
        mood=mood,
        bpm=bpm,
        section_id=section_id,
    )


def make_beat_grid(bpm: float = 120.0, duration: float = 240.0) -> BeatGrid:
    period = 60.0 / bpm
    beats = tuple(round(i * period, 4) for i in range(int(duration / period) + 1))
    downbeats = tuple(beats[i] for i in range(0, len(beats), 4))
    return BeatGrid(bpm=bpm, beat_times=beats, downbeat_times=downbeats, time_signature=4)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_first_section_always_cut():
    """Any section at index 0 gets cut with 0 beats."""
    planner = TransitionPlanner()
    grid = make_beat_grid()
    sections = (
        make_section(label="verse", start_t=0.0, end_t=30.0),
        make_section(label="chorus", start_t=30.0, end_t=60.0),
    )
    plans = planner.plan(sections, grid)
    assert plans[0] == TransitionPlan(section_index=0, transition="cut", transition_beats=0)


def test_drop_always_cut():
    """Incoming drop section always gets cut regardless of outgoing label."""
    planner = TransitionPlanner()
    grid = make_beat_grid()
    sections = (
        make_section(label="verse", start_t=0.0, end_t=30.0),
        make_section(label="drop", start_t=30.0, end_t=60.0),
    )
    plans = planner.plan(sections, grid)
    assert plans[1].transition == "cut"
    assert plans[1].transition_beats == 0


def test_outro_long_fade():
    """Incoming outro gets 8-beat fade."""
    planner = TransitionPlanner()
    grid = make_beat_grid()
    sections = (
        make_section(label="chorus", start_t=0.0, end_t=60.0),
        make_section(label="outro", start_t=60.0, end_t=120.0),
    )
    plans = planner.plan(sections, grid)
    assert plans[1].transition == "fade"
    assert plans[1].transition_beats == 8


def test_chorus_to_chorus_cut():
    """Chorus followed by chorus gets a hard cut."""
    planner = TransitionPlanner()
    grid = make_beat_grid()
    sections = (
        make_section(label="chorus", start_t=0.0, end_t=30.0),
        make_section(label="chorus", start_t=30.0, end_t=60.0),
    )
    plans = planner.plan(sections, grid)
    assert plans[1].transition == "cut"
    assert plans[1].transition_beats == 0


def test_verse_to_chorus_short_fade():
    """Verse -> chorus gets a 2-beat fade."""
    planner = TransitionPlanner()
    grid = make_beat_grid()
    sections = (
        make_section(label="verse", start_t=0.0, end_t=30.0),
        make_section(label="chorus", start_t=30.0, end_t=60.0),
    )
    plans = planner.plan(sections, grid)
    assert plans[1].transition == "fade"
    assert plans[1].transition_beats == 2


def test_chorus_to_verse_medium_fade():
    """Chorus -> verse gets a 4-beat fade."""
    planner = TransitionPlanner()
    grid = make_beat_grid()
    sections = (
        make_section(label="chorus", start_t=0.0, end_t=30.0),
        make_section(label="verse", start_t=30.0, end_t=60.0),
    )
    plans = planner.plan(sections, grid)
    assert plans[1].transition == "fade"
    assert plans[1].transition_beats == 4


def test_default_fallback():
    """Unknown label pair gets default_fade_beats."""
    planner = TransitionPlanner(default_fade_beats=6)
    grid = make_beat_grid()
    sections = (
        make_section(label="intro", start_t=0.0, end_t=30.0),
        make_section(label="breakdown", start_t=30.0, end_t=60.0),
    )
    plans = planner.plan(sections, grid)
    # intro -> breakdown doesn't match any rule, so fallback applies
    assert plans[1].transition == "fade"
    assert plans[1].transition_beats == 6


def test_max_fade_cap():
    """Custom max_fade_beats=2: a normally-8-beat outro transition is capped at 2."""
    planner = TransitionPlanner(max_fade_beats=2)
    grid = make_beat_grid()
    sections = (
        make_section(label="chorus", start_t=0.0, end_t=60.0),
        make_section(label="outro", start_t=60.0, end_t=120.0),
    )
    plans = planner.plan(sections, grid)
    assert plans[1].transition == "fade"
    assert plans[1].transition_beats == 2


def test_insufficient_beats_reduction():
    """Section with very few beats: transition_beats reduced to available count."""
    planner = TransitionPlanner()
    # Create a very short section (1 second at 120 BPM = 2 beats)
    grid = make_beat_grid(bpm=120.0, duration=240.0)
    sections = (
        make_section(label="chorus", start_t=0.0, end_t=100.0),
        make_section(label="outro", start_t=100.0, end_t=101.0),  # only ~2 beats
    )
    plans = planner.plan(sections, grid)
    # Outro normally gets 8 beats, but only 2 are available
    assert plans[1].transition == "fade"
    assert plans[1].transition_beats == 2


def test_single_section():
    """One section: returns [TransitionPlan(0, "cut", 0)]."""
    planner = TransitionPlanner()
    grid = make_beat_grid()
    sections = (make_section(label="verse"),)
    plans = planner.plan(sections, grid)
    assert plans == [TransitionPlan(section_index=0, transition="cut", transition_beats=0)]
