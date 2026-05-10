"""Tests for coordinate-based spatial placement parsing."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dreamsync.output.auto_detect import load_device_config
from dreamsync.spatial.models import DeviceOrientation, DevicePlacement, parse_device_placement


class ParseDevicePlacementTests(unittest.TestCase):
    def test_no_spatial_fields_returns_none(self) -> None:
        self.assertIsNone(parse_device_placement({"address": "10.0.0.1"}))

    def test_left_front_alias_parses_to_negative_coordinates(self) -> None:
        placement = parse_device_placement({"x_position": "left", "y_position": "front"})
        self.assertEqual(placement, DevicePlacement(x=-1.0, y=-1.0))

    def test_center_alias_parses_to_zero(self) -> None:
        placement = parse_device_placement({"x_position": "center", "y_position": "center"})
        assert placement is not None
        self.assertEqual(placement.x, 0.0)
        self.assertEqual(placement.y, 0.0)

    def test_right_back_alias_parses_to_positive_coordinates(self) -> None:
        placement = parse_device_placement({"x_position": "right", "y_position": "back"})
        assert placement is not None
        self.assertEqual(placement.x, 1.0)
        self.assertEqual(placement.y, 1.0)

    def test_numeric_coordinates_parse(self) -> None:
        placement = parse_device_placement({"x": 0.25, "y": -0.75})
        self.assertEqual(placement, DevicePlacement(x=0.25, y=-0.75))

    def test_invalid_x_alias_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "x_position"):
            parse_device_placement({"x_position": "west", "y_position": "front"})

    def test_invalid_y_alias_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "y_position"):
            parse_device_placement({"x_position": "left", "y_position": "north"})

    def test_out_of_range_x_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "x must be"):
            parse_device_placement({"x": 1.1, "y": 0.0})

    def test_out_of_range_y_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "y must be"):
            parse_device_placement({"x": 0.0, "y": -1.1})

    def test_default_orientation_applied(self) -> None:
        placement = parse_device_placement({"x_position": "left", "y_position": "front"})
        assert placement is not None
        self.assertEqual(placement.orientation, DeviceOrientation.LEFT_TO_RIGHT)

    def test_invalid_orientation_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "orientation"):
            parse_device_placement({
                "x_position": "left",
                "y_position": "front",
                "orientation": "backwards",
            })

    def test_default_weight_applied(self) -> None:
        placement = parse_device_placement({"x_position": "left", "y_position": "front"})
        assert placement is not None
        self.assertEqual(placement.weight, 1.0)

    def test_nonpositive_weight_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "weight"):
            parse_device_placement({
                "x_position": "left",
                "y_position": "front",
                "weight": 0.0,
            })

    def test_both_alias_and_numeric_axis_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "x_position"):
            parse_device_placement({
                "x_position": "left",
                "x": -1.0,
                "y_position": "front",
            })


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
