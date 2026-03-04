"""Tests for dreamsync.compiler.treatments — Treatment Selector (Feature 3, Component 2)."""

from __future__ import annotations

import pytest

from dreamsync.compiler.treatments import Treatment, TreatmentSelector
from dreamsync.effects import EFFECTS, PALETTES, MOOD_EFFECTS, MOOD_PALETTES
from dreamsync.mood import Mood
from dreamsync.profile import (
    MoodEffectEntry,
    MoodProfileConfig,
    ProfileConfig,
    TransitionRule,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_profile(
    moods=None, palettes=None, transitions=(),
) -> ProfileConfig:
    """Build a minimal ProfileConfig for testing (no YAML needed)."""
    default_palettes = palettes or {}
    default_moods = moods or {
        "chill": MoodProfileConfig(palettes=("warm",)),
        "groove": MoodProfileConfig(palettes=("vivid",)),
        "hype": MoodProfileConfig(palettes=("neon",)),
        "drop": MoodProfileConfig(palettes=("fire",)),
    }
    return ProfileConfig(
        name="test",
        palettes=default_palettes,
        moods=default_moods,
        transitions=tuple(transitions),
    )


# ---------------------------------------------------------------------------
# 1. test_chill_mood_defaults
# ---------------------------------------------------------------------------

def test_chill_mood_defaults():
    """CHILL mood with no profile selects from built-in MOOD_EFFECTS and MOOD_PALETTES."""
    sel = TreatmentSelector(seed=42)
    t = sel.select("chill", "intro", 100.0)

    assert isinstance(t, Treatment)
    valid_effects = {name for name, _ in MOOD_EFFECTS[Mood.CHILL]}
    assert t.effect_name in valid_effects

    valid_palette_names = MOOD_PALETTES[Mood.CHILL]
    valid_color_sets = {PALETTES[p] for p in valid_palette_names}
    assert t.color_palette in valid_color_sets

    assert t.render_mode == EFFECTS[t.effect_name].render_mode.value


# ---------------------------------------------------------------------------
# 2. test_groove_mood_defaults
# ---------------------------------------------------------------------------

def test_groove_mood_defaults():
    """GROOVE mood with no profile selects from built-in pools."""
    sel = TreatmentSelector(seed=42)
    t = sel.select("groove", "verse", 110.0)

    assert isinstance(t, Treatment)
    valid_effects = {name for name, _ in MOOD_EFFECTS[Mood.GROOVE]}
    assert t.effect_name in valid_effects

    valid_palette_names = MOOD_PALETTES[Mood.GROOVE]
    valid_color_sets = {PALETTES[p] for p in valid_palette_names}
    assert t.color_palette in valid_color_sets


# ---------------------------------------------------------------------------
# 3. test_hype_mood_defaults
# ---------------------------------------------------------------------------

def test_hype_mood_defaults():
    """HYPE mood with no profile selects from built-in pools."""
    sel = TreatmentSelector(seed=42)
    t = sel.select("hype", "chorus", 135.0)

    assert isinstance(t, Treatment)
    valid_effects = {name for name, _ in MOOD_EFFECTS[Mood.HYPE]}
    assert t.effect_name in valid_effects

    valid_palette_names = MOOD_PALETTES[Mood.HYPE]
    valid_color_sets = {PALETTES[p] for p in valid_palette_names}
    assert t.color_palette in valid_color_sets


# ---------------------------------------------------------------------------
# 4. test_drop_always_drop_blast
# ---------------------------------------------------------------------------

def test_drop_always_drop_blast():
    """DROP mood always selects 'drop_blast' regardless of seed or profile."""
    for seed in [0, 42, 99, 1234]:
        sel = TreatmentSelector(seed=seed)
        t = sel.select("drop", "drop", 128.0)
        assert t.effect_name == "drop_blast"

    # Also with a profile that overrides drop effects
    profile = _make_profile(
        moods={
            "chill": MoodProfileConfig(palettes=("warm",)),
            "groove": MoodProfileConfig(palettes=("vivid",)),
            "hype": MoodProfileConfig(palettes=("neon",)),
            "drop": MoodProfileConfig(
                palettes=("fire",),
                effects=(MoodEffectEntry(name="beat_pulse", weight=10.0),),
            ),
        }
    )
    sel2 = TreatmentSelector(profile=profile, seed=42)
    t2 = sel2.select("drop", "drop", 128.0)
    assert t2.effect_name == "drop_blast"


# ---------------------------------------------------------------------------
# 5. test_profile_effect_override
# ---------------------------------------------------------------------------

def test_profile_effect_override():
    """Profile with custom effect pool: selected effect comes from profile pool."""
    profile = _make_profile(
        moods={
            "chill": MoodProfileConfig(
                palettes=("warm",),
                effects=(
                    MoodEffectEntry(name="wave_drift", weight=10.0),
                ),
            ),
            "groove": MoodProfileConfig(palettes=("vivid",)),
            "hype": MoodProfileConfig(palettes=("neon",)),
            "drop": MoodProfileConfig(palettes=("fire",)),
        }
    )
    sel = TreatmentSelector(profile=profile, seed=42)
    t = sel.select("chill", "intro", 100.0)
    # With only wave_drift in the pool, it must be selected
    assert t.effect_name == "wave_drift"


# ---------------------------------------------------------------------------
# 6. test_profile_palette_override
# ---------------------------------------------------------------------------

def test_profile_palette_override():
    """Profile with custom palette pool: palette comes from profile."""
    profile = _make_profile(
        moods={
            "chill": MoodProfileConfig(palettes=("ice",)),
            "groove": MoodProfileConfig(palettes=("vivid",)),
            "hype": MoodProfileConfig(palettes=("neon",)),
            "drop": MoodProfileConfig(palettes=("fire",)),
        }
    )
    sel = TreatmentSelector(profile=profile, seed=42)
    t = sel.select("chill", "intro", 100.0)
    # Only "ice" in the pool, so the colors must be the ice palette
    assert t.color_palette == PALETTES["ice"]


# ---------------------------------------------------------------------------
# 7. test_builtin_fallback
# ---------------------------------------------------------------------------

def test_builtin_fallback():
    """Profile exists but doesn't define effects for one mood: fallback to MOOD_EFFECTS."""
    profile = _make_profile(
        moods={
            "chill": MoodProfileConfig(palettes=("warm",)),  # no effects defined
            "groove": MoodProfileConfig(palettes=("vivid",)),
            "hype": MoodProfileConfig(palettes=("neon",)),
            "drop": MoodProfileConfig(palettes=("fire",)),
        }
    )
    sel = TreatmentSelector(profile=profile, seed=42)
    t = sel.select("chill", "intro", 100.0)
    valid_effects = {name for name, _ in MOOD_EFFECTS[Mood.CHILL]}
    assert t.effect_name in valid_effects


# ---------------------------------------------------------------------------
# 8. test_repeat_avoidance_effect
# ---------------------------------------------------------------------------

def test_repeat_avoidance_effect():
    """select() many times with same mood, pool > 1: at least 2 different effects appear."""
    sel = TreatmentSelector(seed=42)
    effects_seen = set()
    for i in range(20):
        t = sel.select("groove", f"section_{i}", 110.0)
        effects_seen.add(t.effect_name)
    # GROOVE has 4 effects; over 20 calls we must see at least 2 different ones
    assert len(effects_seen) >= 2, f"Only saw {effects_seen} over 20 calls"


# ---------------------------------------------------------------------------
# 9. test_repeat_avoidance_palette
# ---------------------------------------------------------------------------

def test_repeat_avoidance_palette():
    """select() many times with same mood, pool > 1: at least 2 different palettes appear."""
    sel = TreatmentSelector(seed=42)
    palettes_seen = set()
    for i in range(20):
        t = sel.select("groove", f"section_{i}", 110.0)
        palettes_seen.add(t.color_palette)
    # GROOVE has 4 palettes; over 20 calls we must see at least 2 different ones
    assert len(palettes_seen) >= 2, f"Only saw {len(palettes_seen)} palette(s) over 20 calls"


# ---------------------------------------------------------------------------
# 10. test_single_pool_entry_no_crash
# ---------------------------------------------------------------------------

def test_single_pool_entry_no_crash():
    """Mood with single-entry pool: no crash/infinite loop on re-roll."""
    profile = _make_profile(
        moods={
            "chill": MoodProfileConfig(
                palettes=("warm",),
                effects=(MoodEffectEntry(name="warm_glow", weight=1.0),),
            ),
            "groove": MoodProfileConfig(palettes=("vivid",)),
            "hype": MoodProfileConfig(palettes=("neon",)),
            "drop": MoodProfileConfig(palettes=("fire",)),
        }
    )
    sel = TreatmentSelector(profile=profile, seed=42)
    t1 = sel.select("chill", "intro", 100.0)
    t2 = sel.select("chill", "verse", 100.0)
    # Both must be warm_glow (only option), and no crash
    assert t1.effect_name == "warm_glow"
    assert t2.effect_name == "warm_glow"


# ---------------------------------------------------------------------------
# 11. test_transition_rule_palette
# ---------------------------------------------------------------------------

def test_transition_rule_palette():
    """Profile with TransitionRule from 'chill' to 'hype': forced palette on mood change."""
    profile = _make_profile(
        moods={
            "chill": MoodProfileConfig(palettes=("warm",)),
            "groove": MoodProfileConfig(palettes=("vivid",)),
            "hype": MoodProfileConfig(palettes=("neon",)),
            "drop": MoodProfileConfig(palettes=("fire",)),
        },
        transitions=[
            TransitionRule(from_mood="chill", to_mood="hype", palette="sunset"),
        ],
    )
    sel = TreatmentSelector(profile=profile, seed=42)
    # First call establishes chill as prev_mood
    sel.select("chill", "intro", 100.0)
    # Transition from chill to hype should force sunset palette
    t2 = sel.select("hype", "chorus", 135.0)
    assert t2.color_palette == PALETTES["sunset"]


# ---------------------------------------------------------------------------
# 12. test_speed_from_bpm
# ---------------------------------------------------------------------------

def test_speed_from_bpm():
    """Verify all BPM breakpoints: <90->0.3, 90->0.5, 120->0.7, 140->0.9."""
    sel = TreatmentSelector(seed=42)

    # bpm < 90 -> 0.3
    t = sel.select("chill", "s1", 80.0)
    assert t.speed == 0.3

    # bpm = 90 (>= 90 and < 120) -> 0.5
    sel2 = TreatmentSelector(seed=42)
    t = sel2.select("chill", "s2", 90.0)
    assert t.speed == 0.5

    # bpm = 120 (>= 120 and < 140) -> 0.7
    sel3 = TreatmentSelector(seed=42)
    t = sel3.select("chill", "s3", 120.0)
    assert t.speed == 0.7

    # bpm = 140 (>= 140) -> 0.9
    sel4 = TreatmentSelector(seed=42)
    t = sel4.select("chill", "s4", 140.0)
    assert t.speed == 0.9


# ---------------------------------------------------------------------------
# 13. test_drop_speed_override
# ---------------------------------------------------------------------------

def test_drop_speed_override():
    """DROP mood speed = 1.0 even with BPM < 90."""
    sel = TreatmentSelector(seed=42)
    for bpm in [60.0, 80.0, 100.0, 120.0, 160.0]:
        t = sel.select("drop", "drop_section", bpm)
        assert t.speed == 1.0, f"Expected speed=1.0 for drop at bpm={bpm}, got {t.speed}"


# ---------------------------------------------------------------------------
# 14. test_params_merge
# ---------------------------------------------------------------------------

def test_params_merge():
    """Profile mood params override effect defaults."""
    profile = _make_profile(
        moods={
            "chill": MoodProfileConfig(
                palettes=("warm",),
                effects=(MoodEffectEntry(name="slow_breathe", weight=10.0),),
                params={"breathe_rate_mult": 0.25, "custom_key": "custom_val"},
            ),
            "groove": MoodProfileConfig(palettes=("vivid",)),
            "hype": MoodProfileConfig(palettes=("neon",)),
            "drop": MoodProfileConfig(palettes=("fire",)),
        }
    )
    sel = TreatmentSelector(profile=profile, seed=42)
    t = sel.select("chill", "intro", 100.0)
    assert t.effect_name == "slow_breathe"
    # Profile param overrides the effect default (0.5 -> 0.25)
    assert t.params["breathe_rate_mult"] == 0.25
    # Profile adds a custom key
    assert t.params["custom_key"] == "custom_val"


# ---------------------------------------------------------------------------
# 15. test_deterministic_seed
# ---------------------------------------------------------------------------

def test_deterministic_seed():
    """Same seed -> identical treatment sequence across two independent selectors."""
    moods_sequence = ["chill", "chill", "groove", "hype", "drop", "chill"]
    bpms = [80.0, 100.0, 115.0, 135.0, 128.0, 95.0]
    labels = [f"section_{i}" for i in range(len(moods_sequence))]

    sel_a = TreatmentSelector(seed=999)
    sel_b = TreatmentSelector(seed=999)

    for mood, label, bpm in zip(moods_sequence, labels, bpms):
        ta = sel_a.select(mood, label, bpm)
        tb = sel_b.select(mood, label, bpm)
        assert ta == tb, f"Mismatch at {label}: {ta} != {tb}"
