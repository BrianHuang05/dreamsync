"""Timeline Assembler — combine arc weights, treatments, and transitions into a ShowTimeline."""

from __future__ import annotations

import logging

from dreamsync.analyzer.features import FeatureRow
from dreamsync.analyzer.instruments import INSTRUMENT_PROXY_NAMES, InstrumentProxy
from dreamsync.analyzer.models import SongStructure
from dreamsync.analyzer.phrases import Phrase
from dreamsync.compiler.arc import ArcWeight
from dreamsync.compiler.treatments import Treatment
from dreamsync.compiler.transitions import TransitionPlan
from dreamsync.color_utils import nearest_palette_color
from dreamsync.show.models import ShowCue, ShowTimeline

logger = logging.getLogger(__name__)

_SPATIAL_LAYER_ROUTE_KEYS: tuple[str, ...] = (
    "effect_layer",
    "layer_category",
    "trigger_mode",
    "falloff",
    "radius",
    "speed_units_per_second",
    "intensity_scale",
    "time_offset_s",
    "duration_s",
    "layer_priority",
    "target_groups",
    "exclude_groups",
    "target_match",
    "untargeted_behavior",
)

_EQ_ROUTE_DEFAULTS: dict[tuple[str, str], dict[str, object]] = {
    ("kick", "enter"): {
        "band": "kick",
        "when": "enter",
        "color_bias": "#ff5033",
        "render_mode": "pulse",
        "spatial_preset": "ripple_from_center",
        "intensity_boost": 0.15,
    },
    ("sub", "dominant"): {
        "band": "sub",
        "when": "dominant",
        "color_bias": "#ff6b3d",
        "render_mode": "pulse",
        "spatial_preset": "flash_floor_only",
        "intensity_boost": 0.08,
    },
    ("sub", "enter"): {
        "band": "sub",
        "when": "enter",
        "color_bias": "#ff6b3d",
        "render_mode": "pulse",
        "spatial_preset": "flash_floor_only",
        "intensity_boost": 0.14,
    },
    ("bass", "dominant"): {
        "band": "bass",
        "when": "dominant",
        "color_bias": "#ff8a3d",
        "render_mode": "pulse",
        "spatial_preset": "flash_floor_only",
        "intensity_boost": 0.06,
    },
    ("bass", "enter"): {
        "band": "bass",
        "when": "enter",
        "color_bias": "#ff8a3d",
        "render_mode": "pulse",
        "spatial_preset": "flash_floor_only",
        "intensity_boost": 0.15,
    },
    ("bass", "drop"): {
        "band": "bass",
        "when": "drop",
        "color_bias": "#ffb24d",
        "render_mode": "pulse",
        "spatial_preset": "ripple_from_center",
        "intensity_boost": 0.18,
    },
    ("presence", "dominant"): {
        "band": "presence",
        "when": "dominant",
        "color_bias": "#66ccff",
        "render_mode": "gradient",
        "spatial_preset": "flash_top_only",
        "intensity_boost": 0.05,
    },
    ("presence", "lift"): {
        "band": "presence",
        "when": "lift",
        "color_bias": "#66ccff",
        "render_mode": "gradient",
        "spatial_preset": "flash_top_only",
        "intensity_boost": 0.10,
    },
    ("air", "dominant"): {
        "band": "air",
        "when": "dominant",
        "color_bias": "#dff6ff",
        "render_mode": "gradient",
        "spatial_preset": "blend_left_to_right",
        "intensity_boost": 0.04,
    },
    ("air", "swell"): {
        "band": "air",
        "when": "swell",
        "color_bias": "#dff6ff",
        "render_mode": "gradient",
        "spatial_preset": "blend_front_to_back",
        "intensity_boost": 0.08,
    },
}

_INSTRUMENT_ROUTE_DEFAULTS: dict[tuple[str, str], dict[str, object]] = {
    ("drums", "dominant"): {
        "instrument": "drums",
        "when": "dominant",
        "color_bias": "#ff5a36",
        "render_mode": "pulse",
        "spatial_preset": "ripple_from_center",
        "confidence_min": 0.45,
        "intensity_boost": 0.12,
    },
    ("drums", "enter"): {
        "instrument": "drums",
        "when": "enter",
        "color_bias": "#ff5a36",
        "render_mode": "pulse",
        "spatial_preset": "ripple_from_center",
        "confidence_min": 0.45,
        "intensity_boost": 0.18,
    },
    ("bass", "dominant"): {
        "instrument": "bass",
        "when": "dominant",
        "color_bias": "#ff8a3d",
        "render_mode": "pulse",
        "spatial_preset": "flash_floor_only",
        "pan_follow": 0.18,
        "width_scale": 1.05,
        "confidence_min": 0.42,
        "intensity_boost": 0.10,
    },
    ("bass", "enter"): {
        "instrument": "bass",
        "when": "enter",
        "color_bias": "#ff8a3d",
        "render_mode": "pulse",
        "spatial_preset": "flash_floor_only",
        "pan_follow": 0.14,
        "width_scale": 1.0,
        "confidence_min": 0.42,
        "intensity_boost": 0.16,
    },
    ("vocals", "dominant"): {
        "instrument": "vocals",
        "when": "dominant",
        "color_bias": "#cceeff",
        "render_mode": "gradient",
        "spatial_preset": "blend_left_to_right",
        "pan_follow": 0.72,
        "width_scale": 1.35,
        "confidence_min": 0.48,
        "intensity_boost": 0.08,
    },
    ("vocals", "present"): {
        "instrument": "vocals",
        "when": "present",
        "color_bias": "#cceeff",
        "render_mode": "gradient",
        "spatial_preset": "blend_front_to_back",
        "pan_follow": 0.52,
        "width_scale": 1.22,
        "confidence_min": 0.48,
        "intensity_boost": 0.04,
    },
    ("harmonic", "present"): {
        "instrument": "harmonic",
        "when": "present",
        "color_bias": "#b38cff",
        "render_mode": "wave",
        "spatial_preset": "blend_front_to_back",
        "pan_follow": 0.38,
        "width_scale": 1.28,
        "confidence_min": 0.45,
        "intensity_boost": 0.04,
    },
    ("percussive", "present"): {
        "instrument": "percussive",
        "when": "present",
        "color_bias": "#ffd07a",
        "render_mode": "pulse",
        "spatial_preset": "ripple_from_center",
        "pan_follow": 0.10,
        "width_scale": 1.08,
        "confidence_min": 0.48,
        "intensity_boost": 0.06,
    },
}


class TimelineAssembler:
    def assemble(
        self,
        structure: SongStructure,
        arc_weights: list[ArcWeight],
        treatments: list[Treatment],
        transition_plans: list[TransitionPlan],
        features: list[FeatureRow] | None = None,
    ) -> ShowTimeline:
        """Build a ShowTimeline from the compiler's intermediate outputs."""
        sections = structure.sections
        n = len(sections)
        timeline_bpm = self._effective_timeline_bpm(structure)
        if len(arc_weights) != n or len(treatments) != n or len(transition_plans) != n:
            raise ValueError(
                f"Input length mismatch: sections={n}, "
                f"arc_weights={len(arc_weights)}, "
                f"treatments={len(treatments)}, "
                f"transition_plans={len(transition_plans)}"
            )

        cues: list[ShowCue] = []
        proxy_lookup, prev_proxy_lookup = self._build_proxy_lookups(structure.instrument_proxies)
        for i in range(n):
            # D2: outro gets intensity_start ramp
            intensity_start = None
            if sections[i].label == "outro" and i > 0:
                intensity_start = arc_weights[i - 1].final_intensity

            cues.append(ShowCue(
                t=sections[i].start_t,
                render_mode=treatments[i].render_mode,
                color_palette=treatments[i].color_palette,
                intensity=arc_weights[i].final_intensity,
                speed=treatments[i].speed,
                params=treatments[i].params,
                transition=transition_plans[i].transition,
                transition_beats=transition_plans[i].transition_beats,
                intensity_start=intensity_start,
            ))

            # Insert micro-cues for phrases within this section
            if structure.phrases:
                section_phrases = [
                    p for p in structure.phrases if p.parent_section_index == i
                ]
                section_events = [
                    e for e in structure.instrument_events
                    if sections[i].start_t <= e.t < sections[i].end_t
                ]

                for phrase in section_phrases[1:]:  # skip first (covered by section cue)
                    micro_cue = self._build_micro_cue(
                        phrase,
                        treatments[i],
                        arc_weights[i],
                        section_events,
                        proxy_lookup.get(round(phrase.start_t, 4)),
                        prev_proxy_lookup.get(round(phrase.start_t, 4)),
                    )
                    if micro_cue is not None:
                        cues.append(micro_cue)

        # D1: Insert fade-to-black cue after last musical beat
        if features:
            last_beat = self._find_last_musical_beat(
                structure.beat_grid.beat_times, features,
            )
            if last_beat is not None:
                fade_duration = min(4.0, structure.duration - last_beat)
                if fade_duration > 0.5 and cues:
                    last_cue = cues[-1]
                    fade_beats = min(8, int(fade_duration * timeline_bpm / 60))
                    cues.append(ShowCue(
                        t=last_beat,
                        render_mode="solid",
                        color_palette=last_cue.color_palette,
                        intensity=0.0,
                        speed=0.0,
                        params={},
                        transition="fade",
                        transition_beats=max(1, fade_beats),
                    ))

        # Sort by time
        cues.sort(key=lambda c: c.t)

        return ShowTimeline(
            song_path=structure.path,
            duration=structure.duration,
            bpm=timeline_bpm,
            time_signature=structure.time_signature,
            beat_times=structure.beat_grid.beat_times,
            downbeat_times=structure.beat_grid.downbeat_times,
            cues=tuple(cues),
            metadata=structure.metadata,
        )

    def _effective_timeline_bpm(self, structure: SongStructure) -> float:
        """Return a BPM suitable for runtime playback, even for weak analysis cases."""
        candidates = [structure.bpm, structure.beat_grid.bpm]
        candidates.extend(region.bpm for region in structure.tempo_regions)
        candidates.extend(section.bpm for section in structure.sections)
        for bpm in candidates:
            if bpm > 0:
                return float(bpm)

        # Very short or low-confidence captures can legitimately analyze to 0 BPM.
        # Use a neutral fallback so local preview can still compile and play.
        fallback_bpm = 120.0
        logger.warning(
            "compile: using fallback BPM %.1f for '%s' because analysis produced no positive BPM",
            fallback_bpm,
            structure.path,
        )
        return fallback_bpm

    def _find_last_musical_beat(
        self,
        beat_times: tuple[float, ...],
        features: list[FeatureRow],
        energy_threshold: float = 0.10,
    ) -> float | None:
        """Find the last beat where surrounding energy exceeds threshold."""
        if not beat_times or not features:
            return None

        for beat_t in reversed(beat_times):
            nearby_energy = [
                f.energy for f in features
                if abs(f.t - beat_t) <= 0.5
            ]
            if nearby_energy and sum(nearby_energy) / len(nearby_energy) > energy_threshold:
                return beat_t

        return None

    def _build_micro_cue(
        self,
        phrase: Phrase,
        treatment: Treatment,
        arc_weight: ArcWeight,
        section_events: list,
        instrument_proxy: InstrumentProxy | None = None,
        previous_instrument_proxy: InstrumentProxy | None = None,
    ) -> ShowCue | None:
        """Build a micro-cue for a phrase within a section."""
        base_intensity = arc_weight.final_intensity

        # Check for instrument events at this phrase boundary
        events_at_phrase = [e for e in section_events if abs(e.t - phrase.start_t) < 0.1]
        event_types = {e.event_type for e in events_at_phrase}

        render_mode = treatment.render_mode
        intensity = base_intensity
        speed = treatment.speed
        params = dict(treatment.params)
        color_palette = treatment.color_palette

        if "kick_enter" in event_types:
            intensity = min(1.0, base_intensity + 0.15)
            render_mode = "pulse"
        elif phrase.phrase_type == "build":
            intensity = min(1.0, base_intensity + 0.20)
        elif phrase.phrase_type == "breakdown":
            intensity = max(0.05, base_intensity - 0.20)
            render_mode = "breathe"
        elif phrase.phrase_type == "steady":
            # Steady phrase: minor variation to prevent staleness
            pass
        elif phrase.phrase_type == "drop":
            intensity = min(1.0, base_intensity + 0.10)

        configured_eq_routes = self._normalize_eq_routes(params.get("eq_routes"))
        active_eq_routes = self._resolve_active_eq_routes(phrase, events_at_phrase, params)
        active_eq_routes = self._align_route_color_biases(
            active_eq_routes,
            color_palette,
        )
        if active_eq_routes:
            active_keys = {
                (str(route.get("band", "")), str(route.get("when", "")))
                for route in active_eq_routes
            }
            passthrough_routes = [
                route for route in configured_eq_routes
                if (str(route.get("band", "")), str(route.get("when", ""))) not in active_keys
            ]
            params["eq_routes"] = active_eq_routes + passthrough_routes
            params["active_eq_routes"] = [dict(route) for route in active_eq_routes]

        configured_instrument_routes = self._normalize_instrument_routes(params.get("instrument_routes"))
        active_instrument_routes = self._resolve_active_instrument_routes(
            instrument_proxy,
            previous_instrument_proxy,
            params,
        )
        active_instrument_routes = self._align_route_color_biases(
            active_instrument_routes,
            color_palette,
        )
        if active_instrument_routes:
            active_keys = {
                (str(route.get("instrument", "")), str(route.get("when", "")))
                for route in active_instrument_routes
            }
            passthrough_routes = [
                route for route in configured_instrument_routes
                if (str(route.get("instrument", "")), str(route.get("when", ""))) not in active_keys
            ]
            params["instrument_routes"] = active_instrument_routes + passthrough_routes
            params["active_instrument_routes"] = [dict(route) for route in active_instrument_routes]

        if instrument_proxy is not None:
            params["instrument_proxy"] = self._instrument_proxy_to_dict(instrument_proxy)

        all_active_routes = active_instrument_routes + active_eq_routes
        if all_active_routes:
            scene_layers = self._build_route_layers(all_active_routes)
            params["scene_layers"] = scene_layers
            params["eq_layers"] = scene_layers
            intensity = self._apply_route_intensity(intensity, all_active_routes)
            route_render_mode = self._first_route_value(all_active_routes, "render_mode")
            route_spatial_preset = self._first_route_value(all_active_routes, "spatial_preset")
            route_color_bias = self._first_route_value(all_active_routes, "color_bias")
            if route_render_mode is not None:
                render_mode = str(route_render_mode)
            if route_spatial_preset is not None:
                params["spatial_preset"] = route_spatial_preset
            for key in _SPATIAL_LAYER_ROUTE_KEYS:
                value = self._first_route_value(all_active_routes, key)
                if value is not None and key not in params:
                    params[key] = value
            if route_color_bias is not None:
                color_palette = self._apply_color_bias(color_palette, str(route_color_bias))

        return ShowCue(
            t=phrase.start_t,
            render_mode=render_mode,
            color_palette=color_palette,
            intensity=round(intensity, 4),
            speed=speed,
            params=params,
            transition="fade",
            transition_beats=2,
        )

    def _resolve_active_eq_routes(
        self,
        phrase: Phrase,
        events_at_phrase: list,
        params: dict,
    ) -> list[dict]:
        configured_routes = self._normalize_eq_routes(params.get("eq_routes"))
        trigger_keys = self._eq_route_trigger_keys(phrase, events_at_phrase)
        if not trigger_keys:
            return []

        default_routes = [
            dict(_EQ_ROUTE_DEFAULTS[key])
            for key in trigger_keys
            if key in _EQ_ROUTE_DEFAULTS
        ]
        matching_configured = [
            route for route in configured_routes
            if (str(route.get("band", "")), str(route.get("when", ""))) in trigger_keys
        ]
        return self._merge_eq_routes(default_routes, matching_configured)

    def _resolve_active_instrument_routes(
        self,
        instrument_proxy: InstrumentProxy | None,
        previous_instrument_proxy: InstrumentProxy | None,
        params: dict,
    ) -> list[dict]:
        if instrument_proxy is None:
            return []
        configured_routes = self._normalize_instrument_routes(params.get("instrument_routes"))
        default_routes = [dict(route) for route in _INSTRUMENT_ROUTE_DEFAULTS.values()]
        merged_routes = self._merge_instrument_routes(default_routes, configured_routes)
        active_routes = [
            route for route in merged_routes
            if self._instrument_route_is_active(route, instrument_proxy, previous_instrument_proxy)
        ]
        dominant_instruments = {
            str(route.get("instrument", ""))
            for route in active_routes
            if str(route.get("when", "")) == "dominant"
        }
        if not dominant_instruments:
            return [
                self._enrich_instrument_route(route, instrument_proxy)
                for route in active_routes
            ]
        return [
            self._enrich_instrument_route(route, instrument_proxy)
            for route in active_routes
            if not (
                str(route.get("when", "")) == "present"
                and str(route.get("instrument", "")) in dominant_instruments
            )
        ]

    def _eq_route_trigger_keys(self, phrase: Phrase, events_at_phrase: list) -> list[tuple[str, str]]:
        keys: list[tuple[str, str]] = []
        for event in events_at_phrase:
            key = self._event_route_key(event)
            if key is not None and key not in keys:
                keys.append(key)

        dominant_key = self._dominant_band_route_key(phrase)
        if dominant_key is not None and dominant_key not in keys:
            keys.append(dominant_key)
        return keys

    @staticmethod
    def _event_route_key(event) -> tuple[str, str] | None:
        event_type = str(getattr(event, "event_type", "")).strip().lower()
        if not event_type:
            return None
        band = str(getattr(event, "band", "") or "").strip().lower()
        if not band:
            if "_" not in event_type:
                return None
            band = event_type.split("_", 1)[0]
        if event_type.endswith("_enter"):
            return (band, "enter")
        if event_type.endswith("_drop"):
            return (band, "drop")
        if event_type.endswith("_lift"):
            return (band, "lift")
        if event_type.endswith("_swell"):
            return (band, "swell")
        if event_type.endswith("_exit"):
            return (band, "exit")
        return None

    @staticmethod
    def _dominant_band_route_key(phrase: Phrase) -> tuple[str, str] | None:
        if not phrase.dominant_band or not phrase.band_ratios:
            return None
        try:
            dominant_idx = max(
                range(len(phrase.band_ratios)),
                key=phrase.band_ratios.__getitem__,
            )
        except ValueError:
            return None
        dominant_strength = float(phrase.band_ratios[dominant_idx])
        if dominant_strength < 0.14:
            return None
        return (phrase.dominant_band, "dominant")

    @staticmethod
    def _normalize_eq_routes(raw: object) -> list[dict]:
        if not isinstance(raw, list):
            return []
        routes: list[dict] = []
        for route in raw:
            if isinstance(route, dict):
                routes.append(dict(route))
        return routes

    @staticmethod
    def _normalize_instrument_routes(raw: object) -> list[dict]:
        if not isinstance(raw, list):
            return []
        routes: list[dict] = []
        for route in raw:
            if isinstance(route, dict):
                routes.append(dict(route))
        return routes

    @staticmethod
    def _merge_eq_routes(base_routes: list[dict], override_routes: list[dict]) -> list[dict]:
        merged: dict[tuple[str, str], dict] = {
            (str(route.get("band", "")), str(route.get("when", ""))): dict(route)
            for route in base_routes
        }
        order = [
            (str(route.get("band", "")), str(route.get("when", "")))
            for route in base_routes
        ]
        for route in override_routes:
            key = (str(route.get("band", "")), str(route.get("when", "")))
            if key not in merged:
                order.append(key)
            existing = dict(merged.get(key, {}))
            existing.update(route)
            merged[key] = existing
        return [merged[key] for key in order if key in merged]

    @staticmethod
    def _merge_instrument_routes(base_routes: list[dict], override_routes: list[dict]) -> list[dict]:
        merged: dict[tuple[str, str], dict] = {
            (str(route.get("instrument", "")), str(route.get("when", ""))): dict(route)
            for route in base_routes
        }
        order = [
            (str(route.get("instrument", "")), str(route.get("when", "")))
            for route in base_routes
        ]
        for route in override_routes:
            key = (str(route.get("instrument", "")), str(route.get("when", "")))
            if key not in merged:
                order.append(key)
            existing = dict(merged.get(key, {}))
            existing.update(route)
            merged[key] = existing
        return [merged[key] for key in order if key in merged]

    @staticmethod
    def _align_route_color_biases(
        routes: list[dict],
        color_palette: tuple[str, ...],
    ) -> list[dict]:
        """Keep compiled route accents inside the authoritative selected palette."""
        aligned_routes: list[dict] = []
        for route in routes:
            aligned = dict(route)
            if aligned.get("color_bias") is not None:
                aligned["color_bias"] = nearest_palette_color(
                    str(aligned["color_bias"]),
                    color_palette,
                )
            aligned_routes.append(aligned)
        return aligned_routes

    @staticmethod
    def _apply_route_intensity(intensity: float, routes: list[dict]) -> float:
        boost = sum(float(route.get("intensity_boost", 0.0)) for route in routes)
        return min(1.0, intensity + boost)

    @staticmethod
    def _first_route_value(routes: list[dict], key: str) -> object | None:
        for route in routes:
            value = route.get(key)
            if value is not None:
                return value
        return None

    @staticmethod
    def _build_proxy_lookups(
        proxies: tuple[InstrumentProxy, ...],
    ) -> tuple[dict[float, InstrumentProxy], dict[float, InstrumentProxy | None]]:
        sorted_proxies = sorted(proxies, key=lambda proxy: (proxy.start_t, proxy.end_t))
        lookup: dict[float, InstrumentProxy] = {}
        prev_lookup: dict[float, InstrumentProxy | None] = {}
        previous: InstrumentProxy | None = None
        for proxy in sorted_proxies:
            key = round(proxy.start_t, 4)
            lookup[key] = proxy
            prev_lookup[key] = previous
            previous = proxy
        return lookup, prev_lookup

    @staticmethod
    def _apply_color_bias(
        color_palette: tuple[str, ...],
        color_bias: str,
    ) -> tuple[str, ...]:
        remaining = tuple(color for color in color_palette if color != color_bias)
        return (color_bias,) + remaining

    def _enrich_instrument_route(
        self,
        route: dict,
        instrument_proxy: InstrumentProxy,
    ) -> dict:
        enriched = dict(route)
        pan_follow = max(0.0, min(1.0, float(route.get("pan_follow", 0.0) or 0.0)))
        width_scale = max(0.0, float(route.get("width_scale", 1.0) or 1.0))
        pan_center = max(-1.0, min(1.0, float(instrument_proxy.pan_center)))
        pan_width = max(0.0, min(1.0, float(instrument_proxy.pan_width)))
        enriched["pan_center"] = round(pan_center, 4)
        enriched["pan_width"] = round(pan_width, 4)
        if any(abs(value) > 1e-6 for value in instrument_proxy.band_pan_centers):
            enriched["band_pan_centers"] = tuple(
                round(float(value), 4) for value in instrument_proxy.band_pan_centers
            )

        origin = self._spatial_origin_for_zone(str(route.get("spatial_zone", "") or ""))
        if origin is None:
            origin = {"x": 0.0, "y": 0.0, "z": 0.0}
        origin["x"] = round(max(-1.0, min(1.0, float(origin["x"]) + (pan_center * pan_follow))), 4)
        enriched["spatial_origin"] = origin
        if "spatial_width" not in enriched:
            enriched["spatial_width"] = round(
                max(0.12, min(1.5, 0.18 + (pan_width * width_scale))),
                4,
            )
        focus = self._spatial_focus_for_zone(str(route.get("spatial_zone", "") or ""))
        if focus is not None and "spatial_focus" not in enriched:
            enriched["spatial_focus"] = focus
        return enriched

    @staticmethod
    def _spatial_origin_for_zone(zone: str) -> dict[str, float] | None:
        if not zone:
            return None
        alias = zone.strip().lower()
        if alias in {"left", "right", "top", "bottom", "front", "back", "center", "balanced"}:
            return {
                "left": {"x": -1.0, "y": 0.0, "z": 0.0},
                "right": {"x": 1.0, "y": 0.0, "z": 0.0},
                "top": {"x": 0.0, "y": 1.0, "z": 0.0},
                "bottom": {"x": 0.0, "y": -1.0, "z": 0.0},
                "front": {"x": 0.0, "y": 0.0, "z": -1.0},
                "back": {"x": 0.0, "y": 0.0, "z": 1.0},
                "center": {"x": 0.0, "y": 0.0, "z": 0.0},
                "balanced": {"x": 0.0, "y": 0.0, "z": 0.0},
            }[alias].copy()

        parts = alias.replace("-", "_").split("_")
        x = 0.0
        y = 0.0
        z = 0.0
        matched = False
        for part in parts:
            if part == "left":
                x = -1.0
                matched = True
            elif part == "right":
                x = 1.0
                matched = True
            elif part in {"top", "ceiling", "high"}:
                y = 1.0
                matched = True
            elif part in {"bottom", "floor", "low"}:
                y = -1.0
                matched = True
            elif part == "front":
                z = -1.0
                matched = True
            elif part == "back":
                z = 1.0
                matched = True
            elif part in {"center", "mid", "middle"}:
                matched = True
        if not matched:
            return None
        return {"x": x, "y": y, "z": z}

    @staticmethod
    def _spatial_focus_for_zone(zone: str) -> str | None:
        if not zone:
            return None
        alias = zone.strip().lower()
        if alias in {"left", "right", "top", "bottom", "front", "back", "center", "balanced"}:
            return alias
        return None

    @staticmethod
    def _build_route_layers(routes: list[dict]) -> list[dict]:
        layers: list[dict] = []
        for route in routes:
            layer: dict[str, object] = {
                "when": str(route.get("when", "")),
                "layer_blend": str(route.get("layer_blend", "max") or "max"),
                "layer_weight": round(
                    max(0.15, min(1.0, 0.65 + float(route.get("intensity_boost", 0.0) or 0.0))),
                    3,
                ),
            }
            if "band" in route:
                layer["band"] = str(route.get("band", ""))
            if "instrument" in route:
                layer["instrument"] = str(route.get("instrument", ""))
            for key in (
                "color_bias",
                "spatial_preset",
                "spatial_mode",
                "spatial_origin",
                "spatial_direction",
                "spatial_width",
                "spatial_blend",
                "spatial_extent",
                "spatial_delay_ms",
                "spatial_axis",
                "spatial_focus",
                "spatial_zone",
                "pan_follow",
                "width_scale",
                "confidence_min",
                *_SPATIAL_LAYER_ROUTE_KEYS,
            ):
                if key in route:
                    layer[key] = route[key]
            if any(
                key in layer
                for key in (
                    "color_bias",
                    "spatial_preset",
                    "spatial_mode",
                    "spatial_origin",
                    "spatial_direction",
                    "spatial_width",
                    "spatial_blend",
                    "spatial_extent",
                    "spatial_delay_ms",
                    "spatial_axis",
                    "spatial_focus",
                    "spatial_zone",
                    "pan_follow",
                    "width_scale",
                    *_SPATIAL_LAYER_ROUTE_KEYS,
                )
            ):
                layers.append(layer)
        return layers

    @staticmethod
    def _instrument_route_is_active(
        route: dict,
        instrument_proxy: InstrumentProxy,
        previous_instrument_proxy: InstrumentProxy | None,
    ) -> bool:
        instrument = str(route.get("instrument", "")).strip().lower()
        when = str(route.get("when", "dominant")).strip().lower()
        if instrument not in INSTRUMENT_PROXY_NAMES:
            return False
        current_score = float(getattr(instrument_proxy, instrument, 0.0))
        previous_score = 0.0
        if previous_instrument_proxy is not None:
            previous_score = float(getattr(previous_instrument_proxy, instrument, 0.0))
        threshold = float(route.get("confidence_min", 0.45) or 0.45)

        if when == "dominant":
            return instrument_proxy.dominant_proxy == instrument and current_score >= threshold
        if when == "present":
            return current_score >= threshold
        if when == "enter":
            return current_score >= threshold and previous_score < threshold
        if when == "drop":
            return current_score < threshold and previous_score >= threshold
        return False

    @staticmethod
    def _instrument_proxy_to_dict(proxy: InstrumentProxy) -> dict[str, object]:
        return {
            "start_t": proxy.start_t,
            "end_t": proxy.end_t,
            "parent_section_index": proxy.parent_section_index,
            "dominant_proxy": proxy.dominant_proxy,
            "secondary_proxy": proxy.secondary_proxy,
            "drums": proxy.drums,
            "bass": proxy.bass,
            "vocals": proxy.vocals,
            "harmonic": proxy.harmonic,
            "percussive": proxy.percussive,
            "active_proxies": tuple(proxy.active_proxies),
        }
