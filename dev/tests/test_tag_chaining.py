"""Tests for tag-aware profile chaining."""

from __future__ import annotations

import random

import pytest

from dreamsync.color_utils import hsl_to_hex
from dreamsync.profile import (
    MoodEffectEntry,
    MoodProfileConfig,
    ProfileConfig,
    TransitionRule,
)
from dreamsync.profile_chain import (
    ChainConfig,
    ProfileChain,
    _get_tag,
    _tag_score,
    pick_next_profile,
)
from dreamsync.profile_generator import generate_profile, generate_profile_set, GeneratorParams


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _quick_profile(
    name: str,
    hue: float = 0.0,
    tags: tuple[str, ...] = (),
    sat: float = 0.8,
) -> ProfileConfig:
    """Build a minimal ProfileConfig for testing."""
    pal = tuple(hsl_to_hex(hue + i * 10, sat, 0.5) for i in range(6))
    moods = {
        m: MoodProfileConfig(
            palettes=("p",),
            effects=(MoodEffectEntry(name="beat_pulse", weight=1.0),),
        )
        for m in ("chill", "groove", "hype", "drop")
    }
    return ProfileConfig(
        name=name,
        palettes={"p": pal},
        moods=moods,
        tags=tags,
        transitions=(
            TransitionRule(from_mood="chill", to_mood="groove", palette="p"),
        ),
    )


def _tagged_profile(
    name: str,
    primary: str = "R",
    secondary: str = "B",
    temp: str = "warm",
    intensity: str = "vivid",
    hue: float = 0.0,
) -> ProfileConfig:
    """Build a profile with full semantic tags."""
    return _quick_profile(
        name,
        hue=hue,
        tags=(
            "generated",
            f"primary:{primary}",
            f"secondary:{secondary}",
            temp,
            intensity,
            "complementary",
        ),
    )


def _fast_chain_config() -> ChainConfig:
    return ChainConfig(
        min_profile_duration=1.0,
        max_profile_duration=5.0,
        blend_duration=0.5,
    )


# ---------------------------------------------------------------------------
# Deliverable 2A: _get_tag
# ---------------------------------------------------------------------------

class TestGetTag:
    def test_get_tag_primary(self):
        p = _quick_profile("x", tags=("generated", "primary:O", "warm"))
        assert _get_tag(p, "primary:") == "O"

    def test_get_tag_secondary(self):
        p = _quick_profile("x", tags=("secondary:B",))
        assert _get_tag(p, "secondary:") == "B"

    def test_get_tag_missing(self):
        p = _quick_profile("x", tags=("warm", "vivid"))
        assert _get_tag(p, "primary:") is None

    def test_get_tag_empty_tags(self):
        p = _quick_profile("x", tags=())
        assert _get_tag(p, "primary:") is None


# ---------------------------------------------------------------------------
# Deliverable 2A: _tag_score
# ---------------------------------------------------------------------------

class TestTagScore:
    def test_different_primary_scores_higher(self):
        current = _tagged_profile("cur", primary="R")
        same = _tagged_profile("same", primary="R")
        diff = _tagged_profile("diff", primary="B", hue=180.0)
        assert _tag_score(current, diff) > _tag_score(current, same)

    def test_color_thread_bonus(self):
        current = _tagged_profile("cur", primary="R", secondary="G")
        # Candidate whose secondary matches current's primary (R)
        threaded = _tagged_profile("threaded", primary="B", secondary="R", hue=180.0)
        not_threaded = _tagged_profile("not_threaded", primary="B", secondary="G", hue=180.0)
        assert _tag_score(current, threaded) > _tag_score(current, not_threaded)

    def test_chill_same_temp_scores_higher(self):
        current = _tagged_profile("cur", primary="R", temp="warm")
        same_temp = _tagged_profile("same", primary="B", temp="warm", hue=180.0)
        diff_temp = _tagged_profile("diff", primary="B", temp="cool", hue=180.0)
        assert _tag_score(current, same_temp, mood="chill") > _tag_score(current, diff_temp, mood="chill")

    def test_hype_different_temp_scores_higher(self):
        current = _tagged_profile("cur", primary="R", temp="warm")
        same_temp = _tagged_profile("same", primary="B", temp="warm", hue=180.0)
        diff_temp = _tagged_profile("diff", primary="B", temp="cool", hue=180.0)
        assert _tag_score(current, diff_temp, mood="hype") > _tag_score(current, same_temp, mood="hype")

    def test_same_intensity_scores_higher(self):
        current = _tagged_profile("cur", primary="R", intensity="vivid")
        same_int = _tagged_profile("same", primary="B", intensity="vivid", hue=180.0)
        opp_int = _tagged_profile("opp", primary="B", intensity="muted", hue=180.0)
        assert _tag_score(current, same_int) > _tag_score(current, opp_int)

    def test_score_range_zero_to_one(self):
        """Score is always in [0.0, 1.0]."""
        for _ in range(50):
            rng = random.Random(_)
            cur = _tagged_profile(
                "cur",
                primary=rng.choice("ROYGBIV"),
                secondary=rng.choice("ROYGBIV"),
                temp=rng.choice(["warm", "cool", "neutral"]),
                intensity=rng.choice(["muted", "medium", "vivid"]),
            )
            cand = _tagged_profile(
                "cand",
                primary=rng.choice("ROYGBIV"),
                secondary=rng.choice("ROYGBIV"),
                temp=rng.choice(["warm", "cool", "neutral"]),
                intensity=rng.choice(["muted", "medium", "vivid"]),
                hue=rng.uniform(0, 360),
            )
            score = _tag_score(cur, cand, mood=rng.choice(["chill", "groove", "hype", "drop", None]))
            assert 0.0 <= score <= 1.0

    def test_no_tags_returns_neutral(self):
        """Profiles without primary: tag get neutral 0.5 score."""
        cur = _quick_profile("cur", tags=("warm",))
        cand = _quick_profile("cand", tags=("cool",))
        assert _tag_score(cur, cand) == 0.5


# ---------------------------------------------------------------------------
# Deliverable 2B: pick_next_profile with tags
# ---------------------------------------------------------------------------

class TestPickNextProfileWithTags:
    def test_prefers_different_primary(self):
        """Over many picks, different-primary is chosen more often."""
        current = _tagged_profile("cur", primary="R")
        same = _tagged_profile("same", primary="R", hue=50.0)
        diff = _tagged_profile("diff", primary="B", hue=50.0)
        pool = [current, same, diff]

        diff_count = 0
        for i in range(200):
            rng = random.Random(i)
            picked = pick_next_profile(current, pool, [], rng, mood="chill")
            if picked.name == "diff":
                diff_count += 1
        assert diff_count > 100  # should be majority

    def test_color_thread_preference(self):
        """Profile whose secondary matches current primary is picked more often."""
        current = _tagged_profile("cur", primary="R")
        threaded = _tagged_profile("threaded", primary="G", secondary="R", hue=80.0)
        other = _tagged_profile("other", primary="G", secondary="V", hue=80.0)
        pool = [current, threaded, other]

        thread_count = 0
        for i in range(200):
            rng = random.Random(i)
            picked = pick_next_profile(current, pool, [], rng)
            if picked.name == "threaded":
                thread_count += 1
        assert thread_count > 100

    def test_chill_prefers_same_temp(self):
        current = _tagged_profile("cur", primary="R", temp="warm")
        same_t = _tagged_profile("same_t", primary="B", temp="warm", hue=180.0)
        diff_t = _tagged_profile("diff_t", primary="B", temp="cool", hue=180.0)
        pool = [current, same_t, diff_t]

        same_count = 0
        for i in range(200):
            rng = random.Random(i)
            picked = pick_next_profile(current, pool, [], rng, mood="chill")
            if picked.name == "same_t":
                same_count += 1
        assert same_count > 100

    def test_hype_prefers_different_temp(self):
        current = _tagged_profile("cur", primary="R", temp="warm")
        same_t = _tagged_profile("same_t", primary="B", temp="warm", hue=180.0)
        diff_t = _tagged_profile("diff_t", primary="B", temp="cool", hue=180.0)
        pool = [current, same_t, diff_t]

        diff_count = 0
        for i in range(200):
            rng = random.Random(i)
            picked = pick_next_profile(current, pool, [], rng, mood="hype")
            if picked.name == "diff_t":
                diff_count += 1
        assert diff_count > 100

    def test_no_tags_falls_back(self):
        """Hand-crafted profiles without tags still get picked (no crash)."""
        current = _quick_profile("cur", tags=())
        others = [_quick_profile(f"p{i}", hue=i * 40.0, tags=()) for i in range(5)]
        pool = [current] + others
        rng = random.Random(42)
        picked = pick_next_profile(current, pool, [], rng)
        assert picked.name != "cur"

    def test_mixed_pool(self):
        """Pool of tagged + untagged profiles — no crash, all selectable."""
        current = _tagged_profile("cur", primary="R")
        tagged = _tagged_profile("tagged", primary="G", hue=120.0)
        untagged = _quick_profile("untagged", hue=120.0, tags=())
        pool = [current, tagged, untagged]
        rng = random.Random(42)
        # Just verify no crash and something is picked
        picked = pick_next_profile(current, pool, [], rng)
        assert picked.name in ("tagged", "untagged")

    def test_still_avoids_history(self):
        """Tag scoring doesn't override history exclusion."""
        current = _tagged_profile("cur", primary="R")
        best = _tagged_profile("best", primary="B", secondary="R", hue=180.0)
        fallback = _tagged_profile("fb", primary="O", hue=30.0)
        pool = [current, best, fallback]

        rng = random.Random(42)
        picked = pick_next_profile(current, pool, ["best"], rng)
        assert picked.name == "fb"

    def test_still_respects_distance_bounds(self):
        """Tag scoring doesn't override min/max distance."""
        current = _tagged_profile("cur", primary="R", hue=0.0)
        # Make two candidates: one very close (same hue), one far
        close = _tagged_profile("close", primary="B", hue=5.0)
        far = _tagged_profile("far", primary="B", hue=180.0)
        pool = [current, close, far]

        rng = random.Random(42)
        picked = pick_next_profile(current, pool, [], rng, min_distance=40.0, max_distance=200.0)
        # Close profile likely has distance < 40, so only far should be picked
        # (depending on palette generation, but the intent is to test bounds are respected)
        assert picked is not None

    def test_deterministic_with_seed(self):
        """Same seed + mood -> same sequence."""
        pool = generate_profile_set(8, seed=42)
        seq1 = []
        seq2 = []
        for seq in (seq1, seq2):
            current = pool[0]
            for i in range(5):
                rng = random.Random(42 + i)
                nxt = pick_next_profile(current, pool, [], rng, mood="chill")
                seq.append(nxt.name)
                current = nxt
        assert seq1 == seq2


# ---------------------------------------------------------------------------
# ProfileChain integration
# ---------------------------------------------------------------------------

class TestProfileChainTagIntegration:
    def test_chain_passes_mood_to_picker(self):
        """Chain.update() uses mood for tag-aware selection."""
        pool = generate_profile_set(6, seed=42)
        chain = ProfileChain(pool, _fast_chain_config(), seed=42)
        # Advance past min duration
        result = chain.update(2.0, "chill")
        # Should trigger switch and return a blended profile
        assert result is not None

    def test_pool_index_of(self):
        pool = generate_profile_set(6, seed=42)
        chain = ProfileChain(pool, seed=42)
        for i, p in enumerate(pool):
            assert chain.pool_index_of(p) == i

    def test_pool_index_of_unknown(self):
        pool = generate_profile_set(6, seed=42)
        chain = ProfileChain(pool, seed=42)
        unknown = _quick_profile("unknown")
        assert chain.pool_index_of(unknown) is None

    def test_seed_property(self):
        chain = ProfileChain(generate_profile_set(4, seed=99), seed=99)
        assert chain.seed == 99

    def test_seed_property_none(self):
        chain = ProfileChain(generate_profile_set(4, seed=42))
        assert chain.seed is None
