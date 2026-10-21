"""GUI-facing device discovery and assignment helpers."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterable

from dreamsync.output.auto_detect import DeviceConfig, load_device_config, save_device_config
from dreamsync.output.discovery import GoveeDevice, probe_devices, scan_devices
from dreamsync.output.govee_ble import (
    BleProtocol,
    GoveeBleAdapter,
    GoveeBleConfig,
    GoveeBleDevice,
    scan_ble_devices,
)
from dreamsync.output.govee_lan import GoveeLanAdapter, GoveeLanConfig, TransportMode
from dreamsync.spatial.models import DevicePlacement


@dataclass(frozen=True)
class DiscoveredDeviceEntry:
    """A LAN or BLE device found by the GUI discovery workflow."""

    key: str
    source: str
    name: str
    address: str
    sku: str = ""
    device_id: str = ""
    rssi: int | None = None
    latency_ms: float | None = None
    assigned: bool = False
    existing_name: str = ""
    connected: bool = True


@dataclass(frozen=True)
class DeviceTestSpec:
    """Validated test requested for one selected device."""

    color: str = "#3366ff"
    pattern: str = "solid"
    duration_seconds: float = 3.0
    brightness: float = 0.2
    segments: int = 15
    transport: str = "ptreal"
    protocol: str = "segment"
    repeat: bool = False

    def validate(self, source: str) -> None:
        _parse_hex_color(self.color)
        if not 0.1 <= float(self.duration_seconds) <= 15.0:
            raise ValueError("Device test duration must be between 0.1 and 15 seconds.")
        if not 0.05 <= float(self.brightness) <= 1.0:
            raise ValueError("Device test brightness must be between 0.05 and 1.0.")
        if int(self.segments) < 1:
            raise ValueError("Device test requires at least one segment.")
        allowed = {"solid", "alternate", "rainbow", "walk"} if source == "lan" else {"solid", "walk"}
        if self.pattern not in allowed:
            raise ValueError(f"Pattern '{self.pattern}' is not supported for {source.upper()} devices.")
        if source == "ble" and self.protocol == BleProtocol.BULB.value and self.pattern != "solid":
            raise ValueError("BLE bulb tests support only the solid pattern.")


@dataclass(frozen=True)
class DeviceTestResult:
    frames_sent: int
    stopped: bool = False


class DeviceDiscoveryService:
    """Scan for Govee devices, identify them, and persist assignments."""

    def __init__(
        self,
        *,
        lan_scan: Callable[[float], list[GoveeDevice]] | None = None,
        lan_direct_probe: Callable[[list[str], float], list[GoveeDevice]] | None = None,
        ble_scan: Callable[[float], list[GoveeBleDevice]] | None = None,
        lan_adapter_factory: Callable[[GoveeLanConfig], object] | None = None,
        ble_adapter_factory: Callable[[GoveeBleConfig], object] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self._lan_scan = lan_scan or scan_devices
        self._lan_direct_probe = lan_direct_probe or probe_devices
        self._ble_scan = ble_scan or scan_ble_devices
        self._lan_adapter_factory = lan_adapter_factory or GoveeLanAdapter
        self._ble_adapter_factory = ble_adapter_factory or GoveeBleAdapter
        self._sleep = sleep_fn or time.sleep

    def scan_lan(self, timeout: float = 5.0) -> list[DiscoveredDeviceEntry]:
        """Find Govee LAN devices."""

        entries: list[DiscoveredDeviceEntry] = []
        for device in self._lan_scan(timeout):
            name = device.sku or device.device_id or device.ip
            entries.append(
                DiscoveredDeviceEntry(
                    key=f"lan:{device.ip}",
                    source="lan",
                    name=name,
                    address=device.ip,
                    sku=device.sku,
                    device_id=device.device_id,
                )
            )
        return sorted(entries, key=lambda entry: (entry.name.lower(), entry.address))

    def scan_ble(self, timeout: float = 10.0) -> list[DiscoveredDeviceEntry]:
        """Find Govee BLE devices, strongest RSSI first."""

        entries = [
            DiscoveredDeviceEntry(
                key=f"ble:{device.address}",
                source="ble",
                name=device.name or device.address,
                address=device.address,
                rssi=device.rssi,
            )
            for device in self._ble_scan(timeout)
        ]
        return self.sort_entries(entries)

    def scan_all(
        self,
        *,
        lan_timeout: float = 5.0,
        ble_timeout: float = 10.0,
    ) -> list[DiscoveredDeviceEntry]:
        """Run LAN and BLE scans and return a single sorted list."""

        return self.sort_entries([*self.scan_ble(ble_timeout), *self.scan_lan(lan_timeout)])

    def measure_lan_latency(
        self,
        entries: Iterable[DiscoveredDeviceEntry],
    ) -> list[DiscoveredDeviceEntry]:
        """Add a short, direct-unicast latency measurement to live LAN results."""

        live_entries = list(entries)
        lan_addresses = [entry.address for entry in live_entries if entry.source == "lan" and entry.connected]
        if not lan_addresses:
            return live_entries
        try:
            from dreamsync.output.auto_detect import _probe_lan_batch

            stats_by_address = _probe_lan_batch(lan_addresses, num_packets=3, rate_hz=20.0)
        except Exception:
            return live_entries
        return [
            replace(
                entry,
                latency_ms=(
                    stats_by_address[entry.address].median_ms
                    if entry.address in stats_by_address and stats_by_address[entry.address].count
                    else None
                ),
            )
            if entry.source == "lan" and entry.connected
            else entry
            for entry in live_entries
        ]

    def probe_configured_lan(
        self,
        entries: Iterable[DiscoveredDeviceEntry],
    ) -> list[DiscoveredDeviceEntry]:
        """Directly query configured LAN entries omitted by multicast discovery."""

        current = list(entries)
        missing_ips = [
            entry.address
            for entry in current
            if entry.source == "lan" and entry.assigned and not entry.connected
        ]
        if not missing_ips:
            return current
        try:
            found = {
                device.ip: device
                for device in self._lan_direct_probe(missing_ips, 1.0)
            }
        except Exception:
            return current
        return [
            replace(
                entry,
                connected=True,
                sku=found[entry.address].sku,
                device_id=found[entry.address].device_id,
            )
            if entry.address in found
            else entry
            for entry in current
        ]

    @staticmethod
    def collapse_transport_aliases(
        entries: Iterable[DiscoveredDeviceEntry],
        config_path: Path | None,
    ) -> list[DiscoveredDeviceEntry]:
        """Hide unconfigured BLE advertisements already represented by LAN."""

        current = list(entries)
        explicitly_configured: set[str] = set()
        if config_path is not None and config_path.exists():
            explicitly_configured = {
                config.address.upper()
                for config in load_device_config(config_path)
            }
        ble_aliases = {
            _ble_identity_from_lan_device_id(entry.device_id): entry.address
            for entry in current
            if entry.source == "lan" and entry.device_id
        }
        return [
            entry
            for entry in current
            if not (
                entry.source == "ble"
                and entry.address.upper() not in explicitly_configured
                and entry.address.upper() in ble_aliases
            )
        ]

    def merge_with_config(
        self,
        discovered: Iterable[DiscoveredDeviceEntry],
        config_path: Path | None,
    ) -> list[DiscoveredDeviceEntry]:
        """Mark discovered devices and retain configured devices absent from a scan."""

        existing_by_address: dict[str, DeviceConfig] = {}
        if config_path is not None and config_path.exists():
            existing_by_address = {config.address: config for config in load_device_config(config_path)}

        merged: list[DiscoveredDeviceEntry] = []
        discovered_addresses: set[str] = set()
        for entry in discovered:
            discovered_addresses.add(entry.address)
            existing = existing_by_address.get(entry.address)
            if existing is None:
                merged.append(entry)
                continue
            merged.append(
                replace(
                    entry,
                    assigned=True,
                    existing_name=existing.name,
                    name=entry.name or existing.name,
                )
            )
        for address, config in existing_by_address.items():
            if address in discovered_addresses:
                continue
            source = (
                config.type
                if config.type in {"lan", "ble"}
                else ("ble" if ":" in address else "lan")
            )
            merged.append(
                DiscoveredDeviceEntry(
                    key=f"{source}:{address}",
                    source=source,
                    name=config.name,
                    address=address,
                    assigned=True,
                    existing_name=config.name,
                    connected=False,
                )
            )
        return self.sort_entries(merged)

    @staticmethod
    def sort_entries(entries: Iterable[DiscoveredDeviceEntry]) -> list[DiscoveredDeviceEntry]:
        """Sort unassigned devices first, ranking BLE devices by signal strength."""

        def sort_key(entry: DiscoveredDeviceEntry) -> tuple[object, ...]:
            rssi_rank = -(entry.rssi if entry.rssi is not None else -999)
            source_rank = 0 if entry.source == "ble" else 1
            assigned_rank = 1 if entry.assigned else 0
            return (assigned_rank, source_rank, rssi_rank, entry.name.lower(), entry.address)

        return sorted(entries, key=sort_key)

    def assignment_to_config(
        self,
        entry: DiscoveredDeviceEntry,
        *,
        name: str | None = None,
        device_type: str | None = None,
        segments: int = 15,
        transport: str | None = None,
        protocol: str | None = None,
        role: str | None = None,
        brightness_scale: float | None = None,
        max_fps: float | None = None,
        x: float = 0.0,
        y: float = 0.0,
        z: float = 0.0,
    ) -> DeviceConfig:
        """Convert a selected discovered device and form values into config."""

        normalized_type = (device_type or entry.source or "auto").strip().lower()
        normalized_transport = (transport or "").strip().lower() or None
        normalized_protocol = (protocol or "").strip().lower() or None
        normalized_role = (role or "").strip().lower() or None
        if normalized_type == "lan" and normalized_transport is None:
            normalized_transport = TransportMode.PTREAL.value
        if normalized_type == "ble" and normalized_protocol is None:
            normalized_protocol = BleProtocol.SEGMENT.value
        if normalized_type == "lan":
            normalized_protocol = None
        elif normalized_type == "ble":
            normalized_transport = None
        return DeviceConfig(
            name=(name or entry.existing_name or entry.name or entry.address).strip(),
            address=entry.address,
            type=normalized_type,
            segments=max(1, int(segments)),
            transport=normalized_transport,
            protocol=normalized_protocol,
            role=normalized_role,
            brightness_scale=brightness_scale,
            max_fps=float(max_fps if max_fps is not None else
                          (3.0 if normalized_type == "ble" else 5.0)),
            placement=DevicePlacement(x=float(x), y=float(y), z=float(z)),
        )

    def update_device_segments(self, path: Path, entry: DiscoveredDeviceEntry, segments: int) -> None:
        """Update only the selected entry's segment count, retaining other YAML data."""
        import yaml

        if not 1 <= segments <= 2048:
            raise ValueError("Segment count must be between 1 and 2048.")
        source = path.read_text(encoding="utf-8")
        payload = yaml.safe_load(source)
        devices = payload.get("devices", []) if isinstance(payload, dict) else []
        matches = [index for index, device in enumerate(devices)
                   if device.get("address") == entry.address]
        if len(matches) != 1:
            raise ValueError("Save this device to config first; an existing unique entry is required.")
        # Replace the scalar in the source to preserve comments, inventory notes,
        # unknown fields, and all other device settings verbatim.
        root = yaml.compose(source)
        device_nodes = next(value for key, value in root.value if key.value == "devices")
        node = device_nodes.value[matches[0]]
        segment_node = next((value for key, value in node.value if key.value == "segments"), None)
        if segment_node is not None:
            updated = (source[:segment_node.start_mark.index] + str(segments)
                       + source[segment_node.end_mark.index:])
        elif node.flow_style:
            offset = node.end_mark.index - 1
            updated = source[:offset] + f", segments: {segments}" + source[offset:]
        else:
            key = node.value[0][0]
            offset = key.start_mark.index
            updated = (source[:offset] + f"segments: {segments}\n{' ' * key.start_mark.column}"
                       + source[offset:])
        yaml.safe_load(updated)
        path.write_text(updated, encoding="utf-8")

    def upsert_device_config(self, path: Path, config: DeviceConfig) -> None:
        """Create or update a device entry by address."""

        configs = load_device_config(path) if path.exists() else []
        updated = False
        next_configs: list[DeviceConfig] = []
        for existing in configs:
            if existing.address == config.address:
                placement = config.placement
                if placement is not None and existing.placement is not None:
                    placement = replace(
                        placement,
                        orientation=existing.placement.orientation,
                        weight=existing.placement.weight,
                        enabled=existing.placement.enabled,
                        sections=existing.placement.sections,
                        groups=existing.placement.groups,
                    )
                next_configs.append(
                    replace(
                        config,
                        placement=placement,
                        group_definitions=existing.group_definitions,
                        background_color=existing.background_color,
                        device_id=config.device_id or existing.device_id,
                        lan_address=config.lan_address or existing.lan_address,
                        ble_address=config.ble_address or existing.ble_address,
                        transport_policy=existing.transport_policy,
                        spacing_warnings_disabled=existing.spacing_warnings_disabled,
                        protocol=(config.protocol or existing.protocol
                                  if existing.transport_policy == "auto" else config.protocol),
                        transport=(config.transport or existing.transport
                                   if existing.transport_policy == "auto" else config.transport),
                        lan_validated_fps=(config.lan_validated_fps if config.lan_validated_fps is not None
                                           else existing.lan_validated_fps),
                        ble_validated_fps=(config.ble_validated_fps if config.ble_validated_fps is not None
                                           else existing.ble_validated_fps),
                    )
                )
                updated = True
            else:
                next_configs.append(existing)
        if not updated:
            definitions = next(
                (
                    existing.group_definitions
                    for existing in configs
                    if existing.group_definitions
                ),
                (),
            )
            next_configs.append(
                replace(config, group_definitions=definitions)
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        save_device_config(path, next_configs)

    def update_device_lighting_advance(self, path: Path, entry: DiscoveredDeviceEntry, advance_ms: float) -> None:
        """Persist the selected device's calibrated light advance, preserving other YAML fields."""
        import yaml

        value = float(advance_ms)
        if not math.isfinite(value) or not -2000.0 <= value <= 2000.0:
            raise ValueError("Lighting advance must be between -2000 and 2000 ms.")
        source = path.read_text(encoding="utf-8")
        payload = yaml.safe_load(source)
        devices = payload.get("devices", []) if isinstance(payload, dict) else []
        matches = [item for item in devices if isinstance(item, dict) and item.get("address") == entry.address]
        if len(matches) != 1:
            raise ValueError("Save this device to config first; a unique config entry is required.")
        root = yaml.compose(source)
        device_nodes = next(node for key, node in root.value if key.value == "devices")
        node = device_nodes.value[devices.index(matches[0])]
        advance_node = next((val for key, val in node.value if key.value == "lighting_advance_ms"), None)
        if advance_node is not None:
            updated = source[:advance_node.start_mark.index] + str(value) + source[advance_node.end_mark.index:]
        else:
            key = node.value[0][0]
            updated = source[:key.start_mark.index] + f"lighting_advance_ms: {value}\n{' ' * key.start_mark.column}" + source[key.start_mark.index:]
        yaml.safe_load(updated)
        path.write_text(updated, encoding="utf-8")

    def calibrate_device(
        self, entry: DiscoveredDeviceEntry, spec: DeviceTestSpec, *,
        advance_ms: float, stop_event: threading.Event,
        tone_device_id: int | None = None,
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        """Play one short test flash against a tone at the selected timing offset."""
        spec.validate(entry.source)
        advance = max(-2000.0, min(2000.0, float(advance_ms))) / 1000.0
        report_status = on_status or (lambda _message: None)
        if entry.source == "lan":
            report_status("Preparing the light…")
            adapter = self._lan_adapter_factory(GoveeLanConfig(
                device_ip=entry.address, segments=max(1, spec.segments), fps=20,
                brightness=float(spec.brightness), transport=TransportMode(spec.transport),
            ))
            off = [(0, 0, 0)] * max(1, spec.segments)
            flash = [_parse_hex_color(spec.color)] * max(1, spec.segments)
            try:
                adapter.turn_on()
                adapter.set_brightness(max(1, round(spec.brightness * 100)))
                tone_stream, tone = self._open_calibration_tone(tone_device_id)
                try:
                    report_status("Tone output ready. Playing tone and light flash…")
                    tone_at = time.monotonic() + 0.25 + max(0.0, advance)
                    flash_at = tone_at - advance
                    self._run_calibration_flash_sequence(
                        lambda: adapter.send_frame(flash), tone_at, flash_at,
                        stop_event, tone_stream, tone,
                    )
                finally:
                    tone_stream.close()
                stop_event.wait(0.18)
            finally:
                adapter.send_frame(off)
                adapter.turn_off()
            return
        if entry.source == "ble":
            report_status("Connecting to the light over Bluetooth…")
            protocol = BleProtocol(spec.protocol)
            adapter = self._ble_adapter_factory(GoveeBleConfig(
                address=entry.address, name=entry.name, protocol=protocol,
                segments=max(1, spec.segments), max_fps=10.0,
            ))
            brightness = max(5, min(100, round(spec.brightness * 100)))
            adapter.start()
            try:
                self._wait_for_ble_connection(adapter, entry.address)
                tone_stream, tone = self._open_calibration_tone(tone_device_id)
                try:
                    report_status("Tone output ready. Playing tone and light flash…")
                    tone_at = time.monotonic() + 0.25 + max(0.0, advance)
                    flash_at = tone_at - advance
                    def send_flash():
                        if protocol == BleProtocol.BULB:
                            adapter.send_color(*_parse_hex_color(spec.color), brightness)
                        else:
                            adapter.send_segment_colors(
                                [_parse_hex_color(spec.color)] * max(1, spec.segments), brightness,
                            )
                    self._run_calibration_flash_sequence(
                        send_flash, tone_at, flash_at, stop_event, tone_stream, tone,
                    )
                finally:
                    tone_stream.close()
                stop_event.wait(0.18)
            finally:
                adapter.send_color(0, 0, 0, brightness)
                adapter.stop()
            return
        raise ValueError(f"Unsupported calibration device: {entry.source}")

    @staticmethod
    def _run_calibration_flash_sequence(send_flash, tone_at, flash_at, stop_event, tone_stream, tone):
        wait_until = DeviceDiscoveryService._wait_until_calibration_time

        errors: list[Exception] = []

        def schedule(target_time, action):
            wait_until(target_time, stop_event)
            if stop_event.is_set():
                return
            try:
                action()
            except Exception as exc:
                errors.append(exc)

        flash_thread = threading.Thread(target=schedule, args=(flash_at, send_flash), daemon=True)
        tone_thread = threading.Thread(
            target=schedule,
            args=(tone_at, lambda: DeviceDiscoveryService._play_calibration_tone_at(
                tone_at, stop_event, tone_stream, tone,
            )),
            daemon=True,
        )
        flash_thread.start()
        tone_thread.start()
        flash_thread.join()
        tone_thread.join()
        if errors:
            raise RuntimeError(f"Calibration tone or light flash failed: {errors[0]}") from errors[0]

    @staticmethod
    def _wait_until_calibration_time(target_time: float, stop_event: threading.Event) -> None:
        while not stop_event.is_set() and time.monotonic() < target_time:
            stop_event.wait(min(0.02, target_time - time.monotonic()))

    @staticmethod
    def _open_calibration_tone(device_id: int | None = None):
        import numpy as np
        import sounddevice as sd

        stream = None
        try:
            device_info = sd.query_devices(device_id, "output")
            sample_rate = int(round(float(device_info["default_samplerate"])))
            if sample_rate < 8000:
                raise ValueError("The audio device reported an invalid sample rate.")
            sd.check_output_settings(
                device=device_id, channels=1, dtype="float32", samplerate=sample_rate,
            )
            stream = sd.OutputStream(
                device=device_id, samplerate=sample_rate, channels=1,
                dtype="float32", latency="low",
            )
            stream.start()
        except Exception as exc:
            if stream is not None:
                stream.close()
            raise RuntimeError(
                f"Could not open DreamSync's configured audio output for the calibration tone: {exc}"
            ) from exc
        count = int(sample_rate * 0.12)
        phase = 2.0 * np.pi * 880.0 * np.arange(count) / sample_rate
        envelope = np.minimum(1.0, np.minimum(np.arange(count) / 220.0, (count - np.arange(count)) / 220.0))
        tone = (0.15 * np.sin(phase) * envelope).astype(np.float32)
        return stream, tone

    @staticmethod
    def _play_calibration_tone_at(
        target_time: float, stop_event: threading.Event, stream: object, tone: object,
    ) -> None:
        DeviceDiscoveryService._wait_until_calibration_time(target_time, stop_event)
        if not stop_event.is_set():
            stream.write(tone)

    def identify(
        self,
        entry: DiscoveredDeviceEntry,
        *,
        seconds: float = 5.0,
        color: str = "#0000ff",
        segments: int = 15,
        transport: str | None = None,
        protocol: str | None = None,
    ) -> None:
        """Hard-flash the selected device blue at maximum brightness for up to five seconds."""

        seconds = min(5.0, max(0.1, float(seconds)))

        if entry.source == "lan":
            self._identify_lan(entry, seconds=seconds, color=color, segments=segments, transport=transport)
            return
        if entry.source == "ble":
            self._identify_ble(entry, seconds=seconds, color=color, segments=segments, protocol=protocol)
            return
        raise ValueError(f"Unsupported discovery source: {entry.source}")

    def test_device(
        self,
        entry: DiscoveredDeviceEntry,
        spec: DeviceTestSpec,
        *,
        stop_event: threading.Event | None = None,
        spec_provider: Callable[[], DeviceTestSpec] | None = None,
    ) -> DeviceTestResult:
        """Run a selected-device test, optionally repeating until stopped."""
        if entry.source not in {"lan", "ble"}:
            raise ValueError(f"Unsupported discovery source: {entry.source}")
        spec.validate(entry.source)
        stop_event = stop_event or threading.Event()
        if entry.source == "lan":
            return self._test_lan(entry, spec, stop_event, spec_provider)
        return self._test_ble(entry, spec, stop_event, spec_provider)

    def _test_lan(
        self,
        entry: DiscoveredDeviceEntry,
        spec: DeviceTestSpec,
        stop_event: threading.Event,
        spec_provider: Callable[[], DeviceTestSpec] | None = None,
    ) -> DeviceTestResult:
        segments = max(1, int(spec.segments))
        adapter = self._lan_adapter_factory(
            GoveeLanConfig(
                device_ip=entry.address,
                segments=segments,
                fps=20,
                brightness=float(spec.brightness),
                transport=TransportMode(spec.transport),
            )
        )
        frames_sent = 0
        off = [(0, 0, 0)] * segments
        try:
            if hasattr(adapter, "turn_on"):
                adapter.turn_on()
            if hasattr(adapter, "set_brightness"):
                adapter.set_brightness(max(1, round(float(spec.brightness) * 100)))
            for current, frame, delay in _live_test_frames(spec, stop_event, spec_provider):
                if frame is None:
                    self._sleep(delay)
                    continue
                if current != spec:
                    current.validate(entry.source)
                    adapter.config = replace(adapter.config, segments=current.segments,
                                             brightness=current.brightness)
                    if hasattr(adapter, "set_brightness"):
                        adapter.set_brightness(max(1, round(current.brightness * 100)))
                    spec = current
                    off = [(0, 0, 0)] * current.segments
                frames_sent += int(bool(adapter.send_frame(frame)))
                self._sleep(delay)
        finally:
            if hasattr(adapter, "send_frame"):
                adapter.send_frame(off)
            if hasattr(adapter, "turn_off"):
                adapter.turn_off()
        return DeviceTestResult(frames_sent=frames_sent, stopped=stop_event.is_set())

    def _test_ble(
        self,
        entry: DiscoveredDeviceEntry,
        spec: DeviceTestSpec,
        stop_event: threading.Event,
        spec_provider: Callable[[], DeviceTestSpec] | None = None,
    ) -> DeviceTestResult:
        segments = max(1, int(spec.segments))
        protocol = BleProtocol(spec.protocol)
        adapter = self._ble_adapter_factory(
            GoveeBleConfig(
                address=entry.address,
                name=entry.name,
                protocol=protocol,
                segments=segments,
                max_fps=10.0,
            )
        )
        brightness = max(5, min(100, round(float(spec.brightness) * 100)))
        frames_sent = 0
        adapter.start()
        try:
            self._wait_for_ble_connection(adapter, entry.address)
            for current, frame, delay in _live_test_frames(spec, stop_event, spec_provider, ble=True):
                if frame is None:
                    self._sleep(delay)
                    continue
                current.validate(entry.source)
                adapter.config = replace(adapter.config, segments=current.segments)
                brightness = max(5, min(100, round(current.brightness * 100)))
                if current.pattern == "solid":
                    adapter.send_color(*frame[0], brightness)
                else:
                    adapter.send_segment_colors(frame, brightness)
                frames_sent += 1
                self._sleep(delay)
            adapter.send_color(0, 0, 0, brightness)
        finally:
            adapter.stop()
        return DeviceTestResult(frames_sent=frames_sent, stopped=stop_event.is_set())

    def _identify_lan(
        self,
        entry: DiscoveredDeviceEntry,
        *,
        seconds: float,
        color: str,
        segments: int,
        transport: str | None,
    ) -> None:
        mode = TransportMode((transport or TransportMode.PTREAL.value).strip().lower())
        adapter = self._lan_adapter_factory(
            GoveeLanConfig(
                device_ip=entry.address,
                segments=max(1, int(segments)),
                fps=30,
                brightness=1.0,
                transport=mode,
            )
        )
        on = [_parse_hex_color(color)] * max(1, int(segments))
        off = [(0, 0, 0)] * max(1, int(segments))
        try:
            self._pulse_frames(adapter, on, off, seconds)
        finally:
            if hasattr(adapter, "send_frame"):
                adapter.send_frame(off)

    def _identify_ble(
        self,
        entry: DiscoveredDeviceEntry,
        *,
        seconds: float,
        color: str,
        segments: int,
        protocol: str | None,
    ) -> None:
        adapter = self._ble_adapter_factory(
            GoveeBleConfig(
                address=entry.address,
                name=entry.name,
                protocol=BleProtocol((protocol or BleProtocol.SEGMENT.value).strip().lower()),
                segments=max(1, int(segments)),
                max_fps=10.0,
            )
        )
        r, g, b = _parse_hex_color(color)
        adapter.start()
        try:
            self._wait_for_ble_connection(adapter, entry.address)
            for _ in range(_pulse_count(seconds)):
                adapter.send_color(r, g, b, 100)
                self._sleep(_pulse_duration(seconds))
                adapter.send_color(0, 0, 0, 100)
                self._sleep(_pulse_duration(seconds))
            adapter.send_color(0, 0, 0, 100)
        finally:
            adapter.stop()

    @staticmethod
    def _wait_for_ble_connection(adapter: object, address: str) -> None:
        """Wait for adapters that expose readiness; retain simple test fakes."""

        wait = getattr(adapter, "wait_until_connected", None)
        if not callable(wait):
            return
        config = getattr(adapter, "config", None)
        connect_timeout = float(getattr(config, "connect_timeout", 10.0))
        if wait(connect_timeout + 1.0):
            return
        raise RuntimeError(
            f"BLE device {address} advertised, but DreamSync could not establish "
            "a writable GATT connection. Close Govee Home/other controllers, move "
            "the device closer, and try again."
        )

    def _pulse_frames(
        self,
        adapter: object,
        on: list[tuple[int, int, int]],
        off: list[tuple[int, int, int]],
        seconds: float,
    ) -> None:
        for _ in range(_pulse_count(seconds)):
            adapter.send_frame(on)
            self._sleep(_pulse_duration(seconds))
            adapter.send_frame(off)
            self._sleep(_pulse_duration(seconds))


def _parse_hex_color(color: str) -> tuple[int, int, int]:
    value = color.strip().lstrip("#")
    if len(value) != 6:
        raise ValueError(f"Expected #RRGGBB color, got {color!r}")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _ble_identity_from_lan_device_id(device_id: str) -> str:
    """Return the six-byte BLE MAC embedded at the end of a LAN device ID."""

    octets = [
        octet.upper()
        for octet in str(device_id).strip().split(":")
        if octet
    ]
    return ":".join(octets[-6:]) if len(octets) >= 6 else ""


def _pulse_count(seconds: float) -> int:
    return max(1, int(math.ceil(min(5.0, max(0.1, float(seconds))) / 1.0)))


def _pulse_duration(seconds: float) -> float:
    """Split the capped identify window into evenly timed on/off flashes."""

    return min(5.0, max(0.1, float(seconds))) / (_pulse_count(seconds) * 2)


_RAINBOW = (
    (255, 0, 0),
    (255, 127, 0),
    (255, 255, 0),
    (0, 255, 0),
    (0, 255, 255),
    (0, 0, 255),
    (127, 0, 255),
)


def _walk_frames(segments: int, duration_seconds: float):
    frame_count = max(1, int(math.ceil(float(duration_seconds) / 0.3)))
    for index in range(frame_count):
        frame = [(0, 0, 0)] * segments
        segment = index % segments
        frame[segment] = _RAINBOW[segment % len(_RAINBOW)]
        yield frame, min(0.3, float(duration_seconds))


def _lan_test_frames(spec: DeviceTestSpec):
    segments = max(1, int(spec.segments))
    color = _parse_hex_color(spec.color)
    if spec.pattern == "walk":
        yield from _walk_frames(segments, segments * 0.3 if spec.repeat else spec.duration_seconds)
        return
    if spec.pattern == "alternate":
        frame = [color if index % 2 == 0 else (0, 0, 0) for index in range(segments)]
    elif spec.pattern == "rainbow":
        frame = [_RAINBOW[index % len(_RAINBOW)] for index in range(segments)]
    else:
        frame = [color] * segments
    interval = 0.05
    frame_count = max(1, int(math.ceil(float(spec.duration_seconds) / interval)))
    for _ in range(frame_count):
        yield frame, interval


def _bounded_sleep(
    sleep_fn: Callable[[float], None],
    duration_seconds: float,
    stop_event: threading.Event,
) -> None:
    remaining = float(duration_seconds)
    while remaining > 0 and not stop_event.is_set():
        step = min(0.1, remaining)
        sleep_fn(step)
        remaining -= step


def _live_test_frames(spec, stop_event, spec_provider=None, *, ble=False):
    """Reset the sequence on edits without disconnecting the selected device."""
    while not stop_event.is_set():
        if spec_provider is not None:
            spec = spec_provider()
        changed = False
        frames = ([([_parse_hex_color(spec.color)], spec.duration_seconds)]
                  if ble and spec.pattern == "solid" else _lan_test_frames(spec))
        for frame, delay in frames:
            if stop_event.is_set():
                return
            if spec_provider is not None and spec_provider() != spec:
                changed = True
                break
            yield spec, frame, min(0.05, delay)
            remaining = delay - min(0.05, delay)
            while remaining > 1e-9 and not stop_event.is_set():
                if spec_provider is not None and spec_provider() != spec:
                    changed = True
                    break
                # Waits are kept separate from output so a walk keeps its pace.
                step = min(0.05, remaining)
                yield spec, None, step
                remaining -= step
            if changed:
                break
        if not changed and not spec.repeat:
            return
