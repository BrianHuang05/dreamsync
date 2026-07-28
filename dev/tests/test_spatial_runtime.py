"""Tests for placement-aware multi-device spatial routing."""

from __future__ import annotations

import unittest

from dreamsync.director import EffectMode, LightingIntent
from dreamsync.output.govee_lan import MultiGoveeLanAdapter
from dreamsync.output.null_adapter import NullMultiAdapter
from dreamsync.output.roles import DeviceRole
from dreamsync.spatial.mapper import SpatialMapper
from dreamsync.spatial.models import (
    DeviceOrientation,
    DevicePlacement,
    GridCell,
    SectionPlacement,
    SpatialCellState,
)


class _FakeAdapter:
    def __init__(self, *, returns: bool = True):
        self.returns = returns
        self.frames: list[list[tuple[int, int, int]]] = []
        self.config = type("Config", (), {"device_ip": "fake-ip"})()

    def send_frame(self, colors):
        self.frames.append(colors)
        return self.returns

    def turn_on(self):
        pass

    def set_brightness(self, brightness):
        pass


class _FakeRenderer:
    def __init__(self, colors: list[tuple[int, int, int]] | None = None):
        self.calls: list[dict] = []
        self.mirror = True
        self._colors = colors or [(1, 2, 3)]
        self.segments = len(self._colors)

    def render(self, t, intent, beat=False, params=None):
        self.calls.append({
            "t": t,
            "intent": intent,
            "beat": beat,
            "params": params,
            "mirror": self.mirror,
        })
        return list(self._colors)


class _FakeBleFollower:
    def __init__(self):
        self.calls: list[tuple[float, LightingIntent]] = []

    def emit(self, t, intent):
        self.calls.append((t, intent))

    def start(self):
        pass

    def stop(self):
        pass


class _CountingSpatialMapper(SpatialMapper):
    def __init__(self):
        super().__init__(enabled=True)
        self.resolve_calls = 0

    def resolve_spatial_layers(self, intent, *, params=None):
        self.resolve_calls += 1
        return super().resolve_spatial_layers(intent, params=params)


def _intent(intensity: float = 0.5, color: str = "#3366ff") -> LightingIntent:
    return LightingIntent(
        mode=EffectMode.PULSE,
        intensity=intensity,
        speed=0.4,
        bpm=120.0,
        color=color,
    )


def _scene(center_intensity: float = 0.5) -> dict[GridCell, SpatialCellState]:
    scene: dict[GridCell, SpatialCellState] = {}
    for cell in GridCell:
        base = _intent(intensity=center_intensity, color="#3366ff")
        if cell == GridCell.FRONT_LEFT:
            base = _intent(intensity=0.8, color="#ff0000")
        elif cell == GridCell.CENTER:
            base = _intent(intensity=center_intensity, color="#00ff00")
        elif cell == GridCell.BACK_RIGHT:
            base = _intent(intensity=0.3, color="#0000ff")
        scene[cell] = SpatialCellState(intent=base, emphasis=1.0, params={"cell": cell.value})
    return scene


class SpatialRuntimeTests(unittest.TestCase):
    def test_legacy_four_tuple_still_normalizes(self) -> None:
        multi = MultiGoveeLanAdapter([(_FakeAdapter(), _FakeRenderer(), DeviceRole.PRIMARY, 1.0)])
        self.assertEqual(len(multi.devices[0]), 5)
        self.assertIsNone(multi.devices[0][4])

    def test_five_tuple_with_placement_normalizes(self) -> None:
        placement = DevicePlacement(x=0.0, y=0.0)
        multi = MultiGoveeLanAdapter([
            (_FakeAdapter(), _FakeRenderer(), DeviceRole.PRIMARY, 1.0, placement)
        ])
        self.assertEqual(multi.devices[0][4], placement)

    def test_spatial_off_uses_legacy_send_path(self) -> None:
        adapter = _FakeAdapter()
        renderer = _FakeRenderer()
        multi = MultiGoveeLanAdapter([(adapter, renderer, DeviceRole.PRIMARY)])
        multi.send_frame(0.0, _intent(), beat=True)
        self.assertEqual(len(renderer.calls), 1)
        self.assertEqual(renderer.calls[0]["intent"].color, "#3366ff")

    def test_spatial_on_uses_continuous_path_with_render_mode_defaults(self) -> None:
        adapter = _FakeAdapter()
        renderer = _FakeRenderer(colors=[(100, 100, 100)])
        mapper = SpatialMapper(enabled=True)
        left = DevicePlacement(x=-1.0, y=0.0, z=0.0)
        right = DevicePlacement(x=1.0, y=0.0, z=0.0)
        multi = MultiGoveeLanAdapter(
            [
                (adapter, renderer, DeviceRole.PRIMARY, 1.0, left),
                (_FakeAdapter(), _FakeRenderer(colors=[(100, 100, 100)]), DeviceRole.PRIMARY, 1.0, right),
            ],
            spatial_mapper=mapper,
        )
        sent = multi.send_frame(
            0.25,
            LightingIntent(mode=EffectMode.MOTION, intensity=1.0, speed=1.0, bpm=60.0, color="#ffffff"),
            beat=False,
            params={"spatial_mode": "wave", "spatial_direction": "x+", "spatial_width": 0.25},
        )
        self.assertTrue(sent)
        left_frame = multi.devices[0][0].frames[0]
        right_frame = multi.devices[1][0].frames[0]
        self.assertGreater(sum(left_frame[0]), sum(right_frame[0]))

    def test_continuous_spatial_frame_uses_cue_local_spatial_time(self) -> None:
        left_adapter = _FakeAdapter()
        right_adapter = _FakeAdapter()
        layer = {
            "layer_category": "slice",
            "effect_mode": "pulse",
            "origin": {"x": 0.0, "y": 0.0, "z": 0.0},
            "direction": {"x": 1.0, "y": 0.0, "z": 0.0},
            "thickness": 0.2,
            "speed_units_per_second": 0.25,
            "falloff": "hard",
        }
        multi = MultiGoveeLanAdapter(
            [
                (
                    left_adapter,
                    _FakeRenderer(colors=[(100, 100, 100)]),
                    DeviceRole.PRIMARY,
                    1.0,
                    DevicePlacement(x=-1.0, y=0.0, z=0.0),
                ),
                (
                    right_adapter,
                    _FakeRenderer(colors=[(100, 100, 100)]),
                    DeviceRole.PRIMARY,
                    1.0,
                    DevicePlacement(x=1.0, y=0.0, z=0.0),
                ),
            ],
            spatial_mapper=SpatialMapper(enabled=True),
        )

        multi.send_frame(20.0, _intent(), params={"effect_layer": layer, "_spatial_t": 0.0})
        self.assertGreater(sum(left_adapter.frames[-1][0]), sum(right_adapter.frames[-1][0]))

        multi.send_frame(20.0, _intent(), params={"effect_layer": layer, "_spatial_t": 7.6})
        self.assertGreater(sum(right_adapter.frames[-1][0]), sum(left_adapter.frames[-1][0]))

    def test_spatial_control_params_are_not_forwarded_to_renderer(self) -> None:
        adapter = _FakeAdapter()
        renderer = _FakeRenderer(colors=[(100, 100, 100)])
        multi = MultiGoveeLanAdapter(
            [
                (
                    adapter,
                    renderer,
                    DeviceRole.PRIMARY,
                    1.0,
                    DevicePlacement(x=0.0, y=0.0, z=0.0),
                ),
            ],
            spatial_mapper=SpatialMapper(enabled=True),
        )
        multi.send_frame(
            0.0,
            _intent(),
            params={
                "effect_layer": {"layer_category": "slice"},
                "duration_s": 0.25,
                "spatial_width": 0.4,
                "speed_units_per_second": 0.25,
                "_spatial_t": 0.0,
                "wave_rate_mult": 1.5,
            },
        )

        render_params = renderer.calls[-1]["params"]
        self.assertEqual(render_params, {"wave_rate_mult": 1.5})

    def test_prepared_spatial_cue_skips_runtime_layer_resolution(self) -> None:
        adapter = _FakeAdapter()
        renderer = _FakeRenderer(colors=[(100, 100, 100)])
        mapper = _CountingSpatialMapper()
        multi = MultiGoveeLanAdapter(
            [
                (
                    adapter,
                    renderer,
                    DeviceRole.PRIMARY,
                    1.0,
                    DevicePlacement(x=0.0, y=0.0, z=0.0),
                ),
            ],
            spatial_mapper=mapper,
        )
        params = {
            "effect_layer": {"layer_category": "slice", "spatial_direction": "x+"},
            "_prepared_spatial_key": "cue-1",
        }

        self.assertTrue(multi.prepare_spatial_cue("cue-1", _intent(), params=params))
        multi.send_frame(0.0, _intent(), params=params)

        self.assertEqual(mapper.resolve_calls, 1)

    def test_legacy_send_path_also_hides_spatial_control_params(self) -> None:
        adapter = _FakeAdapter()
        renderer = _FakeRenderer(colors=[(100, 100, 100)])
        multi = MultiGoveeLanAdapter([(adapter, renderer, DeviceRole.PRIMARY)])

        multi.send_frame(
            0.0,
            _intent(),
            params={
                "effect_layer": {"layer_category": "slice"},
                "duration_s": 0.25,
                "_spatial_t": 0.0,
                "wave_rate_mult": 1.5,
            },
        )

        render_params = renderer.calls[-1]["params"]
        self.assertEqual(render_params, {"wave_rate_mult": 1.5})

    def test_vertical_wave_can_run_top_to_bottom(self) -> None:
        top = _FakeAdapter()
        bottom = _FakeAdapter()
        mapper = SpatialMapper(enabled=True)
        multi = MultiGoveeLanAdapter(
            [
                (top, _FakeRenderer(colors=[(100, 100, 100)]), DeviceRole.PRIMARY, 1.0, DevicePlacement(x=0.0, y=1.0, z=0.0)),
                (bottom, _FakeRenderer(colors=[(100, 100, 100)]), DeviceRole.PRIMARY, 1.0, DevicePlacement(x=0.0, y=-1.0, z=0.0)),
            ],
            spatial_mapper=mapper,
        )
        multi.send_frame(
            0.25,
            LightingIntent(mode=EffectMode.MOTION, intensity=1.0, speed=1.0, bpm=60.0, color="#ffffff"),
            params={"spatial_mode": "wave", "spatial_direction": "y-", "spatial_width": 0.25},
        )
        self.assertGreater(sum(top.frames[0][0]), sum(bottom.frames[0][0]))

    def test_depth_wave_can_run_front_to_back(self) -> None:
        front = _FakeAdapter()
        back = _FakeAdapter()
        mapper = SpatialMapper(enabled=True)
        multi = MultiGoveeLanAdapter(
            [
                (front, _FakeRenderer(colors=[(100, 100, 100)]), DeviceRole.PRIMARY, 1.0, DevicePlacement(x=0.0, y=0.0, z=-1.0)),
                (back, _FakeRenderer(colors=[(100, 100, 100)]), DeviceRole.PRIMARY, 1.0, DevicePlacement(x=0.0, y=0.0, z=1.0)),
            ],
            spatial_mapper=mapper,
        )
        multi.send_frame(
            0.25,
            LightingIntent(mode=EffectMode.MOTION, intensity=1.0, speed=1.0, bpm=60.0, color="#ffffff"),
            params={"spatial_mode": "wave", "spatial_direction": "z+", "spatial_width": 0.25},
        )
        self.assertGreater(sum(front.frames[0][0]), sum(back.frames[0][0]))

    def test_spatial_preset_can_flash_top_only(self) -> None:
        top = _FakeAdapter()
        bottom = _FakeAdapter()
        mapper = SpatialMapper(enabled=True)
        multi = MultiGoveeLanAdapter(
            [
                (top, _FakeRenderer(colors=[(100, 0, 0)]), DeviceRole.PRIMARY, 1.0, DevicePlacement(x=0.0, y=1.0, z=0.0)),
                (bottom, _FakeRenderer(colors=[(100, 0, 0)]), DeviceRole.PRIMARY, 1.0, DevicePlacement(x=0.0, y=-1.0, z=0.0)),
            ],
            spatial_mapper=mapper,
        )
        multi.send_frame(
            0.0,
            LightingIntent(mode=EffectMode.AMBIENT, intensity=1.0, speed=0.0, bpm=60.0, color="#ff0000"),
            params={"spatial_preset": "flash_top_only"},
        )
        self.assertGreater(sum(top.frames[0][0]), 0)
        self.assertEqual(sum(bottom.frames[0][0]), 0)

    def test_eq_layers_can_split_top_and_bottom_color_regions(self) -> None:
        top = _FakeAdapter()
        bottom = _FakeAdapter()
        mapper = SpatialMapper(enabled=True)
        multi = MultiGoveeLanAdapter(
            [
                (top, _FakeRenderer(colors=[(40, 40, 40)]), DeviceRole.PRIMARY, 1.0, DevicePlacement(x=0.0, y=1.0, z=0.0)),
                (bottom, _FakeRenderer(colors=[(40, 40, 40)]), DeviceRole.PRIMARY, 1.0, DevicePlacement(x=0.0, y=-1.0, z=0.0)),
            ],
            spatial_mapper=mapper,
        )
        params = {
            "spatial_mode": "wash",
            "eq_layers": [
                {"band": "bass", "spatial_preset": "flash_floor_only", "color_bias": "#ff8800", "layer_weight": 0.9},
                {"band": "presence", "spatial_preset": "flash_top_only", "color_bias": "#66ccff", "layer_weight": 0.9},
            ],
        }
        multi.send_frame(
            0.0,
            LightingIntent(mode=EffectMode.AMBIENT, intensity=1.0, speed=0.0, bpm=60.0, color="#202020"),
            params=params,
        )
        top_color = top.frames[0][0]
        bottom_color = bottom.frames[0][0]
        self.assertGreater(top_color[2], bottom_color[2])
        self.assertGreater(bottom_color[0], top_color[0])

    def test_eq_layer_blending_is_deterministic(self) -> None:
        adapter = _FakeAdapter()
        mapper = SpatialMapper(enabled=True)
        multi = MultiGoveeLanAdapter(
            [
                (
                    adapter,
                    _FakeRenderer(colors=[(50, 50, 50)]),
                    DeviceRole.PRIMARY,
                    1.0,
                    DevicePlacement(x=0.0, y=1.0, z=0.0),
                )
            ],
            spatial_mapper=mapper,
        )
        params = {
            "spatial_mode": "wash",
            "eq_layers": [
                {"band": "presence", "spatial_preset": "flash_top_only", "color_bias": "#66ccff", "layer_weight": 0.8},
                {"band": "air", "spatial_preset": "flash_top_only", "color_bias": "#dff6ff", "layer_weight": 0.7},
            ],
        }
        intent = LightingIntent(mode=EffectMode.AMBIENT, intensity=1.0, speed=0.0, bpm=60.0, color="#202020")
        multi.send_frame(0.0, intent, params=params)
        multi.send_frame(0.0, intent, params=params)
        self.assertEqual(adapter.frames[0], adapter.frames[1])

    def test_sections_override_parent_device_position_during_continuous_sampling(self) -> None:
        adapter = _FakeAdapter()
        renderer = _FakeRenderer(colors=[(100, 0, 0), (100, 0, 0), (100, 0, 0)])
        placement = DevicePlacement(
            x=0.0,
            y=0.0,
            z=0.0,
            sections=(
                SectionPlacement(index=0, x=-1.0, y=0.0, z=0.0),
                SectionPlacement(index=1, x=0.0, y=0.0, z=0.0),
                SectionPlacement(index=2, x=1.0, y=0.0, z=0.0),
            ),
        )
        multi = MultiGoveeLanAdapter(
            [(adapter, renderer, DeviceRole.PRIMARY, 1.0, placement)],
            spatial_mapper=SpatialMapper(enabled=True),
        )
        multi.send_frame(
            0.0,
            LightingIntent(mode=EffectMode.MOTION, intensity=1.0, speed=1.0, bpm=60.0, color="#ff0000"),
            params={"spatial_mode": "blend", "spatial_direction": "x+", "spatial_width": 1.0, "_spatial_palette": ("#0000ff", "#ff0000")},
        )
        colors = adapter.frames[0]
        self.assertLess(colors[0][0], colors[2][0])
        self.assertGreater(colors[0][2], colors[2][2])

    def test_ble_followers_can_carry_placement_metadata_in_continuous_mode(self) -> None:
        ble = _FakeBleFollower()
        multi = MultiGoveeLanAdapter(
            [],
            ble_followers=[(ble, DeviceRole.PRIMARY, 1.0, DevicePlacement(x=1.0, y=0.0, z=0.0))],
            spatial_mapper=SpatialMapper(enabled=True),
        )
        base = LightingIntent(mode=EffectMode.AMBIENT, intensity=1.0, speed=0.0, bpm=60.0, color="#abcdef")
        multi.send_frame(
            0.0,
            base,
            params={"spatial_mode": "blend", "spatial_direction": "x+", "_spatial_palette": ("#000000", "#ffffff")},
        )
        self.assertEqual(ble.calls[0][1].color, "#ffffff")

    def test_ble_follower_layer_color_prefers_higher_priority(self) -> None:
        ble = _FakeBleFollower()
        multi = MultiGoveeLanAdapter(
            [],
            ble_followers=[(ble, DeviceRole.PRIMARY, 1.0, DevicePlacement(x=0.0, y=1.0, z=0.0))],
            spatial_mapper=SpatialMapper(enabled=True),
        )
        multi.send_frame(
            0.0,
            LightingIntent(mode=EffectMode.AMBIENT, intensity=1.0, speed=0.0, bpm=60.0, color="#101010"),
            params={
                "spatial_mode": "wash",
                "scene_layers": [
                    {
                        "band": "presence",
                        "spatial_preset": "flash_top_only",
                        "color_bias": "#66ccff",
                        "layer_weight": 0.8,
                        "layer_priority": 1,
                    },
                    {
                        "band": "air",
                        "spatial_preset": "flash_top_only",
                        "color_bias": "#dff6ff",
                        "layer_weight": 0.7,
                        "layer_priority": 5,
                    },
                ],
            },
        )
        self.assertEqual(ble.calls[0][1].color, "#dff6ff")

    def test_spatial_on_routes_by_resolved_grid_cell(self) -> None:
        adapter = _FakeAdapter()
        renderer = _FakeRenderer()
        mapper = SpatialMapper(enabled=True)
        placement = DevicePlacement(x=-1.0, y=-1.0)
        multi = MultiGoveeLanAdapter(
            [(adapter, renderer, DeviceRole.PRIMARY, 1.0, placement)],
            spatial_mapper=mapper,
        )
        scene = _scene()
        sent = multi.send_spatial_scene(0.0, scene, base_intent=_intent())
        self.assertTrue(sent)
        self.assertEqual(renderer.calls[0]["intent"].color, "#ff0000")

    def test_two_devices_in_same_cell_receive_same_cell_state(self) -> None:
        scene = _scene()
        r1 = _FakeRenderer()
        r2 = _FakeRenderer()
        placement = DevicePlacement(x=-1.0, y=-1.0)
        multi = MultiGoveeLanAdapter([
            (_FakeAdapter(), r1, DeviceRole.PRIMARY, 1.0, placement),
            (_FakeAdapter(), r2, DeviceRole.PRIMARY, 1.0, placement),
        ])
        multi.send_spatial_scene(0.0, scene, base_intent=_intent())
        self.assertEqual(r1.calls[0]["intent"].color, r2.calls[0]["intent"].color)
        self.assertEqual(r1.calls[0]["intent"].intensity, r2.calls[0]["intent"].intensity)

    def test_center_device_receives_center_cell_state(self) -> None:
        scene = _scene(center_intensity=0.6)
        renderer = _FakeRenderer()
        placement = DevicePlacement(x=0.0, y=0.0)
        multi = MultiGoveeLanAdapter([
            (_FakeAdapter(), renderer, DeviceRole.PRIMARY, 1.0, placement)
        ])
        multi.send_spatial_scene(0.0, scene, base_intent=_intent())
        self.assertEqual(renderer.calls[0]["intent"].color, "#00ff00")
        self.assertEqual(renderer.calls[0]["intent"].intensity, 0.6)

    def test_unplaced_device_uses_legacy_or_fallback_behavior(self) -> None:
        scene = _scene(center_intensity=0.7)
        renderer = _FakeRenderer()
        multi = MultiGoveeLanAdapter([
            (_FakeAdapter(), renderer, DeviceRole.PRIMARY, 1.0, None)
        ])
        multi.send_spatial_scene(0.0, scene, base_intent=_intent())
        self.assertEqual(renderer.calls[0]["intent"].color, "#00ff00")

    def test_orientation_flip_applied_to_renderer(self) -> None:
        scene = _scene()
        renderer = _FakeRenderer()
        placement = DevicePlacement(
            x=1.0,
            y=1.0,
            orientation=DeviceOrientation.RIGHT_TO_LEFT,
        )
        multi = MultiGoveeLanAdapter([
            (_FakeAdapter(), renderer, DeviceRole.PRIMARY, 1.0, placement)
        ])
        multi.send_spatial_scene(0.0, scene, base_intent=_intent())
        self.assertFalse(renderer.calls[0]["mirror"])
        self.assertTrue(renderer.mirror)

    def test_role_transform_still_applies_after_spatial_lookup(self) -> None:
        scene = _scene(center_intensity=1.0)
        renderer = _FakeRenderer()
        placement = DevicePlacement(x=0.0, y=0.0)
        multi = MultiGoveeLanAdapter([
            (_FakeAdapter(), renderer, DeviceRole.ACCENT, 1.0, placement)
        ])
        multi.send_spatial_scene(0.0, scene, base_intent=_intent())
        self.assertLess(renderer.calls[0]["intent"].intensity, 1.0)

    def test_brightness_scale_still_applies_after_spatial_lookup(self) -> None:
        scene = _scene(center_intensity=1.0)
        renderer = _FakeRenderer()
        placement = DevicePlacement(x=0.0, y=0.0)
        multi = MultiGoveeLanAdapter([
            (_FakeAdapter(), renderer, DeviceRole.PRIMARY, 0.5, placement)
        ])
        multi.send_spatial_scene(0.0, scene, base_intent=_intent())
        self.assertEqual(renderer.calls[0]["intent"].intensity, 0.5)

    def test_ble_followers_still_receive_base_intent(self) -> None:
        ble = _FakeBleFollower()
        multi = MultiGoveeLanAdapter([], ble_followers=[ble])
        base = _intent(color="#abcdef")
        multi.send_spatial_scene(0.0, _scene(), base_intent=base)
        self.assertEqual(ble.calls[0][1], base)

    def test_null_adapter_remains_compatible(self) -> None:
        adapter = NullMultiAdapter()
        self.assertTrue(adapter.send_spatial_scene(0.0, _scene(), base_intent=_intent()))

    def test_any_sent_true_if_any_device_sent(self) -> None:
        multi = MultiGoveeLanAdapter([
            (_FakeAdapter(returns=False), _FakeRenderer(), DeviceRole.PRIMARY, 1.0, None),
            (_FakeAdapter(returns=True), _FakeRenderer(), DeviceRole.PRIMARY, 1.0, None),
        ])
        self.assertTrue(multi.send_spatial_scene(0.0, _scene(), base_intent=_intent()))

    def test_baked_frame_maps_section_keys_to_segments(self) -> None:
        adapter = _FakeAdapter()
        adapter.config.device_ip = "10.0.0.2"
        renderer = _FakeRenderer(colors=[(0, 0, 0), (0, 0, 0), (0, 0, 0)])
        multi = MultiGoveeLanAdapter([
            (adapter, renderer, DeviceRole.PRIMARY, 1.0, None)
        ])

        sent = multi.send_baked_frame(
            0.5,
            {
                "10.0.0.2#section:0": "#ff0000",
                "10.0.0.2#section:1": "#00ff00",
                "10.0.0.2#section:2": "#0000ff",
            },
        )

        self.assertTrue(sent)
        self.assertEqual(adapter.frames[0], [(255, 0, 0), (0, 255, 0), (0, 0, 255)])

    def test_baked_frame_uses_device_color_and_fallback(self) -> None:
        known = _FakeAdapter()
        known.config.device_ip = "10.0.0.2"
        missing = _FakeAdapter()
        missing.config.device_ip = "10.0.0.3"
        multi = MultiGoveeLanAdapter([
            (known, _FakeRenderer(colors=[(0, 0, 0), (0, 0, 0)]), DeviceRole.PRIMARY, 1.0, None),
            (missing, _FakeRenderer(colors=[(0, 0, 0)]), DeviceRole.PRIMARY, 1.0, None),
        ])

        multi.send_baked_frame(
            0.5,
            {"10.0.0.2": "#123456"},
            fallback_color="#010203",
        )

        self.assertEqual(known.frames[0], [(18, 52, 86), (18, 52, 86)])
        self.assertEqual(missing.frames[0], [(1, 2, 3)])

    def test_baked_frame_emits_ble_fallback_intent(self) -> None:
        ble = _FakeBleFollower()
        multi = MultiGoveeLanAdapter([], ble_followers=[ble])

        self.assertTrue(multi.send_baked_frame(1.25, {}, fallback_color="#abcdef"))

        self.assertEqual(ble.calls[0][0], 1.25)
        self.assertEqual(ble.calls[0][1].color, "#abcdef")

    def test_replace_devices_preserves_placement_metadata(self) -> None:
        placement = DevicePlacement(x=0.0, y=1.0)
        multi = MultiGoveeLanAdapter([])
        multi.replace_devices([
            (_FakeAdapter(), _FakeRenderer(), DeviceRole.PRIMARY, 1.0, placement)
        ])
        self.assertEqual(multi.devices[0][4], placement)


if __name__ == "__main__":
    unittest.main()
