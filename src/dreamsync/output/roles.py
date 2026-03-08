from __future__ import annotations

from enum import Enum

from dreamsync.director import EffectMode, LightingIntent


class DeviceRole(str, Enum):
    PRIMARY = "primary"
    ACCENT = "accent"


class DeviceType(str, Enum):
    BULB = "bulb"
    STRIP_SINGLE = "strip_single"
    STRIP_MULTI = "strip_multi"


# Map from show render_mode → device-appropriate render_mode
DEVICE_EFFECT_MAP: dict[DeviceType, dict[str, str]] = {
    DeviceType.BULB: {
        "scroll": "pulse",
        "wave": "breathe",
        "gradient": "breathe",
    },
    DeviceType.STRIP_SINGLE: {
        "scroll": "pulse",
        "wave": "pulse",
        "gradient": "solid",
    },
    DeviceType.STRIP_MULTI: {},
}


DEFAULT_BRIGHTNESS_SCALE: dict[DeviceType, float] = {
    DeviceType.BULB: 1.0,
    DeviceType.STRIP_SINGLE: 0.5,
    DeviceType.STRIP_MULTI: 0.4,
}

DEFAULT_ROLE: dict[DeviceType, DeviceRole] = {
    DeviceType.BULB: DeviceRole.PRIMARY,
    DeviceType.STRIP_SINGLE: DeviceRole.ACCENT,
    DeviceType.STRIP_MULTI: DeviceRole.PRIMARY,
}


def default_device_config(device_type: DeviceType) -> tuple[DeviceRole, float]:
    """Return sensible defaults for role and brightness_scale."""
    return (
        DEFAULT_ROLE.get(device_type, DeviceRole.PRIMARY),
        DEFAULT_BRIGHTNESS_SCALE.get(device_type, 1.0),
    )


def adapt_render_mode(mode: str, device_type: DeviceType) -> str:
    """Map a show render_mode to the best available mode for this device type."""
    mapping = DEVICE_EFFECT_MAP.get(device_type, {})
    return mapping.get(mode, mode)


def infer_device_type(segments: int, model_hint: str = "") -> DeviceType:
    """Infer device type from segment count and optional model hint."""
    if segments <= 1:
        if "bulb" in model_hint.lower() or "light" in model_hint.lower():
            return DeviceType.BULB
        return DeviceType.STRIP_SINGLE
    return DeviceType.STRIP_MULTI


def transform_intent(
    intent: LightingIntent,
    role: DeviceRole,
    brightness_scale: float = 1.0,
) -> LightingIntent:
    if role == DeviceRole.ACCENT:
        intent = LightingIntent(
            mode=EffectMode.AMBIENT,
            intensity=intent.intensity * 0.6,
            speed=min(intent.speed, 0.25),
            bpm=intent.bpm,
            color=intent.color,
        )

    if brightness_scale != 1.0:
        clamped = max(0.0, min(1.0, brightness_scale))
        intent = LightingIntent(
            mode=intent.mode,
            intensity=intent.intensity * clamped,
            speed=intent.speed,
            bpm=intent.bpm,
            color=intent.color,
        )

    return intent
