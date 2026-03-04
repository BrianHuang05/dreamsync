"""Tests for profile_fingerprint() — D4.1."""

from pathlib import Path

import pytest

from dreamsync.cache import profile_fingerprint
from dreamsync.profile import (
    MoodEffectEntry,
    MoodProfileConfig,
    ProfileConfig,
    TransitionRule,
)


def _make_profile(**kwargs) -> ProfileConfig:
    """Build a minimal valid ProfileConfig with overridable fields."""
    defaults = dict(
        name="test_profile",
        palettes={
            "warm": ("#FF0000", "#FF8800", "#FFFF00"),
            "cool": ("#0000FF", "#0088FF", "#00FFFF"),
        },
        moods={
            "chill": MoodProfileConfig(
                palettes=("cool",),
                effects=(MoodEffectEntry("breathe", 1.0),),
                params={"breathe_rate_mult": 0.6},
            ),
            "groove": MoodProfileConfig(
                palettes=("warm",),
                effects=(MoodEffectEntry("scroll", 0.7), MoodEffectEntry("wave", 0.3)),
                params={},
            ),
            "hype": MoodProfileConfig(
                palettes=("warm",),
                effects=(MoodEffectEntry("pulse", 1.0),),
                params={"pulse_decay": 0.3},
            ),
            "drop": MoodProfileConfig(
                palettes=("warm", "cool"),
                effects=(MoodEffectEntry("pulse", 0.8), MoodEffectEntry("wave", 0.2)),
                params={"pulse_decay": 0.15},
            ),
        },
        transitions=(
            TransitionRule("chill", "groove", "warm"),
            TransitionRule("groove", "hype", "warm"),
        ),
    )
    defaults.update(kwargs)
    return ProfileConfig(**defaults)


class TestProfileFingerprint:
    def test_none_returns_sentinel(self):
        assert profile_fingerprint(None) == "00000000"

    def test_determinism_repeated_calls(self):
        p = _make_profile()
        fp1 = profile_fingerprint(p)
        fp2 = profile_fingerprint(p)
        assert fp1 == fp2
        assert len(fp1) == 8
        assert all(c in "0123456789abcdef" for c in fp1)

    def test_content_equality(self):
        p1 = _make_profile()
        p2 = _make_profile()
        assert profile_fingerprint(p1) == profile_fingerprint(p2)

    def test_palette_change(self):
        p1 = _make_profile()
        p2 = _make_profile(palettes={
            "warm": ("#FF0000", "#FF8800", "#FFFFFF"),  # changed last color
            "cool": ("#0000FF", "#0088FF", "#00FFFF"),
        })
        assert profile_fingerprint(p1) != profile_fingerprint(p2)

    def test_mood_effect_change(self):
        p1 = _make_profile()
        moods = dict(p1.moods)
        moods["chill"] = MoodProfileConfig(
            palettes=("cool",),
            effects=(MoodEffectEntry("scroll", 1.0),),  # changed from breathe to scroll
            params={"breathe_rate_mult": 0.6},
        )
        p2 = _make_profile(moods=moods)
        assert profile_fingerprint(p1) != profile_fingerprint(p2)

    def test_mood_param_change(self):
        p1 = _make_profile()
        moods = dict(p1.moods)
        moods["chill"] = MoodProfileConfig(
            palettes=("cool",),
            effects=(MoodEffectEntry("breathe", 1.0),),
            params={"breathe_rate_mult": 0.9},  # changed from 0.6
        )
        p2 = _make_profile(moods=moods)
        assert profile_fingerprint(p1) != profile_fingerprint(p2)

    def test_transition_change(self):
        p1 = _make_profile()
        p2 = _make_profile(transitions=(
            TransitionRule("chill", "groove", "cool"),  # changed palette
            TransitionRule("groove", "hype", "warm"),
        ))
        assert profile_fingerprint(p1) != profile_fingerprint(p2)

    def test_cosmetic_fields_ignored(self):
        p1 = _make_profile()
        p2 = _make_profile(
            description="A totally different description",
            author="Someone Else",
            tags=("tag1", "tag2"),
            source_path=Path("/some/other/path.yaml"),
        )
        assert profile_fingerprint(p1) == profile_fingerprint(p2)
