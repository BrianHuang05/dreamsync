from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EffectMode(str, Enum):
    AMBIENT = "ambient"
    PULSE = "pulse"
    MOTION = "motion"


@dataclass(frozen=True)
class LightingIntent:
    mode: EffectMode
    intensity: float
    speed: float
    bpm: float


@dataclass(frozen=True)
class DirectorConfig:
    high_energy_enter: float = 0.24
    high_energy_exit: float = 0.18
    pulse_enter: float = 0.07
    pulse_exit: float = 0.05
    min_switch_interval_seconds: float = 3.0


class Director:
    def __init__(self, config: DirectorConfig | None = None) -> None:
        self.config = config or DirectorConfig()
        self.mode: EffectMode = EffectMode.AMBIENT
        self._last_switch_time = -1e9

    def _can_switch(self, t: float) -> bool:
        return (t - self._last_switch_time) >= self.config.min_switch_interval_seconds

    @staticmethod
    def _beat_stability(features: dict[str, float | bool]) -> float:
        # Small values imply more stable beat timing.
        zcr = float(features.get("zcr", 0.0))
        return zcr

    def update(self, features: dict[str, float | bool]) -> LightingIntent:
        t = float(features.get("t", 0.0))
        rms = float(features.get("rms", 0.0))
        bpm = float(features.get("bpm", 0.0))
        stability = self._beat_stability(features)

        if self._can_switch(t):
            if self.mode != EffectMode.MOTION and rms >= self.config.high_energy_enter:
                self.mode = EffectMode.MOTION
                self._last_switch_time = t
            elif self.mode == EffectMode.MOTION and rms <= self.config.high_energy_exit:
                # Leaving motion mode falls through to pulse/ambient decision.
                if stability <= self.config.pulse_enter and bpm >= 70:
                    self.mode = EffectMode.PULSE
                else:
                    self.mode = EffectMode.AMBIENT
                self._last_switch_time = t
            elif self.mode == EffectMode.AMBIENT and stability <= self.config.pulse_enter and bpm >= 70:
                self.mode = EffectMode.PULSE
                self._last_switch_time = t
            elif self.mode == EffectMode.PULSE and (
                stability >= self.config.pulse_exit or bpm < 60
            ):
                self.mode = EffectMode.AMBIENT
                self._last_switch_time = t

        intensity = min(1.0, max(0.05, rms * 3.0))
        if self.mode == EffectMode.AMBIENT:
            speed = 0.25
        elif self.mode == EffectMode.PULSE:
            speed = min(1.0, max(0.2, bpm / 180.0))
        else:
            speed = min(1.0, max(0.5, rms * 2.5))

        return LightingIntent(mode=self.mode, intensity=intensity, speed=speed, bpm=bpm)
