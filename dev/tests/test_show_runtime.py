"""Tests for ShowPlaybackRuntime + run_show_playback (D5.3)."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from dreamsync.director import EffectMode, LightingIntent
from dreamsync.render import RenderMode
from dreamsync.show.models import ShowCue, ShowTimeline
from dreamsync.show.runtime_control import RuntimeControlState
from dreamsync.show.runtime import (
    ShowPlaybackRuntime, _interpolate_hex, _lerp, run_show_playback,
)


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
    duration: float = 60.0,
    bpm: float = 120.0,
    cues: tuple[ShowCue, ...] | None = None,
) -> ShowTimeline:
    beat_period = 60.0 / bpm
    beats = tuple(round(i * beat_period, 4) for i in range(int(duration / beat_period)))
    downbeats = tuple(beats[i] for i in range(0, len(beats), 4))

    if cues is None:
        cues = (
            _make_cue(0.0, render_mode="breathe", intensity=0.2, speed=0.2,
                       transition="cut", transition_beats=0),
            _make_cue(10.0, render_mode="scroll", intensity=0.6, speed=0.5,
                       transition="fade", transition_beats=4),
            _make_cue(30.0, render_mode="pulse", intensity=0.9, speed=0.8,
                       transition="cut", transition_beats=0),
        )

    return ShowTimeline(
        song_path="test.mp3",
        duration=duration,
        bpm=bpm,
        time_signature=4,
        beat_times=beats,
        downbeat_times=downbeats,
        cues=cues,
        metadata={"track_name": "Test"},
    )


def _mock_multi_adapter():
    """Create a mock MultiGoveeLanAdapter with recording send_frame."""
    adapter = MagicMock()
    adapter.send_frame = MagicMock(return_value=True)
    adapter.activate = MagicMock()
    adapter.deactivate = MagicMock()
    # No devices attribute → runtime won't try to set renderer mode
    if hasattr(adapter, "devices"):
        del adapter.devices
    return adapter


def _mock_multi_adapter_with_renderers():
    """Multi adapter with renderers that the runtime can switch modes on."""
    renderer = MagicMock()
    renderer.mode = RenderMode.SOLID
    adapter = MagicMock()
    adapter.send_frame = MagicMock(return_value=True)
    adapter.activate = MagicMock()
    adapter.deactivate = MagicMock()
    adapter.devices = [(MagicMock(), renderer, MagicMock())]
    return adapter, renderer


# ---------------------------------------------------------------------------
# _lerp
# ---------------------------------------------------------------------------

class TestLerp:
    def test_at_zero(self):
        assert _lerp(0.0, 1.0, 0.0) == 0.0

    def test_at_one(self):
        assert _lerp(0.0, 1.0, 1.0) == 1.0

    def test_midpoint(self):
        assert abs(_lerp(0.2, 0.8, 0.5) - 0.5) < 1e-9


# ---------------------------------------------------------------------------
# ShowPlaybackRuntime.tick — basic cue lookup
# ---------------------------------------------------------------------------

class TestTickBasic:
    def test_tick_returns_true_on_send(self):
        tl = _make_timeline()
        adapter = _mock_multi_adapter()
        runtime = ShowPlaybackRuntime(tl, adapter)
        assert runtime.tick(5.0) is True
        adapter.send_frame.assert_called_once()

    def test_tick_returns_false_before_first_cue(self):
        cues = (_make_cue(1.0),)
        tl = _make_timeline(cues=cues)
        adapter = _mock_multi_adapter()
        runtime = ShowPlaybackRuntime(tl, adapter)
        assert runtime.tick(0.5) is False

    def test_tick_sets_current_cue(self):
        tl = _make_timeline()
        adapter = _mock_multi_adapter()
        runtime = ShowPlaybackRuntime(tl, adapter)
        runtime.tick(0.0)
        assert runtime.current_cue is not None
        assert runtime.current_cue.render_mode == "breathe"

    def test_tick_switches_cue(self):
        tl = _make_timeline()
        adapter = _mock_multi_adapter()
        runtime = ShowPlaybackRuntime(tl, adapter)
        runtime.tick(5.0)
        assert runtime.current_cue.render_mode == "breathe"
        runtime.tick(15.0)
        assert runtime.current_cue.render_mode == "scroll"


# ---------------------------------------------------------------------------
# Intent construction
# ---------------------------------------------------------------------------

class TestIntentConstruction:
    def test_intent_has_correct_fields(self):
        tl = _make_timeline()
        adapter = _mock_multi_adapter()
        runtime = ShowPlaybackRuntime(tl, adapter)
        runtime.tick(5.0)
        call_args = adapter.send_frame.call_args
        intent = call_args[0][1]  # second positional arg
        assert isinstance(intent, LightingIntent)
        assert intent.mode == EffectMode.AMBIENT  # breathe → AMBIENT
        assert intent.bpm == 120.0

    def test_intent_intensity_matches_cue(self):
        tl = _make_timeline()
        adapter = _mock_multi_adapter()
        runtime = ShowPlaybackRuntime(tl, adapter)
        runtime.tick(5.0)  # breathe cue, intensity=0.2
        intent = adapter.send_frame.call_args[0][1]
        assert abs(intent.intensity - 0.2) < 0.01

    def test_intent_color_from_palette(self):
        tl = _make_timeline()
        adapter = _mock_multi_adapter()
        runtime = ShowPlaybackRuntime(tl, adapter)
        runtime.tick(5.0)
        intent = adapter.send_frame.call_args[0][1]
        assert intent.color in ("#ff0000", "#00ff00", "#0000ff")

    def test_runtime_params_include_spatial_render_metadata(self):
        cue = _make_cue(
            0.0,
            render_mode="gradient",
            params={"spatial_mode": "blend"},
            color_palette=("#111111", "#222222", "#333333"),
        )
        tl = _make_timeline(cues=(cue,))
        adapter = _mock_multi_adapter()
        runtime = ShowPlaybackRuntime(tl, adapter)
        runtime.tick(0.0)
        params = adapter.send_frame.call_args[1]["params"]
        assert params["_render_mode"] == "gradient"
        assert params["_spatial_palette"] == cue.color_palette
        assert params["spatial_mode"] == "blend"

    def test_runtime_control_can_override_cue_palette_and_render_mode(self):
        cue = _make_cue(
            0.0,
            render_mode="scroll",
            intensity=0.4,
            color_palette=("#111111", "#222222", "#333333"),
        )
        tl = _make_timeline(cues=(cue,))
        adapter = _mock_multi_adapter()
        state = RuntimeControlState(
            palette_override=("#abcdef", "#123456", "#654321"),
            render_mode="gradient",
            intensity_multiplier=1.25,
        )
        runtime = ShowPlaybackRuntime(tl, adapter, control_state_getter=lambda: state)
        runtime.tick(0.0)
        intent = adapter.send_frame.call_args[0][1]
        params = adapter.send_frame.call_args[1]["params"]
        assert runtime.current_cue is not None
        assert runtime.current_cue.render_mode == "gradient"
        assert intent.intensity == pytest.approx(0.5)
        assert params["_render_mode"] == "gradient"
        assert params["_spatial_palette"][0] == "#abcdef"


# ---------------------------------------------------------------------------
# Fade transitions
# ---------------------------------------------------------------------------

class TestFadeTransition:
    def test_fade_interpolates_intensity(self):
        tl = _make_timeline(bpm=120.0)
        adapter = _mock_multi_adapter()
        runtime = ShowPlaybackRuntime(tl, adapter)

        # Tick at t=5 → breathe cue (intensity=0.2)
        runtime.tick(5.0)

        # Tick at t=10.0 → scroll cue starts (transition=fade, 4 beats)
        # At 120 BPM, 4 beats = 2 seconds, so fade from 10.0 to 12.0
        runtime.tick(10.0)
        assert runtime.current_cue.render_mode == "scroll"

        # Tick at t=11.0 → midpoint of fade (50%)
        adapter.send_frame.reset_mock()
        runtime.tick(11.0)
        intent = adapter.send_frame.call_args[0][1]
        # Should be between 0.2 (old) and 0.6 (new)
        assert 0.2 < intent.intensity < 0.6

    def test_fade_completes(self):
        tl = _make_timeline(bpm=120.0)
        adapter = _mock_multi_adapter()
        runtime = ShowPlaybackRuntime(tl, adapter)

        runtime.tick(5.0)  # breathe
        runtime.tick(10.0)  # scroll with fade

        # After fade window (10 + 2 = 12)
        adapter.send_frame.reset_mock()
        runtime.tick(13.0)
        intent = adapter.send_frame.call_args[0][1]
        assert abs(intent.intensity - 0.6) < 0.01

    def test_cut_transition_no_fade(self):
        tl = _make_timeline(bpm=120.0)
        adapter = _mock_multi_adapter()
        runtime = ShowPlaybackRuntime(tl, adapter)

        runtime.tick(15.0)  # scroll cue
        runtime.tick(30.0)  # pulse cue (transition=cut)
        adapter.send_frame.reset_mock()
        runtime.tick(30.1)
        intent = adapter.send_frame.call_args[0][1]
        assert abs(intent.intensity - 0.9) < 0.01


# ---------------------------------------------------------------------------
# Beat detection + color cycling
# ---------------------------------------------------------------------------

class TestBeatAndColor:
    def test_color_advances_on_beat(self):
        tl = _make_timeline(bpm=120.0)
        adapter = _mock_multi_adapter()
        runtime = ShowPlaybackRuntime(tl, adapter)

        # At 120 BPM, beats are at 0.0, 0.5, 1.0, ...
        runtime.tick(0.0)
        color_0 = adapter.send_frame.call_args[0][1].color

        adapter.send_frame.reset_mock()
        runtime.tick(0.25)  # between beats — no advance
        runtime.tick(0.5)  # beat!
        color_1 = adapter.send_frame.call_args[0][1].color

        # Color should have advanced
        assert color_0 != color_1

    def test_beat_flag_passed_to_adapter(self):
        tl = _make_timeline(bpm=120.0)
        adapter = _mock_multi_adapter()
        runtime = ShowPlaybackRuntime(tl, adapter)

        runtime.tick(0.0)  # This is on a beat
        call_kwargs = adapter.send_frame.call_args[1]
        assert call_kwargs["beat"] is True

    def test_beat_tolerance_40ms_catches_nearby(self):
        """At default 40ms tolerance, beat at t=0.0, tick at t=0.035 → beat=True."""
        tl = _make_timeline(bpm=120.0)
        adapter = _mock_multi_adapter()
        runtime = ShowPlaybackRuntime(tl, adapter)
        runtime.tick(0.035)
        call_kwargs = adapter.send_frame.call_args[1]
        assert call_kwargs["beat"] is True

    def test_beat_tolerance_parameterized(self):
        """Construct runtime with custom beat_tolerance."""
        tl = _make_timeline(bpm=120.0)
        adapter = _mock_multi_adapter()
        runtime = ShowPlaybackRuntime(tl, adapter, beat_tolerance=0.050)
        assert runtime._beat_tolerance == 0.050


# ---------------------------------------------------------------------------
# Color cycling modes (Issue 3, D2)
# ---------------------------------------------------------------------------

class TestColorCycling:
    def test_color_cycles_on_downbeat_not_beat(self):
        """Default mode: tick through 8 beats in 4/4 → color advances twice (not 8)."""
        tl = _make_timeline(bpm=120.0)
        adapter = _mock_multi_adapter()
        runtime = ShowPlaybackRuntime(tl, adapter)  # default: downbeat mode

        # At 120 BPM 4/4: beats at 0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5
        # Downbeats at 0.0, 2.0
        initial = runtime._color_index
        beat_times = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5]
        for bt in beat_times:
            runtime.tick(bt)
            # tick between to reset debounce
            runtime.tick(bt + 0.1)

        # Should advance on downbeats (0.0, 2.0) = 2 advances
        assert runtime._color_index == (initial + 2) % 3

    def test_color_cycle_mode_beat_preserves_old_behavior(self):
        """color_cycle_mode='beat' → color changes on every beat."""
        tl = _make_timeline(bpm=120.0)
        adapter = _mock_multi_adapter()
        runtime = ShowPlaybackRuntime(tl, adapter, color_cycle_mode="beat")

        initial = runtime._color_index
        # Tick on 4 beats
        for bt in [0.0, 0.5, 1.0, 1.5]:
            runtime.tick(bt)
            runtime.tick(bt + 0.1)

        assert runtime._color_index == (initial + 4) % 3

    def test_beat_flag_still_fires_for_effects(self):
        """Even in downbeat mode, beat flag is passed to send_frame on every beat."""
        tl = _make_timeline(bpm=120.0)
        adapter = _mock_multi_adapter()
        runtime = ShowPlaybackRuntime(tl, adapter)  # downbeat mode

        runtime.tick(0.5)  # beat but not downbeat
        call_kwargs = adapter.send_frame.call_args[1]
        assert call_kwargs["beat"] is True

    def test_color_cycle_mode_downbeat_default(self):
        """Default construction → downbeat mode."""
        tl = _make_timeline(bpm=120.0)
        adapter = _mock_multi_adapter()
        runtime = ShowPlaybackRuntime(tl, adapter)
        assert runtime._color_cycle_mode == "downbeat"


# ---------------------------------------------------------------------------
# Color interpolation (Issue 3, D3)
# ---------------------------------------------------------------------------

class TestColorInterpolation:
    def test_interpolate_hex_basic(self):
        result = _interpolate_hex("#ff0000", "#0000ff", 0.5)
        # Midpoint: R=127, G=0, B=127
        assert result == "#800080" or result == "#7f007f"

    def test_interpolate_hex_at_zero(self):
        assert _interpolate_hex("#ff0000", "#0000ff", 0.0) == "#ff0000"

    def test_interpolate_hex_at_one(self):
        assert _interpolate_hex("#ff0000", "#0000ff", 1.0) == "#0000ff"

    def test_color_interpolation_complete(self):
        """After blend duration, color is fully at current index."""
        tl = _make_timeline(bpm=120.0)
        adapter = _mock_multi_adapter()
        runtime = ShowPlaybackRuntime(tl, adapter, color_cycle_mode="beat")

        runtime.tick(0.0)  # beat, triggers color change
        # After blend duration (2 beats = 1s at 120 BPM)
        runtime.tick(0.1)
        runtime.tick(1.5)
        intent = adapter.send_frame.call_args[0][1]
        # Should be at the current color, not interpolated
        assert intent.color in ("#ff0000", "#00ff00", "#0000ff")


# ---------------------------------------------------------------------------
# Render mode switching
# ---------------------------------------------------------------------------

class TestRenderModeSwitch:
    def test_renderer_mode_updated_on_cue_change(self):
        tl = _make_timeline(bpm=120.0)
        adapter, renderer = _mock_multi_adapter_with_renderers()
        runtime = ShowPlaybackRuntime(tl, adapter)

        runtime.tick(5.0)  # breathe
        assert renderer.mode == RenderMode.BREATHE

        runtime.tick(15.0)  # scroll
        assert renderer.mode == RenderMode.SCROLL


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

class TestStats:
    def test_stats_track_frames_and_cues(self):
        tl = _make_timeline(bpm=120.0)
        adapter = _mock_multi_adapter()
        runtime = ShowPlaybackRuntime(tl, adapter)

        runtime.tick(0.0)
        runtime.tick(5.0)
        runtime.tick(15.0)

        stats = runtime.stats
        assert stats["frames_sent"] == 3
        assert stats["cues_played"] == 2  # breathe + scroll


# ---------------------------------------------------------------------------
# run_show_playback (orchestrator)
# ---------------------------------------------------------------------------

class TestRunShowPlayback:
    def test_orchestrator_calls_lifecycle(self, tmp_path: Path):
        """run_show_playback loads show, plays audio, activates/deactivates devices."""
        tl = _make_timeline(duration=0.1, bpm=120.0)
        show_file = tmp_path / "show.json"
        tl.to_json(show_file)

        mp3_path = tmp_path / "test.mp3"
        mp3_path.write_bytes(b"\x00")

        adapter = _mock_multi_adapter()

        # Mock AudioPlayer to immediately finish
        mock_player = MagicMock()
        mock_player.position_seconds = 0.0
        mock_player.duration = 0.1
        # finished returns True after first check to break loop
        mock_player.finished = True

        with patch("dreamsync.show.player.AudioPlayer", return_value=mock_player):
            summary = run_show_playback(
                mp3_path, show_file, adapter,
                sample_rate=44100,
            )

        adapter.activate.assert_called_once_with(brightness=100)
        mock_player.play.assert_called_once()
        mock_player.stop.assert_called_once()
        adapter.deactivate.assert_called_once()
        assert "duration" in summary
        assert "elapsed" in summary

    def test_stop_event_interrupts(self, tmp_path: Path):
        tl = _make_timeline(duration=10.0, bpm=120.0)
        show_file = tmp_path / "show.json"
        tl.to_json(show_file)
        mp3_path = tmp_path / "test.mp3"
        mp3_path.write_bytes(b"\x00")

        adapter = _mock_multi_adapter()
        stop_event = threading.Event()
        stop_event.set()  # Pre-set so loop exits immediately

        mock_player = MagicMock()
        mock_player.position_seconds = 0.0
        mock_player.duration = 10.0
        mock_player.finished = False

        with patch("dreamsync.show.player.AudioPlayer", return_value=mock_player):
            summary = run_show_playback(
                mp3_path, show_file, adapter,
                stop_event=stop_event,
            )

        mock_player.stop.assert_called_once()
        adapter.deactivate.assert_called_once()


# ---------------------------------------------------------------------------
# Intensity ramp tests (Issue 6, D2)
# ---------------------------------------------------------------------------

class TestIntensityRamp:
    def test_intensity_ramp_midway(self) -> None:
        """Cue with intensity_start=0.8, intensity=0.2, query at 50% → ~0.5."""
        cues = (
            _make_cue(0.0, intensity=0.2, intensity_start=0.8),
            _make_cue(30.0, intensity=0.5),
        )
        tl = _make_timeline(duration=60.0, cues=cues)
        adapter = _mock_multi_adapter()
        runtime = ShowPlaybackRuntime(tl, adapter)

        # Tick at t=15 (50% through first 30s cue)
        runtime.tick(15.0)
        call_args = adapter.send_frame.call_args
        intent = call_args[0][1]
        # Should be ~0.5 (lerp from 0.8 to 0.2 at 50%)
        assert abs(intent.intensity - 0.5) < 0.05

    def test_intensity_ramp_start(self) -> None:
        """At cue start, intensity = intensity_start."""
        cues = (
            _make_cue(0.0, intensity=0.1, intensity_start=0.8),
            _make_cue(30.0, intensity=0.5),
        )
        tl = _make_timeline(duration=60.0, cues=cues)
        adapter = _mock_multi_adapter()
        runtime = ShowPlaybackRuntime(tl, adapter)

        runtime.tick(0.0)
        call_args = adapter.send_frame.call_args
        intent = call_args[0][1]
        assert abs(intent.intensity - 0.8) < 0.05

    def test_intensity_ramp_end(self) -> None:
        """At cue end, intensity = cue.intensity."""
        cues = (
            _make_cue(0.0, intensity=0.1, intensity_start=0.8),
            _make_cue(30.0, intensity=0.5),
        )
        tl = _make_timeline(duration=60.0, cues=cues)
        adapter = _mock_multi_adapter()
        runtime = ShowPlaybackRuntime(tl, adapter)

        # Tick at t=29.9 (near end of 30s cue)
        runtime.tick(29.9)
        call_args = adapter.send_frame.call_args
        intent = call_args[0][1]
        assert abs(intent.intensity - 0.1) < 0.05

    def test_no_ramp_when_intensity_start_none(self) -> None:
        """Cue without intensity_start → flat intensity."""
        cues = (
            _make_cue(0.0, intensity=0.5),
            _make_cue(30.0, intensity=0.5),
        )
        tl = _make_timeline(duration=60.0, cues=cues)
        adapter = _mock_multi_adapter()
        runtime = ShowPlaybackRuntime(tl, adapter)

        runtime.tick(15.0)
        call_args = adapter.send_frame.call_args
        intent = call_args[0][1]
        assert abs(intent.intensity - 0.5) < 0.01
