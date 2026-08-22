"""GUI-friendly audio device discovery helpers."""

from __future__ import annotations

import sys

from dreamsync.audio.system_input import (
    is_capture_device,
    list_input_devices,
    list_output_devices,
    pulse_source_device_id,
)
from dreamsync.capture.ffmpeg_device import CaptureDiscoveryError, list_pulse_sources
from dreamsync.gui.models.runtime_routing_state import AudioDeviceOption


class AudioDeviceService:
    """Expose input/output device options in a GUI-friendly shape."""

    def list_output_options(self) -> tuple[AudioDeviceOption, ...]:
        options = [AudioDeviceOption(id=None, name="System default", kind="output")]
        for row in list_output_devices():
            options.append(
                AudioDeviceOption(
                    id=int(row["id"]),
                    name=str(row["name"]),
                    hostapi=str(row.get("hostapi", "")),
                    default_samplerate=float(row.get("default_samplerate", 0.0)),
                    channel_count=int(row.get("max_output_channels", 0)),
                    kind="output",
                    is_capture_device=is_capture_device(str(row["name"])),
                )
            )
        return tuple(options)

    def list_input_options(self) -> tuple[AudioDeviceOption, ...]:
        options = [AudioDeviceOption(id=None, name="System default", kind="input")]
        for row in list_input_devices():
            options.append(
                AudioDeviceOption(
                    id=int(row["id"]),
                    name=str(row["name"]),
                    hostapi=str(row.get("hostapi", "")),
                    default_samplerate=float(row.get("default_samplerate", 0.0)),
                    channel_count=int(row.get("max_input_channels", 0)),
                    kind="input",
                    is_capture_device=is_capture_device(str(row["name"])),
                )
            )
        if sys.platform != "win32":
            try:
                for source in list_pulse_sources():
                    options.append(
                        AudioDeviceOption(
                            id=pulse_source_device_id(source.name),
                            name=source.name,
                            hostapi="PipeWire/Pulse",
                            default_samplerate=float(source.sample_rate or 0.0),
                            channel_count=int(source.channels or 0),
                            kind="input",
                        )
                    )
            except CaptureDiscoveryError:
                # ALSA input discovery remains useful when PipeWire/Pulse is
                # unavailable or pactl is not installed.
                pass
        return tuple(options)

    def list_capture_source_names(self) -> tuple[str, ...]:
        """Return exact backend source names suitable for loopback capture."""
        if sys.platform != "win32":
            try:
                sources = list_pulse_sources()
            except CaptureDiscoveryError:
                return ()
            monitors = tuple(source.name for source in sources if source.name.endswith(".monitor"))
            return monitors or tuple(source.name for source in sources)
        return tuple(str(row["name"]) for row in list_input_devices())

    @staticmethod
    def capture_source_names_from_options(
        options: tuple[AudioDeviceOption, ...],
    ) -> tuple[str, ...]:
        """Extract exact loopback choices from an already-discovered input list."""
        named = tuple(option.name for option in options if option.id is not None)
        monitors = tuple(name for name in named if name.endswith(".monitor"))
        return monitors or named

    @staticmethod
    def find_label(options: tuple[AudioDeviceOption, ...], device_id: int | None) -> str:
        for option in options:
            if option.id == device_id:
                return option.label
        return "System default" if device_id is None else f"Device {device_id}"

    @staticmethod
    def resolve_input_sample_rate(
        options: tuple[AudioDeviceOption, ...],
        device_id: int | str | None,
        requested_sample_rate: int,
    ) -> int:
        """Return the selected endpoint's native input rate when known."""

        requested = max(1, int(requested_sample_rate))
        if device_id is None:
            return requested
        for option in options:
            if option.id == device_id:
                native = round(float(option.default_samplerate or 0.0))
                return native if native > 0 else requested
        return requested

