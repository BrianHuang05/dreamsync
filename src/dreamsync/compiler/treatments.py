"""Treatment Selector — map section mood to concrete lighting treatment."""

from __future__ import annotations

import random
from dataclasses import dataclass

from dreamsync.mood import Mood
from dreamsync.effects import EFFECTS, PALETTES, MOOD_EFFECTS, MOOD_PALETTES
from dreamsync.profile import ProfileConfig


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

        # 2. Palette selection
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
