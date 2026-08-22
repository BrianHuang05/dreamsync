"""Routing and device-selection state for the GUI runtime dashboard."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AudioDeviceOption:
    id: int | str | None = None
    name: str = ""
    hostapi: str = ""
    default_samplerate: float = 0.0
    channel_count: int = 0
    kind: str = "output"
    is_capture_device: bool = False

    @property
    def label(self) -> str:
        if self.id is None:
            return self.name or "System default"
        host = f" [{self.hostapi}]" if self.hostapi else ""
        return f"{self.id}: {self.name}{host}"


@dataclass(frozen=True)
class OutputTarget:
    mode: str = "simulation"
    config_path: str = ""
    fallback_to_simulation: bool = True


@dataclass(frozen=True)
class RuntimeRoutingState:
    output_target: OutputTarget = field(default_factory=OutputTarget)
    available_output_devices: tuple[AudioDeviceOption, ...] = field(default_factory=tuple)
    available_input_devices: tuple[AudioDeviceOption, ...] = field(default_factory=tuple)
    available_capture_sources: tuple[str, ...] = field(default_factory=tuple)
    selected_output_audio_device_id: int | None = None
    selected_live_input_device_id: int | str | None = None
    selected_output_owner: str = ""
    resolved_output_mode: str = "simulation"
    resolved_config_path: str = ""
    routing_status: str = "Simulation only."
