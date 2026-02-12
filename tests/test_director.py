import unittest

from dreamsync.director import Director, EffectMode


class DirectorTests(unittest.TestCase):
    def test_calm_goes_ambient(self) -> None:
        d = Director()
        intent = d.update({"t": 0.0, "rms": 0.03, "zcr": 0.12, "bpm": 80.0})
        self.assertEqual(intent.mode, EffectMode.AMBIENT)

    def test_stable_beat_enters_pulse(self) -> None:
        d = Director()
        intent = d.update({"t": 5.0, "rms": 0.10, "zcr": 0.03, "bpm": 120.0})
        self.assertEqual(intent.mode, EffectMode.PULSE)

    def test_high_energy_enters_motion(self) -> None:
        d = Director()
        intent = d.update({"t": 5.0, "rms": 0.30, "zcr": 0.06, "bpm": 128.0})
        self.assertEqual(intent.mode, EffectMode.MOTION)

    def test_hysteresis_motion_exit_requires_lower_energy(self) -> None:
        d = Director()
        d.update({"t": 5.0, "rms": 0.30, "zcr": 0.06, "bpm": 128.0})  # enter motion
        # Still above exit threshold, should stay motion.
        intent = d.update({"t": 9.0, "rms": 0.20, "zcr": 0.03, "bpm": 120.0})
        self.assertEqual(intent.mode, EffectMode.MOTION)
        # Below exit threshold, should leave motion.
        intent = d.update({"t": 13.0, "rms": 0.15, "zcr": 0.03, "bpm": 120.0})
        self.assertIn(intent.mode, (EffectMode.PULSE, EffectMode.AMBIENT))

    def test_cooldown_prevents_thrashing(self) -> None:
        d = Director()
        d.update({"t": 5.0, "rms": 0.10, "zcr": 0.03, "bpm": 120.0})  # pulse
        # Immediately noisy; should remain pulse until cooldown expires.
        intent = d.update({"t": 6.0, "rms": 0.10, "zcr": 0.20, "bpm": 120.0})
        self.assertEqual(intent.mode, EffectMode.PULSE)
        # After cooldown, can drop to ambient.
        intent = d.update({"t": 9.5, "rms": 0.10, "zcr": 0.20, "bpm": 120.0})
        self.assertEqual(intent.mode, EffectMode.AMBIENT)
