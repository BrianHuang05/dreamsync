from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

from dreamsync.director import LightingIntent


class RenderMode(str, Enum):
    SOLID = "solid"
    PULSE = "pulse"
    SCROLL = "scroll"
    BREATHE = "breathe"


def _parse_hex(color: str) -> tuple[int, int, int]:
    h = color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


_DEFAULT_COLOR = (255, 180, 100)  # warm white


@dataclass
class SegmentRenderer:
    """Converts LightingIntent into per-segment RGB frame buffers."""

    segments: int
    mode: RenderMode = RenderMode.SOLID
    mirror: bool = True

    # Pulse state
    _pulse_brightness: float = field(default=0.0, init=False, repr=False)
    _pulse_decay: float = field(default=6.0, init=False, repr=False)

    # Scroll state — half-buffer (center-to-edge), mirrored on output
    _scroll_buf: list[tuple[float, float, float]] = field(
        default_factory=list, init=False, repr=False
    )
    _scroll_pos: float = field(default=0.0, init=False, repr=False)

    # Breathe state
    _breathe_phase: float = field(default=0.0, init=False, repr=False)

    # Tracking
    _last_t: float = field(default=0.0, init=False, repr=False)

    def __post_init__(self) -> None:
        half = (self.segments + 1) // 2
        self._scroll_buf = [(0.0, 0.0, 0.0)] * half

    def render(
        self, t: float, intent: LightingIntent, beat: bool = False
    ) -> list[tuple[int, int, int]]:
        """Produce one frame of RGB segment colors."""
        dt = max(0.0, t - self._last_t) if self._last_t > 0 else 0.0
        self._last_t = t

        if self.mode == RenderMode.SOLID:
            return self._render_solid(intent)
        elif self.mode == RenderMode.PULSE:
            return self._render_pulse(intent, dt, beat)
        elif self.mode == RenderMode.BREATHE:
            return self._render_breathe(intent, dt)
        elif self.mode == RenderMode.SCROLL:
            return self._render_scroll(intent, dt, beat)
        return self._render_solid(intent)

    # -- Solid ---------------------------------------------------------------

    def _render_solid(self, intent: LightingIntent) -> list[tuple[int, int, int]]:
        r, g, b = _parse_hex(intent.color) if intent.color else _DEFAULT_COLOR
        factor = max(0.0, min(1.0, intent.intensity))
        pixel = (int(r * factor), int(g * factor), int(b * factor))
        return [pixel] * self.segments

    # -- Pulse ---------------------------------------------------------------

    def _render_pulse(
        self, intent: LightingIntent, dt: float, beat: bool
    ) -> list[tuple[int, int, int]]:
        if beat:
            self._pulse_brightness = 1.0
        else:
            self._pulse_brightness *= math.exp(-self._pulse_decay * dt)

        r, g, b = _parse_hex(intent.color) if intent.color else _DEFAULT_COLOR
        level = self._pulse_brightness * max(0.0, min(1.0, intent.intensity))
        pixel = (int(r * level), int(g * level), int(b * level))
        return [pixel] * self.segments

    # -- Breathe -------------------------------------------------------------

    def _render_breathe(
        self, intent: LightingIntent, dt: float
    ) -> list[tuple[int, int, int]]:
        bpm = max(1.0, intent.bpm)
        freq = bpm / 60.0  # cycles per second
        self._breathe_phase += freq * dt
        # Sine wave 0→1→0
        wave = (math.sin(2.0 * math.pi * self._breathe_phase) + 1.0) / 2.0

        r, g, b = _parse_hex(intent.color) if intent.color else _DEFAULT_COLOR
        level = wave * max(0.0, min(1.0, intent.intensity))
        pixel = (int(r * level), int(g * level), int(b * level))
        return [pixel] * self.segments

    # -- Scroll --------------------------------------------------------------

    def _render_scroll(
        self, intent: LightingIntent, dt: float, beat: bool
    ) -> list[tuple[int, int, int]]:
        half = len(self._scroll_buf)
        bpm = max(1.0, intent.bpm)
        # Scroll speed: pixels per second, scaled by BPM
        pixels_per_sec = (bpm / 60.0) * max(0.1, intent.speed) * half
        self._scroll_pos += pixels_per_sec * dt

        # Shift buffer outward by whole pixels
        shift = int(self._scroll_pos)
        if shift > 0:
            self._scroll_pos -= shift
            for _ in range(shift):
                # Shift everything one position outward (toward edges)
                self._scroll_buf = [(0.0, 0.0, 0.0)] + self._scroll_buf[:-1]

        # Inject new color at center on beat — fill a wider band so the
        # pattern is visible on large segment counts.
        if beat:
            color = _parse_hex(intent.color) if intent.color else _DEFAULT_COLOR
            cf = (float(color[0]), float(color[1]), float(color[2]))
            # Inject center + neighbors: ~20% of half-buffer, minimum 2 pixels
            inject_width = max(2, half // 5)
            for i in range(min(inject_width, half)):
                self._scroll_buf[i] = cf

        # Fade: gentle distance-based attenuation so outer pixels stay visible
        # longer.  Fade increases with distance from center so nearby pixels
        # barely dim while edges gradually darken.
        base_fade_rate = 0.4
        faded: list[tuple[float, float, float]] = []
        for i, (rf, gf, bf) in enumerate(self._scroll_buf):
            if i == 0:
                fade = 1.0  # center never fades
            else:
                dist = i / max(1, half - 1)  # 0..1
                rate = base_fade_rate * (0.3 + 0.7 * dist)
                fade = math.exp(-rate * dt)
            faded.append((rf * fade, gf * fade, bf * fade))
        self._scroll_buf = faded

        # Apply intensity
        intensity = max(0.0, min(1.0, intent.intensity))

        # Build full strip from half-buffer
        if self.mirror:
            # Center-outward: mirror the half-buffer
            left = list(reversed(self._scroll_buf))
            right = self._scroll_buf[:]
            if self.segments % 2 == 0:
                full = left + right
            else:
                full = left + right[1:]  # avoid duplicating center
            # Trim/pad to exact segment count
            full = full[:self.segments]
            while len(full) < self.segments:
                full.append((0.0, 0.0, 0.0))
        else:
            # Left-to-right: use scroll_buf directly, pad if needed
            full = self._scroll_buf[:self.segments]
            while len(full) < self.segments:
                full.append((0.0, 0.0, 0.0))

        return [
            (
                int(max(0, min(255, r * intensity))),
                int(max(0, min(255, g * intensity))),
                int(max(0, min(255, b * intensity))),
            )
            for r, g, b in full
        ]
