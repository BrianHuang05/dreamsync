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
        params={"pulse_decay": 6.0},
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
        params={"pulse_decay": 4.0},
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
    ) -> None:
        self.config = config or EffectCyclerConfig()
        self._rng = random.Random(seed)
        self._current_effect: str | None = None
        self._current_mood: Mood | None = None
        self._effect_start_t: float = -1e9
        self._palette_name: str | None = None
        self._in_drop: bool = False
        self._drop_start_t: float = -1e9

    @property
    def current_effect(self) -> str | None:
        return self._current_effect

    @property
    def current_palette(self) -> str | None:
        return self._palette_name

    def _pick_effect(self, mood: Mood, exclude: str | None = None) -> str:
        """Weighted random selection from the mood's effect pool."""
        pool = MOOD_EFFECTS[mood]
        if exclude is not None and len(pool) > 1:
            pool = [(name, w) for name, w in pool if name != exclude]
        names = [name for name, _ in pool]
        weights = [w for _, w in pool]
        return self._rng.choices(names, weights=weights, k=1)[0]

    def _pick_palette(self, mood: Mood) -> str:
        """Random palette from the mood's eligible palettes."""
        candidates = MOOD_PALETTES[mood]
        return self._rng.choice(candidates)

    def _apply_palette(self, effect_name: str, palette_name: str) -> EffectPreset:
        """Return a copy of the named effect with the given palette's colors."""
        base = EFFECTS[effect_name]
        colors = PALETTES[palette_name]
        return EffectPreset(
            name=base.name,
            render_mode=base.render_mode,
            color_palette=colors,
            params=base.params,
        )

    def update(
        self,
        mood: Mood,
        t: float,
        beat: bool,
        bpm: float,
        energy: float,
    ) -> EffectPreset:
        # --- DROP handling ---
        if mood == Mood.DROP:
            if not self._in_drop:
                self._in_drop = True
                self._drop_start_t = t
                self._current_effect = "drop_blast"
                self._palette_name = self._pick_palette(Mood.DROP)
                self._effect_start_t = t
                self._current_mood = Mood.DROP
            return self._apply_palette("drop_blast", self._palette_name)

        # --- Leaving DROP ---
        if self._in_drop:
            self._in_drop = False
            self._current_effect = self._pick_effect(mood)
            self._palette_name = self._pick_palette(mood)
            self._effect_start_t = t
            self._current_mood = mood
            return self._apply_palette(self._current_effect, self._palette_name)

        # --- First call or mood change ---
        if self._current_mood is None or mood != self._current_mood:
            self._current_effect = self._pick_effect(mood)
            self._palette_name = self._pick_palette(mood)
            self._effect_start_t = t
            self._current_mood = mood
            return self._apply_palette(self._current_effect, self._palette_name)

        # --- Time-based cycling within same mood ---
        if (t - self._effect_start_t) >= self.config.cycle_interval:
            self._current_effect = self._pick_effect(mood, exclude=self._current_effect)
            self._palette_name = self._pick_palette(mood)
            self._effect_start_t = t
            return self._apply_palette(self._current_effect, self._palette_name)

        # --- Steady state: return current preset ---
        return self._apply_palette(self._current_effect, self._palette_name)
