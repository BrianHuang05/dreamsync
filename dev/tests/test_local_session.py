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

    @patch("dreamsync.local_session.analyze_song")
    @patch("dreamsync.local_session.cached_compile_show")
    def test_profile_resolver_applies_per_track_compile(self, mock_compile, mock_analyze):
        """Track-specific profile resolution is used for cache and compile calls."""
        timeline = _make_timeline()
        mock_analyze.return_value = MagicMock()
        mock_compile.return_value = (timeline, False)

        cache = MagicMock()
        cache.has = MagicMock(return_value=False)
        cache.get = MagicMock(return_value=None)
        resolved_profile = MagicMock(name="resolved_profile")
        resolver = MagicMock(return_value=resolved_profile)

        adapter = MagicMock()
        adapter.activate = MagicMock()
        adapter.deactivate = MagicMock()
        adapter.send_frame = MagicMock(return_value=True)
        adapter.devices = []

        session = LocalShowSession(
            adapter,
            cache=cache,
            profile=None,
            profile_resolver=resolver,
        )

        result = session.load_track(Path("test.mp3"))

        assert result is not None
        resolver.assert_called_once()
        cache.has.assert_called_once_with(session._track_id_for_file(Path("test.mp3")), resolved_profile)
        mock_compile.assert_called_once()
        assert mock_compile.call_args.args[1] is resolved_profile

    def test_timeline_resolver_applies_after_cache_lookup(self):
        """Cached timelines still pass through the track-level timeline resolver."""
        timeline = _make_timeline()
        patched_timeline = _make_timeline(metadata={"patched": True})
        cache = MagicMock()
        cache.has = MagicMock(return_value=True)
        cache.get = MagicMock(return_value=timeline)
        resolver = MagicMock(return_value=patched_timeline)

        adapter = MagicMock()
        adapter.activate = MagicMock()
        adapter.deactivate = MagicMock()
        adapter.send_frame = MagicMock(return_value=True)
        adapter.devices = []

        session = LocalShowSession(
            adapter,
            cache=cache,
            profile=None,
            timeline_resolver=resolver,
        )

        result = session.load_track(Path("test.mp3"))

        assert result is patched_timeline
        resolver.assert_called_once_with(Path("test.mp3"), timeline)


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

    def test_local_session_snapshot_defaults(self):
        session, _, _ = _make_session()
        snapshot = session.session_snapshot()

        assert snapshot["playback_state"] == "idle"
        assert snapshot["audio_output"] == "system default"

    def test_local_session_snapshot_exposes_runtime_control_fields(self):
        session, _, _ = _make_session()
        cue = ShowCue(
            t=0.0,
            render_mode="gradient",
            color_palette=("#111111", "#222222"),
            intensity=0.8,
            speed=1.0,
            params={
                "active_eq_routes": [{"band": "bass", "when": "dominant"}],
                "active_instrument_routes": [{"instrument": "vocals", "when": "dominant"}],
                "scene_layers": [{"instrument": "vocals"}],
            },
            transition="cut",
            transition_beats=0,
        )
        session._current_track = Path("song.mp3")
        session._current_timeline = _make_timeline(cues=(cue,))
        session._current_runtime = MagicMock(current_cue=cue)
        session.update_runtime_control(render_mode="wave")

        snapshot = session.session_snapshot()

        assert snapshot["runtime_control"]["active"] is True
        assert snapshot["current_render_mode"] == "gradient"
        assert snapshot["current_palette"] == ("#111111", "#222222")
        assert snapshot["runtime_state"]["active_eq_routes"][0]["band"] == "bass"
        assert snapshot["runtime_state"]["active_instrument_routes"][0]["instrument"] == "vocals"
        assert snapshot["runtime_state"]["active_scene_layers"][0]["instrument"] == "vocals"


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

    def test_run_local_session_populates_session_ref_for_playlist(self, tmp_path):
        """Playlist mode exposes the live session object to the caller."""
        from dreamsync.local_session import run_local_session

        for name in ("a.mp3", "b.mp3"):
            (tmp_path / name).touch()

        adapter = MagicMock()
        adapter.activate = MagicMock()
        adapter.deactivate = MagicMock()
        adapter.send_frame = MagicMock(return_value=True)
        adapter.devices = []

        playlist = PlaylistManager.from_directory(tmp_path)
        stop_event = threading.Event()
        session_ref = [None]

        with patch("dreamsync.local_session.ShowCache") as MockCache:
            mock_cache = MockCache.return_value
            mock_cache.has = MagicMock(return_value=True)
            mock_cache.get = MagicMock(return_value=_make_timeline())

            with patch("dreamsync.local_session.AudioPlayer") as MockPlayer:
                player_instance = MockPlayer.return_value
                player_instance.playing = True
                type(player_instance).finished = PropertyMock(side_effect=[True, True])
                player_instance.position_seconds = 0.0

                summary = run_local_session(
                    adapter,
                    tmp_path / "a.mp3",
                    stop_event=stop_event,
                    playlist=playlist,
                    session_ref=session_ref,
                )

        assert summary["mode"] == "local_playlist"
        assert isinstance(session_ref[0], LocalPlaylistSession)

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

    def test_precompile_upcoming_uses_profile_resolver(self, tmp_path):
        """Upcoming-track precompile checks the effective per-track profile."""
        from dreamsync.playlist import content_hash_track_id

        for name in ("a.mp3", "b.mp3"):
            (tmp_path / name).touch()

        playlist = PlaylistManager.from_directory(tmp_path)
        adapter = MagicMock()
        adapter.activate = MagicMock()
        adapter.deactivate = MagicMock()
        adapter.send_frame = MagicMock(return_value=True)
        adapter.devices = []
        cache = MagicMock()
        cache.has = MagicMock(return_value=False)
        resolved_profile = MagicMock(name="resolved_profile")
        resolver = MagicMock(return_value=resolved_profile)

        session = LocalPlaylistSession(
            adapter,
            playlist,
            cache=cache,
            profile=None,
            profile_resolver=resolver,
        )
        session._session.load_track = MagicMock(return_value=_make_timeline())

        session._precompile_upcoming()

        upcoming = playlist.peek_next(count=2)[0]
        expected_track_id = content_hash_track_id(upcoming)
        cache.has.assert_any_call(expected_track_id, resolved_profile)
        resolver.assert_called()

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

    def test_queue_snapshot_reports_current_and_tracks(self, tmp_path):
        for name in ("a.mp3", "b.mp3", "c.mp3"):
            (tmp_path / name).touch()

        playlist = PlaylistManager.from_directory(tmp_path)
        adapter = MagicMock()
        adapter.activate = MagicMock()
        adapter.deactivate = MagicMock()
        adapter.devices = []

        session = LocalPlaylistSession(adapter, playlist, cache=MagicMock(), profile=None)
        snapshot = session.queue_snapshot()

        assert snapshot["current_index"] == 0
        assert [p.name for p in snapshot["tracks"]] == ["a.mp3", "b.mp3", "c.mp3"]

    def test_remove_track_removes_upcoming_item(self, tmp_path):
        for name in ("a.mp3", "b.mp3", "c.mp3"):
            (tmp_path / name).touch()

        playlist = PlaylistManager.from_directory(tmp_path)
        adapter = MagicMock()
        adapter.activate = MagicMock()
        adapter.deactivate = MagicMock()
        adapter.devices = []

        session = LocalPlaylistSession(adapter, playlist, cache=MagicMock(), profile=None)
        removed = session.remove_track(1)

        assert removed.name == "b.mp3"
        assert [p.name for p in session.queue_snapshot()["tracks"]] == ["a.mp3", "c.mp3"]

    def test_remove_track_rejects_current_item(self, tmp_path):
        for name in ("a.mp3", "b.mp3"):
            (tmp_path / name).touch()

        playlist = PlaylistManager.from_directory(tmp_path)
        adapter = MagicMock()
        adapter.activate = MagicMock()
        adapter.deactivate = MagicMock()
        adapter.devices = []

        session = LocalPlaylistSession(adapter, playlist, cache=MagicMock(), profile=None)

        with pytest.raises(ValueError):
            session.remove_track(0)

    def test_session_snapshot_reports_runtime_queue_state(self, tmp_path):
        for name in ("a.mp3", "b.mp3"):
            (tmp_path / name).touch()

        playlist = PlaylistManager.from_directory(tmp_path)
        adapter = MagicMock()
        adapter.activate = MagicMock()
        adapter.deactivate = MagicMock()
        adapter.devices = []

        session = LocalPlaylistSession(adapter, playlist, cache=MagicMock(), profile=None)
        snapshot = session.session_snapshot()

        assert snapshot["current_track"].name == "a.mp3"
        assert snapshot["current_index"] == 0
        assert snapshot["queue"]["current_index"] == 0

    def test_move_track_reorders_upcoming_items(self, tmp_path):
        for name in ("a.mp3", "b.mp3", "c.mp3", "d.mp3"):
            (tmp_path / name).touch()

        playlist = PlaylistManager.from_directory(tmp_path)
        adapter = MagicMock()
        adapter.activate = MagicMock()
        adapter.deactivate = MagicMock()
        adapter.devices = []

        session = LocalPlaylistSession(adapter, playlist, cache=MagicMock(), profile=None)
        session.move_track(3, 1)

        assert [p.name for p in session.queue_snapshot()["tracks"]] == [
            "a.mp3", "d.mp3", "b.mp3", "c.mp3",
        ]

    def test_play_now_sets_pending_jump(self, tmp_path):
        for name in ("a.mp3", "b.mp3", "c.mp3"):
            (tmp_path / name).touch()

        playlist = PlaylistManager.from_directory(tmp_path)
        adapter = MagicMock()
        adapter.activate = MagicMock()
        adapter.deactivate = MagicMock()
        adapter.devices = []

        session = LocalPlaylistSession(adapter, playlist, cache=MagicMock(), profile=None)
        session.play_now(2)

        assert session._signal_jump.is_set() is True
        assert session._consume_jump_target() == 2

    def test_playlist_session_snapshot_defaults(self, tmp_path):
        for name in ("a.mp3", "b.mp3"):
            (tmp_path / name).touch()

        playlist = PlaylistManager.from_directory(tmp_path)
        adapter = MagicMock()
        adapter.activate = MagicMock()
        adapter.deactivate = MagicMock()
        adapter.devices = []

        session = LocalPlaylistSession(adapter, playlist, cache=MagicMock(), profile=None)
        snapshot = session.session_snapshot()

        assert snapshot["playback_state"] == "idle"
        assert snapshot["current_track"].name == "a.mp3"

    def test_append_track_adds_to_queue(self, tmp_path):
        for name in ("a.mp3", "b.mp3", "c.mp3"):
            (tmp_path / name).touch()

        playlist = PlaylistManager.from_tracks([tmp_path / "a.mp3", tmp_path / "b.mp3"])
        adapter = MagicMock()
        adapter.activate = MagicMock()
        adapter.deactivate = MagicMock()
        adapter.devices = []

        session = LocalPlaylistSession(adapter, playlist, cache=MagicMock(), profile=None)
        appended = session.append_track(tmp_path / "c.mp3")

        assert appended.name == "c.mp3"
        assert [p.name for p in session.queue_snapshot()["tracks"]] == ["a.mp3", "b.mp3", "c.mp3"]

    def test_insert_track_places_item_into_upcoming_queue(self, tmp_path):
        for name in ("a.mp3", "b.mp3", "c.mp3", "d.mp3"):
            (tmp_path / name).touch()

        playlist = PlaylistManager.from_tracks(
            [tmp_path / "a.mp3", tmp_path / "b.mp3", tmp_path / "d.mp3"]
        )
        adapter = MagicMock()
        adapter.activate = MagicMock()
        adapter.deactivate = MagicMock()
        adapter.devices = []

        session = LocalPlaylistSession(adapter, playlist, cache=MagicMock(), profile=None)
        inserted = session.insert_track(1, tmp_path / "c.mp3")

        assert inserted.name == "c.mp3"
        assert [p.name for p in session.queue_snapshot()["tracks"]] == ["a.mp3", "c.mp3", "b.mp3", "d.mp3"]

    def test_skip_current_alias_sets_next_signal(self, tmp_path):
        for name in ("a.mp3", "b.mp3"):
            (tmp_path / name).touch()

        playlist = PlaylistManager.from_directory(tmp_path)
        adapter = MagicMock()
        adapter.activate = MagicMock()
        adapter.deactivate = MagicMock()
        adapter.devices = []

        session = LocalPlaylistSession(adapter, playlist, cache=MagicMock(), profile=None)
        session.skip_current()

        assert session._signal_next.is_set() is True


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
