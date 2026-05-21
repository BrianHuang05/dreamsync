"""Tests for ShowTimeline + ShowCue models (D5.1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dreamsync.show.models import ShowCue, ShowTimeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cue(t: float = 0.0, **kwargs) -> ShowCue:
    defaults = dict(
        render_mode="scroll",
        color_palette=("#ff0000", "#00ff00", "#0000ff"),
        intensity=0.5,
        speed=0.4,
        params={},
        transition="cut",
        transition_beats=0,
    )
    defaults.update(kwargs)
    defaults["t"] = t
    return ShowCue(**defaults)


def _make_timeline(
    duration: float = 180.0,
    bpm: float = 128.0,
    cues: tuple[ShowCue, ...] | None = None,
) -> ShowTimeline:
    beat_period = 60.0 / bpm
    beats = tuple(round(i * beat_period, 4) for i in range(int(duration / beat_period)))
    downbeats = tuple(beats[i] for i in range(0, len(beats), 4))

    if cues is None:
        cues = (
            _make_cue(0.0, render_mode="breathe", intensity=0.15, speed=0.22,
                       transition="cut", transition_beats=0),
            _make_cue(16.2, render_mode="scroll", intensity=0.35, speed=0.4,
                       transition="fade", transition_beats=4),
            _make_cue(48.5, render_mode="pulse", intensity=0.72, speed=0.6,
                       transition="fade", transition_beats=2),
            _make_cue(120.0, render_mode="wave", intensity=0.55, speed=0.5,
                       transition="fade", transition_beats=4),
            _make_cue(160.0, render_mode="breathe", intensity=0.10, speed=0.15,
                       transition="fade", transition_beats=8),
        )

    return ShowTimeline(
        song_path="test.mp3",
        duration=duration,
        bpm=bpm,
        time_signature=4,
        beat_times=beats,
        downbeat_times=downbeats,
        cues=cues,
        metadata={"track_name": "Test Song", "artist": "Test Artist"},
    )


# ---------------------------------------------------------------------------
# ShowCue + ShowTimeline dataclass basics
# ---------------------------------------------------------------------------

class TestShowCueBasics:
    def test_frozen(self):
        cue = _make_cue()
        with pytest.raises(AttributeError):
            cue.intensity = 0.9  # type: ignore[misc]

    def test_fields(self):
        cue = _make_cue(t=5.0, render_mode="pulse", intensity=0.8, speed=0.6)
        assert cue.t == 5.0
        assert cue.render_mode == "pulse"
        assert cue.intensity == 0.8
        assert len(cue.color_palette) == 3


class TestShowTimelineBasics:
    def test_frozen(self):
        tl = _make_timeline()
        with pytest.raises(AttributeError):
            tl.bpm = 140.0  # type: ignore[misc]

    def test_fields(self):
        tl = _make_timeline(bpm=120.0)
        assert tl.bpm == 120.0
        assert tl.time_signature == 4
        assert len(tl.cues) == 5
        assert tl.metadata["artist"] == "Test Artist"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class TestValidation:
    def test_negative_duration_raises(self):
        with pytest.raises(ValueError, match="duration must be positive"):
            _make_timeline(duration=-1.0)

    def test_zero_bpm_raises(self):
        cues = (_make_cue(0.0),)
        with pytest.raises(ValueError, match="bpm must be positive"):
            ShowTimeline(
                song_path="test.mp3", duration=180.0, bpm=0.0,
                time_signature=4, beat_times=(), downbeat_times=(),
                cues=cues, metadata={},
            )

    def test_bad_time_signature_raises(self):
        tl = _make_timeline()
        d = tl.to_dict()
        d["time_signature"] = 5
        with pytest.raises(ValueError, match="time_signature must be 3 or 4"):
            ShowTimeline.from_dict(d)

    def test_unsorted_cues_raises(self):
        cues = (
            _make_cue(10.0),
            _make_cue(5.0),  # out of order
        )
        with pytest.raises(ValueError, match="cues must be sorted"):
            _make_timeline(cues=cues)

    def test_empty_cues_raises(self):
        with pytest.raises(ValueError, match="cues must not be empty"):
            _make_timeline(cues=())


# ---------------------------------------------------------------------------
# Serialisation — to_dict / to_json / from_json
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_to_dict_returns_valid_json_types(self):
        tl = _make_timeline()
        d = tl.to_dict()
        json_str = json.dumps(d)
        assert len(json_str) > 100

    def test_to_dict_fields(self):
        tl = _make_timeline(bpm=128.0)
        d = tl.to_dict()
        assert d["bpm"] == 128.0
        assert d["time_signature"] == 4
        assert len(d["cues"]) == 5
        assert d["cues"][0]["render_mode"] == "breathe"
        assert isinstance(d["beat_times"], list)
        assert isinstance(d["cues"][0]["color_palette"], list)

    def test_to_json_creates_file(self, tmp_path: Path):
        tl = _make_timeline()
        out = tmp_path / "show.json"
        tl.to_json(out)
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["bpm"] == 128.0

    def test_from_json_roundtrip(self, tmp_path: Path):
        tl = _make_timeline()
        out = tmp_path / "show.json"
        tl.to_json(out)
        loaded = ShowTimeline.from_json(out)
        assert loaded.bpm == tl.bpm
        assert loaded.duration == tl.duration
        assert loaded.time_signature == tl.time_signature
        assert len(loaded.cues) == len(tl.cues)
        assert loaded.cues[0].render_mode == tl.cues[0].render_mode
        assert loaded.cues[1].transition == "fade"
        assert loaded.cues[1].transition_beats == 4
        assert len(loaded.beat_times) == len(tl.beat_times)
        assert loaded.metadata == tl.metadata

    def test_from_dict_roundtrip(self):
        tl = _make_timeline()
        d = tl.to_dict()
        loaded = ShowTimeline.from_dict(d)
        assert loaded.bpm == tl.bpm
        assert loaded.song_path == tl.song_path

    def test_eq_routes_params_roundtrip(self):
        tl = _make_timeline(cues=(
            _make_cue(
                0.0,
                params={
                    "eq_routes": [
                        {
                            "band": "bass",
                            "when": "enter",
                            "color_bias": "#ff6600",
                            "spatial_preset": "flash_floor_only",
                            "intensity_boost": 0.15,
                        },
                    ],
                },
            ),
        ))
        loaded = ShowTimeline.from_dict(tl.to_dict())
        assert loaded.cues[0].params["eq_routes"][0]["band"] == "bass"
        assert loaded.cues[0].params["eq_routes"][0]["spatial_preset"] == "flash_floor_only"

    def test_to_json_creates_parent_dirs(self, tmp_path: Path):
        tl = _make_timeline()
        out = tmp_path / "subdir" / "nested" / "show.json"
        tl.to_json(out)
        assert out.exists()

    def test_float_precision(self):
        tl = _make_timeline()
        d = tl.to_dict()
        assert isinstance(d["duration"], float)
        bpm_str = str(d["bpm"])
        if "." in bpm_str:
            assert len(bpm_str.split(".")[1]) <= 2


# ---------------------------------------------------------------------------
# cue_at — binary search
# ---------------------------------------------------------------------------

class TestCueAt:
    def test_before_first_cue(self):
        cues = (_make_cue(1.0),)
        tl = _make_timeline(cues=cues)
        assert tl.cue_at(0.5) is None

    def test_at_first_cue(self):
        tl = _make_timeline()
        cue = tl.cue_at(0.0)
        assert cue is not None
        assert cue.render_mode == "breathe"

    def test_mid_cue(self):
        tl = _make_timeline()
        cue = tl.cue_at(30.0)
        assert cue is not None
        assert cue.render_mode == "scroll"  # cue at t=16.2

    def test_at_cue_boundary(self):
        tl = _make_timeline()
        cue = tl.cue_at(48.5)
        assert cue is not None
        assert cue.render_mode == "pulse"

    def test_after_last_cue(self):
        tl = _make_timeline()
        cue = tl.cue_at(175.0)
        assert cue is not None
        assert cue.render_mode == "breathe"  # last cue at t=160


# ---------------------------------------------------------------------------
# is_beat / is_downbeat
# ---------------------------------------------------------------------------

class TestBeatDetection:
    def test_is_beat_exact(self):
        tl = _make_timeline(bpm=120.0)
        # At 120 BPM, beats are at 0.0, 0.5, 1.0, 1.5, ...
        assert tl.is_beat(0.0)
        assert tl.is_beat(0.5)
        assert tl.is_beat(1.0)

    def test_is_beat_within_tolerance(self):
        tl = _make_timeline(bpm=120.0)
        assert tl.is_beat(0.02, tolerance=0.025)  # 0.02 is within 0.025 of 0.0
        assert tl.is_beat(0.52, tolerance=0.025)  # 0.52 is within 0.025 of 0.5

    def test_is_beat_outside_tolerance(self):
        tl = _make_timeline(bpm=120.0)
        # Midpoint between beats at 0.5s intervals = 0.25
        assert not tl.is_beat(0.25, tolerance=0.025)

    def test_is_downbeat(self):
        tl = _make_timeline(bpm=120.0)
        # At 120 BPM, 4/4: downbeats at 0.0, 2.0, 4.0, ...
        assert tl.is_downbeat(0.0)
        assert tl.is_downbeat(2.0)
        assert not tl.is_downbeat(0.5)  # regular beat, not downbeat

    def test_empty_beat_times(self):
        cues = (_make_cue(0.0),)
        tl = ShowTimeline(
            song_path="test.mp3", duration=10.0, bpm=128.0,
            time_signature=4, beat_times=(), downbeat_times=(),
            cues=cues, metadata={},
        )
        assert not tl.is_beat(0.0)
        assert not tl.is_downbeat(0.0)

    def test_beat_tolerance_40ms_catches_nearby(self):
        """Default 40ms tolerance catches beats that 25ms would miss."""
        tl = _make_timeline(bpm=120.0)
        # Beat at t=0.0, tick at t=0.035 → within 40ms, outside 25ms
        assert tl.is_beat(0.035)  # default tolerance=0.040
        assert not tl.is_beat(0.035, tolerance=0.025)

    def test_downbeat_tolerance_40ms_catches_nearby(self):
        """Default 40ms tolerance catches downbeats that 25ms would miss."""
        tl = _make_timeline(bpm=120.0)
        # Downbeat at t=0.0, tick at t=0.035 → within 40ms
        assert tl.is_downbeat(0.035)
        assert not tl.is_downbeat(0.035, tolerance=0.025)
