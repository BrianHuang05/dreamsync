"""Tests for the auto-detection module."""

import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dreamsync.groups.models import GroupDefinition
from dreamsync.output.auto_detect import (
    DeviceConfig,
    DetectedDevice,
    LatencyStats,
    _detect_parallel,
    _detect_sequential,
    _is_ip_address,
    _probe_lan_batch,
    build_multi_adapter,
    classify_role,
    device_config_to_mapping,
    detect_all_devices,
    load_device_config,
    save_device_config,
)
from dreamsync.output.govee_lan import MultiGoveeLanAdapter, TransportMode
from dreamsync.output.roles import DeviceRole
from dreamsync.render import RenderMode
from dreamsync.spatial.models import DevicePlacement, SectionPlacement


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

    def test_device_config_to_mapping_includes_spatial_z(self) -> None:
        cfg = DeviceConfig(
            name="Desk",
            address="10.0.0.1",
            placement=DevicePlacement(x=0.2, y=-0.3, z=0.4),
        )
        mapping = device_config_to_mapping(cfg)

        self.assertEqual(mapping["z"], 0.4)

    def test_save_device_config_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "devices.yaml"
            configs = [
                DeviceConfig(
                    name="Desk",
                    address="10.0.0.1",
                    placement=DevicePlacement(x=0.2, y=-0.3, z=0.4),
                )
            ]
            save_device_config(path, configs)
            loaded = load_device_config(path)

        self.assertEqual(loaded[0].placement, DevicePlacement(x=0.2, y=-0.3, z=0.4))

    def test_save_device_config_round_trip_with_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "devices.yaml"
            configs = [
                DeviceConfig(
                    name="Desk",
                    address="10.0.0.1",
                    placement=DevicePlacement(
                        x=0.0,
                        y=0.0,
                        z=0.0,
                        sections=(
                            SectionPlacement(index=0, x=-1.0, y=0.0, z=-0.5),
                            SectionPlacement(index=1, x=1.0, y=1.0, z=0.5),
                        ),
                    ),
                )
            ]
            save_device_config(path, configs)
            loaded = load_device_config(path)

        assert loaded[0].placement is not None
        self.assertEqual(len(loaded[0].placement.sections), 2)
        self.assertEqual(loaded[0].placement.sections[0].index, 0)
        self.assertEqual(loaded[0].placement.sections[1].z, 0.5)


# ---------------------------------------------------------------------------
# build_multi_adapter tests
# ---------------------------------------------------------------------------


class BuildMultiAdapterTests(unittest.TestCase):
    def test_reachable_hardware_preserves_configured_group_definitions(self) -> None:
        group = GroupDefinition(id="ceiling", name="Ceiling")
        detected = [
            DetectedDevice(
                name="Grouped",
                address="192.168.1.10",
                connection_type="lan",
                latency=LatencyStats(samples=[5.0]),
                role="realtime",
                config=DeviceConfig(
                    name="Grouped",
                    address="192.168.1.10",
                    group_definitions=(group,),
                ),
                transport=TransportMode.PTREAL,
            ),
        ]

        multi = build_multi_adapter(detected)

        self.assertEqual(multi._group_definitions, (group,))

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
        adapter, renderer, role, _bs, placement = multi.devices[0]
        self.assertEqual(adapter.config.device_ip, "192.168.1.10")
        self.assertEqual(adapter.config.segments, 15)
        self.assertEqual(role, DeviceRole.PRIMARY)
        self.assertIsNone(placement)
        self.assertIsNone(multi._spatial_mapper)

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

    def test_all_unreachable_returns_null_adapter(self) -> None:
        from dreamsync.output.null_adapter import NullMultiAdapter

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
        adapter = build_multi_adapter(detected)
        self.assertIsInstance(adapter, NullMultiAdapter)
        self.assertEqual(adapter.devices, [])
        self.assertTrue(adapter.send_frame(0.0, None))

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
        follower = multi._ble_followers[0]
        self.assertIsInstance(follower, tuple)
        self.assertEqual(follower[1], DeviceRole.PRIMARY)
        self.assertIsNotNone(follower[4])

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
        _, _, role, _bs, _placement = multi.devices[0]
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
        _, renderer, _, _bs, _placement = multi.devices[0]
        self.assertEqual(renderer.mode, RenderMode.PULSE)
        self.assertFalse(renderer.mirror)

    def test_spatial_mapper_enabled_when_config_has_placement(self) -> None:
        detected = [
            DetectedDevice(
                name="Placed",
                address="192.168.1.10",
                connection_type="lan",
                latency=LatencyStats(samples=[5.0]),
                role="realtime",
                config=DeviceConfig(
                    name="Placed",
                    address="192.168.1.10",
                    placement=DevicePlacement(x=0.2, y=0.3, z=-0.4),
                ),
                transport=TransportMode.PTREAL,
            ),
        ]
        multi = build_multi_adapter(detected)
        self.assertIsNotNone(multi._spatial_mapper)
        self.assertTrue(multi._spatial_mapper.enabled)


# ---------------------------------------------------------------------------
# _probe_lan_batch tests
# ---------------------------------------------------------------------------


class ProbeLanBatchTests(unittest.TestCase):
    """Tests for the shared-socket LAN batch prober."""

    def _make_mock_listener(self, responses: dict[str, list[float]]):
        """Build a mock listener that returns canned RTT data.

        *responses* maps IP -> list of RTT offsets (in seconds) at which
        the device "responds".  The mock returns one response per recvfrom
        call.
        """
        call_count = [0]
        flat_responses: list[tuple[bytes, tuple[str, int]]] = []
        for ip, _rtts in responses.items():
            for _ in _rtts:
                flat_responses.append((b'{"msg":{}}', (ip, 4002)))

        def _recvfrom(bufsize):
            if call_count[0] < len(flat_responses):
                resp = flat_responses[call_count[0]]
                call_count[0] += 1
                return resp
            raise socket.timeout("no more data")

        return _recvfrom

    @patch("dreamsync.output.auto_detect.socket.socket")
    @patch("dreamsync.output.auto_detect.time.sleep")
    def test_all_respond(self, mock_sleep, mock_socket_cls) -> None:
        """All IPs get latency samples from shared socket."""
        ips = ["10.0.0.1", "10.0.0.2"]

        listener = MagicMock()
        sender = MagicMock()
        instances = [listener, sender]
        mock_socket_cls.side_effect = lambda *a, **kw: instances.pop(0)

        # Each call to recvfrom returns a response from one IP
        responses = []
        for _ in range(3):  # 3 packets
            for ip in ips:
                responses.append((b'{}', (ip, 4002)))

        call_idx = [0]
        def _recvfrom(bufsize):
            if call_idx[0] < len(responses):
                r = responses[call_idx[0]]
                call_idx[0] += 1
                return r
            raise socket.timeout()
        listener.recvfrom.side_effect = _recvfrom

        result = _probe_lan_batch(ips, num_packets=3, rate_hz=100.0)

        self.assertIn("10.0.0.1", result)
        self.assertIn("10.0.0.2", result)
        self.assertGreater(result["10.0.0.1"].count, 0)
        self.assertGreater(result["10.0.0.2"].count, 0)

    @patch("dreamsync.output.auto_detect.socket.socket")
    @patch("dreamsync.output.auto_detect.time.sleep")
    def test_one_unreachable(self, mock_sleep, mock_socket_cls) -> None:
        """Unreachable IP gets empty samples, others unaffected."""
        ips = ["10.0.0.1", "10.0.0.99"]

        listener = MagicMock()
        sender = MagicMock()
        instances = [listener, sender]
        mock_socket_cls.side_effect = lambda *a, **kw: instances.pop(0)

        # Only 10.0.0.1 responds
        responses = [(b'{}', ("10.0.0.1", 4002))] * 3
        call_idx = [0]
        def _recvfrom(bufsize):
            if call_idx[0] < len(responses):
                r = responses[call_idx[0]]
                call_idx[0] += 1
                return r
            raise socket.timeout()
        listener.recvfrom.side_effect = _recvfrom

        result = _probe_lan_batch(ips, num_packets=3, rate_hz=100.0)

        self.assertGreater(result["10.0.0.1"].count, 0)
        self.assertEqual(result["10.0.0.99"].count, 0)

    @patch("dreamsync.output.auto_detect.socket.socket")
    @patch("dreamsync.output.auto_detect.time.sleep")
    def test_filters_by_ip(self, mock_sleep, mock_socket_cls) -> None:
        """Response from IP A not attributed to IP B."""
        ips = ["10.0.0.1", "10.0.0.2"]

        listener = MagicMock()
        sender = MagicMock()
        instances = [listener, sender]
        mock_socket_cls.side_effect = lambda *a, **kw: instances.pop(0)

        # Only 10.0.0.1 responds, 10.0.0.2 never responds
        responses = [(b'{}', ("10.0.0.1", 4002))] * 2
        call_idx = [0]
        def _recvfrom(bufsize):
            if call_idx[0] < len(responses):
                r = responses[call_idx[0]]
                call_idx[0] += 1
                return r
            raise socket.timeout()
        listener.recvfrom.side_effect = _recvfrom

        result = _probe_lan_batch(ips, num_packets=2, rate_hz=100.0)

        self.assertGreater(result["10.0.0.1"].count, 0)
        self.assertEqual(result["10.0.0.2"].count, 0)

    def test_empty_ips(self) -> None:
        """Calling with empty list returns empty dict."""
        self.assertEqual(_probe_lan_batch([]), {})


# ---------------------------------------------------------------------------
# Parallel vs Sequential detection tests
# ---------------------------------------------------------------------------


class DetectParallelTests(unittest.TestCase):
    """Tests for the parallel detection path."""

    @patch("dreamsync.output.auto_detect._probe_lan_batch")
    def test_parallel_matches_sequential_lan(self, mock_batch) -> None:
        """Same classification results in parallel vs sequential for LAN."""
        configs = [
            DeviceConfig(name="A", address="10.0.0.1"),
            DeviceConfig(name="B", address="10.0.0.2"),
        ]
        mock_batch.return_value = {
            "10.0.0.1": LatencyStats(samples=[3.0, 4.0, 5.0]),
            "10.0.0.2": LatencyStats(samples=[2.0, 3.0]),
        }

        result = _detect_parallel(configs, 5, 10, 20.0, 10.0)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].role, "realtime")
        self.assertEqual(result[1].role, "realtime")
        self.assertEqual(result[0].connection_type, "lan")
        self.assertEqual(result[1].connection_type, "lan")

    @patch("dreamsync.output.auto_detect.probe_ble_device")
    @patch("dreamsync.output.auto_detect._probe_lan_batch")
    def test_ble_probe_failure_isolated(self, mock_batch, mock_ble) -> None:
        """One BLE failure doesn't crash other probes."""
        configs = [
            DeviceConfig(name="LAN", address="10.0.0.1"),
            DeviceConfig(name="BLE-OK", address="AA:BB:CC:DD:EE:01", type="ble"),
            DeviceConfig(name="BLE-Bad", address="AA:BB:CC:DD:EE:02", type="ble"),
        ]
        mock_batch.return_value = {
            "10.0.0.1": LatencyStats(samples=[5.0]),
        }

        def ble_side_effect(addr, protocol=None, num_packets=10, rate_hz=10.0):
            if addr == "AA:BB:CC:DD:EE:01":
                return LatencyStats(samples=[4.0, 5.0, 6.0])
            raise ConnectionError("BLE connect timeout")

        mock_ble.side_effect = ble_side_effect

        result = _detect_parallel(configs, 5, 10, 20.0, 10.0)

        self.assertEqual(len(result), 3)
        self.assertEqual(result[0].role, "realtime")       # LAN
        self.assertEqual(result[1].role, "realtime")       # BLE-OK (4-6ms median)
        self.assertEqual(result[2].role, "unreachable")    # BLE-Bad

    @patch("dreamsync.output.auto_detect.probe_ble_device")
    @patch("dreamsync.output.auto_detect._probe_lan_batch")
    def test_ble_parallel_max_workers(self, mock_batch, mock_ble) -> None:
        """Thread pool caps at 4 workers for BLE (6 devices -> max_workers=4)."""
        configs = [
            DeviceConfig(name=f"BLE-{i}", address=f"AA:BB:CC:DD:EE:{i:02X}", type="ble")
            for i in range(6)
        ]
        mock_batch.return_value = {}
        mock_ble.return_value = LatencyStats(samples=[5.0])

        # Capture the max_workers actually used
        captured = {}
        _orig_pool = __import__("concurrent.futures", fromlist=["ThreadPoolExecutor"]).ThreadPoolExecutor

        class _SpyPool(_orig_pool):
            def __init__(self, **kwargs):
                captured["max_workers"] = kwargs.get("max_workers")
                super().__init__(**kwargs)

        with patch("dreamsync.output.auto_detect.ThreadPoolExecutor", _SpyPool):
            _detect_parallel(configs, 5, 10, 20.0, 10.0)

        self.assertEqual(captured["max_workers"], 4)


# ---------------------------------------------------------------------------
# detect_all_devices default parameter tests
# ---------------------------------------------------------------------------


class DetectAllDevicesDefaultsTests(unittest.TestCase):
    """Tests for the fast default parameters in detect_all_devices."""

    @patch("dreamsync.output.auto_detect._detect_parallel")
    def test_fast_defaults_lan(self, mock_par) -> None:
        """Default LAN probe uses 5 packets at 20 Hz."""
        configs = [
            DeviceConfig(name="A", address="10.0.0.1"),
            DeviceConfig(name="B", address="10.0.0.2"),
        ]
        mock_par.return_value = []

        detect_all_devices(configs)

        mock_par.assert_called_once_with(configs, 5, 10, 20.0, 10.0)

    @patch("dreamsync.output.auto_detect._detect_parallel")
    def test_fast_defaults_ble(self, mock_par) -> None:
        """Default BLE probe uses 10 packets at 10 Hz."""
        configs = [
            DeviceConfig(name="A", address="10.0.0.1"),
            DeviceConfig(name="B", address="AA:BB:CC:DD:EE:FF", type="ble"),
        ]
        mock_par.return_value = []

        detect_all_devices(configs)

        # Second arg is lan_packets=5, third is ble_packets=10
        mock_par.assert_called_once_with(configs, 5, 10, 20.0, 10.0)

    @patch("dreamsync.output.auto_detect._detect_sequential")
    def test_probe_packets_override(self, mock_seq) -> None:
        """--probe-packets 50 overrides both LAN and BLE counts."""
        configs = [
            DeviceConfig(name="A", address="10.0.0.1"),
        ]
        mock_seq.return_value = []

        # num_packets != 100 triggers override; parallel=False for single device
        detect_all_devices(configs, num_packets=50, rate_hz=7.0)

        mock_seq.assert_called_once_with(configs, 50, 50, 7.0, 7.0)

    @patch("dreamsync.output.auto_detect._detect_sequential")
    def test_parallel_false_uses_sequential(self, mock_seq) -> None:
        """parallel=False forces sequential detection."""
        configs = [
            DeviceConfig(name="A", address="10.0.0.1"),
            DeviceConfig(name="B", address="10.0.0.2"),
        ]
        mock_seq.return_value = []

        detect_all_devices(configs, parallel=False)

        mock_seq.assert_called_once()

    @patch("dreamsync.output.auto_detect._detect_sequential")
    def test_single_device_uses_sequential(self, mock_seq) -> None:
        """Single device skips parallel even when parallel=True."""
        configs = [DeviceConfig(name="A", address="10.0.0.1")]
        mock_seq.return_value = []

        detect_all_devices(configs, parallel=True)

        mock_seq.assert_called_once()

    @patch("dreamsync.output.auto_detect._detect_parallel")
    @patch("dreamsync.output.auto_detect._detect_without_ble_probes")
    def test_probe_free_ble_mode_uses_only_the_long_lived_adapter(
        self, mock_without_ble, mock_parallel
    ) -> None:
        """GUI playback must not create a disposable Bleak probe loop first."""
        configs = [DeviceConfig(name="BLE", address="AA:BB:CC:DD:EE:FF", type="ble")]
        mock_without_ble.return_value = []

        detect_all_devices(configs, probe_ble=False)

        mock_without_ble.assert_called_once_with(configs, 5, 20.0)
        mock_parallel.assert_not_called()


if __name__ == "__main__":
    unittest.main()
