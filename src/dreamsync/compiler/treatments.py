"""Treatment Selector — map section mood to concrete lighting treatment."""

from __future__ import annotations

import random
from dataclasses import dataclass

from dreamsync.mood import Mood
from dreamsync.effects import EFFECTS, PALETTES, MOOD_EFFECTS, MOOD_PALETTES
from dreamsync.profile import EqRouteRule, InstrumentRouteRule, ProfileConfig


_DIRECTIONAL_SPATIAL_PRESETS: tuple[str, ...] = (
    "ripple_left_to_right",
    "ripple_right_to_left",
    "wave_top_to_bottom",
    "wave_bottom_to_top",
    "wave_front_to_back",
    "wave_back_to_front",
)

_PULSE_SPATIAL_PRESETS: tuple[str, ...] = (
    "ripple_from_center",
    "flash_top_only",
    "flash_floor_only",
)

_GRADIENT_SPATIAL_PRESETS: tuple[str, ...] = (
    "blend_left_to_right",
    "blend_front_to_back",
)

_SPATIAL_AREA_EXTENTS: dict[str, dict[str, dict[str, float]]] = {
    "top": {
        "min": {"x": -1.0, "y": 0.35, "z": -1.0},
        "max": {"x": 1.0, "y": 1.0, "z": 1.0},
    },
    "center": {
        "min": {"x": -1.0, "y": -0.35, "z": -1.0},
        "max": {"x": 1.0, "y": 0.35, "z": 1.0},
    },
    "bottom": {
        "min": {"x": -1.0, "y": -1.0, "z": -1.0},
        "max": {"x": 1.0, "y": -0.35, "z": 1.0},
    },
}


@dataclass(frozen=True)
class Treatment:
    render_mode: str              # "solid" | "pulse" | "breathe" | "scroll" | "wave" | "gradient"
    color_palette: tuple[str, ...]  # hex colors from the selected palette
    params: dict                  # renderer-specific params (pulse_decay, breathe_rate_mult, etc.)
    speed: float                  # effect speed multiplier
    effect_name: str              # for debugging/logging: which effect preset was chosen


class TreatmentSelector:
    def __init__(
        self,
        profile: ProfileConfig | None = None,
        seed: int | None = None,
    ) -> None:
        self._profile = profile
        self._rng = random.Random(seed)
        self._last_effect: dict[str, str] = {}
        self._last_palette: dict[str, str] = {}
        self._prev_mood: str | None = None
        self._song_primary_palette: str | None = None
        self._song_accent_palette: str | None = None

    def select_song_palettes(self, dominant_mood: str, all_moods: list[str]) -> None:
        """Pick 1-2 palettes for the entire song. Call once before per-section select()."""
        mood_enum = Mood(dominant_mood)
        pool = list(self._resolve_palette_pool(mood_enum))

        self._song_primary_palette = self._rng.choice(pool)

        unique_moods = set(all_moods) - {dominant_mood}
        if unique_moods:
            accent_mood = self._rng.choice(list(unique_moods))
            accent_pool = list(self._resolve_palette_pool(Mood(accent_mood)))
            candidates = [p for p in accent_pool if p != self._song_primary_palette]
            self._song_accent_palette = self._rng.choice(candidates) if candidates else accent_pool[0]
        else:
            candidates = [p for p in pool if p != self._song_primary_palette]
            self._song_accent_palette = self._rng.choice(candidates) if candidates else self._song_primary_palette

    def select(
        self,
        mood: str,
        section_label: str,
        section_bpm: float,
    ) -> Treatment:
        """Pick a treatment for one section. Avoids repeating the previous selection."""
        mood_enum = Mood(mood)

        # 1. Effect selection
        if mood == "drop":
            effect_name = "drop_blast"
        else:
            pool = self._resolve_effect_pool(mood_enum)
            names = [n for n, _ in pool]
            weights = [w for _, w in pool]
            effect_name = self._rng.choices(names, weights=weights, k=1)[0]
            # Re-roll once if repeat and pool > 1
            if effect_name == self._last_effect.get(mood) and len(pool) > 1:
                effect_name = self._rng.choices(names, weights=weights, k=1)[0]

        # 2. Palette selection — use song palette if set
        if self._song_primary_palette is not None:
            if section_label in ("bridge", "breakdown", "outro"):
                palette_name = self._song_accent_palette or self._song_primary_palette
            else:
                palette_name = self._song_primary_palette
        else:
            palette_pool = self._resolve_palette_pool(mood_enum)
            palette_name = self._rng.choice(palette_pool)
            # Re-roll once if repeat and pool > 1
            if palette_name == self._last_palette.get(mood) and len(palette_pool) > 1:
                palette_name = self._rng.choice(palette_pool)

        # 3. Transition rule check
        if self._prev_mood is not None and self._prev_mood != mood and self._profile is not None:
            for tr in self._profile.transitions:
                if tr.from_mood == self._prev_mood and tr.to_mood == mood:
                    palette_name = tr.palette
                    break

        # 4. Resolve palette name to hex colors
        color_palette = self._resolve_palette_colors(palette_name)

        # 5. Render mode
        render_mode = EFFECTS[effect_name].render_mode.value

        # 6. Params merge
        params = dict(EFFECTS[effect_name].params)
        if self._profile is not None:
            mood_cfg = self._profile.moods.get(mood)
            if mood_cfg and mood_cfg.params:
                params.update(mood_cfg.params)
            eq_routes = self._resolve_eq_routes(mood)
            if eq_routes:
                params["eq_routes"] = eq_routes
            instrument_routes = self._resolve_instrument_routes(mood)
            if instrument_routes:
                params["instrument_routes"] = instrument_routes
        self._apply_generated_palette_layer(
            effect_name=effect_name,
            render_mode=render_mode,
            params=params,
            color_palette=color_palette,
        )
        self._apply_generated_spatial_layer(
            effect_name=effect_name,
            render_mode=render_mode,
            params=params,
        )

        # 7. Speed from BPM
        speed = self._compute_speed(mood, section_bpm)

        # 8. Update history
        self._last_effect[mood] = effect_name
        self._last_palette[mood] = palette_name
        self._prev_mood = mood

        return Treatment(
            render_mode=render_mode,
            color_palette=color_palette,
            params=params,
            speed=speed,
            effect_name=effect_name,
        )

    def reset(self) -> None:
        """Clear history (call between songs)."""
        self._last_effect.clear()
        self._last_palette.clear()
        self._prev_mood = None
        self._song_primary_palette = None
        self._song_accent_palette = None

    def _resolve_effect_pool(self, mood_enum: Mood) -> list[tuple[str, float]]:
        """Get effect pool: profile first, then built-in."""
        if self._profile is not None:
            mood_cfg = self._profile.moods.get(mood_enum.value)
            if mood_cfg and mood_cfg.effects:
                return [(e.name, e.weight) for e in mood_cfg.effects]
        return MOOD_EFFECTS[mood_enum]

    def _resolve_palette_pool(self, mood_enum: Mood) -> tuple[str, ...]:
        """Get palette pool: profile first, then built-in."""
        if self._profile is not None:
            mood_cfg = self._profile.moods.get(mood_enum.value)
            if mood_cfg and mood_cfg.palettes:
                return mood_cfg.palettes
        return MOOD_PALETTES[mood_enum]

    def _resolve_palette_colors(self, palette_name: str) -> tuple[str, ...]:
        """Resolve palette name to hex colors: profile first, then built-in."""
        if self._profile is not None and palette_name in self._profile.palettes:
            return self._profile.palettes[palette_name]
        return PALETTES[palette_name]

    def _resolve_eq_routes(self, mood: str) -> list[dict]:
        if self._profile is None:
            return []
        routes = list(self._profile.eq_routes)
        mood_cfg = self._profile.moods.get(mood)
        if mood_cfg and mood_cfg.eq_routes:
            routes = self._merge_eq_route_rules(routes, list(mood_cfg.eq_routes))
        return [self._eq_route_to_dict(route) for route in routes]

    def _resolve_instrument_routes(self, mood: str) -> list[dict]:
        if self._profile is None:
            return []
        routes = list(self._profile.instrument_routes)
        mood_cfg = self._profile.moods.get(mood)
        if mood_cfg and mood_cfg.instrument_routes:
            routes = self._merge_instrument_route_rules(routes, list(mood_cfg.instrument_routes))
        return [self._instrument_route_to_dict(route) for route in routes]

    @staticmethod
    def _merge_eq_route_rules(
        base_routes: list[EqRouteRule],
        override_routes: list[EqRouteRule],
    ) -> list[EqRouteRule]:
        merged: dict[tuple[str, str], EqRouteRule] = {
            (route.band, route.when): route for route in base_routes
        }
        order = [(route.band, route.when) for route in base_routes]
        for route in override_routes:
            key = (route.band, route.when)
            if key not in merged:
                order.append(key)
            merged[key] = route
        return [merged[key] for key in order]

    @staticmethod
    def _eq_route_to_dict(route: EqRouteRule) -> dict:
        data = {
            "band": route.band,
            "when": route.when,
        }
        if route.color_bias is not None:
            data["color_bias"] = route.color_bias
        if route.render_mode is not None:
            data["render_mode"] = route.render_mode
        if route.spatial_preset is not None:
            data["spatial_preset"] = route.spatial_preset
        if route.intensity_boost:
            data["intensity_boost"] = route.intensity_boost
        return data

    @staticmethod
    def _merge_instrument_route_rules(
        base_routes: list[InstrumentRouteRule],
        override_routes: list[InstrumentRouteRule],
    ) -> list[InstrumentRouteRule]:
        merged: dict[tuple[str, str], InstrumentRouteRule] = {
            (route.instrument, route.when): route for route in base_routes
        }
        order = [(route.instrument, route.when) for route in base_routes]
        for route in override_routes:
            key = (route.instrument, route.when)
            if key not in merged:
                order.append(key)
            merged[key] = route
        return [merged[key] for key in order]

    @staticmethod
    def _instrument_route_to_dict(route: InstrumentRouteRule) -> dict:
        data = {
            "instrument": route.instrument,
            "when": route.when,
            "pan_follow": route.pan_follow,
            "width_scale": route.width_scale,
            "confidence_min": route.confidence_min,
        }
        if route.color_bias is not None:
            data["color_bias"] = route.color_bias
        if route.render_mode is not None:
            data["render_mode"] = route.render_mode
        if route.spatial_preset is not None:
            data["spatial_preset"] = route.spatial_preset
        if route.spatial_zone is not None:
            data["spatial_zone"] = route.spatial_zone
        if route.intensity_boost:
            data["intensity_boost"] = route.intensity_boost
        return data

    def _apply_generated_palette_layer(
        self,
        *,
        effect_name: str,
        render_mode: str,
        params: dict,
        color_palette: tuple[str, ...],
    ) -> None:
        """Align generated color-bearing params with the chosen section palette."""
        if render_mode != "gradient":
            return

        default_gradient = EFFECTS[effect_name].params.get("gradient_colors")
        current_gradient = params.get("gradient_colors")
        if current_gradient == default_gradient:
            params["gradient_colors"] = color_palette

    def _apply_generated_spatial_layer(
        self,
        *,
        effect_name: str,
        render_mode: str,
        params: dict,
    ) -> None:
        """Attach spatial direction/area metadata for auto-generated show cues."""
        if self._has_explicit_spatial_metadata(params):
            return

        preset = self._pick_spatial_preset(effect_name=effect_name, render_mode=render_mode)
        if preset is not None:
            params["spatial_preset"] = preset

        if render_mode in {"solid", "breathe"}:
            params.setdefault("spatial_mode", "wash")

        area_name = self._rng.choice(("all", "top", "center", "bottom"))
        if area_name != "all" and self._preset_supports_area(preset):
            params["spatial_extent"] = self._copy_spatial_extent(_SPATIAL_AREA_EXTENTS[area_name])

    @staticmethod
    def _has_explicit_spatial_metadata(params: dict) -> bool:
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

    def _pick_spatial_preset(self, *, effect_name: str, render_mode: str) -> str | None:
        if effect_name == "drop_blast":
            return self._rng.choice(_PULSE_SPATIAL_PRESETS)
        if render_mode == "pulse":
            return self._rng.choice(_PULSE_SPATIAL_PRESETS)
        if render_mode in {"scroll", "wave"}:
            return self._rng.choice(_DIRECTIONAL_SPATIAL_PRESETS)
        if render_mode == "gradient":
            return self._rng.choice(_GRADIENT_SPATIAL_PRESETS)
        return None

    @staticmethod
    def _preset_supports_area(preset: str | None) -> bool:
        return preset not in {"flash_top_only", "flash_floor_only"}

    @staticmethod
    def _copy_spatial_extent(
        extent: dict[str, dict[str, float]],
    ) -> dict[str, dict[str, float]]:
        return {
            bound: {axis: float(value) for axis, value in coords.items()}
            for bound, coords in extent.items()
        }

    @staticmethod
    def _compute_speed(mood: str, bpm: float) -> float:
        """Compute speed multiplier from mood and BPM."""
        if mood == "drop":
            return 1.0
        if bpm < 90:
            return 0.3
        if bpm < 120:
            return 0.5
        if bpm < 140:
            return 0.7
        return 0.9
