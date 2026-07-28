from __future__ import annotations

from pathlib import Path
import unittest

from dreamsync.gui.services.show_service import ShowService
from dreamsync.show.models import Show, ShowCue, ShowTimeline, ShowTrack


def _timeline(audio_path: Path) -> ShowTimeline:
    return ShowTimeline(
        song_path=str(audio_path),
        duration=2.0,
        bpm=120.0,
        time_signature=4,
        beat_times=(0.0, 0.5, 1.0, 1.5),
        downbeat_times=(0.0,),
        cues=(
            ShowCue(
                t=0.0,
                render_mode="pulse",
                color_palette=("#112233", "#445566"),
                intensity=0.6,
                speed=1.0,
                params={},
                transition="cut",
                transition_beats=0,
            ),
        ),
        metadata={"track_name": audio_path.stem},
    )


class SavedShowLoaderTests(unittest.TestCase):
    def test_load_precompiled_track_keeps_legacy_timeline_bound_to_selected_file(self):
        with self.subTest("legacy single-track timeline"):
            from tempfile import TemporaryDirectory

            with TemporaryDirectory() as raw_directory:
                directory = Path(raw_directory)
                audio_path = directory / "song.mp3"
                audio_path.touch()
                show_path = directory / "alternate-lightshow.show.json"
                _timeline(audio_path).to_json(show_path)

                show = ShowService().load_precompiled_track_show(show_path)

                self.assertEqual(len(show.tracks), 1)
                self.assertEqual(show.tracks[0].audio_path, str(audio_path))
                self.assertTrue(show.tracks[0].is_compiled)
                self.assertEqual(show.name, "alternate-lightshow")

    def test_show_and_track_loaders_enforce_their_distinct_formats(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            first_audio = directory / "first.mp3"
            second_audio = directory / "second.mp3"
            first_audio.touch()
            second_audio.touch()
            show = Show(
                name="Two Track Set",
                tracks=(
                    ShowTrack(str(first_audio), _timeline(first_audio)),
                    ShowTrack(str(second_audio), _timeline(second_audio)),
                ),
                metadata={},
            )
            show_path = directory / "two-track-set.show.json"
            show.to_json(show_path)
            legacy_path = directory / "legacy.show.json"
            _timeline(first_audio).to_json(legacy_path)
            service = ShowService()

            loaded = service.load_show_manifest(show_path)

            self.assertEqual(loaded.name, "Two Track Set")
            self.assertEqual(len(loaded.tracks), 2)
            with self.assertRaisesRegex(ValueError, "multiple tracks"):
                service.load_precompiled_track_show(show_path)
            with self.assertRaisesRegex(ValueError, "Cue Compiled Track"):
                service.load_show_manifest(legacy_path)
