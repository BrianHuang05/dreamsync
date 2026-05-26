"""Structured runtime telemetry for the diagnostics panel."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RuntimeTelemetrySnapshot:
    output_mode: str = "idle"
    capture_state: str = "off"
    pipeline_state: str = "idle"
    ready_queue_count: int = 0
    current_track: str = ""
    device_status: str = ""
    audio_output: str = ""
    input_device: str = ""
    routing_status: str = ""
    elapsed_seconds: float = 0.0
    last_error: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)
    recent_events: tuple[str, ...] = field(default_factory=tuple)
    current_render_mode: str = ""
    current_palette: tuple[str, ...] = field(default_factory=tuple)
    dominant_band: str = ""
    dominant_proxy: str = ""
    pan_center: float = 0.0
    pan_width: float = 0.0
    active_eq_routes: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    active_instrument_routes: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    active_scene_layers: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    runtime_control: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
