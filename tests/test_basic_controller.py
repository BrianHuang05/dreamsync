import unittest

from dreamsync.basic_controller import BeatRippleConfig, BeatRippleController
from dreamsync.director import EffectMode


class BeatRippleControllerTests(unittest.TestCase):
    def test_color_advances_on_beat(self) -> None:
        colors = ("#ff0000", "#00ff00", "#0000ff")
        ctrl = BeatRippleController(BeatRippleConfig(colors=colors))
        intent1, _ = ctrl.update(t=0.0, bpm=120.0, beat=True)
        self.assertIsNotNone(intent1)
        self.assertEqual(intent1.color, "#ff0000")

        intent2, _ = ctrl.update(t=0.5, bpm=120.0, beat=True)
        self.assertIsNotNone(intent2)
        self.assertEqual(intent2.color, "#00ff00")

        intent3, _ = ctrl.update(t=1.0, bpm=120.0, beat=True)
        self.assertIsNotNone(intent3)
        self.assertEqual(intent3.color, "#0000ff")

        # Wraps around
        intent4, _ = ctrl.update(t=1.5, bpm=120.0, beat=True)
        self.assertIsNotNone(intent4)
        self.assertEqual(intent4.color, "#ff0000")

    def test_returns_constant_brightness(self) -> None:
        ctrl = BeatRippleController(BeatRippleConfig(brightness=0.7))
        intent, beat_event = ctrl.update(t=1.0, bpm=120.0, beat=True)
        self.assertTrue(beat_event)
        self.assertIsNotNone(intent)
        self.assertAlmostEqual(intent.intensity, 0.7)
        self.assertEqual(intent.mode, EffectMode.RIPPLE)

    def test_returns_none_between_beats(self) -> None:
        ctrl = BeatRippleController()
        ctrl.update(t=0.0, bpm=120.0, beat=True)

        intent, beat_event = ctrl.update(t=0.5, bpm=120.0, beat=False)
        self.assertIsNone(intent)
        self.assertFalse(beat_event)

    def test_respects_min_beat_interval(self) -> None:
        cfg = BeatRippleConfig(min_beat_interval_seconds=0.3)
        ctrl = BeatRippleController(cfg)
        ctrl.update(t=0.0, bpm=120.0, beat=True)

        # Too soon — should not trigger
        intent, beat_event = ctrl.update(t=0.1, bpm=120.0, beat=True)
        self.assertFalse(beat_event)
        self.assertIsNone(intent)

        # After interval — should trigger
        intent, beat_event = ctrl.update(t=0.35, bpm=120.0, beat=True)
        self.assertTrue(beat_event)
        self.assertIsNotNone(intent)

    def test_speed_is_bpm_over_divisor_clamped(self) -> None:
        cfg = BeatRippleConfig(speed_divisor=480.0, speed_floor=0.08, speed_ceiling=0.35)
        ctrl = BeatRippleController(cfg)

        # 120 / 480 = 0.25, within range
        intent, _ = ctrl.update(t=0.0, bpm=120.0, beat=True)
        self.assertAlmostEqual(intent.speed, 0.25)

        # Very low BPM — clamps to floor
        ctrl2 = BeatRippleController(cfg)
        intent2, _ = ctrl2.update(t=0.0, bpm=20.0, beat=True)
        self.assertAlmostEqual(intent2.speed, 0.08)

        # Very high BPM — clamps to ceiling
        ctrl3 = BeatRippleController(cfg)
        intent3, _ = ctrl3.update(t=0.0, bpm=500.0, beat=True)
        self.assertAlmostEqual(intent3.speed, 0.35)
