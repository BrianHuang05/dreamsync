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
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from dreamsync.groups.models import (
    GroupDefinition,
    parse_group_definitions,
    validate_memberships,
)
from dreamsync.output.govee_lan import (
    GoveeLanAdapter,
    GoveeLanConfig,
    MultiGoveeLanAdapter,
    TransportMode,
    build_ptreal_power_packet,
)
from dreamsync.output.roles import DeviceRole, default_device_config, infer_device_type
from dreamsync.render import RenderMode, SegmentRenderer
from dreamsync.spatial.mapper import SpatialMapper
from dreamsync.spatial.models import DevicePlacement, parse_device_placement
from dreamsync.spatial.models import placement_to_mapping

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
    brightness_scale: float | None = None  # 0.0–1.0, None = use default
    max_fps: float = 5.0
    placement: DevicePlacement | None = None
    group_definitions: tuple[GroupDefinition, ...] = ()


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


def _probe_lan_batch(
    ips: list[str],
    num_packets: int = 5,
    rate_hz: float = 20.0,
) -> dict[str, LatencyStats]:
    """Probe multiple LAN devices concurrently using a single shared listener.

    Sends *num_packets* rounds at *rate_hz*.  Each round sends one scan
    packet to every IP and collects responses, so N devices take roughly
    the same time as 1 device.
    """
    from dreamsync.output.discovery import _SCAN_MSG, LISTEN_PORT, MCAST_PORT

    if not ips:
        return {}

    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind(("", LISTEN_PORT))
    except OSError:
        _logger.warning("Could not bind to port %d for LAN batch probe", LISTEN_PORT)
        return {ip: LatencyStats(samples=[]) for ip in ips}
    listener.settimeout(0.5)

    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)

    samples: dict[str, list[float]] = {ip: [] for ip in ips}
    ip_set = set(ips)
    interval = 1.0 / max(0.1, rate_hz)

    try:
        for _ in range(num_packets):
            # Send to all devices in rapid succession
            send_times: dict[str, float] = {}
            for ip in ips:
                send_times[ip] = time.monotonic()
                sender.sendto(_SCAN_MSG, (ip, MCAST_PORT))

            # Collect responses (wait up to timeout for all)
            deadline = time.monotonic() + 0.5
            received: set[str] = set()
            while len(received) < len(ips) and time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                listener.settimeout(remaining)
                try:
                    _data, addr = listener.recvfrom(4096)
                    resp_ip = addr[0]
                    if resp_ip in ip_set and resp_ip not in received:
                        rtt = (time.monotonic() - send_times[resp_ip]) * 1000.0
                        samples[resp_ip].append(rtt)
                        received.add(resp_ip)
                except socket.timeout:
                    break

            # Rate limit between rounds
            elapsed = time.monotonic() - min(send_times.values())
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
    finally:
        sender.close()
        listener.close()

    return {ip: LatencyStats(samples=s) for ip, s in samples.items()}


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

    group_definitions = parse_group_definitions(raw.get("groups"))
    configs: list[DeviceConfig] = []
    for i, entry in enumerate(devices):
        if not isinstance(entry, dict):
            raise ValueError(f"Device entry {i} must be a mapping in {path}")
        if "address" not in entry:
            raise ValueError(f"Device entry {i} missing required 'address' field in {path}")
        bs_raw = entry.get("brightness_scale")
        placement = parse_device_placement(entry)
        if placement is not None:
            validate_memberships(
                group_definitions,
                placement.groups,
                field_name=f"devices[{i}].groups",
            )
            for section in placement.sections:
                validate_memberships(
                    group_definitions,
                    (*section.groups, *section.exclude_groups),
                    field_name=f"devices[{i}].sections[{section.index}]",
                )
                overlap = set(section.groups) & set(section.exclude_groups)
                if overlap:
                    raise ValueError(
                        f"devices[{i}].sections[{section.index}] lists group(s) "
                        f"in both groups and exclude_groups: "
                        f"{', '.join(sorted(overlap))}."
                    )
        configs.append(DeviceConfig(
            name=entry.get("name", entry["address"]),
            address=entry["address"],
            type=entry.get("type", "auto"),
            segments=int(entry.get("segments", 15)),
            transport=entry.get("transport"),
            protocol=entry.get("protocol"),
            role=entry.get("role"),
            brightness_scale=float(bs_raw) if bs_raw is not None else None,
            max_fps=float(entry.get("max_fps", 5.0)),
            placement=placement,
            group_definitions=group_definitions,
        ))

    return configs


def save_device_config(path: Path, configs: list[DeviceConfig]) -> None:
    """Write a list of DeviceConfig objects back to a YAML file."""
    yaml = _require_yaml()
    definitions = next(
        (cfg.group_definitions for cfg in configs if cfg.group_definitions),
        (),
    )
    payload: dict[str, object] = {
        "devices": [device_config_to_mapping(cfg) for cfg in configs]
    }
    if definitions:
        payload = {
            "groups": [definition.to_mapping() for definition in definitions],
            **payload,
        }
    with open(path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False)


def device_config_to_mapping(config: DeviceConfig) -> dict[str, object]:
    """Serialize a DeviceConfig to a YAML-friendly mapping."""
    data: dict[str, object] = {
        "name": config.name,
        "address": config.address,
        "type": config.type,
        "segments": config.segments,
        "max_fps": config.max_fps,
    }
    if config.transport is not None:
        data["transport"] = config.transport
    if config.protocol is not None:
        data["protocol"] = config.protocol
    if config.role is not None:
        data["role"] = config.role
    if config.brightness_scale is not None:
        data["brightness_scale"] = config.brightness_scale
    if config.placement is not None:
        data.update(placement_to_mapping(config.placement))
    return data


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def detect_all_devices(
    configs: list[DeviceConfig],
    num_packets: int = 100,
    rate_hz: float = 5.0,
    *,
    lan_packets: int = 5,
    ble_packets: int = 10,
    lan_rate_hz: float = 20.0,
    ble_rate_hz: float = 10.0,
    parallel: bool = True,
) -> list[DetectedDevice]:
    """Probe all configured devices and classify their roles.

    When *parallel* is True (default), LAN devices are probed with a
    shared-socket batch and BLE devices are probed via a thread pool.
    The per-type packet counts default to fast values (5 LAN / 10 BLE).

    If *num_packets* is explicitly changed from the default of 100 (i.e.
    the caller passed ``--probe-packets``), it overrides both
    *lan_packets* and *ble_packets* for backward compatibility.
    """
    # Legacy override: if caller explicitly set num_packets, use it everywhere
    _lan_pkt = lan_packets
    _ble_pkt = ble_packets
    _lan_hz = lan_rate_hz
    _ble_hz = ble_rate_hz
    if num_packets != 100:
        _lan_pkt = num_packets
        _ble_pkt = num_packets
        _lan_hz = rate_hz
        _ble_hz = rate_hz

    if parallel and len(configs) > 1:
        return _detect_parallel(configs, _lan_pkt, _ble_pkt, _lan_hz, _ble_hz)
    return _detect_sequential(configs, _lan_pkt, _ble_pkt, _lan_hz, _ble_hz)


def _classify_lan(cfg: DeviceConfig, stats: LatencyStats) -> DetectedDevice:
    """Build a DetectedDevice for a LAN device from its probe stats."""
    if stats.count > 0:
        transport = TransportMode(cfg.transport) if cfg.transport else TransportMode.PTREAL
        return DetectedDevice(
            name=cfg.name,
            address=cfg.address,
            connection_type="lan",
            latency=stats,
            role="realtime",
            config=cfg,
            transport=transport,
        )
    return DetectedDevice(
        name=cfg.name,
        address=cfg.address,
        connection_type="unreachable",
        latency=stats,
        role="unreachable",
        config=cfg,
    )


def _classify_ble(cfg: DeviceConfig, stats: LatencyStats) -> DetectedDevice:
    """Build a DetectedDevice for a BLE device from its probe stats."""
    if stats.count > 0:
        role = classify_role(stats)
        return DetectedDevice(
            name=cfg.name,
            address=cfg.address,
            connection_type="ble",
            latency=stats,
            role=role,
            config=cfg,
            ble_protocol=cfg.protocol or "segment",
        )
    return DetectedDevice(
        name=cfg.name,
        address=cfg.address,
        connection_type="unreachable",
        latency=LatencyStats(samples=[]),
        role="unreachable",
        config=cfg,
    )


def _detect_sequential(
    configs: list[DeviceConfig],
    lan_packets: int,
    ble_packets: int,
    lan_rate_hz: float,
    ble_rate_hz: float,
) -> list[DetectedDevice]:
    """Probe devices one at a time (legacy path)."""
    detected: list[DetectedDevice] = []

    for cfg in configs:
        is_ip = _is_ip_address(cfg.address)
        device_type = cfg.type

        # Determine connection type
        if device_type == "lan" or (device_type == "auto" and is_ip):
            stats = probe_lan_device(cfg.address, num_packets=lan_packets, rate_hz=lan_rate_hz)
            if stats.count > 0:
                detected.append(_classify_lan(cfg, stats))
                continue
            # Fall through to BLE if LAN probe failed and type is auto
            if device_type == "lan":
                detected.append(_classify_lan(cfg, stats))
                continue

        # Try BLE
        if device_type == "ble" or (device_type == "auto" and not is_ip) or (device_type == "auto" and is_ip):
            try:
                stats = probe_ble_device(
                    cfg.address, protocol=cfg.protocol,
                    num_packets=ble_packets, rate_hz=ble_rate_hz,
                )
                if stats.count > 0:
                    detected.append(_classify_ble(cfg, stats))
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


def _detect_parallel(
    configs: list[DeviceConfig],
    lan_packets: int,
    ble_packets: int,
    lan_rate_hz: float,
    ble_rate_hz: float,
) -> list[DetectedDevice]:
    """Probe devices concurrently: batch UDP for LAN, thread pool for BLE."""
    lan_configs = [
        c for c in configs
        if c.type == "lan" or (c.type == "auto" and _is_ip_address(c.address))
    ]
    ble_configs = [
        c for c in configs
        if c.type == "ble" or (c.type == "auto" and not _is_ip_address(c.address))
    ]

    # Phase 1: LAN batch (single-socket, fast)
    lan_results: dict[str, LatencyStats] = {}
    if lan_configs:
        lan_ips = [c.address for c in lan_configs]
        lan_results = _probe_lan_batch(lan_ips, lan_packets, lan_rate_hz)

    # Phase 2: BLE parallel (thread pool, ~4s limited by slowest connection)
    ble_results: dict[str, LatencyStats] = {}
    if ble_configs:
        with ThreadPoolExecutor(max_workers=min(len(ble_configs), 4)) as pool:
            futures = {
                pool.submit(
                    probe_ble_device, c.address, c.protocol, ble_packets, ble_rate_hz
                ): c
                for c in ble_configs
            }
            for future in as_completed(futures):
                cfg = futures[future]
                try:
                    ble_results[cfg.address] = future.result()
                except ImportError:
                    _logger.warning("BLE support not available; skipping %s", cfg.address)
                    ble_results[cfg.address] = LatencyStats(samples=[])
                except Exception as exc:
                    _logger.warning("BLE probe failed for %s: %s", cfg.address, exc)
                    ble_results[cfg.address] = LatencyStats(samples=[])

    # Phase 3: Classify and build DetectedDevice list (preserves config order)
    detected: list[DetectedDevice] = []
    for cfg in configs:
        if cfg.address in lan_results:
            stats = lan_results[cfg.address]
            dev = _classify_lan(cfg, stats)
            # If LAN failed and type is auto, try BLE result or mark unreachable
            if dev.role == "unreachable" and cfg.type == "auto" and cfg.address in ble_results:
                dev = _classify_ble(cfg, ble_results[cfg.address])
            detected.append(dev)
        elif cfg.address in ble_results:
            detected.append(_classify_ble(cfg, ble_results[cfg.address]))
        else:
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
    device_triples: list[
        tuple[GoveeLanAdapter, SegmentRenderer, DeviceRole, float, DevicePlacement | None]
    ] = []
    ble_followers: list = []

    reachable_count = sum(1 for d in detected if d.role != "unreachable")
    if reachable_count == 0:
        from .null_adapter import NullMultiAdapter
        _logger.warning("No reachable devices — falling back to null adapter (audio only).")
        return NullMultiAdapter()

    spatial_enabled = False

    for dev in detected:
        if dev.role == "unreachable":
            _logger.warning("Skipping unreachable device: %s (%s)", dev.name, dev.address)
            continue

        cfg = dev.config
        if cfg.placement is not None:
            spatial_enabled = True

        # Resolve role and brightness_scale: explicit config wins, else defaults
        device_type = infer_device_type(cfg.segments, cfg.name)
        default_role, default_bs = default_device_config(device_type)
        device_role = DeviceRole(cfg.role) if cfg.role else default_role
        device_bs = cfg.brightness_scale if cfg.brightness_scale is not None else default_bs

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
                device_type=device_type.value,
            )
            device_triples.append((adapter, renderer, device_role, device_bs, cfg.placement))

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
            renderer = None
            if proto == BleProtocol.SEGMENT:
                renderer = SegmentRenderer(
                    segments=cfg.segments,
                    mode=render_mode,
                    mirror=mirror,
                    device_type=device_type.value,
                )
            ble_followers.append((GoveeBleAdapter(ble_config), device_role, device_bs, cfg.placement, renderer))

    spatial_mapper = SpatialMapper(enabled=spatial_enabled) if spatial_enabled else None
    return MultiGoveeLanAdapter(
        device_triples,
        ble_followers=ble_followers,
        spatial_mapper=spatial_mapper,
        group_definitions=next(
            (
                device.config.group_definitions
                for device in detected
                if device.config.group_definitions
            ),
            (),
        ),
    )


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
