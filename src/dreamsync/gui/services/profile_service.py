"""Profile and palette persistence services."""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any, Iterable

from dreamsync.color_utils import (
    analogous,
    complementary,
    generate_palette,
    hex_to_hsl,
    interpolate_hex_hsl,
)
from dreamsync.profile_generator import generate_random_profile
from dreamsync.profile import (
    EqRouteRule,
    InstrumentRouteRule,
    MoodEffectEntry,
    ProfileConfig,
    TransitionRule,
    list_available_profiles,
    load_profile,
)


class ProfileService:
    """Load and save profile data for the GUI."""

    def __init__(self) -> None:
        self._yaml = None

    def _require_yaml(self):
        if self._yaml is None:
            import yaml

            self._yaml = yaml
        return self._yaml

    def load_profile(self, path: Path) -> ProfileConfig:
        return load_profile(path)

    def list_profiles(self) -> list[dict[str, object]]:
        return list_available_profiles()

    def load_palette_choices(self, path: Path) -> list[dict[str, object]]:
        profile = self.load_profile(path)
        return [
            {
                "profile_name": profile.name,
                "profile_path": str(path),
                "palette_name": palette_name,
                "colors": tuple(colors),
            }
            for palette_name, colors in profile.palettes.items()
        ]

    def generate_palette_choice(self, *, seed: int | None = None) -> dict[str, object]:
        rng = random.Random(seed)
        profile = generate_random_profile(rng)
        palette_name = "energy" if "energy" in profile.palettes else next(iter(profile.palettes))
        return {
            "profile_name": profile.name,
            "profile_path": "",
            "palette_name": palette_name,
            "colors": tuple(profile.palettes[palette_name]),
            "generated_seed": seed,
        }

    def generate_palette_from_seed_colors(
        self,
        seed_colors: Iterable[str],
        *,
        scheme: str,
        count: int = 6,
    ) -> tuple[str, ...]:
        colors = tuple(color.strip() for color in seed_colors if color and color.strip())
        if not colors:
            raise ValueError("Choose at least one seed color.")
        if count < 3:
            raise ValueError("Generated palettes must have at least 3 colors.")

        if scheme == "gradient":
            return self._gradient_palette(colors, count=count)

        hue_values = [hex_to_hsl(color)[0] for color in colors]
        saturation_values = [hex_to_hsl(color)[1] for color in colors]
        lightness_values = [hex_to_hsl(color)[2] for color in colors]
        anchor_hues = list(hue_values)

        if scheme == "complementary":
            for hue in hue_values:
                for anchor in complementary(hue):
                    if anchor not in anchor_hues:
                        anchor_hues.append(anchor)
        elif scheme == "analogous":
            anchor_hues = analogous(hue_values[0], spread=28.0)
        elif scheme == "triadic":
            base = hue_values[0]
            anchor_hues = [base % 360.0, (base + 120.0) % 360.0, (base + 240.0) % 360.0]
        else:
            raise ValueError(f"Unknown palette scheme '{scheme}'.")

        saturation = sum(saturation_values) / len(saturation_values)
        lightness = sum(lightness_values) / len(lightness_values)
        return generate_palette(
            anchor_hues,
            saturation=max(0.25, min(0.95, saturation)),
            lightness=max(0.25, min(0.72, lightness)),
            count=count,
            variation=0.08,
        )

    @staticmethod
    def _gradient_palette(colors: tuple[str, ...], *, count: int) -> tuple[str, ...]:
        if len(colors) == 1:
            base = colors[0]
            h, s, l = hex_to_hsl(base)
            steps = []
            for index in range(count):
                phase = index / max(count - 1, 1)
                lightness = max(0.16, min(0.82, (l * 0.65) + (0.38 * phase)))
                saturation = max(0.18, min(1.0, s * (0.85 + 0.25 * phase)))
                steps.append(generate_palette([h], saturation=saturation, lightness=lightness, count=1, variation=0.0)[0])
            return tuple(steps)

        if len(colors) == 2:
            return tuple(
                interpolate_hex_hsl(colors[0], colors[1], index / max(count - 1, 1))
                for index in range(count)
            )

        segments = len(colors) - 1
        generated: list[str] = []
        for index in range(count):
            t = index / max(count - 1, 1)
            scaled = t * segments
            segment_index = min(int(scaled), segments - 1)
            local_t = scaled - segment_index
            generated.append(
                interpolate_hex_hsl(
                    colors[segment_index],
                    colors[segment_index + 1],
                    local_t,
                )
            )
        return tuple(generated)

    def generate_quickshow_profile_document(
        self,
        *,
        name: str,
        seed_colors: Iterable[str],
        scheme: str,
        energy_modifier: float,
        rng_seed: int | None = None,
    ) -> dict[str, object]:
        colors = tuple(color.strip() for color in seed_colors if color and color.strip())
        if not colors:
            colors = ("#7fffd4",)
        rng = random.Random(rng_seed)
        base_palette = self.generate_palette_from_seed_colors(colors, scheme=scheme, count=6)
        calm_palette = self._scale_palette(base_palette, saturation_scale=0.75, lightness_shift=0.10)
        energy_palette = self._scale_palette(
            base_palette,
            saturation_scale=max(0.7, min(1.25, 0.95 + ((energy_modifier - 1.0) * 0.35))),
            lightness_shift=0.0,
        )
        intense_palette = self._scale_palette(
            base_palette,
            saturation_scale=max(0.85, min(1.5, 1.12 + ((energy_modifier - 1.0) * 0.45))),
            lightness_shift=-0.10,
        )

        eq_routes = self._quickshow_eq_routes(rng, energy_palette, intense_palette, energy_modifier)
        instrument_routes = self._quickshow_instrument_routes(rng, energy_palette, intense_palette, energy_modifier)

        return {
            "name": name,
            "description": f"Quickshow generated from {scheme} seed colors at energy {energy_modifier:.2f}",
            "author": "dreamsync-gui",
            "tags": ["quickshow", scheme],
            "version": 1,
            "palettes": {
                "calm": list(calm_palette),
                "energy": list(energy_palette),
                "intense": list(intense_palette),
            },
            "eq_routes": eq_routes,
            "instrument_routes": instrument_routes,
            "moods": {
                "chill": {
                    "palettes": ["calm"],
                    "effects": self._quickshow_effects(
                        rng, ("wave_drift", "slow_breathe", "gradient_flow"), energy_modifier, count=2
                    ),
                    "params": {
                        "wave_rate_mult": round(max(0.2, 0.3 + ((energy_modifier - 1.0) * 0.18)), 3),
                        "gradient_speed": round(max(0.02, 0.05 + ((energy_modifier - 1.0) * 0.03)), 3),
                    },
                },
                "groove": {
                    "palettes": ["calm", "energy"],
                    "effects": self._quickshow_effects(
                        rng, ("beat_pulse", "color_scroll", "wave_drift"), energy_modifier, count=2
                    ),
                    "params": {
                        "scroll_inject_width": round(min(0.5, 0.18 + ((energy_modifier - 1.0) * 0.08)), 3),
                        "pulse_decay": round(max(2.0, 3.6 - ((energy_modifier - 1.0) * 0.8)), 3),
                    },
                    "eq_routes": eq_routes[:1],
                },
                "hype": {
                    "palettes": ["energy", "intense"],
                    "effects": self._quickshow_effects(
                        rng, ("fast_scroll", "beat_pulse", "gradient_flow"), energy_modifier, count=2
                    ),
                    "params": {
                        "pulse_decay": round(max(1.5, 3.0 - ((energy_modifier - 1.0) * 0.9)), 3),
                        "wave_wavelength": round(max(0.8, 1.4 - ((energy_modifier - 1.0) * 0.18)), 3),
                    },
                    "instrument_routes": instrument_routes[:1],
                },
                "drop": {
                    "palettes": ["intense"],
                    "effects": self._quickshow_effects(
                        rng, ("drop_blast", "beat_pulse", "fast_scroll"), energy_modifier, count=2
                    ),
                    "params": {
                        "pulse_decay": round(max(1.0, 2.8 - ((energy_modifier - 1.0) * 1.1)), 3),
                        "gradient_speed": round(min(0.2, 0.07 + ((energy_modifier - 1.0) * 0.04)), 3),
                    },
                    "eq_routes": eq_routes[1:2],
                    "instrument_routes": instrument_routes[1:2],
                },
            },
            "transitions": [
                {"from": "chill", "to": "groove", "palette": "energy"},
                {"from": "groove", "to": "hype", "palette": "energy"},
                {"from": "hype", "to": "drop", "palette": "intense"},
            ],
            "cycle_interval": round(max(10.0, 18.0 - ((energy_modifier - 1.0) * 4.0)), 2),
        }

    def save_generated_quickshow_profile(
        self,
        directory: Path,
        *,
        name: str,
        seed_colors: Iterable[str],
        scheme: str,
        energy_modifier: float,
        rng_seed: int | None = None,
    ) -> Path:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        base_name = self._slugify(name or "quickshow")
        path = directory / f"{base_name}.yaml"
        suffix = 2
        while path.exists():
            path = directory / f"{base_name}-{suffix}.yaml"
            suffix += 1
        raw = self.generate_quickshow_profile_document(
            name=name or "Quickshow",
            seed_colors=seed_colors,
            scheme=scheme,
            energy_modifier=energy_modifier,
            rng_seed=rng_seed,
        )
        self.save_profile_document(path, raw)
        return path

    @staticmethod
    def _scale_palette(
        palette: tuple[str, ...],
        *,
        saturation_scale: float,
        lightness_shift: float,
    ) -> tuple[str, ...]:
        from dreamsync.color_utils import hsl_to_hex

        scaled: list[str] = []
        for color in palette:
            hue, saturation, lightness = hex_to_hsl(color)
            scaled.append(
                hsl_to_hex(
                    hue,
                    max(0.12, min(1.0, saturation * saturation_scale)),
                    max(0.12, min(0.82, lightness + lightness_shift)),
                )
            )
        return tuple(scaled)

    @staticmethod
    def _quickshow_effects(
        rng: random.Random,
        names: tuple[str, ...],
        energy_modifier: float,
        *,
        count: int,
    ) -> list[dict[str, object]]:
        pool = list(names)
        rng.shuffle(pool)
        selected = pool[:count]
        return [
            {
                "name": name,
                "weight": round(max(0.5, rng.uniform(0.9, 2.4) * max(0.7, energy_modifier)), 2),
            }
            for name in selected
        ]

    @staticmethod
    def _quickshow_eq_routes(
        rng: random.Random,
        energy_palette: tuple[str, ...],
        intense_palette: tuple[str, ...],
        energy_modifier: float,
    ) -> list[dict[str, object]]:
        return [
            {
                "band": rng.choice(["bass", "kick", "presence"]),
                "when": rng.choice(["dominant", "enter", "lift"]),
                "render_mode": rng.choice(["pulse", "wave", "gradient"]),
                "color_bias": rng.choice(energy_palette),
                "intensity_boost": round(rng.uniform(0.08, 0.22) * max(0.8, energy_modifier), 2),
            },
            {
                "band": rng.choice(["sub", "air", "presence"]),
                "when": rng.choice(["enter", "swell", "lift"]),
                "render_mode": rng.choice(["gradient", "scroll", "breathe"]),
                "color_bias": rng.choice(intense_palette),
                "intensity_boost": round(rng.uniform(0.05, 0.18) * max(0.8, energy_modifier), 2),
            },
        ]

    @staticmethod
    def _quickshow_instrument_routes(
        rng: random.Random,
        energy_palette: tuple[str, ...],
        intense_palette: tuple[str, ...],
        energy_modifier: float,
    ) -> list[dict[str, object]]:
        return [
            {
                "instrument": rng.choice(["vocals", "harmonic"]),
                "when": rng.choice(["dominant", "present"]),
                "render_mode": rng.choice(["gradient", "wave", "breathe"]),
                "color_bias": rng.choice(energy_palette),
                "intensity_boost": round(rng.uniform(0.04, 0.16) * max(0.8, energy_modifier), 2),
                "confidence_min": round(rng.uniform(0.35, 0.55), 2),
            },
            {
                "instrument": rng.choice(["bass", "drums", "percussive"]),
                "when": rng.choice(["dominant", "enter"]),
                "render_mode": rng.choice(["pulse", "scroll", "wave"]),
                "color_bias": rng.choice(intense_palette),
                "intensity_boost": round(rng.uniform(0.08, 0.24) * max(0.8, energy_modifier), 2),
                "confidence_min": round(rng.uniform(0.35, 0.55), 2),
            },
        ]

    @staticmethod
    def _slugify(value: str) -> str:
        slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
        slug = "-".join(part for part in slug.split("-") if part)
        return slug or "quickshow"

    def load_profile_document(self, path: Path) -> dict[str, object]:
        yaml = self._require_yaml()
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Profile must be a YAML mapping")
        return raw

    def save_profile_document(self, path: Path, raw: dict[str, object]) -> ProfileConfig:
        yaml = self._require_yaml()
        path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        return load_profile(path)

    def update_palette(self, path: Path, palette_name: str, colors: tuple[str, ...]) -> ProfileConfig:
        raw = self.load_profile_document(path)
        palettes = raw.setdefault("palettes", {})
        if not isinstance(palettes, dict):
            raise ValueError("Profile palettes must be a mapping")
        palettes[palette_name] = list(colors)
        return self.save_profile_document(path, raw)

    def update_mood_effects(
        self,
        path: Path,
        mood: str,
        effects: Iterable[dict[str, object] | MoodEffectEntry],
    ) -> ProfileConfig:
        raw = self.load_profile_document(path)
        mood_doc = self._require_mood_document(raw, mood)
        mood_doc["effects"] = [self._effect_to_data(effect) for effect in effects]
        return self.save_profile_document(path, raw)

    def update_mood_params(
        self,
        path: Path,
        mood: str,
        params: dict[str, object],
    ) -> ProfileConfig:
        raw = self.load_profile_document(path)
        mood_doc = self._require_mood_document(raw, mood)
        mood_doc["params"] = dict(params)
        return self.save_profile_document(path, raw)

    def update_eq_routes(
        self,
        path: Path,
        routes: Iterable[dict[str, object] | EqRouteRule],
        *,
        mood: str | None = None,
    ) -> ProfileConfig:
        raw = self.load_profile_document(path)
        target = self._require_mood_document(raw, mood) if mood else raw
        target["eq_routes"] = [self._eq_route_to_data(route) for route in routes]
        return self.save_profile_document(path, raw)

    def update_instrument_routes(
        self,
        path: Path,
        routes: Iterable[dict[str, object] | InstrumentRouteRule],
        *,
        mood: str | None = None,
    ) -> ProfileConfig:
        raw = self.load_profile_document(path)
        target = self._require_mood_document(raw, mood) if mood else raw
        target["instrument_routes"] = [
            self._instrument_route_to_data(route) for route in routes
        ]
        return self.save_profile_document(path, raw)

    def update_transitions(
        self,
        path: Path,
        transitions: Iterable[dict[str, object] | TransitionRule],
    ) -> ProfileConfig:
        raw = self.load_profile_document(path)
        raw["transitions"] = [
            self._transition_to_data(transition) for transition in transitions
        ]
        return self.save_profile_document(path, raw)

    @staticmethod
    def _require_mood_document(raw: dict[str, object], mood: str | None) -> dict[str, object]:
        if not mood:
            raise ValueError("A mood name is required for mood-scoped updates")
        moods = raw.setdefault("moods", {})
        if not isinstance(moods, dict):
            raise ValueError("Profile moods must be a mapping")
        mood_doc = moods.setdefault(mood, {})
        if not isinstance(mood_doc, dict):
            raise ValueError(f"Profile mood '{mood}' must be a mapping")
        return mood_doc

    @staticmethod
    def _effect_to_data(effect: dict[str, object] | MoodEffectEntry) -> dict[str, object]:
        if isinstance(effect, MoodEffectEntry):
            return {"name": effect.name, "weight": effect.weight}
        return {
            "name": str(effect.get("name", "")),
            **({"weight": float(effect["weight"])} if "weight" in effect else {}),
        }

    @staticmethod
    def _eq_route_to_data(route: dict[str, object] | EqRouteRule) -> dict[str, object]:
        if isinstance(route, EqRouteRule):
            data: dict[str, object] = {"band": route.band, "when": route.when}
            if route.color_bias is not None:
                data["color_bias"] = route.color_bias
            if route.render_mode is not None:
                data["render_mode"] = route.render_mode
            if route.spatial_preset is not None:
                data["spatial_preset"] = route.spatial_preset
            if route.intensity_boost:
                data["intensity_boost"] = route.intensity_boost
            return data
        return dict(route)

    @staticmethod
    def _instrument_route_to_data(
        route: dict[str, object] | InstrumentRouteRule,
    ) -> dict[str, object]:
        if isinstance(route, InstrumentRouteRule):
            data: dict[str, object] = {
                "instrument": route.instrument,
                "when": route.when,
            }
            if route.color_bias is not None:
                data["color_bias"] = route.color_bias
            if route.render_mode is not None:
                data["render_mode"] = route.render_mode
            if route.spatial_preset is not None:
                data["spatial_preset"] = route.spatial_preset
            if route.spatial_zone is not None:
                data["spatial_zone"] = route.spatial_zone
            if route.pan_follow:
                data["pan_follow"] = route.pan_follow
            if route.width_scale != 1.0:
                data["width_scale"] = route.width_scale
            if route.confidence_min != 0.45:
                data["confidence_min"] = route.confidence_min
            if route.intensity_boost:
                data["intensity_boost"] = route.intensity_boost
            return data
        return dict(route)

    @staticmethod
    def _transition_to_data(
        transition: dict[str, object] | TransitionRule,
    ) -> dict[str, object]:
        if isinstance(transition, TransitionRule):
            return {
                "from": transition.from_mood,
                "to": transition.to_mood,
                "palette": transition.palette,
            }
        return dict(transition)
