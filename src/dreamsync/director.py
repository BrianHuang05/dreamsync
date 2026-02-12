from __future__ import annotations

from dataclasses import dataclass
from collections import deque
import math
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
    warmup_seconds: float = 8.0
    history_seconds: float = 10.0
    ema_alpha_rms: float = 0.2
    ema_alpha_bpm: float = 0.12
    ema_alpha_zcr: float = 0.2
    bpm_min: float = 60.0
    bpm_max: float = 180.0
    bpm_jump_limit: float = 22.0
    intensity_floor: float = 0.15
    intensity_ceiling: float = 0.85
    intensity_gamma: float = 0.7
    intensity_slew_per_sec: float = 0.6
    speed_slew_per_sec: float = 0.7
    ambient_speed: float = 0.22
    pulse_speed_min: float = 0.18
    pulse_speed_max: float = 0.6
    motion_speed_min: float = 0.3
    motion_speed_max: float = 0.75


class Director:
    def __init__(self, config: DirectorConfig | None = None) -> None:
        self.config = config or DirectorConfig()
        self.mode: EffectMode = EffectMode.AMBIENT
        self._last_switch_time = -1e9
        self._last_t: float | None = None
        self._ema_rms = 0.0
        self._ema_bpm = 0.0
        self._ema_zcr = 0.0
        self._history: deque[tuple[float, float, float, float]] = deque()
        self._last_intensity = self.config.intensity_floor
        self._last_speed = self.config.ambient_speed

    def _can_switch(self, t: float) -> bool:
        return (t - self._last_switch_time) >= self.config.min_switch_interval_seconds

    def _prune_history(self, t: float) -> None:
        cutoff = t - self.config.history_seconds
        while self._history and self._history[0][0] < cutoff:
            self._history.popleft()

    def _update_history(self, t: float, rms: float, zcr: float, bpm: float) -> None:
        self._history.append((t, rms, zcr, bpm))
        self._prune_history(t)

    def _update_ema(self, rms: float, zcr: float, bpm: float) -> None:
        self._ema_rms = (self.config.ema_alpha_rms * rms) + (
            (1.0 - self.config.ema_alpha_rms) * self._ema_rms
        )
        self._ema_zcr = (self.config.ema_alpha_zcr * zcr) + (
            (1.0 - self.config.ema_alpha_zcr) * self._ema_zcr
        )
        if bpm > 0.0:
            if self._ema_bpm <= 0.0:
                self._ema_bpm = bpm
            else:
                if abs(bpm - self._ema_bpm) <= self.config.bpm_jump_limit:
                    self._ema_bpm = (self.config.ema_alpha_bpm * bpm) + (
                        (1.0 - self.config.ema_alpha_bpm) * self._ema_bpm
                    )

    def _beat_stability(self) -> float:
        # Small values imply more stable beat timing.
        if len(self._history) < 4:
            return self._ema_zcr
        zcr_vals = [row[2] for row in self._history]
        bpm_vals = [row[3] for row in self._history if row[3] > 0.0]
        zcr_mean = sum(zcr_vals) / len(zcr_vals)
        zcr_var = sum((v - zcr_mean) ** 2 for v in zcr_vals) / len(zcr_vals)
        zcr_std = math.sqrt(zcr_var)
        bpm_std = 0.0
        if bpm_vals:
            bpm_mean = sum(bpm_vals) / len(bpm_vals)
            bpm_var = sum((v - bpm_mean) ** 2 for v in bpm_vals) / len(bpm_vals)
            bpm_std = math.sqrt(bpm_var) / max(1.0, bpm_mean)
        return zcr_std + (0.6 * bpm_std)

    def _effective_bpm(self, bpm: float) -> float:
        if bpm <= 0.0:
            return self._ema_bpm
        if bpm < self.config.bpm_min or bpm > self.config.bpm_max:
            return self._ema_bpm
        return bpm

    @staticmethod
    def _slew(value: float, last: float, rate_per_sec: float, dt: float) -> float:
        if dt <= 0.0:
            return last
        max_delta = rate_per_sec * dt
        if value > last + max_delta:
            return last + max_delta
        if value < last - max_delta:
            return last - max_delta
        return value

    def update(self, features: dict[str, float | bool]) -> LightingIntent:
        t = float(features.get("t", 0.0))
        dt = 0.0 if self._last_t is None else max(0.0, t - self._last_t)
        self._last_t = t
        rms = float(features.get("rms", 0.0))
        bpm = float(features.get("bpm", 0.0))
        zcr = float(features.get("zcr", 0.0))

        self._update_history(t, rms, zcr, bpm)
        self._update_ema(rms, zcr, bpm)
        bpm = self._effective_bpm(bpm)
        stability = self._beat_stability()

        if t < self.config.warmup_seconds:
            rms_norm = min(1.0, max(0.0, self._ema_rms * 2.2))
            shaped = rms_norm ** self.config.intensity_gamma
            intensity_target = self.config.intensity_floor + (
                (self.config.intensity_ceiling - self.config.intensity_floor) * shaped
            )
            intensity = self._slew(
                intensity_target, self._last_intensity, self.config.intensity_slew_per_sec, dt
            )
            self._last_intensity = intensity
            speed = self._slew(
                self.config.ambient_speed,
                self._last_speed,
                self.config.speed_slew_per_sec,
                dt,
            )
            self._last_speed = speed
            return LightingIntent(
                mode=EffectMode.AMBIENT,
                intensity=intensity,
                speed=speed,
                bpm=bpm if bpm > 0.0 else 120.0,
            )

        if self._can_switch(t):
            if self.mode != EffectMode.MOTION and self._ema_rms >= self.config.high_energy_enter:
                self.mode = EffectMode.MOTION
                self._last_switch_time = t
            elif self.mode == EffectMode.MOTION and self._ema_rms <= self.config.high_energy_exit:
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

        rms_norm = min(1.0, max(0.0, self._ema_rms * 2.4))
        shaped = rms_norm ** self.config.intensity_gamma
        intensity_target = self.config.intensity_floor + (
            (self.config.intensity_ceiling - self.config.intensity_floor) * shaped
        )
        if self.mode == EffectMode.AMBIENT:
            speed_target = self.config.ambient_speed
        elif self.mode == EffectMode.PULSE:
            speed_target = min(
                self.config.pulse_speed_max,
                max(self.config.pulse_speed_min, bpm / 220.0),
            )
        else:
            speed_target = min(
                self.config.motion_speed_max,
                max(self.config.motion_speed_min, self._ema_rms * 1.6),
            )

        intensity = self._slew(
            intensity_target, self._last_intensity, self.config.intensity_slew_per_sec, dt
        )
        speed = self._slew(
            speed_target, self._last_speed, self.config.speed_slew_per_sec, dt
        )
        self._last_intensity = intensity
        self._last_speed = speed

        return LightingIntent(mode=self.mode, intensity=intensity, speed=speed, bpm=bpm)
