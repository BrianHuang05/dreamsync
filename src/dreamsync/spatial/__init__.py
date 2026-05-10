"""Spatial placement helpers for room-aware device mapping."""

from .grid import format_mapping_table, resolve_grid_cell, resolve_grid_cell_from_coordinates
from .models import (
    DeviceOrientation,
    DevicePlacement,
    GridCell,
    SpatialCellState,
    parse_device_placement,
)

__all__ = [
    "DeviceOrientation",
    "DevicePlacement",
    "GridCell",
    "SpatialCellState",
    "format_mapping_table",
    "parse_device_placement",
    "resolve_grid_cell",
    "resolve_grid_cell_from_coordinates",
]
