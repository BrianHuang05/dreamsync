"""GUI-friendly audio device discovery helpers."""

from __future__ import annotations

import sys
import json
import subprocess

from dreamsync.audio.system_input import (
    is_capture_device,
    list_input_devices,
    list_output_devices,
    pulse_source_device_id,
)
from dreamsync.audio.route import is_alsa_loopback_endpoint
from dreamsync.capture.ffmpeg_device import CaptureDiscoveryError, list_pulse_sources
from dreamsync.gui.models.runtime_routing_state import AudioDeviceOption


class AudioDeviceService:
    """Expose input/output device options in a GUI-friendly shape."""

    def list_physical_sink_options(self) -> tuple[tuple[str, str], ...]:
        """Return exact Pulse sink names and friendly labels for physical outputs."""
        if not sys.platform.startswith("linux"):
            raise RuntimeError("Physical PipeWire outputs are available on Linux. Saved selection is retained.")
        try:
            result = subprocess.run(
                ["pactl", "--format=json", "list", "sinks"],
                capture_output=True, text=True, timeout=3, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("Could not discover outputs. Check pactl and PipeWire, then refresh.") from exc
        if result.returncode:
            raise RuntimeError("Could not connect to PipeWire/PulseAudio. Refresh after it is running.")
        try:
            rows = json.loads(result.stdout)
            if not isinstance(rows, list):
                raise ValueError("Expected sink list")
            options = {}
            for row in rows:
                name = row["name"]
                properties = row.get("properties") or {}
                identity = " ".join(str(properties.get(key, "")) for key in
                                    ("alsa.card_name", "alsa.long_card_name", "device.description"))
                if (name.startswith("dreamsync_") or name.endswith(".monitor")
                        or is_alsa_loopback_endpoint(name + " " + identity)
                        or properties.get("device.class") in {"abstract", "filter"}):
                    continue
                if not (name.startswith(("alsa_output.", "bluez_output.", "bluez_sink."))
                        or properties.get("device.api") in {"alsa", "bluez5"}):
                    continue
                options[name] = str(row.get("description") or properties.get("device.description") or name)
            return tuple(sorted(options.items(), key=lambda option: (option[1].casefold(), option[0])))
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise RuntimeError("Could not read the PipeWire output list. Check pactl and refresh.") from exc

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
            loopback = tuple(
                source.name for source in sources
                if is_alsa_loopback_endpoint(source.name)
            )
            loopback = tuple(name for name in loopback if name.endswith(".monitor")) + tuple(
                name for name in loopback if not name.endswith(".monitor")
            )
            monitors = tuple(source.name for source in sources if source.name.endswith(".monitor"))
            return loopback or monitors or tuple(source.name for source in sources)
        return tuple(str(row["name"]) for row in list_input_devices())

    @staticmethod
    def capture_source_names_from_options(
        options: tuple[AudioDeviceOption, ...],
    ) -> tuple[str, ...]:
        """Extract exact loopback choices from an already-discovered input list."""
        named = tuple(option.name for option in options if option.id is not None)
        loopback = tuple(name for name in named if is_alsa_loopback_endpoint(name))
        loopback = tuple(name for name in loopback if name.endswith(".monitor")) + tuple(
            name for name in loopback if not name.endswith(".monitor")
        )
        monitors = tuple(name for name in named if name.endswith(".monitor"))
        return loopback or monitors or named

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

