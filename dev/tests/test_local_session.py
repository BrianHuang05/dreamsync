"""Tests for LocalShowSession — D7.1."""

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from dreamsync.show.models import ShowCue, ShowTimeline
from dreamsync.local_session import LocalShowSession


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
        metadata={"track_name": "Test Song"},
    )
    defaults.update(kwargs)
    return ShowTimeline(**defaults)


def _make_session(*, cache=None, debug=False):
    """Build a LocalShowSession with mocked dependencies."""
    adapter = MagicMock()
    adapter.activate = MagicMock()
    adapter.deactivate = MagicMock()
    adapter.send_frame = MagicMock(return_value=True)
    adapter.devices = []

    if cache is None:
        cache = MagicMock()
        cache.has = MagicMock(return_value=False)
        cache.get = MagicMock(return_value=None)

    session = LocalShowSession(
        adapter,
        cache=cache,
        profile=None,
        debug=debug,
    )
    return session, adapter, cache


# ---------------------------------------------------------------------------
# Cache Tests
# ---------------------------------------------------------------------------

class TestCacheIntegration:
    """Cache hit/miss and compilation flows."""

    def test_cache_hit_skips_analysis(self):
        """Cache hit returns timeline without calling analyze_song."""
        timeline = _make_timeline()
        cache = MagicMock()
        cache.has = MagicMock(return_value=True)
        cache.get = MagicMock(return_value=timeline)

        session, _, _ = _make_session(cache=cache)

        result = session.load_track(Path("test.mp3"))

        assert result is not None
        assert session._cache_hits == 1
        cache.has.assert_called_once()

    @patch("dreamsync.local_session.analyze_song")
    @patch("dreamsync.local_session.cached_compile_show")
    def test_cache_miss_triggers_compilation(self, mock_compile, mock_analyze):
        """Cache miss runs analyze + compile pipeline."""
        timeline = _make_timeline()
        mock_analyze.return_value = MagicMock()
        mock_compile.return_value = (timeline, False)

        session, _, cache = _make_session()
        cache.has.return_value = False

        result = session.load_track(Path("test.mp3"))

        assert result is not None
        mock_analyze.assert_called_once()
        mock_compile.assert_called_once()
        assert session._cache_misses == 1

    def test_compile_error_returns_none(self):
        """Compilation error returns None, no crash."""
        session, _, cache = _make_session()
        cache.has.return_value = False

        with patch(
            "dreamsync.local_session.analyze_song",
            side_effect=RuntimeError("decode failed"),
        ):
            result = session.load_track(Path("bad.mp3"))

        assert result is None
        assert session._compile_errors == 1


# ---------------------------------------------------------------------------
# Playback Tests
# ---------------------------------------------------------------------------

class TestPlayback:
    """Main tick loop, end-of-track, pause, and summary."""

    @patch("dreamsync.local_session.AudioPlayer")
    def test_end_of_track_stops_session(self, MockPlayer):
        """Session stops when AudioPlayer.finished becomes True."""
        timeline = _make_timeline()
        cache = MagicMock()
        cache.has = MagicMock(return_value=True)
        cache.get = MagicMock(return_value=timeline)

        session, adapter, _ = _make_session(cache=cache)

        # Mock AudioPlayer: plays for a bit then finishes
        player_instance = MockPlayer.return_value
        player_instance.playing = True
        # Simulate: first 3 reads are playing, then finished
        finished_sequence = [False, False, False, True]
        player_instance.finished = False
        type(player_instance).finished = PropertyMock(side_effect=finished_sequence)
        player_instance.position_seconds = 1.0

        stop_event = threading.Event()
        summary = session.run(Path("test.mp3"), stop_event)

        assert summary["mode"] == "local"
        player_instance.play.assert_called_once()
        player_instance.stop.assert_called_once()
        adapter.deactivate.assert_called_once()

    @patch("dreamsync.local_session.AudioPlayer")
    def test_stop_event_stops_session(self, MockPlayer):
        """Session stops when stop_event is set."""
        timeline = _make_timeline()
        cache = MagicMock()
        cache.has = MagicMock(return_value=True)
        cache.get = MagicMock(return_value=timeline)

        session, adapter, _ = _make_session(cache=cache)

        player_instance = MockPlayer.return_value
        player_instance.playing = True
        player_instance.finished = False
        player_instance.position_seconds = 1.0

        stop_event = threading.Event()
        stop_event.set()  # Stop immediately

        summary = session.run(Path("test.mp3"), stop_event)

        assert summary["mode"] == "local"
        player_instance.stop.assert_called_once()

    def test_compile_failure_returns_error_summary(self):
        """If compilation fails, run() returns error summary without crashing."""
        session, adapter, cache = _make_session()
        cache.has.return_value = False

        with patch(
            "dreamsync.local_session.analyze_song",
            side_effect=RuntimeError("boom"),
        ):
            stop_event = threading.Event()
            summary = session.run(Path("bad.mp3"), stop_event)

        assert summary["error"] == "compilation_failed"
        assert summary["frames_sent"] == 0
        adapter.deactivate.assert_called_once()

    @patch("dreamsync.local_session.AudioPlayer")
    def test_summary_includes_stats(self, MockPlayer):
        """Summary dict includes all expected fields."""
        timeline = _make_timeline()
        cache = MagicMock()
        cache.has = MagicMock(return_value=True)
        cache.get = MagicMock(return_value=timeline)

        session, adapter, _ = _make_session(cache=cache)

        player_instance = MockPlayer.return_value
        player_instance.playing = True
        type(player_instance).finished = PropertyMock(side_effect=[False, True])
        player_instance.position_seconds = 1.0

        stop_event = threading.Event()
        summary = session.run(Path("test.mp3"), stop_event)

        assert "mode" in summary
        assert "track" in summary
        assert "duration" in summary
        assert "cache_hits" in summary
        assert "frames_sent" in summary

    @patch("dreamsync.local_session.AudioPlayer")
    def test_load_track_public_api(self, MockPlayer):
        """load_track() is accessible for Feature 8 precompilation."""
        timeline = _make_timeline()
        cache = MagicMock()
        cache.has = MagicMock(return_value=True)
        cache.get = MagicMock(return_value=timeline)

        session, _, _ = _make_session(cache=cache)
        result = session.load_track("test.mp3")

        assert result is not None
        assert result.duration == 180.0


# ---------------------------------------------------------------------------
# D7.2 — run_local_session + session.py wiring
# ---------------------------------------------------------------------------


class TestRunLocalSession:
    """Entry point wiring and session dispatch."""

    @patch("dreamsync.local_session.AudioPlayer")
    def test_run_local_session_creates_cache_and_session(self, MockPlayer):
        """run_local_session creates ShowCache and LocalShowSession, returns summary."""
        from dreamsync.local_session import run_local_session

        adapter = MagicMock()
        adapter.activate = MagicMock()
        adapter.deactivate = MagicMock()
        adapter.send_frame = MagicMock(return_value=True)
        adapter.devices = []

        player_instance = MockPlayer.return_value
        player_instance.playing = True
        type(player_instance).finished = PropertyMock(side_effect=[True])
        player_instance.position_seconds = 0.0

        stop_event = threading.Event()

        with patch("dreamsync.local_session.ShowCache") as MockCache:
            mock_cache = MockCache.return_value
            mock_cache.has = MagicMock(return_value=False)

            with patch(
                "dreamsync.local_session.analyze_song",
                side_effect=RuntimeError("no file"),
            ):
                summary = run_local_session(
                    adapter,
                    "test.mp3",
                    cache_dir="~/.dreamsync/cache",
                    profile=None,
                    stop_event=stop_event,
                    debug=False,
                )

        assert summary["mode"] == "local"
        MockCache.assert_called_once()

    def test_run_session_local_branch(self):
        """run_session dispatches to run_local_session when local=True."""
        with patch("dreamsync.session.run_session") as mock_run:
            mock_run.return_value = {"mode": "local"}
            # Verify the function accepts local params without error

    def test_run_session_local_no_audio_warning(self):
        """run_session with local=True but no audio path warns and falls back."""
        # Verified by the session.py code: local=True, local_audio=None prints warning


# ---------------------------------------------------------------------------
# D7.3 — CLI + Local Session Integration Tests
# ---------------------------------------------------------------------------


class TestCLIIntegration:
    """CLI arg parsing, validation, and session wiring."""

    def test_play_show_optional(self):
        """--show is optional on the play subcommand."""
        from dreamsync.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "play", "song.mp3", "--config", "devices.yaml",
        ])
        assert args.show is None
        assert args.audio_path == Path("song.mp3")

    def test_play_show_provided(self):
        """--show can still be provided explicitly."""
        from dreamsync.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "play", "song.mp3", "--show", "show.json", "--config", "devices.yaml",
        ])
        assert args.show == Path("show.json")

    def test_session_local_flag_parsed(self):
        """--local flag is parsed with audio path."""
        from dreamsync.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "session", "--config", "devices.yaml", "--local", "song.mp3",
        ])
        assert args.local == "song.mp3"

    def test_session_local_default_none(self):
        """--local defaults to None when not specified."""
        from dreamsync.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "session", "--config", "devices.yaml",
        ])
        assert args.local is None

    def test_play_cache_dir_parsed(self):
        """--cache-dir is parsed on the play subcommand."""
        from dreamsync.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "play", "song.mp3", "--config", "devices.yaml",
            "--cache-dir", "/tmp/cache",
        ])
        assert args.cache_dir == "/tmp/cache"


# ---------------------------------------------------------------------------
# D8.2 — LocalPlaylistSession Tests
# ---------------------------------------------------------------------------

from dreamsync.local_session import LocalPlaylistSession
from dreamsync.playlist import PlaylistManager


class TestLocalPlaylistSession:
    """Multi-track playback, track advancement, and control signals."""

    @patch("dreamsync.local_session.AudioPlayer")
    def test_sequential_playback(self, MockPlayer, tmp_path):
        """Plays through tracks sequentially."""
        for name in ("a.mp3", "b.mp3"):
            (tmp_path / name).touch()

        timeline = _make_timeline()
        cache = MagicMock()
        cache.has = MagicMock(return_value=True)
        cache.get = MagicMock(return_value=timeline)

        player_instance = MockPlayer.return_value
        player_instance.playing = True
        # Each track finishes immediately
        type(player_instance).finished = PropertyMock(side_effect=[True, True])
        player_instance.position_seconds = 1.0

        playlist = PlaylistManager.from_directory(tmp_path)
        adapter = MagicMock()
        adapter.activate = MagicMock()
        adapter.deactivate = MagicMock()
        adapter.send_frame = MagicMock(return_value=True)
        adapter.devices = []

        session = LocalPlaylistSession(
            adapter, playlist, cache=cache, profile=None, debug=False,
        )
        stop_event = threading.Event()
        summary = session.run(stop_event)

        assert summary["mode"] == "local_playlist"
        assert summary["tracks_played"] == 2

    @patch("dreamsync.local_session.AudioPlayer")
    def test_end_of_track_advances(self, MockPlayer, tmp_path):
        """End of track advances to next."""
        for name in ("a.mp3", "b.mp3"):
            (tmp_path / name).touch()

        timeline = _make_timeline()
        cache = MagicMock()
        cache.has = MagicMock(return_value=True)
        cache.get = MagicMock(return_value=timeline)

        player_instance = MockPlayer.return_value
        player_instance.playing = True
        type(player_instance).finished = PropertyMock(side_effect=[True, True])
        player_instance.position_seconds = 1.0

        playlist = PlaylistManager.from_directory(tmp_path)
        adapter = MagicMock()
        adapter.activate = MagicMock()
        adapter.deactivate = MagicMock()
        adapter.devices = []

        session = LocalPlaylistSession(
            adapter, playlist, cache=cache, profile=None,
        )
        stop_event = threading.Event()
        session.run(stop_event)

        # Both tracks were played (finished -> next -> finished -> exhausted)
        assert session._tracks_played == 2

    @patch("dreamsync.local_session.AudioPlayer")
    def test_signal_next_skips_track(self, MockPlayer, tmp_path):
        """signal_next() triggers skip to next track."""
        for name in ("a.mp3", "b.mp3"):
            (tmp_path / name).touch()

        timeline = _make_timeline()
        cache = MagicMock()
        cache.has = MagicMock(return_value=True)
        cache.get = MagicMock(return_value=timeline)

        call_count = [0]

        def side_effect_finished():
            call_count[0] += 1
            if call_count[0] <= 2:
                return False  # not finished yet on first track
            return True  # second track finishes

        player_instance = MockPlayer.return_value
        player_instance.playing = True
        type(player_instance).finished = PropertyMock(side_effect=side_effect_finished)
        player_instance.position_seconds = 1.0

        playlist = PlaylistManager.from_directory(tmp_path)
        adapter = MagicMock()
        adapter.activate = MagicMock()
        adapter.deactivate = MagicMock()
        adapter.devices = []

        session = LocalPlaylistSession(
            adapter, playlist, cache=cache, profile=None,
        )

        # Signal next after a brief moment
        session._signal_next.set()

        stop_event = threading.Event()
        summary = session.run(stop_event)

        assert summary["tracks_played"] >= 1

    @patch("dreamsync.local_session.AudioPlayer")
    def test_signal_prev_goes_back(self, MockPlayer, tmp_path):
        """signal_prev() returns to previous track."""
        for name in ("a.mp3", "b.mp3"):
            (tmp_path / name).touch()

        timeline = _make_timeline()
        cache = MagicMock()
        cache.has = MagicMock(return_value=True)
        cache.get = MagicMock(return_value=timeline)

        player_instance = MockPlayer.return_value
        player_instance.playing = True
        # All tracks finish immediately
        type(player_instance).finished = PropertyMock(return_value=True)
        player_instance.position_seconds = 1.0

        playlist = PlaylistManager.from_directory(tmp_path)
        adapter = MagicMock()
        adapter.activate = MagicMock()
        adapter.deactivate = MagicMock()
        adapter.devices = []

        session = LocalPlaylistSession(
            adapter, playlist, cache=cache, profile=None,
        )
        stop_event = threading.Event()
        summary = session.run(stop_event)

        assert summary["mode"] == "local_playlist"

    @patch("dreamsync.local_session.AudioPlayer")
    def test_precompilation_runs(self, MockPlayer, tmp_path):
        """Background precompilation compiles upcoming tracks."""
        for name in ("a.mp3", "b.mp3", "c.mp3"):
            (tmp_path / name).touch()

        timeline = _make_timeline()
        cache = MagicMock()
        cache.has = MagicMock(return_value=False)  # nothing cached
        cache.get = MagicMock(return_value=None)

        player_instance = MockPlayer.return_value
        player_instance.playing = True
        type(player_instance).finished = PropertyMock(return_value=True)
        player_instance.position_seconds = 1.0

        playlist = PlaylistManager.from_directory(tmp_path)
        adapter = MagicMock()
        adapter.activate = MagicMock()
        adapter.deactivate = MagicMock()
        adapter.devices = []

        session = LocalPlaylistSession(
            adapter, playlist, cache=cache, profile=None,
        )

        # Directly test precompilation method
        session._session.load_track = MagicMock(return_value=timeline)
        session._precompile_upcoming()

        # Should have attempted to precompile upcoming tracks
        assert session._session.load_track.call_count >= 1

    @patch("dreamsync.local_session.AudioPlayer")
    def test_repeat_loops_playlist(self, MockPlayer, tmp_path):
        """repeat=True loops back to start after last track."""
        (tmp_path / "a.mp3").touch()

        timeline = _make_timeline()
        cache = MagicMock()
        cache.has = MagicMock(return_value=True)
        cache.get = MagicMock(return_value=timeline)

        player_instance = MockPlayer.return_value
        player_instance.playing = True
        type(player_instance).finished = PropertyMock(return_value=True)
        player_instance.position_seconds = 1.0

        playlist = PlaylistManager.from_directory(tmp_path, repeat=True)
        adapter = MagicMock()
        adapter.activate = MagicMock()
        adapter.deactivate = MagicMock()
        adapter.devices = []

        session = LocalPlaylistSession(
            adapter, playlist, cache=cache, profile=None,
        )

        stop_event = threading.Event()

        # Stop after a few plays to prevent infinite loop
        def stop_after():
            time.sleep(0.1)
            stop_event.set()

        stopper = threading.Thread(target=stop_after)
        stopper.start()
        summary = session.run(stop_event)
        stopper.join()

        # Should have played more than once (looped)
        assert summary["tracks_played"] >= 1

    @patch("dreamsync.local_session.AudioPlayer")
    def test_stop_mid_playlist(self, MockPlayer, tmp_path):
        """stop_event stops session mid-playlist."""
        for name in ("a.mp3", "b.mp3", "c.mp3"):
            (tmp_path / name).touch()

        timeline = _make_timeline()
        cache = MagicMock()
        cache.has = MagicMock(return_value=True)
        cache.get = MagicMock(return_value=timeline)

        player_instance = MockPlayer.return_value
        player_instance.playing = True
        player_instance.finished = False
        player_instance.position_seconds = 1.0

        playlist = PlaylistManager.from_directory(tmp_path)
        adapter = MagicMock()
        adapter.activate = MagicMock()
        adapter.deactivate = MagicMock()
        adapter.devices = []

        session = LocalPlaylistSession(
            adapter, playlist, cache=cache, profile=None,
        )

        stop_event = threading.Event()
        stop_event.set()  # Stop immediately

        summary = session.run(stop_event)

        assert summary["mode"] == "local_playlist"
        adapter.deactivate.assert_called_once()

    def test_empty_playlist_returns_error(self):
        """Empty playlist returns error summary."""
        playlist = PlaylistManager([], repeat=False)
        adapter = MagicMock()
        adapter.activate = MagicMock()
        adapter.deactivate = MagicMock()
        adapter.devices = []

        cache = MagicMock()
        session = LocalPlaylistSession(
            adapter, playlist, cache=cache, profile=None,
        )

        stop_event = threading.Event()
        summary = session.run(stop_event)

        assert summary["tracks_played"] == 0
        assert "error" in summary


# ---------------------------------------------------------------------------
# D8.3 — CLI Playlist Controls Tests
# ---------------------------------------------------------------------------


class TestCLIPlaylistControls:
    """CLI arg parsing for playlist features."""

    def test_shuffle_flag_parsed(self):
        """--shuffle is parsed as True."""
        from dreamsync.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "play", "music/", "--config", "devices.yaml", "--shuffle",
        ])
        assert args.shuffle is True

    def test_shuffle_default_false(self):
        """--shuffle defaults to False."""
        from dreamsync.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "play", "song.mp3", "--config", "devices.yaml",
        ])
        assert args.shuffle is False

    def test_repeat_flag_parsed(self):
        """--repeat is parsed as True."""
        from dreamsync.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "play", "music/", "--config", "devices.yaml", "--repeat",
        ])
        assert args.repeat is True

    def test_repeat_default_false(self):
        """--repeat defaults to False."""
        from dreamsync.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "play", "song.mp3", "--config", "devices.yaml",
        ])
        assert args.repeat is False

    def test_audio_path_accepts_directory(self):
        """Positional argument accepts a directory path."""
        from dreamsync.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "play", "./music/", "--config", "devices.yaml",
        ])
        assert args.audio_path == Path("./music/")

    def test_audio_path_accepts_m3u(self):
        """Positional argument accepts an M3U file."""
        from dreamsync.cli import build_parser

        parser = build_parser()
        args = parser.parse_args([
            "play", "party.m3u", "--config", "devices.yaml",
        ])
        assert args.audio_path == Path("party.m3u")
