"""Smart profile chaining with mood-aware switching and palette cross-fade.

Replaces ProfileRotation for --auto-palette and --smart-rotation modes.
Picks next profiles based on color-space proximity, only switches during
calm moods, and cross-fades palettes over a configurable duration.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from dreamsync.color_utils import (
    interpolate_hex_hsl,
    profile_color_distance,
)
from dreamsync.profile import (
    MoodProfileConfig,
    ProfileConfig,
    TransitionRule,
)


# ---------------------------------------------------------------------------
# Distance matrix & neighbor selection (Deliverable 2A)
# ---------------------------------------------------------------------------

def build_distance_matrix(profiles: list[ProfileConfig]) -> list[list[float]]:
    """NxN symmetric matrix of profile_color_distance between all pairs."""
    n = len(profiles)
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            d = profile_color_distance(profiles[i], profiles[j])
            matrix[i][j] = d
            matrix[j][i] = d
    return matrix


def pick_next_profile(
    current: ProfileConfig,
    pool: list[ProfileConfig],
    history: list[str],
    rng: random.Random,
    max_distance: float | None = None,
    min_distance: float | None = None,
) -> ProfileConfig:
    """Select next profile from pool based on color distance to current.

    Excludes recent history, filters by distance bounds, and uses
    inverse-distance weighting for smooth transitions.
    """
    if max_distance is None:
        max_distance = 180.0
    if min_distance is None:
        min_distance = 40.0

    candidates: list[tuple[ProfileConfig, float]] = []
    for p in pool:
        if p.name == current.name:
            continue
        if p.name in history:
            continue
        d = profile_color_distance(current, p)
        if min_distance <= d <= max_distance:
            candidates.append((p, d))

    if not candidates:
        # Relax: pick closest non-history, non-current profile
        for p in pool:
            if p.name == current.name:
                continue
            if p.name in history:
                continue
            d = profile_color_distance(current, p)
            candidates.append((p, d))

    if not candidates:
        # Ultra-fallback: pick any non-current
        for p in pool:
            if p.name != current.name:
                d = profile_color_distance(current, p)
                candidates.append((p, d))

    if not candidates:
        return current  # single-profile pool

    # Weighted random: inverse distance (closer = more likely)
    max_d = max(d for _, d in candidates)
    weights = [(max_d - d + 1.0) for _, d in candidates]
    total = sum(weights)
    if total <= 0:
        return candidates[0][0]
    r = rng.uniform(0, total)
    cumulative = 0.0
    for (p, _), w in zip(candidates, weights):
        cumulative += w
        if r <= cumulative:
            return p
    return candidates[-1][0]


# ---------------------------------------------------------------------------
# Palette cross-fade (Deliverable 2B)
# ---------------------------------------------------------------------------

def _sort_palettes_by_lightness(
    palettes: dict[str, tuple[str, ...]],
) -> list[tuple[str, tuple[str, ...]]]:
    """Sort palettes by average lightness (low → high energy)."""
    from dreamsync.color_utils import hex_to_hsl

    def avg_lightness(colors: tuple[str, ...]) -> float:
        if not colors:
            return 0.5
        return sum(hex_to_hsl(c)[2] for c in colors) / len(colors)

    return sorted(palettes.items(), key=lambda kv: avg_lightness(kv[1]), reverse=True)


def make_blendable_pair(
    outgoing: ProfileConfig,
    incoming: ProfileConfig,
) -> tuple[ProfileConfig, ProfileConfig]:
    """Normalize two profiles so their palettes can be blended.

    Renames palettes to canonical names (palette_0, palette_1, palette_2)
    mapped by energy level (sorted by average lightness).
    """
    if set(outgoing.palettes.keys()) == set(incoming.palettes.keys()):
        return outgoing, incoming

    def _remap(profile: ProfileConfig) -> ProfileConfig:
        sorted_pals = _sort_palettes_by_lightness(profile.palettes)
        name_map: dict[str, str] = {}
        new_palettes: dict[str, tuple[str, ...]] = {}
        for i, (old_name, colors) in enumerate(sorted_pals):
            new_name = f"palette_{i}"
            name_map[old_name] = new_name
            new_palettes[new_name] = colors

        # Remap mood palette references
        new_moods: dict[str, MoodProfileConfig] = {}
        for mood_name, mc in profile.moods.items():
            new_pal_refs = tuple(name_map.get(p, p) for p in mc.palettes)
            new_moods[mood_name] = MoodProfileConfig(
                palettes=new_pal_refs,
                effects=mc.effects,
                params=mc.params,
            )

        # Remap transition palette references
        new_transitions = tuple(
            TransitionRule(
                from_mood=t.from_mood,
                to_mood=t.to_mood,
                palette=name_map.get(t.palette, t.palette),
            )
            for t in profile.transitions
        )

        return ProfileConfig(
            name=profile.name,
            palettes=new_palettes,
            moods=new_moods,
            description=profile.description,
            author=profile.author,
            tags=profile.tags,
            version=profile.version,
            source_path=profile.source_path,
            transitions=new_transitions,
            cycle_interval=profile.cycle_interval,
        )

    return _remap(outgoing), _remap(incoming)


def blend_profiles(
    outgoing: ProfileConfig,
    incoming: ProfileConfig,
    t: float,
) -> ProfileConfig:
    """Create a transient ProfileConfig by interpolating palette colors.

    t=0.0 -> outgoing's colors, t=1.0 -> incoming's colors.
    """
    out_norm, in_norm = make_blendable_pair(outgoing, incoming)

    blended_palettes: dict[str, tuple[str, ...]] = {}
    all_names = set(out_norm.palettes.keys()) | set(in_norm.palettes.keys())

    for name in all_names:
        if name in out_norm.palettes and name in in_norm.palettes:
            out_colors = out_norm.palettes[name]
            in_colors = in_norm.palettes[name]
            n = max(len(out_colors), len(in_colors))
            blended: list[str] = []
            for i in range(n):
                c_out = out_colors[i % len(out_colors)]
                c_in = in_colors[i % len(in_colors)]
                blended.append(interpolate_hex_hsl(c_out, c_in, t))
            blended_palettes[name] = tuple(blended)
        elif name in out_norm.palettes and t < 0.5:
            blended_palettes[name] = out_norm.palettes[name]
        elif name in in_norm.palettes:
            blended_palettes[name] = in_norm.palettes[name]
        else:
            blended_palettes[name] = out_norm.palettes.get(
                name, in_norm.palettes.get(name, ())
            )

    # Mood config comes from the incoming profile
    return ProfileConfig(
        name=f"blend_{outgoing.name}_to_{incoming.name}",
        palettes=blended_palettes,
        moods=in_norm.moods,
        description="",
        author="",
        tags=(),
        version=1,
        source_path=None,
        transitions=in_norm.transitions,
        cycle_interval=in_norm.cycle_interval,
    )


# ---------------------------------------------------------------------------
# Smart chain controller (Deliverable 2C)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ChainConfig:
    min_profile_duration: float = 60.0
    max_profile_duration: float = 180.0
    blend_duration: float = 8.0
    switch_on_moods: frozenset[str] = frozenset({"chill", "groove"})
    min_color_distance: float = 40.0
    max_color_distance: float = 180.0
    history_size: int = 3


class ProfileChain:
    """Intelligent profile sequencer with mood-aware switching and cross-fade."""

    def __init__(
        self,
        pool: list[ProfileConfig],
        config: ChainConfig | None = None,
        seed: int | None = None,
    ) -> None:
        self._pool = list(pool)
        self._config = config or ChainConfig()
        self._rng = random.Random(seed)
        self._current = self._pool[0]
        self._current_start_t: float = 0.0
        self._next: ProfileConfig | None = None
        self._blend_start_t: float | None = None
        self._history: list[str] = []
        self._distance_matrix = build_distance_matrix(self._pool)

    @property
    def current(self) -> ProfileConfig:
        """The profile that should be active right now."""
        return self._current

    @property
    def is_blending(self) -> bool:
        return self._blend_start_t is not None

    def update(self, t: float, mood: str) -> ProfileConfig | None:
        """Called every frame. Returns new ProfileConfig if changed, else None."""
        if len(self._pool) <= 1:
            return None

        # --- BLENDING state ---
        if self._blend_start_t is not None and self._next is not None:
            elapsed = t - self._blend_start_t
            progress = elapsed / self._config.blend_duration
            if progress >= 1.0:
                # Blend complete
                self._current = self._next
                self._current_start_t = t
                self._history.append(self._current.name)
                if len(self._history) > self._config.history_size:
                    self._history = self._history[-self._config.history_size:]
                self._next = None
                self._blend_start_t = None
                return self._current
            else:
                return blend_profiles(self._current, self._next, progress)

        # --- IDLE state ---
        elapsed_in_profile = t - self._current_start_t

        should_switch = False
        if elapsed_in_profile >= self._config.max_profile_duration:
            should_switch = True  # forced
        elif (
            elapsed_in_profile >= self._config.min_profile_duration
            and mood in self._config.switch_on_moods
        ):
            should_switch = True

        if should_switch:
            self._next = pick_next_profile(
                self._current,
                self._pool,
                self._history,
                self._rng,
                max_distance=self._config.max_color_distance,
                min_distance=self._config.min_color_distance,
            )
            self._blend_start_t = t
            # Return first blend frame
            return blend_profiles(self._current, self._next, 0.0)

        return None

    def force_switch(self, t: float) -> ProfileConfig:
        """Immediately switch to next profile (no blend)."""
        next_p = pick_next_profile(
            self._current,
            self._pool,
            self._history,
            self._rng,
            max_distance=self._config.max_color_distance,
            min_distance=self._config.min_color_distance,
        )
        self._current = next_p
        self._current_start_t = t
        self._history.append(self._current.name)
        if len(self._history) > self._config.history_size:
            self._history = self._history[-self._config.history_size:]
        self._next = None
        self._blend_start_t = None
        return self._current

    def reset(self, t: float) -> None:
        """Reset state for new session."""
        self._current = self._pool[0]
        self._current_start_t = t
        self._next = None
        self._blend_start_t = None
        self._history.clear()
