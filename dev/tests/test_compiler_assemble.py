"""Tests for dreamsync.compiler.assemble — Timeline Assembler."""

from __future__ import annotations

import pytest

from dreamsync.analyzer.bpm import BeatGrid, TempoRegion
from dreamsync.analyzer.models import SongStructure
from dreamsync.analyzer.sections import Section
from dreamsync.compiler.arc import ArcWeight
from dreamsync.compiler.assemble import TimelineAssembler
from dreamsync.compiler.treatments import Treatment
from dreamsync.compiler.transitions import TransitionPlan
from dreamsync.show.models import ShowTimeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_section(
    start_t: float = 0.0,
    end_t: float = 30.0,
    label: str = "verse",
    energy_mean: float = 0.5,
    mood: str = "groove",
    bpm: float = 120.0,
    section_id: str = "A",
) -> Section:
    return Section(
        start_t=start_t, end_t=end_t, label=label,
        energy_mean=energy_mean, mood=mood, bpm=bpm, section_id=section_id,
    )


def make_beat_grid(bpm: float = 120.0, duration: float = 120.0) -> BeatGrid:
    period = 60.0 / bpm
    beats = tuple(round(i * period, 4) for i in range(int(duration / period) + 1))
    downbeats = tuple(beats[i] for i in range(0, len(beats), 4))
    return BeatGrid(bpm=bpm, beat_times=beats, downbeat_times=downbeats, time_signature=4)


def make_structure(
    sections: tuple[Section, ...],
    bpm: float = 120.0,
    duration: float = 120.0,
    metadata: dict | None = None,
) -> SongStructure:
    bg = make_beat_grid(bpm=bpm, duration=duration)
    return SongStructure(
        path="/tmp/test.mp3",
        duration=duration,
        bpm=bpm,
        time_signature=4,
        beat_grid=bg,
        tempo_regions=(TempoRegion(0.0, duration, bpm, 1.0),),
        sections=sections,
        metadata=metadata or {},
    )


def make_arc(index: int, final_intensity: float = 0.5) -> ArcWeight:
    return ArcWeight(
        section_index=index,
        base_energy=0.5,
        arc_multiplier=1.0,
        final_intensity=final_intensity,
    )


def make_treatment(
    render_mode: str = "pulse",
    color_palette: tuple[str, ...] = ("#ff0000", "#00ff00"),
    params: dict | None = None,
    speed: float = 0.5,
    effect_name: str = "beat_pulse",
) -> Treatment:
    return Treatment(
        render_mode=render_mode,
        color_palette=color_palette,
        params=params or {},
        speed=speed,
        effect_name=effect_name,
    )


def make_transition(index: int, transition: str = "cut", beats: int = 0) -> TransitionPlan:
    return TransitionPlan(section_index=index, transition=transition, transition_beats=beats)


# ---------------------------------------------------------------------------
# 1. test_basic_assembly
# ---------------------------------------------------------------------------
def test_basic_assembly():
    """3-section song: verify ShowTimeline has 3 cues with correct field mapping."""
    sections = (
        make_section(start_t=0.0, end_t=30.0, label="verse", section_id="A"),
        make_section(start_t=30.0, end_t=60.0, label="chorus", section_id="B"),
        make_section(start_t=60.0, end_t=90.0, label="verse", section_id="A"),
    )
    structure = make_structure(sections, duration=90.0)

    arcs = [make_arc(0, 0.4), make_arc(1, 0.8), make_arc(2, 0.5)]
    treats = [
        make_treatment(render_mode="breathe", speed=0.3, effect_name="slow_breathe"),
        make_treatment(render_mode="pulse", speed=0.7, effect_name="beat_pulse"),
        make_treatment(render_mode="scroll", speed=0.5, effect_name="color_scroll"),
    ]
    trans = [make_transition(0, "cut", 0), make_transition(1, "fade", 2), make_transition(2, "fade", 4)]

    assembler = TimelineAssembler()
    tl = assembler.assemble(structure, arcs, treats, trans)

    assert isinstance(tl, ShowTimeline)
    assert len(tl.cues) == 3
    assert tl.cues[0].t == 0.0
    assert tl.cues[1].t == 30.0
    assert tl.cues[2].t == 60.0
    assert tl.cues[0].render_mode == "breathe"
    assert tl.cues[1].render_mode == "pulse"
    assert tl.cues[2].render_mode == "scroll"
    assert tl.cues[0].intensity == 0.4
    assert tl.cues[1].intensity == 0.8
    assert tl.cues[1].transition == "fade"
    assert tl.cues[1].transition_beats == 2


# ---------------------------------------------------------------------------
# 2. test_field_mapping_intensity
# ---------------------------------------------------------------------------
def test_field_mapping_intensity():
    """Verify cue intensity comes from ArcWeight.final_intensity, not Section.energy_mean."""
    sections = (
        make_section(start_t=0.0, end_t=30.0, energy_mean=0.9),
    )
    structure = make_structure(sections, duration=30.0)

    # ArcWeight gives 0.35, section energy_mean is 0.9
    arcs = [make_arc(0, final_intensity=0.35)]
    treats = [make_treatment()]
    trans = [make_transition(0)]

    tl = TimelineAssembler().assemble(structure, arcs, treats, trans)
    assert tl.cues[0].intensity == 0.35
    assert tl.cues[0].intensity != 0.9


# ---------------------------------------------------------------------------
# 3. test_field_mapping_render_mode
# ---------------------------------------------------------------------------
def test_field_mapping_render_mode():
    """Verify cue render_mode comes from Treatment.render_mode."""
    sections = (make_section(start_t=0.0, end_t=30.0),)
    structure = make_structure(sections, duration=30.0)

    treats = [make_treatment(render_mode="gradient")]
    tl = TimelineAssembler().assemble(
        structure, [make_arc(0)], treats, [make_transition(0)]
    )
    assert tl.cues[0].render_mode == "gradient"


# ---------------------------------------------------------------------------
# 4. test_beat_grid_copied
# ---------------------------------------------------------------------------
def test_beat_grid_copied():
    """Verify beat_times and downbeat_times match structure.beat_grid."""
    sections = (make_section(start_t=0.0, end_t=30.0),)
    structure = make_structure(sections, duration=30.0, bpm=120.0)

    tl = TimelineAssembler().assemble(
        structure, [make_arc(0)], [make_treatment()], [make_transition(0)]
    )
    assert tl.beat_times == structure.beat_grid.beat_times
    assert tl.downbeat_times == structure.beat_grid.downbeat_times
    assert tl.bpm == structure.bpm


# ---------------------------------------------------------------------------
# 5. test_metadata_preserved
# ---------------------------------------------------------------------------
def test_metadata_preserved():
    """Verify metadata dict from SongStructure appears in ShowTimeline."""
    meta = {"track_name": "Test Song", "artist": "DJ Test"}
    sections = (make_section(start_t=0.0, end_t=30.0),)
    structure = make_structure(sections, duration=30.0, metadata=meta)

    tl = TimelineAssembler().assemble(
        structure, [make_arc(0)], [make_treatment()], [make_transition(0)]
    )
    assert tl.metadata == meta
    assert tl.metadata["track_name"] == "Test Song"
    assert tl.metadata["artist"] == "DJ Test"


# ---------------------------------------------------------------------------
# 6. test_mismatched_lengths_error
# ---------------------------------------------------------------------------
def test_mismatched_lengths_error():
    """Pass lists of different lengths: verify ValueError with informative message."""
    sections = (
        make_section(start_t=0.0, end_t=30.0),
        make_section(start_t=30.0, end_t=60.0),
    )
    structure = make_structure(sections, duration=60.0)

    # Only 1 arc, but 2 sections
    with pytest.raises(ValueError, match="Input length mismatch"):
        TimelineAssembler().assemble(
            structure,
            [make_arc(0)],
            [make_treatment(), make_treatment()],
            [make_transition(0), make_transition(1)],
        )

    # Only 1 treatment, but 2 sections
    with pytest.raises(ValueError, match="Input length mismatch"):
        TimelineAssembler().assemble(
            structure,
            [make_arc(0), make_arc(1)],
            [make_treatment()],
            [make_transition(0), make_transition(1)],
        )


# ---------------------------------------------------------------------------
# 7. test_json_round_trip
# ---------------------------------------------------------------------------
def test_json_round_trip():
    """Assemble timeline, serialize to dict, deserialize: verify equivalence."""
    sections = (
        make_section(start_t=0.0, end_t=30.0, label="verse"),
        make_section(start_t=30.0, end_t=60.0, label="chorus"),
    )
    structure = make_structure(sections, duration=60.0, metadata={"artist": "Test"})

    arcs = [make_arc(0, 0.4), make_arc(1, 0.8)]
    treats = [
        make_treatment(render_mode="breathe", color_palette=("#aabb00", "#00ccdd"), speed=0.3),
        make_treatment(render_mode="pulse", color_palette=("#ff0000",), speed=0.7),
    ]
    trans = [make_transition(0, "cut", 0), make_transition(1, "fade", 2)]

    tl = TimelineAssembler().assemble(structure, arcs, treats, trans)

    # Round-trip
    d = tl.to_dict()
    tl2 = ShowTimeline.from_dict(d)

    assert tl2.song_path == tl.song_path
    assert tl2.duration == tl.duration
    assert tl2.bpm == tl.bpm
    assert tl2.time_signature == tl.time_signature
    assert len(tl2.cues) == len(tl.cues)
    assert tl2.metadata == tl.metadata

    for c1, c2 in zip(tl.cues, tl2.cues):
        assert c2.t == pytest.approx(c1.t, abs=0.001)
        assert c2.render_mode == c1.render_mode
        assert c2.intensity == pytest.approx(c1.intensity, abs=0.001)
        assert c2.speed == pytest.approx(c1.speed, abs=0.001)
        assert c2.transition == c1.transition
        assert c2.transition_beats == c1.transition_beats


# ---------------------------------------------------------------------------
# 8. test_single_section
# ---------------------------------------------------------------------------
def test_single_section():
    """Song with one section: produces valid timeline with one cue."""
    sections = (make_section(start_t=0.0, end_t=30.0),)
    structure = make_structure(sections, duration=30.0)

    tl = TimelineAssembler().assemble(
        structure,
        [make_arc(0, 0.6)],
        [make_treatment(render_mode="solid", speed=0.3)],
        [make_transition(0, "cut", 0)],
    )

    assert isinstance(tl, ShowTimeline)
    assert len(tl.cues) == 1
    assert tl.cues[0].t == 0.0
    assert tl.cues[0].render_mode == "solid"
    assert tl.cues[0].intensity == 0.6
    assert tl.cues[0].speed == 0.3
    assert tl.cues[0].transition == "cut"
    assert tl.cues[0].transition_beats == 0
    assert tl.song_path == "/tmp/test.mp3"
    assert tl.duration == 30.0
