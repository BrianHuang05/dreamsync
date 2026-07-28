"""Tests for coordinate-based spatial placement parsing."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dreamsync.output.auto_detect import load_device_config
from dreamsync.spatial.models import (
    DeviceOrientation,
    DevicePlacement,
    SectionPlacement,
    SpatialEffectLayer,
    SpatialFalloff,
    SpatialLayerCategory,
    SpatialTriggerMode,
    parse_device_placement,
    placement_to_mapping,
)


class ParseDevicePlacementTests(unittest.TestCase):
    def test_no_spatial_fields_returns_none(self) -> None:
        self.assertIsNone(parse_device_placement({"address": "10.0.0.1"}))

    def test_left_top_front_aliases_parse_to_canonical_axes(self) -> None:
        placement = parse_device_placement(
            {"x_position": "left", "y_position": "top", "z_position": "front"}
        )
        self.assertEqual(placement, DevicePlacement(x=-1.0, y=1.0, z=-1.0))

    def test_center_alias_parses_to_zero(self) -> None:
        placement = parse_device_placement(
            {"x_position": "center", "y_position": "center", "z_position": "center"}
        )
        assert placement is not None
        self.assertEqual(placement.x, 0.0)
        self.assertEqual(placement.y, 0.0)
        self.assertEqual(placement.z, 0.0)

    def test_right_bottom_back_aliases_parse_to_positive_depth(self) -> None:
        placement = parse_device_placement(
            {"x_position": "right", "y_position": "bottom", "z_position": "back"}
        )
        assert placement is not None
        self.assertEqual(placement.x, 1.0)
        self.assertEqual(placement.y, -1.0)
        self.assertEqual(placement.z, 1.0)

    def test_legacy_front_back_y_alias_maps_to_depth_for_compatibility(self) -> None:
        placement = parse_device_placement({"x_position": "left", "y_position": "front"})
        self.assertEqual(placement, DevicePlacement(x=-1.0, y=0.0, z=-1.0))

    def test_numeric_coordinates_parse(self) -> None:
        placement = parse_device_placement({"x": 0.25, "y": -0.75})
        self.assertEqual(placement, DevicePlacement(x=0.25, y=-0.75))

    def test_numeric_coordinates_parse_with_z(self) -> None:
        placement = parse_device_placement({"x": 0.25, "y": -0.75, "z": 0.5})
        self.assertEqual(placement, DevicePlacement(x=0.25, y=-0.75, z=0.5))

    def test_invalid_x_alias_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "x_position"):
            parse_device_placement({"x_position": "west", "y_position": "front"})

    def test_invalid_y_alias_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "y_position|z"):
            parse_device_placement({"x_position": "left", "y_position": "north"})

    def test_invalid_z_alias_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "z_position"):
            parse_device_placement(
                {"x_position": "left", "y_position": "center", "z_position": "north"}
            )

    def test_out_of_range_x_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "x must be"):
            parse_device_placement({"x": 1.1, "y": 0.0})

    def test_out_of_range_y_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "y must be"):
            parse_device_placement({"x": 0.0, "y": -1.1})

    def test_default_orientation_applied(self) -> None:
        placement = parse_device_placement({"x_position": "left", "y_position": "center"})
        assert placement is not None
        self.assertEqual(placement.orientation, DeviceOrientation.LEFT_TO_RIGHT)

    def test_invalid_orientation_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "orientation"):
            parse_device_placement({
                "x_position": "left",
                "y_position": "center",
                "orientation": "backwards",
            })

    def test_default_weight_applied(self) -> None:
        placement = parse_device_placement({"x_position": "left", "y_position": "center"})
        assert placement is not None
        self.assertEqual(placement.weight, 1.0)

    def test_nonpositive_weight_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "weight"):
            parse_device_placement({
                "x_position": "left",
                "y_position": "center",
                "weight": 0.0,
            })

    def test_out_of_range_z_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "z must be"):
            parse_device_placement({"x": 0.0, "y": 0.0, "z": 1.1})

    def test_both_alias_and_numeric_axis_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "x_position"):
            parse_device_placement({
                "x_position": "left",
                "x": -1.0,
                "y_position": "center",
            })

    def test_placement_to_mapping_includes_z(self) -> None:
        mapping = placement_to_mapping(DevicePlacement(x=0.2, y=-0.2, z=0.8))
        self.assertEqual(mapping["z"], 0.8)

    def test_sections_are_parsed_and_serialized(self) -> None:
        placement = parse_device_placement(
            {
                "x": 0.0,
                "y": 0.0,
                "z": 0.0,
                "sections": [
                    {"index": 0, "x": -1.0, "y": 0.2, "z": -0.5},
                    {"index": 1, "x": 1.0, "y": 0.8, "z": 0.5},
                ],
            }
        )
        assert placement is not None
        self.assertEqual(
            placement.sections,
            (
                SectionPlacement(index=0, x=-1.0, y=0.2, z=-0.5),
                SectionPlacement(index=1, x=1.0, y=0.8, z=0.5),
            ),
        )
        mapping = placement_to_mapping(placement)
        self.assertEqual(len(mapping["sections"]), 2)

    def test_sections_without_root_position_average_to_device_position(self) -> None:
        placement = parse_device_placement(
            {
                "sections": [
                    {"index": 0, "x": -1.0, "y": -1.0, "z": -1.0},
                    {"index": 1, "x": 1.0, "y": 1.0, "z": 1.0},
                ]
            }
        )
        self.assertEqual(placement, DevicePlacement(x=0.0, y=0.0, z=0.0, sections=(
            SectionPlacement(index=0, x=-1.0, y=-1.0, z=-1.0),
            SectionPlacement(index=1, x=1.0, y=1.0, z=1.0),
        )))


class SpatialEffectLayerTests(unittest.TestCase):
    def test_effect_layer_mapping_roundtrips_json_shape(self) -> None:
        layer = SpatialEffectLayer(
            category=SpatialLayerCategory.SLICE,
            effect_mode="pulse",
            trigger_mode=SpatialTriggerMode.ONESHOT,
            origin=(-1.0, 0.0, 0.25),
            direction=(1.0, 0.0, 0.0),
            extent=((-1.0, -0.5, -1.0), (1.0, 0.5, 1.0)),
            thickness=0.2,
            radius=0.1,
            speed_units_per_second=1.4,
            falloff=SpatialFalloff.SMOOTHSTEP,
            palette=("#112233", "#445566"),
            color_bias="#abcdef",
            intensity_scale=1.2,
            time_offset_s=0.5,
            duration_s=1.5,
            coordinate_scale=1.0,
        )
        data = layer.to_mapping()
        self.assertEqual(data["layer_category"], "slice")
        self.assertEqual(data["trigger_mode"], "oneshot")
        self.assertEqual(data["direction"], {"x": 1.0, "y": 0.0, "z": 0.0})
        self.assertEqual(data["palette"], ["#112233", "#445566"])
        self.assertEqual(SpatialEffectLayer.from_mapping(data), layer)


class LoadDeviceConfigSpatialTests(unittest.TestCase):
    def test_device_config_without_spatial_fields_still_valid(self) -> None:
        yaml_content = {"devices": [{"address": "10.0.0.1"}]}
        with patch("dreamsync.output.auto_detect._require_yaml") as mock_yaml:
            mock_module = MagicMock()
            mock_module.safe_load.return_value = yaml_content
            mock_yaml.return_value = mock_module

            with patch("builtins.open", unittest.mock.mock_open()):
                configs = load_device_config(Path("test.yaml"))

        self.assertIsNone(configs[0].placement)

    def test_template_examples_load(self) -> None:
        yaml_content = {
            "devices": [
                {
                    "name": "Desk Left",
                    "address": "10.0.0.100",
                    "x_position": "left",
                    "y_position": "front",
                },
                {
                    "name": "Rear Lamp",
                    "address": "AA:BB:CC:DD:EE:FF",
                    "x": 0.0,
                    "y": 0.8,
                },
            ]
        }
        with patch("dreamsync.output.auto_detect._require_yaml") as mock_yaml:
            mock_module = MagicMock()
            mock_module.safe_load.return_value = yaml_content
            mock_yaml.return_value = mock_module

            with patch("builtins.open", unittest.mock.mock_open()):
                configs = load_device_config(Path("test.yaml"))

        self.assertEqual(len(configs), 2)
        self.assertIsNotNone(configs[0].placement)
        self.assertIsNotNone(configs[1].placement)


if __name__ == "__main__":
    unittest.main()
