"""GUI-facing device discovery and assignment helpers."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterable

from dreamsync.output.auto_detect import DeviceConfig, load_device_config, save_device_config
from dreamsync.output.discovery import GoveeDevice, scan_devices
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
    """Validated, short-lived test requested for one selected device."""

    color: str = "#3366ff"
    pattern: str = "solid"
    duration_seconds: float = 3.0
    brightness: float = 0.2
    segments: int = 15
    transport: str = "ptreal"
    protocol: str = "segment"

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
        ble_scan: Callable[[float], list[GoveeBleDevice]] | None = None,
        lan_adapter_factory: Callable[[GoveeLanConfig], object] | None = None,
        ble_adapter_factory: Callable[[GoveeBleConfig], object] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self._lan_scan = lan_scan or scan_devices
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
            max_fps=float(max_fps if max_fps is not None else 5.0),
            placement=DevicePlacement(x=float(x), y=float(y), z=float(z)),
        )

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
    ) -> DeviceTestResult:
        """Run one bounded test against the explicitly selected device."""
        if entry.source not in {"lan", "ble"}:
            raise ValueError(f"Unsupported discovery source: {entry.source}")
        spec.validate(entry.source)
        stop_event = stop_event or threading.Event()
        if entry.source == "lan":
            return self._test_lan(entry, spec, stop_event)
        return self._test_ble(entry, spec, stop_event)

    def _test_lan(
        self,
        entry: DiscoveredDeviceEntry,
        spec: DeviceTestSpec,
        stop_event: threading.Event,
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
            for frame, delay in _lan_test_frames(spec):
                if stop_event.is_set():
                    break
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
            if spec.pattern == "solid":
                r, g, b = _parse_hex_color(spec.color)
                adapter.send_color(r, g, b, brightness)
                frames_sent = 1
                _bounded_sleep(self._sleep, spec.duration_seconds, stop_event)
            else:
                for frame, delay in _walk_frames(segments, spec.duration_seconds):
                    if stop_event.is_set():
                        break
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
            for _ in range(_pulse_count(seconds)):
                adapter.send_color(r, g, b, 100)
                self._sleep(_pulse_duration(seconds))
                adapter.send_color(0, 0, 0, 100)
                self._sleep(_pulse_duration(seconds))
            adapter.send_color(0, 0, 0, 100)
        finally:
            adapter.stop()

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
        yield from _walk_frames(segments, spec.duration_seconds)
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
