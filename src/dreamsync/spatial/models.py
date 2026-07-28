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


class SpatialLayerCategory(str, Enum):
    STATIC = "static"
    SLICE = "slice"
    EXPAND = "expand"


class SpatialTriggerMode(str, Enum):
    CONTINUOUS = "continuous"
    ONESHOT = "oneshot"
    LATCHED = "latched"


class SpatialFalloff(str, Enum):
    HARD = "hard"
    LINEAR = "linear"
    SMOOTHSTEP = "smoothstep"
    RADIAL = "radial"


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
    priority: int = 0
    color_override: str | None = None
    params: dict[str, object] | None = None


@dataclass(frozen=True)
class SpatialEffectLayer:
    """Canonical 3-D effecting layer resolved from cue/live spatial metadata."""

    category: SpatialLayerCategory
    effect_mode: str
    trigger_mode: SpatialTriggerMode = SpatialTriggerMode.CONTINUOUS
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
    origin_mode: str = "point"
    direction: tuple[float, float, float] | None = None
    extent: tuple[tuple[float, float, float], tuple[float, float, float]] | None = None
    thickness: float = 0.35
    radius: float = 0.0
    speed_units_per_second: float = 1.0
    falloff: SpatialFalloff = SpatialFalloff.LINEAR
    palette: tuple[str, ...] = ()
    color_bias: str | None = None
    intensity_scale: float = 1.0
    time_offset_s: float = 0.0
    duration_s: float = 0.0
    coordinate_scale: float = 1.0

    def to_mapping(self) -> dict[str, object]:
        """Serialize this layer descriptor to cue/show JSON-compatible data."""

        data: dict[str, object] = {
            "layer_category": self.category.value,
            "effect_mode": self.effect_mode,
            "trigger_mode": self.trigger_mode.value,
            "origin": _point_to_mapping(self.origin),
            "origin_mode": self.origin_mode,
            "thickness": float(self.thickness),
            "radius": float(self.radius),
            "speed_units_per_second": float(self.speed_units_per_second),
            "falloff": self.falloff.value,
            "palette": list(self.palette),
            "intensity_scale": float(self.intensity_scale),
            "time_offset_s": float(self.time_offset_s),
            "duration_s": float(self.duration_s),
            "coordinate_scale": float(self.coordinate_scale),
        }
        if self.direction is not None:
            data["direction"] = _point_to_mapping(self.direction)
        if self.extent is not None:
            data["extent"] = {
                "min": _point_to_mapping(self.extent[0]),
                "max": _point_to_mapping(self.extent[1]),
            }
        if self.color_bias is not None:
            data["color_bias"] = self.color_bias
        return data

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "SpatialEffectLayer":
        """Load a layer descriptor from cue/show JSON-compatible data."""

        category = SpatialLayerCategory(str(data.get("layer_category", SpatialLayerCategory.STATIC.value)))
        trigger_mode = SpatialTriggerMode(str(data.get("trigger_mode", SpatialTriggerMode.CONTINUOUS.value)))
        falloff = SpatialFalloff(str(data.get("falloff", SpatialFalloff.LINEAR.value)))
        direction_raw = data.get("direction")
        extent_raw = data.get("extent")
        palette_raw = data.get("palette", ())
        return cls(
            category=category,
            effect_mode=str(data.get("effect_mode", "solid")),
            trigger_mode=trigger_mode,
            origin=_point_from_mapping(data.get("origin"), default=(0.0, 0.0, 0.0)),
            origin_mode=str(data.get("origin_mode", "point") or "point"),
            direction=_point_from_mapping(direction_raw, default=(0.0, 0.0, 0.0)) if isinstance(direction_raw, Mapping) else None,
            extent=_extent_from_mapping(extent_raw),
            thickness=float(data.get("thickness", 0.35)),
            radius=float(data.get("radius", 0.0)),
            speed_units_per_second=float(data.get("speed_units_per_second", 1.0)),
            falloff=falloff,
            palette=tuple(str(color) for color in palette_raw) if isinstance(palette_raw, (list, tuple)) else (),
            color_bias=str(data["color_bias"]) if isinstance(data.get("color_bias"), str) else None,
            intensity_scale=float(data.get("intensity_scale", 1.0)),
            time_offset_s=float(data.get("time_offset_s", 0.0)),
            duration_s=float(data.get("duration_s", 0.0)),
            coordinate_scale=float(data.get("coordinate_scale", 1.0)),
        )


@dataclass(frozen=True)
class SpatialLayerState:
    """Evaluated effecting layer position/state at one runtime timestamp."""

    layer: SpatialEffectLayer
    t: float
    center: float = 0.0
    radius: float = 0.0


@dataclass(frozen=True)
class SpatialActivation:
    """Intersection payload produced when a node is sampled against a layer."""

    strength: float
    intensity_scale: float
    color_override: str | None = None
    effect_mode: str = ""


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


def _point_to_mapping(point: tuple[float, float, float]) -> dict[str, float]:
    return {"x": float(point[0]), "y": float(point[1]), "z": float(point[2])}


def _point_from_mapping(
    raw: object,
    *,
    default: tuple[float, float, float],
) -> tuple[float, float, float]:
    if not isinstance(raw, Mapping):
        return default
    return (
        float(raw.get("x", default[0])),
        float(raw.get("y", default[1])),
        float(raw.get("z", default[2])),
    )


def _extent_from_mapping(
    raw: object,
) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
    if not isinstance(raw, Mapping):
        return None
    min_raw = raw.get("min")
    max_raw = raw.get("max")
    if not isinstance(min_raw, Mapping) or not isinstance(max_raw, Mapping):
        return None
    return (
        _point_from_mapping(min_raw, default=(-1.0, -1.0, -1.0)),
        _point_from_mapping(max_raw, default=(1.0, 1.0, 1.0)),
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
