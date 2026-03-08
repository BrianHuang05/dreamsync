import unittest

from dreamsync.director import EffectMode, LightingIntent
from dreamsync.output.roles import (
    DeviceRole,
    DeviceType,
    adapt_render_mode,
    default_device_config,
    infer_device_type,
    transform_intent,
)


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

    def test_accent_preserves_color(self) -> None:
        intent = LightingIntent(mode=EffectMode.RIPPLE, intensity=0.7, speed=0.5, bpm=120.0, color="#ff0000")
        result = transform_intent(intent, DeviceRole.ACCENT)
        self.assertEqual(result.color, "#ff0000")

    def test_accent_preserves_none_color(self) -> None:
        intent = LightingIntent(mode=EffectMode.PULSE, intensity=0.5, speed=0.5, bpm=120.0)
        result = transform_intent(intent, DeviceRole.ACCENT)
        self.assertIsNone(result.color)


class DeviceTypeTests(unittest.TestCase):
    def test_adapt_render_mode_bulb_scroll_to_pulse(self) -> None:
        assert adapt_render_mode("scroll", DeviceType.BULB) == "pulse"

    def test_adapt_render_mode_bulb_wave_to_breathe(self) -> None:
        assert adapt_render_mode("wave", DeviceType.BULB) == "breathe"

    def test_adapt_render_mode_strip_multi_passthrough(self) -> None:
        assert adapt_render_mode("scroll", DeviceType.STRIP_MULTI) == "scroll"

    def test_adapt_render_mode_unknown_passthrough(self) -> None:
        assert adapt_render_mode("solid", DeviceType.BULB) == "solid"

    def test_infer_device_type_single_segment(self) -> None:
        assert infer_device_type(1) == DeviceType.STRIP_SINGLE

    def test_infer_device_type_bulb_hint(self) -> None:
        assert infer_device_type(1, "H6001 Bulb") == DeviceType.BULB

    def test_infer_device_type_multi_segment(self) -> None:
        assert infer_device_type(15) == DeviceType.STRIP_MULTI


class BrightnessScaleTests(unittest.TestCase):
    def test_brightness_scale_reduces_intensity(self) -> None:
        intent = LightingIntent(mode=EffectMode.PULSE, intensity=0.8, speed=0.5, bpm=120.0)
        result = transform_intent(intent, DeviceRole.PRIMARY, brightness_scale=0.4)
        self.assertAlmostEqual(result.intensity, 0.32)

    def test_brightness_scale_default_noop(self) -> None:
        intent = LightingIntent(mode=EffectMode.PULSE, intensity=0.8, speed=0.5, bpm=120.0)
        result = transform_intent(intent, DeviceRole.PRIMARY)
        self.assertAlmostEqual(result.intensity, 0.8)

    def test_brightness_scale_stacks_with_accent(self) -> None:
        intent = LightingIntent(mode=EffectMode.PULSE, intensity=1.0, speed=0.5, bpm=120.0)
        result = transform_intent(intent, DeviceRole.ACCENT, brightness_scale=0.5)
        self.assertAlmostEqual(result.intensity, 0.3)

    def test_brightness_scale_clamped(self) -> None:
        intent = LightingIntent(mode=EffectMode.PULSE, intensity=0.8, speed=0.5, bpm=120.0)
        result = transform_intent(intent, DeviceRole.PRIMARY, brightness_scale=1.5)
        self.assertAlmostEqual(result.intensity, 0.8)

    def test_brightness_scale_zero(self) -> None:
        intent = LightingIntent(mode=EffectMode.PULSE, intensity=0.8, speed=0.5, bpm=120.0)
        result = transform_intent(intent, DeviceRole.PRIMARY, brightness_scale=0.0)
        self.assertAlmostEqual(result.intensity, 0.0)


class DefaultDeviceConfigTests(unittest.TestCase):
    def test_default_bulb_config(self) -> None:
        role, bs = default_device_config(DeviceType.BULB)
        self.assertEqual(role, DeviceRole.PRIMARY)
        self.assertAlmostEqual(bs, 1.0)

    def test_default_strip_multi_config(self) -> None:
        role, bs = default_device_config(DeviceType.STRIP_MULTI)
        self.assertEqual(role, DeviceRole.PRIMARY)
        self.assertAlmostEqual(bs, 0.4)

    def test_default_strip_single_config(self) -> None:
        role, bs = default_device_config(DeviceType.STRIP_SINGLE)
        self.assertEqual(role, DeviceRole.ACCENT)
        self.assertAlmostEqual(bs, 0.5)
