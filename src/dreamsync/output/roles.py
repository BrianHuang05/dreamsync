from __future__ import annotations

from enum import Enum

from dreamsync.director import EffectMode, LightingIntent


class DeviceRole(str, Enum):
    PRIMARY = "primary"
    ACCENT = "accent"


def transform_intent(intent: LightingIntent, role: DeviceRole) -> LightingIntent:
    if role == DeviceRole.PRIMARY:
        return intent
    if role == DeviceRole.ACCENT:
        return LightingIntent(
            mode=EffectMode.AMBIENT,
            intensity=intent.intensity * 0.6,
            speed=min(intent.speed, 0.25),
            bpm=intent.bpm,
            color=intent.color,
        )
    return intent
