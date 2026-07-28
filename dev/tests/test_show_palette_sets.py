from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from dreamsync.effects import EffectCycler
from dreamsync.gui.controllers.palette_controller import PaletteController
from dreamsync.gui.services.profile_service import ProfileService
from dreamsync.gui.services.session_service import SessionService
from dreamsync.mood import Mood
from dreamsync.profile import load_profile


PROFILE_YAML = """
name: Party Profile
version: 1
palettes:
  winter: ["#f5fbff", "#c7e5ff", "#8bbef0"]
  evergreen: ["#123d2b", "#286b45", "#e3b341"]
  dinner: ["#ffb36a", "#f2764e", "#fff0d5"]
show_palette_sets:
  christmas: [winter, evergreen]
moods:
  chill:
    palettes: [winter]
    effects: [{name: gradient_flow, weight: 1.0}]
  groove: {palettes: [winter]}
  hype: {palettes: [evergreen]}
  drop: {palettes: [evergreen]}
"""


def _write_profile(tmp_path: Path) -> Path:
    path = tmp_path / "party.yaml"
    path.write_text(PROFILE_YAML.strip() + "\n", encoding="utf-8")
    return path


class ShowPaletteSetTests(unittest.TestCase):
    def test_profile_loads_and_validates_show_palette_sets(self) -> None:
        with TemporaryDirectory() as directory:
            profile = load_profile(_write_profile(Path(directory)))

        self.assertEqual(profile.show_palette_sets, {"christmas": ("winter", "evergreen")})

    def test_palette_controller_saves_show_palette_set(self) -> None:
        with TemporaryDirectory() as directory:
            path = _write_profile(Path(directory))
            controller = PaletteController(ProfileService())
            controller.load(path)

            state = controller.create_show_palette_set(
                name="dinner party",
                palette_names=("dinner", "winter"),
            )
            saved = controller.save_show_palette_set()

            self.assertEqual(state.selected_show_palette_set, "dinner party")
            self.assertEqual(saved.show_palette_sets["dinner party"], ("dinner", "winter"))
            self.assertEqual(
                load_profile(path).show_palette_sets["dinner party"],
                ("dinner", "winter"),
            )

    def test_effect_cycler_holds_one_set_palette_per_song_and_updates_gradient(self) -> None:
        with TemporaryDirectory() as directory:
            profile = load_profile(_write_profile(Path(directory)))
        cycler = EffectCycler(
            profile=profile,
            show_palette_cycle=profile.show_palette_sets["christmas"],
        )

        first_song = cycler.update(Mood.CHILL, t=0.0, beat=False, bpm=100.0, energy=0.1)
        cycler.reset()
        cycler.advance_show_palette()
        second_song = cycler.update(Mood.CHILL, t=10.0, beat=False, bpm=100.0, energy=0.1)

        self.assertEqual(first_song.color_palette, profile.palettes["winter"])
        self.assertEqual(first_song.params["gradient_colors"], profile.palettes["winter"])
        self.assertEqual(second_song.color_palette, profile.palettes["evergreen"])
        self.assertEqual(second_song.params["gradient_colors"], profile.palettes["evergreen"])

    def test_effect_cycler_exposes_rotated_show_palette_queue(self) -> None:
        with TemporaryDirectory() as directory:
            profile = load_profile(_write_profile(Path(directory)))
        cycler = EffectCycler(
            profile=profile,
            show_palette_cycle=profile.show_palette_sets["christmas"],
        )

        self.assertEqual(cycler.current_palette, "winter")
        self.assertEqual(cycler.show_palette_queue, ("winter", "evergreen"))
        self.assertIsNone(cycler.seconds_until_next_cycle(12.0))

        cycler.advance_show_palette()

        self.assertEqual(cycler.current_palette, "evergreen")
        self.assertEqual(cycler.show_palette_queue, ("evergreen", "winter"))

    def test_effect_cycler_reports_remaining_timed_cycle(self) -> None:
        cycler = EffectCycler()
        cycler.update(Mood.CHILL, t=10.0, beat=False, bpm=100.0, energy=0.1)

        self.assertAlmostEqual(cycler.seconds_until_next_cycle(15.5) or 0.0, 10.5)

    def test_reactive_session_passes_selected_show_palette_set_to_live_runtime(self) -> None:
        with TemporaryDirectory() as directory:
            profile = load_profile(_write_profile(Path(directory)))
        seen: dict[str, object] = {}

        def fake_run_live_to_govee(*_args, **kwargs):
            seen.update(kwargs)
            return [], {"sent": 0, "beats": 0}

        service = SessionService()
        with patch("dreamsync.live.run_live_to_govee", side_effect=fake_run_live_to_govee):
            handle = service.start_reactive_live_session(
                profile=profile,
                show_palette_set="christmas",
            )
            handle.wait(timeout=2)

        self.assertEqual(seen["show_palette_cycle"], ("winter", "evergreen"))


if __name__ == "__main__":
    unittest.main()
