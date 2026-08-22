from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from dreamsync.gui.settings import GuiSettingsStore
from dreamsync.gui.settings import GuiSettings
from dreamsync.gui.models.capture_settings import CaptureSettings, LearnedLiveSettings


class GuiSettingsRecoveryTests(unittest.TestCase):
    def test_library_roots_round_trip(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "gui-settings.json"
            store = GuiSettingsStore(path)
            expected = CaptureSettings(
                capture_dir="D:/DreamSync/temp",
                captured_audio_root="D:/DreamSync/audio",
                analysis_root="D:/DreamSync/analysis",
                compiled_show_root="D:/DreamSync/shows",
                temp_capture_root="D:/DreamSync/temp",
                temp_retention_hours=12,
                device_pattern="dreamsync_queue_capture.monitor",
            )
            store.save(GuiSettings(capture_settings=expected))

            loaded = store.load().capture_settings

        self.assertEqual(loaded, expected)

    def test_learned_live_settings_round_trip(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "gui-settings.json"
            store = GuiSettingsStore(path)
            expected = LearnedLiveSettings(
                enabled=True,
                learning_enabled=False,
                capture_device_pattern="Loopback",
                mp3_retention_policy="delete_after_verified_compile",
                retained_mp3_limit=3,
                diagnostic_logging=True,
            )
            store.save(GuiSettings(
                live_start_mode="spotify_learned_live",
                learned_live_settings=expected,
            ))
            loaded = store.load()
        self.assertEqual(loaded.learned_live_settings, expected)
        self.assertEqual(loaded.live_start_mode, "spotify_learned_live")
    def test_incomplete_saved_rotation_recovers_to_active_profile(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "gui-settings.json"
            path.write_text(
                json.dumps(
                    {
                        "reactive_settings": {
                            "profile_strategy": "smart_rotation",
                            "rotation_profiles": [],
                            "show_palette_set": "christmas",
                        }
                    }
                ),
                encoding="utf-8",
            )

            settings = GuiSettingsStore(path).load().reactive_settings

        self.assertEqual(settings.profile_strategy, "active_profile")
        self.assertEqual(settings.show_palette_set, "christmas")

    def test_structure_similarity_controls_load_from_saved_settings(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "gui-settings.json"
            path.write_text(
                json.dumps(
                    {
                        "reactive_settings": {
                            "structure_similarity_enabled": True,
                            "structure_similarity_diagnostics": True,
                            "structure_similarity_shadow_mode": False,
                            "structure_bar_actions_enabled": True,
                            "structure_phrase_actions_enabled": True,
                            "structure_section_actions_enabled": True,
                        }
                    }
                ),
                encoding="utf-8",
            )

            settings = GuiSettingsStore(path).load().reactive_settings

        self.assertTrue(settings.structure_similarity_enabled)
        self.assertTrue(settings.structure_similarity_diagnostics)
        self.assertFalse(settings.structure_similarity_shadow_mode)
        self.assertTrue(settings.structure_bar_actions_enabled)
        self.assertTrue(settings.structure_phrase_actions_enabled)
        self.assertTrue(settings.structure_section_actions_enabled)


if __name__ == "__main__":
    unittest.main()
