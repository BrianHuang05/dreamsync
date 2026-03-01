import unittest

from dreamsync.mood import Mood, MoodClassifier, MoodConfig


class MoodClassifierBasicTests(unittest.TestCase):
    """Test each mood is detected under the right conditions."""

    def _make(self, **kwargs) -> MoodClassifier:
        return MoodClassifier(MoodConfig(min_dwell_seconds=0.0, **kwargs))

    def test_chill_on_low_energy(self) -> None:
        mc = self._make()
        mood = mc.update(energy=0.10, stability=0.03, bpm=120.0, t=0.0)
        self.assertEqual(mood, Mood.CHILL)

    def test_chill_on_unstable_beat_low_bpm(self) -> None:
        mc = self._make()
        mood = mc.update(energy=0.35, stability=0.12, bpm=55.0, t=0.0)
        self.assertEqual(mood, Mood.CHILL)

    def test_groove_on_moderate_energy_stable_beat(self) -> None:
        mc = self._make()
        mood = mc.update(energy=0.35, stability=0.04, bpm=120.0, t=0.0)
        self.assertEqual(mood, Mood.GROOVE)

    def test_hype_on_high_energy_stable_beat(self) -> None:
        mc = self._make()
        mood = mc.update(energy=0.65, stability=0.03, bpm=128.0, t=0.0)
        self.assertEqual(mood, Mood.HYPE)

    def test_hype_requires_stable_beat(self) -> None:
        """High energy but unstable beat should not be HYPE."""
        mc = self._make()
        mood = mc.update(energy=0.65, stability=0.12, bpm=128.0, t=0.0)
        # Unstable → falls through to CHILL (not enough stability for GROOVE/HYPE)
        self.assertNotEqual(mood, Mood.HYPE)


class MoodDropDetectionTests(unittest.TestCase):
    """Test DROP detection: energy dip → spike pattern."""

    def _make(self, **kwargs) -> MoodClassifier:
        return MoodClassifier(MoodConfig(
            min_dwell_seconds=0.0,
            drop_cooldown=0.0,
            **kwargs,
        ))

    def test_drop_on_energy_spike_after_dip(self) -> None:
        mc = self._make()
        # Dip: energy drops below drop_energy_dip (0.15)
        mc.update(energy=0.10, stability=0.03, bpm=120.0, t=0.0)
        # Spike: energy jumps by >= drop_energy_spike (0.25) within drop_window (0.5s)
        mood = mc.update(energy=0.55, stability=0.03, bpm=120.0, t=0.3)
        self.assertEqual(mood, Mood.DROP)

    def test_no_drop_without_dip(self) -> None:
        """Spike alone without prior dip should not trigger DROP."""
        mc = self._make()
        mc.update(energy=0.40, stability=0.03, bpm=120.0, t=0.0)
        mood = mc.update(energy=0.70, stability=0.03, bpm=120.0, t=0.3)
        self.assertNotEqual(mood, Mood.DROP)

    def test_no_drop_if_spike_too_late(self) -> None:
        """Spike after the drop_window expires should not trigger DROP."""
        mc = self._make()
        mc.update(energy=0.10, stability=0.03, bpm=120.0, t=0.0)
        # 1.0s later — outside drop_window (0.5s)
        mood = mc.update(energy=0.55, stability=0.03, bpm=120.0, t=1.0)
        self.assertNotEqual(mood, Mood.DROP)

    def test_drop_auto_expires(self) -> None:
        """DROP should auto-transition after drop_duration."""
        mc = self._make(drop_duration=2.0)
        # Trigger DROP
        mc.update(energy=0.10, stability=0.03, bpm=120.0, t=0.0)
        mc.update(energy=0.55, stability=0.03, bpm=120.0, t=0.3)
        self.assertEqual(mc.mood, Mood.DROP)
        # Still DROP within duration
        mood = mc.update(energy=0.55, stability=0.03, bpm=120.0, t=1.5)
        self.assertEqual(mood, Mood.DROP)
        # After drop_duration, transitions out
        mood = mc.update(energy=0.55, stability=0.03, bpm=120.0, t=2.5)
        self.assertNotEqual(mood, Mood.DROP)
        self.assertIn(mood, (Mood.GROOVE, Mood.HYPE))

    def test_drop_cooldown_prevents_rapid_refire(self) -> None:
        """A second DROP should not fire during cooldown."""
        mc = MoodClassifier(MoodConfig(
            min_dwell_seconds=0.0,
            drop_duration=1.0,
            drop_cooldown=10.0,
        ))
        # First DROP
        mc.update(energy=0.10, stability=0.03, bpm=120.0, t=0.0)
        mc.update(energy=0.55, stability=0.03, bpm=120.0, t=0.3)
        self.assertEqual(mc.mood, Mood.DROP)
        # Expire first DROP
        mc.update(energy=0.55, stability=0.03, bpm=120.0, t=2.0)
        self.assertNotEqual(mc.mood, Mood.DROP)
        # Try second DROP within cooldown (10s)
        mc.update(energy=0.10, stability=0.03, bpm=120.0, t=3.0)
        mood = mc.update(energy=0.55, stability=0.03, bpm=120.0, t=3.3)
        self.assertNotEqual(mood, Mood.DROP)


class MoodHysteresisTests(unittest.TestCase):
    """Test that hysteresis prevents thrashing on borderline inputs."""

    def _make(self, **kwargs) -> MoodClassifier:
        return MoodClassifier(MoodConfig(min_dwell_seconds=0.0, **kwargs))

    def test_chill_to_groove_requires_exit_threshold(self) -> None:
        """Energy between chill_energy_ceiling and chill_energy_exit should stay CHILL."""
        mc = self._make()
        # Start in CHILL
        mc.update(energy=0.10, stability=0.04, bpm=120.0, t=0.0)
        self.assertEqual(mc.mood, Mood.CHILL)
        # Energy = 0.24: above chill_energy_ceiling (0.20) but below chill_energy_exit (0.28)
        mood = mc.update(energy=0.24, stability=0.04, bpm=120.0, t=1.0)
        self.assertEqual(mood, Mood.CHILL, "Should stay CHILL due to hysteresis")
        # Energy = 0.35: above chill_energy_exit (0.28) → transitions
        mood = mc.update(energy=0.35, stability=0.04, bpm=120.0, t=2.0)
        self.assertEqual(mood, Mood.GROOVE)

    def test_groove_to_hype_requires_exit_threshold(self) -> None:
        """Energy between groove_energy_ceiling and groove_energy_exit_high should stay GROOVE."""
        mc = self._make()
        # Enter GROOVE
        mc.update(energy=0.35, stability=0.04, bpm=120.0, t=0.0)
        self.assertEqual(mc.mood, Mood.GROOVE)
        # Energy = 0.54: above groove_energy_ceiling (0.50) but below groove_energy_exit_high (0.58)
        mood = mc.update(energy=0.54, stability=0.04, bpm=120.0, t=1.0)
        self.assertEqual(mood, Mood.GROOVE, "Should stay GROOVE due to hysteresis")
        # Energy = 0.65: above groove_energy_exit_high (0.58) → transitions
        mood = mc.update(energy=0.65, stability=0.04, bpm=120.0, t=2.0)
        self.assertEqual(mood, Mood.HYPE)

    def test_hype_to_groove_requires_exit_threshold(self) -> None:
        """Energy between hype_energy_exit and groove_energy_ceiling should stay HYPE."""
        mc = self._make()
        # Enter HYPE
        mc.update(energy=0.65, stability=0.04, bpm=128.0, t=0.0)
        self.assertEqual(mc.mood, Mood.HYPE)
        # Energy = 0.45: below groove_energy_ceiling (0.50) but above hype_energy_exit (0.40)
        mood = mc.update(energy=0.45, stability=0.04, bpm=128.0, t=1.0)
        self.assertEqual(mood, Mood.HYPE, "Should stay HYPE due to hysteresis")
        # Energy = 0.35: below hype_energy_exit (0.40) → transitions
        mood = mc.update(energy=0.35, stability=0.04, bpm=120.0, t=2.0)
        self.assertIn(mood, (Mood.GROOVE, Mood.CHILL))


class MoodDwellTimeTests(unittest.TestCase):
    """Test that mood holds for minimum duration before switching."""

    def test_dwell_prevents_early_switch(self) -> None:
        mc = MoodClassifier(MoodConfig(min_dwell_seconds=4.0))
        # Enter GROOVE
        mc.update(energy=0.35, stability=0.04, bpm=120.0, t=0.0)
        self.assertEqual(mc.mood, Mood.GROOVE)
        # Strong HYPE signal at t=2.0 (within 4s dwell) → stays GROOVE
        mood = mc.update(energy=0.65, stability=0.03, bpm=128.0, t=2.0)
        self.assertEqual(mood, Mood.GROOVE, "Should hold GROOVE during dwell period")
        # Same signal after dwell expires → transitions to HYPE
        mood = mc.update(energy=0.65, stability=0.03, bpm=128.0, t=5.0)
        self.assertEqual(mood, Mood.HYPE)

    def test_drop_ignores_dwell(self) -> None:
        """DROP should override dwell time of current mood."""
        mc = MoodClassifier(MoodConfig(min_dwell_seconds=4.0, drop_cooldown=0.0))
        # Enter GROOVE
        mc.update(energy=0.35, stability=0.04, bpm=120.0, t=0.0)
        self.assertEqual(mc.mood, Mood.GROOVE)
        # DROP signal at t=1.0 (within 4s dwell) → DROP overrides dwell
        mc.update(energy=0.10, stability=0.03, bpm=120.0, t=0.8)
        mood = mc.update(energy=0.55, stability=0.03, bpm=120.0, t=1.0)
        self.assertEqual(mood, Mood.DROP)


class MoodIntegrationTests(unittest.TestCase):
    """Simulate a full song and verify mood transitions."""

    def test_song_simulation(self) -> None:
        """Simulate: ambient intro → verse → chorus → drop → outro."""
        mc = MoodClassifier(MoodConfig(
            min_dwell_seconds=0.0,
            drop_cooldown=0.0,
            drop_duration=2.0,
        ))

        # --- Ambient intro: low energy, no beat ---
        mood = mc.update(energy=0.05, stability=0.15, bpm=0.0, t=0.0)
        self.assertEqual(mood, Mood.CHILL)

        # --- Verse: moderate energy, stable beat ---
        mood = mc.update(energy=0.35, stability=0.04, bpm=110.0, t=5.0)
        self.assertEqual(mood, Mood.GROOVE)

        # --- Chorus: high energy ---
        mood = mc.update(energy=0.65, stability=0.03, bpm=128.0, t=10.0)
        self.assertEqual(mood, Mood.HYPE)

        # --- Drop: energy dips then spikes ---
        mc.update(energy=0.10, stability=0.03, bpm=128.0, t=15.0)
        mood = mc.update(energy=0.65, stability=0.03, bpm=128.0, t=15.3)
        self.assertEqual(mood, Mood.DROP)

        # --- DROP auto-expires into HYPE ---
        mood = mc.update(energy=0.65, stability=0.03, bpm=128.0, t=18.0)
        self.assertIn(mood, (Mood.HYPE, Mood.GROOVE))

        # --- Outro: energy fades ---
        mood = mc.update(energy=0.05, stability=0.15, bpm=0.0, t=25.0)
        self.assertEqual(mood, Mood.CHILL)


if __name__ == "__main__":
    unittest.main()
