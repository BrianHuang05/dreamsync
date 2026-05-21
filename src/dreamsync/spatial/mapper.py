from __future__ import annotations

import math
from dataclasses import dataclass
from dataclasses import replace
from typing import Any, Literal

from dreamsync.director import EffectMode, LightingIntent
from dreamsync.show.models import ShowCue

from .models import DevicePlacement, GridCell, SpatialCellState, SpatialLayerSpec
from .patterns import cell_coordinates, shift_hex_color

SpatialAxis = Literal["x", "y", "z", "horizontal", "vertical", "depth", "diagonal", "radial"]
SpatialFocus = Literal["left", "center", "right", "front", "back", "top", "bottom", "balanced"]
SpatialMode = Literal["emanation", "wave", "blend", "wash"]
SpatialPreset = Literal[
    "ripple_left_to_right",
    "ripple_right_to_left",
    "ripple_from_center",
    "wave_top_to_bottom",
    "wave_bottom_to_top",
    "wave_front_to_back",
    "wave_back_to_front",
    "flash_top_only",
    "flash_floor_only",
    "blend_left_to_right",
    "blend_front_to_back",
]


@dataclass(frozen=True)
class SpatialSpec:
    mode: SpatialMode
    origin: tuple[float, float, float]
    direction: tuple[float, float, float] | None
    width: float
    blend: str
    extent: tuple[tuple[float, float, float], tuple[float, float, float]] | None
    delay_ms: float
    palette: tuple[str, ...] = ()


@dataclass(frozen=True)
class SpatialSample:
    intensity_scale: float
    color_override: str | None = None


@dataclass(frozen=True)
class ResolvedSpatialLayer:
    layer: SpatialLayerSpec
    spec: SpatialSpec

ALL_CELLS: tuple[GridCell, ...] = (
    GridCell.FRONT_LEFT,
    GridCell.FRONT_CENTER,
    GridCell.FRONT_RIGHT,
    GridCell.CENTER_LEFT,
    GridCell.CENTER,
    GridCell.CENTER_RIGHT,
    GridCell.BACK_LEFT,
    GridCell.BACK_CENTER,
    GridCell.BACK_RIGHT,
)

SPATIAL_PRESETS: dict[str, dict[str, object]] = {
    "ripple_left_to_right": {
        "spatial_mode": "wave",
        "spatial_direction": "x+",
        "spatial_width": 0.30,
        "spatial_blend": "smoothstep",
        "spatial_delay_ms": 120.0,
    },
    "ripple_right_to_left": {
        "spatial_mode": "wave",
        "spatial_direction": "x-",
        "spatial_width": 0.30,
        "spatial_blend": "smoothstep",
        "spatial_delay_ms": 120.0,
    },
    "ripple_from_center": {
        "spatial_mode": "emanation",
        "spatial_origin": {"x": 0.0, "y": 0.0, "z": 0.0},
        "spatial_width": 0.32,
        "spatial_blend": "radial",
        "spatial_delay_ms": 140.0,
    },
    "wave_top_to_bottom": {
        "spatial_mode": "wave",
        "spatial_direction": "y-",
        "spatial_width": 0.28,
        "spatial_blend": "smoothstep",
        "spatial_delay_ms": 115.0,
    },
    "wave_bottom_to_top": {
        "spatial_mode": "wave",
        "spatial_direction": "y+",
        "spatial_width": 0.28,
        "spatial_blend": "smoothstep",
        "spatial_delay_ms": 115.0,
    },
    "wave_front_to_back": {
        "spatial_mode": "wave",
        "spatial_direction": "z+",
        "spatial_width": 0.28,
        "spatial_blend": "smoothstep",
        "spatial_delay_ms": 115.0,
    },
    "wave_back_to_front": {
        "spatial_mode": "wave",
        "spatial_direction": "z-",
        "spatial_width": 0.28,
        "spatial_blend": "smoothstep",
        "spatial_delay_ms": 115.0,
    },
    "flash_top_only": {
        "spatial_mode": "wash",
        "spatial_extent": {
            "min": {"x": -1.0, "y": 0.35, "z": -1.0},
            "max": {"x": 1.0, "y": 1.0, "z": 1.0},
        },
    },
    "flash_floor_only": {
        "spatial_mode": "wash",
        "spatial_extent": {
            "min": {"x": -1.0, "y": -1.0, "z": -1.0},
            "max": {"x": 1.0, "y": -0.35, "z": 1.0},
        },
    },
    "blend_left_to_right": {
        "spatial_mode": "blend",
        "spatial_direction": "x+",
        "spatial_width": 1.0,
        "spatial_blend": "linear",
    },
    "blend_front_to_back": {
        "spatial_mode": "blend",
        "spatial_direction": "z+",
        "spatial_width": 1.0,
        "spatial_blend": "linear",
    },
}


class SpatialMapper:
    """Expand one global musical state into spatial routing metadata."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        mode: str = "grid_3x3",
        front_back_separation: float = 0.35,
        left_right_separation: float = 0.35,
        center_gain: float = 0.15,
        rear_delay_ms: float = 60.0,
        color_spread: float = 0.08,
    ) -> None:
        self.enabled = enabled
        self.mode = mode
        self.front_back_separation = front_back_separation
        self.left_right_separation = left_right_separation
        self.center_gain = center_gain
        self.rear_delay_ms = rear_delay_ms
        self.color_spread = color_spread

    def map_reactive(
        self,
        t: float,
        intent: LightingIntent,
        *,
        beat: bool = False,
        params: dict | None = None,
    ) -> dict[GridCell, SpatialCellState]:
        del t, beat
        params = self._normalize_spatial_params(intent, params=params)
        if not self.enabled:
            return self._balanced_scene(intent, params=params)

        axis = self._resolve_axis(
            params.get("spatial_axis"),
            fallback="x" if intent.mode == EffectMode.MOTION else "radial",
        )
        focus = self._resolve_focus(params.get("spatial_focus"))
        return self._scene_for(intent, axis=axis, focus=focus, params=params)

    def map_show_cue(
        self,
        t: float,
        cue: ShowCue,
        base_intent: LightingIntent,
    ) -> dict[GridCell, SpatialCellState]:
        del t
        params = self._normalize_spatial_params(
            base_intent,
            params=cue.params,
            render_mode=cue.render_mode,
        )
        if not self.enabled:
            return self._balanced_scene(base_intent, params=params)

        axis = self._resolve_axis(
            params.get("spatial_axis"),
            fallback=self._default_axis_for_render_mode(cue.render_mode),
        )
        focus = self._resolve_focus(params.get("spatial_focus"))
        return self._scene_for(base_intent, axis=axis, focus=focus, params=params)

    def resolve_spatial_spec(
        self,
        intent: LightingIntent,
        *,
        params: dict | None = None,
    ) -> SpatialSpec:
        params = self._normalize_spatial_params(intent, params=params)
        render_mode = str(params.get("_render_mode") or "")
        mode = self._resolve_mode(
            params.get("spatial_mode"),
            render_mode=render_mode,
            intent_mode=intent.mode,
        )
        direction = self._resolve_direction(
            params.get("spatial_direction"),
            axis=params.get("spatial_axis"),
            fallback=self._default_direction_for_mode(mode),
        )
        origin = self._resolve_origin(
            params.get("spatial_origin"),
            focus=params.get("spatial_focus"),
            mode=mode,
        )
        width = max(0.05, min(2.0, float(params.get("spatial_width", 0.35))))
        blend = str(params.get("spatial_blend", "radial" if mode == "emanation" else "linear"))
        extent = self._resolve_extent(params.get("spatial_extent"))
        delay_ms = float(params.get("spatial_delay_ms", params.get("rear_delay_ms", self.rear_delay_ms)))
        palette_raw = params.get("_spatial_palette")
        palette = tuple(str(color) for color in palette_raw) if isinstance(palette_raw, (list, tuple)) else ()
        return SpatialSpec(
            mode=mode,
            origin=origin,
            direction=direction,
            width=width,
            blend=blend,
            extent=extent,
            delay_ms=delay_ms,
            palette=palette,
        )

    def resolve_spatial_layers(
        self,
        intent: LightingIntent,
        *,
        params: dict | None = None,
    ) -> tuple[SpatialSpec, tuple[ResolvedSpatialLayer, ...]]:
        base_params = dict(params or {})
        base_spec = self.resolve_spatial_spec(intent, params=base_params)
        raw_layers = base_params.get("eq_layers")
        if not isinstance(raw_layers, list):
            return base_spec, ()

        layers: list[ResolvedSpatialLayer] = []
        for layer in raw_layers:
            if not isinstance(layer, dict):
                continue
            spatial_params = self._layer_spatial_params(base_params, layer)
            if not self._has_explicit_spatial_metadata(spatial_params):
                continue
            spec = self.resolve_spatial_spec(intent, params=spatial_params)
            weight = self._layer_weight(layer)
            if weight <= 0.0:
                continue
            layers.append(
                ResolvedSpatialLayer(
                    layer=SpatialLayerSpec(
                        band=str(layer.get("band", "")),
                        weight=weight,
                        instrument=str(layer.get("instrument", "")),
                        blend_mode=str(layer.get("layer_blend", "max") or "max"),
                        color_override=str(layer["color_bias"]) if isinstance(layer.get("color_bias"), str) else None,
                        params=spatial_params,
                    ),
                    spec=spec,
                )
            )
        return base_spec, tuple(layers)

    def sample_point(
        self,
        t: float,
        placement: DevicePlacement,
        intent: LightingIntent,
        spec: SpatialSpec,
    ) -> SpatialSample:
        if not placement.enabled:
            return SpatialSample(intensity_scale=0.0)

        point = (placement.x, placement.y, placement.z)
        extent_factor = self._extent_factor(point, spec.extent)
        if extent_factor <= 0.0:
            return SpatialSample(intensity_scale=0.0)

        if spec.mode == "wash":
            return SpatialSample(intensity_scale=max(0.0, min(1.5, extent_factor * placement.weight)))

        if spec.mode == "blend":
            blend_factor = self._blend_factor(point, spec)
            color_override = self._blend_palette(spec.palette, blend_factor)
            return SpatialSample(
                intensity_scale=max(0.0, min(1.5, extent_factor * placement.weight)),
                color_override=color_override,
            )

        freq = max(0.05, intent.speed) * max(0.5, intent.bpm / 60.0)
        if spec.mode == "wave":
            activation = self._wave_activation(t, point, spec, freq)
        else:
            activation = self._emanation_activation(t, point, spec, freq)

        return SpatialSample(
            intensity_scale=max(0.0, min(1.5, extent_factor * placement.weight * (0.15 + (0.85 * activation)))),
        )

    def _balanced_scene(
        self,
        intent: LightingIntent,
        *,
        params: dict | None = None,
    ) -> dict[GridCell, SpatialCellState]:
        return {
            cell: SpatialCellState(
                intent=replace(intent),
                emphasis=1.0,
                phase_offset=0.0,
                color_shift=0.0,
                params=dict(params or {}),
            )
            for cell in ALL_CELLS
        }

    def _scene_for(
        self,
        intent: LightingIntent,
        *,
        axis: SpatialAxis,
        focus: SpatialFocus,
        params: dict,
    ) -> dict[GridCell, SpatialCellState]:
        scene: dict[GridCell, SpatialCellState] = {}
        rear_delay_ms = float(params.get("rear_delay_ms", self.rear_delay_ms))
        color_spread = float(params.get("color_spread", self.color_spread))
        center_gain = float(params.get("center_gain", self.center_gain))

        for cell in ALL_CELLS:
            x, depth = cell_coordinates(cell)
            emphasis = self._compute_emphasis(
                intent=intent,
                axis=axis,
                focus=focus,
                x=x,
                depth=depth,
                center_gain=center_gain,
            )
            phase_offset = self._compute_phase_offset(axis=axis, x=x, depth=depth)
            color_shift = self._compute_color_shift(axis=axis, x=x, depth=depth, spread=color_spread)
            cell_params = dict(params)
            if depth > 0:
                cell_params["rear_delay_ms"] = rear_delay_ms
            shifted_color = shift_hex_color(intent.color, color_shift)
            cell_intent = replace(
                intent,
                intensity=max(0.0, min(1.0, intent.intensity * emphasis)),
                color=shifted_color,
            )
            scene[cell] = SpatialCellState(
                intent=cell_intent,
                emphasis=emphasis,
                phase_offset=phase_offset,
                color_shift=color_shift,
                params=cell_params,
            )

        return scene

    def _compute_emphasis(
        self,
        *,
        intent: LightingIntent,
        axis: SpatialAxis,
        focus: SpatialFocus,
        x: int,
        depth: int,
        center_gain: float,
    ) -> float:
        emphasis = 1.0

        if intent.mode == EffectMode.PULSE:
            if depth < 0:
                emphasis += self.front_back_separation * 0.35
            elif depth > 0:
                emphasis -= self.front_back_separation * 0.20
        elif intent.mode == EffectMode.AMBIENT:
            emphasis += center_gain * 0.35 if (x == 0 or depth == 0) else 0.0

        if axis in {"x", "horizontal"}:
            if x == 0:
                emphasis += center_gain
        elif axis in {"z", "depth"}:
            if depth == 0:
                emphasis += center_gain
        elif axis == "radial":
            if x == 0 and depth == 0:
                emphasis += center_gain
            elif x == 0 or depth == 0:
                emphasis += center_gain * 0.5
        elif axis == "diagonal":
            if x == 0 or depth == 0:
                emphasis += center_gain * 0.4

        if focus == "left" and x < 0:
            emphasis += self.left_right_separation * 0.35
        elif focus == "right" and x > 0:
            emphasis += self.left_right_separation * 0.35
        elif focus == "front" and depth < 0:
            emphasis += self.front_back_separation * 0.35
        elif focus == "back" and depth > 0:
            emphasis += self.front_back_separation * 0.35
        elif focus == "center":
            if x == 0 and depth == 0:
                emphasis += center_gain
            elif x == 0 or depth == 0:
                emphasis += center_gain * 0.5

        return max(0.05, emphasis)

    def _compute_phase_offset(self, *, axis: SpatialAxis, x: int, depth: int) -> float:
        if axis in {"x", "horizontal"}:
            return x * self.left_right_separation
        if axis in {"z", "depth"}:
            return depth * self.front_back_separation
        if axis == "diagonal":
            return (x + depth) * 0.5 * max(self.left_right_separation, self.front_back_separation)
        if axis == "radial":
            return 0.0 if (x == 0 and depth == 0) else 0.5 * (abs(x) + abs(depth))
        return 0.0

    def _compute_color_shift(self, *, axis: SpatialAxis, x: int, depth: int, spread: float) -> float:
        if spread == 0.0:
            return 0.0
        if axis in {"x", "horizontal"}:
            return x * spread
        if axis in {"z", "depth"}:
            return depth * spread
        if axis == "diagonal":
            return (x - depth) * 0.5 * spread
        if axis == "radial":
            return 0.0 if (x == 0 and depth == 0) else spread * 0.5 * (x + depth)
        return 0.0

    def _resolve_axis(self, raw: object, *, fallback: SpatialAxis) -> SpatialAxis:
        if raw in {"x", "horizontal", "left_right"}:
            return "x"
        if raw in {"y", "vertical", "up_down", "height"}:
            return "y"
        if raw in {"z", "depth", "front_back"}:
            return "z"
        if raw in {"diagonal", "radial"}:
            return raw  # type: ignore[return-value]
        return fallback

    def _resolve_focus(self, raw: object) -> SpatialFocus:
        if raw in {"left", "center", "right", "front", "back", "top", "bottom", "balanced"}:
            return raw  # type: ignore[return-value]
        return "balanced"

    def _default_axis_for_render_mode(self, render_mode: str) -> SpatialAxis:
        if render_mode in {"scroll", "wave"}:
            return "x"
        if render_mode == "gradient":
            return "z"
        return "radial"

    def _resolve_mode(
        self,
        raw: object,
        *,
        render_mode: str,
        intent_mode: EffectMode,
    ) -> SpatialMode:
        if raw in {"emanation", "wave", "blend", "wash"}:
            return raw  # type: ignore[return-value]
        if render_mode in {"scroll", "wave"}:
            return "wave"
        if render_mode == "ripple":
            return "emanation"
        if render_mode == "gradient":
            return "blend"
        if render_mode == "pulse":
            return "emanation"
        if intent_mode == EffectMode.RIPPLE:
            return "emanation"
        if intent_mode == EffectMode.MOTION:
            return "wave"
        if intent_mode == EffectMode.PULSE:
            return "emanation"
        return "wash"

    def _resolve_direction(
        self,
        raw: object,
        *,
        axis: object,
        fallback: tuple[float, float, float] | None,
    ) -> tuple[float, float, float] | None:
        if isinstance(raw, str):
            alias = raw.strip().lower()
            aliases = {
                "x+": (1.0, 0.0, 0.0),
                "x-": (-1.0, 0.0, 0.0),
                "y+": (0.0, 1.0, 0.0),
                "y-": (0.0, -1.0, 0.0),
                "z+": (0.0, 0.0, 1.0),
                "z-": (0.0, 0.0, -1.0),
                "left_to_right": (1.0, 0.0, 0.0),
                "right_to_left": (-1.0, 0.0, 0.0),
                "bottom_to_top": (0.0, 1.0, 0.0),
                "top_to_bottom": (0.0, -1.0, 0.0),
                "front_to_back": (0.0, 0.0, 1.0),
                "back_to_front": (0.0, 0.0, -1.0),
            }
            if alias in aliases:
                return aliases[alias]
        if isinstance(raw, dict):
            try:
                return self._normalize_vector(
                    float(raw.get("x", 0.0)),
                    float(raw.get("y", 0.0)),
                    float(raw.get("z", 0.0)),
                )
            except (TypeError, ValueError):
                return fallback
        if axis in {"x", "horizontal"}:
            return (1.0, 0.0, 0.0)
        if axis in {"y", "vertical"}:
            return (0.0, 1.0, 0.0)
        if axis in {"z", "depth"}:
            return (0.0, 0.0, 1.0)
        if axis == "diagonal":
            return self._normalize_vector(1.0, 0.0, 1.0)
        if axis == "radial":
            return None
        return fallback

    def _resolve_origin(
        self,
        raw: object,
        *,
        focus: object,
        mode: SpatialMode,
    ) -> tuple[float, float, float]:
        if isinstance(raw, dict):
            try:
                return (
                    max(-1.0, min(1.0, float(raw.get("x", 0.0)))),
                    max(-1.0, min(1.0, float(raw.get("y", 0.0)))),
                    max(-1.0, min(1.0, float(raw.get("z", 0.0)))),
                )
            except (TypeError, ValueError):
                return (0.0, 0.0, 0.0)
        focus_map = {
            "left": (-1.0, 0.0, 0.0),
            "right": (1.0, 0.0, 0.0),
            "top": (0.0, 1.0, 0.0),
            "bottom": (0.0, -1.0, 0.0),
            "front": (0.0, 0.0, -1.0),
            "back": (0.0, 0.0, 1.0),
            "center": (0.0, 0.0, 0.0),
            "balanced": (0.0, 0.0, 0.0),
        }
        if focus in focus_map:
            return focus_map[focus]  # type: ignore[return-value]
        if mode == "emanation":
            return (0.0, 0.0, 0.0)
        return (0.0, 0.0, 0.0)

    def _resolve_extent(
        self,
        raw: object,
    ) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
        if not isinstance(raw, dict):
            return None
        min_raw = raw.get("min")
        max_raw = raw.get("max")
        if not isinstance(min_raw, dict) or not isinstance(max_raw, dict):
            return None
        try:
            min_point = (
                float(min_raw.get("x", -1.0)),
                float(min_raw.get("y", -1.0)),
                float(min_raw.get("z", -1.0)),
            )
            max_point = (
                float(max_raw.get("x", 1.0)),
                float(max_raw.get("y", 1.0)),
                float(max_raw.get("z", 1.0)),
            )
        except (TypeError, ValueError):
            return None
        return min_point, max_point

    def _default_direction_for_mode(self, mode: SpatialMode) -> tuple[float, float, float] | None:
        if mode == "blend":
            return (0.0, 0.0, 1.0)
        if mode == "wave":
            return (1.0, 0.0, 0.0)
        return None

    def _normalize_spatial_params(
        self,
        intent: LightingIntent,
        *,
        params: dict | None = None,
        render_mode: str | None = None,
    ) -> dict[str, object]:
        normalized: dict[str, object] = dict(params or {})
        effective_render_mode = render_mode or str(
            normalized.get("_render_mode") or self._infer_render_mode_from_intent(intent)
        )

        preset_name = normalized.get("spatial_preset")
        if isinstance(preset_name, str):
            self._apply_spatial_preset(normalized, preset_name)
        elif not self._has_explicit_spatial_metadata(normalized):
            implicit = self._default_preset_for_intent(intent, render_mode=effective_render_mode)
            if implicit is not None:
                self._apply_spatial_preset(normalized, implicit)

        self._apply_legacy_spatial_compatibility(normalized)
        normalized.setdefault("_render_mode", effective_render_mode)
        return normalized

    def _infer_render_mode_from_intent(self, intent: LightingIntent) -> str:
        if intent.mode == EffectMode.RIPPLE:
            return "ripple"
        if intent.mode == EffectMode.MOTION:
            return "wave"
        if intent.mode == EffectMode.PULSE:
            return "pulse"
        return "solid"

    def _default_preset_for_intent(
        self,
        intent: LightingIntent,
        *,
        render_mode: str,
    ) -> str | None:
        if render_mode == "ripple" or intent.mode == EffectMode.RIPPLE:
            return "ripple_from_center"
        return None

    def _has_explicit_spatial_metadata(self, params: dict[str, object]) -> bool:
        return any(
            key in params
            for key in (
                "spatial_mode",
                "spatial_origin",
                "spatial_direction",
                "spatial_width",
                "spatial_blend",
                "spatial_extent",
                "spatial_delay_ms",
                "spatial_preset",
            )
        )

    def _apply_spatial_preset(self, params: dict[str, object], preset_name: str) -> None:
        preset = SPATIAL_PRESETS.get(preset_name.strip().lower())
        if preset is None:
            return
        for key, value in preset.items():
            params.setdefault(key, self._copy_spatial_value(value))

    def _apply_legacy_spatial_compatibility(self, params: dict[str, object]) -> None:
        axis = params.get("spatial_axis")
        focus = params.get("spatial_focus")

        if "spatial_direction" not in params:
            axis_direction = self._direction_alias_for_axis(axis)
            if axis_direction is not None:
                params["spatial_direction"] = axis_direction

        if "spatial_origin" not in params:
            origin = self._origin_for_focus(focus)
            if origin is not None:
                params["spatial_origin"] = origin

    def _direction_alias_for_axis(self, axis: object) -> str | None:
        if not isinstance(axis, str):
            return None
        alias = axis.strip().lower()
        if alias in {"x", "horizontal", "left_right"}:
            return "x+"
        if alias in {"y", "vertical", "up_down", "height"}:
            return "y+"
        if alias in {"z", "depth", "front_back"}:
            return "z+"
        return None

    def _origin_for_focus(self, focus: object) -> dict[str, float] | None:
        if not isinstance(focus, str):
            return None
        alias = focus.strip().lower()
        origins = {
            "left": {"x": -1.0, "y": 0.0, "z": 0.0},
            "right": {"x": 1.0, "y": 0.0, "z": 0.0},
            "top": {"x": 0.0, "y": 1.0, "z": 0.0},
            "bottom": {"x": 0.0, "y": -1.0, "z": 0.0},
            "front": {"x": 0.0, "y": 0.0, "z": -1.0},
            "back": {"x": 0.0, "y": 0.0, "z": 1.0},
            "center": {"x": 0.0, "y": 0.0, "z": 0.0},
            "balanced": {"x": 0.0, "y": 0.0, "z": 0.0},
        }
        return origins.get(alias)

    def _copy_spatial_value(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self._copy_spatial_value(item) for key, item in value.items()}
        if isinstance(value, tuple):
            return tuple(self._copy_spatial_value(item) for item in value)
        if isinstance(value, list):
            return [self._copy_spatial_value(item) for item in value]
        return value

    def _normalize_vector(
        self,
        x: float,
        y: float,
        z: float,
    ) -> tuple[float, float, float] | None:
        length = math.sqrt((x * x) + (y * y) + (z * z))
        if length <= 1e-9:
            return None
        return (x / length, y / length, z / length)

    def _extent_factor(
        self,
        point: tuple[float, float, float],
        extent: tuple[tuple[float, float, float], tuple[float, float, float]] | None,
    ) -> float:
        if extent is None:
            return 1.0
        min_point, max_point = extent
        if min_point[0] <= point[0] <= max_point[0] and min_point[1] <= point[1] <= max_point[1] and min_point[2] <= point[2] <= max_point[2]:
            return 1.0
        return 0.0

    def _blend_factor(
        self,
        point: tuple[float, float, float],
        spec: SpatialSpec,
    ) -> float:
        if spec.direction is None:
            return self._smoothstep(min(1.0, max(0.0, self._distance(point, spec.origin) / max(spec.width, 0.05))))
        projection = self._project(point, spec.origin, spec.direction)
        normalized = min(1.0, max(0.0, ((projection / max(spec.width, 0.05)) + 1.0) * 0.5))
        if spec.blend == "smoothstep":
            return self._smoothstep(normalized)
        return normalized

    def _wave_activation(
        self,
        t: float,
        point: tuple[float, float, float],
        spec: SpatialSpec,
        freq: float,
    ) -> float:
        if spec.direction is None:
            return 1.0
        projection = self._project(point, spec.origin, spec.direction)
        phase_time = (t * freq) - (projection * spec.delay_ms / 1000.0)
        front = -1.0 + (2.0 * (phase_time % 1.0))
        distance = abs(projection - front)
        return self._falloff(distance, spec.width, spec.blend)

    def _emanation_activation(
        self,
        t: float,
        point: tuple[float, float, float],
        spec: SpatialSpec,
        freq: float,
    ) -> float:
        distance = self._distance(point, spec.origin)
        phase_time = (t * freq) - (distance * spec.delay_ms / 1000.0)
        radius = 2.0 * (phase_time % 1.0)
        return self._falloff(abs(distance - radius), spec.width, spec.blend)

    def _project(
        self,
        point: tuple[float, float, float],
        origin: tuple[float, float, float],
        direction: tuple[float, float, float],
    ) -> float:
        raw = (
            ((point[0] - origin[0]) * direction[0])
            + ((point[1] - origin[1]) * direction[1])
            + ((point[2] - origin[2]) * direction[2])
        )
        return max(-1.0, min(1.0, raw / 2.0))

    def _distance(
        self,
        point: tuple[float, float, float],
        origin: tuple[float, float, float],
    ) -> float:
        dx = point[0] - origin[0]
        dy = point[1] - origin[1]
        dz = point[2] - origin[2]
        return min(2.0, math.sqrt((dx * dx) + (dy * dy) + (dz * dz)))

    def _falloff(self, distance: float, width: float, blend: str) -> float:
        normalized = min(1.0, max(0.0, distance / max(width, 0.05)))
        if blend == "smoothstep":
            return 1.0 - self._smoothstep(normalized)
        if blend == "radial":
            return max(0.0, 1.0 - (normalized * normalized))
        return max(0.0, 1.0 - normalized)

    def _smoothstep(self, value: float) -> float:
        value = min(1.0, max(0.0, value))
        return value * value * (3.0 - (2.0 * value))

    def _blend_palette(self, palette: tuple[str, ...], factor: float) -> str | None:
        if not palette:
            return None
        if len(palette) == 1:
            return palette[0]
        factor = min(1.0, max(0.0, factor))
        span = factor * (len(palette) - 1)
        index = int(span)
        if index >= len(palette) - 1:
            return palette[-1]
        blend = span - index
        return _interpolate_hex(palette[index], palette[index + 1], blend)

    def _layer_spatial_params(
        self,
        base_params: dict[str, object],
        layer: dict[str, object],
    ) -> dict[str, object]:
        params: dict[str, object] = {}
        if "_spatial_palette" in base_params:
            params["_spatial_palette"] = self._copy_spatial_value(base_params["_spatial_palette"])
        for key in (
            "spatial_mode",
            "spatial_origin",
            "spatial_direction",
            "spatial_width",
            "spatial_blend",
            "spatial_extent",
            "spatial_delay_ms",
            "spatial_preset",
            "spatial_axis",
            "spatial_focus",
        ):
            if key in layer:
                params[key] = self._copy_spatial_value(layer[key])
        return params

    @staticmethod
    def _layer_weight(layer: dict[str, object]) -> float:
        raw = layer.get("layer_weight")
        if raw is None:
            raw = 0.65 + float(layer.get("intensity_boost", 0.0) or 0.0)
        try:
            return max(0.0, min(1.5, float(raw)))
        except (TypeError, ValueError):
            return 0.0


def _interpolate_hex(a: str, b: str, t: float) -> str:
    ar = int(a[1:3], 16)
    ag = int(a[3:5], 16)
    ab = int(a[5:7], 16)
    br = int(b[1:3], 16)
    bg = int(b[3:5], 16)
    bb = int(b[5:7], 16)
    rr = int(ar + ((br - ar) * t))
    rg = int(ag + ((bg - ag) * t))
    rb = int(ab + ((bb - ab) * t))
    return f"#{rr:02x}{rg:02x}{rb:02x}"
