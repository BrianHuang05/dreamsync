from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from dreamsync.mood import Mood
from dreamsync.render import RenderMode


@dataclass(frozen=True)
class EffectPreset:
    name: str
    render_mode: RenderMode
    color_palette: tuple[str, ...]
    params: dict[str, Any]


def _eq_route_to_mapping(route: Any) -> dict[str, Any]:
    """Convert a profile EqRouteRule-like object into runtime params data."""

    data = {
        "band": str(getattr(route, "band", "")),
        "when": str(getattr(route, "when", "dominant")),
    }
    color_bias = getattr(route, "color_bias", None)
    if color_bias is not None:
        data["color_bias"] = color_bias
    render_mode = getattr(route, "render_mode", None)
    if render_mode is not None:
        data["render_mode"] = render_mode
    spatial_preset = getattr(route, "spatial_preset", None)
    if spatial_preset is not None:
        data["spatial_preset"] = spatial_preset
    intensity_boost = float(getattr(route, "intensity_boost", 0.0) or 0.0)
    if intensity_boost:
        data["intensity_boost"] = intensity_boost
    return data


def _instrument_route_to_mapping(route: Any) -> dict[str, Any]:
    """Convert a profile InstrumentRouteRule-like object into runtime params data."""

    data = {
        "instrument": str(getattr(route, "instrument", "")),
        "when": str(getattr(route, "when", "dominant")),
        "pan_follow": float(getattr(route, "pan_follow", 0.0) or 0.0),
        "width_scale": float(getattr(route, "width_scale", 1.0) or 1.0),
        "confidence_min": float(getattr(route, "confidence_min", 0.45) or 0.45),
    }
    color_bias = getattr(route, "color_bias", None)
    if color_bias is not None:
        data["color_bias"] = color_bias
    render_mode = getattr(route, "render_mode", None)
    if render_mode is not None:
        data["render_mode"] = render_mode
    spatial_preset = getattr(route, "spatial_preset", None)
    if spatial_preset is not None:
        data["spatial_preset"] = spatial_preset
    spatial_zone = getattr(route, "spatial_zone", None)
    if spatial_zone is not None:
        data["spatial_zone"] = spatial_zone
    intensity_boost = float(getattr(route, "intensity_boost", 0.0) or 0.0)
    if intensity_boost:
        data["intensity_boost"] = intensity_boost
    return data


# ---------------------------------------------------------------------------
# Palettes
# ---------------------------------------------------------------------------

PALETTES: dict[str, tuple[str, ...]] = {
    "warm": ("#ff4400", "#ff8800", "#ffcc00", "#ff6600", "#ffaa00", "#ff5500"),
    "cool": ("#0044ff", "#0088ff", "#00ccff", "#0066ff", "#00aaff", "#0055ff"),
    "sunset": ("#ff4400", "#ff0066", "#cc00ff", "#ff8800", "#ff0044", "#aa00ff"),
    "vivid": ("#ff0000", "#00ff00", "#0000ff", "#ff8800", "#aa00ff", "#00ffcc"),
    "pastel": ("#ffaacc", "#aaccff", "#ccffaa", "#ffccaa", "#ccaaff", "#aaffcc"),
    "neon": ("#ff00ff", "#00ffff", "#ffff00", "#ff0088", "#00ff88", "#88ff00"),
    "fire": ("#ff0000", "#ff4400", "#ff8800", "#ffcc00", "#ff2200", "#ff6600"),
    "ice": ("#0044ff", "#0088ff", "#00ccff", "#aaddff", "#0066ff", "#44aaff"),
}

MOOD_PALETTES: dict[Mood, tuple[str, ...]] = {
    Mood.CHILL: ("warm", "cool", "pastel", "ice"),
    Mood.GROOVE: ("vivid", "sunset", "neon", "warm"),
    Mood.HYPE: ("neon", "vivid", "fire", "sunset"),
    Mood.DROP: ("fire", "neon", "vivid"),
}

# ---------------------------------------------------------------------------
# Effect presets
# ---------------------------------------------------------------------------

EFFECTS: dict[str, EffectPreset] = {
    "warm_glow": EffectPreset(
        name="warm_glow",
        render_mode=RenderMode.SOLID,
        color_palette=PALETTES["warm"],
        params={},
    ),
    "slow_breathe": EffectPreset(
        name="slow_breathe",
        render_mode=RenderMode.BREATHE,
        color_palette=PALETTES["cool"],
        params={"breathe_rate_mult": 0.5},
    ),
    "color_breathe": EffectPreset(
        name="color_breathe",
        render_mode=RenderMode.BREATHE,
        color_palette=PALETTES["vivid"],
        params={"breathe_rate_mult": 1.0},
    ),
    "beat_pulse": EffectPreset(
        name="beat_pulse",
        render_mode=RenderMode.PULSE,
        color_palette=PALETTES["vivid"],
        params={"pulse_decay": 4.0},
    ),
    "color_scroll": EffectPreset(
        name="color_scroll",
        render_mode=RenderMode.SCROLL,
        color_palette=PALETTES["sunset"],
        params={"scroll_inject_width": 0.2},
    ),
    "fast_scroll": EffectPreset(
        name="fast_scroll",
        render_mode=RenderMode.SCROLL,
        color_palette=PALETTES["neon"],
        params={"scroll_inject_width": 0.35},
    ),
"drop_blast": EffectPreset(
        name="drop_blast",
        render_mode=RenderMode.PULSE,
        color_palette=PALETTES["fire"],
        params={"pulse_decay": 6.0},
    ),
    "wave_drift": EffectPreset(
        name="wave_drift",
        render_mode=RenderMode.WAVE,
        color_palette=PALETTES["cool"],
        params={"wave_rate_mult": 0.5, "wave_wavelength": 1.5},
    ),
    "gradient_flow": EffectPreset(
        name="gradient_flow",
        render_mode=RenderMode.GRADIENT,
        color_palette=PALETTES["sunset"],
        params={
            "gradient_speed": 0.08,
            "gradient_colors": PALETTES["sunset"],
        },
    ),
}

# Mood → list of (effect_name, weight)
MOOD_EFFECTS: dict[Mood, list[tuple[str, float]]] = {
    Mood.CHILL: [
        ("warm_glow", 2.0),
        ("slow_breathe", 3.0),
        ("color_breathe", 1.0),
        ("wave_drift", 2.0),
        ("gradient_flow", 2.0),
    ],
    Mood.GROOVE: [
        ("color_breathe", 1.0),
        ("beat_pulse", 3.0),
        ("color_scroll", 2.0),
        ("wave_drift", 1.0),
    ],
    Mood.HYPE: [
        ("fast_scroll", 2.0),
        ("beat_pulse", 1.0),
    ],
    Mood.DROP: [
        ("drop_blast", 1.0),
    ],
}


# ---------------------------------------------------------------------------
# EffectCycler
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EffectCyclerConfig:
    cycle_interval: float = 16.0
    drop_blast_duration: float = 2.0


class EffectCycler:
    """Selects and cycles EffectPresets based on mood, time, and beat events."""

    def __init__(
        self,
        config: EffectCyclerConfig | None = None,
        seed: int | None = None,
        profile: Any | None = None,
        show_palette_cycle: tuple[str, ...] = (),
    ) -> None:
        self.config = config or EffectCyclerConfig()
        self._rng = random.Random(seed)
        self._profile = profile  # ProfileConfig | None (lazy import avoidance)
        self._current_effect: str | None = None
        self._current_mood: Mood | None = None
        self._prev_mood: Mood | None = None
        self._effect_start_t: float = -1e9
        self._palette_name: str | None = None
        self._in_drop: bool = False
        self._drop_start_t: float = -1e9
        self._show_palette_cycle: tuple[str, ...] = ()
        self._show_palette_index = 0
        self.set_show_palette_cycle(show_palette_cycle)

    def set_profile(self, profile: Any | None) -> None:
        """Hot-swap the active profile (single reference assignment, GIL-safe).

        Clears cached mood, palette, and drop state so the next update()
        re-picks everything from the new profile (avoids stale palette name
        references that would crash _resolve_palette_colors).
        """
        self._profile = profile
        self._current_mood = None
        self._palette_name = None
        self._in_drop = False

    def set_show_palette_cycle(self, palette_names: tuple[str, ...]) -> None:
        """Use the named palettes in order, advancing only on song boundaries."""
        self._show_palette_cycle = tuple(name for name in palette_names if name)
        self._show_palette_index = 0
        self._palette_name = None

    def advance_show_palette(self) -> str | None:
        """Move to the next Show Palette after a detected song boundary."""
        if not self._show_palette_cycle:
            return None
        self._show_palette_index = (
            self._show_palette_index + 1
        ) % len(self._show_palette_cycle)
        self._palette_name = None
        return self.current_show_palette

    @property
    def profile(self) -> Any | None:
        """The profile currently supplying reactive palettes and presets."""
        return self._profile

    def reset(self) -> None:
        """Clear accumulated state for a new song."""
        self._current_effect = None
        self._current_mood = None
        self._prev_mood = None
        self._effect_start_t = -1e9
        self._palette_name = None
        self._in_drop = False
        self._drop_start_t = -1e9

    @property
    def current_effect(self) -> str | None:
        return self._current_effect

    @property
    def current_palette(self) -> str | None:
        return self.current_show_palette or self._palette_name

    @property
    def current_show_palette(self) -> str | None:
        if not self._show_palette_cycle:
            return None
        return self._show_palette_cycle[self._show_palette_index]

    @property
    def show_palette_colors(self) -> tuple[str, ...]:
        palette_name = self.current_show_palette
        return self._resolve_palette_colors(palette_name) if palette_name else ()

    @property
    def show_palette_queue(self) -> tuple[str, ...]:
        """The selected Show Palette set, ordered from the active palette."""
        if not self._show_palette_cycle:
            return ()
        count = len(self._show_palette_cycle)
        return tuple(
            self._show_palette_cycle[(self._show_palette_index + offset) % count]
            for offset in range(count)
        )

    def seconds_until_next_cycle(self, t: float) -> float | None:
        """Return the remaining timed-effect cycle delay, when one is active."""
        if self._show_palette_cycle or self._effect_start_t <= -1e8:
            return None
        return max(0.0, self._cycle_interval - (t - self._effect_start_t))

    @property
    def _cycle_interval(self) -> float:
        """Effective cycle interval: profile override → config default."""
        p = self._profile
        if p is not None and p.cycle_interval is not None:
            return p.cycle_interval
        return self.config.cycle_interval

    def _pick_effect(self, mood: Mood, exclude: str | None = None) -> str:
        """Weighted random selection from the mood's effect pool."""
        p = self._profile
        if p is not None:
            mood_cfg = p.moods.get(mood.value)
            if mood_cfg and mood_cfg.effects:
                pool = [(e.name, e.weight) for e in mood_cfg.effects]
                if exclude is not None and len(pool) > 1:
                    pool = [(n, w) for n, w in pool if n != exclude]
                names = [n for n, _ in pool]
                weights = [w for _, w in pool]
                return self._rng.choices(names, weights=weights, k=1)[0]

        pool = MOOD_EFFECTS[mood]
        if exclude is not None and len(pool) > 1:
            pool = [(name, w) for name, w in pool if name != exclude]
        names = [name for name, _ in pool]
        weights = [w for _, w in pool]
        return self._rng.choices(names, weights=weights, k=1)[0]

    def _pick_palette(self, mood: Mood, exclude: str | None = None) -> str:
        """Random palette from the mood's eligible palettes."""
        if self.current_show_palette is not None:
            return self.current_show_palette
        p = self._profile
        if p is not None:
            mood_cfg = p.moods.get(mood.value)
            if mood_cfg and mood_cfg.palettes:
                candidates = list(mood_cfg.palettes)
                if exclude is not None and len(candidates) > 1:
                    candidates = [
                        name for name in candidates if name != exclude
                    ]
                return self._rng.choice(candidates)

        candidates = list(MOOD_PALETTES[mood])
        if exclude is not None and len(candidates) > 1:
            candidates = [name for name in candidates if name != exclude]
        return self._rng.choice(candidates)

    def _resolve_palette_colors(self, palette_name: str) -> tuple[str, ...]:
        """Resolve a palette name to its color tuple (profile-local first, then built-in)."""
        p = self._profile
        if p is not None and palette_name in p.palettes:
            return p.palettes[palette_name]
        return PALETTES[palette_name]

    def _check_transition_palette(self, old_mood: Mood, new_mood: Mood) -> str | None:
        """Check if the profile has a forced palette for this mood transition."""
        if self.current_show_palette is not None:
            return None
        p = self._profile
        if p is None:
            return None
        for tr in p.transitions:
            if tr.from_mood == old_mood.value and tr.to_mood == new_mood.value:
                return tr.palette
        return None

    def apply_structural_action(
        self,
        *,
        cue_class: str,
        effect_name: str | None,
        color_action: str | None,
        target_bar: int,
        now_t: float,
        palette_name: str | None = None,
    ) -> EffectPreset:
        """Atomically apply one bar-locked structural visual action."""

        if target_bar < 0:
            raise ValueError("target_bar must be non-negative")
        mood = self._current_mood or Mood.GROOVE
        if color_action == "advance_approved_palette":
            advanced = self.advance_show_palette()
            if advanced is None:
                self._palette_name = self._pick_palette(
                    mood,
                    exclude=self._palette_name,
                )
        elif color_action == "recall_palette" and palette_name is not None:
            if palette_name in self._show_palette_cycle:
                self._show_palette_index = self._show_palette_cycle.index(
                    palette_name
                )
                self._palette_name = None
            else:
                self._palette_name = palette_name
        elif self._palette_name is None and self.current_show_palette is None:
            self._palette_name = self._pick_palette(mood)

        if cue_class not in {"bar_marker", "phrase_reset"} or self._current_effect is None:
            if effect_name is not None:
                if effect_name not in EFFECTS:
                    raise ValueError(f"unknown structural effect: {effect_name}")
                self._current_effect = effect_name
        if self._current_effect is None:
            if effect_name is None:
                raise ValueError("no structural effect is available")
            self._current_effect = effect_name
        palette = self.current_show_palette or self._palette_name
        if palette is None:
            palette = self._pick_palette(mood)
            self._palette_name = palette
        self._effect_start_t = float(now_t)
        preset = self._apply_palette(self._current_effect, palette, mood)
        if cue_class in {"bar_marker", "phrase_reset"}:
            params = dict(preset.params)
            params["structure_phase_reset"] = cue_class == "phrase_reset"
            params["structure_bar_marker"] = cue_class == "bar_marker"
            params["structure_target_bar"] = int(target_bar)
            preset = EffectPreset(
                name=preset.name,
                render_mode=preset.render_mode,
                color_palette=preset.color_palette,
                params=params,
            )
        return preset

    def _apply_palette(self, effect_name: str, palette_name: str, mood: Mood | None = None) -> EffectPreset:
        """Return a copy of the named effect with the given palette's colors.

        If a profile is active and defines param overrides for the mood,
        they are merged on top of the effect's base params.
        """
        base = EFFECTS[effect_name]
        colors = self._resolve_palette_colors(palette_name)
        params = dict(base.params)
        if base.render_mode == RenderMode.GRADIENT:
            # ``gradient_flow`` has built-in stops; replace them with the
            # selected profile palette so Reactive visibly follows the choice.
            params["gradient_colors"] = colors

        # Merge profile mood params
        p = self._profile
        if p is not None and mood is not None:
            mood_cfg = p.moods.get(mood.value)
            eq_routes: list[dict[str, Any]] = []
            instrument_routes: list[dict[str, Any]] = []
            if p.eq_routes:
                eq_routes.extend(_eq_route_to_mapping(route) for route in p.eq_routes)
            if p.instrument_routes:
                instrument_routes.extend(
                    _instrument_route_to_mapping(route) for route in p.instrument_routes
                )
            if mood_cfg and mood_cfg.params:
                params.update(mood_cfg.params)
            if mood_cfg and mood_cfg.eq_routes:
                eq_routes.extend(_eq_route_to_mapping(route) for route in mood_cfg.eq_routes)
            if mood_cfg and mood_cfg.instrument_routes:
                instrument_routes.extend(
                    _instrument_route_to_mapping(route)
                    for route in mood_cfg.instrument_routes
                )
            if eq_routes:
                existing = params.get("eq_routes")
                merged = [dict(route) for route in existing] if isinstance(existing, list) else []
                merged.extend(eq_routes)
                params["eq_routes"] = merged
            if instrument_routes:
                existing = params.get("instrument_routes")
                merged = [dict(route) for route in existing] if isinstance(existing, list) else []
                merged.extend(instrument_routes)
                params["instrument_routes"] = merged

        return EffectPreset(
            name=base.name,
            render_mode=base.render_mode,
            color_palette=colors,
            params=params,
        )

    def update(
        self,
        mood: Mood,
        t: float,
        beat: bool,
        bpm: float,
        energy: float,
        *,
        structure_event: str | None = None,
        structure_controlled: bool = False,
    ) -> EffectPreset:
        if (
            structure_controlled
            and structure_event != "macro_change"
            and self._current_effect is not None
        ):
            active_mood = self._current_mood or mood
            palette = self.current_show_palette or self._palette_name
            if palette is None:
                palette = self._pick_palette(active_mood)
                self._palette_name = palette
            return self._apply_palette(
                self._current_effect,
                palette,
                active_mood,
            )
        # --- DROP handling ---
        if mood == Mood.DROP:
            if not self._in_drop:
                self._in_drop = True
                self._drop_start_t = t
                self._current_effect = "drop_blast"
                self._palette_name = self._pick_palette(Mood.DROP)
                self._effect_start_t = t
                self._prev_mood = self._current_mood
                self._current_mood = Mood.DROP
            return self._apply_palette("drop_blast", self._palette_name, Mood.DROP)

        # --- Leaving DROP ---
        if self._in_drop:
            self._in_drop = False
            self._current_effect = self._pick_effect(mood)
            # Check transition rule from DROP to new mood
            tr_pal = self._check_transition_palette(Mood.DROP, mood)
            self._palette_name = tr_pal if tr_pal else self._pick_palette(mood)
            self._effect_start_t = t
            self._prev_mood = self._current_mood
            self._current_mood = mood
            return self._apply_palette(self._current_effect, self._palette_name, mood)

        # --- First call or mood change ---
        if self._current_mood is None or mood != self._current_mood:
            self._current_effect = self._pick_effect(mood)
            # Check transition rule
            tr_pal = None
            if self._current_mood is not None:
                tr_pal = self._check_transition_palette(self._current_mood, mood)
            self._palette_name = tr_pal if tr_pal else self._pick_palette(mood)
            self._effect_start_t = t
            self._prev_mood = self._current_mood
            self._current_mood = mood
            return self._apply_palette(self._current_effect, self._palette_name, mood)

        # A confirmed live macro event requests one immediate, deliberate
        # effect/palette transition.  This optional seam is inert for compiled
        # shows and all existing callers.
        if structure_event == "macro_change":
            self._current_effect = self._pick_effect(
                mood,
                exclude=self._current_effect,
            )
            self._palette_name = self._pick_palette(
                mood,
                exclude=self._palette_name,
            )
            self._effect_start_t = t
            return self._apply_palette(
                self._current_effect,
                self._palette_name,
                mood,
            )

        # --- Time-based cycling within same mood ---
        if (
            not structure_controlled
            and (t - self._effect_start_t) >= self._cycle_interval
        ):
            self._current_effect = self._pick_effect(mood, exclude=self._current_effect)
            self._palette_name = self._pick_palette(mood)
            self._effect_start_t = t
            return self._apply_palette(self._current_effect, self._palette_name, mood)

        # --- Steady state: return current preset ---
        return self._apply_palette(self._current_effect, self._palette_name, mood)
