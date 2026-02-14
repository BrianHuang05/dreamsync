import unittest

from dreamsync.director import EffectMode, LightingIntent
from dreamsync.output.roles import DeviceRole, transform_intent


class TransformIntentTests(unittest.TestCase):
    def test_primary_is_passthrough(self) -> None:
        intent = LightingIntent(mode=EffectMode.MOTION, intensity=0.8, speed=0.7, bpm=130.0)
        result = transform_intent(intent, DeviceRole.PRIMARY)
        self.assertIs(result, intent)

    def test_accent_locks_to_ambient(self) -> None:
        intent = LightingIntent(mode=EffectMode.MOTION, intensity=0.8, speed=0.7, bpm=130.0)
        result = transform_intent(intent, DeviceRole.ACCENT)
        self.assertEqual(result.mode, EffectMode.AMBIENT)

    def test_accent_scales_intensity(self) -> None:
        intent = LightingIntent(mode=EffectMode.PULSE, intensity=1.0, speed=0.5, bpm=120.0)
        result = transform_intent(intent, DeviceRole.ACCENT)
        self.assertAlmostEqual(result.intensity, 0.6)

    def test_accent_caps_speed(self) -> None:
        intent = LightingIntent(mode=EffectMode.PULSE, intensity=0.5, speed=0.8, bpm=120.0)
        result = transform_intent(intent, DeviceRole.ACCENT)
        self.assertAlmostEqual(result.speed, 0.25)

    def test_accent_preserves_low_speed(self) -> None:
        intent = LightingIntent(mode=EffectMode.AMBIENT, intensity=0.5, speed=0.1, bpm=90.0)
        result = transform_intent(intent, DeviceRole.ACCENT)
        self.assertAlmostEqual(result.speed, 0.1)

    def test_accent_preserves_bpm(self) -> None:
        intent = LightingIntent(mode=EffectMode.PULSE, intensity=0.5, speed=0.5, bpm=140.0)
        result = transform_intent(intent, DeviceRole.ACCENT)
        self.assertAlmostEqual(result.bpm, 140.0)
