import unittest

from dreamsync.director import Director, DirectorConfig, EffectMode


def _seed(d: Director, t_start: float, count: int, **kwargs) -> None:
    """Feed N consistent feature updates to build up EMA values."""
    dt = 0.05  # ~20 updates per second
    for i in range(count):
        t = t_start + i * dt
        features = {"t": t, "rms": 0.0, "zcr": 0.0, "bpm": 0.0, "beat": False}
        features.update(kwargs)
        features["t"] = t
        d.update(features)


class DirectorModeTests(unittest.TestCase):
    def test_calm_goes_ambient(self) -> None:
        d = Director(DirectorConfig(warmup_seconds=0.0))
        # Seed with low RMS, high ZCR (unstable), low BPM
        _seed(d, t_start=0.0, count=30, rms=0.03, zcr=0.40, bpm=60.0)
        intent = d.update({"t": 2.0, "rms": 0.03, "zcr": 0.40, "bpm": 60.0})
        self.assertEqual(intent.mode, EffectMode.AMBIENT)

    def test_stable_beat_enters_pulse(self) -> None:
        d = Director(DirectorConfig(warmup_seconds=0.0))
        # Seed with moderate RMS, low ZCR (stable), good BPM
        _seed(d, t_start=0.0, count=30, rms=0.10, zcr=0.03, bpm=120.0)
        intent = d.update({"t": 2.0, "rms": 0.10, "zcr": 0.03, "bpm": 120.0})
        self.assertEqual(intent.mode, EffectMode.PULSE)

    def test_high_energy_enters_motion(self) -> None:
        # Use short switch interval so EMA ramp through PULSE → MOTION
        # isn't blocked by cooldown
        d = Director(DirectorConfig(warmup_seconds=0.0, min_switch_interval_seconds=0.0))
        # Seed with high RMS so EMA crosses high_energy_enter (0.24)
        _seed(d, t_start=0.0, count=30, rms=0.35, zcr=0.03, bpm=128.0)
        intent = d.update({"t": 2.0, "rms": 0.35, "zcr": 0.03, "bpm": 128.0})
        self.assertEqual(intent.mode, EffectMode.MOTION)

    def test_hysteresis_motion_exit_requires_lower_energy(self) -> None:
        d = Director(DirectorConfig(warmup_seconds=0.0, min_switch_interval_seconds=0.0))
        # Seed to enter MOTION
        _seed(d, t_start=0.0, count=30, rms=0.35, zcr=0.03, bpm=128.0)
        intent = d.update({"t": 2.0, "rms": 0.35, "zcr": 0.03, "bpm": 128.0})
        self.assertEqual(intent.mode, EffectMode.MOTION)
        # Moderate energy — above exit threshold (0.18), should stay motion
        _seed(d, t_start=3.0, count=30, rms=0.22, zcr=0.03, bpm=120.0)
        intent = d.update({"t": 6.0, "rms": 0.22, "zcr": 0.03, "bpm": 120.0})
        self.assertEqual(intent.mode, EffectMode.MOTION)
        # Drop below exit threshold
        _seed(d, t_start=7.0, count=30, rms=0.10, zcr=0.03, bpm=120.0)
        intent = d.update({"t": 10.0, "rms": 0.10, "zcr": 0.03, "bpm": 120.0})
        self.assertIn(intent.mode, (EffectMode.PULSE, EffectMode.AMBIENT))

    def test_cooldown_prevents_thrashing(self) -> None:
        d = Director(DirectorConfig(warmup_seconds=0.0))
        # Enter PULSE
        _seed(d, t_start=0.0, count=30, rms=0.10, zcr=0.03, bpm=120.0)
        intent = d.update({"t": 2.0, "rms": 0.10, "zcr": 0.03, "bpm": 120.0})
        self.assertEqual(intent.mode, EffectMode.PULSE)
        # Immediately noisy; should remain PULSE due to min_switch_interval (3s)
        intent = d.update({"t": 3.0, "rms": 0.10, "zcr": 0.20, "bpm": 120.0})
        self.assertEqual(intent.mode, EffectMode.PULSE)
        # After cooldown, can exit to ambient
        _seed(d, t_start=5.5, count=30, rms=0.10, zcr=0.20, bpm=120.0)
        intent = d.update({"t": 8.0, "rms": 0.10, "zcr": 0.20, "bpm": 120.0})
        self.assertEqual(intent.mode, EffectMode.AMBIENT)

    def test_warmup_stays_ambient(self) -> None:
        d = Director()  # default warmup_seconds=8.0
        intent = d.update({"t": 3.0, "rms": 0.30, "zcr": 0.03, "bpm": 128.0})
        self.assertEqual(intent.mode, EffectMode.AMBIENT)


class DirectorColorCyclingTests(unittest.TestCase):
    def test_color_advances_on_beat(self) -> None:
        colors = ("#ff0000", "#00ff00", "#0000ff")
        d = Director(DirectorConfig(colors=colors))
        # First color is at index 0 before any beat
        intent = d.update({"t": 0.0, "rms": 0.1, "zcr": 0.05, "bpm": 120.0, "beat": False})
        self.assertEqual(intent.color, "#ff0000")
        # Beat advances to index 1
        intent = d.update({"t": 0.5, "rms": 0.1, "zcr": 0.05, "bpm": 120.0, "beat": True})
        self.assertEqual(intent.color, "#00ff00")
        # Beat advances to index 2
        intent = d.update({"t": 1.0, "rms": 0.1, "zcr": 0.05, "bpm": 120.0, "beat": True})
        self.assertEqual(intent.color, "#0000ff")

    def test_color_wraps_around(self) -> None:
        colors = ("#ff0000", "#00ff00")
        d = Director(DirectorConfig(colors=colors))
        d.update({"t": 0.0, "rms": 0.1, "zcr": 0.05, "bpm": 120.0, "beat": True})
        d.update({"t": 0.5, "rms": 0.1, "zcr": 0.05, "bpm": 120.0, "beat": True})
        intent = d.update({"t": 1.0, "rms": 0.1, "zcr": 0.05, "bpm": 120.0, "beat": True})
        # After 3 beats: 0->1->0->1, so should be at index 1
        self.assertEqual(intent.color, "#00ff00")

    def test_color_stable_without_beat(self) -> None:
        colors = ("#ff0000", "#00ff00", "#0000ff")
        d = Director(DirectorConfig(colors=colors))
        intent1 = d.update({"t": 0.0, "rms": 0.1, "zcr": 0.05, "bpm": 120.0, "beat": False})
        intent2 = d.update({"t": 0.5, "rms": 0.1, "zcr": 0.05, "bpm": 120.0, "beat": False})
        intent3 = d.update({"t": 1.0, "rms": 0.1, "zcr": 0.05, "bpm": 120.0, "beat": False})
        self.assertEqual(intent1.color, intent2.color)
        self.assertEqual(intent2.color, intent3.color)

    def test_color_always_set(self) -> None:
        d = Director()
        intent = d.update({"t": 0.0, "rms": 0.1, "zcr": 0.05, "bpm": 120.0, "beat": False})
        self.assertIsNotNone(intent.color)
        self.assertTrue(intent.color.startswith("#"))

    def test_last_beat_event_tracks_beats(self) -> None:
        d = Director()
        d.update({"t": 0.0, "rms": 0.1, "zcr": 0.05, "bpm": 120.0, "beat": False})
        self.assertFalse(d.last_beat_event)
        d.update({"t": 0.5, "rms": 0.1, "zcr": 0.05, "bpm": 120.0, "beat": True})
        self.assertTrue(d.last_beat_event)
        d.update({"t": 1.0, "rms": 0.1, "zcr": 0.05, "bpm": 120.0, "beat": False})
        self.assertFalse(d.last_beat_event)


class DirectorIntegrationTests(unittest.TestCase):
    """Feed a simulated song through the Director and verify mode transitions
    happen at the right energy levels, and colors cycle on beats throughout."""

    def test_full_sequence_ambient_pulse_motion_and_back(self) -> None:
        """Simulate: quiet intro → steady verse → loud chorus → cooldown.

        Verify AMBIENT → PULSE → MOTION → PULSE/AMBIENT in one run.
        Uses short history_seconds so old phase data ages out, matching
        how real audio transitions work with the EMA/stability smoothing.
        """
        colors = ("#aa0000", "#00bb00", "#0000cc")
        d = Director(DirectorConfig(
            warmup_seconds=0.0,
            min_switch_interval_seconds=0.0,
            history_seconds=2.0,
            colors=colors,
        ))

        # --- Phase A: quiet intro (low RMS, unstable beat) → AMBIENT ---
        _seed(d, t_start=0.0, count=60, rms=0.03, zcr=0.40, bpm=0.0)
        intent = d.update({"t": 3.5, "rms": 0.03, "zcr": 0.40, "bpm": 0.0})
        self.assertEqual(intent.mode, EffectMode.AMBIENT,
                         "Quiet intro should be AMBIENT")

        # --- Phase B: verse kicks in (moderate RMS, stable beat) → PULSE ---
        # Gap long enough for Phase A history to age out
        _seed(d, t_start=5.0, count=60, rms=0.12, zcr=0.03, bpm=120.0)
        intent = d.update({"t": 8.5, "rms": 0.12, "zcr": 0.03, "bpm": 120.0})
        self.assertEqual(intent.mode, EffectMode.PULSE,
                         "Steady verse should be PULSE")

        # --- Phase C: chorus / drop (high RMS) → MOTION ---
        _seed(d, t_start=10.0, count=60, rms=0.38, zcr=0.03, bpm=128.0)
        intent = d.update({"t": 13.5, "rms": 0.38, "zcr": 0.03, "bpm": 128.0})
        self.assertEqual(intent.mode, EffectMode.MOTION,
                         "Loud chorus should be MOTION")

        # --- Phase D: cooldown (energy drops) → exits MOTION ---
        _seed(d, t_start=15.0, count=60, rms=0.08, zcr=0.03, bpm=110.0)
        intent = d.update({"t": 18.5, "rms": 0.08, "zcr": 0.03, "bpm": 110.0})
        self.assertIn(intent.mode, (EffectMode.PULSE, EffectMode.AMBIENT),
                      "Cooldown should leave MOTION")

    def test_color_cycles_across_mode_transitions(self) -> None:
        """Verify colors keep advancing on beats even as the mode changes."""
        colors = ("#r1", "#g2", "#b3")
        d = Director(DirectorConfig(
            warmup_seconds=0.0,
            min_switch_interval_seconds=0.0,
            history_seconds=2.0,
            colors=colors,
        ))

        # Start in AMBIENT with no beats — color stays at index 0
        _seed(d, t_start=0.0, count=60, rms=0.03, zcr=0.40, bpm=0.0)
        intent = d.update({"t": 3.5, "rms": 0.03, "zcr": 0.40, "bpm": 0.0, "beat": False})
        self.assertEqual(intent.mode, EffectMode.AMBIENT)
        self.assertEqual(intent.color, "#r1")

        # Beat in AMBIENT → color advances to index 1
        intent = d.update({"t": 3.6, "rms": 0.03, "zcr": 0.40, "bpm": 0.0, "beat": True})
        self.assertEqual(intent.color, "#g2")

        # Transition to PULSE — seed long enough for old history to age out
        _seed(d, t_start=5.0, count=60, rms=0.12, zcr=0.03, bpm=120.0)
        intent = d.update({"t": 8.5, "rms": 0.12, "zcr": 0.03, "bpm": 120.0, "beat": True})
        self.assertEqual(intent.mode, EffectMode.PULSE)
        # _seed sent 60 frames with beat=False (index stayed at 1), this beat → index 2
        self.assertEqual(intent.color, "#b3")

        # Transition to MOTION with a beat → color wraps to index 0
        _seed(d, t_start=10.0, count=60, rms=0.38, zcr=0.03, bpm=128.0)
        intent = d.update({"t": 13.5, "rms": 0.38, "zcr": 0.03, "bpm": 128.0, "beat": True})
        self.assertEqual(intent.mode, EffectMode.MOTION)
        self.assertEqual(intent.color, "#r1")

        # Another beat in MOTION → advances again
        intent = d.update({"t": 13.6, "rms": 0.38, "zcr": 0.03, "bpm": 128.0, "beat": True})
        self.assertEqual(intent.mode, EffectMode.MOTION)
        self.assertEqual(intent.color, "#g2")

        # No beat → color stays
        intent = d.update({"t": 13.7, "rms": 0.38, "zcr": 0.03, "bpm": 128.0, "beat": False})
        self.assertEqual(intent.color, "#g2")


if __name__ == "__main__":
    unittest.main()
