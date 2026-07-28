from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from dreamsync.gui.settings import GuiSettingsStore


class GuiSettingsRecoveryTests(unittest.TestCase):
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
