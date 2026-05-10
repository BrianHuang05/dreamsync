"""Tests for 3x3 spatial grid quantization and formatting."""

from __future__ import annotations

import unittest

from dreamsync.spatial.grid import (
    format_mapping_table,
    resolve_grid_cell,
    resolve_grid_cell_from_coordinates,
)
from dreamsync.spatial.models import DevicePlacement, GridCell


class ResolveGridCellFromCoordinatesTests(unittest.TestCase):
    def test_far_left_maps_left_column(self) -> None:
        self.assertEqual(
            resolve_grid_cell_from_coordinates(-1.0, 0.0),
            GridCell.CENTER_LEFT,
        )

    def test_center_x_maps_center_column(self) -> None:
        self.assertEqual(
            resolve_grid_cell_from_coordinates(0.0, 0.0),
            GridCell.CENTER,
        )

    def test_far_right_maps_right_column(self) -> None:
        self.assertEqual(
            resolve_grid_cell_from_coordinates(1.0, 0.0),
            GridCell.CENTER_RIGHT,
        )

    def test_front_y_maps_front_row(self) -> None:
        self.assertEqual(
            resolve_grid_cell_from_coordinates(0.0, -1.0),
            GridCell.FRONT_CENTER,
        )

    def test_center_y_maps_center_row(self) -> None:
        self.assertEqual(
            resolve_grid_cell_from_coordinates(0.0, 0.0),
            GridCell.CENTER,
        )

    def test_back_y_maps_back_row(self) -> None:
        self.assertEqual(
            resolve_grid_cell_from_coordinates(0.0, 1.0),
            GridCell.BACK_CENTER,
        )

    def test_front_center_maps_front_center(self) -> None:
        self.assertEqual(
            resolve_grid_cell_from_coordinates(0.0, -0.75),
            GridCell.FRONT_CENTER,
        )

    def test_center_right_maps_center_right(self) -> None:
        self.assertEqual(
            resolve_grid_cell_from_coordinates(0.8, 0.0),
            GridCell.CENTER_RIGHT,
        )

    def test_back_left_maps_back_left(self) -> None:
        self.assertEqual(
            resolve_grid_cell_from_coordinates(-0.8, 0.8),
            GridCell.BACK_LEFT,
        )

    def test_boundary_values_bucket_stably(self) -> None:
        self.assertEqual(
            resolve_grid_cell_from_coordinates(-0.33, -0.33),
            GridCell.CENTER,
        )
        self.assertEqual(
            resolve_grid_cell_from_coordinates(0.33, 0.33),
            GridCell.CENTER,
        )


class ResolveGridCellTests(unittest.TestCase):
    def test_unplaced_device_has_no_cell(self) -> None:
        self.assertIsNone(resolve_grid_cell(None))

    def test_device_placement_resolves(self) -> None:
        placement = DevicePlacement(x=1.0, y=-1.0)
        self.assertEqual(resolve_grid_cell(placement), GridCell.FRONT_RIGHT)


class FormatMappingTableTests(unittest.TestCase):
    def test_format_mapping_table_includes_resolved_cell(self) -> None:
        placement = DevicePlacement(x=0.0, y=-1.0)
        table = format_mapping_table([
            ("Monitor Bar", placement, None, "primary"),
            ("Legacy Strip", None, None, "accent"),
        ])
        self.assertIn("Monitor Bar", table)
        self.assertIn("front_center", table)
        self.assertIn("Legacy Strip", table)
        self.assertIn("unplaced", table)


if __name__ == "__main__":
    unittest.main()
