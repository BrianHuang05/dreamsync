from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

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
    "bottom": -1.0,
    "floor": -1.0,
    "low": -1.0,
    "center": 0.0,
    "mid": 0.0,
    "middle": 0.0,
    "top": 1.0,
    "high": 1.0,
    "ceiling": 1.0,
}

_Z_ALIASES: dict[str, float] = {
    "front": -1.0,
    "center": 0.0,
    "back": 1.0,
}

_LEGACY_Y_DEPTH_ALIASES = frozenset(_Z_ALIASES)


@dataclass(frozen=True)
class SectionPlacement:
    """Normalized placement for one addressable strip section."""

    index: int
    x: float
    y: float
    z: float
    weight: float = 1.0
    enabled: bool = True


@dataclass(frozen=True)
class DevicePlacement:
    """Normalized room placement for a device."""

    x: float
    y: float
    z: float = 0.0
    orientation: DeviceOrientation = DeviceOrientation.LEFT_TO_RIGHT
    weight: float = 1.0
    enabled: bool = True
    sections: tuple[SectionPlacement, ...] = ()


@dataclass(frozen=True)
class SpatialCellState:
    """Per-cell lighting state in the spatial scene."""

    intent: LightingIntent
    emphasis: float
    phase_offset: float = 0.0
    color_shift: float = 0.0
    params: dict[str, object] | None = None


@dataclass(frozen=True)
class SpatialLayerSpec:
    """One additive route-driven layer applied on top of a base spatial spec."""

    band: str
    weight: float
    instrument: str = ""
    blend_mode: str = "max"
    color_override: str | None = None
    params: dict[str, object] | None = None


def parse_device_placement(entry: Mapping[str, object]) -> DevicePlacement | None:
    """Parse coordinate-based placement fields from a YAML device entry."""

    sections = _parse_section_placements(entry)
    has_x_alias = "x_position" in entry
    has_y_alias = "y_position" in entry
    has_z_alias = "z_position" in entry
    has_x_numeric = "x" in entry
    has_y_numeric = "y" in entry
    has_z_numeric = "z" in entry

    if not any((has_x_alias, has_y_alias, has_z_alias, has_x_numeric, has_y_numeric, has_z_numeric)) and not sections:
        return None

    if has_x_alias and has_x_numeric:
        raise ValueError("Device entry cannot define both 'x_position' and 'x'.")
    if has_y_alias and has_y_numeric:
        raise ValueError("Device entry cannot define both 'y_position' and 'y'.")
    if has_z_alias and has_z_numeric:
        raise ValueError("Device entry cannot define both 'z_position' and 'z'.")

    legacy_depth_raw = None
    if has_y_alias and not has_y_numeric and not has_z_alias and not has_z_numeric:
        maybe_legacy = entry.get("y_position")
        if isinstance(maybe_legacy, str) and maybe_legacy.strip().lower() in _LEGACY_Y_DEPTH_ALIASES:
            legacy_depth_raw = maybe_legacy

    if any((has_x_alias, has_x_numeric, has_y_alias, has_y_numeric, has_z_alias, has_z_numeric)):
        x = _parse_axis_value(
            entry.get("x_position") if has_x_alias else entry.get("x"),
            axis_name="x",
            aliases=_X_ALIASES,
        )
        y = _parse_axis_value(
            entry.get("y_position") if has_y_alias and legacy_depth_raw is None else entry.get("y", 0.0),
            axis_name="y",
            aliases=_Y_ALIASES,
        )
        if legacy_depth_raw is not None:
            z = _parse_axis_value(
                legacy_depth_raw,
                axis_name="z",
                aliases=_Z_ALIASES,
            )
        else:
            z = _parse_axis_value(
                entry.get("z_position") if has_z_alias else entry.get("z", 0.0),
                axis_name="z",
                aliases=_Z_ALIASES,
            )
    else:
        x, y, z = _average_section_coordinates(sections)

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
        z=z,
        orientation=orientation,
        weight=weight,
        enabled=enabled,
        sections=sections,
    )


def _parse_axis_value(
    raw: object,
    *,
    axis_name: str,
    aliases: Mapping[str, float],
    allow_aliases: bool = True,
) -> float:
    if raw is None:
        raise ValueError(f"Missing required '{axis_name}' spatial value.")

    if isinstance(raw, str):
        if not allow_aliases:
            raise ValueError(f"{axis_name} must be a numeric value in the range [-1.0, 1.0]")
        key = raw.strip().lower()
        if key not in aliases:
            allowed = ", ".join(sorted(aliases))
            raise ValueError(f"{axis_name}_position must be one of: {allowed}")
        return aliases[key]

    value = float(raw)
    if not -1.0 <= value <= 1.0:
        raise ValueError(f"{axis_name} must be in the range [-1.0, 1.0]")
    return value


def placement_to_mapping(placement: DevicePlacement) -> dict[str, object]:
    """Serialize a placement to editor-friendly numeric fields."""
    data: dict[str, object] = {
        "x": float(placement.x),
        "y": float(placement.y),
        "z": float(placement.z),
        "orientation": placement.orientation.value,
        "weight": float(placement.weight),
        "enabled": bool(placement.enabled),
    }
    if placement.sections:
        data["sections"] = [
            {
                "index": int(section.index),
                "x": float(section.x),
                "y": float(section.y),
                "z": float(section.z),
                "weight": float(section.weight),
                "enabled": bool(section.enabled),
            }
            for section in placement.sections
        ]
    return data


def _parse_section_placements(entry: Mapping[str, object]) -> tuple[SectionPlacement, ...]:
    raw_sections = entry.get("sections")
    if not isinstance(raw_sections, list):
        return ()

    sections: list[SectionPlacement] = []
    for offset, raw_section in enumerate(raw_sections):
        if not isinstance(raw_section, Mapping):
            continue
        index = int(raw_section.get("index", offset))
        x = _parse_axis_value(raw_section.get("x", 0.0), axis_name="x", aliases=_X_ALIASES)
        y = _parse_axis_value(raw_section.get("y", 0.0), axis_name="y", aliases=_Y_ALIASES)
        z = _parse_axis_value(raw_section.get("z", 0.0), axis_name="z", aliases=_Z_ALIASES)
        weight = float(raw_section.get("weight", 1.0))
        if weight <= 0.0:
            raise ValueError("section weight must be > 0")
        sections.append(
            SectionPlacement(
                index=index,
                x=x,
                y=y,
                z=z,
                weight=weight,
                enabled=bool(raw_section.get("enabled", True)),
            )
        )

    return tuple(sorted(sections, key=lambda section: section.index))


def _average_section_coordinates(sections: tuple[SectionPlacement, ...]) -> tuple[float, float, float]:
    if not sections:
        return 0.0, 0.0, 0.0
    count = float(len(sections))
    return (
        sum(section.x for section in sections) / count,
        sum(section.y for section in sections) / count,
        sum(section.z for section in sections) / count,
    )
