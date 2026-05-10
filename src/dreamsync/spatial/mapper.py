from __future__ import annotations

from dataclasses import replace
from typing import Literal

from dreamsync.director import EffectMode, LightingIntent
from dreamsync.show.models import ShowCue

from .models import GridCell, SpatialCellState
from .patterns import cell_coordinates, shift_hex_color

SpatialAxis = Literal["horizontal", "depth", "diagonal", "radial"]
SpatialFocus = Literal["left", "center", "right", "front", "back", "balanced"]

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


class SpatialMapper:
    """Expand one global musical state into a 3x3 spatial scene."""

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
        params = params or {}
        if not self.enabled:
            return self._balanced_scene(intent, params=params)

        axis = self._resolve_axis(
            params.get("spatial_axis"),
            fallback="horizontal" if intent.mode == EffectMode.MOTION else "radial",
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
        params = cue.params or {}
        if not self.enabled:
            return self._balanced_scene(base_intent, params=params)

        axis = self._resolve_axis(
            params.get("spatial_axis"),
            fallback=self._default_axis_for_render_mode(cue.render_mode),
        )
        focus = self._resolve_focus(params.get("spatial_focus"))
        return self._scene_for(base_intent, axis=axis, focus=focus, params=params)

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
            x, y = cell_coordinates(cell)
            emphasis = self._compute_emphasis(
                intent=intent,
                axis=axis,
                focus=focus,
                x=x,
                y=y,
                center_gain=center_gain,
            )
            phase_offset = self._compute_phase_offset(axis=axis, x=x, y=y)
            color_shift = self._compute_color_shift(axis=axis, x=x, y=y, spread=color_spread)
            cell_params = dict(params)
            if y > 0:
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
        y: int,
        center_gain: float,
    ) -> float:
        emphasis = 1.0

        if intent.mode == EffectMode.PULSE:
            if y < 0:
                emphasis += self.front_back_separation * 0.35
            elif y > 0:
                emphasis -= self.front_back_separation * 0.20
        elif intent.mode == EffectMode.AMBIENT:
            emphasis += center_gain * 0.35 if (x == 0 or y == 0) else 0.0

        if axis == "horizontal":
            if x == 0:
                emphasis += center_gain
        elif axis == "depth":
            if y == 0:
                emphasis += center_gain
        elif axis == "radial":
            if x == 0 and y == 0:
                emphasis += center_gain
            elif x == 0 or y == 0:
                emphasis += center_gain * 0.5
        elif axis == "diagonal":
            if x == 0 or y == 0:
                emphasis += center_gain * 0.4

        if focus == "left" and x < 0:
            emphasis += self.left_right_separation * 0.35
        elif focus == "right" and x > 0:
            emphasis += self.left_right_separation * 0.35
        elif focus == "front" and y < 0:
            emphasis += self.front_back_separation * 0.35
        elif focus == "back" and y > 0:
            emphasis += self.front_back_separation * 0.35
        elif focus == "center":
            if x == 0 and y == 0:
                emphasis += center_gain
            elif x == 0 or y == 0:
                emphasis += center_gain * 0.5

        return max(0.05, emphasis)

    def _compute_phase_offset(self, *, axis: SpatialAxis, x: int, y: int) -> float:
        if axis == "horizontal":
            return x * self.left_right_separation
        if axis == "depth":
            return y * self.front_back_separation
        if axis == "diagonal":
            return (x + y) * 0.5 * max(self.left_right_separation, self.front_back_separation)
        if axis == "radial":
            return 0.0 if (x == 0 and y == 0) else 0.5 * (abs(x) + abs(y))
        return 0.0

    def _compute_color_shift(self, *, axis: SpatialAxis, x: int, y: int, spread: float) -> float:
        if spread == 0.0:
            return 0.0
        if axis == "horizontal":
            return x * spread
        if axis == "depth":
            return y * spread
        if axis == "diagonal":
            return (x - y) * 0.5 * spread
        if axis == "radial":
            return 0.0 if (x == 0 and y == 0) else spread * 0.5 * (x + y)
        return 0.0

    def _resolve_axis(self, raw: object, *, fallback: SpatialAxis) -> SpatialAxis:
        if raw in {"horizontal", "depth", "diagonal", "radial"}:
            return raw  # type: ignore[return-value]
        return fallback

    def _resolve_focus(self, raw: object) -> SpatialFocus:
        if raw in {"left", "center", "right", "front", "back", "balanced"}:
            return raw  # type: ignore[return-value]
        return "balanced"

    def _default_axis_for_render_mode(self, render_mode: str) -> SpatialAxis:
        if render_mode in {"scroll", "wave"}:
            return "horizontal"
        if render_mode == "gradient":
            return "depth"
        return "radial"
