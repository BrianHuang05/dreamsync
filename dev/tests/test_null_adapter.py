"""Tests for NullMultiAdapter (dry-run / audio-only adapter)."""

from __future__ import annotations

import pytest

from dreamsync.director import EffectMode, LightingIntent
from dreamsync.output.null_adapter import NullMultiAdapter
from dreamsync.show.models import ShowCue, ShowTimeline
from dreamsync.show.runtime import ShowPlaybackRuntime


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


def _make_timeline(duration: float = 10.0, bpm: float = 120.0) -> ShowTimeline:
    beat_period = 60.0 / bpm
    beats = tuple(round(i * beat_period, 4) for i in range(int(duration / beat_period)))
    downbeats = tuple(beats[i] for i in range(0, len(beats), 4))
    cues = (
        _make_cue(0.0, render_mode="breathe", intensity=0.3),
        _make_cue(5.0, render_mode="scroll", intensity=0.7),
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


class TestNullMultiAdapter:
    def test_activate_deactivate_noop(self):
        adapter = NullMultiAdapter()
        adapter.activate(brightness=100)
        adapter.deactivate()

    def test_send_frame_returns_true(self):
        adapter = NullMultiAdapter()
        intent = LightingIntent(
            mode=EffectMode.PULSE,
            intensity=0.5,
            color=(255, 0, 0),
            bpm=120.0,
            speed=0.5,
        )
        assert adapter.send_frame(1.0, intent, beat=True) is True

    def test_send_frame_counts(self):
        adapter = NullMultiAdapter()
        intent = LightingIntent(
            mode=EffectMode.PULSE,
            intensity=0.5,
            color=(255, 0, 0),
            bpm=120.0,
            speed=0.5,
        )
        assert adapter._frames_sent == 0
        adapter.send_frame(0.0, intent)
        adapter.send_frame(0.1, intent)
        adapter.send_frame(0.2, intent)
        assert adapter._frames_sent == 3

    def test_devices_empty_list(self):
        adapter = NullMultiAdapter()
        assert adapter.devices == []

    def test_compatible_with_show_runtime(self):
        adapter = NullMultiAdapter()
        timeline = _make_timeline()
        runtime = ShowPlaybackRuntime(timeline, adapter)
        runtime.tick(0.0)
        runtime.tick(1.0)
        runtime.tick(5.5)
        assert adapter._frames_sent > 0

    def test_compatible_with_local_session(self):
        from dreamsync.cache import ShowCache
        from dreamsync.local_session import LocalShowSession

        adapter = NullMultiAdapter()
        cache = ShowCache("/tmp/test-null-cache")
        session = LocalShowSession(
            adapter,
            cache=cache,
            sample_rate=44100,
            debug=False,
        )
        assert session is not None
