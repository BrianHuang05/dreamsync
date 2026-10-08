"""Runtime measurements count host I/O and cannot certify light delivery."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import asyncio
import pytest

from dreamsync.output.send_metrics import HostSendMetrics
from dreamsync.output.govee_lan import GoveeLanAdapter, GoveeLanConfig
from dreamsync.output.govee_ble import GoveeBleAdapter, GoveeBleConfig, build_ble_bulb_color_packet
from dreamsync.gui.services.device_health_service import DeviceHealthService
from dreamsync.output.auto_detect import DeviceConfig


def test_window_rate_errors_gaps_and_stale_samples():
    clock = [0.0]
    metrics = HostSendMetrics(lambda: clock[0])
    assert metrics.snapshot()["host_send_hz"] is None
    metrics.record(0, .002, True)
    metrics.record(1, 1.004, False)
    metrics.record(2, 2.006, True)
    clock[0] = 3
    snapshot = metrics.snapshot()
    assert snapshot["host_send_hz"] == pytest.approx(2 / 3)
    assert snapshot["host_send_ms_p95"] == pytest.approx(6)
    assert snapshot["host_send_gap_ms_max"] == 2000
    assert snapshot["host_send_errors"] == 1
    clock[0] = 20
    assert metrics.snapshot()["host_send_hz"] == 0
    assert metrics.snapshot()["host_send_ms_p95"] is None


def test_lan_counts_frames_not_control_or_rate_limited_calls():
    clock = [0.0]
    fail = [False]
    def transport(*_args):
        if fail[0]:
            raise OSError("send failed")
    adapter = GoveeLanAdapter(GoveeLanConfig("test", fps=5), transport=transport,
                              monotonic_fn=lambda: clock[0])
    adapter.turn_on()
    adapter.set_brightness(30)
    assert adapter.send_frame([(1, 2, 3)])
    assert not adapter.send_frame([(4, 5, 6)])
    clock[0] = 1
    fail[0] = True
    assert not adapter.send_frame([(4, 5, 6)])
    clock[0] = 2
    snapshot = adapter.health_snapshot()
    assert snapshot["host_send_hz"] == .5
    assert snapshot["host_send_errors"] == 1
    assert snapshot["status"] == "degraded"


def test_ble_counts_color_writes_and_reports_failure():
    adapter = GoveeBleAdapter(GoveeBleConfig("test"))
    async def run():
        client = SimpleNamespace(write_gatt_char=AsyncMock())
        await adapter._ble_write(client, bytes([0x33, 1, 1]))
        await adapter._ble_write(client, build_ble_bulb_color_packet(1, 2, 3))
        client.write_gatt_char.side_effect = OSError("disconnected")
        with pytest.raises(OSError):
            await adapter._ble_write(client, build_ble_bulb_color_packet(4, 5, 6))
    asyncio.run(run())
    assert adapter.health_snapshot()["host_send_errors"] == 1
    assert len(adapter._send_metrics._samples) == 2


@pytest.mark.parametrize("kind", ["lan", "ble", "auto"])
def test_gui_health_surfaces_runtime_host_measurements(kind, tmp_path):
    config = DeviceConfig("light", "test", type="ble" if kind == "ble" else "lan",
                          transport_policy="auto" if kind == "auto" else "fixed")
    observation = {"status": "online", "active_transport": "lan", "host_send_hz": 3.5,
                   "host_send_ms_p95": 2.4, "host_send_gap_ms_max": 900,
                   "host_send_errors": 2, "host_send_unit": "frames"}
    service = DeviceHealthService(config_loader=lambda _: [config],
        lan_probe=lambda *_a, **_kw: SimpleNamespace(count=1, median_ms=10),
        runtime_health_provider=lambda: {config.address: observation})
    entry, = service.refresh_once(tmp_path / "unused.yaml").entries
    assert entry.host_send_hz == 3.5
    assert "delivery unconfirmed" in entry.host_measurement_text
    assert "2 send errors" in entry.host_measurement_text
