"""Tests for device_health: DeviceHealth, DeviceHealthMonitor."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from dreamsync.device_health import (
    DeviceHealth,
    DeviceHealthMonitor,
    OFFLINE_THRESHOLD,
    ONLINE_THRESHOLD,
    ROLE_RECLASS_THRESHOLD,
    classify_role_from_window,
)
from dreamsync.output.auto_detect import DeviceConfig, LatencyStats
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

def _cfg(address: str, name: str = "") -> DeviceConfig:
    return DeviceConfig(name=name or address, address=address)


def _make_triple(ip: str, segments: int = 15):
    config = GoveeLanConfig(device_ip=ip, segments=segments, transport=TransportMode.PTREAL)
    adapter = GoveeLanAdapter(config, transport=lambda *a: None)
    renderer = SegmentRenderer(segments=segments, mode=RenderMode.SCROLL)
    return (adapter, renderer, DeviceRole.PRIMARY)


def _make_monitor(
    ips: list[str],
    *,
    probe_interval: float = 0.1,
    **kwargs,
) -> tuple[MultiGoveeLanAdapter, DeviceHealthMonitor]:
    """Build a MultiGoveeLanAdapter + DeviceHealthMonitor for testing."""
    triples = [_make_triple(ip) for ip in ips]
    multi = MultiGoveeLanAdapter(triples)
    configs = [_cfg(ip) for ip in ips]
    monitor = DeviceHealthMonitor(
        multi, configs, probe_interval=probe_interval, **kwargs,
    )
    return multi, monitor


# ===========================================================================
# DeviceHealth dataclass tests
# ===========================================================================


class TestDeviceHealth:
    def test_defaults(self):
        h = DeviceHealth(address="10.0.0.1", role="realtime")
        assert h.status == "online"
        assert h.consecutive_failures == 0
        assert h.consecutive_successes == 0
        assert h.offline_since is None
        assert h.latency_history == []

    def test_mutable(self):
        h = DeviceHealth(address="10.0.0.1", role="realtime")
        h.status = "offline"
        h.consecutive_failures = 3
        assert h.status == "offline"
        assert h.consecutive_failures == 3


# ===========================================================================
# classify_role_from_window tests
# ===========================================================================


class TestClassifyRoleFromWindow:
    def test_empty(self):
        assert classify_role_from_window([]) == "unreachable"

    def test_realtime(self):
        assert classify_role_from_window([5.0, 8.0, 10.0]) == "realtime"

    def test_follower(self):
        assert classify_role_from_window([50.0, 60.0, 70.0]) == "follower"

    def test_slow(self):
        assert classify_role_from_window([300.0, 400.0]) == "slow"

    def test_boundary_20ms(self):
        # median of [20.0] = 20.0, which is <= 200 → follower
        assert classify_role_from_window([20.0]) == "follower"

    def test_boundary_below_20ms(self):
        assert classify_role_from_window([19.9]) == "realtime"


# ===========================================================================
# GoveeLanAdapter paused flag tests
# ===========================================================================


class TestAdapterPaused:
    def test_paused_adapter_skips_send(self):
        sent = []
        config = GoveeLanConfig(device_ip="10.0.0.1", segments=5, transport=TransportMode.PTREAL)
        adapter = GoveeLanAdapter(config, transport=lambda p, i, pt: sent.append(p))
        adapter.paused = True

        result = adapter.send_frame([(255, 0, 0)] * 5)
        assert result is False
        assert len(sent) == 0

    def test_unpaused_adapter_resumes(self):
        sent = []
        config = GoveeLanConfig(device_ip="10.0.0.1", segments=5, transport=TransportMode.PTREAL)
        adapter = GoveeLanAdapter(config, transport=lambda p, i, pt: sent.append(p))

        adapter.paused = True
        assert adapter.send_frame([(255, 0, 0)] * 5) is False

        adapter.paused = False
        assert adapter.send_frame([(255, 0, 0)] * 5) is True
        assert len(sent) == 1

    def test_send_frame_tracks_last_send_ok(self):
        config = GoveeLanConfig(device_ip="10.0.0.1", segments=5, transport=TransportMode.PTREAL)
        adapter = GoveeLanAdapter(config, transport=lambda *a: None)
        assert adapter.last_send_ok is True

        adapter.send_frame([(255, 0, 0)] * 5)
        assert adapter.last_send_ok is True

    def test_send_frame_tracks_failure(self):
        def failing_transport(*a):
            raise OSError("network down")

        config = GoveeLanConfig(device_ip="10.0.0.1", segments=5, transport=TransportMode.PTREAL)
        adapter = GoveeLanAdapter(config, transport=failing_transport)
        adapter.send_frame([(255, 0, 0)] * 5)
        assert adapter.last_send_ok is False


# ===========================================================================
# DeviceHealthMonitor unit tests (with mocked probes)
# ===========================================================================


class TestHealthMonitorInit:
    def test_initializes_health_for_all_devices(self):
        multi, monitor = _make_monitor(["10.0.0.1", "10.0.0.2"])
        health = monitor.get_all_health()
        assert "10.0.0.1" in health
        assert "10.0.0.2" in health
        assert health["10.0.0.1"].status == "online"
        assert health["10.0.0.2"].role == "primary"

    def test_get_health_returns_none_for_unknown(self):
        multi, monitor = _make_monitor(["10.0.0.1"])
        assert monitor.get_health("10.0.0.99") is None


class TestHealthMonitorProbe:
    """Tests that use mocked probe_lan_device to simulate probe results."""

    @patch("dreamsync.device_health.probe_lan_device")
    def test_healthy_device_stays_online(self, mock_probe):
        mock_probe.return_value = LatencyStats(samples=[5.0, 6.0, 7.0])
        multi, monitor = _make_monitor(["10.0.0.1"])

        # Manually call the probe method
        health = monitor.get_health("10.0.0.1")
        monitor._probe_device("10.0.0.1", health)

        assert health.status == "online"
        assert health.consecutive_successes == 1
        assert health.consecutive_failures == 0
        assert len(health.latency_history) == 1

    @patch("dreamsync.device_health.probe_lan_device")
    def test_offline_after_threshold(self, mock_probe):
        mock_probe.return_value = LatencyStats(samples=[])
        multi, monitor = _make_monitor(["10.0.0.1"])
        offline_callback = MagicMock()
        monitor._on_device_offline = offline_callback

        health = monitor.get_health("10.0.0.1")

        # Probe N times with no response
        for i in range(OFFLINE_THRESHOLD):
            monitor._probe_device("10.0.0.1", health)

        assert health.status == "offline"
        assert health.consecutive_failures == OFFLINE_THRESHOLD
        assert health.offline_since is not None
        offline_callback.assert_called_once_with("10.0.0.1")

    @patch("dreamsync.device_health.probe_lan_device")
    def test_adapter_paused_on_offline(self, mock_probe):
        mock_probe.return_value = LatencyStats(samples=[])
        multi, monitor = _make_monitor(["10.0.0.1"])

        health = monitor.get_health("10.0.0.1")
        adapter = multi.devices[0][0]
        assert adapter.paused is False

        for _ in range(OFFLINE_THRESHOLD):
            monitor._probe_device("10.0.0.1", health)

        assert adapter.paused is True

    @patch("dreamsync.device_health.probe_lan_device")
    def test_online_after_recovery(self, mock_probe):
        multi, monitor = _make_monitor(["10.0.0.1"])
        online_callback = MagicMock()
        monitor._on_device_online = online_callback

        health = monitor.get_health("10.0.0.1")

        # Drive to offline
        mock_probe.return_value = LatencyStats(samples=[])
        for _ in range(OFFLINE_THRESHOLD):
            monitor._probe_device("10.0.0.1", health)
        assert health.status == "offline"

        # Recover
        mock_probe.return_value = LatencyStats(samples=[8.0, 9.0])
        for _ in range(ONLINE_THRESHOLD):
            monitor._probe_device("10.0.0.1", health)

        assert health.status == "online"
        assert health.offline_since is None
        assert online_callback.called

    @patch("dreamsync.device_health.probe_lan_device")
    def test_adapter_unpaused_on_recovery(self, mock_probe):
        multi, monitor = _make_monitor(["10.0.0.1"])

        health = monitor.get_health("10.0.0.1")
        adapter = multi.devices[0][0]

        # Go offline
        mock_probe.return_value = LatencyStats(samples=[])
        for _ in range(OFFLINE_THRESHOLD):
            monitor._probe_device("10.0.0.1", health)
        assert adapter.paused is True

        # Come back online
        mock_probe.return_value = LatencyStats(samples=[5.0])
        for _ in range(ONLINE_THRESHOLD):
            monitor._probe_device("10.0.0.1", health)
        assert adapter.paused is False

    @patch("dreamsync.device_health.probe_lan_device")
    def test_no_flapping(self, mock_probe):
        """A single success during an offline streak doesn't trigger online."""
        multi, monitor = _make_monitor(["10.0.0.1"])

        health = monitor.get_health("10.0.0.1")

        # Go offline
        mock_probe.return_value = LatencyStats(samples=[])
        for _ in range(OFFLINE_THRESHOLD):
            monitor._probe_device("10.0.0.1", health)
        assert health.status == "offline"

        # Single success — not enough
        mock_probe.return_value = LatencyStats(samples=[5.0])
        monitor._probe_device("10.0.0.1", health)
        assert health.status == "offline"  # still offline

        # Another failure
        mock_probe.return_value = LatencyStats(samples=[])
        monitor._probe_device("10.0.0.1", health)
        assert health.status == "offline"

    @patch("dreamsync.device_health.probe_lan_device")
    def test_role_reclassification(self, mock_probe):
        """Sustained latency shift triggers reclassification."""
        multi, monitor = _make_monitor(["10.0.0.1"])
        role_callback = MagicMock()
        monitor._on_role_changed = role_callback

        health = monitor.get_health("10.0.0.1")
        assert health.role == "primary"

        # Feed high-latency probes to trigger follower classification
        mock_probe.return_value = LatencyStats(samples=[80.0, 90.0, 85.0])
        for _ in range(ROLE_RECLASS_THRESHOLD):
            monitor._probe_device("10.0.0.1", health)

        assert health.role == "follower"
        role_callback.assert_called_once_with("10.0.0.1", "primary", "follower")

    @patch("dreamsync.device_health.probe_lan_device")
    def test_role_no_flap(self, mock_probe):
        """Brief latency spike doesn't trigger reclassification."""
        multi, monitor = _make_monitor(["10.0.0.1"])
        role_callback = MagicMock()
        monitor._on_role_changed = role_callback

        health = monitor.get_health("10.0.0.1")

        # One high-latency probe, then back to normal
        mock_probe.return_value = LatencyStats(samples=[80.0])
        monitor._probe_device("10.0.0.1", health)

        mock_probe.return_value = LatencyStats(samples=[5.0])
        monitor._probe_device("10.0.0.1", health)
        monitor._probe_device("10.0.0.1", health)

        # Should not have reclassified
        role_callback.assert_not_called()

    @patch("dreamsync.device_health.probe_lan_device")
    def test_offline_not_fired_twice(self, mock_probe):
        """Once offline, additional failures don't re-fire the callback."""
        multi, monitor = _make_monitor(["10.0.0.1"])
        offline_callback = MagicMock()
        monitor._on_device_offline = offline_callback

        health = monitor.get_health("10.0.0.1")
        mock_probe.return_value = LatencyStats(samples=[])

        for _ in range(OFFLINE_THRESHOLD + 3):
            monitor._probe_device("10.0.0.1", health)

        # Only called once when first going offline
        offline_callback.assert_called_once()


class TestHealthMonitorThread:
    """Tests for the daemon thread lifecycle."""

    @patch("dreamsync.device_health.probe_lan_device")
    def test_start_and_stop(self, mock_probe):
        mock_probe.return_value = LatencyStats(samples=[5.0])
        multi, monitor = _make_monitor(["10.0.0.1"], probe_interval=0.05)

        monitor.start()
        assert monitor._thread is not None
        assert monitor._thread.is_alive()

        time.sleep(0.15)  # let it run a couple cycles
        monitor.stop()
        assert monitor._thread is None

    @patch("dreamsync.device_health.probe_lan_device")
    def test_stop_joins_thread(self, mock_probe):
        mock_probe.return_value = LatencyStats(samples=[5.0])
        multi, monitor = _make_monitor(["10.0.0.1"], probe_interval=0.05)

        monitor.start()
        thread = monitor._thread
        monitor.stop()

        assert not thread.is_alive()

    @patch("dreamsync.device_health.probe_lan_device")
    def test_concurrent_probe_and_send(self, mock_probe):
        """Health probe doesn't block frame sending (thread safety)."""
        mock_probe.return_value = LatencyStats(samples=[5.0])
        multi, monitor = _make_monitor(["10.0.0.1"], probe_interval=0.05)

        from dreamsync.director import EffectMode, LightingIntent

        errors = []
        stop = threading.Event()

        def sender():
            intent = LightingIntent(
                mode=EffectMode.PULSE, intensity=1.0, speed=1.0, bpm=120.0, color="#ff0000",
            )
            while not stop.is_set():
                try:
                    multi.send_frame(time.monotonic(), intent)
                except Exception as e:
                    errors.append(e)
                time.sleep(0.001)

        monitor.start()
        sender_thread = threading.Thread(target=sender, daemon=True)
        sender_thread.start()

        time.sleep(0.2)

        stop.set()
        sender_thread.join(timeout=2.0)
        monitor.stop()

        assert errors == []


class TestHealthMonitorDiscovery:
    """Tests for the optional device discovery feature."""

    @patch("dreamsync.device_health.probe_lan_device")
    def test_discovery_finds_new_device(self, mock_probe):
        mock_probe.return_value = LatencyStats(samples=[5.0])
        discovered_callback = MagicMock()
        multi, monitor = _make_monitor(
            ["10.0.0.1"],
            enable_discovery=True,
            discovery_interval=0.0,
            on_device_discovered=discovered_callback,
        )

        mock_device = MagicMock()
        mock_device.ip = "10.0.0.99"
        mock_device.sku = "H6199"

        with patch("dreamsync.output.discovery.scan_devices", return_value=[mock_device]) as mock_scan:
            monitor._run_discovery({"10.0.0.1"})

        discovered_callback.assert_called_once_with(mock_device)

    @patch("dreamsync.device_health.probe_lan_device")
    def test_discovery_ignores_known(self, mock_probe):
        mock_probe.return_value = LatencyStats(samples=[5.0])
        discovered_callback = MagicMock()
        multi, monitor = _make_monitor(
            ["10.0.0.1"],
            enable_discovery=True,
            on_device_discovered=discovered_callback,
        )

        mock_device = MagicMock()
        mock_device.ip = "10.0.0.1"  # already known

        with patch("dreamsync.output.discovery.scan_devices", return_value=[mock_device]):
            monitor._run_discovery({"10.0.0.1"})

        discovered_callback.assert_not_called()


# ===========================================================================
# Telemetry snapshot tests
# ===========================================================================


class TestHealthTelemetrySnapshot:
    def test_health_snapshot_schema(self):
        """get_all_health returns correct structure for telemetry."""
        multi, monitor = _make_monitor(["10.0.0.1", "10.0.0.2"])

        health_all = monitor.get_all_health()
        assert len(health_all) == 2
        for addr, h in health_all.items():
            assert isinstance(h.address, str)
            assert h.status in ("online", "offline", "degraded")
            assert isinstance(h.consecutive_failures, int)
            assert isinstance(h.latency_history, list)
