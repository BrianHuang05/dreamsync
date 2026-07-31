"""HSL color utilities for procedural palette generation and cross-fade blending.

All functions operate on hex strings (#rrggbb), the format used throughout the codebase.
Pure stdlib — uses only colorsys for conversions.
"""

from __future__ import annotations

import colorsys
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Conversions
# ---------------------------------------------------------------------------

def hex_to_rgb(color: str) -> tuple[int, int, int]:
    """'#ff4400' -> (255, 68, 0)"""
    c = color.lstrip("#")
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))


def rgb_to_hex(r: int, g: int, b: int) -> str:
    """(255, 68, 0) -> '#ff4400'"""
    return f"#{r:02x}{g:02x}{b:02x}"


def nearest_palette_color(color: str, palette: tuple[str, ...]) -> str:
    """Return the palette member nearest to *color* in RGB space."""
    if not palette:
        return color
    for candidate in palette:
        if candidate.casefold() == color.casefold():
            return candidate
    try:
        target = hex_to_rgb(color)
    except (ValueError, IndexError):
        return color

    valid_candidates: list[tuple[str, tuple[int, int, int]]] = []
    for candidate in palette:
        try:
            valid_candidates.append((candidate, hex_to_rgb(candidate)))
        except (ValueError, IndexError):
            continue
    if not valid_candidates:
        return color
    return min(
        valid_candidates,
        key=lambda item: sum(
            (channel - target_channel) ** 2
            for channel, target_channel in zip(item[1], target)
        ),
    )[0]


def hex_to_hsl(color: str) -> tuple[float, float, float]:
    """'#ff4400' -> (h: 0-360, s: 0-1, l: 0-1)"""
    r, g, b = hex_to_rgb(color)
    r_f, g_f, b_f = r / 255.0, g / 255.0, b / 255.0
    # colorsys.rgb_to_hls returns (h 0-1, l 0-1, s 0-1)
    h, l, s = colorsys.rgb_to_hls(r_f, g_f, b_f)
    return (h * 360.0, s, l)


def hsl_to_hex(h: float, s: float, l: float) -> str:
    """(16.0, 1.0, 0.5) -> '#ff4400'"""
    h_norm = (h % 360.0) / 360.0
    # colorsys.hls_to_rgb takes (h 0-1, l 0-1, s 0-1)
    r_f, g_f, b_f = colorsys.hls_to_rgb(h_norm, l, s)
    r = max(0, min(255, round(r_f * 255)))
    g = max(0, min(255, round(g_f * 255)))
    b = max(0, min(255, round(b_f * 255)))
    return rgb_to_hex(r, g, b)


def hex_to_hsv(color: str) -> tuple[float, float, float]:
    """'#ff4400' -> (h: 0-360, s: 0-1, v: 0-1)."""
    r, g, b = hex_to_rgb(color)
    r_f, g_f, b_f = r / 255.0, g / 255.0, b / 255.0
    h, s, v = colorsys.rgb_to_hsv(r_f, g_f, b_f)
    return (h * 360.0, s, v)


def hsv_to_hex(h: float, s: float, v: float) -> str:
    """(16.0, 1.0, 1.0) -> '#ff4400'."""
    h_norm = (h % 360.0) / 360.0
    r_f, g_f, b_f = colorsys.hsv_to_rgb(h_norm, s, v)
    return rgb_to_hex(
        max(0, min(255, round(r_f * 255))),
        max(0, min(255, round(g_f * 255))),
        max(0, min(255, round(b_f * 255))),
    )


# ---------------------------------------------------------------------------
# Interpolation
# ---------------------------------------------------------------------------

def interpolate_hex(a: str, b: str, t: float) -> str:
    """Linear RGB interpolation. t=0 -> a, t=1 -> b."""
    ra, ga, ba = hex_to_rgb(a)
    rb, gb, bb = hex_to_rgb(b)
    r = max(0, min(255, round(ra + (rb - ra) * t)))
    g = max(0, min(255, round(ga + (gb - ga) * t)))
    bl = max(0, min(255, round(ba + (bb - ba) * t)))
    return rgb_to_hex(r, g, bl)


def _shortest_hue_interp(h_a: float, h_b: float, t: float) -> float:
    """Interpolate hues via shortest arc on the 360 circle."""
    diff = (h_b - h_a) % 360.0
    if diff > 180.0:
        diff -= 360.0
    result = (h_a + diff * t) % 360.0
    return result


def interpolate_hex_hsl(a: str, b: str, t: float) -> str:
    """HSL interpolation (shortest hue arc). Better for perceptual blending."""
    h_a, s_a, l_a = hex_to_hsl(a)
    h_b, s_b, l_b = hex_to_hsl(b)
    h = _shortest_hue_interp(h_a, h_b, t)
    s = s_a + (s_b - s_a) * t
    l = l_a + (l_b - l_a) * t
    return hsl_to_hex(h, s, l)


# ---------------------------------------------------------------------------
# Harmony generators
# ---------------------------------------------------------------------------

def complementary(base_hue: float) -> list[float]:
    """[base, base+180]"""
    return [base_hue % 360.0, (base_hue + 180.0) % 360.0]


def triadic(base_hue: float) -> list[float]:
    """[base, base+120, base+240]"""
    return [(base_hue + i * 120.0) % 360.0 for i in range(3)]


def analogous(base_hue: float, spread: float = 30.0) -> list[float]:
    """[base-spread, base, base+spread]"""
    return [
        (base_hue - spread) % 360.0,
        base_hue % 360.0,
        (base_hue + spread) % 360.0,
    ]


def split_complementary(base_hue: float) -> list[float]:
    """[base, base+150, base+210]"""
    return [
        base_hue % 360.0,
        (base_hue + 150.0) % 360.0,
        (base_hue + 210.0) % 360.0,
    ]


def tetradic(base_hue: float) -> list[float]:
    """[base, base+90, base+180, base+270]"""
    return [(base_hue + i * 90.0) % 360.0 for i in range(4)]


# ---------------------------------------------------------------------------
# Palette generation
# ---------------------------------------------------------------------------

def generate_palette(
    anchor_hues: list[float],
    saturation: float,
    lightness: float,
    count: int = 6,
    variation: float = 0.1,
) -> tuple[str, ...]:
    """Generate `count` hex colors distributed around anchor hues with S/L variation.

    Distributes colors evenly across anchor hues. Each color gets a slight
    randomized offset in saturation and lightness (deterministic when called
    with consistent inputs).
    """
    colors: list[str] = []
    n_anchors = len(anchor_hues)
    for i in range(count):
        anchor_idx = i % n_anchors
        hue = anchor_hues[anchor_idx]
        # Offset hue slightly for variety within same anchor
        hue_offset = (i / count) * (360.0 / max(n_anchors, 1)) * 0.15
        h = (hue + hue_offset) % 360.0
        # Vary S/L slightly per color
        phase = i / max(count - 1, 1)
        s = max(0.0, min(1.0, saturation + variation * math.sin(phase * math.pi)))
        l = max(0.0, min(1.0, lightness + variation * 0.5 * math.cos(phase * math.pi)))
        colors.append(hsl_to_hex(h, s, l))
    return tuple(colors)


# ---------------------------------------------------------------------------
# ROYGBIVW color classification
# ---------------------------------------------------------------------------

# Perceptual hue boundaries for ROYGBIVW classification
HUE_BUCKETS: list[tuple[str, float, float]] = [
    ("R", 345.0, 15.0),   # Red: 345-360, 0-15 (wraps)
    ("O", 15.0, 45.0),    # Orange: 15-45
    ("Y", 45.0, 70.0),    # Yellow: 45-70
    ("G", 70.0, 165.0),   # Green: 70-165
    ("B", 165.0, 255.0),  # Blue: 165-255
    ("I", 255.0, 285.0),  # Indigo: 255-285
    ("V", 285.0, 345.0),  # Violet: 285-345
]


def hue_to_color_name(hue: float, saturation: float | None = None) -> str:
    """Map a hue (0-360) to a ROYGBIVW letter.

    Achromatic colors (saturation < 0.1) return 'W' (white/neutral).
    """
    if saturation is not None and saturation < 0.1:
        return "W"
    hue = hue % 360.0
    for letter, lo, hi in HUE_BUCKETS:
        if letter == "R":
            # Red wraps around 0
            if hue >= lo or hue < hi:
                return letter
        else:
            if lo <= hue < hi:
                return letter
    return "R"  # fallback (shouldn't happen)


def classify_palette_colors(colors: tuple[str, ...]) -> tuple[str, str]:
    """Return (primary, secondary) ROYGBIVW letters for a palette.

    Primary = most frequent hue bucket across all colors.
    Secondary = second most frequent (or same as primary if monochromatic).
    Achromatic colors (very low saturation) are excluded from counting
    unless all colors are achromatic, in which case both return 'W'.
    """
    from collections import Counter

    counts: Counter[str] = Counter()
    for c in colors:
        h, s, _l = hex_to_hsl(c)
        letter = hue_to_color_name(h, s)
        counts[letter] += 1

    if not counts:
        return ("W", "W")

    # If all achromatic, return W
    non_w = {k: v for k, v in counts.items() if k != "W"}
    if not non_w:
        return ("W", "W")

    ranked = sorted(non_w.items(), key=lambda kv: kv[1], reverse=True)
    primary = ranked[0][0]
    secondary = ranked[1][0] if len(ranked) > 1 else primary
    return (primary, secondary)


# ---------------------------------------------------------------------------
# Distance
# ---------------------------------------------------------------------------

def _rgb_centroid(palette: tuple[str, ...]) -> tuple[float, float, float]:
    """Average RGB values of a palette."""
    if not palette:
        return (0.0, 0.0, 0.0)
    r_sum = g_sum = b_sum = 0.0
    for c in palette:
        r, g, b = hex_to_rgb(c)
        r_sum += r
        g_sum += g
        b_sum += b
    n = len(palette)
    return (r_sum / n, g_sum / n, b_sum / n)


def palette_distance(a: tuple[str, ...], b: tuple[str, ...]) -> float:
    """Mean Euclidean distance in RGB space between two palettes' centroids."""
    ca = _rgb_centroid(a)
    cb = _rgb_centroid(b)
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(ca, cb)))


def profile_color_distance(a: object, b: object) -> float:
    """Average palette_distance across all palettes in two ProfileConfig objects."""
    a_palettes = getattr(a, "palettes", {})
    b_palettes = getattr(b, "palettes", {})
    if not a_palettes or not b_palettes:
        return 0.0
    # Compare all pairs by sorted palette names
    a_vals = [v for _, v in sorted(a_palettes.items())]
    b_vals = [v for _, v in sorted(b_palettes.items())]
    # Pair up by index, cycling shorter list
    n = max(len(a_vals), len(b_vals))
    total = 0.0
    for i in range(n):
        pa = a_vals[i % len(a_vals)]
        pb = b_vals[i % len(b_vals)]
        total += palette_distance(pa, pb)
    return total / n
