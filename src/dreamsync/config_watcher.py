"""Hot-reload watcher for devices.yaml.

Polls the config file for mtime changes, diffs old vs new DeviceConfig lists,
and applies adds/removes/changes to the live MultiGoveeLanAdapter without
interrupting the audio loop.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from dreamsync.output.auto_detect import (
    DeviceConfig,
    build_multi_adapter,
    detect_all_devices,
    load_device_config,
)
from dreamsync.output.govee_lan import MultiGoveeLanAdapter
from dreamsync.render import RenderMode

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure diff function
# ---------------------------------------------------------------------------


def _normalize_address(addr: str) -> str:
    """Normalize a device address for comparison (lowercase, strip whitespace)."""
    return addr.strip().lower()


def diff_device_configs(
    old: list[DeviceConfig],
    new: list[DeviceConfig],
) -> tuple[list[DeviceConfig], list[DeviceConfig], list[DeviceConfig]]:
    """Diff two device config lists keyed by normalized address.

    Returns ``(added, removed, changed)`` where:
    - *added*: configs in *new* but not in *old*
    - *removed*: configs in *old* but not in *new*
    - *changed*: configs present in both but with differing fields
    """
    old_by_addr = {_normalize_address(c.address): c for c in old}
    new_by_addr = {_normalize_address(c.address): c for c in new}

    old_keys = set(old_by_addr)
    new_keys = set(new_by_addr)

    added = [new_by_addr[k] for k in sorted(new_keys - old_keys)]
    removed = [old_by_addr[k] for k in sorted(old_keys - new_keys)]
    changed = [
        new_by_addr[k]
        for k in sorted(old_keys & new_keys)
        if old_by_addr[k] != new_by_addr[k]
    ]

    return added, removed, changed


# ---------------------------------------------------------------------------
# ConfigWatcher
# ---------------------------------------------------------------------------


class ConfigWatcher:
    """Watches a YAML device config file and hot-reloads on changes.

    Runs a daemon thread that polls the file's mtime. When a change is
    detected, it reloads the config, diffs against the previous state,
    and applies adds/removes/changes to the live *multi_adapter*.
    """

    def __init__(
        self,
        config_path: Path,
        multi_adapter: MultiGoveeLanAdapter,
        *,
        poll_interval: float = 2.0,
        probe_packets: int = 10,
        probe_rate: float = 5.0,
        render_mode: RenderMode = RenderMode.SCROLL,
        mirror: bool = True,
        brightness: float = 1.0,
        fps: int = 30,
    ) -> None:
        self._config_path = config_path
        self._multi = multi_adapter
        self._poll_interval = poll_interval
        self._probe_packets = probe_packets
        self._probe_rate = probe_rate
        self._render_mode = render_mode
        self._mirror = mirror
        self._brightness = brightness
        self._fps = fps

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_mtime: float = 0.0
        self._current_configs: list[DeviceConfig] = []

        # Snapshot current state
        try:
            self._last_mtime = config_path.stat().st_mtime
            self._current_configs = load_device_config(config_path)
        except Exception:
            _logger.warning("Could not read initial config from %s", config_path)

    def start(self) -> None:
        """Launch the background watcher thread."""
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._watch_loop, name="config-watcher", daemon=True
        )
        self._thread.start()
        _logger.info("Config watcher started for %s", self._config_path)

    def stop(self) -> None:
        """Signal the watcher thread to exit and wait for it."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        _logger.info("Config watcher stopped")

    def _watch_loop(self) -> None:
        """Poll config file mtime and reload on change."""
        while not self._stop_event.is_set():
            self._stop_event.wait(self._poll_interval)
            if self._stop_event.is_set():
                break
            try:
                mtime = self._config_path.stat().st_mtime
            except OSError:
                continue

            if mtime <= self._last_mtime:
                continue

            self._last_mtime = mtime
            _logger.info("Config change detected, reloading %s", self._config_path.name)

            try:
                new_configs = load_device_config(self._config_path)
            except Exception as exc:
                _logger.warning("Config reload failed: %s", exc)
                continue

            added, removed, changed = diff_device_configs(
                self._current_configs, new_configs
            )

            if not added and not removed and not changed:
                _logger.info("Config reloaded but no device changes detected")
                self._current_configs = new_configs
                continue

            # Log what's changing
            for cfg in added:
                _logger.info("  + Adding device: %r at %s", cfg.name, cfg.address)
            for cfg in removed:
                _logger.info("  - Removing device: %r at %s", cfg.name, cfg.address)
            for cfg in changed:
                _logger.info("  ~ Updating device: %r at %s", cfg.name, cfg.address)

            self._apply_changes(added, removed, changed, new_configs)
            self._current_configs = new_configs

    def _apply_changes(
        self,
        added: list[DeviceConfig],
        removed: list[DeviceConfig],
        changed: list[DeviceConfig],
        all_new: list[DeviceConfig],
    ) -> None:
        """Apply config diff to the live multi_adapter."""
        # Strategy: rebuild from the full new config list.
        # This is simpler and safer than incremental add/remove, and
        # the probe cost is low (only 10 packets for hot-add).
        #
        # For removes/changes we need to stop old BLE threads first.
        removed_addrs = {_normalize_address(c.address) for c in removed}
        changed_addrs = {_normalize_address(c.address) for c in changed}
        teardown_addrs = removed_addrs | changed_addrs

        # Stop BLE adapters that are being removed or changed
        old_ble = self._multi._ble_followers
        for ble in old_ble:
            addr = _normalize_address(getattr(ble, 'config', None) and ble.config.address or '')
            if addr in teardown_addrs:
                try:
                    ble.stop()
                except Exception as exc:
                    _logger.warning("Error stopping BLE adapter %s: %s", addr, exc)

        # Turn off LAN adapters being removed or changed (fire-and-forget)
        old_devices = self._multi.devices
        for adapter, _renderer, _role, *_ in old_devices:
            addr = _normalize_address(adapter.config.device_ip)
            if addr in teardown_addrs:
                try:
                    adapter.turn_off()
                except Exception as exc:
                    _logger.warning("Error turning off LAN adapter %s: %s", addr, exc)

        # Probe and build new adapter from full config
        try:
            detected = detect_all_devices(
                all_new,
                num_packets=self._probe_packets,
                rate_hz=self._probe_rate,
            )
            new_adapter = build_multi_adapter(
                detected,
                render_mode=self._render_mode,
                mirror=self._mirror,
                brightness=self._brightness,
                fps=self._fps,
            )
        except Exception as exc:
            _logger.warning("Config reload failed during device detection: %s", exc)
            return

        # Atomic swap
        self._multi.replace_devices(new_adapter.devices)
        self._multi.replace_ble_followers(new_adapter._ble_followers)

        # Activate new devices
        brightness_pct = max(0, min(100, int(self._brightness * 100)))
        for adapter, _renderer, _role, *_ in new_adapter.devices:
            addr = _normalize_address(adapter.config.device_ip)
            # Only activate newly added or changed devices
            if addr in {_normalize_address(c.address) for c in added} | changed_addrs:
                try:
                    adapter.turn_on()
                    time.sleep(0.3)
                    adapter.set_brightness(brightness_pct)
                except Exception as exc:
                    _logger.warning("Error activating device %s: %s", addr, exc)

        # Start new BLE adapters
        for ble in new_adapter._ble_followers:
            addr = _normalize_address(getattr(ble, 'config', None) and ble.config.address or '')
            if addr in {_normalize_address(c.address) for c in added} | changed_addrs:
                try:
                    ble.start()
                except Exception as exc:
                    _logger.warning("Error starting BLE adapter %s: %s", addr, exc)

        total = len(new_adapter.devices) + len(new_adapter._ble_followers)
        _logger.info(
            "Config reload complete: %d devices active (%d added, %d removed, %d updated)",
            total, len(added), len(removed), len(changed),
        )
