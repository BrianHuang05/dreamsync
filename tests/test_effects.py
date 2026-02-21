import unittest

from dreamsync.effects import (
    EFFECTS,
    MOOD_EFFECTS,
    MOOD_PALETTES,
    PALETTES,
    EffectCycler,
    EffectCyclerConfig,
    EffectPreset,
)
from dreamsync.mood import Mood
from dreamsync.render import RenderMode


class EffectPresetTests(unittest.TestCase):
    def test_preset_is_frozen(self) -> None:
        p = EffectPreset(
            name="test", render_mode=RenderMode.SOLID,
            color_palette=("#ff0000",), params={},
        )
        with self.assertRaises(AttributeError):
            p.name = "other"  # type: ignore[misc]

    def test_all_effects_have_valid_render_mode(self) -> None:
        for name, preset in EFFECTS.items():
            self.assertIsInstance(preset.render_mode, RenderMode, f"{name} has invalid mode")

    def test_all_mood_effects_reference_existing_effects(self) -> None:
        for mood, pool in MOOD_EFFECTS.items():
            for effect_name, weight in pool:
                self.assertIn(effect_name, EFFECTS, f"{mood.value} references unknown effect {effect_name}")
                self.assertGreater(weight, 0.0)

    def test_all_mood_palettes_reference_existing_palettes(self) -> None:
        for mood, palette_names in MOOD_PALETTES.items():
            for pname in palette_names:
                self.assertIn(pname, PALETTES, f"{mood.value} references unknown palette {pname}")


class EffectCyclerSelectionTests(unittest.TestCase):
    """Test that effects are selected from the correct mood pool."""

    def test_chill_selects_from_chill_pool(self) -> None:
        cycler = EffectCycler(seed=42)
        preset = cycler.update(Mood.CHILL, t=0.0, beat=False, bpm=90.0, energy=0.05)
        chill_names = {name for name, _ in MOOD_EFFECTS[Mood.CHILL]}
        self.assertIn(preset.name, chill_names)

    def test_groove_selects_from_groove_pool(self) -> None:
        cycler = EffectCycler(seed=42)
        preset = cycler.update(Mood.GROOVE, t=0.0, beat=False, bpm=120.0, energy=0.15)
        groove_names = {name for name, _ in MOOD_EFFECTS[Mood.GROOVE]}
        self.assertIn(preset.name, groove_names)

    def test_hype_selects_from_hype_pool(self) -> None:
        cycler = EffectCycler(seed=42)
        preset = cycler.update(Mood.HYPE, t=0.0, beat=False, bpm=128.0, energy=0.30)
        hype_names = {name for name, _ in MOOD_EFFECTS[Mood.HYPE]}
        self.assertIn(preset.name, hype_names)

    def test_drop_always_returns_drop_blast(self) -> None:
        cycler = EffectCycler(seed=42)
        preset = cycler.update(Mood.DROP, t=0.0, beat=True, bpm=128.0, energy=0.35)
        self.assertEqual(preset.name, "drop_blast")
        self.assertEqual(preset.render_mode, RenderMode.PULSE)


class EffectCyclerTimingTests(unittest.TestCase):
    """Test time-based cycling behavior."""

    def test_cycling_after_interval(self) -> None:
        config = EffectCyclerConfig(cycle_interval=10.0)
        cycler = EffectCycler(config=config, seed=42)
        # Initial selection
        p1 = cycler.update(Mood.GROOVE, t=0.0, beat=False, bpm=120.0, energy=0.15)
        # Before interval: same effect
        p2 = cycler.update(Mood.GROOVE, t=5.0, beat=False, bpm=120.0, energy=0.15)
        self.assertEqual(p1.name, p2.name)
        # After interval: different effect (pool has >1 effects, so exclude works)
        p3 = cycler.update(Mood.GROOVE, t=11.0, beat=False, bpm=120.0, energy=0.15)
        self.assertNotEqual(p2.name, p3.name)

    def test_no_cycle_within_interval(self) -> None:
        config = EffectCyclerConfig(cycle_interval=16.0)
        cycler = EffectCycler(config=config, seed=123)
        p1 = cycler.update(Mood.CHILL, t=0.0, beat=False, bpm=90.0, energy=0.05)
        # Multiple updates within interval should return the same effect
        for t in [2.0, 4.0, 8.0, 12.0, 15.0]:
            p = cycler.update(Mood.CHILL, t=t, beat=False, bpm=90.0, energy=0.05)
            self.assertEqual(p.name, p1.name)


class EffectCyclerMoodChangeTests(unittest.TestCase):
    """Test that mood changes trigger immediate effect switches."""

    def test_mood_change_triggers_new_effect(self) -> None:
        cycler = EffectCycler(seed=42)
        p1 = cycler.update(Mood.CHILL, t=0.0, beat=False, bpm=90.0, energy=0.05)
        chill_names = {name for name, _ in MOOD_EFFECTS[Mood.CHILL]}
        self.assertIn(p1.name, chill_names)
        # Switch to GROOVE — should immediately pick from GROOVE pool
        p2 = cycler.update(Mood.GROOVE, t=1.0, beat=False, bpm=120.0, energy=0.15)
        groove_names = {name for name, _ in MOOD_EFFECTS[Mood.GROOVE]}
        self.assertIn(p2.name, groove_names)

    def test_mood_change_resets_cycle_timer(self) -> None:
        config = EffectCyclerConfig(cycle_interval=10.0)
        cycler = EffectCycler(config=config, seed=42)
        cycler.update(Mood.CHILL, t=0.0, beat=False, bpm=90.0, energy=0.05)
        # Switch mood at t=5
        p2 = cycler.update(Mood.GROOVE, t=5.0, beat=False, bpm=120.0, energy=0.15)
        # At t=12 (7s into GROOVE, < cycle_interval) should still be same
        p3 = cycler.update(Mood.GROOVE, t=12.0, beat=False, bpm=120.0, energy=0.15)
        self.assertEqual(p2.name, p3.name)
        # At t=16 (11s into GROOVE, > cycle_interval) should cycle
        p4 = cycler.update(Mood.GROOVE, t=16.0, beat=False, bpm=120.0, energy=0.15)
        self.assertNotEqual(p3.name, p4.name)


class EffectCyclerDropTests(unittest.TestCase):
    """Test DROP behavior."""

    def test_drop_always_returns_drop_blast(self) -> None:
        cycler = EffectCycler(seed=42)
        # Start in GROOVE
        cycler.update(Mood.GROOVE, t=0.0, beat=False, bpm=120.0, energy=0.15)
        # Enter DROP
        preset = cycler.update(Mood.DROP, t=1.0, beat=True, bpm=128.0, energy=0.35)
        self.assertEqual(preset.name, "drop_blast")

    def test_drop_stays_drop_blast_while_in_drop(self) -> None:
        cycler = EffectCycler(seed=42)
        cycler.update(Mood.DROP, t=0.0, beat=True, bpm=128.0, energy=0.35)
        # Multiple DROP updates
        for t in [0.5, 1.0, 1.5]:
            preset = cycler.update(Mood.DROP, t=t, beat=True, bpm=128.0, energy=0.35)
            self.assertEqual(preset.name, "drop_blast")

    def test_leaving_drop_picks_from_new_mood(self) -> None:
        cycler = EffectCycler(seed=42)
        # Enter DROP
        cycler.update(Mood.DROP, t=0.0, beat=True, bpm=128.0, energy=0.35)
        # Leave DROP into HYPE
        preset = cycler.update(Mood.HYPE, t=3.0, beat=False, bpm=128.0, energy=0.30)
        hype_names = {name for name, _ in MOOD_EFFECTS[Mood.HYPE]}
        self.assertIn(preset.name, hype_names)

    def test_leaving_drop_into_groove(self) -> None:
        cycler = EffectCycler(seed=42)
        cycler.update(Mood.DROP, t=0.0, beat=True, bpm=128.0, energy=0.35)
        preset = cycler.update(Mood.GROOVE, t=3.0, beat=False, bpm=120.0, energy=0.15)
        groove_names = {name for name, _ in MOOD_EFFECTS[Mood.GROOVE]}
        self.assertIn(preset.name, groove_names)


class EffectCyclerPaletteTests(unittest.TestCase):
    """Test palette selection per mood."""

    def test_palette_from_mood_eligible_set(self) -> None:
        cycler = EffectCycler(seed=42)
        for mood in Mood:
            preset = cycler.update(mood, t=0.0, beat=False, bpm=120.0, energy=0.15)
            # Palette should be one of the mood's eligible palettes
            eligible_palettes = MOOD_PALETTES[mood]
            found = False
            for pname in eligible_palettes:
                if PALETTES[pname] == preset.color_palette:
                    found = True
                    break
            self.assertTrue(found, f"Palette for {mood.value} not in eligible set")
            # Reset for next mood
            cycler = EffectCycler(seed=42)

    def test_palette_changes_on_cycle(self) -> None:
        """Palette should be re-picked when the effect cycles."""
        config = EffectCyclerConfig(cycle_interval=5.0)
        # Try many seeds — at least one should produce a palette change
        palette_changed = False
        for seed in range(50):
            cycler = EffectCycler(config=config, seed=seed)
            p1 = cycler.update(Mood.GROOVE, t=0.0, beat=False, bpm=120.0, energy=0.15)
            p2 = cycler.update(Mood.GROOVE, t=6.0, beat=False, bpm=120.0, energy=0.15)
            if p1.color_palette != p2.color_palette:
                palette_changed = True
                break
        self.assertTrue(palette_changed, "Palette should change on at least one cycle across 50 seeds")


class EffectCyclerIntegrationTests(unittest.TestCase):
    """Full song simulation through mood -> effect chain."""

    def test_song_simulation(self) -> None:
        """Simulate: CHILL intro -> GROOVE verse -> HYPE chorus -> DROP -> HYPE -> CHILL outro."""
        config = EffectCyclerConfig(cycle_interval=10.0, drop_blast_duration=2.0)
        cycler = EffectCycler(config=config, seed=42)

        chill_names = {name for name, _ in MOOD_EFFECTS[Mood.CHILL]}
        groove_names = {name for name, _ in MOOD_EFFECTS[Mood.GROOVE]}
        hype_names = {name for name, _ in MOOD_EFFECTS[Mood.HYPE]}

        # --- CHILL intro ---
        p = cycler.update(Mood.CHILL, t=0.0, beat=False, bpm=0.0, energy=0.04)
        self.assertIn(p.name, chill_names)
        first_chill = p.name

        # Still CHILL at t=5
        p = cycler.update(Mood.CHILL, t=5.0, beat=False, bpm=0.0, energy=0.04)
        self.assertEqual(p.name, first_chill)

        # --- GROOVE verse at t=8 ---
        p = cycler.update(Mood.GROOVE, t=8.0, beat=True, bpm=120.0, energy=0.15)
        self.assertIn(p.name, groove_names)

        # --- HYPE chorus at t=15 ---
        p = cycler.update(Mood.HYPE, t=15.0, beat=True, bpm=128.0, energy=0.30)
        self.assertIn(p.name, hype_names)

        # --- DROP at t=20 ---
        p = cycler.update(Mood.DROP, t=20.0, beat=True, bpm=128.0, energy=0.35)
        self.assertEqual(p.name, "drop_blast")

        # Still DROP at t=21
        p = cycler.update(Mood.DROP, t=21.0, beat=True, bpm=128.0, energy=0.35)
        self.assertEqual(p.name, "drop_blast")

        # --- Back to HYPE at t=23 ---
        p = cycler.update(Mood.HYPE, t=23.0, beat=True, bpm=128.0, energy=0.30)
        self.assertIn(p.name, hype_names)

        # --- CHILL outro at t=35 ---
        p = cycler.update(Mood.CHILL, t=35.0, beat=False, bpm=0.0, energy=0.04)
        self.assertIn(p.name, chill_names)

    def test_preset_has_valid_palette_colors(self) -> None:
        """Every returned preset should have parseable hex colors."""
        cycler = EffectCycler(seed=42)
        for mood in [Mood.CHILL, Mood.GROOVE, Mood.HYPE, Mood.DROP]:
            preset = cycler.update(mood, t=0.0, beat=False, bpm=120.0, energy=0.15)
            self.assertGreater(len(preset.color_palette), 0)
            for color in preset.color_palette:
                self.assertTrue(color.startswith("#"), f"Color {color} doesn't start with #")
                h = color.lstrip("#")
                self.assertEqual(len(h), 6, f"Color {color} is not 6 hex chars")
                int(h, 16)  # Should not raise
            cycler = EffectCycler(seed=42)


if __name__ == "__main__":
    unittest.main()
