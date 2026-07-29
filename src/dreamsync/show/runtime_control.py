"""Runtime control state and override helpers for active sessions."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace
from typing import Any

from dreamsync.director import LightingIntent

from .models import ShowCue


@dataclass(frozen=True)
class RuntimeControlState:
    palette_override: tuple[str, ...] = ()
    color_bias: str = ""
    render_mode: str = ""
    effect_bank: tuple[str, ...] = ()
    intensity_multiplier: float = 1.0
    intensity_offset: float = 0.0
    speed_multiplier: float = 1.0
    speed_offset: float = 0.0
    effect_speed_beats: str = ""
    effect_origin: str = ""
    spatial_preset: str = ""
    spatial_origin: dict[str, float] | None = None
    spatial_width: float | None = None
    disable_eq_routes: bool = False
    disable_instrument_routes: bool = False
    muted_bands: tuple[str, ...] = ()
    muted_instruments: tuple[str, ...] = ()
    disabled_groups: tuple[str, ...] = ()
    enabled_groups: tuple[str, ...] = ()
    solo_groups: tuple[str, ...] = ()
    revision: int = 0

    @property
    def active(self) -> bool:
        return any(
            (
                self.palette_override,
                self.color_bias,
                self.render_mode,
                self.effect_bank,
                self.effect_speed_beats,
                self.effect_origin,
                self.spatial_preset,
                self.spatial_origin,
                self.spatial_width is not None,
                self.disable_eq_routes,
                self.disable_instrument_routes,
                self.muted_bands,
                self.muted_instruments,
                self.disabled_groups,
                self.enabled_groups,
                self.solo_groups,
                self.intensity_multiplier != 1.0,
                self.intensity_offset != 0.0,
                self.speed_multiplier != 1.0,
                self.speed_offset != 0.0,
            )
        )


class RuntimeControlBus:
    """Thread-safe runtime override store for active sessions."""

    def __init__(self, state: RuntimeControlState | None = None) -> None:
        self._lock = threading.RLock()
        self._state = state or RuntimeControlState()

    def snapshot(self) -> RuntimeControlState:
        with self._lock:
            return self._state

    def clear(self) -> RuntimeControlState:
        with self._lock:
            self._state = RuntimeControlState(revision=self._state.revision + 1)
            return self._state

    def update(self, **changes: Any) -> RuntimeControlState:
        with self._lock:
            next_state = replace(self._state, **changes, revision=self._state.revision + 1)
            self._state = next_state
            return next_state

    def set_palette(self, colors: tuple[str, ...] | list[str]) -> RuntimeControlState:
        return self.update(palette_override=tuple(colors))

    def set_color_bias(self, color: str) -> RuntimeControlState:
        return self.update(color_bias=str(color))

    def set_render_mode(self, render_mode: str) -> RuntimeControlState:
        return self.update(render_mode=str(render_mode))

    def set_dynamics(
        self,
        *,
        intensity_multiplier: float | None = None,
        intensity_offset: float | None = None,
        speed_multiplier: float | None = None,
        speed_offset: float | None = None,
    ) -> RuntimeControlState:
        changes: dict[str, Any] = {}
        if intensity_multiplier is not None:
            changes["intensity_multiplier"] = float(intensity_multiplier)
        if intensity_offset is not None:
            changes["intensity_offset"] = float(intensity_offset)
        if speed_multiplier is not None:
            changes["speed_multiplier"] = float(speed_multiplier)
        if speed_offset is not None:
            changes["speed_offset"] = float(speed_offset)
        return self.update(**changes)

    def set_route_mutes(
        self,
        *,
        disable_eq_routes: bool | None = None,
        disable_instrument_routes: bool | None = None,
        muted_bands: tuple[str, ...] | list[str] | None = None,
        muted_instruments: tuple[str, ...] | list[str] | None = None,
    ) -> RuntimeControlState:
        changes: dict[str, Any] = {}
        if disable_eq_routes is not None:
            changes["disable_eq_routes"] = bool(disable_eq_routes)
        if disable_instrument_routes is not None:
            changes["disable_instrument_routes"] = bool(disable_instrument_routes)
        if muted_bands is not None:
            changes["muted_bands"] = tuple(str(value) for value in muted_bands)
        if muted_instruments is not None:
            changes["muted_instruments"] = tuple(str(value) for value in muted_instruments)
        return self.update(**changes)

    def set_spatial_override(
        self,
        *,
        spatial_preset: str | None = None,
        spatial_origin: dict[str, float] | None = None,
        spatial_width: float | None = None,
    ) -> RuntimeControlState:
        changes: dict[str, Any] = {}
        if spatial_preset is not None:
            changes["spatial_preset"] = str(spatial_preset)
        if spatial_origin is not None:
            changes["spatial_origin"] = dict(spatial_origin)
        if spatial_width is not None:
            changes["spatial_width"] = float(spatial_width)
        return self.update(**changes)

    def set_group_state(
        self,
        *,
        disabled_groups: tuple[str, ...] | list[str] | None = None,
        enabled_groups: tuple[str, ...] | list[str] | None = None,
        solo_groups: tuple[str, ...] | list[str] | None = None,
    ) -> RuntimeControlState:
        changes: dict[str, Any] = {}
        if disabled_groups is not None:
            changes["disabled_groups"] = tuple(
                str(value).strip().lower()
                for value in disabled_groups
                if str(value).strip()
            )
        if enabled_groups is not None:
            changes["enabled_groups"] = tuple(
                str(value).strip().lower()
                for value in enabled_groups
                if str(value).strip()
            )
        if solo_groups is not None:
            changes["solo_groups"] = tuple(
                str(value).strip().lower()
                for value in solo_groups
                if str(value).strip()
            )
        return self.update(**changes)


def runtime_control_to_dict(state: RuntimeControlState | None) -> dict[str, Any]:
    if state is None:
        return {}
    return {
        "palette_override": tuple(state.palette_override),
        "color_bias": state.color_bias,
        "render_mode": state.render_mode,
        "effect_bank": tuple(state.effect_bank),
        "intensity_multiplier": state.intensity_multiplier,
        "intensity_offset": state.intensity_offset,
        "speed_multiplier": state.speed_multiplier,
        "speed_offset": state.speed_offset,
        "effect_speed_beats": state.effect_speed_beats,
        "effect_origin": state.effect_origin,
        "spatial_preset": state.spatial_preset,
        "spatial_origin": dict(state.spatial_origin) if state.spatial_origin is not None else None,
        "spatial_width": state.spatial_width,
        "disable_eq_routes": state.disable_eq_routes,
        "disable_instrument_routes": state.disable_instrument_routes,
        "muted_bands": tuple(state.muted_bands),
        "muted_instruments": tuple(state.muted_instruments),
        "disabled_groups": tuple(state.disabled_groups),
        "enabled_groups": tuple(state.enabled_groups),
        "solo_groups": tuple(state.solo_groups),
        "revision": state.revision,
        "active": state.active,
    }


def apply_runtime_control_to_cue(
    cue: ShowCue,
    state: RuntimeControlState | None,
) -> ShowCue:
    if state is None or not state.active:
        return cue

    params = _apply_runtime_control_to_params(cue.params, state)
    color_palette = _apply_palette_override(cue.color_palette, state)
    return replace(
        cue,
        render_mode=state.render_mode or cue.render_mode,
        color_palette=color_palette,
        intensity=round(
            max(0.0, min(1.0, (cue.intensity * state.intensity_multiplier) + state.intensity_offset)),
            4,
        ),
        speed=round(max(0.0, (cue.speed * state.speed_multiplier) + state.speed_offset), 4),
        params=params,
    )


def apply_runtime_control_to_intent_params(
    intent: LightingIntent,
    params: dict[str, Any] | None,
    state: RuntimeControlState | None,
) -> tuple[LightingIntent, dict[str, Any] | None]:
    if state is None or not state.active:
        return intent, params

    next_params = _apply_runtime_control_to_params(params or {}, state)
    next_params.setdefault("runtime_control", runtime_control_to_dict(state))
    # The effect bank constrains which presets the reactive structural policy
    # may choose; it must not replace the committed preset on every frame.
    selected_render_mode = state.render_mode
    if selected_render_mode:
        next_params["_render_mode"] = selected_render_mode
        if selected_render_mode == "ripple":
            next_params.setdefault("spatial_preset", "ripple_from_center")

    if state.color_bias:
        next_color = state.color_bias
    elif state.palette_override:
        next_color = _map_color_to_palette(
            intent.color,
            state.palette_override,
        )
    else:
        next_color = intent.color

    next_intensity = max(
        0.0,
        min(1.0, (intent.intensity * state.intensity_multiplier) + state.intensity_offset),
    )
    next_speed = max(0.0, (intent.speed * state.speed_multiplier) + state.speed_offset)
    return (
        LightingIntent(
            mode=intent.mode,
            intensity=next_intensity,
            speed=next_speed,
            bpm=intent.bpm,
            color=next_color,
        ),
        next_params,
    )


def _map_color_to_palette(
    source_color: str | None,
    palette: tuple[str, ...],
) -> str | None:
    """Map changing director colors onto a stable slot in an override palette."""
    colors = tuple(str(color) for color in palette if str(color).strip())
    if not colors:
        return source_color
    normalized_source = str(source_color or "").strip().casefold()
    for color in colors:
        if color.strip().casefold() == normalized_source:
            return color
    normalized = str(source_color or "").strip().lstrip("#")
    try:
        source_value = int(normalized, 16)
    except ValueError:
        source_value = 0
    return colors[source_value % len(colors)]


def _apply_palette_override(
    color_palette: tuple[str, ...],
    state: RuntimeControlState,
) -> tuple[str, ...]:
    palette = tuple(state.palette_override) if state.palette_override else tuple(color_palette)
    if state.color_bias:
        remaining = tuple(color for color in palette if color != state.color_bias)
        return (state.color_bias,) + remaining
    return palette


def _apply_runtime_control_to_params(
    params: dict[str, Any],
    state: RuntimeControlState,
) -> dict[str, Any]:
    next_params = dict(params)

    if state.render_mode:
        next_params["_render_mode"] = state.render_mode
    if state.spatial_preset:
        next_params["spatial_preset"] = state.spatial_preset
    if state.spatial_origin is not None:
        next_params["spatial_origin"] = dict(state.spatial_origin)
    if state.spatial_width is not None:
        next_params["spatial_width"] = float(state.spatial_width)

    if state.disable_eq_routes:
        next_params["eq_routes"] = []
        next_params["active_eq_routes"] = []
    if state.disable_instrument_routes:
        next_params["instrument_routes"] = []
        next_params["active_instrument_routes"] = []

    muted_bands = {value.strip().lower() for value in state.muted_bands}
    muted_instruments = {value.strip().lower() for value in state.muted_instruments}
    if muted_bands or muted_instruments or state.disable_eq_routes or state.disable_instrument_routes:
        for key in ("eq_routes", "active_eq_routes", "instrument_routes", "active_instrument_routes", "scene_layers", "eq_layers"):
            next_params[key] = _filter_route_entries(
                next_params.get(key),
                muted_bands=muted_bands,
                muted_instruments=muted_instruments,
                disable_eq=state.disable_eq_routes,
                disable_instrument=state.disable_instrument_routes,
            )

    next_params["runtime_control"] = runtime_control_to_dict(state)
    return next_params


def _filter_route_entries(
    raw: object,
    *,
    muted_bands: set[str],
    muted_instruments: set[str],
    disable_eq: bool,
    disable_instrument: bool,
) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    results: list[dict[str, Any]] = []
    for value in raw:
        if not isinstance(value, dict):
            continue
        band = str(value.get("band", "")).strip().lower()
        instrument = str(value.get("instrument", "")).strip().lower()
        if disable_eq and band:
            continue
        if disable_instrument and instrument:
            continue
        if band and band in muted_bands:
            continue
        if instrument and instrument in muted_instruments:
            continue
        results.append(dict(value))
    return results
