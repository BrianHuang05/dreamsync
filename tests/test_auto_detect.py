"""Tests for the auto-detection module."""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dreamsync.output.auto_detect import (
    DeviceConfig,
    DetectedDevice,
    LatencyStats,
    _is_ip_address,
    build_multi_adapter,
    classify_role,
    load_device_config,
)
from dreamsync.output.govee_lan import MultiGoveeLanAdapter, TransportMode
from dreamsync.output.roles import DeviceRole
from dreamsync.render import RenderMode


# ---------------------------------------------------------------------------
# _is_ip_address tests
# ---------------------------------------------------------------------------


class IsIpAddressTests(unittest.TestCase):
    def test_ipv4(self) -> None:
        self.assertTrue(_is_ip_address("192.168.1.1"))

    def test_ipv4_zeros(self) -> None:
        self.assertTrue(_is_ip_address("0.0.0.0"))

    def test_ipv4_255(self) -> None:
        self.assertTrue(_is_ip_address("255.255.255.255"))

    def test_ble_mac(self) -> None:
        self.assertFalse(_is_ip_address("AA:BB:CC:DD:EE:FF"))

    def test_hostname(self) -> None:
        self.assertFalse(_is_ip_address("mydevice.local"))

    def test_empty(self) -> None:
        self.assertFalse(_is_ip_address(""))

    def test_ipv6(self) -> None:
        self.assertFalse(_is_ip_address("::1"))

    def test_partial_ip(self) -> None:
        self.assertFalse(_is_ip_address("192.168.1"))


# ---------------------------------------------------------------------------
# LatencyStats tests
# ---------------------------------------------------------------------------


class LatencyStatsTests(unittest.TestCase):
    def test_empty_samples(self) -> None:
        stats = LatencyStats(samples=[])
        self.assertEqual(stats.count, 0)
        self.assertEqual(stats.min_ms, 0.0)
        self.assertEqual(stats.max_ms, 0.0)
        self.assertEqual(stats.median_ms, 0.0)
        self.assertEqual(stats.p95_ms, 0.0)
        self.assertEqual(stats.mean_ms, 0.0)

    def test_single_sample(self) -> None:
        stats = LatencyStats(samples=[10.0])
        self.assertEqual(stats.count, 1)
        self.assertEqual(stats.min_ms, 10.0)
        self.assertEqual(stats.max_ms, 10.0)
        self.assertEqual(stats.median_ms, 10.0)
        self.assertEqual(stats.p95_ms, 10.0)
        self.assertEqual(stats.mean_ms, 10.0)

    def test_multiple_samples(self) -> None:
        stats = LatencyStats(samples=[5.0, 10.0, 15.0, 20.0, 25.0])
        self.assertEqual(stats.count, 5)
        self.assertEqual(stats.min_ms, 5.0)
        self.assertEqual(stats.max_ms, 25.0)
        self.assertEqual(stats.median_ms, 15.0)
        self.assertEqual(stats.mean_ms, 15.0)

    def test_p95_correctness(self) -> None:
        # 20 samples: 1..20
        samples = [float(i) for i in range(1, 21)]
        stats = LatencyStats(samples=samples)
        # 95th percentile index: int(20 * 0.95) = 19, sorted[19] = 20
        self.assertEqual(stats.p95_ms, 20.0)

    def test_p95_small_list(self) -> None:
        stats = LatencyStats(samples=[1.0, 2.0, 3.0])
        # int(3 * 0.95) = 2, sorted[2] = 3.0
        self.assertEqual(stats.p95_ms, 3.0)


# ---------------------------------------------------------------------------
# classify_role tests
# ---------------------------------------------------------------------------


class ClassifyRoleTests(unittest.TestCase):
    def test_unreachable(self) -> None:
        stats = LatencyStats(samples=[])
        self.assertEqual(classify_role(stats), "unreachable")

    def test_realtime(self) -> None:
        stats = LatencyStats(samples=[5.0, 8.0, 10.0, 12.0, 15.0])
        self.assertEqual(classify_role(stats), "realtime")

    def test_realtime_boundary(self) -> None:
        # Median just below 20ms
        stats = LatencyStats(samples=[19.0, 19.5, 19.9])
        self.assertEqual(classify_role(stats), "realtime")

    def test_follower(self) -> None:
        stats = LatencyStats(samples=[50.0, 60.0, 70.0])
        self.assertEqual(classify_role(stats), "follower")

    def test_follower_at_20ms(self) -> None:
        # Exactly 20ms median -> follower
        stats = LatencyStats(samples=[20.0])
        self.assertEqual(classify_role(stats), "follower")

    def test_follower_at_200ms(self) -> None:
        # Exactly 200ms median -> follower
        stats = LatencyStats(samples=[200.0])
        self.assertEqual(classify_role(stats), "follower")

    def test_slow(self) -> None:
        stats = LatencyStats(samples=[300.0, 400.0, 500.0])
        self.assertEqual(classify_role(stats), "slow")

    def test_slow_boundary(self) -> None:
        # Just above 200ms
        stats = LatencyStats(samples=[200.1])
        self.assertEqual(classify_role(stats), "slow")


# ---------------------------------------------------------------------------
# load_device_config tests
# ---------------------------------------------------------------------------


class LoadDeviceConfigTests(unittest.TestCase):
    def test_valid_config(self) -> None:
        yaml_content = {
            "devices": [
                {"name": "Living Room", "address": "192.168.1.10", "segments": 15},
                {"address": "AA:BB:CC:DD:EE:FF", "protocol": "bulb"},
            ]
        }
        with patch("dreamsync.output.auto_detect._require_yaml") as mock_yaml:
            mock_module = MagicMock()
            mock_module.safe_load.return_value = yaml_content
            mock_yaml.return_value = mock_module

            with patch("builtins.open", unittest.mock.mock_open()):
                configs = load_device_config(Path("test.yaml"))

        self.assertEqual(len(configs), 2)
        self.assertEqual(configs[0].name, "Living Room")
        self.assertEqual(configs[0].address, "192.168.1.10")
        self.assertEqual(configs[0].segments, 15)
        self.assertEqual(configs[1].name, "AA:BB:CC:DD:EE:FF")
        self.assertEqual(configs[1].protocol, "bulb")

    def test_missing_address_raises(self) -> None:
        yaml_content = {"devices": [{"name": "Bad Device"}]}
        with patch("dreamsync.output.auto_detect._require_yaml") as mock_yaml:
            mock_module = MagicMock()
            mock_module.safe_load.return_value = yaml_content
            mock_yaml.return_value = mock_module

            with patch("builtins.open", unittest.mock.mock_open()):
                with self.assertRaises(ValueError) as ctx:
                    load_device_config(Path("test.yaml"))
                self.assertIn("address", str(ctx.exception))

    def test_missing_devices_key_raises(self) -> None:
        yaml_content = {"something_else": []}
        with patch("dreamsync.output.auto_detect._require_yaml") as mock_yaml:
            mock_module = MagicMock()
            mock_module.safe_load.return_value = yaml_content
            mock_yaml.return_value = mock_module

            with patch("builtins.open", unittest.mock.mock_open()):
                with self.assertRaises(ValueError) as ctx:
                    load_device_config(Path("test.yaml"))
                self.assertIn("devices", str(ctx.exception))

    def test_defaults_applied(self) -> None:
        yaml_content = {"devices": [{"address": "10.0.0.1"}]}
        with patch("dreamsync.output.auto_detect._require_yaml") as mock_yaml:
            mock_module = MagicMock()
            mock_module.safe_load.return_value = yaml_content
            mock_yaml.return_value = mock_module

            with patch("builtins.open", unittest.mock.mock_open()):
                configs = load_device_config(Path("test.yaml"))

        self.assertEqual(configs[0].name, "10.0.0.1")  # defaults to address
        self.assertEqual(configs[0].type, "auto")
        self.assertEqual(configs[0].segments, 15)
        self.assertIsNone(configs[0].transport)
        self.assertIsNone(configs[0].protocol)
        self.assertIsNone(configs[0].role)
        self.assertEqual(configs[0].max_fps, 5.0)


# ---------------------------------------------------------------------------
# build_multi_adapter tests
# ---------------------------------------------------------------------------


class BuildMultiAdapterTests(unittest.TestCase):
    def test_single_realtime_lan(self) -> None:
        detected = [
            DetectedDevice(
                name="Test",
                address="192.168.1.10",
                connection_type="lan",
                latency=LatencyStats(samples=[5.0, 8.0, 10.0]),
                role="realtime",
                config=DeviceConfig(name="Test", address="192.168.1.10", segments=15),
                transport=TransportMode.PTREAL,
            ),
        ]
        multi = build_multi_adapter(detected)
        self.assertIsInstance(multi, MultiGoveeLanAdapter)
        self.assertEqual(len(multi.devices), 1)
        adapter, renderer, role = multi.devices[0]
        self.assertEqual(adapter.config.device_ip, "192.168.1.10")
        self.assertEqual(adapter.config.segments, 15)
        self.assertEqual(role, DeviceRole.PRIMARY)

    def test_unreachable_skipped(self) -> None:
        detected = [
            DetectedDevice(
                name="Reachable",
                address="192.168.1.10",
                connection_type="lan",
                latency=LatencyStats(samples=[5.0]),
                role="realtime",
                config=DeviceConfig(name="Reachable", address="192.168.1.10"),
                transport=TransportMode.PTREAL,
            ),
            DetectedDevice(
                name="Dead",
                address="192.168.1.99",
                connection_type="unreachable",
                latency=LatencyStats(samples=[]),
                role="unreachable",
                config=DeviceConfig(name="Dead", address="192.168.1.99"),
            ),
        ]
        multi = build_multi_adapter(detected)
        self.assertEqual(len(multi.devices), 1)
        self.assertEqual(multi.devices[0][0].config.device_ip, "192.168.1.10")

    def test_all_unreachable_raises(self) -> None:
        detected = [
            DetectedDevice(
                name="Dead",
                address="192.168.1.99",
                connection_type="unreachable",
                latency=LatencyStats(samples=[]),
                role="unreachable",
                config=DeviceConfig(name="Dead", address="192.168.1.99"),
            ),
        ]
        with self.assertRaises(RuntimeError) as ctx:
            build_multi_adapter(detected)
        self.assertIn("No reachable devices", str(ctx.exception))

    def test_ble_follower_added(self) -> None:
        detected = [
            DetectedDevice(
                name="LAN",
                address="192.168.1.10",
                connection_type="lan",
                latency=LatencyStats(samples=[5.0]),
                role="realtime",
                config=DeviceConfig(name="LAN", address="192.168.1.10"),
                transport=TransportMode.PTREAL,
            ),
            DetectedDevice(
                name="BLE",
                address="AA:BB:CC:DD:EE:FF",
                connection_type="ble",
                latency=LatencyStats(samples=[50.0]),
                role="follower",
                config=DeviceConfig(name="BLE", address="AA:BB:CC:DD:EE:FF"),
                ble_protocol="segment",
            ),
        ]
        multi = build_multi_adapter(detected)
        self.assertEqual(len(multi.devices), 1)  # only LAN device
        self.assertEqual(len(multi._ble_followers), 1)

    def test_custom_role_from_config(self) -> None:
        detected = [
            DetectedDevice(
                name="Accent",
                address="192.168.1.10",
                connection_type="lan",
                latency=LatencyStats(samples=[5.0]),
                role="realtime",
                config=DeviceConfig(name="Accent", address="192.168.1.10", role="accent"),
                transport=TransportMode.RAZER,
            ),
        ]
        multi = build_multi_adapter(detected)
        _, _, role = multi.devices[0]
        self.assertEqual(role, DeviceRole.ACCENT)

    def test_render_mode_propagated(self) -> None:
        detected = [
            DetectedDevice(
                name="Test",
                address="192.168.1.10",
                connection_type="lan",
                latency=LatencyStats(samples=[5.0]),
                role="realtime",
                config=DeviceConfig(name="Test", address="192.168.1.10"),
                transport=TransportMode.PTREAL,
            ),
        ]
        multi = build_multi_adapter(detected, render_mode=RenderMode.PULSE, mirror=False)
        _, renderer, _ = multi.devices[0]
        self.assertEqual(renderer.mode, RenderMode.PULSE)
        self.assertFalse(renderer.mirror)


if __name__ == "__main__":
    unittest.main()
