"""Tests for SpotifyShowSession — D5.2."""

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from dreamsync.show.models import ShowCue, ShowTimeline
from dreamsync.spotify.models import PlaybackState, QueueSnapshot, SpotifyTrack
from dreamsync.v3_session import SpotifyShowSession


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_track(**kwargs) -> SpotifyTrack:
    defaults = dict(
        track_id="abc123",
        name="Test Song",
        artist="Test Artist",
        album="Test Album",
        duration_ms=180_000,
        uri="spotify:track:abc123",
    )
    defaults.update(kwargs)
    return SpotifyTrack(**defaults)


def _make_timeline(**kwargs) -> ShowTimeline:
    defaults = dict(
        song_path="test.mp3",
        duration=180.0,
        bpm=120.0,
        time_signature=4,
        beat_times=tuple(i * 0.5 for i in range(360)),
        downbeat_times=tuple(i * 2.0 for i in range(90)),
        cues=(
            ShowCue(
                t=0.0,
                render_mode="scroll",
                color_palette=("#FF0000", "#00FF00"),
                intensity=0.8,
                speed=1.0,
                params={},
                transition="cut",
                transition_beats=0,
            ),
        ),
        metadata={"track_name": "Test Song", "artist": "Test Artist"},
    )
    defaults.update(kwargs)
    return ShowTimeline(**defaults)


def _make_playback_state(**kwargs) -> PlaybackState:
    defaults = dict(
        is_playing=True,
        track=_make_track(),
        progress_ms=5000,
        timestamp=1000.0,
        device_name="Test Device",
        shuffle=False,
        repeat="off",
    )
    defaults.update(kwargs)
    return PlaybackState(**defaults)


def _make_queue_snapshot(**kwargs) -> QueueSnapshot:
    defaults = dict(
        currently_playing=_make_track(),
        queue=(
            _make_track(track_id="next1", name="Next Song 1"),
            _make_track(track_id="next2", name="Next Song 2"),
        ),
        fetched_at=1000.0,
    )
    defaults.update(kwargs)
    return QueueSnapshot(**defaults)


def _make_session(*, cache=None, capture_dir=None, debug=False):
    """Build a SpotifyShowSession with mocked dependencies."""
    adapter = MagicMock()
    adapter.activate = MagicMock()
    adapter.deactivate = MagicMock()
    adapter.send_frame = MagicMock(return_value=True)
    adapter.devices = []

    watcher = MagicMock()
    watcher._on_track_changed = None
    watcher._on_playback_state_changed = None
    watcher._on_queue_updated = None

    if cache is None:
        cache = MagicMock()
        cache.has = MagicMock(return_value=False)
        cache.get = MagicMock(return_value=None)

    session = SpotifyShowSession(
        adapter,
        watcher,
        cache=cache,
        profile=None,
        capture_dir=capture_dir or "nonexistent_capture_dir",
        debug=debug,
    )
    return session, adapter, watcher, cache


# ---------------------------------------------------------------------------
# Track Change Tests
# ---------------------------------------------------------------------------

class TestTrackChange:
    """Track change triggers compilation and runtime swap."""

    def test_track_change_cache_hit(self):
        """Cache hit → runtime created from cached timeline."""
        timeline = _make_timeline()
        cache = MagicMock()
        cache.has = MagicMock(return_value=True)
        cache.get = MagicMock(return_value=timeline)

        session, adapter, _, _ = _make_session(cache=cache)
        track = _make_track()

        session._on_track_changed(track, None)

        cache.has.assert_called_with(track.track_id, None)
        assert session._runtime is not None
        assert session._tracks_played == 1
        assert session._cache_hits == 1

    def test_track_change_cache_miss_no_mp3(self):
        """Cache miss + no mp3 → runtime set to None."""
        session, adapter, _, cache = _make_session()
        cache.has.return_value = False

        track = _make_track()
        session._on_track_changed(track, None)

        assert session._runtime is None
        assert session._tracks_played == 1

    @patch("dreamsync.analyzer.analyze.analyze_song")
    @patch("dreamsync.cache.cached_compile_show")
    def test_track_change_cache_miss_with_mp3(self, mock_compile, mock_analyze, tmp_path):
        """Cache miss + mp3 available → analyze + compile → runtime created."""
        timeline = _make_timeline()
        mock_analyze.return_value = MagicMock()
        mock_compile.return_value = (timeline, False)

        # Create a capture dir with a matching mp3
        capture_dir = tmp_path / "captured"
        capture_dir.mkdir()
        (capture_dir / "Test Song.mp3").touch()

        session, adapter, _, cache = _make_session(capture_dir=str(capture_dir))
        cache.has.return_value = False

        track = _make_track()
        session._on_track_changed(track, None)

        mock_analyze.assert_called_once()
        mock_compile.assert_called_once()
        assert session._runtime is not None
        assert session._cache_misses == 1

    def test_track_change_compile_error(self, tmp_path):
        """Compile error → runtime set to None, no crash."""
        capture_dir = tmp_path / "captured"
        capture_dir.mkdir()
        (capture_dir / "Test Song.mp3").touch()

        session, adapter, _, cache = _make_session(capture_dir=str(capture_dir))
        cache.has.return_value = False

        with patch("dreamsync.analyzer.analyze.analyze_song", side_effect=RuntimeError("boom")):
            track = _make_track()
            session._on_track_changed(track, None)

        assert session._runtime is None
        assert session._compile_errors == 1


# ---------------------------------------------------------------------------
# Playback Handling Tests
# ---------------------------------------------------------------------------

class TestPlaybackHandling:
    """Position tracking, pause, and seek via interpolator."""

    def test_playback_state_updates_interpolator(self):
        """_on_playback_state feeds the interpolator."""
        session, _, _, _ = _make_session()
        state = _make_playback_state(progress_ms=10_000, is_playing=True, timestamp=1000.0)

        session._on_playback_state(state)

        assert session._interpolator.is_playing is True
        with patch("time.monotonic", return_value=1000.0):
            assert abs(session._interpolator.position_seconds - 10.0) < 0.01

    def test_pause_freezes_interpolator(self):
        """Pause state stops position from advancing."""
        session, _, _, _ = _make_session()

        session._on_playback_state(
            _make_playback_state(progress_ms=10_000, is_playing=True, timestamp=1000.0)
        )
        session._on_playback_state(
            _make_playback_state(progress_ms=11_000, is_playing=False, timestamp=1001.0)
        )

        assert session._interpolator.is_playing is False
        with patch("time.monotonic", return_value=1010.0):
            assert abs(session._interpolator.position_seconds - 11.0) < 0.01

    def test_seek_updates_position(self):
        """Seek (large jump) snaps interpolator to new position."""
        session, _, _, _ = _make_session()

        session._on_playback_state(
            _make_playback_state(progress_ms=10_000, is_playing=True, timestamp=1000.0)
        )
        # Seek to 120s
        session._on_playback_state(
            _make_playback_state(progress_ms=120_000, is_playing=True, timestamp=1002.0)
        )

        with patch("time.monotonic", return_value=1002.0):
            assert abs(session._interpolator.position_seconds - 120.0) < 0.01


# ---------------------------------------------------------------------------
# Queue Precompilation + Session Tests
# ---------------------------------------------------------------------------

class TestQueueAndSession:
    """Queue precompilation, run loop, and summary stats."""

    def test_queue_precompilation(self):
        """Upcoming tracks are compiled when queue is updated."""
        timeline = _make_timeline()
        cache = MagicMock()
        # First track not cached, second is cached
        cache.has = MagicMock(side_effect=[False, True])
        cache.get = MagicMock(return_value=timeline)

        session, _, _, _ = _make_session(cache=cache)

        # Mock _compile_for_track to return a timeline
        session._compile_for_track = MagicMock(return_value=timeline)

        queue = (
            _make_track(track_id="next1"),
            _make_track(track_id="next2"),
        )
        session._precompile_queue(queue)

        # Only the first (uncached) track should be compiled
        session._compile_for_track.assert_called_once()
        assert session._precompiled == 1

    def test_run_returns_summary(self):
        """run() returns a summary dict with stats."""
        session, adapter, watcher, _ = _make_session()
        stop_event = threading.Event()
        stop_event.set()  # Stop immediately

        summary = session.run(stop_event)

        assert summary["mode"] == "v3"
        assert "tracks_played" in summary
        assert "cache_hits" in summary
        assert "frames_sent" in summary
        adapter.activate.assert_called_once_with(brightness=100)
        adapter.deactivate.assert_called_once()

    def test_thread_safe_runtime_swap(self):
        """Runtime can be swapped from watcher thread while main thread reads."""
        timeline = _make_timeline()
        cache = MagicMock()
        cache.has = MagicMock(return_value=True)
        cache.get = MagicMock(return_value=timeline)

        session, _, _, _ = _make_session(cache=cache)

        # Simulate concurrent access
        def swap_runtime():
            for i in range(50):
                session._on_track_changed(
                    _make_track(track_id=f"track_{i}"),
                    None,
                )

        def read_runtime():
            for _ in range(50):
                with session._runtime_lock:
                    _ = session._runtime

        t1 = threading.Thread(target=swap_runtime)
        t2 = threading.Thread(target=read_runtime)
        t1.start()
        t2.start()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)

        # No crash = success. Verify final state is consistent
        assert session._tracks_played == 50

    def test_callback_wiring(self):
        """run() chains callbacks onto the watcher."""
        session, adapter, watcher, _ = _make_session()
        stop_event = threading.Event()
        stop_event.set()

        session.run(stop_event)

        # After run() completes, original callbacks should be restored
        assert watcher._on_track_changed is None
        assert watcher._on_playback_state_changed is None
        assert watcher._on_queue_updated is None


# ---------------------------------------------------------------------------
# D5.3 — CLI + Session Integration Tests
# ---------------------------------------------------------------------------


class TestCLIAndIntegration:
    """CLI arg parsing, validation, and session wiring."""

    def test_cli_v3_flag_parsed(self):
        """--v3 flag is parsed as True."""
        from dreamsync.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "session", "--config", "devices.yaml", "--spotify", "--v3",
        ])
        assert args.v3 is True

    def test_cli_v3_default_false(self):
        """--v3 defaults to False when not specified."""
        from dreamsync.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "session", "--config", "devices.yaml",
        ])
        assert args.v3 is False

    def test_cli_cache_dir_parsed(self):
        """--cache-dir is parsed correctly."""
        from dreamsync.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "session", "--config", "devices.yaml", "--cache-dir", "/tmp/my-cache",
        ])
        assert args.cache_dir == "/tmp/my-cache"

    def test_cli_cache_dir_default(self):
        """--cache-dir defaults to ~/.dreamsync/cache."""
        from dreamsync.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "session", "--config", "devices.yaml",
        ])
        assert args.cache_dir == "~/.dreamsync/cache"

    def test_run_v3_session_wiring(self):
        """run_v3_session creates ShowCache and SpotifyShowSession, calls run()."""
        from dreamsync.v3_session import run_v3_session

        adapter = MagicMock()
        adapter.activate = MagicMock()
        adapter.deactivate = MagicMock()
        adapter.send_frame = MagicMock(return_value=True)

        watcher = MagicMock()
        watcher._on_track_changed = None
        watcher._on_playback_state_changed = None
        watcher._on_queue_updated = None

        stop_event = threading.Event()
        stop_event.set()  # Stop immediately

        summary = run_v3_session(
            adapter,
            watcher,
            cache_dir="~/.dreamsync/cache",
            profile=None,
            capture_dir="captured_songs",
            stop_event=stop_event,
            debug=False,
        )

        assert summary["mode"] == "v3"
        assert "tracks_played" in summary
        adapter.activate.assert_called_once()
        adapter.deactivate.assert_called_once()
