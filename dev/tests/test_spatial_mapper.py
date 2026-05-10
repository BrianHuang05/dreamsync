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
