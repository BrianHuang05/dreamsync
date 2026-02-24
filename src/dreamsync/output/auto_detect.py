"""Auto-detection of Govee devices from YAML config.

Loads device definitions from a YAML file, probes each device for latency,
classifies roles (realtime / follower / slow / unreachable), and builds a
MultiGoveeLanAdapter ready for the session runner.
"""

from __future__ import annotations

import logging
import re
import socket
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from dreamsync.output.govee_lan import (
    GoveeLanAdapter,
    GoveeLanConfig,
    MultiGoveeLanAdapter,
    TransportMode,
    build_ptreal_power_packet,
)
from dreamsync.output.roles import DeviceRole
from dreamsync.render import RenderMode, SegmentRenderer

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy YAML import
# ---------------------------------------------------------------------------

_IP_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")


def _require_yaml() -> Any:
    """Import and return the ``yaml`` package, raising a clear error if missing."""
    try:
        import yaml
        return yaml
    except ImportError:
        raise ImportError(
            "The 'pyyaml' package is required for YAML config support. "
            "Install it with:  pip install dreamsync-music-sync[yaml]"
        ) from None


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeviceConfig:
    """One device entry from the YAML config file."""

    name: str
    address: str
    type: str = "auto"          # "lan", "ble", or "auto"
    segments: int = 15
    transport: str | None = None  # "razer", "ptreal", "colorwc"
    protocol: str | None = None   # "segment", "bulb" (BLE only)
    role: str | None = None       # "primary", "accent"
    max_fps: float = 5.0


@dataclass(frozen=True)
class LatencyStats:
    """Latency probe results for a single device."""

    samples: list[float]  # individual round-trip times in ms

    @property
    def count(self) -> int:
        return len(self.samples)

    @property
    def min_ms(self) -> float:
        return min(self.samples) if self.samples else 0.0

    @property
    def max_ms(self) -> float:
        return max(self.samples) if self.samples else 0.0

    @property
    def median_ms(self) -> float:
        return statistics.median(self.samples) if self.samples else 0.0

    @property
    def p95_ms(self) -> float:
        if not self.samples:
            return 0.0
        sorted_s = sorted(self.samples)
        idx = int(len(sorted_s) * 0.95)
        idx = min(idx, len(sorted_s) - 1)
        return sorted_s[idx]

    @property
    def mean_ms(self) -> float:
        return statistics.mean(self.samples) if self.samples else 0.0


@dataclass(frozen=True)
class DetectedDevice:
    """Result of probing a single device."""

    name: str
    address: str
    connection_type: Literal["lan", "ble", "unreachable"]
    latency: LatencyStats
    role: Literal["realtime", "follower", "slow", "unreachable"]
    config: DeviceConfig
    transport: TransportMode | None = None
    ble_protocol: str | None = None


# ---------------------------------------------------------------------------
# Address classification
# ---------------------------------------------------------------------------


def _is_ip_address(address: str) -> bool:
    """Return True if *address* looks like an IPv4 address."""
    return bool(_IP_RE.match(address))


# ---------------------------------------------------------------------------
# Latency probing
# ---------------------------------------------------------------------------


def probe_lan_device(
    ip: str,
    num_packets: int = 100,
    rate_hz: float = 5.0,
) -> LatencyStats:
    """Probe a LAN Govee device by sending UDP scan packets and timing responses.

    Sends *num_packets* scan messages at *rate_hz* and records round-trip times.
    """
    from dreamsync.output.discovery import _SCAN_MSG, LISTEN_PORT, MCAST_PORT

    samples: list[float] = []
    interval = 1.0 / max(0.1, rate_hz)

    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind(("", LISTEN_PORT))
    except OSError:
        _logger.warning("Could not bind to port %d for probing %s", LISTEN_PORT, ip)
        return LatencyStats(samples=[])
    listener.settimeout(0.5)

    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)

    try:
        for _ in range(num_packets):
            t0 = time.monotonic()
            sender.sendto(_SCAN_MSG, (ip, MCAST_PORT))
            try:
                _data, addr = listener.recvfrom(4096)
                if addr[0] == ip:
                    rtt = (time.monotonic() - t0) * 1000.0
                    samples.append(rtt)
            except socket.timeout:
                pass
            elapsed = time.monotonic() - t0
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
    finally:
        sender.close()
        listener.close()

    return LatencyStats(samples=samples)


def probe_ble_device(
    address: str,
    protocol: str | None = None,
    num_packets: int = 100,
    rate_hz: float = 5.0,
) -> LatencyStats:
    """Probe a BLE Govee device by connecting and timing write round-trips.

    This is a synchronous wrapper around an async BLE latency test.
    """
    import asyncio

    from dreamsync.output.govee_ble import GOVEE_BLE_CHAR_UUID, _require_bleak

    bleak = _require_bleak()
    interval = 1.0 / max(0.1, rate_hz)

    async def _probe() -> list[float]:
        samples: list[float] = []
        client = bleak.BleakClient(address, timeout=10.0)
        try:
            await client.connect()
            power_on = build_ptreal_power_packet(True)
            await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, power_on, response=False)
            await asyncio.sleep(0.3)

            for _ in range(num_packets):
                pkt = build_ptreal_power_packet(True)
                t0 = time.monotonic()
                await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, pkt, response=False)
                rtt = (time.monotonic() - t0) * 1000.0
                samples.append(rtt)
                await asyncio.sleep(max(0, interval - rtt / 1000.0))
        except Exception as exc:
            _logger.warning("BLE probe of %s failed: %s", address, exc)
        finally:
            try:
                if client.is_connected:
                    await client.disconnect()
            except Exception:
                pass
        return samples

    samples = asyncio.run(_probe())
    return LatencyStats(samples=samples)


# ---------------------------------------------------------------------------
# Role classification
# ---------------------------------------------------------------------------


def classify_role(
    stats: LatencyStats,
) -> Literal["realtime", "follower", "slow", "unreachable"]:
    """Classify a device role based on latency statistics.

    - <20ms median  -> realtime
    - 20-200ms      -> follower
    - >200ms        -> slow
    - 0 samples     -> unreachable
    """
    if stats.count == 0:
        return "unreachable"
    median = stats.median_ms
    if median < 20.0:
        return "realtime"
    elif median <= 200.0:
        return "follower"
    else:
        return "slow"


# ---------------------------------------------------------------------------
# YAML config loading
# ---------------------------------------------------------------------------


def load_device_config(path: Path) -> list[DeviceConfig]:
    """Parse a YAML device config file and return a list of DeviceConfig."""
    yaml = _require_yaml()

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict) or "devices" not in raw:
        raise ValueError(f"YAML config must contain a top-level 'devices' key: {path}")

    devices = raw["devices"]
    if not isinstance(devices, list):
        raise ValueError(f"'devices' must be a list in {path}")

    configs: list[DeviceConfig] = []
    for i, entry in enumerate(devices):
        if not isinstance(entry, dict):
            raise ValueError(f"Device entry {i} must be a mapping in {path}")
        if "address" not in entry:
            raise ValueError(f"Device entry {i} missing required 'address' field in {path}")
        configs.append(DeviceConfig(
            name=entry.get("name", entry["address"]),
            address=entry["address"],
            type=entry.get("type", "auto"),
            segments=int(entry.get("segments", 15)),
            transport=entry.get("transport"),
            protocol=entry.get("protocol"),
            role=entry.get("role"),
            max_fps=float(entry.get("max_fps", 5.0)),
        ))

    return configs


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def detect_all_devices(
    configs: list[DeviceConfig],
    num_packets: int = 100,
    rate_hz: float = 5.0,
) -> list[DetectedDevice]:
    """Probe all configured devices and classify their roles."""
    detected: list[DetectedDevice] = []

    for cfg in configs:
        is_ip = _is_ip_address(cfg.address)
        device_type = cfg.type

        # Determine connection type
        if device_type == "lan" or (device_type == "auto" and is_ip):
            stats = probe_lan_device(cfg.address, num_packets=num_packets, rate_hz=rate_hz)
            if stats.count > 0:
                # LAN devices are always realtime — no latency classification needed
                role: Literal["realtime", "follower", "slow", "unreachable"] = "realtime"
                transport = TransportMode(cfg.transport) if cfg.transport else TransportMode.PTREAL
                detected.append(DetectedDevice(
                    name=cfg.name,
                    address=cfg.address,
                    connection_type="lan",
                    latency=stats,
                    role=role,
                    config=cfg,
                    transport=transport,
                ))
                continue
            # Fall through to BLE if LAN probe failed and type is auto
            if device_type == "lan":
                detected.append(DetectedDevice(
                    name=cfg.name,
                    address=cfg.address,
                    connection_type="unreachable",
                    latency=stats,
                    role="unreachable",
                    config=cfg,
                ))
                continue

        # Try BLE
        if device_type == "ble" or (device_type == "auto" and not is_ip) or (device_type == "auto" and is_ip):
            try:
                stats = probe_ble_device(
                    cfg.address, protocol=cfg.protocol,
                    num_packets=num_packets, rate_hz=rate_hz,
                )
                if stats.count > 0:
                    role = classify_role(stats)
                    detected.append(DetectedDevice(
                        name=cfg.name,
                        address=cfg.address,
                        connection_type="ble",
                        latency=stats,
                        role=role,
                        config=cfg,
                        ble_protocol=cfg.protocol or "segment",
                    ))
                    continue
            except ImportError:
                _logger.warning("BLE support not available; skipping %s", cfg.address)
            except Exception as exc:
                _logger.warning("BLE probe failed for %s: %s", cfg.address, exc)

        # Unreachable
        detected.append(DetectedDevice(
            name=cfg.name,
            address=cfg.address,
            connection_type="unreachable",
            latency=LatencyStats(samples=[]),
            role="unreachable",
            config=cfg,
        ))

    return detected


# ---------------------------------------------------------------------------
# Adapter builder
# ---------------------------------------------------------------------------


def build_multi_adapter(
    detected: list[DetectedDevice],
    render_mode: RenderMode = RenderMode.SCROLL,
    mirror: bool = True,
    brightness: float = 1.0,
    fps: int = 30,
) -> MultiGoveeLanAdapter:
    """Build a MultiGoveeLanAdapter from detected devices.

    Realtime LAN devices get a GoveeLanAdapter + SegmentRenderer.
    BLE / follower devices get a GoveeBleAdapter.
    Unreachable devices are skipped with a warning.
    """
    device_triples: list[tuple[GoveeLanAdapter, SegmentRenderer, DeviceRole]] = []
    ble_followers: list = []

    reachable_count = sum(1 for d in detected if d.role != "unreachable")
    if reachable_count == 0:
        raise RuntimeError("No reachable devices found. Check your config and network.")

    for dev in detected:
        if dev.role == "unreachable":
            _logger.warning("Skipping unreachable device: %s (%s)", dev.name, dev.address)
            continue

        cfg = dev.config
        device_role = DeviceRole(cfg.role) if cfg.role else DeviceRole.PRIMARY

        if dev.connection_type == "lan":
            transport = dev.transport or TransportMode.PTREAL
            if transport == TransportMode.COLORWC:
                effective_fps = min(fps, 10)
            elif transport == TransportMode.PTREAL:
                effective_fps = min(fps, 20)
            else:
                effective_fps = fps

            lan_config = GoveeLanConfig(
                device_ip=dev.address,
                segments=cfg.segments,
                fps=effective_fps,
                brightness=brightness,
                transport=transport,
            )
            adapter = GoveeLanAdapter(lan_config)
            renderer = SegmentRenderer(
                segments=cfg.segments,
                mode=render_mode,
                mirror=mirror,
            )
            device_triples.append((adapter, renderer, device_role))

        elif dev.connection_type == "ble":
            from dreamsync.output.govee_ble import (
                BleProtocol,
                GoveeBleAdapter,
                GoveeBleConfig,
            )

            proto = BleProtocol(dev.ble_protocol) if dev.ble_protocol else BleProtocol.SEGMENT
            ble_config = GoveeBleConfig(
                address=dev.address,
                protocol=proto,
                segments=cfg.segments,
                max_fps=cfg.max_fps,
            )
            ble_followers.append(GoveeBleAdapter(ble_config))

    return MultiGoveeLanAdapter(device_triples, ble_followers=ble_followers)


# ---------------------------------------------------------------------------
# Human-readable report
# ---------------------------------------------------------------------------


def print_detection_report(detected: list[DetectedDevice]) -> None:
    """Print a human-readable summary of detected devices to stdout."""
    print(f"\n{'='*60}")
    print(f"  Device Detection Report ({len(detected)} devices)")
    print(f"{'='*60}")

    for dev in detected:
        status = "OK" if dev.role != "unreachable" else "UNREACHABLE"
        latency_str = (
            f"{dev.latency.median_ms:.1f}ms median"
            if dev.latency.count > 0
            else "no response"
        )
        print(
            f"  {dev.name:20s}  {dev.address:20s}  "
            f"{dev.connection_type:5s}  {dev.role:10s}  "
            f"{latency_str}  [{status}]"
        )

    reachable = sum(1 for d in detected if d.role != "unreachable")
    print(f"\n  Reachable: {reachable}/{len(detected)}")
    print(f"{'='*60}\n")
