"""Scheduling compensation shared by audible compiled-show players."""

import math
import os


def lighting_position(audible_seconds: float, timeline) -> float:
    """Schedule lights ahead of the DAC clock by their delivery delay.

    80 ms is a starting estimate, not a device measurement. A show can override
    it with metadata.lighting_latency_ms; the environment provides a room-wide
    calibration without rewriting saved shows. Negative values delay lights.
    """
    delay = float(os.environ.get(
        "DREAMSYNC_LIGHTING_LATENCY_MS",
        timeline.metadata.get("lighting_latency_ms", 80.0),
    ))
    if not math.isfinite(delay) or abs(delay) > 2000:
        raise ValueError("Lighting latency must be finite and within +/-2000 ms")
    return max(0.0, min(timeline.duration, audible_seconds + delay / 1000.0))
