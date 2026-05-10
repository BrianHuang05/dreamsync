from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from dreamsync.director import LightingIntent


class DeviceOrientation(str, Enum):
    LEFT_TO_RIGHT = "left_to_right"
    RIGHT_TO_LEFT = "right_to_left"


class GridCell(str, Enum):
    FRONT_LEFT = "front_left"
    FRONT_CENTER = "front_center"
    FRONT_RIGHT = "front_right"
    CENTER_LEFT = "center_left"
    CENTER = "center"
    CENTER_RIGHT = "center_right"
    BACK_LEFT = "back_left"
    BACK_CENTER = "back_center"
    BACK_RIGHT = "back_right"


_X_ALIASES: dict[str, float] = {
    "left": -1.0,
    "center": 0.0,
    "right": 1.0,
}

_Y_ALIASES: dict[str, float] = {
    "front": -1.0,
    "center": 0.0,
    "back": 1.0,
}


@dataclass(frozen=True)
class DevicePlacement:
    """Normalized room placement for a device."""

    x: float
    y: float
    orientation: DeviceOrientation = DeviceOrientation.LEFT_TO_RIGHT
    weight: float = 1.0
    enabled: bool = True


@dataclass(frozen=True)
class SpatialCellState:
    """Per-cell lighting state in the spatial scene."""

    intent: LightingIntent
    emphasis: float
    phase_offset: float = 0.0
    color_shift: float = 0.0
    params: dict[str, object] | None = None


def parse_device_placement(entry: Mapping[str, object]) -> DevicePlacement | None:
    """Parse coordinate-based placement fields from a YAML device entry."""

    has_x_alias = "x_position" in entry
    has_y_alias = "y_position" in entry
    has_x_numeric = "x" in entry
    has_y_numeric = "y" in entry

    if not any((has_x_alias, has_y_alias, has_x_numeric, has_y_numeric)):
        return None

    if has_x_alias and has_x_numeric:
        raise ValueError("Device entry cannot define both 'x_position' and 'x'.")
    if has_y_alias and has_y_numeric:
        raise ValueError("Device entry cannot define both 'y_position' and 'y'.")

    x = _parse_axis_value(
        entry.get("x_position") if has_x_alias else entry.get("x"),
        axis_name="x",
        aliases=_X_ALIASES,
    )
    y = _parse_axis_value(
        entry.get("y_position") if has_y_alias else entry.get("y"),
        axis_name="y",
        aliases=_Y_ALIASES,
    )

    orientation_raw = entry.get("orientation", DeviceOrientation.LEFT_TO_RIGHT.value)
    try:
        orientation = DeviceOrientation(str(orientation_raw))
    except ValueError as exc:
        raise ValueError(
            "orientation must be 'left_to_right' or 'right_to_left'"
        ) from exc

    weight = float(entry.get("weight", 1.0))
    if weight <= 0.0:
        raise ValueError("weight must be > 0")

    enabled = bool(entry.get("enabled", True))

    return DevicePlacement(
        x=x,
        y=y,
        orientation=orientation,
        weight=weight,
        enabled=enabled,
    )


def _parse_axis_value(
    raw: object,
    *,
    axis_name: str,
    aliases: Mapping[str, float],
) -> float:
    if raw is None:
        raise ValueError(f"Missing required '{axis_name}' spatial value.")

    if isinstance(raw, str):
        key = raw.strip().lower()
        if key not in aliases:
            allowed = ", ".join(sorted(aliases))
            raise ValueError(f"{axis_name}_position must be one of: {allowed}")
        return aliases[key]

    value = float(raw)
    if not -1.0 <= value <= 1.0:
        raise ValueError(f"{axis_name} must be in the range [-1.0, 1.0]")
    return value
