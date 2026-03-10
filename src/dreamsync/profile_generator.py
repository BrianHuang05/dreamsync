"""Procedural ProfileConfig generation from color theory parameters.

Generates complete ProfileConfig objects algorithmically — structurally
identical to hand-crafted YAML profiles. Uses color harmony schemes and
energy-tiered palettes for mood-appropriate variety.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from dreamsync.color_utils import (
    analogous,
    complementary,
    generate_palette,
    split_complementary,
    tetradic,
    triadic,
)
from dreamsync.profile import (
    MoodEffectEntry,
    MoodProfileConfig,
    ProfileConfig,
    TransitionRule,
)

# ---------------------------------------------------------------------------
# Generator parameters
# ---------------------------------------------------------------------------

HARMONY_SCHEMES: dict[str, Any] = {
    "complementary": complementary,
    "triadic": triadic,
    "analogous": analogous,
    "split_complementary": split_complementary,
    "tetradic": tetradic,
}

TEMPERATURE_BIAS: dict[str, float] = {
    "warm": -15.0,
    "neutral": 0.0,
    "cool": 15.0,
}

SATURATION_RANGES: dict[str, tuple[float, float]] = {
    "muted": (0.25, 0.45),
    "medium": (0.50, 0.70),
    "vivid": (0.80, 1.00),
}

# Cycle intervals per harmony type (more colors = longer cycle)
_CYCLE_INTERVALS: dict[str, float] = {
    "complementary": 20.0,
    "triadic": 24.0,
    "analogous": 16.0,
    "split_complementary": 22.0,
    "tetradic": 28.0,
}


@dataclass(frozen=True)
class GeneratorParams:
    base_hue: float  # 0-360
    temperature: str  # "warm" | "neutral" | "cool"
    saturation: str  # "muted" | "medium" | "vivid"
    harmony: str  # key of HARMONY_SCHEMES
    seed: int | None = None


# ---------------------------------------------------------------------------
# Mood effect pools (replicates the best hand-crafted profile patterns)
# ---------------------------------------------------------------------------

_CHILL_EFFECTS = (
    MoodEffectEntry(name="wave_drift", weight=3.0),
    MoodEffectEntry(name="slow_breathe", weight=2.0),
    MoodEffectEntry(name="gradient_flow", weight=2.0),
)

_GROOVE_EFFECTS = (
    MoodEffectEntry(name="beat_pulse", weight=3.0),
    MoodEffectEntry(name="color_scroll", weight=2.0),
)

_HYPE_EFFECTS = (
    MoodEffectEntry(name="fast_scroll", weight=2.0),
    MoodEffectEntry(name="beat_pulse", weight=1.0),
)

_DROP_EFFECTS = (
    MoodEffectEntry(name="drop_blast", weight=1.0),
)


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

def generate_profile(params: GeneratorParams) -> ProfileConfig:
    """Generate a complete ProfileConfig from GeneratorParams.

    Creates 3 energy-tiered palettes (calm/energy/intense), maps them to
    4 moods with appropriate effect pools, and adds transition rules.
    """
    harmony_fn = HARMONY_SCHEMES[params.harmony]
    temp_bias = TEMPERATURE_BIAS[params.temperature]
    sat_lo, sat_hi = SATURATION_RANGES[params.saturation]

    biased_hue = (params.base_hue + temp_bias) % 360.0
    anchor_hues = harmony_fn(biased_hue)

    # 3 palettes at different energy levels
    calm_palette = generate_palette(
        anchor_hues, saturation=sat_lo, lightness=0.6, count=6, variation=0.08,
    )
    energy_palette = generate_palette(
        anchor_hues, saturation=(sat_lo + sat_hi) / 2, lightness=0.5, count=6, variation=0.10,
    )
    intense_palette = generate_palette(
        anchor_hues, saturation=sat_hi, lightness=0.4, count=6, variation=0.12,
    )

    palettes = {
        "calm": calm_palette,
        "energy": energy_palette,
        "intense": intense_palette,
    }

    moods = {
        "chill": MoodProfileConfig(
            palettes=("calm",),
            effects=_CHILL_EFFECTS,
            params={"wave_rate_mult": 0.3},
        ),
        "groove": MoodProfileConfig(
            palettes=("calm", "energy"),
            effects=_GROOVE_EFFECTS,
            params={},
        ),
        "hype": MoodProfileConfig(
            palettes=("energy", "intense"),
            effects=_HYPE_EFFECTS,
            params={},
        ),
        "drop": MoodProfileConfig(
            palettes=("intense",),
            effects=_DROP_EFFECTS,
            params={"pulse_decay": 3.5},
        ),
    }

    transitions = (
        TransitionRule(from_mood="chill", to_mood="groove", palette="calm"),
        TransitionRule(from_mood="groove", to_mood="hype", palette="energy"),
        TransitionRule(from_mood="hype", to_mood="drop", palette="intense"),
    )

    cycle_interval = _CYCLE_INTERVALS.get(params.harmony, 20.0)
    name = f"{params.harmony}_{int(params.base_hue)}_{params.temperature}"

    return ProfileConfig(
        name=name,
        palettes=palettes,
        moods=moods,
        description=f"Generated {params.harmony} profile at hue {int(params.base_hue)} ({params.temperature}/{params.saturation})",
        author="dreamsync-generator",
        tags=(params.harmony, params.temperature, params.saturation, "generated"),
        version=1,
        source_path=None,
        transitions=transitions,
        cycle_interval=cycle_interval,
    )


def generate_random_profile(rng: random.Random | None = None) -> ProfileConfig:
    """Generate a profile with randomized GeneratorParams."""
    if rng is None:
        rng = random.Random()
    params = GeneratorParams(
        base_hue=rng.uniform(0.0, 360.0),
        temperature=rng.choice(["warm", "neutral", "cool"]),
        saturation=rng.choice(["muted", "medium", "vivid"]),
        harmony=rng.choice(list(HARMONY_SCHEMES.keys())),
        seed=None,
    )
    return generate_profile(params)


def generate_profile_set(count: int, seed: int | None = None) -> list[ProfileConfig]:
    """Generate `count` profiles with maximally-spread base hues.

    Uses golden-angle spacing (137.5 deg) to distribute hues evenly around
    the color wheel. Alternates harmony schemes and temperatures.
    """
    rng = random.Random(seed)
    golden_angle = 137.508  # 360 / phi^2

    harmonies = list(HARMONY_SCHEMES.keys())
    temperatures = ["warm", "neutral", "cool"]
    saturations = ["muted", "medium", "vivid"]

    profiles: list[ProfileConfig] = []
    start_hue = rng.uniform(0.0, 360.0)

    for i in range(count):
        hue = (start_hue + i * golden_angle) % 360.0
        params = GeneratorParams(
            base_hue=hue,
            temperature=temperatures[i % len(temperatures)],
            saturation=saturations[i % len(saturations)],
            harmony=harmonies[i % len(harmonies)],
            seed=seed,
        )
        profiles.append(generate_profile(params))

    return profiles
