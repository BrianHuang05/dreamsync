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
    WAVE = "wave"
    GRADIENT = "gradient"


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
    device_type: str | None = None
    pulse_decay_override: float | None = None

    # Pulse state
    _pulse_brightness: float = field(default=0.0, init=False, repr=False)
    _pulse_decay: float = field(default=4.0, init=False, repr=False)

    # Scroll state — half-buffer (center-to-edge), mirrored on output
    _scroll_buf: list[tuple[float, float, float]] = field(
        default_factory=list, init=False, repr=False
    )
    _scroll_pos: float = field(default=0.0, init=False, repr=False)

    # Breathe state
    _breathe_phase: float = field(default=0.0, init=False, repr=False)

    # Wave state
    _wave_phase: float = field(default=0.0, init=False, repr=False)

    # Gradient state
    _gradient_offset: float = field(default=0.0, init=False, repr=False)

    # Tracking
    _last_t: float = field(default=0.0, init=False, repr=False)

    def __post_init__(self) -> None:
        half = (self.segments + 1) // 2
        self._scroll_buf = [(0.0, 0.0, 0.0)] * half
        if self.pulse_decay_override is not None:
            self._pulse_decay = self.pulse_decay_override

    def render(
        self, t: float, intent: LightingIntent, beat: bool = False,
        params: dict | None = None,
    ) -> list[tuple[int, int, int]]:
        """Produce one frame of RGB segment colors."""
        dt = max(0.0, t - self._last_t) if self._last_t > 0 else 0.0
        self._last_t = t

        if self.mode == RenderMode.SOLID:
            return self._render_solid(intent)
        elif self.mode == RenderMode.PULSE:
            return self._render_pulse(intent, dt, beat, params)
        elif self.mode == RenderMode.BREATHE:
            return self._render_breathe(intent, dt, params)
        elif self.mode == RenderMode.SCROLL:
            return self._render_scroll(intent, dt, beat, params)
        elif self.mode == RenderMode.WAVE:
            return self._render_wave(intent, dt, params)
        elif self.mode == RenderMode.GRADIENT:
            return self._render_gradient(intent, dt, params)
        return self._render_solid(intent)

    # -- Solid ---------------------------------------------------------------

    def _render_solid(self, intent: LightingIntent) -> list[tuple[int, int, int]]:
        r, g, b = _parse_hex(intent.color) if intent.color else _DEFAULT_COLOR
        factor = max(0.0, min(1.0, intent.intensity))
        pixel = (int(r * factor), int(g * factor), int(b * factor))
        return [pixel] * self.segments

    # -- Pulse ---------------------------------------------------------------

    def _render_pulse(
        self, intent: LightingIntent, dt: float, beat: bool,
        params: dict | None = None,
    ) -> list[tuple[int, int, int]]:
        decay = params.get("pulse_decay", self._pulse_decay) if params else self._pulse_decay
        self._pulse_brightness *= math.exp(-decay * dt)
        if beat:
            accent = float(params.get("beat_accent", 1.0)) if params else 1.0
            accent = max(0.0, min(1.0, accent))
            # A secondary beat only lifts the existing envelope. Full
            # downbeats still reach 100%, while intervening beats add a gentle
            # rhythmic nudge instead of a hard flash.
            self._pulse_brightness = max(self._pulse_brightness, accent)

        r, g, b = _parse_hex(intent.color) if intent.color else _DEFAULT_COLOR
        level = self._pulse_brightness * max(0.0, min(1.0, intent.intensity))
        pixel = (int(r * level), int(g * level), int(b * level))
        return [pixel] * self.segments

    # -- Breathe -------------------------------------------------------------

    def _render_breathe(
        self, intent: LightingIntent, dt: float,
        params: dict | None = None,
    ) -> list[tuple[int, int, int]]:
        bpm = max(1.0, intent.bpm)
        freq = (bpm / 60.0) * (params.get("breathe_rate_mult", 1.0) if params else 1.0)
        self._breathe_phase += freq * dt
        # Sine wave 0→1→0
        wave = (math.sin(2.0 * math.pi * self._breathe_phase) + 1.0) / 2.0

        r, g, b = _parse_hex(intent.color) if intent.color else _DEFAULT_COLOR
        level = wave * max(0.0, min(1.0, intent.intensity))
        pixel = (int(r * level), int(g * level), int(b * level))
        return [pixel] * self.segments

    # -- Scroll --------------------------------------------------------------

    def _render_scroll(
        self, intent: LightingIntent, dt: float, beat: bool,
        params: dict | None = None,
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
            accent = float(params.get("beat_accent", 1.0)) if params else 1.0
            accent = max(0.0, min(1.0, accent))
            cf = (float(color[0]), float(color[1]), float(color[2]))
            # Inject center + neighbors: ~20% of half-buffer, minimum 2 pixels
            inject_frac = params.get("scroll_inject_width", 0.2) if params else 0.2
            inject_width = max(2, int(half * inject_frac))
            for i in range(min(inject_width, half)):
                previous = self._scroll_buf[i]
                self._scroll_buf[i] = tuple(
                    current + ((target - current) * accent)
                    for current, target in zip(previous, cf, strict=True)
                )

        # Fade: distance-based attenuation tuned so a pixel reaches ~3%
        # brightness by the time it scrolls to the strip edge (~3s at
        # typical BPM).  Near-center pixels fade gently; edges fade faster
        # to prevent accumulation.
        base_fade_rate = 1.2
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

    # -- Wave ----------------------------------------------------------------

    def _render_wave(
        self, intent: LightingIntent, dt: float,
        params: dict | None = None,
    ) -> list[tuple[int, int, int]]:
        bpm = max(1.0, intent.bpm)
        rate_mult = params.get("wave_rate_mult", 1.0) if params else 1.0
        wavelength = params.get("wave_wavelength", 1.0) if params else 1.0
        freq = (bpm / 60.0) * rate_mult

        self._wave_phase += freq * dt

        r, g, b = _parse_hex(intent.color) if intent.color else _DEFAULT_COLOR
        intensity = max(0.0, min(1.0, intent.intensity))

        result: list[tuple[int, int, int]] = []
        for i in range(self.segments):
            seg_pos = i / max(1, self.segments - 1) if self.segments > 1 else 0.0
            seg_phase = self._wave_phase - seg_pos * wavelength
            wave = (math.sin(2.0 * math.pi * seg_phase) + 1.0) / 2.0
            level = wave * intensity
            result.append((int(r * level), int(g * level), int(b * level)))
        return result

    # -- Gradient ------------------------------------------------------------

    def _render_gradient(
        self, intent: LightingIntent, dt: float,
        params: dict | None = None,
    ) -> list[tuple[int, int, int]]:
        speed = params.get("gradient_speed", 0.1) if params else 0.1
        colors_hex = params.get("gradient_colors", None) if params else None

        self._gradient_offset += speed * dt

        if colors_hex and len(colors_hex) >= 2:
            colors = [_parse_hex(c) for c in colors_hex]
        elif intent.color:
            c = _parse_hex(intent.color)
            colors = [c, (0, 0, 0)]
        else:
            colors = [_DEFAULT_COLOR, (0, 0, 0)]

        intensity = max(0.0, min(1.0, intent.intensity))
        n_colors = len(colors)

        result: list[tuple[int, int, int]] = []
        for i in range(self.segments):
            pos = i / max(1, self.segments - 1) if self.segments > 1 else 0.0
            if self._gradient_offset != 0.0:
                pos = (pos + self._gradient_offset) % 1.0
            scaled = pos * (n_colors - 1)
            idx = int(scaled)
            frac = scaled - idx
            if idx >= n_colors - 1:
                idx = n_colors - 2
                frac = 1.0
            c1 = colors[idx]
            c2 = colors[idx + 1]
            cr = c1[0] + (c2[0] - c1[0]) * frac
            cg = c1[1] + (c2[1] - c1[1]) * frac
            cb = c1[2] + (c2[2] - c1[2]) * frac
            result.append((
                int(max(0, min(255, cr * intensity))),
                int(max(0, min(255, cg * intensity))),
                int(max(0, min(255, cb * intensity))),
            ))
        return result
