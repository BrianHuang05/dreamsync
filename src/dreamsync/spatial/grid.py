from __future__ import annotations

from typing import Sequence

from .models import DevicePlacement, GridCell


def resolve_grid_cell(placement: DevicePlacement | None) -> GridCell | None:
    """Resolve a placement to one of the MVP 3x3 grid cells."""

    if placement is None:
        return None
    return resolve_grid_cell_from_coordinates(placement.x, placement.y)


def resolve_grid_cell_from_coordinates(x: float, y: float) -> GridCell:
    """Bucket normalized coordinates into the 3x3 grid."""

    col = _bucket_x(x)
    row = _bucket_y(y)

    if row == "front":
        if col == "left":
            return GridCell.FRONT_LEFT
        if col == "center":
            return GridCell.FRONT_CENTER
        return GridCell.FRONT_RIGHT
    if row == "center":
        if col == "left":
            return GridCell.CENTER_LEFT
        if col == "center":
            return GridCell.CENTER
        return GridCell.CENTER_RIGHT
    if col == "left":
        return GridCell.BACK_LEFT
    if col == "center":
        return GridCell.BACK_CENTER
    return GridCell.BACK_RIGHT


def format_mapping_table(
    devices: Sequence[tuple[str, DevicePlacement | None, str | None, str | None]],
) -> str:
    """Format a simple placement table for CLI validation/debug output."""

    header = (
        f"{'Device':20s} {'X':>6s} {'Y':>6s} "
        f"{'Cell':14s} {'Orientation':14s} {'Role':10s}"
    )
    lines = [header, "-" * len(header)]

    for name, placement, orientation, role in devices:
        if placement is None:
            x_str = "-"
            y_str = "-"
            cell_str = "unplaced"
            orientation_str = orientation or "-"
        else:
            x_str = f"{placement.x:.2f}"
            y_str = f"{placement.y:.2f}"
            cell = resolve_grid_cell(placement)
            cell_str = cell.value if cell is not None else "unplaced"
            orientation_str = placement.orientation.value
        lines.append(
            f"{name:20.20s} {x_str:>6s} {y_str:>6s} "
            f"{cell_str:14.14s} {orientation_str:14.14s} {(role or '-'):10.10s}"
        )

    return "\n".join(lines)


def _bucket_x(value: float) -> str:
    """Bucket x into left/center/right."""

    if value < -0.33:
        return "left"
    if value > 0.33:
        return "right"
    return "center"


def _bucket_y(value: float) -> str:
    """Bucket y into front/center/back."""

    if value < -0.33:
        return "front"
    if value > 0.33:
        return "back"
    return "center"
