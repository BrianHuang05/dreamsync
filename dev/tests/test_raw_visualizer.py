from __future__ import annotations

import pytest

from dreamsync.director import EffectMode, LightingIntent
from dreamsync.raw_visualizer import (
    RAW_VISUALIZER_COLORS,
    RawFrequencyVisualizer,
)
from dreamsync.render import RenderMode, SegmentRenderer


def test_frequency_visualizer_louder_audio_fills_more() -> None:
    visualizer = RawFrequencyVisualizer(attack=1.0, release=1.0)

    quiet = visualizer.update(
        rms=0.02,
        band_ratios=(0.12, 0.12, 0.12, 0.2, 0.18, 0.13, 0.13),
    )
    loud = visualizer.update(
        rms=0.18,
        band_ratios=(0.12, 0.12, 0.12, 0.2, 0.18, 0.13, 0.13),
    )

    assert loud.intensity > quiet.intensity
    assert all(
        loud_level > quiet_level
        for loud_level, quiet_level in zip(loud.levels, quiet.levels)
    )


@pytest.mark.parametrize(
    ("ratios", "dominant_index"),
    [
        ((0.3, 0.3, 0.3, 0.03, 0.02, 0.02, 0.02), 0),
        ((0.02, 0.02, 0.02, 0.42, 0.42, 0.05, 0.05), 1),
        ((0.02, 0.02, 0.02, 0.03, 0.03, 0.44, 0.44), 2),
    ],
)
def test_frequency_visualizer_maps_bass_mids_and_highs(
    ratios: tuple[float, ...],
    dominant_index: int,
) -> None:
    visualizer = RawFrequencyVisualizer(attack=1.0, release=1.0)
    frame = visualizer.update(rms=0.18, band_ratios=ratios)

    assert frame.levels[dominant_index] == max(frame.levels)
    assert frame.render_params()["raw_visualizer_colors"] == (
        "#ff0000",
        "#00ff00",
        "#8f00ff",
    )
    assert visualizer.intent(frame).bpm == 0.0


def test_segment_renderer_fills_symmetrically_from_center() -> None:
    renderer = SegmentRenderer(
        segments=9,
        mode=RenderMode.SOLID,
        mirror=False,
    )
    intent = LightingIntent(
        mode=EffectMode.AMBIENT,
        intensity=1.0,
        speed=0.0,
        bpm=0.0,
        color=RAW_VISUALIZER_COLORS[0],
    )
    params = {
        "raw_visualizer_levels": (0.5, 0.0, 0.0),
        "raw_visualizer_colors": RAW_VISUALIZER_COLORS,
    }

    colors = renderer.render(0.0, intent, params=params)

    assert colors == list(reversed(colors))
    assert colors[4][0] > 0
    assert colors[0] == (0, 0, 0)
    assert colors[-1] == (0, 0, 0)
    assert all(green == 0 and blue == 0 for red, green, blue in colors)


def test_segment_renderer_uses_frequency_band_colors() -> None:
    renderer = SegmentRenderer(segments=5, mode=RenderMode.SOLID)
    intent = LightingIntent(
        mode=EffectMode.AMBIENT,
        intensity=1.0,
        speed=0.0,
        bpm=0.0,
        color="#ffffff",
    )

    bass = renderer.render(
        0.0,
        intent,
        params={
            "raw_visualizer_levels": (1.0, 0.0, 0.0),
            "raw_visualizer_colors": RAW_VISUALIZER_COLORS,
        },
    )
    highs = renderer.render(
        0.1,
        intent,
        params={
            "raw_visualizer_levels": (0.0, 0.0, 1.0),
            "raw_visualizer_colors": RAW_VISUALIZER_COLORS,
        },
    )

    assert bass[2][0] > bass[2][1] == bass[2][2]
    assert highs[2][2] > highs[2][0] > highs[2][1]
