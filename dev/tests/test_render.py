import unittest

from dreamsync.director import EffectMode, LightingIntent
from dreamsync.render import RenderMode, SegmentRenderer


class SolidRenderTests(unittest.TestCase):
    def test_solid_fills_all_segments_with_color(self) -> None:
        renderer = SegmentRenderer(segments=5, mode=RenderMode.SOLID)
        intent = LightingIntent(
            mode=EffectMode.PULSE, intensity=1.0, speed=0.5, bpm=120.0,
            color="#ff0000",
        )
        frame = renderer.render(0.0, intent)
        self.assertEqual(len(frame), 5)
        for pixel in frame:
            self.assertEqual(pixel, (255, 0, 0))

    def test_solid_scales_by_intensity(self) -> None:
        renderer = SegmentRenderer(segments=3, mode=RenderMode.SOLID)
        intent = LightingIntent(
            mode=EffectMode.PULSE, intensity=0.5, speed=0.5, bpm=120.0,
            color="#ff0000",
        )
        frame = renderer.render(0.0, intent)
        for pixel in frame:
            self.assertEqual(pixel, (127, 0, 0))

    def test_solid_uses_default_color_when_none(self) -> None:
        renderer = SegmentRenderer(segments=2, mode=RenderMode.SOLID)
        intent = LightingIntent(
            mode=EffectMode.AMBIENT, intensity=1.0, speed=0.2, bpm=90.0,
        )
        frame = renderer.render(0.0, intent)
        # Default warm white: (255, 180, 100)
        self.assertEqual(frame[0], (255, 180, 100))

    def test_solid_zero_intensity_gives_black(self) -> None:
        renderer = SegmentRenderer(segments=3, mode=RenderMode.SOLID)
        intent = LightingIntent(
            mode=EffectMode.PULSE, intensity=0.0, speed=0.5, bpm=120.0,
            color="#ff8800",
        )
        frame = renderer.render(0.0, intent)
        for pixel in frame:
            self.assertEqual(pixel, (0, 0, 0))


class PulseRenderTests(unittest.TestCase):
    def test_pulse_bright_on_beat(self) -> None:
        renderer = SegmentRenderer(segments=3, mode=RenderMode.PULSE)
        intent = LightingIntent(
            mode=EffectMode.PULSE, intensity=1.0, speed=0.5, bpm=120.0,
            color="#ff0000",
        )
        frame = renderer.render(1.0, intent, beat=True)
        # On beat, should be at full brightness
        for pixel in frame:
            self.assertEqual(pixel, (255, 0, 0))

    def test_pulse_decays_after_beat(self) -> None:
        renderer = SegmentRenderer(segments=3, mode=RenderMode.PULSE)
        intent = LightingIntent(
            mode=EffectMode.PULSE, intensity=1.0, speed=0.5, bpm=120.0,
            color="#ff0000",
        )
        renderer.render(1.0, intent, beat=True)
        # 200ms later, should have decayed significantly
        frame = renderer.render(1.2, intent, beat=False)
        self.assertLess(frame[0][0], 200)
        self.assertGreater(frame[0][0], 0)

    def test_pulse_fully_decays_to_near_black(self) -> None:
        renderer = SegmentRenderer(segments=3, mode=RenderMode.PULSE)
        intent = LightingIntent(
            mode=EffectMode.PULSE, intensity=1.0, speed=0.5, bpm=120.0,
            color="#ff0000",
        )
        renderer.render(1.0, intent, beat=True)
        # 2 seconds later, should be near black
        frame = renderer.render(3.0, intent, beat=False)
        self.assertLessEqual(frame[0][0], 1)

    def test_pulse_no_beat_starts_dark(self) -> None:
        renderer = SegmentRenderer(segments=3, mode=RenderMode.PULSE)
        intent = LightingIntent(
            mode=EffectMode.PULSE, intensity=1.0, speed=0.5, bpm=120.0,
            color="#ff0000",
        )
        frame = renderer.render(1.0, intent, beat=False)
        # No beat ever triggered, should be black
        for pixel in frame:
            self.assertEqual(pixel, (0, 0, 0))

    def test_pulse_second_beat_resets(self) -> None:
        renderer = SegmentRenderer(segments=3, mode=RenderMode.PULSE)
        intent = LightingIntent(
            mode=EffectMode.PULSE, intensity=1.0, speed=0.5, bpm=120.0,
            color="#ff0000",
        )
        renderer.render(1.0, intent, beat=True)
        renderer.render(1.5, intent, beat=False)  # partially decayed
        frame = renderer.render(2.0, intent, beat=True)  # new beat
        for pixel in frame:
            self.assertEqual(pixel, (255, 0, 0))

    def test_pulse_respects_intensity(self) -> None:
        renderer = SegmentRenderer(segments=3, mode=RenderMode.PULSE)
        intent = LightingIntent(
            mode=EffectMode.PULSE, intensity=0.5, speed=0.5, bpm=120.0,
            color="#ff0000",
        )
        frame = renderer.render(1.0, intent, beat=True)
        # Intensity 0.5 * brightness 1.0 = half
        self.assertEqual(frame[0][0], 127)


class BreatheRenderTests(unittest.TestCase):
    def test_breathe_oscillates(self) -> None:
        renderer = SegmentRenderer(segments=3, mode=RenderMode.BREATHE)
        intent = LightingIntent(
            mode=EffectMode.AMBIENT, intensity=1.0, speed=0.2, bpm=60.0,
            color="#ff0000",
        )
        # At 60 BPM, one full cycle per second.
        # Sample at multiple points to verify oscillation.
        values = []
        for i in range(20):
            t = 1.0 + i * 0.05  # 0 to 1 second in 50ms steps
            frame = renderer.render(t, intent)
            values.append(frame[0][0])

        # Should have both high and low values (oscillation)
        self.assertGreater(max(values), 100)
        self.assertLess(min(values), 50)

    def test_breathe_all_segments_same(self) -> None:
        renderer = SegmentRenderer(segments=5, mode=RenderMode.BREATHE)
        intent = LightingIntent(
            mode=EffectMode.AMBIENT, intensity=1.0, speed=0.2, bpm=120.0,
            color="#00ff00",
        )
        renderer.render(1.0, intent)  # init
        frame = renderer.render(1.1, intent)
        # All segments should be identical
        for pixel in frame:
            self.assertEqual(pixel, frame[0])

    def test_breathe_respects_intensity(self) -> None:
        r1 = SegmentRenderer(segments=3, mode=RenderMode.BREATHE)
        r2 = SegmentRenderer(segments=3, mode=RenderMode.BREATHE)
        intent_full = LightingIntent(
            mode=EffectMode.AMBIENT, intensity=1.0, speed=0.2, bpm=60.0,
            color="#ff0000",
        )
        intent_half = LightingIntent(
            mode=EffectMode.AMBIENT, intensity=0.5, speed=0.2, bpm=60.0,
            color="#ff0000",
        )
        r1.render(1.0, intent_full)
        r2.render(1.0, intent_half)
        f1 = r1.render(1.25, intent_full)
        f2 = r2.render(1.25, intent_half)
        # Half intensity should produce roughly half the brightness
        if f1[0][0] > 0:
            ratio = f2[0][0] / f1[0][0]
            self.assertAlmostEqual(ratio, 0.5, delta=0.05)


class ScrollRenderTests(unittest.TestCase):
    def test_scroll_beat_injects_color_at_center(self) -> None:
        renderer = SegmentRenderer(segments=7, mode=RenderMode.SCROLL, mirror=True)
        intent = LightingIntent(
            mode=EffectMode.RIPPLE, intensity=1.0, speed=0.5, bpm=120.0,
            color="#ff0000",
        )
        frame = renderer.render(1.0, intent, beat=True)
        center = len(frame) // 2
        # Center pixel should be red
        self.assertEqual(frame[center][0], 255)
        self.assertEqual(frame[center][1], 0)
        self.assertEqual(frame[center][2], 0)

    def test_scroll_no_beat_no_injection(self) -> None:
        renderer = SegmentRenderer(segments=5, mode=RenderMode.SCROLL, mirror=True)
        intent = LightingIntent(
            mode=EffectMode.RIPPLE, intensity=1.0, speed=0.5, bpm=120.0,
            color="#ff0000",
        )
        frame = renderer.render(1.0, intent, beat=False)
        # No beat ever, all should be black
        for pixel in frame:
            self.assertEqual(pixel, (0, 0, 0))

    def test_scroll_correct_segment_count(self) -> None:
        for n in [5, 7, 10, 15]:
            renderer = SegmentRenderer(segments=n, mode=RenderMode.SCROLL, mirror=True)
            intent = LightingIntent(
                mode=EffectMode.RIPPLE, intensity=1.0, speed=0.5, bpm=120.0,
                color="#ff0000",
            )
            frame = renderer.render(1.0, intent, beat=True)
            self.assertEqual(len(frame), n, f"Expected {n} segments")

    def test_scroll_mirror_symmetry(self) -> None:
        renderer = SegmentRenderer(segments=7, mode=RenderMode.SCROLL, mirror=True)
        intent = LightingIntent(
            mode=EffectMode.RIPPLE, intensity=1.0, speed=0.5, bpm=120.0,
            color="#ff0000",
        )
        frame = renderer.render(1.0, intent, beat=True)
        # Should be symmetric around center
        n = len(frame)
        for i in range(n // 2):
            self.assertEqual(frame[i], frame[n - 1 - i])

    def test_scroll_color_shifts_outward_over_time(self) -> None:
        renderer = SegmentRenderer(segments=9, mode=RenderMode.SCROLL, mirror=True)
        intent = LightingIntent(
            mode=EffectMode.RIPPLE, intensity=1.0, speed=0.5, bpm=120.0,
            color="#ff0000",
        )
        # Beat at t=1.0
        renderer.render(1.0, intent, beat=True)
        center = 4

        # Simulate ~10 frames at 30fps (0.33s)
        # pixels_per_sec = (120/60) * 0.5 * 5 = 5, so ~1.65 pixels shifted
        for i in range(10):
            renderer.render(1.0 + (i + 1) * 0.033, intent, beat=False)

        frame = renderer.render(1.35, intent, beat=False)

        # Color should have shifted from center to neighboring positions
        has_nonzero_off_center = any(
            frame[i][0] > 0 for i in range(len(frame)) if i != center
        )
        self.assertTrue(has_nonzero_off_center, "Color should have shifted from center")

    def test_scroll_second_beat_different_color(self) -> None:
        renderer = SegmentRenderer(segments=9, mode=RenderMode.SCROLL, mirror=True)
        intent_red = LightingIntent(
            mode=EffectMode.RIPPLE, intensity=1.0, speed=0.5, bpm=120.0,
            color="#ff0000",
        )
        intent_green = LightingIntent(
            mode=EffectMode.RIPPLE, intensity=1.0, speed=0.5, bpm=120.0,
            color="#00ff00",
        )
        renderer.render(1.0, intent_red, beat=True)
        # Simulate ~30fps frames so red shifts outward by at least 1 pixel
        # pixels_per_sec = (120/60) * 0.5 * 5 = 5, need 0.2s for 1 pixel shift
        for i in range(15):
            renderer.render(1.0 + (i + 1) * 0.033, intent_red, beat=False)
        # Second beat with green (~0.5s later)
        frame = renderer.render(1.5, intent_green, beat=True)
        center = 4
        # Center should be green
        self.assertGreater(frame[center][1], 100)  # green channel
        # Should still have some red in non-center positions
        has_red = any(frame[i][0] > 0 for i in range(len(frame)) if i != center)
        self.assertTrue(has_red, "Red should still exist in shifted positions")

    def test_scroll_respects_intensity(self) -> None:
        renderer = SegmentRenderer(segments=5, mode=RenderMode.SCROLL, mirror=True)
        intent = LightingIntent(
            mode=EffectMode.RIPPLE, intensity=0.5, speed=0.5, bpm=120.0,
            color="#ff0000",
        )
        frame = renderer.render(1.0, intent, beat=True)
        center = 2
        # At 50% intensity, red channel should be ~127
        self.assertAlmostEqual(frame[center][0], 127, delta=2)

    def test_scroll_non_mirror_mode(self) -> None:
        renderer = SegmentRenderer(segments=5, mode=RenderMode.SCROLL, mirror=False)
        intent = LightingIntent(
            mode=EffectMode.RIPPLE, intensity=1.0, speed=0.5, bpm=120.0,
            color="#ff0000",
        )
        frame = renderer.render(1.0, intent, beat=True)
        self.assertEqual(len(frame), 5)
        # First pixel should have color (non-mirror uses left-to-right)
        self.assertGreater(frame[0][0], 0)



class WaveRenderTests(unittest.TestCase):
    def test_wave_correct_segment_count(self) -> None:
        for n in [3, 5, 10]:
            renderer = SegmentRenderer(segments=n, mode=RenderMode.WAVE)
            intent = LightingIntent(
                mode=EffectMode.AMBIENT, intensity=1.0, speed=0.2, bpm=60.0,
                color="#ff0000",
            )
            frame = renderer.render(1.0, intent)
            self.assertEqual(len(frame), n)

    def test_wave_segments_differ(self) -> None:
        renderer = SegmentRenderer(segments=7, mode=RenderMode.WAVE)
        intent = LightingIntent(
            mode=EffectMode.AMBIENT, intensity=1.0, speed=0.2, bpm=60.0,
            color="#ff0000",
        )
        renderer.render(1.0, intent)  # init
        frame = renderer.render(1.25, intent)
        # Segments should NOT all be the same (spatial wave)
        unique_values = set(pixel[0] for pixel in frame)
        self.assertGreater(len(unique_values), 1, "Wave should produce varied brightness per segment")

    def test_wave_oscillates_over_time(self) -> None:
        renderer = SegmentRenderer(segments=5, mode=RenderMode.WAVE)
        intent = LightingIntent(
            mode=EffectMode.AMBIENT, intensity=1.0, speed=0.2, bpm=60.0,
            color="#ff0000",
        )
        # Sample first segment over time
        values = []
        for i in range(20):
            t = 1.0 + i * 0.05
            frame = renderer.render(t, intent)
            values.append(frame[0][0])
        self.assertGreater(max(values), 100)
        self.assertLess(min(values), 50)

    def test_wave_respects_intensity(self) -> None:
        r1 = SegmentRenderer(segments=5, mode=RenderMode.WAVE)
        r2 = SegmentRenderer(segments=5, mode=RenderMode.WAVE)
        intent_full = LightingIntent(
            mode=EffectMode.AMBIENT, intensity=1.0, speed=0.2, bpm=60.0,
            color="#ff0000",
        )
        intent_half = LightingIntent(
            mode=EffectMode.AMBIENT, intensity=0.5, speed=0.2, bpm=60.0,
            color="#ff0000",
        )
        r1.render(1.0, intent_full)
        r2.render(1.0, intent_half)
        f1 = r1.render(1.25, intent_full)
        f2 = r2.render(1.25, intent_half)
        # Half intensity should produce roughly half brightness
        if f1[0][0] > 0:
            ratio = f2[0][0] / f1[0][0]
            self.assertAlmostEqual(ratio, 0.5, delta=0.05)

    def test_wave_rate_mult_param(self) -> None:
        r_fast = SegmentRenderer(segments=5, mode=RenderMode.WAVE)
        r_slow = SegmentRenderer(segments=5, mode=RenderMode.WAVE)
        intent = LightingIntent(
            mode=EffectMode.AMBIENT, intensity=1.0, speed=0.2, bpm=120.0,
            color="#ff0000",
        )
        r_fast.render(1.0, intent, params={"wave_rate_mult": 2.0})
        r_slow.render(1.0, intent, params={"wave_rate_mult": 0.5})
        f_fast = r_fast.render(1.2, intent, params={"wave_rate_mult": 2.0})
        f_slow = r_slow.render(1.2, intent, params={"wave_rate_mult": 0.5})
        # Different rates should produce different outputs for segment 0
        self.assertNotEqual(f_fast[0], f_slow[0])

    def test_wave_uses_default_color_when_none(self) -> None:
        renderer = SegmentRenderer(segments=3, mode=RenderMode.WAVE)
        intent = LightingIntent(
            mode=EffectMode.AMBIENT, intensity=1.0, speed=0.2, bpm=60.0,
        )
        frame = renderer.render(1.0, intent)
        # Should produce non-zero output using default color
        total = sum(sum(p) for p in frame)
        self.assertGreater(total, 0)

    def test_wave_single_segment(self) -> None:
        renderer = SegmentRenderer(segments=1, mode=RenderMode.WAVE)
        intent = LightingIntent(
            mode=EffectMode.AMBIENT, intensity=1.0, speed=0.2, bpm=60.0,
            color="#ff0000",
        )
        frame = renderer.render(1.0, intent)
        self.assertEqual(len(frame), 1)


class GradientRenderTests(unittest.TestCase):
    def test_gradient_correct_segment_count(self) -> None:
        for n in [3, 5, 10]:
            renderer = SegmentRenderer(segments=n, mode=RenderMode.GRADIENT)
            intent = LightingIntent(
                mode=EffectMode.AMBIENT, intensity=1.0, speed=0.2, bpm=60.0,
                color="#ff0000",
            )
            frame = renderer.render(1.0, intent)
            self.assertEqual(len(frame), n)

    def test_gradient_with_colors_param(self) -> None:
        renderer = SegmentRenderer(segments=5, mode=RenderMode.GRADIENT)
        intent = LightingIntent(
            mode=EffectMode.AMBIENT, intensity=1.0, speed=0.2, bpm=60.0,
        )
        params = {
            "gradient_colors": ("#ff0000", "#0000ff"),
            "gradient_speed": 0.0,  # no rotation
        }
        frame = renderer.render(1.0, intent, params=params)
        # First segment should be red
        self.assertEqual(frame[0], (255, 0, 0))
        # Last segment should be blue
        self.assertEqual(frame[4], (0, 0, 255))
        # Middle segment should be a blend
        self.assertGreater(frame[2][0], 0)
        self.assertGreater(frame[2][2], 0)

    def test_gradient_interpolation_correctness(self) -> None:
        renderer = SegmentRenderer(segments=3, mode=RenderMode.GRADIENT)
        intent = LightingIntent(
            mode=EffectMode.AMBIENT, intensity=1.0, speed=0.2, bpm=60.0,
        )
        params = {
            "gradient_colors": ("#ff0000", "#0000ff"),
            "gradient_speed": 0.0,
        }
        frame = renderer.render(1.0, intent, params=params)
        # seg 0: pos=0.0 → pure red
        self.assertEqual(frame[0], (255, 0, 0))
        # seg 1: pos=0.5 → 50/50 blend
        self.assertEqual(frame[1][0], 127)  # half red
        self.assertEqual(frame[1][2], 127)  # half blue
        # seg 2: pos=1.0 → pure blue
        self.assertEqual(frame[2], (0, 0, 255))

    def test_gradient_rotates_over_time(self) -> None:
        renderer = SegmentRenderer(segments=5, mode=RenderMode.GRADIENT)
        intent = LightingIntent(
            mode=EffectMode.AMBIENT, intensity=1.0, speed=0.2, bpm=60.0,
        )
        params = {
            "gradient_colors": ("#ff0000", "#0000ff"),
            "gradient_speed": 1.0,  # fast rotation
        }
        frame1 = renderer.render(1.0, intent, params=params)
        frame2 = renderer.render(1.5, intent, params=params)
        # After rotation, first segment should have changed
        self.assertNotEqual(frame1[0], frame2[0])

    def test_gradient_static_when_speed_zero(self) -> None:
        renderer = SegmentRenderer(segments=5, mode=RenderMode.GRADIENT)
        intent = LightingIntent(
            mode=EffectMode.AMBIENT, intensity=1.0, speed=0.2, bpm=60.0,
        )
        params = {
            "gradient_colors": ("#ff0000", "#0000ff"),
            "gradient_speed": 0.0,
        }
        renderer.render(1.0, intent, params=params)
        frame1 = renderer.render(1.1, intent, params=params)
        frame2 = renderer.render(1.5, intent, params=params)
        self.assertEqual(frame1, frame2)

    def test_gradient_respects_intensity(self) -> None:
        renderer = SegmentRenderer(segments=3, mode=RenderMode.GRADIENT)
        intent = LightingIntent(
            mode=EffectMode.AMBIENT, intensity=0.5, speed=0.2, bpm=60.0,
        )
        params = {
            "gradient_colors": ("#ff0000", "#0000ff"),
            "gradient_speed": 0.0,
        }
        frame = renderer.render(1.0, intent, params=params)
        # First segment: pure red at 50% intensity
        self.assertEqual(frame[0], (127, 0, 0))

    def test_gradient_fallback_single_color(self) -> None:
        renderer = SegmentRenderer(segments=3, mode=RenderMode.GRADIENT)
        intent = LightingIntent(
            mode=EffectMode.AMBIENT, intensity=1.0, speed=0.2, bpm=60.0,
            color="#ff0000",
        )
        # No gradient_colors param — falls back to color-to-black
        params = {"gradient_speed": 0.0}
        frame = renderer.render(1.0, intent, params=params)
        self.assertEqual(frame[0], (255, 0, 0))
        self.assertEqual(frame[2], (0, 0, 0))

    def test_gradient_three_color_stops(self) -> None:
        renderer = SegmentRenderer(segments=5, mode=RenderMode.GRADIENT)
        intent = LightingIntent(
            mode=EffectMode.AMBIENT, intensity=1.0, speed=0.2, bpm=60.0,
        )
        params = {
            "gradient_colors": ("#ff0000", "#00ff00", "#0000ff"),
            "gradient_speed": 0.0,
        }
        frame = renderer.render(1.0, intent, params=params)
        # seg 0: pure red
        self.assertEqual(frame[0], (255, 0, 0))
        # seg 2: pure green (midpoint)
        self.assertEqual(frame[2], (0, 255, 0))
        # seg 4: pure blue
        self.assertEqual(frame[4], (0, 0, 255))

    def test_gradient_single_segment(self) -> None:
        renderer = SegmentRenderer(segments=1, mode=RenderMode.GRADIENT)
        intent = LightingIntent(
            mode=EffectMode.AMBIENT, intensity=1.0, speed=0.2, bpm=60.0,
        )
        params = {
            "gradient_colors": ("#ff0000", "#0000ff"),
            "gradient_speed": 0.0,
        }
        frame = renderer.render(1.0, intent, params=params)
        self.assertEqual(len(frame), 1)


class ParamOverrideTests(unittest.TestCase):
    """Test that params dict correctly overrides defaults in existing modes."""

    def test_pulse_decay_param(self) -> None:
        r_fast = SegmentRenderer(segments=3, mode=RenderMode.PULSE)
        r_slow = SegmentRenderer(segments=3, mode=RenderMode.PULSE)
        intent = LightingIntent(
            mode=EffectMode.PULSE, intensity=1.0, speed=0.5, bpm=120.0,
            color="#ff0000",
        )
        r_fast.render(1.0, intent, beat=True, params={"pulse_decay": 20.0})
        r_slow.render(1.0, intent, beat=True, params={"pulse_decay": 2.0})
        # After 0.2s, fast decay should be dimmer than slow decay
        f_fast = r_fast.render(1.2, intent, beat=False, params={"pulse_decay": 20.0})
        f_slow = r_slow.render(1.2, intent, beat=False, params={"pulse_decay": 2.0})
        self.assertLess(f_fast[0][0], f_slow[0][0])

    def test_breathe_rate_mult_param(self) -> None:
        r_fast = SegmentRenderer(segments=3, mode=RenderMode.BREATHE)
        r_slow = SegmentRenderer(segments=3, mode=RenderMode.BREATHE)
        intent = LightingIntent(
            mode=EffectMode.AMBIENT, intensity=1.0, speed=0.2, bpm=120.0,
            color="#ff0000",
        )
        r_fast.render(1.0, intent, params={"breathe_rate_mult": 2.0})
        r_slow.render(1.0, intent, params={"breathe_rate_mult": 0.5})
        f_fast = r_fast.render(1.2, intent, params={"breathe_rate_mult": 2.0})
        f_slow = r_slow.render(1.2, intent, params={"breathe_rate_mult": 0.5})
        # Different rates → different phase → different brightness
        self.assertNotEqual(f_fast[0], f_slow[0])

    def test_scroll_inject_width_param(self) -> None:
        r_wide = SegmentRenderer(segments=15, mode=RenderMode.SCROLL, mirror=True)
        r_narrow = SegmentRenderer(segments=15, mode=RenderMode.SCROLL, mirror=True)
        intent = LightingIntent(
            mode=EffectMode.RIPPLE, intensity=1.0, speed=0.5, bpm=120.0,
            color="#ff0000",
        )
        f_wide = r_wide.render(1.0, intent, beat=True, params={"scroll_inject_width": 0.5})
        f_narrow = r_narrow.render(1.0, intent, beat=True, params={"scroll_inject_width": 0.1})
        # Wide injection should have more non-zero pixels than narrow
        wide_lit = sum(1 for p in f_wide if p[0] > 0)
        narrow_lit = sum(1 for p in f_narrow if p[0] > 0)
        self.assertGreater(wide_lit, narrow_lit)


if __name__ == "__main__":
    unittest.main()
