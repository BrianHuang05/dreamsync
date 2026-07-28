from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from dreamsync.gui.main_window import create_main_window
from dreamsync.gui.qt import require_qt
from dreamsync.gui.settings import GuiSettings
from dreamsync.profile_overrides import SongPaletteAssignment
from dreamsync.show.models import ShowCue, ShowTimeline


class _InMemorySongPaletteStore:
    def __init__(self) -> None:
        self.assignments: dict[str, SongPaletteAssignment] = {}

    def load(self) -> dict[str, SongPaletteAssignment]:
        return dict(self.assignments)

    def upsert(self, assignment: SongPaletteAssignment) -> dict[str, SongPaletteAssignment]:
        self.assignments[assignment.track_key] = assignment
        return self.load()


class CompiledTrackQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_cue_compiled_track_creates_selected_live_queue_entry(self) -> None:
        with TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            audio_path = directory / "track.mp3"
            audio_path.touch()
            show_path = directory / "track.show.json"
            ShowTimeline(
                song_path=str(audio_path),
                duration=1.0,
                bpm=120.0,
                time_signature=4,
                beat_times=(0.0,),
                downbeat_times=(0.0,),
                cues=(
                    ShowCue(
                        t=0.0,
                        render_mode="solid",
                        color_palette=("#112233", "#445566"),
                        intensity=1.0,
                        speed=1.0,
                        params={},
                        transition="cut",
                        transition_beats=0,
                    ),
                ),
                metadata={"show_palette": ["#112233", "#445566"]},
            ).to_json(show_path)
            palette_store = _InMemorySongPaletteStore()
            with patch(
                "dreamsync.gui.main_window.SongPaletteStore",
                return_value=palette_store,
            ):
                window = create_main_window(
                    require_qt(),
                    GuiSettings(show_directory=str(directory)),
                    config_path=Path("dev/devices-dummy.yaml"),
                )
            try:
                window.show()
                self.app.processEvents()
                button = window.findChild(QtWidgets.QPushButton, "loadSavedTrackButton")
                live_queue = window.findChild(QtWidgets.QListWidget, "localQueueList")
                selected_song = window.findChild(QtWidgets.QLabel, "selectedSongLabel")
                assigned_palette = window.findChild(QtWidgets.QLabel, "selectedAssignmentLabel")
                show_source = window.findChild(QtWidgets.QLabel, "liveShowSourceLabel")
                self.assertIsNotNone(button)
                self.assertIsNotNone(live_queue)
                self.assertIsNotNone(selected_song)
                self.assertIsNotNone(assigned_palette)
                self.assertIsNotNone(show_source)

                with patch.object(
                    QtWidgets.QFileDialog,
                    "getOpenFileName",
                    return_value=(str(show_path), "Show JSON Files (*.show.json *.json)"),
                ):
                    button.click()
                    self.app.processEvents()

                self.assertEqual(live_queue.count(), 1)
                self.assertEqual(selected_song.text(), "Selected song: track.mp3")
                self.assertEqual(assigned_palette.text(), "Assigned palette: compiled track / track")
                self.assertIn("precompiled track:", show_source.text())
                self.assertIn(str(show_path.resolve()), show_source.text())
            finally:
                window.close()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
