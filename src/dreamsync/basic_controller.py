from __future__ import annotations

from dataclasses import dataclass

from .director import EffectMode, LightingIntent


@dataclass(frozen=True)
class BeatRippleConfig:
    brightness: float = 0.8
    min_beat_interval_seconds: float = 0.2
    colors: tuple[str, ...] = ("#ff0000", "#00ff00", "#0000ff", "#ff8800", "#aa00ff", "#00ffcc")
    speed_divisor: float = 480.0
    speed_floor: float = 0.08
    speed_ceiling: float = 0.35


class BeatRippleController:
    def __init__(self, config: BeatRippleConfig | None = None) -> None:
        self.config = config or BeatRippleConfig()
        self._last_beat_at = -1e9
        self._last_bpm = 120.0
        self._color_idx = 0

    def update(self, t: float, bpm: float, beat: bool) -> tuple[LightingIntent | None, bool]:
        if bpm > 0.0:
            self._last_bpm = bpm

        beat_event = False
        if beat and (t - self._last_beat_at) >= self.config.min_beat_interval_seconds:
            self._last_beat_at = t
            beat_event = True
            color = self.config.colors[self._color_idx % len(self.config.colors)]
            self._color_idx += 1
            speed = self._last_bpm / self.config.speed_divisor
            speed = min(self.config.speed_ceiling, max(self.config.speed_floor, speed))
            return (
                LightingIntent(
                    mode=EffectMode.RIPPLE,
                    intensity=self.config.brightness,
                    speed=speed,
                    bpm=self._last_bpm,
                    color=color,
                ),
                beat_event,
            )

        return None, beat_event


@dataclass(frozen=True)
class BeatFlashConfig:
    flash_intensity: float = 1.0
    idle_intensity: float = 0.05
    flash_duration_seconds: float = 0.08
    min_flash_interval_seconds: float = 0.2


class BeatFlashController:
    def __init__(self, config: BeatFlashConfig | None = None) -> None:
        self.config = config or BeatFlashConfig()
        self._flash_on_until = -1e9
        self._last_flash_at = -1e9
        self._flash_active = False

    def update(self, t: float, bpm: float, beat: bool) -> tuple[LightingIntent | None, bool]:
        beat_event = False
        if beat and (t - self._last_flash_at) >= self.config.min_flash_interval_seconds:
            self._last_flash_at = t
            self._flash_on_until = t + self.config.flash_duration_seconds
            self._flash_active = True
            beat_event = True
            return (
                LightingIntent(
                    mode=EffectMode.PULSE,
                    intensity=self.config.flash_intensity,
                    speed=min(1.0, max(0.2, bpm / 180.0)),
                    bpm=bpm,
                ),
                beat_event,
            )

        if self._flash_active and t >= self._flash_on_until:
            self._flash_active = False
            return (
                LightingIntent(
                    mode=EffectMode.PULSE,
                    intensity=self.config.idle_intensity,
                    speed=0.2,
                    bpm=bpm,
                ),
                beat_event,
            )

        return None, beat_event
