"""Tests for dreamsync.compiler.assemble — Timeline Assembler."""

from __future__ import annotations

import pytest

from dreamsync.analyzer.bpm import BeatGrid, TempoRegion
from dreamsync.analyzer.features import FeatureRow
from dreamsync.analyzer.instruments import InstrumentProxy
from dreamsync.analyzer.models import SongStructure
from dreamsync.analyzer.phrases import InstrumentEvent, Phrase
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


def test_field_mapping_preserves_spatial_params():
    """Generated spatial metadata should pass through to final cues unchanged."""
    sections = (make_section(start_t=0.0, end_t=30.0),)
    structure = make_structure(sections, duration=30.0)

    params = {
        "spatial_preset": "wave_top_to_bottom",
        "spatial_extent": {
            "min": {"x": -1.0, "y": -0.35, "z": -1.0},
            "max": {"x": 1.0, "y": 0.35, "z": 1.0},
        },
    }
    treats = [make_treatment(render_mode="scroll", params=params)]
    tl = TimelineAssembler().assemble(
        structure, [make_arc(0)], treats, [make_transition(0)]
    )
    assert tl.cues[0].params == params


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


def test_zero_bpm_falls_back_to_default():
    """Assembler should still build a runtime-valid timeline for zero-BPM analysis."""
    sections = (make_section(start_t=0.0, end_t=0.75, bpm=0.0),)
    structure = SongStructure(
        path="/tmp/short.mp3",
        duration=0.75,
        bpm=0.0,
        time_signature=4,
        beat_grid=BeatGrid(bpm=0.0, beat_times=(), downbeat_times=(), time_signature=4),
        tempo_regions=(TempoRegion(0.0, 0.75, 0.0, 0.0),),
        sections=sections,
        metadata={"track_name": "short"},
    )

    tl = TimelineAssembler().assemble(
        structure, [make_arc(0)], [make_treatment()], [make_transition(0)]
    )

    assert tl.bpm == 120.0
    assert len(tl.cues) == 1


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


# ---------------------------------------------------------------------------
# 9-14. Micro-cue tests (Issue 2, D3)
# ---------------------------------------------------------------------------

def _make_structure_with_phrases(
    sections: tuple[Section, ...],
    phrases: tuple[Phrase, ...],
    events: tuple[InstrumentEvent, ...] = (),
    proxies: tuple[InstrumentProxy, ...] = (),
    bpm: float = 120.0,
    duration: float = 120.0,
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
        metadata={},
        phrases=phrases,
        instrument_events=events,
        instrument_proxies=proxies,
    )


def test_micro_cues_inserted_at_phrase_boundaries():
    """Structure with 2 sections, 4 phrases each → 8+ cues (was 2)."""
    sections = (
        make_section(start_t=0.0, end_t=32.0, label="verse", section_id="A"),
        make_section(start_t=32.0, end_t=64.0, label="chorus", section_id="B"),
    )
    phrases = tuple([
        Phrase(0.0, 8.0, 0, "steady", 0.0, True),
        Phrase(8.0, 16.0, 0, "steady", 0.0, True),
        Phrase(16.0, 24.0, 0, "build", 0.2, True),
        Phrase(24.0, 32.0, 0, "steady", 0.0, True),
        Phrase(32.0, 40.0, 1, "steady", 0.0, True),
        Phrase(40.0, 48.0, 1, "steady", 0.0, True),
        Phrase(48.0, 56.0, 1, "drop", -0.1, True),
        Phrase(56.0, 64.0, 1, "breakdown", -0.2, False),
    ])
    structure = _make_structure_with_phrases(sections, phrases, duration=64.0)

    arcs = [make_arc(0, 0.5), make_arc(1, 0.8)]
    treats = [make_treatment(render_mode="scroll"), make_treatment(render_mode="pulse")]
    trans = [make_transition(0), make_transition(1, "fade", 2)]

    tl = TimelineAssembler().assemble(structure, arcs, treats, trans)

    # 2 section cues + 6 micro-cues (3 per section, skip first phrase each)
    assert len(tl.cues) == 8


def test_micro_cues_sorted_by_time():
    """Cue t values should be monotonically non-decreasing."""
    sections = (
        make_section(start_t=0.0, end_t=16.0, label="verse"),
        make_section(start_t=16.0, end_t=32.0, label="chorus"),
    )
    phrases = tuple([
        Phrase(0.0, 8.0, 0, "steady", 0.0, True),
        Phrase(8.0, 16.0, 0, "build", 0.2, True),
        Phrase(16.0, 24.0, 1, "steady", 0.0, True),
        Phrase(24.0, 32.0, 1, "drop", -0.1, True),
    ])
    structure = _make_structure_with_phrases(sections, phrases, duration=32.0)

    arcs = [make_arc(0, 0.5), make_arc(1, 0.8)]
    treats = [make_treatment(), make_treatment()]
    trans = [make_transition(0), make_transition(1)]

    tl = TimelineAssembler().assemble(structure, arcs, treats, trans)

    for i in range(1, len(tl.cues)):
        assert tl.cues[i].t >= tl.cues[i - 1].t


def test_kick_enter_micro_cue_boosts_intensity():
    """Phrase with kick_enter event → micro-cue intensity > section base."""
    sections = (make_section(start_t=0.0, end_t=16.0, label="verse"),)
    phrases = tuple([
        Phrase(0.0, 8.0, 0, "breakdown", 0.0, False),
        Phrase(8.0, 16.0, 0, "steady", 0.0, True),
    ])
    events = (InstrumentEvent(8.0, "kick_enter", 0.9),)
    structure = _make_structure_with_phrases(sections, phrases, events, duration=16.0)

    arcs = [make_arc(0, 0.5)]
    treats = [make_treatment(render_mode="breathe")]
    trans = [make_transition(0)]

    tl = TimelineAssembler().assemble(structure, arcs, treats, trans)

    # Find the micro-cue at t=8.0
    micro = [c for c in tl.cues if c.t == 8.0]
    assert len(micro) == 1
    assert micro[0].intensity > 0.5  # boosted above base
    assert micro[0].render_mode == "pulse"  # kick_enter → pulse


def test_backward_compat_no_phrases():
    """Structure with empty phrases → same output as before (1 cue per section)."""
    sections = (
        make_section(start_t=0.0, end_t=30.0, label="verse"),
        make_section(start_t=30.0, end_t=60.0, label="chorus"),
    )
    structure = make_structure(sections, duration=60.0)

    arcs = [make_arc(0, 0.5), make_arc(1, 0.8)]
    treats = [make_treatment(), make_treatment()]
    trans = [make_transition(0), make_transition(1)]

    tl = TimelineAssembler().assemble(structure, arcs, treats, trans)

    assert len(tl.cues) == 2


def test_micro_cue_inherits_section_palette():
    """Micro-cues use the same palette as their parent section cue."""
    sections = (make_section(start_t=0.0, end_t=16.0),)
    phrases = tuple([
        Phrase(0.0, 8.0, 0, "steady", 0.0, True),
        Phrase(8.0, 16.0, 0, "build", 0.2, True),
    ])
    structure = _make_structure_with_phrases(sections, phrases, duration=16.0)

    palette = ("#aabbcc", "#112233")
    treats = [make_treatment(color_palette=palette)]
    tl = TimelineAssembler().assemble(
        structure, [make_arc(0)], treats, [make_transition(0)]
    )

    for cue in tl.cues:
        assert cue.color_palette == palette


def test_breakdown_micro_cue_reduces_intensity():
    """Breakdown phrase → micro-cue intensity < section base."""
    sections = (make_section(start_t=0.0, end_t=16.0),)
    phrases = tuple([
        Phrase(0.0, 8.0, 0, "steady", 0.0, True),
        Phrase(8.0, 16.0, 0, "breakdown", -0.2, False),
    ])
    structure = _make_structure_with_phrases(sections, phrases, duration=16.0)

    arcs = [make_arc(0, 0.6)]
    treats = [make_treatment(render_mode="scroll")]
    trans = [make_transition(0)]

    tl = TimelineAssembler().assemble(structure, arcs, treats, trans)

    micro = [c for c in tl.cues if c.t == 8.0]
    assert len(micro) == 1
    assert micro[0].intensity < 0.6
    assert micro[0].render_mode == "breathe"


def test_bass_dominant_phrase_adds_eq_route_metadata():
    sections = (make_section(start_t=0.0, end_t=16.0),)
    phrases = tuple([
        Phrase(0.0, 8.0, 0, "steady", 0.0, True),
        Phrase(
            8.0, 16.0, 0, "steady", 0.0, True,
            band_ratios=(0.05, 0.02, 0.32, 0.08, 0.04, 0.03, 0.01),
            dominant_band="bass",
        ),
    ])
    structure = _make_structure_with_phrases(sections, phrases, duration=16.0)

    tl = TimelineAssembler().assemble(
        structure,
        [make_arc(0, 0.5)],
        [make_treatment(render_mode="scroll", color_palette=("#123456", "#abcdef"))],
        [make_transition(0)],
    )

    micro = [c for c in tl.cues if c.t == 8.0][0]
    assert micro.params["eq_routes"][0]["band"] == "bass"
    assert micro.params["eq_routes"][0]["when"] == "dominant"
    assert micro.params["spatial_preset"] == "flash_floor_only"
    assert micro.color_palette[0] == "#abcdef"
    assert micro.params["active_eq_routes"][0]["color_bias"] in micro.color_palette


def test_profile_eq_route_overrides_default_micro_cue_route():
    sections = (make_section(start_t=0.0, end_t=16.0),)
    phrases = tuple([
        Phrase(0.0, 8.0, 0, "steady", 0.0, True),
        Phrase(
            8.0, 16.0, 0, "steady", 0.0, True,
            band_ratios=(0.04, 0.03, 0.28, 0.08, 0.05, 0.02, 0.01),
            dominant_band="bass",
        ),
    ])
    structure = _make_structure_with_phrases(sections, phrases, duration=16.0)
    treatments = [make_treatment(params={
        "eq_routes": [
            {
                "band": "bass",
                "when": "dominant",
                "color_bias": "#00ffaa",
                "render_mode": "wave",
                "spatial_preset": "wave_front_to_back",
                "intensity_boost": 0.03,
            },
        ],
    })]

    tl = TimelineAssembler().assemble(
        structure,
        [make_arc(0, 0.5)],
        treatments,
        [make_transition(0)],
    )

    micro = [c for c in tl.cues if c.t == 8.0][0]
    assert micro.render_mode == "wave"
    assert micro.params["spatial_preset"] == "wave_front_to_back"
    assert micro.params["eq_routes"][0]["color_bias"] == "#00ff00"
    assert micro.color_palette[0] == "#00ff00"


def test_multiple_active_eq_routes_emit_runtime_eq_layers():
    sections = (make_section(start_t=0.0, end_t=16.0),)
    phrases = tuple([
        Phrase(0.0, 8.0, 0, "steady", 0.0, True),
        Phrase(
            8.0, 16.0, 0, "steady", 0.0, True,
            band_ratios=(0.03, 0.02, 0.28, 0.05, 0.04, 0.22, 0.01),
            dominant_band="bass",
        ),
    ])
    events = [InstrumentEvent(t=8.0, event_type="presence_lift", confidence=0.7, band="presence")]
    structure = _make_structure_with_phrases(sections, phrases, events=tuple(events), duration=16.0)

    tl = TimelineAssembler().assemble(
        structure,
        [make_arc(0, 0.5)],
        [make_treatment(render_mode="scroll", color_palette=("#123456", "#abcdef"))],
        [make_transition(0)],
    )

    micro = [c for c in tl.cues if c.t == 8.0][0]
    assert [layer["band"] for layer in micro.params["eq_layers"][:2]] == ["presence", "bass"]
    assert [layer["band"] for layer in micro.params["scene_layers"][:2]] == ["presence", "bass"]
    assert micro.params["eq_layers"][0]["spatial_preset"] == "flash_top_only"
    assert micro.params["eq_layers"][1]["spatial_preset"] == "flash_floor_only"
    assert len(micro.params["active_eq_routes"]) == 2


def test_instrument_dominant_phrase_adds_instrument_route_metadata():
    sections = (make_section(start_t=0.0, end_t=16.0),)
    phrases = tuple([
        Phrase(0.0, 8.0, 0, "steady", 0.0, True),
        Phrase(8.0, 16.0, 0, "steady", 0.0, True),
    ])
    proxies = (
        InstrumentProxy(0.0, 8.0, 0, "harmonic", harmonic=0.52),
        InstrumentProxy(8.0, 16.0, 0, "vocals", vocals=0.74, harmonic=0.38, pan_center=0.62, pan_width=0.31),
    )
    structure = _make_structure_with_phrases(
        sections,
        phrases,
        proxies=proxies,
        duration=16.0,
    )

    tl = TimelineAssembler().assemble(
        structure,
        [make_arc(0, 0.5)],
        [make_treatment(render_mode="scroll", color_palette=("#123456", "#abcdef"))],
        [make_transition(0)],
    )

    micro = [c for c in tl.cues if c.t == 8.0][0]
    assert micro.params["active_instrument_routes"][0]["instrument"] == "vocals"
    assert micro.params["active_instrument_routes"][0]["when"] == "dominant"
    assert micro.params["instrument_proxy"]["dominant_proxy"] == "vocals"
    assert micro.render_mode == "gradient"
    assert micro.params["spatial_preset"] == "blend_left_to_right"
    assert micro.color_palette[0] == "#abcdef"
    assert (
        micro.params["active_instrument_routes"][0]["color_bias"]
        in micro.color_palette
    )
    assert micro.params["active_instrument_routes"][0]["pan_follow"] == pytest.approx(0.72)
    assert micro.params["active_instrument_routes"][0]["width_scale"] == pytest.approx(1.35)
    assert micro.params["active_instrument_routes"][0]["spatial_origin"]["x"] > 0.4
    assert micro.params["active_instrument_routes"][0]["spatial_width"] > 0.55


def test_default_percussive_bias_stays_inside_selected_palette():
    sections = (make_section(start_t=0.0, end_t=16.0),)
    phrases = (
        Phrase(0.0, 8.0, 0, "steady", 0.0, True),
        Phrase(8.0, 16.0, 0, "steady", 0.0, True),
    )
    proxies = (
        InstrumentProxy(0.0, 8.0, 0, "harmonic", harmonic=0.52),
        InstrumentProxy(8.0, 16.0, 0, "percussive", percussive=0.74),
    )
    selected_palette = ("#cc003f", "#5b00b8", "#1d00af")
    structure = _make_structure_with_phrases(
        sections,
        phrases,
        proxies=proxies,
        duration=16.0,
    )

    timeline = TimelineAssembler().assemble(
        structure,
        [make_arc(0, 0.5)],
        [make_treatment(render_mode="scroll", color_palette=selected_palette)],
        [make_transition(0)],
    )

    micro = [cue for cue in timeline.cues if cue.t == 8.0][0]
    assert "#ffd07a" not in micro.color_palette
    assert (
        micro.params["active_instrument_routes"][0]["color_bias"]
        in selected_palette
    )
    assert micro.params["scene_layers"][0]["color_bias"] in selected_palette
    assert set(micro.color_palette) == set(selected_palette)


def test_profile_instrument_route_overrides_eq_route_on_micro_cue():
    sections = (make_section(start_t=0.0, end_t=16.0),)
    phrases = tuple([
        Phrase(
            0.0, 8.0, 0, "steady", 0.0, True,
            band_ratios=(0.05, 0.02, 0.26, 0.06, 0.04, 0.03, 0.01),
            dominant_band="bass",
        ),
        Phrase(
            8.0, 16.0, 0, "steady", 0.0, True,
            band_ratios=(0.05, 0.02, 0.26, 0.06, 0.04, 0.03, 0.01),
            dominant_band="bass",
        ),
    ])
    proxies = (
        InstrumentProxy(0.0, 8.0, 0, "harmonic", harmonic=0.48),
        InstrumentProxy(8.0, 16.0, 0, "vocals", vocals=0.82, pan_center=0.78, pan_width=0.34),
    )
    structure = _make_structure_with_phrases(
        sections,
        phrases,
        proxies=proxies,
        duration=16.0,
    )
    treatments = [make_treatment(params={
        "eq_routes": [
            {
                "band": "bass",
                "when": "dominant",
                "color_bias": "#00ffaa",
                "render_mode": "wave",
                "spatial_preset": "wave_front_to_back",
                "intensity_boost": 0.03,
            },
        ],
        "instrument_routes": [
            {
                "instrument": "vocals",
                "when": "dominant",
                "color_bias": "#ddeeff",
                "render_mode": "gradient",
                "spatial_preset": "blend_front_to_back",
                "effect_layer": {"layer_category": "slice", "effect_mode": "pulse"},
                "layer_category": "slice",
                "speed_units_per_second": 1.4,
                "falloff": "linear",
                "layer_priority": 6,
                "pan_follow": 0.7,
                "confidence_min": 0.5,
            },
        ],
    })]

    tl = TimelineAssembler().assemble(
        structure,
        [make_arc(0, 0.5)],
        treatments,
        [make_transition(0)],
    )

    micro = [c for c in tl.cues if c.t == 8.0][0]
    assert micro.render_mode == "gradient"
    assert micro.params["spatial_preset"] == "blend_front_to_back"
    assert micro.params["active_eq_routes"][0]["band"] == "bass"
    assert micro.params["active_instrument_routes"][0]["instrument"] == "vocals"
    assert micro.params["scene_layers"][0]["instrument"] == "vocals"
    assert micro.params["eq_layers"][0]["instrument"] == "vocals"
    assert micro.params["eq_layers"][1]["band"] == "bass"
    assert micro.params["scene_layers"][0]["effect_layer"]["layer_category"] == "slice"
    assert micro.params["scene_layers"][0]["layer_category"] == "slice"
    assert micro.params["scene_layers"][0]["speed_units_per_second"] == 1.4
    assert micro.params["scene_layers"][0]["layer_priority"] == 6
    assert micro.params["falloff"] == "linear"
    assert micro.color_palette[0] == "#00ff00"
    assert (
        micro.params["active_instrument_routes"][0]["color_bias"]
        in micro.color_palette
    )
    assert micro.params["active_instrument_routes"][0]["spatial_origin"]["x"] > 0.5
    assert micro.params["active_instrument_routes"][0]["spatial_width"] > 0.12


# ---------------------------------------------------------------------------
# 15-20. Fade-to-black and outro ramp tests (Issue 6)
# ---------------------------------------------------------------------------

def _make_feature(t: float, energy: float) -> FeatureRow:
    return FeatureRow(
        t=t, rms=0.0, zcr=0.0, centroid=0.0, bass_ratio=0.0,
        spectral_flux=0.0, kick_spectral_flux=0.0, onset_strength=0.0,
        energy=energy, bpm=120.0, beat=False, mood="groove",
    )


def test_fade_to_black_inserted_after_last_beat():
    """Song with 10s silence at end → fade cue inserted at last energetic beat."""
    sections = (make_section(start_t=0.0, end_t=120.0, label="verse"),)
    structure = make_structure(sections, duration=120.0, bpm=120.0)

    # Energy high up to t=110, then silent
    features = [_make_feature(t, 0.5) for t in range(0, 110)]
    features += [_make_feature(t, 0.01) for t in range(110, 121)]

    tl = TimelineAssembler().assemble(
        structure,
        [make_arc(0, 0.5)],
        [make_treatment()],
        [make_transition(0)],
        features=features,
    )

    # Should have section cue + fade-to-black cue
    assert len(tl.cues) == 2
    fade_cue = tl.cues[-1]
    assert fade_cue.intensity == 0.0
    assert fade_cue.transition == "fade"


def test_fade_to_black_intensity_is_zero():
    """The fade cue has intensity=0.0."""
    sections = (make_section(start_t=0.0, end_t=60.0, label="verse"),)
    structure = make_structure(sections, duration=60.0, bpm=120.0)

    features = [_make_feature(t, 0.5) for t in range(0, 50)]
    features += [_make_feature(t, 0.01) for t in range(50, 61)]

    tl = TimelineAssembler().assemble(
        structure,
        [make_arc(0, 0.5)],
        [make_treatment()],
        [make_transition(0)],
        features=features,
    )

    fade_cue = [c for c in tl.cues if c.intensity == 0.0]
    assert len(fade_cue) == 1
    assert fade_cue[0].render_mode == "solid"


def test_fade_to_black_transition_is_fade():
    """The fade cue has transition='fade' with transition_beats > 0."""
    sections = (make_section(start_t=0.0, end_t=60.0, label="verse"),)
    structure = make_structure(sections, duration=60.0, bpm=120.0)

    features = [_make_feature(t, 0.5) for t in range(0, 50)]
    features += [_make_feature(t, 0.01) for t in range(50, 61)]

    tl = TimelineAssembler().assemble(
        structure,
        [make_arc(0, 0.5)],
        [make_treatment()],
        [make_transition(0)],
        features=features,
    )

    fade_cue = [c for c in tl.cues if c.intensity == 0.0][0]
    assert fade_cue.transition == "fade"
    assert fade_cue.transition_beats > 0


def test_no_fade_cue_if_song_ends_abruptly():
    """Song where last beat is within 0.5s of duration → no fade cue."""
    sections = (make_section(start_t=0.0, end_t=30.0, label="verse"),)
    structure = make_structure(sections, duration=30.0, bpm=120.0)

    # Energy high right to the end
    features = [_make_feature(t * 0.5, 0.5) for t in range(0, 61)]

    tl = TimelineAssembler().assemble(
        structure,
        [make_arc(0, 0.5)],
        [make_treatment()],
        [make_transition(0)],
        features=features,
    )

    # Only section cue, no fade
    assert len(tl.cues) == 1
    assert tl.cues[0].intensity == 0.5


def test_fade_cue_sorted_correctly():
    """Fade cue appears after all section cues in timeline order."""
    sections = (
        make_section(start_t=0.0, end_t=50.0, label="verse"),
        make_section(start_t=50.0, end_t=100.0, label="chorus"),
    )
    structure = make_structure(sections, duration=100.0, bpm=120.0)

    features = [_make_feature(t, 0.5) for t in range(0, 90)]
    features += [_make_feature(t, 0.01) for t in range(90, 101)]

    tl = TimelineAssembler().assemble(
        structure,
        [make_arc(0, 0.5), make_arc(1, 0.8)],
        [make_treatment(), make_treatment()],
        [make_transition(0), make_transition(1)],
        features=features,
    )

    # Verify sorted
    for i in range(1, len(tl.cues)):
        assert tl.cues[i].t >= tl.cues[i - 1].t

    # Last cue should be the fade-to-black
    assert tl.cues[-1].intensity == 0.0


def test_fade_duration_capped_at_4s():
    """20s of silence after last beat → fade transition_beats reflects 4s cap."""
    sections = (make_section(start_t=0.0, end_t=120.0, label="verse"),)
    structure = make_structure(sections, duration=120.0, bpm=120.0)

    # Energy high up to t=95, then 25s of silence
    features = [_make_feature(t, 0.5) for t in range(0, 95)]
    features += [_make_feature(t, 0.01) for t in range(95, 121)]

    tl = TimelineAssembler().assemble(
        structure,
        [make_arc(0, 0.5)],
        [make_treatment()],
        [make_transition(0)],
        features=features,
    )

    fade_cue = [c for c in tl.cues if c.intensity == 0.0][0]
    # 4s at 120 BPM = 8 beats, capped at 8
    assert fade_cue.transition_beats <= 8


# ---------------------------------------------------------------------------
# 21-23. Outro intensity ramp tests (Issue 6, D2)
# ---------------------------------------------------------------------------

def test_outro_cue_has_intensity_start():
    """Assemble with outro section → outro cue has intensity_start from previous section."""
    sections = (
        make_section(start_t=0.0, end_t=60.0, label="verse", section_id="A"),
        make_section(start_t=60.0, end_t=90.0, label="outro", section_id="B"),
    )
    structure = make_structure(sections, duration=90.0)

    arcs = [make_arc(0, 0.7), make_arc(1, 0.10)]
    treats = [make_treatment(), make_treatment(render_mode="breathe")]
    trans = [make_transition(0), make_transition(1, "fade", 4)]

    tl = TimelineAssembler().assemble(structure, arcs, treats, trans)

    # Verse cue: no intensity_start
    assert tl.cues[0].intensity_start is None
    # Outro cue: intensity_start = previous section's intensity (0.7)
    outro_cue = [c for c in tl.cues if c.t == 60.0][0]
    assert outro_cue.intensity_start == pytest.approx(0.7)
    assert outro_cue.intensity == pytest.approx(0.10)


def test_no_ramp_on_non_outro():
    """Non-outro sections have intensity_start=None."""
    sections = (
        make_section(start_t=0.0, end_t=30.0, label="verse"),
        make_section(start_t=30.0, end_t=60.0, label="chorus"),
    )
    structure = make_structure(sections, duration=60.0)

    arcs = [make_arc(0, 0.5), make_arc(1, 0.8)]
    treats = [make_treatment(), make_treatment()]
    trans = [make_transition(0), make_transition(1)]

    tl = TimelineAssembler().assemble(structure, arcs, treats, trans)

    for cue in tl.cues:
        assert cue.intensity_start is None


def test_show_cue_roundtrip_with_intensity_start():
    """Serialize/deserialize ShowCue with intensity_start."""
    sections = (
        make_section(start_t=0.0, end_t=60.0, label="verse"),
        make_section(start_t=60.0, end_t=90.0, label="outro"),
    )
    structure = make_structure(sections, duration=90.0)

    arcs = [make_arc(0, 0.7), make_arc(1, 0.10)]
    treats = [make_treatment(), make_treatment()]
    trans = [make_transition(0), make_transition(1)]

    tl = TimelineAssembler().assemble(structure, arcs, treats, trans)

    d = tl.to_dict()
    tl2 = ShowTimeline.from_dict(d)

    for c1, c2 in zip(tl.cues, tl2.cues):
        assert c1.intensity_start == c2.intensity_start
