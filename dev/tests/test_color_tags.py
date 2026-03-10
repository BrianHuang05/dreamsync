"""Tests for ROYGBIVW color classification and profile tag generation."""

from __future__ import annotations

import pytest

from dreamsync.color_utils import (
    HUE_BUCKETS,
    classify_palette_colors,
    hsl_to_hex,
    hue_to_color_name,
)
from dreamsync.profile_generator import (
    GeneratorParams,
    compute_profile_tags,
    generate_profile,
    generate_profile_set,
)


# ---------------------------------------------------------------------------
# Deliverable 1A: hue_to_color_name
# ---------------------------------------------------------------------------

class TestHueToColorName:
    def test_red_at_zero(self):
        assert hue_to_color_name(0.0) == "R"

    def test_red_wrap_at_350(self):
        assert hue_to_color_name(350.0) == "R"

    def test_red_wrap_at_355(self):
        assert hue_to_color_name(355.0) == "R"

    def test_orange(self):
        assert hue_to_color_name(30.0) == "O"

    def test_yellow(self):
        assert hue_to_color_name(55.0) == "Y"

    def test_green(self):
        assert hue_to_color_name(120.0) == "G"

    def test_blue(self):
        assert hue_to_color_name(220.0) == "B"

    def test_indigo(self):
        assert hue_to_color_name(270.0) == "I"

    def test_violet(self):
        assert hue_to_color_name(300.0) == "V"

    def test_achromatic_returns_w(self):
        assert hue_to_color_name(120.0, saturation=0.05) == "W"

    def test_chromatic_with_saturation(self):
        assert hue_to_color_name(120.0, saturation=0.5) == "G"

    def test_boundary_orange_yellow(self):
        assert hue_to_color_name(45.0) == "Y"  # 45 is start of yellow

    def test_boundary_green_blue(self):
        assert hue_to_color_name(165.0) == "B"  # 165 is start of blue

    def test_hue_360_wraps_to_red(self):
        assert hue_to_color_name(360.0) == "R"

    def test_hue_over_360_wraps(self):
        assert hue_to_color_name(390.0) == "O"  # 390 % 360 = 30


# ---------------------------------------------------------------------------
# Deliverable 1A: classify_palette_colors
# ---------------------------------------------------------------------------

class TestClassifyPaletteColors:
    def test_monochromatic_red(self):
        reds = tuple(hsl_to_hex(h, 0.8, 0.5) for h in [0, 5, 10, 350, 355, 3])
        primary, secondary = classify_palette_colors(reds)
        assert primary == "R"
        assert secondary == "R"

    def test_complementary_red_blue(self):
        colors = (
            hsl_to_hex(0, 0.8, 0.5),
            hsl_to_hex(5, 0.8, 0.5),
            hsl_to_hex(10, 0.8, 0.5),
            hsl_to_hex(200, 0.8, 0.5),
            hsl_to_hex(210, 0.8, 0.5),
            hsl_to_hex(220, 0.8, 0.5),
        )
        primary, secondary = classify_palette_colors(colors)
        assert {primary, secondary} == {"R", "B"}

    def test_triadic_mixed(self):
        colors = (
            hsl_to_hex(0, 0.8, 0.5),    # R
            hsl_to_hex(5, 0.8, 0.5),    # R
            hsl_to_hex(120, 0.8, 0.5),  # G
            hsl_to_hex(240, 0.8, 0.5),  # B
        )
        primary, secondary = classify_palette_colors(colors)
        assert primary == "R"  # most frequent
        assert secondary in ("G", "B")

    def test_all_achromatic(self):
        grays = tuple(hsl_to_hex(0, 0.02, l) for l in [0.3, 0.4, 0.5, 0.6])
        primary, secondary = classify_palette_colors(grays)
        assert primary == "W"
        assert secondary == "W"

    def test_empty_palette(self):
        assert classify_palette_colors(()) == ("W", "W")

    def test_mixed_achromatic_and_chromatic(self):
        colors = (
            hsl_to_hex(0, 0.02, 0.5),   # achromatic -> W
            hsl_to_hex(120, 0.8, 0.5),  # G
            hsl_to_hex(130, 0.8, 0.5),  # G
        )
        primary, secondary = classify_palette_colors(colors)
        assert primary == "G"


# ---------------------------------------------------------------------------
# Deliverable 1B: compute_profile_tags
# ---------------------------------------------------------------------------

class TestComputeProfileTags:
    def _make_params(self, **kwargs):
        defaults = dict(
            base_hue=0.0,
            temperature="warm",
            saturation="vivid",
            harmony="complementary",
        )
        defaults.update(kwargs)
        return GeneratorParams(**defaults)

    def test_tags_contain_primary(self):
        p = generate_profile(self._make_params(base_hue=0.0))
        assert any(t.startswith("primary:") for t in p.tags)

    def test_tags_contain_secondary(self):
        p = generate_profile(self._make_params(base_hue=0.0))
        assert any(t.startswith("secondary:") for t in p.tags)

    def test_tags_contain_temperature(self):
        p = generate_profile(self._make_params(temperature="cool"))
        assert "cool" in p.tags

    def test_tags_contain_intensity(self):
        p = generate_profile(self._make_params(saturation="muted"))
        assert "muted" in p.tags

    def test_tags_contain_harmony(self):
        p = generate_profile(self._make_params(harmony="triadic"))
        assert "triadic" in p.tags

    def test_tags_contain_generated(self):
        p = generate_profile(self._make_params())
        assert "generated" in p.tags

    def test_tags_deterministic(self):
        params = self._make_params(base_hue=120.0, harmony="triadic")
        t1 = generate_profile(params).tags
        t2 = generate_profile(params).tags
        assert t1 == t2

    def test_profile_set_tags_vary(self):
        """A 12-profile set should use at least 4 distinct primary colors."""
        profiles = generate_profile_set(12, seed=42)
        primaries = set()
        for p in profiles:
            for t in p.tags:
                if t.startswith("primary:"):
                    primaries.add(t)
        assert len(primaries) >= 4

    def test_compute_tags_directly(self):
        params = self._make_params(base_hue=220.0, temperature="cool", saturation="vivid", harmony="complementary")
        palettes = {"energy": tuple(hsl_to_hex(220, 0.9, 0.5) for _ in range(6))}
        tags = compute_profile_tags(params, palettes)
        assert "generated" in tags
        assert "cool" in tags
        assert "vivid" in tags
        assert "complementary" in tags
        assert any(t.startswith("primary:") for t in tags)

    def test_all_six_tag_categories(self):
        """Every generated profile has all 6 tag categories."""
        p = generate_profile(self._make_params())
        tags = p.tags
        assert "generated" in tags
        assert any(t.startswith("primary:") for t in tags)
        assert any(t.startswith("secondary:") for t in tags)
        assert any(t in ("warm", "cool", "neutral") for t in tags)
        assert any(t in ("muted", "medium", "vivid") for t in tags)
        assert any(t in ("complementary", "triadic", "analogous", "split_complementary", "tetradic") for t in tags)
