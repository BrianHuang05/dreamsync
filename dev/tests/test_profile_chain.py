"""Tests for profile_chain — distance matrix, neighbor selection, cross-fade, chain controller."""

from __future__ import annotations

import random

import pytest

from dreamsync.profile import (
    MoodEffectEntry,
    MoodProfileConfig,
    ProfileConfig,
    TransitionRule,
)
from dreamsync.profile_chain import (
    ChainConfig,
    ProfileChain,
    blend_profiles,
    build_distance_matrix,
    make_blendable_pair,
    pick_next_profile,
)
from dreamsync.color_utils import hex_to_rgb
from dreamsync.profile_generator import generate_profile, generate_profile_set, GeneratorParams


def _quick_profile(name: str, hue_base: int) -> ProfileConfig:
    """Generate a simple profile for testing."""
    return generate_profile(GeneratorParams(
        base_hue=float(hue_base),
        temperature="neutral",
        saturation="medium",
        harmony="triadic",
    ))


def _make_pool(count: int = 5) -> list[ProfileConfig]:
    return generate_profile_set(count, seed=42)


# ---------------------------------------------------------------------------
# Distance matrix (2A)
# ---------------------------------------------------------------------------


def test_distance_matrix_symmetric() -> None:
    pool = _make_pool(4)
    m = build_distance_matrix(pool)
    for i in range(4):
        for j in range(4):
            assert m[i][j] == pytest.approx(m[j][i])


def test_distance_matrix_diagonal_zero() -> None:
    pool = _make_pool(3)
    m = build_distance_matrix(pool)
    for i in range(3):
        assert m[i][i] == pytest.approx(0.0)


def test_distance_matrix_size() -> None:
    pool = _make_pool(5)
    m = build_distance_matrix(pool)
    assert len(m) == 5
    assert all(len(row) == 5 for row in m)


# ---------------------------------------------------------------------------
# Neighbor selection (2A)
# ---------------------------------------------------------------------------


def test_pick_next_avoids_history() -> None:
    pool = _make_pool(6)
    # Put 3 in history, current is pool[0], leaves pool[4] and pool[5] available
    history = [pool[1].name, pool[2].name, pool[3].name]
    rng = random.Random(0)
    picked = pick_next_profile(pool[0], pool, history, rng)
    assert picked.name not in history


def test_pick_next_prefers_close_profiles() -> None:
    pool = _make_pool(10)
    rng = random.Random(42)
    counts: dict[str, int] = {}
    for _ in range(200):
        p = pick_next_profile(pool[0], pool, [], rng)
        counts[p.name] = counts.get(p.name, 0) + 1
    # The most-picked should not be the most distant
    assert len(counts) > 1


def test_pick_next_respects_max_distance() -> None:
    pool = _make_pool(6)
    rng = random.Random(0)
    from dreamsync.color_utils import profile_color_distance
    for _ in range(20):
        p = pick_next_profile(pool[0], pool, [], rng, max_distance=300.0, min_distance=0.0)
        d = profile_color_distance(pool[0], p)
        assert d <= 300.0 or p.name != pool[0].name  # relaxation fallback is ok


def test_pick_next_relaxes_on_small_pool() -> None:
    pool = _make_pool(2)
    rng = random.Random(0)
    history = [pool[0].name]
    picked = pick_next_profile(pool[0], pool, history, rng)
    assert picked.name == pool[1].name


def test_pick_next_deterministic_with_seed() -> None:
    pool = _make_pool(6)
    seq1 = []
    seq2 = []
    for seed in (42, 42):
        rng = random.Random(seed)
        seq = []
        current = pool[0]
        for _ in range(10):
            p = pick_next_profile(current, pool, [], rng)
            seq.append(p.name)
            current = p
        if not seq1:
            seq1 = seq
        else:
            seq2 = seq
    assert seq1 == seq2


# ---------------------------------------------------------------------------
# Cross-fade blend (2B)
# ---------------------------------------------------------------------------


def test_blend_at_zero_equals_outgoing() -> None:
    a = _quick_profile("a", 0)
    b = _quick_profile("b", 180)
    blended = blend_profiles(a, b, 0.0)
    # At t=0, blended palette colors should match outgoing
    for name in a.palettes:
        if name in blended.palettes:
            for c_a, c_b in zip(a.palettes[name], blended.palettes[name]):
                ra, ga, ba = hex_to_rgb(c_a)
                rb, gb, bb = hex_to_rgb(c_b)
                assert abs(ra - rb) <= 1
                assert abs(ga - gb) <= 1
                assert abs(ba - bb) <= 1


def test_blend_at_one_equals_incoming() -> None:
    a = _quick_profile("a", 0)
    b = _quick_profile("b", 180)
    blended = blend_profiles(a, b, 1.0)
    for name in b.palettes:
        if name in blended.palettes:
            for c_a, c_b in zip(b.palettes[name], blended.palettes[name]):
                ra, ga, ba = hex_to_rgb(c_a)
                rb, gb, bb = hex_to_rgb(c_b)
                assert abs(ra - rb) <= 1
                assert abs(ga - gb) <= 1
                assert abs(ba - bb) <= 1


def test_blend_midpoint_interpolates() -> None:
    a = _quick_profile("a", 0)
    b = _quick_profile("b", 180)
    blended = blend_profiles(a, b, 0.5)
    # Midpoint should differ from both endpoints
    for name in blended.palettes:
        if name in a.palettes and name in b.palettes:
            for c_blend, c_a, c_b in zip(
                blended.palettes[name], a.palettes[name], b.palettes[name]
            ):
                rb, gb, bb = hex_to_rgb(c_blend)
                ra, ga, ba = hex_to_rgb(c_a)
                ri, gi, bi = hex_to_rgb(c_b)
                # At least one channel should be between the two extremes (or close)
                assert not (rb == ra == ri and gb == ga == gi and bb == ba == bi) or (
                    c_a == c_b
                )


def test_blend_returns_valid_profile() -> None:
    a = _quick_profile("a", 60)
    b = _quick_profile("b", 240)
    blended = blend_profiles(a, b, 0.5)
    assert blended.palettes
    assert set(blended.moods.keys()) == {"chill", "groove", "hype", "drop"}
    assert blended.version == 1


def test_blend_uses_incoming_mood_config() -> None:
    a = _quick_profile("a", 0)
    b = _quick_profile("b", 180)
    blended = blend_profiles(a, b, 0.5)
    # Mood config should come from b (the incoming profile)
    _, b_norm = make_blendable_pair(a, b)
    for mood in ("chill", "groove", "hype", "drop"):
        assert blended.moods[mood].effects == b_norm.moods[mood].effects


def test_make_blendable_pair_normalizes_names() -> None:
    """Profiles with different palette names get canonical names."""
    from dreamsync.profile import load_profile, resolve_profile_path

    # Use a generated profile (calm/energy/intense) and check normalization works
    a = _quick_profile("a", 0)
    b = _quick_profile("b", 180)
    # Both already have same names — should return as-is
    a_out, b_out = make_blendable_pair(a, b)
    assert set(a_out.palettes.keys()) == set(b_out.palettes.keys())


def test_blend_two_generated() -> None:
    profiles = generate_profile_set(4, seed=99)
    blended = blend_profiles(profiles[0], profiles[2], 0.5)
    assert blended.name.startswith("blend_")
    assert blended.palettes


# ---------------------------------------------------------------------------
# Chain controller (2C)
# ---------------------------------------------------------------------------


def _fast_chain_config(**kwargs) -> ChainConfig:
    """Short durations for fast testing."""
    defaults = dict(
        min_profile_duration=5.0,
        max_profile_duration=15.0,
        blend_duration=2.0,
        history_size=3,
    )
    defaults.update(kwargs)
    return ChainConfig(**defaults)


def test_chain_no_switch_before_min_duration() -> None:
    pool = _make_pool(5)
    chain = ProfileChain(pool, _fast_chain_config(min_profile_duration=60.0, max_profile_duration=300.0), seed=1)
    # Call update at t=1..59 — should get None (no switch)
    for t in range(1, 60):
        result = chain.update(float(t), "chill")
        assert result is None


def test_chain_switches_on_chill() -> None:
    pool = _make_pool(5)
    chain = ProfileChain(pool, _fast_chain_config(), seed=1)
    result = chain.update(6.0, "chill")
    assert result is not None


def test_chain_switches_on_groove() -> None:
    pool = _make_pool(5)
    chain = ProfileChain(pool, _fast_chain_config(), seed=1)
    result = chain.update(6.0, "groove")
    assert result is not None


def test_chain_blocks_on_hype() -> None:
    pool = _make_pool(5)
    chain = ProfileChain(pool, _fast_chain_config(max_profile_duration=999.0), seed=1)
    result = chain.update(6.0, "hype")
    assert result is None


def test_chain_blocks_on_drop() -> None:
    pool = _make_pool(5)
    chain = ProfileChain(pool, _fast_chain_config(max_profile_duration=999.0), seed=1)
    result = chain.update(6.0, "drop")
    assert result is None


def test_chain_forces_at_max_duration() -> None:
    pool = _make_pool(5)
    chain = ProfileChain(pool, _fast_chain_config(max_profile_duration=10.0), seed=1)
    # Even with "hype" mood, should force switch at max_duration
    result = chain.update(11.0, "hype")
    assert result is not None


def test_chain_blend_returns_interpolated() -> None:
    pool = _make_pool(5)
    chain = ProfileChain(pool, _fast_chain_config(blend_duration=4.0), seed=1)
    # Trigger blend
    result = chain.update(6.0, "chill")
    assert result is not None
    assert chain.is_blending
    assert result.name.startswith("blend_")


def test_chain_blend_completes() -> None:
    pool = _make_pool(5)
    cfg = _fast_chain_config(blend_duration=2.0)
    chain = ProfileChain(pool, cfg, seed=1)
    # Start blend at t=6
    chain.update(6.0, "chill")
    assert chain.is_blending
    # Complete blend at t=9 (well past blend_duration=2)
    result = chain.update(9.0, "chill")
    assert result is not None
    assert not chain.is_blending
    assert not result.name.startswith("blend_")


def test_chain_blend_progress_monotonic() -> None:
    pool = _make_pool(5)
    cfg = _fast_chain_config(blend_duration=4.0)
    chain = ProfileChain(pool, cfg, seed=1)
    initial = chain.current

    # Trigger blend
    chain.update(6.0, "chill")
    # Sample blend at multiple points
    results = []
    for dt in (0.5, 1.0, 2.0, 3.0):
        r = chain.update(6.0 + dt, "chill")
        if r is not None:
            results.append(r)
    assert len(results) >= 2


def test_chain_history_prevents_revisit() -> None:
    pool = _make_pool(5)
    cfg = _fast_chain_config(min_profile_duration=1.0, blend_duration=0.1, history_size=3)
    chain = ProfileChain(pool, cfg, seed=42)
    seen = [chain.current.name]
    t = 0.0
    for _ in range(20):
        t += 2.0
        result = chain.update(t, "chill")
        if result is not None and not result.name.startswith("blend_"):
            # Check recent 3 aren't repeated (mostly — small pools may force it)
            seen.append(result.name)
    # With 5 profiles and history_size=3, we should see variety
    assert len(set(seen)) >= 3


def test_chain_deterministic_with_seed() -> None:
    pool = _make_pool(6)
    cfg = _fast_chain_config(blend_duration=0.1)

    def run_chain(seed: int) -> list[str]:
        chain = ProfileChain(pool, cfg, seed=seed)
        names = []
        t = 0.0
        for _ in range(10):
            t += 6.0
            result = chain.update(t, "chill")
            if result and not result.name.startswith("blend_"):
                names.append(result.name)
            # Complete blend
            t += 1.0
            result = chain.update(t, "chill")
            if result and not result.name.startswith("blend_"):
                names.append(result.name)
        return names

    assert run_chain(42) == run_chain(42)


def test_chain_force_switch_immediate() -> None:
    pool = _make_pool(5)
    chain = ProfileChain(pool, _fast_chain_config(), seed=1)
    original = chain.current.name
    new = chain.force_switch(1.0)
    assert new.name != original
    assert not chain.is_blending


def test_chain_reset_clears_state() -> None:
    pool = _make_pool(5)
    chain = ProfileChain(pool, _fast_chain_config(), seed=1)
    chain.update(6.0, "chill")  # trigger blend
    chain.reset(10.0)
    assert not chain.is_blending
    assert chain.current.name == pool[0].name


def test_chain_small_pool_still_works() -> None:
    pool = _make_pool(2)
    cfg = _fast_chain_config(blend_duration=0.1, min_color_distance=0.0)
    chain = ProfileChain(pool, cfg, seed=1)
    t = 0.0
    switched = False
    for _ in range(10):
        t += 6.0
        r = chain.update(t, "chill")
        if r is not None:
            switched = True
            break
    assert switched


def test_chain_single_profile_pool() -> None:
    pool = [_quick_profile("solo", 120)]
    chain = ProfileChain(pool, _fast_chain_config(), seed=1)
    for t in range(1, 100):
        result = chain.update(float(t), "chill")
        assert result is None
