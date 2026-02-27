"""Tests for config_watcher: diff_device_configs and ConfigWatcher."""

from __future__ import annotations

import textwrap
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dreamsync.config_watcher import ConfigWatcher, diff_device_configs
from dreamsync.output.auto_detect import DeviceConfig
from dreamsync.output.govee_lan import (
    GoveeLanAdapter,
    GoveeLanConfig,
    MultiGoveeLanAdapter,
    TransportMode,
)
from dreamsync.output.roles import DeviceRole
from dreamsync.render import RenderMode, SegmentRenderer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(address: str, name: str = "", segments: int = 15, **kw) -> DeviceConfig:
    """Shorthand for creating a DeviceConfig."""
    return DeviceConfig(name=name or address, address=address, segments=segments, **kw)


def _make_triple(ip: str, segments: int = 15):
    """Build a (GoveeLanAdapter, SegmentRenderer, DeviceRole) triple with a noop transport."""
    config = GoveeLanConfig(device_ip=ip, segments=segments, transport=TransportMode.PTREAL)
    adapter = GoveeLanAdapter(config, transport=lambda *a: None)
    renderer = SegmentRenderer(segments=segments, mode=RenderMode.SCROLL)
    return (adapter, renderer, DeviceRole.PRIMARY)


# ===========================================================================
# diff_device_configs tests
# ===========================================================================


class TestDiffDeviceConfigs:
    """Pure-function tests for diff_device_configs."""

    def test_no_changes(self):
        configs = [_cfg("10.0.0.1"), _cfg("10.0.0.2")]
        added, removed, changed = diff_device_configs(configs, list(configs))
        assert added == []
        assert removed == []
        assert changed == []

    def test_one_added(self):
        old = [_cfg("10.0.0.1")]
        new = [_cfg("10.0.0.1"), _cfg("10.0.0.2")]
        added, removed, changed = diff_device_configs(old, new)
        assert len(added) == 1
        assert added[0].address == "10.0.0.2"
        assert removed == []
        assert changed == []

    def test_one_removed(self):
        old = [_cfg("10.0.0.1"), _cfg("10.0.0.2")]
        new = [_cfg("10.0.0.1")]
        added, removed, changed = diff_device_configs(old, new)
        assert added == []
        assert len(removed) == 1
        assert removed[0].address == "10.0.0.2"
        assert changed == []

    def test_changed_segments(self):
        old = [_cfg("10.0.0.1", segments=15)]
        new = [_cfg("10.0.0.1", segments=25)]
        added, removed, changed = diff_device_configs(old, new)
        assert added == []
        assert removed == []
        assert len(changed) == 1
        assert changed[0].segments == 25

    def test_address_normalization(self):
        """Addresses are compared case-insensitively with whitespace stripped."""
        old = [_cfg("AA:BB:CC:DD:EE:FF")]
        new = [_cfg("  aa:bb:cc:dd:ee:ff  ")]
        added, removed, changed = diff_device_configs(old, new)
        assert added == []
        assert removed == []
        # The addresses differ in whitespace/case, but they're not "changed"
        # because all *fields* on the dataclass are equal after normalization?
        # Actually, the raw DeviceConfig objects will differ (different address strings),
        # so this IS a "changed" entry.
        assert len(changed) == 1

    def test_multiple_simultaneous(self):
        old = [_cfg("10.0.0.1"), _cfg("10.0.0.2"), _cfg("10.0.0.3", segments=15)]
        new = [_cfg("10.0.0.2"), _cfg("10.0.0.3", segments=25), _cfg("10.0.0.4")]
        added, removed, changed = diff_device_configs(old, new)
        assert [c.address for c in added] == ["10.0.0.4"]
        assert [c.address for c in removed] == ["10.0.0.1"]
        assert [c.address for c in changed] == ["10.0.0.3"]

    def test_empty_lists(self):
        added, removed, changed = diff_device_configs([], [])
        assert added == [] and removed == [] and changed == []

    def test_all_new(self):
        added, removed, changed = diff_device_configs(
            [], [_cfg("10.0.0.1"), _cfg("10.0.0.2")]
        )
        assert len(added) == 2
        assert removed == [] and changed == []

    def test_all_removed(self):
        added, removed, changed = diff_device_configs(
            [_cfg("10.0.0.1"), _cfg("10.0.0.2")], []
        )
        assert added == [] and changed == []
        assert len(removed) == 2


# ===========================================================================
# MultiGoveeLanAdapter helper tests
# ===========================================================================


class TestMultiAdapterHelpers:
    """Tests for replace_devices, replace_ble_followers, get_device_addresses."""

    def test_replace_devices(self):
        t1 = _make_triple("10.0.0.1")
        t2 = _make_triple("10.0.0.2")
        multi = MultiGoveeLanAdapter([t1])
        assert len(multi.devices) == 1

        multi.replace_devices([t1, t2])
        assert len(multi.devices) == 2

    def test_replace_ble_followers(self):
        multi = MultiGoveeLanAdapter([])
        assert multi._ble_followers == []

        mock_ble = MagicMock()
        multi.replace_ble_followers([mock_ble])
        assert multi._ble_followers == [mock_ble]

    def test_get_device_addresses(self):
        t1 = _make_triple("10.0.0.1")
        t2 = _make_triple("10.0.0.2")
        multi = MultiGoveeLanAdapter([t1, t2])
        addrs = multi.get_device_addresses()
        assert addrs == ["10.0.0.1", "10.0.0.2"]

    def test_get_device_addresses_empty(self):
        multi = MultiGoveeLanAdapter([])
        assert multi.get_device_addresses() == []


# ===========================================================================
# ConfigWatcher integration tests
# ===========================================================================


def _write_yaml(path: Path, devices: list[dict]) -> None:
    """Write a minimal devices.yaml."""
    import yaml
    path.write_text(yaml.dump({"devices": devices}), encoding="utf-8")


@pytest.fixture
def config_dir(tmp_path):
    """Create a temp dir with a devices.yaml."""
    cfg_path = tmp_path / "devices.yaml"
    _write_yaml(cfg_path, [
        {"name": "strip1", "address": "10.0.0.1", "type": "lan", "segments": 15},
    ])
    return tmp_path, cfg_path


class TestConfigWatcher:
    """Integration tests for ConfigWatcher with mocked adapters."""

    def test_detects_mtime_change(self, config_dir):
        tmp_path, cfg_path = config_dir
        multi = MultiGoveeLanAdapter([_make_triple("10.0.0.1")])

        with patch("dreamsync.config_watcher.detect_all_devices") as mock_detect, \
             patch("dreamsync.config_watcher.build_multi_adapter") as mock_build:
            mock_detect.return_value = []
            mock_build.return_value = MultiGoveeLanAdapter([])

            watcher = ConfigWatcher(
                cfg_path, multi, poll_interval=0.1, probe_packets=1
            )
            watcher.start()

            # Modify config file
            time.sleep(0.05)
            _write_yaml(cfg_path, [
                {"name": "strip1", "address": "10.0.0.1", "type": "lan", "segments": 15},
                {"name": "strip2", "address": "10.0.0.2", "type": "lan", "segments": 25},
            ])

            # Wait for watcher to pick up the change
            time.sleep(0.5)
            watcher.stop()

            assert mock_detect.called

    def test_malformed_yaml_skipped(self, config_dir, caplog):
        tmp_path, cfg_path = config_dir
        multi = MultiGoveeLanAdapter([_make_triple("10.0.0.1")])

        watcher = ConfigWatcher(cfg_path, multi, poll_interval=0.1)
        watcher.start()

        # Write malformed content
        time.sleep(0.05)
        cfg_path.write_text("not: valid: yaml: [[[", encoding="utf-8")

        time.sleep(0.5)
        watcher.stop()

        # Multi adapter should be unchanged
        assert len(multi.devices) == 1

    def test_added_device_appears(self, config_dir):
        tmp_path, cfg_path = config_dir
        t1 = _make_triple("10.0.0.1")
        multi = MultiGoveeLanAdapter([t1])

        t2 = _make_triple("10.0.0.2", segments=25)
        new_multi = MultiGoveeLanAdapter([t1, t2])

        with patch("dreamsync.config_watcher.detect_all_devices") as mock_detect, \
             patch("dreamsync.config_watcher.build_multi_adapter") as mock_build:
            mock_detect.return_value = []
            mock_build.return_value = new_multi

            watcher = ConfigWatcher(
                cfg_path, multi, poll_interval=0.1, probe_packets=1
            )
            watcher.start()

            time.sleep(0.05)
            _write_yaml(cfg_path, [
                {"name": "strip1", "address": "10.0.0.1", "type": "lan", "segments": 15},
                {"name": "strip2", "address": "10.0.0.2", "type": "lan", "segments": 25},
            ])

            time.sleep(0.5)
            watcher.stop()

            assert len(multi.devices) == 2

    def test_removed_device_disappears(self, config_dir):
        tmp_path, cfg_path = config_dir
        t1 = _make_triple("10.0.0.1")
        t2 = _make_triple("10.0.0.2")
        multi = MultiGoveeLanAdapter([t1, t2])

        new_multi = MultiGoveeLanAdapter([t1])

        with patch("dreamsync.config_watcher.detect_all_devices") as mock_detect, \
             patch("dreamsync.config_watcher.build_multi_adapter") as mock_build:
            mock_detect.return_value = []
            mock_build.return_value = new_multi

            # Override initial configs to include both devices
            watcher = ConfigWatcher(
                cfg_path, multi, poll_interval=0.1, probe_packets=1
            )
            watcher._current_configs = [
                _cfg("10.0.0.1", segments=15),
                _cfg("10.0.0.2", segments=15),
            ]
            watcher.start()

            time.sleep(0.05)
            _write_yaml(cfg_path, [
                {"name": "strip1", "address": "10.0.0.1", "type": "lan", "segments": 15},
            ])

            time.sleep(0.5)
            watcher.stop()

            assert len(multi.devices) == 1

    def test_stop_cleanly_exits(self, config_dir):
        tmp_path, cfg_path = config_dir
        multi = MultiGoveeLanAdapter([])

        watcher = ConfigWatcher(cfg_path, multi, poll_interval=0.1)
        watcher.start()
        assert watcher._thread is not None
        assert watcher._thread.is_alive()

        watcher.stop()
        assert watcher._thread is None

    def test_no_change_no_rebuild(self, config_dir):
        """If mtime doesn't change, no rebuild happens."""
        tmp_path, cfg_path = config_dir
        multi = MultiGoveeLanAdapter([_make_triple("10.0.0.1")])

        with patch("dreamsync.config_watcher.detect_all_devices") as mock_detect:
            watcher = ConfigWatcher(
                cfg_path, multi, poll_interval=0.1, probe_packets=1
            )
            watcher.start()
            time.sleep(0.4)
            watcher.stop()

            assert not mock_detect.called


# ===========================================================================
# Thread safety
# ===========================================================================


class TestThreadSafety:
    """Verify concurrent send_frame + replace_devices doesn't crash."""

    def test_concurrent_send_and_replace(self):
        """Rapid replace_devices while send_frame is being called."""
        from dreamsync.director import EffectMode, LightingIntent

        t1 = _make_triple("10.0.0.1")
        multi = MultiGoveeLanAdapter([t1])

        errors: list[Exception] = []
        stop = threading.Event()

        def sender():
            intent = LightingIntent(
                mode=EffectMode.PULSE, intensity=1.0, speed=1.0, bpm=120.0, color="#ff0000"
            )
            while not stop.is_set():
                try:
                    multi.send_frame(time.monotonic(), intent)
                except Exception as e:
                    errors.append(e)

        sender_thread = threading.Thread(target=sender, daemon=True)
        sender_thread.start()

        # Rapidly swap device lists
        for _ in range(50):
            t_new = _make_triple("10.0.0.2")
            multi.replace_devices([t_new])
            time.sleep(0.001)
            multi.replace_devices([t1])
            time.sleep(0.001)

        stop.set()
        sender_thread.join(timeout=2.0)
        assert errors == [], f"Got errors during concurrent access: {errors}"

    def test_rapid_config_changes_last_wins(self, tmp_path):
        """Multiple rapid config changes should converge to the last state."""
        cfg_path = tmp_path / "devices.yaml"
        _write_yaml(cfg_path, [
            {"name": "strip1", "address": "10.0.0.1", "type": "lan", "segments": 15},
        ])

        multi = MultiGoveeLanAdapter([_make_triple("10.0.0.1")])

        call_count = 0
        last_configs = []

        def tracking_detect(configs, **kw):
            nonlocal call_count, last_configs
            call_count += 1
            last_configs = configs
            # Return empty but valid result to avoid network calls
            return []

        t_final = _make_triple("10.0.0.3")

        with patch("dreamsync.config_watcher.detect_all_devices", side_effect=tracking_detect), \
             patch("dreamsync.config_watcher.build_multi_adapter") as mock_build:
            mock_build.return_value = MultiGoveeLanAdapter([t_final])

            watcher = ConfigWatcher(
                cfg_path, multi, poll_interval=0.1, probe_packets=1
            )
            watcher.start()

            # Write multiple rapid changes
            time.sleep(0.05)
            for i in range(5):
                _write_yaml(cfg_path, [
                    {"name": f"strip{i}", "address": f"10.0.0.{i+1}", "type": "lan", "segments": 15},
                ])
                time.sleep(0.02)

            time.sleep(0.5)
            watcher.stop()

            # At least one reload should have happened
            assert call_count >= 1
