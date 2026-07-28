from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from dreamsync.gui.services.session_service import SessionService
from dreamsync.playlist import PlaylistManager
from dreamsync.show.models import ShowTimeline


class PlaylistHandoffTests(unittest.TestCase):
    def test_start_local_preview_uses_the_supplied_cued_playlist(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            first = directory / "first.mp3"
            second = directory / "second.mp3"
            first.touch()
            second.touch()
            playlist = PlaylistManager.from_tracks([first, second])
            service = SessionService()
            service.require_local_preview_dependencies = MagicMock()
            service.build_output_adapter = MagicMock(return_value=MagicMock())

            with patch(
                "dreamsync.gui.services.session_service.run_local_session",
                return_value={"mode": "local_playlist"},
            ) as run_local_session:
                handle = service.start_local_preview_session(first, playlist=playlist)
                handle.wait(timeout=2)

            self.assertIsNone(handle.error)
            self.assertIs(run_local_session.call_args.kwargs["playlist"], playlist)

    def test_start_local_preview_forwards_precompiled_timeline(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as raw_directory:
            track = Path(raw_directory) / "track.mp3"
            track.touch()
            timeline = MagicMock(spec=ShowTimeline)
            timelines = {str(track.resolve()): timeline}
            sources = {str(track.resolve()): "precompiled track: test.show.json"}
            service = SessionService()
            service.require_local_preview_dependencies = MagicMock()
            service.build_output_adapter = MagicMock(return_value=MagicMock())

            with patch(
                "dreamsync.gui.services.session_service.run_local_session",
                return_value={"mode": "local"},
            ) as run_local_session:
                handle = service.start_local_preview_session(
                    track,
                    precompiled_timelines=timelines,
                    precompiled_timeline_sources=sources,
                )
                handle.wait(timeout=2)

            self.assertIsNone(handle.error)
            self.assertEqual(run_local_session.call_args.kwargs["precompiled_timelines"], timelines)
            self.assertEqual(run_local_session.call_args.kwargs["precompiled_timeline_sources"], sources)
