"""Device health monitoring — periodic probing, offline/online transitions, role reclassification.

Runs a daemon thread that periodically probes devices, tracks connectivity state,
and fires callbacks on state changes. Operates alongside ConfigWatcher without
interfering with the audio pipeline.
"""

from __future__ import annotations

import logging
import time
import threading
from dataclasses import dataclass, field
from typing import Callable

from dreamsync.output.auto_detect import (
    DeviceConfig,
    DetectedDevice,
    LatencyStats,
    classify_role,
    probe_lan_device,
)
from dreamsync.output.govee_lan import GoveeLanAdapter, MultiGoveeLanAdapter

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROBE_INTERVAL_S = 30.0
OFFLINE_THRESHOLD = 3        # consecutive failed probes → offline
ONLINE_THRESHOLD = 2         # consecutive successes after offline → online
LATENCY_WINDOW = 10          # rolling window size for latency history
ROLE_RECLASS_THRESHOLD = 3   # consecutive probes at new tier → reclassify
DISCOVERY_INTERVAL_S = 120.0


# ---------------------------------------------------------------------------
# Health state
# ---------------------------------------------------------------------------


@dataclass
class DeviceHealth:
    """Mutable runtime health state for a single device."""

    address: str
    role: str
    status: str = "online"                    # "online" | "offline" | "degraded"
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    last_probe_at: float = 0.0
    last_seen_at: float = 0.0
    latency_history: list[float] = field(default_factory=list)
    offline_since: float | None = None


# ---------------------------------------------------------------------------
# Role classification from rolling window
# ---------------------------------------------------------------------------


def classify_role_from_window(latencies: list[float]) -> str:
    """Classify a device role from a rolling latency window.

    Uses the same thresholds as classify_role but operates on a list of
    median latencies rather than a single LatencyStats.
    """
    if not latencies:
        return "unreachable"
    from statistics import median
    med = median(latencies)
    if med < 20.0:
        return "realtime"
    elif med <= 200.0:
        return "follower"
    else:
        return "slow"


# ---------------------------------------------------------------------------
# DeviceHealthMonitor
# ---------------------------------------------------------------------------


class DeviceHealthMonitor:
    """Periodically probes devices and fires callbacks on state changes."""

    def __init__(
        self,
        multi_adapter: MultiGoveeLanAdapter,
        device_configs: list[DeviceConfig],
        *,
        probe_interval: float = PROBE_INTERVAL_S,
        probe_packets: int = 5,
        probe_rate: float = 10.0,
        enable_discovery: bool = False,
        discovery_interval: float = DISCOVERY_INTERVAL_S,
        on_device_offline: Callable[[str], None] | None = None,
        on_device_online: Callable[[str, LatencyStats], None] | None = None,
        on_role_changed: Callable[[str, str, str], None] | None = None,
        on_device_discovered: Callable | None = None,
    ) -> None:
        self._multi = multi_adapter
        self._configs = {cfg.address: cfg for cfg in device_configs}
        self._probe_interval = probe_interval
        self._probe_packets = probe_packets
        self._probe_rate = probe_rate
        self._enable_discovery = enable_discovery
        self._discovery_interval = discovery_interval

        self._on_device_offline = on_device_offline
        self._on_device_online = on_device_online
        self._on_role_changed = on_role_changed
        self._on_device_discovered = on_device_discovered

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_discovery_at = 0.0

        # Initialize health state from current adapter list
        self._health: dict[str, DeviceHealth] = {}
        now = time.monotonic()
        for adapter, _renderer, role in multi_adapter.devices:
            addr = adapter.config.device_ip
            self._health[addr] = DeviceHealth(
                address=addr,
                role=role.value if hasattr(role, 'value') else str(role),
                last_seen_at=now,
                last_probe_at=now,
            )

    def start(self) -> None:
        """Launch the background health-monitoring thread."""
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._probe_loop, name="device-health", daemon=True,
        )
        self._thread.start()
        _logger.info("Device health monitor started (interval=%.0fs)", self._probe_interval)

    def stop(self) -> None:
        """Signal the monitor thread to exit and wait for it."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=10.0)
            self._thread = None
        _logger.info("Device health monitor stopped")

    def get_health(self, address: str) -> DeviceHealth | None:
        return self._health.get(address)

    def get_all_health(self) -> dict[str, DeviceHealth]:
        """Return a snapshot of all device health states."""
        return dict(self._health)

    # ------------------------------------------------------------------
    # Probe loop
    # ------------------------------------------------------------------

    def _probe_loop(self) -> None:
        while not self._stop_event.is_set():
            self._stop_event.wait(self._probe_interval)
            if self._stop_event.is_set():
                break

            # Sync health dict with current adapter list
            current_addrs = set()
            for adapter, _renderer, role in self._multi.devices:
                addr = adapter.config.device_ip
                current_addrs.add(addr)
                if addr not in self._health:
                    self._health[addr] = DeviceHealth(
                        address=addr,
                        role=role.value if hasattr(role, 'value') else str(role),
                        last_seen_at=time.monotonic(),
                    )
            # Remove stale entries
            for addr in list(self._health.keys()):
                if addr not in current_addrs:
                    del self._health[addr]

            # Probe each device
            for addr, health in list(self._health.items()):
                if self._stop_event.is_set():
                    break
                self._probe_device(addr, health)

            # Optional discovery scan
            if self._enable_discovery:
                now = time.monotonic()
                if now - self._last_discovery_at >= self._discovery_interval:
                    self._last_discovery_at = now
                    self._run_discovery(current_addrs)

    def _probe_device(self, addr: str, health: DeviceHealth) -> None:
        """Probe a single device and update its health state."""
        now = time.monotonic()
        try:
            stats = probe_lan_device(
                addr,
                num_packets=self._probe_packets,
                rate_hz=self._probe_rate,
            )
        except Exception as exc:
            _logger.debug("Probe error for %s: %s", addr, exc)
            stats = LatencyStats(samples=[])

        health.last_probe_at = now

        if stats.count == 0:
            health.consecutive_failures += 1
            health.consecutive_successes = 0

            if (health.consecutive_failures >= OFFLINE_THRESHOLD
                    and health.status != "offline"):
                health.status = "offline"
                health.offline_since = now
                _logger.warning("Device %s went offline", addr)
                # Pause the adapter
                self._set_adapter_paused(addr, True)
                if self._on_device_offline:
                    try:
                        self._on_device_offline(addr)
                    except Exception as exc:
                        _logger.warning("on_device_offline callback error: %s", exc)
        else:
            health.consecutive_successes += 1
            health.consecutive_failures = 0
            health.last_seen_at = now

            # Update latency window
            health.latency_history.append(stats.median_ms)
            if len(health.latency_history) > LATENCY_WINDOW:
                health.latency_history = health.latency_history[-LATENCY_WINDOW:]

            if (health.status == "offline"
                    and health.consecutive_successes >= ONLINE_THRESHOLD):
                health.status = "online"
                health.offline_since = None
                _logger.info("Device %s back online", addr)
                # Unpause the adapter
                self._set_adapter_paused(addr, False)
                if self._on_device_online:
                    try:
                        self._on_device_online(addr, stats)
                    except Exception as exc:
                        _logger.warning("on_device_online callback error: %s", exc)

            # Role reclassification
            if len(health.latency_history) >= ROLE_RECLASS_THRESHOLD:
                recent = health.latency_history[-ROLE_RECLASS_THRESHOLD:]
                new_role = classify_role_from_window(recent)
                if new_role != health.role and new_role != "unreachable":
                    # Check all recent probes agree on the new role
                    all_agree = all(
                        classify_role_from_window([lat]) == new_role
                        for lat in recent
                    )
                    if all_agree:
                        old_role = health.role
                        health.role = new_role
                        _logger.info(
                            "Device %s reclassified: %s -> %s", addr, old_role, new_role,
                        )
                        if self._on_role_changed:
                            try:
                                self._on_role_changed(addr, old_role, new_role)
                            except Exception as exc:
                                _logger.warning("on_role_changed callback error: %s", exc)

    def _set_adapter_paused(self, addr: str, paused: bool) -> None:
        """Set the paused flag on the adapter matching the given address."""
        for adapter, _renderer, _role in self._multi.devices:
            if adapter.config.device_ip == addr:
                adapter.paused = paused
                break

    def _run_discovery(self, known_addrs: set[str]) -> None:
        """Scan for new devices on the network."""
        try:
            from dreamsync.output.discovery import scan_devices
            new_devices = scan_devices(timeout=3.0)
        except Exception as exc:
            _logger.debug("Discovery scan error: %s", exc)
            return

        for dev in new_devices:
            if dev.ip not in known_addrs:
                _logger.info("New device discovered: %s (%s)", dev.ip, dev.sku)
                if self._on_device_discovered:
                    try:
                        self._on_device_discovered(dev)
                    except Exception as exc:
                        _logger.warning("on_device_discovered callback error: %s", exc)
