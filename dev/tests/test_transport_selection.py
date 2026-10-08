"""Physical identity, validated selection and one-writer fallback behavior."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from dreamsync.output import adaptive_transport as adaptive
from dreamsync.output.auto_detect import (
    DeviceConfig, build_multi_adapter, detect_all_devices, load_device_config, save_device_config,
)
from dreamsync.output.transport_selection import candidates_for, validate_transport_configs


@pytest.fixture
def config():
    return DeviceConfig(name="paired", address="10.0.0.1", type="lan", max_fps=30,
                        segments=12, transport="razer", protocol="segment",
                        device_id="39:8A:DD:6E:05:86:6A:53", lan_address="10.0.0.1",
                        ble_address="DD:6E:05:86:6A:53", transport_policy="auto",
                        lan_validated_fps=30, ble_validated_fps=3)


def test_config_roundtrip_and_duplicate_ownership(config, tmp_path):
    path = tmp_path / "devices.yaml"
    save_device_config(path, [config])
    assert load_device_config(path) == [config]
    with pytest.raises(ValueError, match="belongs to both"):
        validate_transport_configs([config, DeviceConfig("duplicate", config.ble_address, type="ble")])


@pytest.mark.parametrize("changes", [
    {"device_id": None}, {"lan_address": None}, {"ble_address": None},
    {"protocol": None}, {"transport": None},
    {"lan_validated_fps": None, "ble_validated_fps": None},
    {"ble_validated_fps": float("nan")},
])
def test_ambiguous_or_unmeasured_auto_rejected(config, changes):
    with pytest.raises(ValueError):
        validate_transport_configs([replace(config, **changes)])


def test_prefer_validated_lan_even_when_ble_has_higher_validated_rate(config):
    assert [c.kind for c in candidates_for(config)] == ["lan", "ble"]
    assert candidates_for(replace(config, lan_validated_fps=2))[0].kind == "lan"
    assert candidates_for(replace(config, ble_validated_fps=20))[1].validated_fps == 3
    assert candidates_for(replace(config, lan_validated_fps=3))[0].kind == "lan"
    assert [c.kind for c in candidates_for(replace(config, ble_validated_fps=None))] == ["lan"]


def test_auto_builds_one_logical_renderer_without_transient_ble_probe(config, monkeypatch):
    from dreamsync.output import auto_detect
    def forbidden(*args, **kwargs):
        raise AssertionError("auto mode must reserve connection ownership for its writer")
    monkeypatch.setattr(auto_detect, "probe_ble_device", forbidden)
    detected = detect_all_devices([config])
    assert detected[0].connection_type == "auto"
    multi = build_multi_adapter(detected)
    assert len(multi.devices) == 1
    assert multi._ble_followers == []
    assert multi.devices[0][0].config.device_ip == config.address
    multi.shutdown()


def make_router(config, *, verified=True):
    events = []
    now = [0.0]
    available = [verified]
    created = {}

    class Lan:
        last_send_ok = True
        send_error_count = 0
        def __init__(self, cfg):
            self.config = cfg
            self.fail = False
            self.rate_limited = False
            created["lan"] = self
        def turn_on(self):
            events.append("lan-on")
        def turn_off(self):
            events.append("lan-off")
        def set_brightness(self, value):
            events.append("lan-brightness")
        def send_frame(self, colors):
            events.append("lan-frame")
            if self.rate_limited:
                return False
            self.last_send_ok = not self.fail
            self.send_error_count += int(self.fail)
            return not self.fail
        def release_stream(self):
            events.append("lan-release")

    class Ble:
        connected = True
        worker_running = False
        def __init__(self, cfg):
            self.config = cfg
            self.refuse_stop = False
            created["ble"] = self
        def start(self):
            events.append("ble-start")
            self.worker_running = True
        def stop(self):
            events.append("ble-stop")
            self.worker_running = self.refuse_stop
        def send_segment_colors(self, colors, brightness):
            events.append("ble-frame")
        def health_snapshot(self):
            return {"status": "online" if self.connected else "degraded"}

    def verify():
        if isinstance(available[0], Exception):
            raise available[0]
        return available[0]
    router = adaptive.AdaptiveGoveeAdapter(config, lan_factory=Lan, ble_factory=Ble,
                                         verify_lan=verify, clock=lambda: now[0])
    router._stop_event.clear()
    assert router._try_next("activation")
    return SimpleNamespace(router=router, events=events, created=created, now=now, available=available)


def test_three_lan_misses_release_before_ble_and_never_switch_back(config):
    test = make_router(config)
    try:
        assert test.router.health_snapshot()["active_transport"] == "lan"
        test.available[0] = False
        for _ in range(2):
            test.router.check_health()
        assert "ble-start" not in test.events
        test.router.check_health()
        assert test.events.index("lan-release") < test.events.index("ble-start")
        assert test.router.send_frame([(1, 2, 3)])
        assert test.events[-1] == "ble-frame"
        test.available[0] = True
        test.now[0] = 100
        test.router.check_health()
        assert test.router.health_snapshot()["active_transport"] == "ble"
        assert test.router.config.device_ip == config.address
    finally:
        test.router.close()
    assert test.events[-1] == "ble-stop"


def test_local_errors_only_count_attempts_not_rate_limited_calls(config):
    test = make_router(config)
    try:
        lan = test.created["lan"]
        lan.fail = True
        assert not test.router.send_frame([(1, 2, 3)])
        lan.rate_limited = True
        for _ in range(20):
            test.router.send_frame([(1, 2, 3)])
        test.router.check_health()
        assert "ble-start" not in test.events
        lan.rate_limited = False
        for _ in range(2):
            test.router.send_frame([(1, 2, 3)])
        test.available[0] = OSError("listener busy")
        test.router.check_health()
        assert "ble-start" in test.events
    finally:
        test.router.close()


def test_probe_unavailable_does_not_infer_offline(config):
    test = make_router(config)
    try:
        test.available[0] = OSError("listener busy")
        for _ in range(5):
            test.router.check_health()
        assert "ble-start" not in test.events
        assert "unavailable" in test.router.health_snapshot()["error"]
    finally:
        test.router.close()


def test_no_unvalidated_fallback(config):
    test = make_router(replace(config, ble_validated_fps=None))
    try:
        test.available[0] = False
        for _ in range(3):
            test.router.check_health()
        assert "ble-start" not in test.events
        assert "No available validated fallback" in test.router.health_snapshot()["error"]
    finally:
        test.router.close()


def test_close_retains_ownership_of_a_writer_that_did_not_stop(config):
    test = make_router(config, verified=False)
    try:
        assert test.router.health_snapshot()["active_transport"] == "ble"
        test.created["ble"].refuse_stop = True
        with pytest.raises(RuntimeError, match="did not stop"):
            test.router.close()
        assert "lan-on" not in test.events
        assert test.router.health_snapshot()["active_transport"] == "ble"
        test.created["ble"].refuse_stop = False
    finally:
        test.router.close()


def test_initial_lan_failure_uses_ble_with_setup_grace_and_no_flapping(config):
    test = make_router(config, verified=False)
    try:
        test.created["ble"].connected = False
        for _ in range(4):
            test.router.check_health()
        assert "lan-on" not in test.events
        assert "No available validated fallback" not in test.router.health_snapshot()["error"]
        test.now[0] = 100
        test.available[0] = True
        for _ in range(3):
            test.router.check_health()
        assert "lan-on" not in test.events
        assert "No available validated fallback" in test.router.health_snapshot()["error"]
    finally:
        test.router.close()


def test_lan_replies_must_match_physical_id(config, monkeypatch):
    monkeypatch.setattr(adaptive, "probe_devices", lambda *_args, **_kwargs: [
        SimpleNamespace(ip=config.lan_address, device_id="different-light"),
    ])
    assert not adaptive.identity_replies(config)


@pytest.mark.parametrize("response_times", [[250, 250, 250], [20, 150, 20, 150]])
def test_repeated_slow_or_inconsistent_local_replies_fall_back(config, response_times):
    test = make_router(config)
    try:
        for rtt in response_times[:-1]:
            test.available[0] = adaptive.LanObservation(True, rtt)
            test.router.check_health()
        assert "ble-start" not in test.events
        test.available[0] = adaptive.LanObservation(True, response_times[-1])
        test.router.check_health()
        assert "ble-start" in test.events
    finally:
        test.router.close()


def test_single_slow_reply_recovers_without_fallback(config):
    test = make_router(config)
    try:
        for rtt in [250, 20, 20, 20]:
            test.available[0] = adaptive.LanObservation(True, rtt)
            test.router.check_health()
        assert "ble-start" not in test.events
    finally:
        test.router.close()


def test_direct_probe_rtt_accepts_variable_reply_port(config, monkeypatch):
    import json
    from unittest.mock import MagicMock
    from dreamsync.output import discovery
    listener, sender = MagicMock(), MagicMock()
    listener.recvfrom.return_value = (json.dumps({"msg": {"cmd": "scan", "data": {
        "device": config.device_id, "sku": "H612F"}}}).encode(), (config.lan_address, 54321))
    monkeypatch.setattr(discovery.socket, "socket", MagicMock(side_effect=[listener, sender]))
    ticks = iter([100.0, 100.01, 100.02, 100.03, 100.05])
    monkeypatch.setattr(discovery, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    reply, = discovery.probe_devices([config.lan_address])
    assert reply.device_id == config.device_id
    assert reply.response_ms == pytest.approx(50.0)
    listener.close.assert_called_once()
    sender.close.assert_called_once()


def test_production_ble_probe_also_caps_at_three_hz(monkeypatch):
    from unittest.mock import AsyncMock
    from dreamsync.output import auto_detect, govee_ble
    client = SimpleNamespace(connect=AsyncMock(), disconnect=AsyncMock(),
                             write_gatt_char=AsyncMock(), is_connected=True)
    monkeypatch.setattr(govee_ble, "_require_bleak", lambda: SimpleNamespace(BleakClient=lambda *_a, **_kw: client))
    monkeypatch.setattr(auto_detect, "time", SimpleNamespace(monotonic=lambda: 0.0))
    sleeps = []
    async def sleep(delay):
        sleeps.append(delay)
    import asyncio
    monkeypatch.setattr(asyncio, "sleep", sleep)
    auto_detect.probe_ble_device("test", num_packets=3, rate_hz=20)
    assert sleeps[1:] == pytest.approx([1 / 3] * 3)


def test_gui_health_reports_selected_transport_without_competing_probe(config, tmp_path):
    from dreamsync.gui.services.device_health_service import DeviceHealthService
    test = make_router(config, verified=False)
    def forbidden(*args, **kwargs):
        raise AssertionError("GUI must use transport-owner health")
    try:
        service = DeviceHealthService(config_loader=lambda *_args: [config], lan_probe=forbidden,
                                      runtime_health_provider=lambda: {config.address: test.router.health_snapshot()})
        entry, = service.refresh_once(tmp_path / "unused.yaml").entries
        assert entry.address == config.address
        assert entry.device_type == "ble"
        assert entry.status == "online"
    finally:
        test.router.close()


def test_gui_assignment_edit_retains_confirmed_pairing_and_protocol(config, tmp_path):
    from dreamsync.gui.services.device_discovery_service import DeviceDiscoveryService
    path = tmp_path / "devices.yaml"
    save_device_config(path, [config])
    edited = DeviceConfig(name="renamed", address=config.address, type="lan", transport="razer")
    DeviceDiscoveryService().upsert_device_config(path, edited)
    result, = load_device_config(path)
    assert result.name == "renamed"
    assert result.transport_policy == "auto"
    assert result.protocol == "segment"
    assert result.ble_address == config.ble_address
    assert result.device_id == config.device_id


def test_shared_lan_query_lock_prevents_reply_listener_competition():
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import time
    from dreamsync.output.discovery import serialize_lan_queries
    state = {"active": 0, "maximum": 0}
    state_lock = threading.Lock()
    @serialize_lan_queries
    def query():
        with state_lock:
            state["active"] += 1
            state["maximum"] = max(state["maximum"], state["active"])
        time.sleep(.01)
        with state_lock:
            state["active"] -= 1
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda _: query(), range(6)))
    assert state["maximum"] == 1
