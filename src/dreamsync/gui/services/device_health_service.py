"""Passive health snapshots for configured GUI hardware."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from dreamsync.output.auto_detect import load_device_config, probe_lan_device


@dataclass(frozen=True)
class DeviceHealthEntry:
    name: str
    address: str
    device_type: str
    status: str
    latency_ms: float | None = None
    error: str = ""


@dataclass(frozen=True)
class DeviceHealthSnapshot:
    entries: tuple[DeviceHealthEntry, ...] = ()
    refreshed_at: float = 0.0
    error: str = ""

    def counts(self) -> dict[str, int]:
        result = {"online": 0, "degraded": 0, "offline": 0, "unknown": 0}
        for entry in self.entries:
            result[entry.status] = result.get(entry.status, 0) + 1
        return result


class DeviceHealthService:
    """Probe configured LAN devices without discovery or output ownership."""

    def __init__(
        self,
        *,
        config_loader=load_device_config,
        lan_probe: Callable[..., object] = probe_lan_device,
        runtime_health_provider: Callable[[], dict[str, dict[str, str | int]]] | None = None,
        interval_seconds: float = 30.0,
    ) -> None:
        self._config_loader = config_loader
        self._lan_probe = lan_probe
        self._runtime_health_provider = runtime_health_provider
        self._interval_seconds = max(1.0, float(interval_seconds))
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._refresh_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._config_path: Path | None = None
        self._snapshot = DeviceHealthSnapshot()

    @property
    def running(self) -> bool:
        with self._lock:
            return self._thread is not None

    def start(self, config_path: Path) -> None:
        config_path = Path(config_path)
        with self._lock:
            if self._thread is not None and self._config_path == config_path:
                return
        self.stop()
        with self._lock:
            self._config_path = config_path
            self._stop_event.clear()
            self._refresh_event.set()
            self._thread = threading.Thread(target=self._run, name="gui-device-health", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._refresh_event.set()
        with self._lock:
            thread = self._thread
            self._thread = None
            self._config_path = None
        if thread is not None:
            thread.join(timeout=5.0)

    def request_refresh(self) -> None:
        self._refresh_event.set()

    def set_runtime_health_provider(self, provider: Callable[[], dict[str, dict[str, str | int]]] | None) -> None:
        self._runtime_health_provider = provider

    def set_interval_seconds(self, value: float) -> None:
        value = max(1.0, float(value))
        with self._lock:
            changed = self._interval_seconds != value
            self._interval_seconds = value
        if changed:
            self._refresh_event.set()

    def snapshot(self) -> DeviceHealthSnapshot:
        with self._lock:
            return self._snapshot

    def refresh_once(self, config_path: Path) -> DeviceHealthSnapshot:
        entries: list[DeviceHealthEntry] = []
        try:
            configs = self._config_loader(Path(config_path))
        except Exception as exc:
            snapshot = DeviceHealthSnapshot(
                refreshed_at=time.time(),
                error=str(exc).strip() or type(exc).__name__,
            )
            with self._lock:
                self._snapshot = snapshot
            return snapshot
        try:
            runtime_health = self._runtime_health_provider() if self._runtime_health_provider else {}
        except Exception:
            runtime_health = {}
        for config in configs:
            observation = runtime_health.get(config.address, {})
            device_type = str(config.type or "").lower()
            if device_type == "auto":
                device_type = "ble" if ":" in config.address else "lan"
            if device_type == "ble":
                status = str(observation.get("status") or "unknown")
                entries.append(
                    DeviceHealthEntry(
                        name=config.name,
                        address=config.address,
                        device_type="ble",
                        status=status,
                        error=str(observation.get("error") or "BLE health is available while an output session is connected."),
                    )
                )
                continue
            try:
                stats = self._lan_probe(config.address, num_packets=3, rate_hz=10.0)
                count = int(getattr(stats, "count", 0))
                latency = float(getattr(stats, "median_ms", 0.0)) if count else None
                status = "offline" if count == 0 else ("degraded" if latency and latency > 200.0 else "online")
                error = "" if count else "No probe response."
            except Exception as exc:
                latency = None
                status = "offline"
                error = str(exc).strip() or type(exc).__name__
            if observation.get("status") in {"degraded", "offline"}:
                status = str(observation["status"])
                error = str(observation.get("error") or error)
            entries.append(
                DeviceHealthEntry(
                    name=config.name,
                    address=config.address,
                    device_type="lan",
                    status=status,
                    latency_ms=latency,
                    error=error,
                )
            )
        snapshot = DeviceHealthSnapshot(entries=tuple(entries), refreshed_at=time.time())
        with self._lock:
            self._snapshot = snapshot
        return snapshot

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._refresh_event.wait(self._interval_seconds)
            self._refresh_event.clear()
            if self._stop_event.is_set():
                break
            with self._lock:
                config_path = self._config_path
            if config_path is not None:
                self.refresh_once(config_path)
