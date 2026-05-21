"""Tests for pure 3x3 spatial scene generation."""

from __future__ import annotations

import unittest

from dreamsync.director import EffectMode, LightingIntent
from dreamsync.show.models import ShowCue
from dreamsync.spatial.mapper import SpatialMapper
from dreamsync.spatial.models import GridCell


def _intent(
    *,
    mode: EffectMode = EffectMode.MOTION,
    intensity: float = 0.5,
    speed: float = 0.4,
    bpm: float = 120.0,
    color: str | None = "#3366ff",
) -> LightingIntent:
    return LightingIntent(mode=mode, intensity=intensity, speed=speed, bpm=bpm, color=color)


def _cue(render_mode: str = "scroll", params: dict | None = None) -> ShowCue:
    return ShowCue(
        t=0.0,
        render_mode=render_mode,
        color_palette=("#3366ff", "#66ccff"),
        intensity=0.5,
        speed=0.4,
        params=params or {},
        transition="cut",
        transition_beats=0,
    )


class SpatialMapperTests(unittest.TestCase):
    def test_mapper_returns_all_nine_cells(self) -> None:
        mapper = SpatialMapper(enabled=True)
        scene = mapper.map_reactive(0.0, _intent())
        self.assertEqual(len(scene), 9)
        self.assertEqual(set(scene.keys()), set(GridCell))

    def test_solid_mode_center_emphasis_is_small_and_stable(self) -> None:
        mapper = SpatialMapper(enabled=True, center_gain=0.2)
        scene = mapper.map_reactive(0.0, _intent(mode=EffectMode.AMBIENT))
        center = scene[GridCell.CENTER].intent.intensity
        corner = scene[GridCell.FRONT_LEFT].intent.intensity
        self.assertGreater(center, corner)

    def test_pulse_front_center_bias_applied(self) -> None:
        mapper = SpatialMapper(enabled=True)
        scene = mapper.map_reactive(0.0, _intent(mode=EffectMode.PULSE))
        self.assertGreater(
            scene[GridCell.FRONT_CENTER].intent.intensity,
            scene[GridCell.BACK_CENTER].intent.intensity,
        )

    def test_breathe_front_back_phase_split(self) -> None:
        mapper = SpatialMapper(enabled=True)
        cue = _cue("breathe", {"spatial_axis": "depth"})
        scene = mapper.map_show_cue(0.0, cue, _intent(mode=EffectMode.AMBIENT))
        self.assertLess(scene[GridCell.FRONT_CENTER].phase_offset, 0.0)
        self.assertGreater(scene[GridCell.BACK_CENTER].phase_offset, 0.0)

    def test_scroll_defaults_to_horizontal_axis(self) -> None:
        mapper = SpatialMapper(enabled=True)
        scene = mapper.map_show_cue(0.0, _cue("scroll"), _intent())
        self.assertLess(scene[GridCell.CENTER_LEFT].phase_offset, 0.0)
        self.assertEqual(scene[GridCell.CENTER].phase_offset, 0.0)
        self.assertGreater(scene[GridCell.CENTER_RIGHT].phase_offset, 0.0)

    def test_scroll_depth_axis_override_respected(self) -> None:
        mapper = SpatialMapper(enabled=True)
        cue = _cue("scroll", {"spatial_axis": "depth"})
        scene = mapper.map_show_cue(0.0, cue, _intent())
        self.assertLess(scene[GridCell.FRONT_CENTER].phase_offset, 0.0)
        self.assertGreater(scene[GridCell.BACK_CENTER].phase_offset, 0.0)

    def test_wave_left_right_phase_pattern(self) -> None:
        mapper = SpatialMapper(enabled=True)
        cue = _cue("wave")
        scene = mapper.map_show_cue(0.0, cue, _intent())
        self.assertLess(scene[GridCell.CENTER_LEFT].phase_offset, 0.0)
        self.assertGreater(scene[GridCell.CENTER_RIGHT].phase_offset, 0.0)

    def test_gradient_depth_bias_respected(self) -> None:
        mapper = SpatialMapper(enabled=True)
        cue = _cue("gradient")
        scene = mapper.map_show_cue(0.0, cue, _intent(mode=EffectMode.AMBIENT))
        self.assertNotEqual(
            scene[GridCell.FRONT_CENTER].intent.color,
            scene[GridCell.BACK_CENTER].intent.color,
        )

    def test_center_column_bridges_horizontal_motion(self) -> None:
        mapper = SpatialMapper(enabled=True, center_gain=0.2)
        scene = mapper.map_show_cue(0.0, _cue("scroll"), _intent())
        center = scene[GridCell.CENTER].intent.intensity
        left = scene[GridCell.CENTER_LEFT].intent.intensity
        self.assertGreater(center, left)

    def test_center_row_bridges_depth_motion(self) -> None:
        mapper = SpatialMapper(enabled=True, center_gain=0.2)
        scene = mapper.map_show_cue(0.0, _cue("gradient"), _intent(mode=EffectMode.AMBIENT))
        center = scene[GridCell.CENTER].intent.intensity
        front = scene[GridCell.FRONT_CENTER].intent.intensity
        self.assertGreater(center, front)

    def test_disabled_mapper_returns_balanced_scene(self) -> None:
        mapper = SpatialMapper(enabled=False)
        scene = mapper.map_reactive(0.0, _intent())
        intensities = {cell_state.intent.intensity for cell_state in scene.values()}
        self.assertEqual(intensities, {0.5})

    def test_show_cue_without_spatial_params_still_maps(self) -> None:
        mapper = SpatialMapper(enabled=True)
        scene = mapper.map_show_cue(0.0, _cue("pulse"), _intent(mode=EffectMode.PULSE))
        self.assertEqual(len(scene), 9)

    def test_show_cue_spatial_focus_center_respected(self) -> None:
        mapper = SpatialMapper(enabled=True, center_gain=0.25)
        cue = _cue("solid", {"spatial_focus": "center"})
        scene = mapper.map_show_cue(0.0, cue, _intent(mode=EffectMode.AMBIENT))
        self.assertGreater(
            scene[GridCell.CENTER].intent.intensity,
            scene[GridCell.FRONT_LEFT].intent.intensity,
        )

    def test_invalid_spatial_axis_falls_back_cleanly(self) -> None:
        mapper = SpatialMapper(enabled=True)
        cue = _cue("scroll", {"spatial_axis": "nonsense"})
        scene = mapper.map_show_cue(0.0, cue, _intent())
        self.assertLess(scene[GridCell.CENTER_LEFT].phase_offset, 0.0)
        self.assertGreater(scene[GridCell.CENTER_RIGHT].phase_offset, 0.0)

    def test_resolve_spatial_spec_uses_center_ripple_preset_for_ripple_intent(self) -> None:
        mapper = SpatialMapper(enabled=True)
        spec = mapper.resolve_spatial_spec(
            _intent(mode=EffectMode.RIPPLE),
            params=None,
        )
        self.assertEqual(spec.mode, "emanation")
        self.assertEqual(spec.origin, (0.0, 0.0, 0.0))

    def test_resolve_spatial_spec_accepts_vertical_aliases(self) -> None:
        mapper = SpatialMapper(enabled=True)
        spec = mapper.resolve_spatial_spec(
            _intent(),
            params={"spatial_mode": "wave", "spatial_axis": "vertical"},
        )
        self.assertEqual(spec.direction, (0.0, 1.0, 0.0))

    def test_resolve_spatial_spec_can_apply_named_flash_preset(self) -> None:
        mapper = SpatialMapper(enabled=True)
        spec = mapper.resolve_spatial_spec(
            _intent(mode=EffectMode.AMBIENT),
            params={"spatial_preset": "flash_top_only"},
        )
        self.assertEqual(spec.mode, "wash")
        self.assertIsNotNone(spec.extent)
        assert spec.extent is not None
        self.assertGreater(spec.extent[0][1], 0.0)

    def test_resolve_spatial_layers_extracts_multiple_band_layers(self) -> None:
        mapper = SpatialMapper(enabled=True)
        base_spec, layers = mapper.resolve_spatial_layers(
            _intent(mode=EffectMode.AMBIENT),
            params={
                "spatial_mode": "wash",
                "eq_layers": [
                    {"band": "bass", "spatial_preset": "flash_floor_only", "color_bias": "#ff8800"},
                    {"band": "presence", "spatial_preset": "flash_top_only", "color_bias": "#66ccff"},
                ],
            },
        )
        self.assertEqual(base_spec.mode, "wash")
        self.assertEqual(len(layers), 2)
        self.assertEqual(layers[0].layer.band, "bass")
        self.assertEqual(layers[0].spec.mode, "wash")
        self.assertEqual(layers[1].layer.band, "presence")
        self.assertEqual(layers[1].layer.color_override, "#66ccff")

    def test_resolve_spatial_layers_preserves_instrument_pan_metadata(self) -> None:
        mapper = SpatialMapper(enabled=True)
        _base_spec, layers = mapper.resolve_spatial_layers(
            _intent(mode=EffectMode.AMBIENT),
            params={
                "spatial_mode": "wash",
                "eq_layers": [
                    {
                        "instrument": "vocals",
                        "spatial_preset": "blend_front_to_back",
                        "spatial_origin": {"x": 0.55, "y": 0.0, "z": -1.0},
                        "spatial_width": 0.42,
                        "color_bias": "#ddeeff",
                    },
                ],
            },
        )
        self.assertEqual(len(layers), 1)
        self.assertEqual(layers[0].layer.instrument, "vocals")
        self.assertEqual(layers[0].spec.origin, (0.55, 0.0, -1.0))
        self.assertEqual(layers[0].spec.width, 0.42)

    def test_mapping_is_deterministic(self) -> None:
        mapper = SpatialMapper(enabled=True)
        scene_a = mapper.map_show_cue(0.0, _cue("wave"), _intent())
        scene_b = mapper.map_show_cue(0.0, _cue("wave"), _intent())
        self.assertEqual(scene_a, scene_b)

    def test_color_spread_applies_small_variation_without_palette_breakage(self) -> None:
        mapper = SpatialMapper(enabled=True, color_spread=0.02)
        scene = mapper.map_show_cue(0.0, _cue("scroll"), _intent(color="#3366ff"))
        self.assertNotEqual(
            scene[GridCell.CENTER_LEFT].intent.color,
            scene[GridCell.CENTER_RIGHT].intent.color,
        )
        for cell_state in scene.values():
            self.assertTrue(cell_state.intent.color.startswith("#"))
            self.assertEqual(len(cell_state.intent.color), 7)


if __name__ == "__main__":
    unittest.main()
