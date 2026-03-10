"""Tests for profile_generator — procedural profile generation."""

from __future__ import annotations

import re

import pytest

from dreamsync.color_utils import hex_to_hsl
from dreamsync.profile_generator import (
    HARMONY_SCHEMES,
    GeneratorParams,
    generate_profile,
    generate_profile_set,
    generate_random_profile,
)

HEX_RE = re.compile(r"^#[0-9a-f]{6}$")


def _make_params(**kwargs) -> GeneratorParams:
    defaults = dict(base_hue=180.0, temperature="neutral", saturation="medium", harmony="triadic")
    defaults.update(kwargs)
    return GeneratorParams(**defaults)


def test_generate_profile_returns_valid_config() -> None:
    p = generate_profile(_make_params())
    assert p.name
    assert p.version == 1
    assert p.palettes
    assert p.moods


def test_generate_profile_has_three_palettes() -> None:
    p = generate_profile(_make_params())
    assert set(p.palettes.keys()) == {"calm", "energy", "intense"}


def test_generate_profile_all_moods_defined() -> None:
    p = generate_profile(_make_params())
    for mood in ("chill", "groove", "hype", "drop"):
        assert mood in p.moods
        assert p.moods[mood].palettes  # non-empty


def test_generate_profile_palette_colors_valid_hex() -> None:
    p = generate_profile(_make_params())
    for name, colors in p.palettes.items():
        for c in colors:
            assert HEX_RE.match(c), f"Invalid hex in {name}: {c}"


def test_generate_profile_deterministic_with_seed() -> None:
    params = _make_params(seed=42)
    p1 = generate_profile(params)
    p2 = generate_profile(params)
    assert p1.palettes == p2.palettes
    assert p1.name == p2.name


def test_generate_profile_different_seeds_differ() -> None:
    p1 = generate_profile(_make_params(base_hue=100.0))
    p2 = generate_profile(_make_params(base_hue=280.0))
    assert p1.palettes != p2.palettes


def test_generate_random_profile_valid() -> None:
    import random
    rng = random.Random(99)
    p = generate_random_profile(rng)
    assert p.name
    assert p.palettes
    assert set(p.moods.keys()) == {"chill", "groove", "hype", "drop"}


def test_generate_profile_set_count() -> None:
    profiles = generate_profile_set(5, seed=1)
    assert len(profiles) == 5


def test_generate_profile_set_hue_spread() -> None:
    profiles = generate_profile_set(8, seed=42)
    hues = []
    for p in profiles:
        # Extract the hue from the first color of the "calm" palette
        first_color = p.palettes["calm"][0]
        h, _, _ = hex_to_hsl(first_color)
        hues.append(h)
    # Check consecutive hues are at least 30 degrees apart
    for i in range(len(hues) - 1):
        diff = abs(hues[i + 1] - hues[i])
        if diff > 180:
            diff = 360 - diff
        assert diff > 30, f"Hues {hues[i]:.0f} and {hues[i+1]:.0f} too close"


def test_generate_profile_set_deterministic() -> None:
    set1 = generate_profile_set(5, seed=42)
    set2 = generate_profile_set(5, seed=42)
    for a, b in zip(set1, set2):
        assert a.palettes == b.palettes


def test_all_harmony_schemes() -> None:
    for harmony in HARMONY_SCHEMES:
        p = generate_profile(_make_params(harmony=harmony))
        assert p.palettes
        assert len(p.palettes) == 3


def test_all_temperature_levels() -> None:
    for temp in ("warm", "neutral", "cool"):
        p = generate_profile(_make_params(temperature=temp))
        assert temp in p.name


def test_all_saturation_levels() -> None:
    profiles = {}
    for sat in ("muted", "medium", "vivid"):
        profiles[sat] = generate_profile(_make_params(saturation=sat))
    # Vivid should have higher average saturation than muted
    def avg_sat(prof):
        total = 0.0
        count = 0
        for colors in prof.palettes.values():
            for c in colors:
                _, s, _ = hex_to_hsl(c)
                total += s
                count += 1
        return total / count if count else 0.0
    assert avg_sat(profiles["vivid"]) > avg_sat(profiles["muted"])


def test_generate_profile_transitions() -> None:
    p = generate_profile(_make_params())
    assert len(p.transitions) == 3
    froms = [t.from_mood for t in p.transitions]
    assert "chill" in froms
    assert "groove" in froms
    assert "hype" in froms
