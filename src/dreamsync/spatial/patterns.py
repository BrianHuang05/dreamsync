from __future__ import annotations

import colorsys

from .models import GridCell


def cell_coordinates(cell: GridCell) -> tuple[int, int]:
    """Return coarse grid coordinates for a 3x3 cell.

    Returns ``(x, y)`` in ``{-1, 0, 1}``, where y is front -> back.
    """

    if cell == GridCell.FRONT_LEFT:
        return -1, -1
    if cell == GridCell.FRONT_CENTER:
        return 0, -1
    if cell == GridCell.FRONT_RIGHT:
        return 1, -1
    if cell == GridCell.CENTER_LEFT:
        return -1, 0
    if cell == GridCell.CENTER:
        return 0, 0
    if cell == GridCell.CENTER_RIGHT:
        return 1, 0
    if cell == GridCell.BACK_LEFT:
        return -1, 1
    if cell == GridCell.BACK_CENTER:
        return 0, 1
    return 1, 1


def shift_hex_color(color: str | None, amount: float) -> str | None:
    """Apply a small hue shift to a hex color."""

    if not color:
        return color
    h = color.lstrip("#")
    if len(h) != 6:
        return color
    r = int(h[0:2], 16) / 255.0
    g = int(h[2:4], 16) / 255.0
    b = int(h[4:6], 16) / 255.0
    hue, sat, val = colorsys.rgb_to_hsv(r, g, b)
    hue = (hue + amount) % 1.0
    nr, ng, nb = colorsys.hsv_to_rgb(hue, sat, val)
    return f"#{int(nr * 255):02x}{int(ng * 255):02x}{int(nb * 255):02x}"
