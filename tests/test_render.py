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


if __name__ == "__main__":
    unittest.main()
