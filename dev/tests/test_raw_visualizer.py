from __future__ import annotations

import pytest

from dreamsync.director import EffectMode, LightingIntent
from dreamsync.raw_visualizer import (
    RAW_VISUALIZER_COLORS,
    RawFrequencyVisualizer,
    RawPaletteRuntime,
    sample_palette_colors,
)
from dreamsync.output.govee_lan import MultiGoveeLanAdapter
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


def test_custom_five_point_gradient_tracks_spectrum() -> None:
    points = (
        (60.0, "#110000"),
        (250.0, "#221100"),
        (1000.0, "#002200"),
        (4000.0, "#001122"),
        (12000.0, "#220044"),
    )
    visualizer = RawFrequencyVisualizer(
        attack=1.0,
        release=1.0,
        gradient_points=points,
    )

    frame = visualizer.update(
        rms=0.18,
        band_ratios=(),
        magnitude=(0.0, 0.0, 1.0, 0.0, 0.0),
        frequencies=(60.0, 250.0, 1000.0, 4000.0, 12000.0),
    )

    assert len(frame.levels) == 5
    assert frame.levels[2] == 1.0
    assert frame.colors == tuple(color for _frequency, color in points)
    assert frame.frequencies == tuple(
        frequency for frequency, _color in points
    )


def test_single_frequency_color_point_is_supported() -> None:
    visualizer = RawFrequencyVisualizer(
        attack=1.0,
        release=1.0,
        gradient_points=((440.0, "#123456"),),
    )

    frame = visualizer.update(
        rms=0.18,
        band_ratios=(),
        magnitude=(1.0,),
        frequencies=(440.0,),
    )

    assert frame.levels == (1.0,)
    assert frame.colors == ("#123456",)
    assert frame.frequencies == (440.0,)


def test_saved_palette_is_sampled_across_frequency_points() -> None:
    assert sample_palette_colors(
        ("#ff0000", "#0000ff"),
        3,
    ) == ("#ff0000", "#800080", "#0000ff")


def test_raw_palette_runtime_hot_swaps_and_auto_chains() -> None:
    runtime = RawPaletteRuntime(("#101010", "#202020", "#303030"))
    control = {
        "revision": 1,
        "pool": (
            ("warm", ("#ff0000", "#ffff00", "#ffffff")),
            ("cool", ("#0000ff", "#00ffff", "#ffffff")),
        ),
        "selected_name": "warm",
        "auto_chain": True,
        "interval_seconds": 2.0,
    }

    runtime.update(0.0, control)
    assert runtime.active_name == "warm"
    assert runtime.queue == ("warm", "cool")
    assert runtime.colors(3)[0] == "#ff0000"
    assert runtime.seconds_until_next(0.5) == 1.5

    runtime.update(2.0, control)
    assert runtime.active_name == "cool"
    assert runtime.queue == ("cool", "warm")
    assert runtime.colors(3)[0] == "#0000ff"

    runtime.update(
        2.1,
        {
            **control,
            "revision": 2,
            "selected_name": "warm",
            "auto_chain": False,
        },
    )
    assert runtime.active_name == "warm"
    assert runtime.seconds_until_next(2.1) is None


def test_raw_palette_pool_preserves_custom_gradient_until_selected() -> None:
    runtime = RawPaletteRuntime(("#110000", "#001100", "#000011"))
    runtime.update(
        0.0,
        {
            "revision": 1,
            "pool": (
                ("saved", ("#abcdef", "#123456", "#654321")),
            ),
            "selected_name": "",
            "use_custom": True,
            "auto_chain": False,
        },
    )

    assert runtime.active_name == "custom_frequency_gradient"
    assert runtime.colors(3) == ("#110000", "#001100", "#000011")


def test_higher_noise_threshold_reduces_sensitivity() -> None:
    sensitive = RawFrequencyVisualizer(
        silence_rms=0.0,
        attack=1.0,
        release=1.0,
    )
    insensitive = RawFrequencyVisualizer(
        silence_rms=0.04,
        attack=1.0,
        release=1.0,
    )
    ratios = (0.12, 0.12, 0.12, 0.2, 0.18, 0.13, 0.13)

    sensitive_frame = sensitive.update(rms=0.05, band_ratios=ratios)
    insensitive_frame = insensitive.update(rms=0.05, band_ratios=ratios)

    assert sensitive_frame.intensity > insensitive_frame.intensity


def test_renderer_uses_configured_origin_node() -> None:
    renderer = SegmentRenderer(segments=7, mode=RenderMode.SOLID)
    intent = LightingIntent(
        mode=EffectMode.AMBIENT,
        intensity=1.0,
        speed=0.0,
        bpm=0.0,
        color="#ff0000",
    )
    colors = renderer.render(
        0.0,
        intent,
        params={
            "raw_visualizer_levels": (0.2,),
            "raw_visualizer_colors": ("#ff0000",),
            "raw_visualizer_origin_index": 5,
        },
    )

    assert colors[5][0] > 0
    assert colors[0] == (0, 0, 0)


def test_device_origin_is_selected_by_strip_address() -> None:
    params = {
        "raw_visualizer_levels": (0.5, 0.25),
        "raw_visualizer_origins": {
            "192.0.2.10": 2,
            "192.0.2.11": 7,
        },
    }

    first = MultiGoveeLanAdapter._raw_visualizer_device_params(
        params,
        "192.0.2.10",
    )
    second = MultiGoveeLanAdapter._raw_visualizer_device_params(
        params,
        "192.0.2.11",
    )

    assert first["raw_visualizer_origin_index"] == 2
    assert second["raw_visualizer_origin_index"] == 7
    assert "raw_visualizer_origin_index" not in params
