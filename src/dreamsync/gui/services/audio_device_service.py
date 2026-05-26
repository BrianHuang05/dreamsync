"""GUI-friendly audio device discovery helpers."""

from __future__ import annotations

from dreamsync.audio.system_input import is_capture_device, list_input_devices, list_output_devices
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
        return tuple(options)

    @staticmethod
    def find_label(options: tuple[AudioDeviceOption, ...], device_id: int | None) -> str:
        for option in options:
            if option.id == device_id:
                return option.label
        return "System default" if device_id is None else f"Device {device_id}"

