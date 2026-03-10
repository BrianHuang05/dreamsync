"""Tests for color_utils — HSL conversions, interpolation, harmony, palette distance."""

from __future__ import annotations

import re

import pytest

from dreamsync.color_utils import (
    analogous,
    complementary,
    generate_palette,
    hex_to_hsl,
    hex_to_rgb,
    hsl_to_hex,
    interpolate_hex,
    interpolate_hex_hsl,
    palette_distance,
    profile_color_distance,
    rgb_to_hex,
    triadic,
)

HEX_RE = re.compile(r"^#[0-9a-f]{6}$")


# --- Conversion round-trips ---


@pytest.mark.parametrize(
    "color",
    ["#ff0000", "#00ff00", "#0000ff", "#000000", "#ffffff", "#ff4400", "#1a2b3c"],
)
def test_hex_rgb_roundtrip(color: str) -> None:
    r, g, b = hex_to_rgb(color)
    assert rgb_to_hex(r, g, b) == color


@pytest.mark.parametrize(
    "color",
    ["#ff0000", "#00ff00", "#0000ff", "#ff4400", "#808080", "#1a2b3c"],
)
def test_hex_hsl_roundtrip(color: str) -> None:
    h, s, l = hex_to_hsl(color)
    result = hsl_to_hex(h, s, l)
    # Allow +-1 per channel due to floating point
    ro, go, bo = hex_to_rgb(color)
    rr, gr, br = hex_to_rgb(result)
    assert abs(ro - rr) <= 1
    assert abs(go - gr) <= 1
    assert abs(bo - br) <= 1


# --- Interpolation ---


def test_interpolate_hex_endpoints() -> None:
    a, b = "#ff0000", "#0000ff"
    assert interpolate_hex(a, b, 0.0) == a
    assert interpolate_hex(a, b, 1.0) == b


def test_interpolate_hex_midpoint() -> None:
    result = interpolate_hex("#ff0000", "#0000ff", 0.5)
    r, g, b = hex_to_rgb(result)
    # Midpoint of red and blue in RGB → ~(128, 0, 128) = purple-ish
    assert 126 <= r <= 130
    assert g == 0
    assert 126 <= b <= 130


def test_interpolate_hsl_shortest_arc() -> None:
    """Hue 350 -> 10 should go through 0, not through 180."""
    a = hsl_to_hex(350.0, 1.0, 0.5)
    b = hsl_to_hex(10.0, 1.0, 0.5)
    mid = interpolate_hex_hsl(a, b, 0.5)
    h_mid, _, _ = hex_to_hsl(mid)
    # Midpoint should be near 0/360, not near 180
    assert h_mid < 20 or h_mid > 340


# --- Harmony generators ---


def test_complementary_hues() -> None:
    result = complementary(0)
    assert len(result) == 2
    assert result[0] == pytest.approx(0.0)
    assert result[1] == pytest.approx(180.0)


def test_triadic_hues() -> None:
    result = triadic(0)
    assert len(result) == 3
    assert result == [pytest.approx(0.0), pytest.approx(120.0), pytest.approx(240.0)]


def test_analogous_hues() -> None:
    result = analogous(180.0, 30.0)
    assert len(result) == 3
    assert result[0] == pytest.approx(150.0)
    assert result[1] == pytest.approx(180.0)
    assert result[2] == pytest.approx(210.0)


# --- Palette generation ---


def test_generate_palette_count() -> None:
    hues = triadic(120.0)
    pal = generate_palette(hues, 0.8, 0.5, count=6)
    assert len(pal) == 6


def test_generate_palette_valid_hex() -> None:
    hues = complementary(60.0)
    pal = generate_palette(hues, 0.7, 0.5, count=8)
    for c in pal:
        assert HEX_RE.match(c), f"Invalid hex: {c}"


# --- Distance ---


def test_palette_distance_identical() -> None:
    pal = ("#ff0000", "#00ff00", "#0000ff")
    assert palette_distance(pal, pal) == pytest.approx(0.0)


def test_palette_distance_different() -> None:
    red_pal = ("#ff0000", "#ff4400", "#ff8800")
    blue_pal = ("#0000ff", "#0044ff", "#0088ff")
    dist = palette_distance(red_pal, blue_pal)
    assert dist > 200


def test_profile_color_distance_symmetric() -> None:
    class FakeProfile:
        def __init__(self, palettes: dict) -> None:
            self.palettes = palettes

    a = FakeProfile({"p1": ("#ff0000", "#ff4400"), "p2": ("#00ff00", "#00ff44")})
    b = FakeProfile({"p1": ("#0000ff", "#0044ff"), "p2": ("#ff00ff", "#ff44ff")})
    assert profile_color_distance(a, b) == pytest.approx(profile_color_distance(b, a))
