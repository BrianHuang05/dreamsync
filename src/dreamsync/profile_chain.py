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
# Tag helpers
# ---------------------------------------------------------------------------

def _get_tag(profile: ProfileConfig, prefix: str) -> str | None:
    """Extract a tag value by prefix. e.g., _get_tag(p, 'primary:') -> 'O'"""
    for tag in profile.tags:
        if tag.startswith(prefix):
            return tag[len(prefix):]
    return None


_INTENSITY_ORDER = ("muted", "medium", "vivid")


def _tag_score(
    current: ProfileConfig,
    candidate: ProfileConfig,
    mood: str | None = None,
) -> float:
    """Score a candidate profile based on tag relationships to current.

    Returns a value in [0.0, 1.0] where 1.0 = ideal transition.
    """
    score = 0.0

    cur_primary = _get_tag(current, "primary:")
    cand_primary = _get_tag(candidate, "primary:")
    cur_secondary = _get_tag(current, "secondary:")
    cand_secondary = _get_tag(candidate, "secondary:")

    # If either profile lacks tags, return neutral score
    if cur_primary is None or cand_primary is None:
        return 0.5

    # Primary color contrast: different primary -> +0.4
    if cand_primary != cur_primary:
        score += 0.4

    # Secondary color thread: candidate secondary == current primary -> +0.2
    if cand_secondary is not None and cand_secondary == cur_primary:
        score += 0.2

    # Temperature
    cur_temp = None
    cand_temp = None
    for t in ("warm", "cool", "neutral"):
        if t in current.tags:
            cur_temp = t
        if t in candidate.tags:
            cand_temp = t

    if cur_temp and cand_temp:
        same_temp = cur_temp == cand_temp
        if mood in ("chill", "groove"):
            score += 0.2 if same_temp else 0.0
        elif mood in ("hype", "drop"):
            score += 0.2 if not same_temp else 0.0
        else:
            score += 0.1

    # Intensity
    cur_int = None
    cand_int = None
    for val in _INTENSITY_ORDER:
        if val in current.tags:
            cur_int = val
        if val in candidate.tags:
            cand_int = val

    if cur_int and cand_int:
        if cur_int == cand_int:
            score += 0.1
        else:
            ci = _INTENSITY_ORDER.index(cur_int)
            cai = _INTENSITY_ORDER.index(cand_int)
            if abs(ci - cai) == 1:
                score += 0.05

    # Color distance within ideal range -> +0.1
    dist = profile_color_distance(current, candidate)
    if 60.0 <= dist <= 150.0:
        score += 0.1

    return min(score, 1.0)


# ---------------------------------------------------------------------------
# Distance matrix & neighbor selection
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
    mood: str | None = None,
) -> ProfileConfig:
    """Select next profile using combined tag score + color distance.

    Scoring:
      1. Filter: exclude current, history, out-of-distance-range
      2. For each candidate:
         - tag_weight = _tag_score(current, candidate, mood)
         - dist_weight = 1.0 / (distance + 1.0)
         - combined = (0.6 * tag_weight) + (0.4 * dist_weight)
      3. Weighted random choice using combined scores

    Fallback: if no tagged profiles (e.g., hand-crafted YAML profiles without
    tags), falls back to pure distance weighting (current behavior).
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

    # Check if any candidate has tags (primary:X)
    has_tags = _get_tag(current, "primary:") is not None and any(
        _get_tag(p, "primary:") is not None for p, _ in candidates
    )

    if has_tags:
        # Combined tag + distance scoring
        max_d = max(d for _, d in candidates) or 1.0
        weights = []
        for p, d in candidates:
            tag_w = _tag_score(current, p, mood)
            dist_w = 1.0 / (d + 1.0)
            # Normalize dist_w relative to pool
            dist_w_norm = dist_w * (max_d + 1.0)  # scale to ~[0, 1]
            combined = 0.6 * tag_w + 0.4 * dist_w_norm
            weights.append(max(combined, 0.01))
    else:
        # Pure distance fallback for untagged profiles
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
        self._seed = seed
        self._rng = random.Random(seed)
        self._current = self._pool[0]
        self._current_start_t: float = 0.0
        self._next: ProfileConfig | None = None
        self._blend_start_t: float | None = None
        self._history: list[str] = []
        self._distance_matrix = build_distance_matrix(self._pool)
        self._pool_index: dict[str, int] = {p.name: i for i, p in enumerate(pool)}

    @property
    def seed(self) -> int | None:
        return self._seed

    def pool_index_of(self, profile: ProfileConfig) -> int | None:
        """Return the profile's index in the original pool, or None."""
        return self._pool_index.get(profile.name)

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
                mood=mood,
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
