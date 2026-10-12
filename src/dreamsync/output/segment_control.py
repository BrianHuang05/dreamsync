"""Final segment overrides, shared by live and precomputed output."""

import re
from typing import Any

from dreamsync.spatial.models import DevicePlacement


def validate_background_color(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise ValueError("background_color must be a color in #RRGGBB format")
    return value.lower()


def apply_segment_control(
    colors: list[tuple[int, int, int]],
    address: str,
    placement: DevicePlacement | None,
    params: dict[str, Any] | None,
    background_color: str,
    *,
    brightness: int = 100,
) -> tuple[list[tuple[int, int, int]], int]:
    """Return final RGB and hardware brightness; keep physical indices intact.

    With exclusions, bake BLE's dynamic hardware dimming into enabled RGB and
    use full hardware brightness so the fallback never follows the music.
    """
    runtime = (params or {}).get("runtime_control") or {}
    overrides = dict(runtime.get("segment_enabled", ()))
    defaults = {s.index: s.enabled for s in placement.sections} if placement else {}
    disabled = {
        i for i in range(len(colors))
        if not overrides.get(f"{address}#section:{i}", defaults.get(i, True))
    }
    if not disabled:
        return colors, brightness
    color = validate_background_color(runtime.get("background_color") or background_color)
    rgb = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
    factor = brightness / 100.0
    return [
        rgb if i in disabled else tuple(int(c * factor) for c in value)
        for i, value in enumerate(colors)
    ], 100
