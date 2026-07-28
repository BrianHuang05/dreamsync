from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from dreamsync.local_session import LocalPlaylistSession, run_precompiled_show_session
from dreamsync.playlist import PlaylistManager


class CuePrecompileTests(unittest.TestCase):
    def test_precompiled_show_session_constructs_a_playlist_at_runtime(self):
        from tempfile import TemporaryDirectory
        import threading

        with TemporaryDirectory() as raw_directory:
            track = Path(raw_directory) / "track.mp3"
            track.touch()
            timeline = MagicMock()
            with patch(
                "dreamsync.local_session.run_local_session",
                return_value={"mode": "local"},
            ) as run_local_session:
                result = run_precompiled_show_session(
                    MagicMock(),
                    ((track, timeline),),
                    stop_event=threading.Event(),
                )

            self.assertEqual(result, {"mode": "local"})
            playlist = run_local_session.call_args.kwargs["playlist"]
            self.assertEqual(playlist.current, track.resolve())
            self.assertIs(run_local_session.call_args.kwargs["precompiled_timelines"][str(track.resolve())], timeline)

    def test_running_session_schedules_new_cued_track_precompile(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            track = directory / "track.mp3"
            track.touch()
            session = LocalPlaylistSession(
                MagicMock(),
                PlaylistManager.from_file(track),
                cache=MagicMock(),
                profile=None,
            )
            session._executor = MagicMock()
            session._playback_state = "playing"

            scheduled = session.request_precompile_upcoming()

            self.assertTrue(scheduled)
            session._executor.submit.assert_called_once()

    def test_idle_session_does_not_schedule_precompile(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            track = directory / "track.mp3"
            track.touch()
            session = LocalPlaylistSession(
                MagicMock(),
                PlaylistManager.from_file(track),
                cache=MagicMock(),
                profile=None,
            )
            session._executor = MagicMock()

            scheduled = session.request_precompile_upcoming()

            self.assertFalse(scheduled)
            session._executor.submit.assert_not_called()
