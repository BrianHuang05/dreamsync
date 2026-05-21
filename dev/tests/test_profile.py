"""Tests for the color profile system."""

from __future__ import annotations

import textwrap
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dreamsync.profile import (
    BUILTIN_PROFILES_DIR,
    EqRouteRule,
    InstrumentRouteRule,
    MoodEffectEntry,
    MoodProfileConfig,
    ProfileConfig,
    ProfileError,
    ProfileRotation,
    ProfileWatcher,
    TransitionRule,
    load_profile,
    list_available_profiles,
    resolve_profile_path,
    suggest_profile,
    validate_color_harmony,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MINIMAL_PROFILE_YAML = textwrap.dedent("""\
    name: "Test Profile"
    version: 1
    palettes:
      test_warm: ["#ff4400", "#ff8800", "#ffcc00"]
      test_cool: ["#0044ff", "#0088ff", "#00ccff"]
      test_neon: ["#ff00ff", "#00ffff", "#ffff00"]

    moods:
      chill:
        palettes: ["test_warm"]
      groove:
        palettes: ["test_warm", "test_cool"]
      hype:
        palettes: ["test_neon"]
      drop:
        palettes: ["test_neon"]
""")

FULL_PROFILE_YAML = textwrap.dedent("""\
    name: "Full Profile"
    description: "A profile with everything"
    author: "tester"
    tags: ["test", "full"]
    version: 1

    palettes:
      aurora_green: ["#003300", "#006633", "#009966", "#00cc99", "#33ffcc", "#66ffcc"]
      aurora_purple: ["#330066", "#6600cc", "#9933ff", "#cc66ff", "#9900ff", "#6633cc"]
      aurora_vivid: ["#00ff99", "#9933ff", "#ff0099", "#00ffcc", "#cc00ff", "#ff3366"]

    moods:
      chill:
        palettes: ["aurora_green"]
        effects:
          - name: "wave_drift"
            weight: 3.0
          - name: "slow_breathe"
            weight: 2.0
        params:
          wave_rate_mult: 0.3
      groove:
        palettes: ["aurora_green", "aurora_purple"]
      hype:
        palettes: ["aurora_vivid", "aurora_purple"]
      drop:
        palettes: ["aurora_vivid"]
        params:
          pulse_decay: 3.5

    transitions:
      - from: "chill"
        to: "groove"
        palette: "aurora_green"
      - from: "groove"
        to: "hype"
        palette: "aurora_purple"

    cycle_interval: 24.0
""")


def _write_yaml(tmp_path: Path, content: str, name: str = "profile.yaml") -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Dataclass tests
# ---------------------------------------------------------------------------


class TestDataclasses:
    def test_mood_effect_entry_frozen(self):
        e = MoodEffectEntry(name="wave_drift", weight=2.0)
        assert e.name == "wave_drift"
        assert e.weight == 2.0
        with pytest.raises(AttributeError):
            e.name = "other"  # type: ignore[misc]

    def test_mood_profile_config_defaults(self):
        m = MoodProfileConfig(palettes=("warm",))
        assert m.effects == ()
        assert m.params == {}

    def test_transition_rule(self):
        tr = TransitionRule(from_mood="chill", to_mood="groove", palette="warm")
        assert tr.from_mood == "chill"

    def test_eq_route_rule_defaults(self):
        route = EqRouteRule(band="bass")
        assert route.when == "dominant"
        assert route.intensity_boost == 0.0

    def test_instrument_route_rule_defaults(self):
        route = InstrumentRouteRule(instrument="vocals")
        assert route.when == "dominant"
        assert route.confidence_min == 0.45
        assert route.width_scale == 1.0

    def test_profile_config_frozen(self):
        pc = ProfileConfig(
            name="test",
            palettes={"a": ("#ff0000", "#00ff00", "#0000ff")},
            moods={"chill": MoodProfileConfig(palettes=("a",))},
        )
        with pytest.raises(AttributeError):
            pc.name = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# load_profile — happy paths
# ---------------------------------------------------------------------------


class TestLoadProfileHappy:
    def test_minimal_profile(self, tmp_path: Path):
        p = _write_yaml(tmp_path, MINIMAL_PROFILE_YAML)
        profile = load_profile(p)
        assert profile.name == "Test Profile"
        assert profile.version == 1
        assert len(profile.palettes) == 3
        assert len(profile.moods) == 4
        assert profile.transitions == ()
        assert profile.cycle_interval is None
        assert profile.source_path == p

    def test_full_profile(self, tmp_path: Path):
        p = _write_yaml(tmp_path, FULL_PROFILE_YAML)
        profile = load_profile(p)
        assert profile.name == "Full Profile"
        assert profile.description == "A profile with everything"
        assert profile.author == "tester"
        assert profile.tags == ("test", "full")
        assert profile.cycle_interval == 24.0
        assert len(profile.transitions) == 2
        assert profile.transitions[0].from_mood == "chill"

        chill = profile.moods["chill"]
        assert len(chill.effects) == 2
        assert chill.effects[0].name == "wave_drift"
        assert chill.effects[0].weight == 3.0
        assert chill.params.get("wave_rate_mult") == 0.3

        drop = profile.moods["drop"]
        assert drop.params.get("pulse_decay") == 3.5

    def test_profile_can_reference_builtin_palettes(self, tmp_path: Path):
        """Moods can reference PALETTES from effects.py (e.g. 'warm')."""
        yaml_text = textwrap.dedent("""\
            name: "Builtin Ref"
            version: 1
            palettes:
              custom: ["#aabbcc", "#ddeeff", "#112233"]
            moods:
              chill:
                palettes: ["warm"]
              groove:
                palettes: ["custom"]
              hype:
                palettes: ["neon"]
              drop:
                palettes: ["fire"]
        """)
        p = _write_yaml(tmp_path, yaml_text)
        profile = load_profile(p)
        assert profile.moods["chill"].palettes == ("warm",)

    def test_profile_eq_routes_load(self, tmp_path: Path):
        yaml_text = textwrap.dedent("""\
            name: "EQ Routed"
            version: 1
            palettes:
              warm: ["#ff4400", "#ff8800", "#ffcc00"]
              cool: ["#2244ff", "#4488ff", "#66ccff"]
            eq_routes:
              - band: bass
                when: dominant
                color_bias: "#ff6600"
                spatial_preset: "flash_floor_only"
                intensity_boost: 0.2
            moods:
              chill:
                palettes: ["warm"]
              groove:
                palettes: ["warm"]
                eq_routes:
                  - band: presence
                    when: lift
                    color_bias: "#66ccff"
                    render_mode: "gradient"
              hype:
                palettes: ["cool"]
              drop:
                palettes: ["cool"]
        """)
        profile = load_profile(_write_yaml(tmp_path, yaml_text))
        assert profile.eq_routes[0].band == "bass"
        assert profile.eq_routes[0].spatial_preset == "flash_floor_only"
        assert profile.moods["groove"].eq_routes[0].band == "presence"
        assert profile.moods["groove"].eq_routes[0].render_mode == "gradient"

    def test_profile_instrument_routes_load(self, tmp_path: Path):
        yaml_text = textwrap.dedent("""\
            name: "Instrument Routed"
            version: 1
            palettes:
              warm: ["#ff4400", "#ff8800", "#ffcc00"]
              cool: ["#2244ff", "#4488ff", "#66ccff"]
            instrument_routes:
              - instrument: vocals
                when: dominant
                color_bias: "#cceeff"
                render_mode: "gradient"
                spatial_zone: "front_center"
                pan_follow: 0.7
                width_scale: 0.4
                confidence_min: 0.55
            moods:
              chill:
                palettes: ["warm"]
              groove:
                palettes: ["warm"]
                instrument_routes:
                  - instrument: drums
                    when: enter
                    spatial_preset: "ripple_from_center"
                    intensity_boost: 0.2
              hype:
                palettes: ["cool"]
              drop:
                palettes: ["cool"]
        """)
        profile = load_profile(_write_yaml(tmp_path, yaml_text))
        assert profile.instrument_routes[0].instrument == "vocals"
        assert profile.instrument_routes[0].pan_follow == 0.7
        assert profile.instrument_routes[0].confidence_min == 0.55
        assert profile.moods["groove"].instrument_routes[0].instrument == "drums"
        assert profile.moods["groove"].instrument_routes[0].when == "enter"


# ---------------------------------------------------------------------------
# load_profile — error paths
# ---------------------------------------------------------------------------


class TestLoadProfileErrors:
    def test_file_not_found(self, tmp_path: Path):
        with pytest.raises(ProfileError, match="not found"):
            load_profile(tmp_path / "nope.yaml")

    def test_not_a_mapping(self, tmp_path: Path):
        p = _write_yaml(tmp_path, "- just a list\n")
        with pytest.raises(ProfileError, match="mapping"):
            load_profile(p)

    def test_missing_name(self, tmp_path: Path):
        yaml_text = textwrap.dedent("""\
            version: 1
            palettes:
              a: ["#ff0000", "#00ff00", "#0000ff"]
            moods:
              chill: {palettes: ["a"]}
              groove: {palettes: ["a"]}
              hype: {palettes: ["a"]}
              drop: {palettes: ["a"]}
        """)
        p = _write_yaml(tmp_path, yaml_text)
        with pytest.raises(ProfileError, match="name"):
            load_profile(p)

    def test_bad_version(self, tmp_path: Path):
        yaml_text = MINIMAL_PROFILE_YAML.replace("version: 1", "version: 2")
        p = _write_yaml(tmp_path, yaml_text)
        with pytest.raises(ProfileError, match="version"):
            load_profile(p)

    def test_palette_too_few_colors(self, tmp_path: Path):
        yaml_text = textwrap.dedent("""\
            name: "Bad"
            version: 1
            palettes:
              tiny: ["#ff0000", "#00ff00"]
            moods:
              chill: {palettes: ["tiny"]}
              groove: {palettes: ["tiny"]}
              hype: {palettes: ["tiny"]}
              drop: {palettes: ["tiny"]}
        """)
        p = _write_yaml(tmp_path, yaml_text)
        with pytest.raises(ProfileError, match="3-8 colors"):
            load_profile(p)

    def test_palette_invalid_hex(self, tmp_path: Path):
        yaml_text = textwrap.dedent("""\
            name: "Bad"
            version: 1
            palettes:
              bad: ["#ff0000", "not-hex", "#0000ff"]
            moods:
              chill: {palettes: ["bad"]}
              groove: {palettes: ["bad"]}
              hype: {palettes: ["bad"]}
              drop: {palettes: ["bad"]}
        """)
        p = _write_yaml(tmp_path, yaml_text)
        with pytest.raises(ProfileError, match="Invalid hex"):
            load_profile(p)

    def test_invalid_mood_key(self, tmp_path: Path):
        yaml_text = textwrap.dedent("""\
            name: "Bad"
            version: 1
            palettes:
              a: ["#ff0000", "#00ff00", "#0000ff"]
            moods:
              chill: {palettes: ["a"]}
              groove: {palettes: ["a"]}
              hype: {palettes: ["a"]}
              drop: {palettes: ["a"]}
              turbo: {palettes: ["a"]}
        """)
        p = _write_yaml(tmp_path, yaml_text)
        with pytest.raises(ProfileError, match="Invalid mood"):
            load_profile(p)

    def test_missing_mood(self, tmp_path: Path):
        yaml_text = textwrap.dedent("""\
            name: "Bad"
            version: 1
            palettes:
              a: ["#ff0000", "#00ff00", "#0000ff"]
            moods:
              chill: {palettes: ["a"]}
              groove: {palettes: ["a"]}
              hype: {palettes: ["a"]}
        """)
        p = _write_yaml(tmp_path, yaml_text)
        with pytest.raises(ProfileError, match="missing"):
            load_profile(p)

    def test_mood_references_unknown_palette(self, tmp_path: Path):
        yaml_text = textwrap.dedent("""\
            name: "Bad"
            version: 1
            palettes:
              a: ["#ff0000", "#00ff00", "#0000ff"]
            moods:
              chill: {palettes: ["nonexistent"]}
              groove: {palettes: ["a"]}
              hype: {palettes: ["a"]}
              drop: {palettes: ["a"]}
        """)
        p = _write_yaml(tmp_path, yaml_text)
        with pytest.raises(ProfileError, match="unknown palette"):
            load_profile(p)

    def test_mood_references_unknown_effect(self, tmp_path: Path):
        yaml_text = textwrap.dedent("""\
            name: "Bad"
            version: 1
            palettes:
              a: ["#ff0000", "#00ff00", "#0000ff"]
            moods:
              chill:
                palettes: ["a"]
                effects:
                  - name: "does_not_exist"
                    weight: 1.0
              groove: {palettes: ["a"]}
              hype: {palettes: ["a"]}
              drop: {palettes: ["a"]}
        """)
        p = _write_yaml(tmp_path, yaml_text)
        with pytest.raises(ProfileError, match="unknown effect"):
            load_profile(p)

    def test_transition_bad_mood(self, tmp_path: Path):
        yaml_text = MINIMAL_PROFILE_YAML + textwrap.dedent("""\
            transitions:
              - from: "chill"
                to: "unknown_mood"
                palette: "test_warm"
        """)
        p = _write_yaml(tmp_path, yaml_text)
        with pytest.raises(ProfileError, match="invalid"):
            load_profile(p)

    def test_transition_bad_palette(self, tmp_path: Path):
        yaml_text = MINIMAL_PROFILE_YAML + textwrap.dedent("""\
            transitions:
              - from: "chill"
                to: "groove"
                palette: "nonexistent_palette"
        """)
        p = _write_yaml(tmp_path, yaml_text)
        with pytest.raises(ProfileError, match="not found"):
            load_profile(p)

    def test_cycle_interval_too_low(self, tmp_path: Path):
        yaml_text = MINIMAL_PROFILE_YAML + "cycle_interval: 0.5\n"
        p = _write_yaml(tmp_path, yaml_text)
        with pytest.raises(ProfileError, match="cycle_interval"):
            load_profile(p)

    def test_eq_route_invalid_band(self, tmp_path: Path):
        yaml_text = textwrap.dedent("""\
            name: "Bad EQ Route"
            version: 1
            palettes:
              p: ["#ff0000", "#00ff00", "#0000ff"]
            eq_routes:
              - band: vocals
            moods:
              chill: {palettes: ["p"]}
              groove: {palettes: ["p"]}
              hype: {palettes: ["p"]}
              drop: {palettes: ["p"]}
        """)
        p = _write_yaml(tmp_path, yaml_text)
        with pytest.raises(ProfileError, match="band"):
            load_profile(p)

    def test_instrument_route_invalid_instrument(self, tmp_path: Path):
        yaml_text = textwrap.dedent("""\
            name: "Bad Instrument Route"
            version: 1
            palettes:
              p: ["#ff0000", "#00ff00", "#0000ff"]
            instrument_routes:
              - instrument: guitar
            moods:
              chill: {palettes: ["p"]}
              groove: {palettes: ["p"]}
              hype: {palettes: ["p"]}
              drop: {palettes: ["p"]}
        """)
        p = _write_yaml(tmp_path, yaml_text)
        with pytest.raises(ProfileError, match="instrument"):
            load_profile(p)


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


class TestResolveProfilePath:
    def test_explicit_path(self, tmp_path: Path):
        p = _write_yaml(tmp_path, MINIMAL_PROFILE_YAML, "my.yaml")
        assert resolve_profile_path(str(p)) == p

    def test_builtin_name(self):
        """If builtin profiles exist, we can resolve by name."""
        # This test is valid once builtins are created (Phase 4).
        # For now, just test that it raises if not found.
        with pytest.raises(ProfileError, match="Could not resolve"):
            resolve_profile_path("definitely_not_a_profile_xyzzy")

    def test_cwd_yaml(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _write_yaml(tmp_path, MINIMAL_PROFILE_YAML, "cool.yaml")
        result = resolve_profile_path("cool")
        assert result.name == "cool.yaml"


# ---------------------------------------------------------------------------
# list_available_profiles
# ---------------------------------------------------------------------------


class TestListAvailableProfiles:
    def test_returns_list(self):
        result = list_available_profiles()
        assert isinstance(result, list)
        # Should have 8 built-in profiles (not counting _template)
        assert len(result) == 8

    def test_all_have_name_and_description(self):
        for info in list_available_profiles():
            assert info["name"]
            assert isinstance(info["description"], str)


class TestBuiltinProfilesLoad:
    """Verify all 8 built-in profiles load without errors and define all 4 moods."""

    EXPECTED_PROFILES = [
        "warm_sunset", "ocean_deep", "midnight_rave", "neon_city",
        "forest_canopy", "aurora", "monochrome", "candy",
    ]

    @pytest.mark.parametrize("profile_name", EXPECTED_PROFILES)
    def test_builtin_loads(self, profile_name: str):
        path = BUILTIN_PROFILES_DIR / f"{profile_name}.yaml"
        assert path.exists(), f"Built-in profile {profile_name}.yaml not found"
        profile = load_profile(path)
        assert profile.name
        assert set(profile.moods.keys()) == {"chill", "groove", "hype", "drop"}
        for mood_cfg in profile.moods.values():
            assert len(mood_cfg.palettes) >= 1


# ---------------------------------------------------------------------------
# suggest_profile
# ---------------------------------------------------------------------------


class TestSuggestProfile:
    def test_morning(self):
        assert suggest_profile(8) == "warm_sunset"

    def test_daytime(self):
        assert suggest_profile(12) == "ocean_deep"

    def test_evening(self):
        assert suggest_profile(18) == "forest_canopy"

    def test_night(self):
        assert suggest_profile(21) == "midnight_rave"

    def test_late_night(self):
        assert suggest_profile(2) == "neon_city"


# ---------------------------------------------------------------------------
# validate_color_harmony
# ---------------------------------------------------------------------------


class TestColorHarmony:
    def test_no_issues(self):
        warnings = validate_color_harmony(["#ff0000", "#00ff00", "#0000ff"])
        assert warnings == []

    def test_similar_adjacent(self):
        warnings = validate_color_harmony(["#ff0000", "#ff0001", "#0000ff"])
        assert any("similar" in w for w in warnings)

    def test_same_brightness(self):
        # All mid-brightness grays
        warnings = validate_color_harmony(["#808080", "#808080", "#808080"])
        assert any("brightness" in w for w in warnings)


# ---------------------------------------------------------------------------
# ProfileRotation
# ---------------------------------------------------------------------------


class TestProfileRotation:
    def _make_profile(self, name: str) -> ProfileConfig:
        return ProfileConfig(
            name=name,
            palettes={"a": ("#ff0000", "#00ff00", "#0000ff")},
            moods={
                mood: MoodProfileConfig(palettes=("a",))
                for mood in ("chill", "groove", "hype", "drop")
            },
        )

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            ProfileRotation(profiles=[])

    def test_initial_current(self):
        p1 = self._make_profile("one")
        pl = ProfileRotation([p1], interval_seconds=10.0)
        assert pl.current.name == "one"

    def test_no_switch_before_interval(self):
        p1 = self._make_profile("one")
        p2 = self._make_profile("two")
        pl = ProfileRotation([p1, p2], interval_seconds=10.0)
        assert pl.update(0.0) is None  # initializes timer
        assert pl.update(5.0) is None  # not time yet

    def test_switch_after_interval(self):
        p1 = self._make_profile("one")
        p2 = self._make_profile("two")
        pl = ProfileRotation([p1, p2], interval_seconds=10.0)
        pl.update(0.0)  # init
        result = pl.update(10.0)
        assert result is not None
        assert result.name == "two"
        assert pl.current.name == "two"

    def test_wraps_around(self):
        p1 = self._make_profile("one")
        p2 = self._make_profile("two")
        pl = ProfileRotation([p1, p2], interval_seconds=10.0)
        pl.update(0.0)
        pl.update(10.0)  # -> two
        result = pl.update(20.0)  # -> one (wrap)
        assert result is not None
        assert result.name == "one"


# ---------------------------------------------------------------------------
# ProfileWatcher
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# EffectCycler integration
# ---------------------------------------------------------------------------


class TestEffectCyclerProfile:
    """Tests for profile-aware EffectCycler behavior."""

    def _load_full_profile(self, tmp_path: Path) -> "ProfileConfig":
        p = _write_yaml(tmp_path, FULL_PROFILE_YAML)
        return load_profile(p)

    def test_profile_palette_used(self, tmp_path: Path):
        from dreamsync.effects import EffectCycler
        from dreamsync.mood import Mood

        profile = self._load_full_profile(tmp_path)
        cycler = EffectCycler(seed=42, profile=profile)

        result = cycler.update(Mood.CHILL, 0.0, False, 120.0, 0.1)
        # Profile chill only allows "aurora_green" palette
        assert cycler.current_palette == "aurora_green"
        # Colors should come from the profile palette, not built-in
        assert result.color_palette == profile.palettes["aurora_green"]

    def test_profile_effect_pool(self, tmp_path: Path):
        from dreamsync.effects import EffectCycler
        from dreamsync.mood import Mood

        profile = self._load_full_profile(tmp_path)
        cycler = EffectCycler(seed=42, profile=profile)

        # Chill effects are wave_drift and slow_breathe in full profile
        effects_seen = set()
        for i in range(50):
            result = cycler.update(Mood.CHILL, float(i) * 20, False, 120.0, 0.1)
            effects_seen.add(result.name)
            cycler._effect_start_t = -1e9  # force re-pick

        assert effects_seen <= {"wave_drift", "slow_breathe"}
        assert len(effects_seen) >= 1

    def test_profile_params_merged(self, tmp_path: Path):
        from dreamsync.effects import EffectCycler
        from dreamsync.mood import Mood

        profile = self._load_full_profile(tmp_path)
        cycler = EffectCycler(seed=42, profile=profile)

        result = cycler.update(Mood.CHILL, 0.0, False, 120.0, 0.1)
        # Full profile sets wave_rate_mult=0.3 for chill
        assert result.params.get("wave_rate_mult") == 0.3

    def test_profile_eq_routes_merged_into_live_preset_params(self, tmp_path: Path):
        from dreamsync.effects import EffectCycler
        from dreamsync.mood import Mood

        yaml_text = """
name: "EQ Live"
version: 1
palettes:
  a: ["#112233", "#445566", "#778899"]
eq_routes:
  - band: "bass"
    when: "dominant"
    color_bias: "#ff6600"
    spatial_preset: "flash_floor_only"
moods:
  chill:
    palettes: ["a"]
    eq_routes:
      - band: "presence"
        when: "lift"
        color_bias: "#66ccff"
        spatial_preset: "flash_top_only"
  groove:
    palettes: ["a"]
  hype:
    palettes: ["a"]
  drop:
    palettes: ["a"]
"""
        profile = load_profile(_write_yaml(tmp_path, yaml_text, "eq-live.yaml"))
        cycler = EffectCycler(seed=42, profile=profile)

        result = cycler.update(Mood.CHILL, 0.0, False, 120.0, 0.1)
        assert [route["band"] for route in result.params["eq_routes"]] == ["bass", "presence"]
        assert result.params["eq_routes"][0]["spatial_preset"] == "flash_floor_only"
        assert result.params["eq_routes"][1]["color_bias"] == "#66ccff"

    def test_profile_instrument_routes_merged_into_live_preset_params(self, tmp_path: Path):
        from dreamsync.effects import EffectCycler
        from dreamsync.mood import Mood

        yaml_text = """
name: "Instrument Live"
version: 1
palettes:
  a: ["#112233", "#445566", "#778899"]
instrument_routes:
  - instrument: "vocals"
    when: "dominant"
    color_bias: "#cceeff"
    pan_follow: 0.7
moods:
  chill:
    palettes: ["a"]
    instrument_routes:
      - instrument: "drums"
        when: "enter"
        spatial_preset: "ripple_from_center"
  groove:
    palettes: ["a"]
  hype:
    palettes: ["a"]
  drop:
    palettes: ["a"]
"""
        profile = load_profile(_write_yaml(tmp_path, yaml_text, "instrument-live.yaml"))
        cycler = EffectCycler(seed=42, profile=profile)

        result = cycler.update(Mood.CHILL, 0.0, False, 120.0, 0.1)
        assert [route["instrument"] for route in result.params["instrument_routes"]] == ["vocals", "drums"]
        assert result.params["instrument_routes"][0]["pan_follow"] == 0.7
        assert result.params["instrument_routes"][1]["spatial_preset"] == "ripple_from_center"

    def test_profile_transition_rule(self, tmp_path: Path):
        from dreamsync.effects import EffectCycler
        from dreamsync.mood import Mood

        profile = self._load_full_profile(tmp_path)
        cycler = EffectCycler(seed=42, profile=profile)

        # Start with chill
        cycler.update(Mood.CHILL, 0.0, False, 120.0, 0.1)
        # Transition chill→groove should force aurora_green palette
        cycler.update(Mood.GROOVE, 5.0, False, 120.0, 0.3)
        assert cycler.current_palette == "aurora_green"

    def test_profile_cycle_interval_override(self, tmp_path: Path):
        from dreamsync.effects import EffectCycler
        from dreamsync.mood import Mood

        profile = self._load_full_profile(tmp_path)
        cycler = EffectCycler(seed=42, profile=profile)
        # Full profile sets cycle_interval=24.0
        assert cycler._cycle_interval == 24.0

        # Should NOT cycle at 20s (within 24s interval)
        cycler.update(Mood.CHILL, 0.0, False, 120.0, 0.1)
        eff1 = cycler.current_effect
        cycler.update(Mood.CHILL, 20.0, False, 120.0, 0.1)
        assert cycler.current_effect == eff1  # same effect, hasn't cycled

        # Should cycle at 25s (past 24s interval)
        cycler.update(Mood.CHILL, 25.0, False, 120.0, 0.1)
        # The effect might be different (or same by chance), but the start time resets
        assert cycler._effect_start_t == 25.0

    def test_set_profile_hot_swap(self, tmp_path: Path):
        from dreamsync.effects import EffectCycler
        from dreamsync.mood import Mood

        profile = self._load_full_profile(tmp_path)
        cycler = EffectCycler(seed=42)

        # Without profile, uses built-in palettes
        cycler.update(Mood.CHILL, 0.0, False, 120.0, 0.1)
        pal_before = cycler.current_palette
        assert pal_before in ("warm", "cool", "pastel", "ice")

        # Hot-swap profile
        cycler.set_profile(profile)

        # Force mood change to trigger re-pick with profile
        cycler.update(Mood.GROOVE, 5.0, False, 120.0, 0.3)
        pal_after = cycler.current_palette
        assert pal_after in ("aurora_green", "aurora_purple")

    def test_no_profile_falls_back_to_builtins(self):
        from dreamsync.effects import EffectCycler
        from dreamsync.mood import Mood

        cycler = EffectCycler(seed=42)
        result = cycler.update(Mood.CHILL, 0.0, False, 120.0, 0.1)
        # Should use built-in palette names
        assert cycler.current_palette in ("warm", "cool", "pastel", "ice")

    def test_drop_mood_with_profile(self, tmp_path: Path):
        from dreamsync.effects import EffectCycler
        from dreamsync.mood import Mood

        profile = self._load_full_profile(tmp_path)
        cycler = EffectCycler(seed=42, profile=profile)

        result = cycler.update(Mood.DROP, 0.0, True, 120.0, 0.9)
        assert result.name == "drop_blast"
        # Drop palette from profile: aurora_vivid
        assert cycler.current_palette == "aurora_vivid"
        assert result.color_palette == profile.palettes["aurora_vivid"]


# ---------------------------------------------------------------------------
# ProfileWatcher
# ---------------------------------------------------------------------------


class TestProfileWatcher:
    def test_detects_change(self, tmp_path: Path):
        p = _write_yaml(tmp_path, MINIMAL_PROFILE_YAML, "watch.yaml")
        callback = MagicMock()

        watcher = ProfileWatcher(p, callback, poll_interval=0.1)
        watcher.start()
        try:
            time.sleep(0.3)
            # Touch the file with new mtime
            p.write_text(MINIMAL_PROFILE_YAML, encoding="utf-8")
            time.sleep(0.5)
        finally:
            watcher.stop()

        assert callback.call_count >= 1
        # The callback receives a ProfileConfig
        first_call_arg = callback.call_args[0][0]
        assert isinstance(first_call_arg, ProfileConfig)
        assert first_call_arg.name == "Test Profile"

    def test_stop_is_clean(self, tmp_path: Path):
        p = _write_yaml(tmp_path, MINIMAL_PROFILE_YAML, "watch2.yaml")
        watcher = ProfileWatcher(p, lambda _: None, poll_interval=0.1)
        watcher.start()
        watcher.stop()
        # Thread should be cleaned up
        assert watcher._thread is None


# ---------------------------------------------------------------------------
# Hot-swap integration tests
# ---------------------------------------------------------------------------

# A second profile with completely different palette colors so we can
# distinguish which profile is active by examining the rendered colors.
PROFILE_A_YAML = textwrap.dedent("""\
    name: "Profile A"
    version: 1
    palettes:
      red_only: ["#ff0000", "#ff0000", "#ff0000"]
    moods:
      chill: {palettes: ["red_only"]}
      groove: {palettes: ["red_only"]}
      hype: {palettes: ["red_only"]}
      drop: {palettes: ["red_only"]}
""")

PROFILE_B_YAML = textwrap.dedent("""\
    name: "Profile B"
    version: 1
    palettes:
      blue_only: ["#0000ff", "#0000ff", "#0000ff"]
    moods:
      chill: {palettes: ["blue_only"]}
      groove: {palettes: ["blue_only"]}
      hype: {palettes: ["blue_only"]}
      drop: {palettes: ["blue_only"]}
""")


class TestHotSwapIntegration:
    """End-to-end hot-swap: file change → ProfileWatcher → set_profile → EffectCycler uses new colors."""

    def test_watcher_drives_cycler_profile_swap(self, tmp_path: Path):
        """Full chain: write Profile A → start watcher+cycler → overwrite with Profile B →
        verify cycler produces Profile B colors on next update."""
        from dreamsync.effects import EffectCycler
        from dreamsync.mood import Mood

        yaml_path = tmp_path / "live.yaml"
        yaml_path.write_text(PROFILE_A_YAML, encoding="utf-8")

        profile_a = load_profile(yaml_path)
        cycler = EffectCycler(seed=42, profile=profile_a)

        # Verify we start with Profile A colors (all red)
        result = cycler.update(Mood.CHILL, 0.0, False, 120.0, 0.1)
        assert all(c == "#ff0000" for c in result.color_palette), \
            f"Expected all-red, got {result.color_palette}"

        # Wire ProfileWatcher → cycler.set_profile
        watcher = ProfileWatcher(yaml_path, cycler.set_profile, poll_interval=0.1)
        watcher.start()
        try:
            time.sleep(0.2)
            # Overwrite with Profile B
            yaml_path.write_text(PROFILE_B_YAML, encoding="utf-8")
            # Wait for watcher to pick up the change
            time.sleep(0.8)
        finally:
            watcher.stop()

        # Force a mood change so cycler re-picks palette from new profile
        result = cycler.update(Mood.GROOVE, 5.0, False, 120.0, 0.3)
        assert all(c == "#0000ff" for c in result.color_palette), \
            f"Expected all-blue after swap, got {result.color_palette}"

    def test_set_profile_takes_effect_immediately(self, tmp_path: Path):
        """set_profile() mid-session: next update (even same mood) picks from new profile.

        set_profile clears _current_mood, so the very next update() re-picks
        palette and effect from the new profile, avoiding stale palette references.
        """
        from dreamsync.effects import EffectCycler
        from dreamsync.mood import Mood

        pa = load_profile(_write_yaml(tmp_path, PROFILE_A_YAML, "a.yaml"))
        pb = load_profile(_write_yaml(tmp_path, PROFILE_B_YAML, "b.yaml"))

        cycler = EffectCycler(seed=42, profile=pa)

        # Establish chill with profile A
        r1 = cycler.update(Mood.CHILL, 0.0, False, 120.0, 0.1)
        assert all(c == "#ff0000" for c in r1.color_palette)

        # Hot-swap to profile B
        cycler.set_profile(pb)

        # Same mood — but set_profile cleared _current_mood, so re-picks from B
        r2 = cycler.update(Mood.CHILL, 1.0, False, 120.0, 0.1)
        assert all(c == "#0000ff" for c in r2.color_palette), \
            f"After set_profile, expected blue immediately, got {r2.color_palette}"

    def test_set_profile_takes_effect_on_cycle_interval(self, tmp_path: Path):
        """set_profile() mid-session: cycle timer expiry picks from new profile."""
        from dreamsync.effects import EffectCycler, EffectCyclerConfig
        from dreamsync.mood import Mood

        pa = load_profile(_write_yaml(tmp_path, PROFILE_A_YAML, "a.yaml"))
        pb = load_profile(_write_yaml(tmp_path, PROFILE_B_YAML, "b.yaml"))

        cycler = EffectCycler(
            config=EffectCyclerConfig(cycle_interval=10.0), seed=42, profile=pa,
        )

        # Establish chill with profile A
        cycler.update(Mood.CHILL, 0.0, False, 120.0, 0.1)

        # Hot-swap to profile B
        cycler.set_profile(pb)

        # Advance past cycle interval → forces re-pick
        r = cycler.update(Mood.CHILL, 11.0, False, 120.0, 0.1)
        assert all(c == "#0000ff" for c in r.color_palette), \
            f"After swap + cycle expiry, expected blue, got {r.color_palette}"

    def test_set_profile_to_none_restores_builtins(self, tmp_path: Path):
        """Swapping profile to None should fall back to built-in palettes."""
        from dreamsync.effects import EffectCycler
        from dreamsync.mood import Mood

        pa = load_profile(_write_yaml(tmp_path, PROFILE_A_YAML, "a.yaml"))
        cycler = EffectCycler(seed=42, profile=pa)

        cycler.update(Mood.CHILL, 0.0, False, 120.0, 0.1)
        assert all(c == "#ff0000" for c in cycler.update(Mood.CHILL, 0.5, False, 120.0, 0.1).color_palette)

        # Remove profile
        cycler.set_profile(None)
        r = cycler.update(Mood.GROOVE, 5.0, False, 120.0, 0.3)
        # Should use a built-in palette now (not all-red)
        assert cycler.current_palette in ("vivid", "sunset", "neon", "warm")

    def test_rotation_swaps_profile_on_cycler(self, tmp_path: Path):
        """ProfileRotation triggers set_profile → cycler uses new colors."""
        from dreamsync.effects import EffectCycler
        from dreamsync.mood import Mood

        pa = load_profile(_write_yaml(tmp_path, PROFILE_A_YAML, "a.yaml"))
        pb = load_profile(_write_yaml(tmp_path, PROFILE_B_YAML, "b.yaml"))

        rotation = ProfileRotation([pa, pb], interval_seconds=10.0)
        cycler = EffectCycler(seed=42, profile=rotation.current)

        # Start at t=0 with profile A
        rotation.update(0.0)
        r1 = cycler.update(Mood.CHILL, 0.0, False, 120.0, 0.1)
        assert all(c == "#ff0000" for c in r1.color_palette)

        # Rotate at t=10 → profile B
        new = rotation.update(10.0)
        assert new is not None
        assert new.name == "Profile B"
        cycler.set_profile(new)

        r2 = cycler.update(Mood.GROOVE, 10.5, False, 120.0, 0.3)
        assert all(c == "#0000ff" for c in r2.color_palette), \
            f"After rotation, expected blue, got {r2.color_palette}"

    def test_watcher_ignores_invalid_yaml(self, tmp_path: Path):
        """If the profile YAML becomes invalid during hot-swap, the old profile stays active."""
        from dreamsync.effects import EffectCycler
        from dreamsync.mood import Mood

        yaml_path = tmp_path / "fragile.yaml"
        yaml_path.write_text(PROFILE_A_YAML, encoding="utf-8")

        profile_a = load_profile(yaml_path)
        cycler = EffectCycler(seed=42, profile=profile_a)
        cycler.update(Mood.CHILL, 0.0, False, 120.0, 0.1)

        watcher = ProfileWatcher(yaml_path, cycler.set_profile, poll_interval=0.1)
        watcher.start()
        try:
            time.sleep(0.2)
            # Write invalid YAML
            yaml_path.write_text("not: valid: yaml: [[[", encoding="utf-8")
            time.sleep(0.8)
        finally:
            watcher.stop()

        # Cycler should still have profile A (invalid reload was caught and logged)
        r = cycler.update(Mood.GROOVE, 5.0, False, 120.0, 0.3)
        assert all(c == "#ff0000" for c in r.color_palette), \
            f"After invalid YAML, should keep old profile, got {r.color_palette}"

    def test_rapid_successive_swaps(self, tmp_path: Path):
        """Multiple rapid set_profile calls: last one wins."""
        from dreamsync.effects import EffectCycler
        from dreamsync.mood import Mood

        pa = load_profile(_write_yaml(tmp_path, PROFILE_A_YAML, "a.yaml"))

        # Profile C: all-green
        profile_c_yaml = textwrap.dedent("""\
            name: "Profile C"
            version: 1
            palettes:
              green_only: ["#00ff00", "#00ff00", "#00ff00"]
            moods:
              chill: {palettes: ["green_only"]}
              groove: {palettes: ["green_only"]}
              hype: {palettes: ["green_only"]}
              drop: {palettes: ["green_only"]}
        """)
        pb = load_profile(_write_yaml(tmp_path, PROFILE_B_YAML, "b.yaml"))
        pc = load_profile(_write_yaml(tmp_path, profile_c_yaml, "c.yaml"))

        cycler = EffectCycler(seed=42, profile=pa)
        cycler.update(Mood.CHILL, 0.0, False, 120.0, 0.1)

        # Rapid swaps: A → B → C
        cycler.set_profile(pb)
        cycler.set_profile(pc)

        r = cycler.update(Mood.GROOVE, 5.0, False, 120.0, 0.3)
        assert all(c == "#00ff00" for c in r.color_palette), \
            f"Last profile (C/green) should win, got {r.color_palette}"
