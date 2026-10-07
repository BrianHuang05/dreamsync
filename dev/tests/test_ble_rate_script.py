"""Config selection and sweep behavior of the manual BLE benchmark."""
import importlib.util
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("command", ["0d", "02"])
def test_rgb_command_changes_only_color_and_checksum(script, command):
    from dreamsync.output.govee_ble import (
        build_ble_bulb_color_packet, build_ble_manual_mode_packet,
    )
    from dreamsync.output.govee_lan import build_ptreal_power_packet, build_ptreal_brightness_packet
    writes = []

    class Client:
        async def write_gatt_char(self, uuid, data, response):
            assert response is False
            writes.append(data)

    adapter = script.MeasuredAdapter(
        script.GoveeBleConfig(address="AA", protocol=script.BleProtocol.BULB),
        bulb_color_command=command,
    )
    controls = [build_ptreal_power_packet(True), build_ptreal_brightness_packet(30),
                build_ble_manual_mode_packet(), script.build_ble_keepalive_packet()]
    color = build_ble_bulb_color_packet(12, 34, 56)

    async def stream():
        # Fresh clients exercise first connection and reconnect behavior.
        for client in [Client(), Client()]:
            for packet in controls + [color]:
                await adapter._ble_write(client, packet)

    asyncio.run(stream())
    for offset in [0, 5]:
        assert writes[offset:offset + 4] == controls
        result = writes[offset + 4]
        assert len(result) == 20
        assert result[:3] == bytes([0x33, 0x05, int(command, 16)])
        assert result[3:19] == color[3:19]
        checksum = 0
        for byte in result:
            checksum ^= byte
        assert checksum == 0
    assert len(adapter.samples) == 2
    assert adapter.session_report(0, float("inf"))["bulb_color_command"] == command


def test_02_rejects_segment_targets_before_hardware(script, monkeypatch, tmp_path):
    monkeypatch.setattr("sys.argv", ["test", "--address", "AA", "--protocol", "segment",
                                     "--bulb-color-command", "02", "--output", str(tmp_path / "out.json")])
    with pytest.raises(SystemExit) as error:
        script.main()
    assert error.value.code == 2


@pytest.fixture
def script():
    path = Path(__file__).parents[1] / "scripts" / "test_ble_update_rates.py"
    spec = importlib.util.spec_from_file_location("ble_rate_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_config_selection_preserves_protocol_and_skips_lan_disabled(script, tmp_path):
    path = tmp_path / "devices.yaml"
    path.write_text('''devices:
  - {name: bulb, address: "AA:BB", type: ble, protocol: bulb, segments: 1}
  - {name: strip, address: "CC:DD", type: auto, protocol: segment, segments: 7}
  - {name: lan, address: "192.168.1.4", type: auto}
  - {name: disabled, address: "EE:FF", type: ble, enabled: false}
''')
    targets = script.load_targets(SimpleNamespace(config=path))
    assert [(t.name, t.protocol.value, t.segments) for t in targets] == [
        ("bulb", "bulb", 1), ("strip", "segment", 7),
    ]


def test_config_sweep_continues_after_failure_and_runs_fleet(script, tmp_path, monkeypatch):
    path = tmp_path / "devices.yaml"
    path.write_text('''devices:
  - {name: first, address: "AA", type: ble, protocol: bulb}
  - {name: second, address: "BB", type: ble, protocol: segment}
''')
    output = tmp_path / "results.json"
    calls = []
    def stage(args, rate, targets):
        addresses = [t.address for t in targets]
        calls.append((rate, addresses))
        if addresses == ["AA"]:
            raise RuntimeError("unreachable")
        return [{"address": a, "write_errors": 0, "observed_disconnects": 0,
                 "connected_at_end": True, "color_writes": 10} for a in addresses]
    monkeypatch.setattr(script, "run_stage", stage)
    monkeypatch.setattr("sys.argv", ["test", "--config", str(path), "--rates", "5", "10",
                                     "--output", str(output)])
    assert script.main() == 1
    assert calls == [(5, ["AA"]), (5, ["BB"]), (10, ["BB"]),
                     (5, ["AA", "BB"]), (10, ["AA", "BB"])]
    report = json.loads(output.read_text())
    assert report["failures"][0]["addresses"] == ["AA"]
    assert {r["phase"] for r in report["results"]} == {"individual", "together"}


def test_explicit_address_mode_remains_supported(script):
    targets = script.load_targets(SimpleNamespace(
        config=None, address=["aa", "AA"], protocol="bulb", segments=1,
    ))
    assert len(targets) == 1
    assert targets[0].address == "AA"


@pytest.mark.parametrize("variant, subscriptions, queries", [
    ("baseline", 0, 0), ("notify", 1, 0), ("notify-query", 1, 4),
])
def test_continuous_stream_queries_and_notifications(script, monkeypatch, variant, subscriptions, queries):
    clock = [0.0]
    monkeypatch.setattr(script, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    writes, subscribed = [], []

    class Client:
        async def start_notify(self, uuid, callback):
            subscribed.append(uuid)
            self.callback = callback

        async def write_gatt_char(self, uuid, data, response):
            assert response is False
            writes.append(data)
            if data == script.build_ble_keepalive_packet():
                self.callback(None, bytearray([0xAA, 0x01, 0x01]))

    adapter = script.MeasuredAdapter(script.GoveeBleConfig(address="AA"), variant)
    client = Client()
    color = bytes([0x33, 0x05, 0x0D])

    async def stream():
        for step in range(50):
            clock[0] = step / 5
            await adapter._ble_write(client, color)
    asyncio.run(stream())
    report = adapter.session_report(0, 10)
    assert len(subscribed) == subscriptions
    assert report["status_queries"] == queries
    assert report["notifications_received"] == queries
    assert writes.count(color) == 50
    assert len(adapter.samples) == 50  # Queries must not inflate color throughput.
    assert report["session_setup_errors"] == 0


def test_reconnect_resubscribes_and_resets_query_deadline(script, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(script, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    calls = []

    class Client:
        async def start_notify(self, uuid, callback):
            calls.append(self)

        async def write_gatt_char(self, *args, **kwargs):
            pass

    adapter = script.MeasuredAdapter(script.GoveeBleConfig(address="AA"), "notify-query")
    first, second = Client(), Client()

    async def reconnect():
        await adapter._ble_write(first, b"color")
        clock[0] = 10
        await adapter._ble_write(second, b"color")
        assert not adapter.queries
        clock[0] = 12
        await adapter._ble_write(second, b"color")
    asyncio.run(reconnect())
    assert calls == [first, second]
    assert adapter.queries == [12]


def test_subscription_failure_is_not_silently_baseline(script):
    class Client:
        async def start_notify(self, *args):
            raise RuntimeError("notifications unavailable")

    adapter = script.MeasuredAdapter(script.GoveeBleConfig(address="AA"), "notify")
    with pytest.raises(RuntimeError, match="notifications unavailable"):
        asyncio.run(adapter._ble_write(Client(), b"color"))
    assert adapter.session_setups[0]["subscribed"] is False
    assert "notifications unavailable" in adapter.session_setups[0]["error"]
    assert not adapter.samples


def test_variants_continue_after_baseline_disconnects(script, tmp_path, monkeypatch):
    calls = []
    output = tmp_path / "sessions.json"

    def stage(args, rate, targets):
        calls.append((args.session_variant, rate))
        return [{"write_errors": 0, "observed_disconnects": int(args.session_variant == "baseline"),
                 "connected_at_end": True, "color_writes": 10}]

    monkeypatch.setattr(script, "run_stage", stage)
    monkeypatch.setattr("sys.argv", ["test", "--address", "AA", "--protocol", "bulb",
                                     "--rates", "5", "10", "--session-variants",
                                     "baseline", "notify", "notify-query", "--output", str(output)])
    assert script.main() == 1
    assert calls == [("baseline", 5), ("notify", 5), ("notify", 10),
                     ("notify-query", 5), ("notify-query", 10)]
    assert {r["session_variant"] for r in json.loads(output.read_text())["results"]} == {
        "baseline", "notify", "notify-query",
    }
