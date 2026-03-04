"""Tests for dreamsync.compiler.compile — compile_show() orchestrator + CLI + format_summary."""

from __future__ import annotations

import pytest

from dreamsync.analyzer.bpm import BeatGrid, TempoRegion
from dreamsync.analyzer.models import SongStructure
from dreamsync.analyzer.sections import Section
from dreamsync.compiler.compile import compile_show, format_summary
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


def _typical_structure() -> SongStructure:
    """A typical song structure with intro, verse, chorus, bridge, chorus, outro."""
    sections = (
        make_section(0, 18, "intro", 0.2, "chill", 128, "A"),
        make_section(18, 52, "verse", 0.4, "groove", 128, "B"),
        make_section(52, 84, "chorus", 0.7, "hype", 128, "C"),
        make_section(84, 100, "bridge", 0.35, "chill", 128, "D"),
        make_section(100, 132, "chorus", 0.75, "hype", 128, "C"),
        make_section(132, 160, "outro", 0.15, "chill", 128, "E"),
    )
    return make_structure(sections, bpm=128.0, duration=160.0)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFullPipelineNoProfile:
    """Test 1: compile_show() with no profile produces a valid ShowTimeline."""

    def test_produces_valid_timeline(self):
        structure = _typical_structure()
        timeline = compile_show(structure)

        assert isinstance(timeline, ShowTimeline)
        assert len(timeline.cues) == len(structure.sections)
        assert timeline.bpm == structure.bpm
        assert timeline.duration == structure.duration
        assert timeline.song_path == structure.path

    def test_cues_ordered_by_time(self):
        structure = _typical_structure()
        timeline = compile_show(structure)

        times = [c.t for c in timeline.cues]
        assert times == sorted(times)

    def test_cue_fields_populated(self):
        structure = _typical_structure()
        timeline = compile_show(structure)

        for cue in timeline.cues:
            assert cue.render_mode in ("solid", "pulse", "breathe", "scroll", "wave", "gradient")
            assert len(cue.color_palette) > 0
            assert 0.0 < cue.intensity <= 1.0
            assert cue.speed > 0
            assert cue.transition in ("cut", "fade")


class TestFullPipelineWithProfile:
    """Test 2: compile_show() with a ProfileConfig respects profile overrides."""

    def test_with_profile(self):
        from dreamsync.profile import ProfileConfig, MoodProfileConfig

        profile = ProfileConfig(
            name="test_profile",
            palettes={"warm": ("#ff4400", "#ff8800", "#ffcc00")},
            moods={
                "chill": MoodProfileConfig(palettes=("warm",)),
                "groove": MoodProfileConfig(palettes=("warm",)),
                "hype": MoodProfileConfig(palettes=("warm",)),
                "drop": MoodProfileConfig(palettes=("warm",)),
            },
        )

        structure = _typical_structure()
        timeline = compile_show(structure, profile, seed=42)

        assert isinstance(timeline, ShowTimeline)
        assert len(timeline.cues) == len(structure.sections)
        # All cues should use the "warm" palette from profile
        for cue in timeline.cues:
            assert cue.color_palette == ("#ff4400", "#ff8800", "#ffcc00")


class TestSeedDeterminism:
    """Test 3: Two calls with same seed produce identical timelines."""

    def test_same_seed_same_output(self):
        structure = _typical_structure()
        t1 = compile_show(structure, seed=42)
        t2 = compile_show(structure, seed=42)

        assert len(t1.cues) == len(t2.cues)
        for c1, c2 in zip(t1.cues, t2.cues):
            assert c1.render_mode == c2.render_mode
            assert c1.color_palette == c2.color_palette
            assert c1.intensity == c2.intensity
            assert c1.speed == c2.speed
            assert c1.transition == c2.transition
            assert c1.transition_beats == c2.transition_beats


class TestDifferentSeedsDiffer:
    """Test 4: Two calls with different seeds produce different timelines."""

    def test_different_seeds(self):
        structure = _typical_structure()
        t1 = compile_show(structure, seed=1)
        t2 = compile_show(structure, seed=9999)

        # At least one cue should differ (effect_name isn't in ShowCue,
        # but render_mode or color_palette should differ with different seeds)
        any_diff = False
        for c1, c2 in zip(t1.cues, t2.cues):
            if c1.render_mode != c2.render_mode or c1.color_palette != c2.color_palette:
                any_diff = True
                break
        assert any_diff, "Expected different seeds to produce at least one different cue"


class TestKwargsForwarded:
    """Test 5: Custom kwargs are forwarded to the correct components."""

    def test_custom_intro_intensity(self):
        structure = _typical_structure()
        # Default intro_intensity=0.15, try 0.5
        timeline = compile_show(structure, seed=42, intro_intensity=0.5)

        # First cue is intro — its intensity should reflect the higher cap
        intro_cue = timeline.cues[0]
        timeline_default = compile_show(structure, seed=42, intro_intensity=0.15)
        intro_default = timeline_default.cues[0]

        # With higher cap, intro intensity should be >= the default
        assert intro_cue.intensity >= intro_default.intensity


class TestSingleSectionSong:
    """Test 6: Song with one section produces a valid timeline."""

    def test_single_section(self):
        sections = (make_section(0, 60, "verse", 0.5, "groove", 120, "A"),)
        structure = make_structure(sections, duration=60.0)
        timeline = compile_show(structure, seed=42)

        assert isinstance(timeline, ShowTimeline)
        assert len(timeline.cues) == 1
        assert timeline.cues[0].t == 0.0
        assert timeline.cues[0].transition == "cut"
        assert timeline.cues[0].transition_beats == 0


class TestManySections:
    """Test 7: Song with 10 sections produces a valid timeline."""

    def test_ten_sections(self):
        labels = ["intro", "verse", "chorus", "verse", "chorus",
                  "bridge", "chorus", "verse", "chorus", "outro"]
        moods = ["chill", "groove", "hype", "groove", "hype",
                 "chill", "hype", "groove", "hype", "chill"]
        sections = tuple(
            make_section(
                start_t=i * 20.0,
                end_t=(i + 1) * 20.0,
                label=labels[i],
                energy_mean=0.3 + i * 0.05,
                mood=moods[i],
                bpm=128.0,
                section_id=chr(65 + i),
            )
            for i in range(10)
        )
        structure = make_structure(sections, bpm=128.0, duration=200.0)
        timeline = compile_show(structure, seed=42)

        assert len(timeline.cues) == 10
        times = [c.t for c in timeline.cues]
        assert times == sorted(times)
        for cue in timeline.cues:
            assert 0.05 <= cue.intensity <= 1.0


class TestCliCompileArgs:
    """Test 8: Parse compile subcommand arguments."""

    def test_parse_compile_args(self):
        from pathlib import Path

        from dreamsync.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "compile", "structure.json",
            "--profile", "midnight_rave",
            "--output", "show.json",
            "--seed", "42",
            "--summary",
        ])
        assert args.command == "compile"
        assert args.structure_path == Path("structure.json")
        assert args.profile == "midnight_rave"
        assert args.output == Path("show.json")
        assert args.seed == 42
        assert args.summary is True


class TestCliCompileAndPlayArgs:
    """Test 9: Parse compile-and-play subcommand arguments."""

    def test_parse_compile_and_play_args(self):
        from dreamsync.cli import build_parser
        from pathlib import Path

        parser = build_parser()
        args = parser.parse_args([
            "compile-and-play", "song.mp3",
            "--config", "devices.yaml",
            "--profile", "midnight_rave",
            "--seed", "42",
            "--output", "show.json",
            "--debug",
        ])
        assert args.command == "compile-and-play"
        assert args.mp3_path == Path("song.mp3")
        assert args.config == Path("devices.yaml")
        assert args.profile == "midnight_rave"
        assert args.seed == 42
        assert args.output == Path("show.json")
        assert args.debug is True


class TestFormatSummary:
    """Test 10: format_summary() produces expected human-readable output."""

    def test_summary_contains_expected_fields(self):
        structure = _typical_structure()
        timeline = compile_show(structure, seed=42)

        summary = format_summary(structure, timeline)

        assert "Show compiled:" in summary
        assert "Duration:" in summary
        assert "2:40" in summary  # 160s = 2:40
        assert "BPM:" in summary
        assert "128.0" in summary
        assert "Sections:" in summary
        assert "6" in summary  # 6 sections
        assert "Cues:" in summary
        # Check a section label appears
        assert "intro" in summary
        assert "verse" in summary
        assert "chorus" in summary

    def test_summary_has_cue_rows(self):
        structure = _typical_structure()
        timeline = compile_show(structure, seed=42)

        summary = format_summary(structure, timeline)
        lines = summary.strip().split("\n")

        # Header lines (8) + 6 cue rows = 14 lines
        assert len(lines) >= 14
