"""Non-destructive show-level control patches."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from .models import ShowCue, ShowTimeline


@dataclass(frozen=True)
class CueMatch:
    cue_index: int | None = None
    start_t: float | None = None
    end_t: float | None = None
    render_mode: str | None = None
    transition: str | None = None
    has_eq_band: str | None = None
    has_instrument: str | None = None


@dataclass(frozen=True)
class CueOverrideRule:
    match: CueMatch = field(default_factory=CueMatch)
    palette: tuple[str, ...] | None = None
    color_bias: str | None = None
    render_mode: str | None = None
    intensity_mult: float = 1.0
    intensity_offset: float = 0.0
    speed_mult: float = 1.0
    speed_offset: float = 0.0
    transition: str | None = None
    transition_beats: int | None = None
    params_update: dict[str, Any] = field(default_factory=dict)
    disable_eq_routes: bool = False
    disable_instrument_routes: bool = False
    disable_bands: tuple[str, ...] = ()
    disable_instruments: tuple[str, ...] = ()


@dataclass(frozen=True)
class ShowControlPatch:
    name: str = ""
    rules: tuple[CueOverrideRule, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_data(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "rules": [rule_to_data(rule) for rule in self.rules],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_data(cls, raw: dict[str, Any]) -> "ShowControlPatch":
        return cls(
            name=str(raw.get("name", "")),
            rules=tuple(
                rule_from_data(rule)
                for rule in raw.get("rules", ())
                if isinstance(rule, dict)
            ),
            metadata=dict(raw.get("metadata", {}))
            if isinstance(raw.get("metadata", {}), dict)
            else {},
        )


def apply_show_control_patch(
    timeline: ShowTimeline,
    patch: ShowControlPatch | None,
) -> ShowTimeline:
    """Apply a show-level control patch without mutating the original timeline."""

    if patch is None or not patch.rules:
        return timeline

    patched_cues: list[ShowCue] = []
    for cue_index, cue in enumerate(timeline.cues):
        updated = cue
        for rule in patch.rules:
            if _rule_matches(rule.match, cue, cue_index):
                updated = _apply_rule(updated, rule)
        patched_cues.append(updated)

    metadata = dict(timeline.metadata)
    metadata["control_patch_name"] = patch.name
    metadata["control_patch_rules"] = len(patch.rules)
    if patch.metadata:
        metadata["control_patch_metadata"] = dict(patch.metadata)

    return ShowTimeline(
        song_path=timeline.song_path,
        duration=timeline.duration,
        bpm=timeline.bpm,
        time_signature=timeline.time_signature,
        beat_times=timeline.beat_times,
        downbeat_times=timeline.downbeat_times,
        cues=tuple(patched_cues),
        metadata=metadata,
    )


def _rule_matches(match: CueMatch, cue: ShowCue, cue_index: int) -> bool:
    if match.cue_index is not None and cue_index != match.cue_index:
        return False
    if match.start_t is not None and cue.t < match.start_t:
        return False
    if match.end_t is not None and cue.t >= match.end_t:
        return False
    if match.render_mode is not None and cue.render_mode != match.render_mode:
        return False
    if match.transition is not None and cue.transition != match.transition:
        return False
    if match.has_eq_band is not None and not _cue_has_eq_band(cue, match.has_eq_band):
        return False
    if match.has_instrument is not None and not _cue_has_instrument(cue, match.has_instrument):
        return False
    return True


def _cue_has_eq_band(cue: ShowCue, band: str) -> bool:
    band = band.strip().lower()
    for key in ("active_eq_routes", "eq_routes", "scene_layers", "eq_layers"):
        for route in _iter_mapping_list(cue.params.get(key)):
            if str(route.get("band", "")).strip().lower() == band:
                return True
    return False


def _cue_has_instrument(cue: ShowCue, instrument: str) -> bool:
    instrument = instrument.strip().lower()
    for key in ("active_instrument_routes", "instrument_routes", "scene_layers", "eq_layers"):
        for route in _iter_mapping_list(cue.params.get(key)):
            if str(route.get("instrument", "")).strip().lower() == instrument:
                return True
    return False


def _apply_rule(cue: ShowCue, rule: CueOverrideRule) -> ShowCue:
    params = dict(cue.params)
    params.update(rule.params_update)

    if rule.disable_eq_routes:
        params["eq_routes"] = []
        params["active_eq_routes"] = []
    if rule.disable_instrument_routes:
        params["instrument_routes"] = []
        params["active_instrument_routes"] = []

    if rule.disable_bands:
        disabled_bands = {value.strip().lower() for value in rule.disable_bands}
        for key in ("eq_routes", "active_eq_routes", "scene_layers", "eq_layers"):
            params[key] = [
                route for route in _iter_mapping_list(params.get(key))
                if str(route.get("band", "")).strip().lower() not in disabled_bands
            ]

    if rule.disable_instruments:
        disabled_instruments = {value.strip().lower() for value in rule.disable_instruments}
        for key in ("instrument_routes", "active_instrument_routes", "scene_layers", "eq_layers"):
            params[key] = [
                route for route in _iter_mapping_list(params.get(key))
                if str(route.get("instrument", "")).strip().lower() not in disabled_instruments
            ]

    color_palette = cue.color_palette
    if rule.palette is not None:
        color_palette = tuple(rule.palette)
    if rule.color_bias is not None:
        remaining = tuple(color for color in color_palette if color != rule.color_bias)
        color_palette = (rule.color_bias,) + remaining

    intensity = max(0.0, min(1.0, (cue.intensity * rule.intensity_mult) + rule.intensity_offset))
    speed = max(0.0, (cue.speed * rule.speed_mult) + rule.speed_offset)

    return replace(
        cue,
        render_mode=rule.render_mode or cue.render_mode,
        color_palette=color_palette,
        intensity=round(float(intensity), 4),
        speed=round(float(speed), 4),
        params=params,
        transition=rule.transition or cue.transition,
        transition_beats=(
            cue.transition_beats if rule.transition_beats is None else int(rule.transition_beats)
        ),
    )


def _iter_mapping_list(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    values: list[dict[str, Any]] = []
    for value in raw:
        if isinstance(value, dict):
            values.append(dict(value))
    return values


def match_to_data(match: CueMatch) -> dict[str, Any]:
    return {
        "cue_index": match.cue_index,
        "start_t": match.start_t,
        "end_t": match.end_t,
        "render_mode": match.render_mode,
        "transition": match.transition,
        "has_eq_band": match.has_eq_band,
        "has_instrument": match.has_instrument,
    }


def match_from_data(raw: dict[str, Any]) -> CueMatch:
    return CueMatch(
        cue_index=int(raw["cue_index"]) if raw.get("cue_index") is not None else None,
        start_t=float(raw["start_t"]) if raw.get("start_t") is not None else None,
        end_t=float(raw["end_t"]) if raw.get("end_t") is not None else None,
        render_mode=str(raw["render_mode"]) if raw.get("render_mode") else None,
        transition=str(raw["transition"]) if raw.get("transition") else None,
        has_eq_band=str(raw["has_eq_band"]) if raw.get("has_eq_band") else None,
        has_instrument=str(raw["has_instrument"]) if raw.get("has_instrument") else None,
    )


def rule_to_data(rule: CueOverrideRule) -> dict[str, Any]:
    return {
        "match": match_to_data(rule.match),
        "palette": list(rule.palette) if rule.palette is not None else None,
        "color_bias": rule.color_bias,
        "render_mode": rule.render_mode,
        "intensity_mult": rule.intensity_mult,
        "intensity_offset": rule.intensity_offset,
        "speed_mult": rule.speed_mult,
        "speed_offset": rule.speed_offset,
        "transition": rule.transition,
        "transition_beats": rule.transition_beats,
        "params_update": dict(rule.params_update),
        "disable_eq_routes": rule.disable_eq_routes,
        "disable_instrument_routes": rule.disable_instrument_routes,
        "disable_bands": list(rule.disable_bands),
        "disable_instruments": list(rule.disable_instruments),
    }


def rule_from_data(raw: dict[str, Any]) -> CueOverrideRule:
    match_raw = raw.get("match", {})
    return CueOverrideRule(
        match=match_from_data(match_raw) if isinstance(match_raw, dict) else CueMatch(),
        palette=tuple(str(value) for value in raw.get("palette", ()))
        if raw.get("palette") is not None
        else None,
        color_bias=str(raw["color_bias"]) if raw.get("color_bias") else None,
        render_mode=str(raw["render_mode"]) if raw.get("render_mode") else None,
        intensity_mult=float(raw.get("intensity_mult", 1.0) or 1.0),
        intensity_offset=float(raw.get("intensity_offset", 0.0) or 0.0),
        speed_mult=float(raw.get("speed_mult", 1.0) or 1.0),
        speed_offset=float(raw.get("speed_offset", 0.0) or 0.0),
        transition=str(raw["transition"]) if raw.get("transition") else None,
        transition_beats=int(raw["transition_beats"])
        if raw.get("transition_beats") is not None
        else None,
        params_update=dict(raw.get("params_update", {}))
        if isinstance(raw.get("params_update", {}), dict)
        else {},
        disable_eq_routes=bool(raw.get("disable_eq_routes", False)),
        disable_instrument_routes=bool(raw.get("disable_instrument_routes", False)),
        disable_bands=tuple(str(value) for value in raw.get("disable_bands", ())),
        disable_instruments=tuple(str(value) for value in raw.get("disable_instruments", ())),
    )
