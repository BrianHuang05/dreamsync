"""Connection scheduling regressions without Bluetooth hardware."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dreamsync.output.govee_ble import (
    GoveeBleAdapter, GoveeBleConfig, _BleConnectionCoordinator,
)


def fake_bleak(addresses=("AA", "BB")):
    devices = [SimpleNamespace(address=address) for address in addresses]
    client = SimpleNamespace(
        connect=AsyncMock(), disconnect=AsyncMock(), is_connected=True, services=object(),
    )
    bleak = SimpleNamespace(
        BleakScanner=SimpleNamespace(discover=AsyncMock(return_value=devices)),
        BleakClient=MagicMock(return_value=client),
    )
    return bleak, devices, client


def test_multiple_devices_share_scan_and_receive_device_objects():
    coordinator = _BleConnectionCoordinator()
    bleak, devices, _ = fake_bleak()
    for address in ("aa", "BB"):
        asyncio.run(coordinator.connect(bleak, GoveeBleConfig(address=address), lambda: True))
    bleak.BleakScanner.discover.assert_awaited_once()
    assert [call.args[0] for call in bleak.BleakClient.call_args_list] == devices


def test_missing_devices_share_negative_scan_cache():
    coordinator = _BleConnectionCoordinator()
    bleak, _, _ = fake_bleak()
    for address in ("missing1", "missing2"):
        with pytest.raises(RuntimeError, match="not found in shared scan"):
            asyncio.run(coordinator.connect(bleak, GoveeBleConfig(address=address), lambda: True))
    bleak.BleakScanner.discover.assert_awaited_once()
    bleak.BleakClient.assert_not_called()
    # Missing devices must not prevent reachable peers connecting.
    asyncio.run(coordinator.connect(bleak, GoveeBleConfig(address="AA"), lambda: True))


def test_expired_discovery_is_refreshed():
    coordinator = _BleConnectionCoordinator()
    bleak, _, _ = fake_bleak()
    with patch("dreamsync.output.govee_ble.time.monotonic", return_value=10):
        asyncio.run(coordinator.connect(bleak, GoveeBleConfig(address="AA"), lambda: True))
    replacement = SimpleNamespace(address="AA")
    bleak.BleakScanner.discover.return_value = [replacement]
    with patch("dreamsync.output.govee_ble.time.monotonic", return_value=41):
        asyncio.run(coordinator.connect(bleak, GoveeBleConfig(address="AA"), lambda: True))
    assert bleak.BleakScanner.discover.await_count == 2
    assert bleak.BleakClient.call_args.args[0] is replacement


def test_failed_connect_cleans_up_and_preserves_other_devices():
    coordinator = _BleConnectionCoordinator()
    bleak, _, client = fake_bleak()
    client.connect.side_effect = RuntimeError("failed")
    with pytest.raises(RuntimeError, match="failed"):
        asyncio.run(coordinator.connect(bleak, GoveeBleConfig(address="AA"), lambda: True))
    client.disconnect.assert_awaited_once()
    assert "AA" not in coordinator._devices
    client.connect.side_effect = None
    asyncio.run(coordinator.connect(bleak, GoveeBleConfig(address="BB"), lambda: True))
    bleak.BleakScanner.discover.assert_awaited_once()


def test_scan_failure_is_throttled_and_releases_gate():
    coordinator = _BleConnectionCoordinator()
    bleak, _, _ = fake_bleak()
    bleak.BleakScanner.discover.side_effect = RuntimeError("radio off")
    with pytest.raises(RuntimeError, match="radio off"):
        asyncio.run(coordinator.connect(bleak, GoveeBleConfig(address="AA"), lambda: True))
    with pytest.raises(RuntimeError, match="not found"):
        asyncio.run(coordinator.connect(bleak, GoveeBleConfig(address="BB"), lambda: True))
    bleak.BleakScanner.discover.assert_awaited_once()
    assert not coordinator._gate.locked()


def test_service_discovery_failure_does_not_return_unusable_client():
    coordinator = _BleConnectionCoordinator()
    bleak, _, _ = fake_bleak()
    class Client:
        is_connected = True
        connect = AsyncMock()
        disconnect = AsyncMock()
        @property
        def services(self):
            raise RuntimeError("Service Discovery has not been performed yet")
    client = Client()
    bleak.BleakClient.return_value = client
    with pytest.raises(RuntimeError, match="Service Discovery"):
        asyncio.run(coordinator.connect(bleak, GoveeBleConfig(address="AA"), lambda: True))
    client.disconnect.assert_awaited_once()
    assert "AA" not in coordinator._devices
    assert not coordinator._gate.locked()


def test_retry_after_failed_connect_uses_fresh_discovery():
    coordinator = _BleConnectionCoordinator()
    bleak, _, client = fake_bleak()
    client.connect.side_effect = RuntimeError("failed")
    with patch("dreamsync.output.govee_ble.time.monotonic", return_value=10):
        with pytest.raises(RuntimeError, match="failed"):
            asyncio.run(coordinator.connect(bleak, GoveeBleConfig(address="AA"), lambda: True))
    replacement = SimpleNamespace(address="AA")
    bleak.BleakScanner.discover.return_value = [replacement]
    client.connect.side_effect = None
    with patch("dreamsync.output.govee_ble.time.monotonic", return_value=16):
        asyncio.run(coordinator.connect(bleak, GoveeBleConfig(address="AA"), lambda: True))
    assert bleak.BleakClient.call_args.args[0] is replacement


def test_setup_is_serialized_across_real_event_loop_threads():
    coordinator = _BleConnectionCoordinator()
    bleak, devices, _ = fake_bleak()
    guard = threading.Lock()
    active = 0
    peak = 0
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    second_started = threading.Event()

    def make_client(device, **kwargs):
        async def connect():
            nonlocal active, peak
            with guard:
                active += 1
                peak = max(peak, active)
            if device is devices[0]:
                first_entered.set()
                while not release_first.is_set():
                    await asyncio.sleep(0.005)
            else:
                second_entered.set()
            with guard:
                active -= 1
        return SimpleNamespace(connect=connect, disconnect=AsyncMock(),
                               is_connected=True, services=object())

    bleak.BleakClient.side_effect = make_client
    def run(address):
        if address == "BB":
            second_started.set()
        return asyncio.run(coordinator.connect(bleak, GoveeBleConfig(address=address), lambda: True))
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run, "AA")
        try:
            assert first_entered.wait(2)
            second = pool.submit(run, "BB")
            assert second_started.wait(2)
            assert not second_entered.wait(0.15)
            assert not second.done()
        finally:
            release_first.set()
        first.result(timeout=2)
        second.result(timeout=2)
    assert peak == 1
    bleak.BleakScanner.discover.assert_awaited_once()


def test_cancelled_connect_disconnects_and_releases_gate():
    async def run():
        coordinator = _BleConnectionCoordinator()
        bleak, _, client = fake_bleak()
        entered = asyncio.Event()
        async def connect():
            entered.set()
            await asyncio.Event().wait()
        client.connect.side_effect = connect
        task = asyncio.create_task(coordinator.connect(
            bleak, GoveeBleConfig(address="AA"), lambda: True,
        ))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        client.disconnect.assert_awaited_once()
        assert not coordinator._gate.locked()
    asyncio.run(run())


def test_connection_timeout_releases_gate_for_other_devices():
    coordinator = _BleConnectionCoordinator()
    bleak, _, client = fake_bleak()
    async def hang():
        await asyncio.Event().wait()
    client.connect.side_effect = hang
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(coordinator.connect(
            bleak, GoveeBleConfig(address="AA", connect_timeout=0.02), lambda: True,
        ))
    client.disconnect.assert_awaited_once()
    assert not coordinator._gate.locked()
    client.connect.side_effect = None
    asyncio.run(coordinator.connect(bleak, GoveeBleConfig(address="BB"), lambda: True))


def test_stop_during_completed_scan_skips_connection():
    coordinator = _BleConnectionCoordinator()
    bleak, devices, _ = fake_bleak()
    active = True
    async def scan(**kwargs):
        nonlocal active
        active = False
        return devices
    bleak.BleakScanner.discover.side_effect = scan
    assert asyncio.run(coordinator.connect(
        bleak, GoveeBleConfig(address="AA"), lambda: active,
    )) is None
    bleak.BleakClient.assert_not_called()
    assert not coordinator._gate.locked()


def test_start_does_not_replace_a_worker_still_shutting_down():
    adapter = GoveeBleAdapter(GoveeBleConfig(address="AA"))
    worker = MagicMock()
    worker.is_alive.return_value = True
    adapter._thread = worker
    adapter.start()
    assert adapter._thread is worker
    assert not adapter._started


def test_stop_interrupts_scan_and_allows_restart():
    coordinator = _BleConnectionCoordinator()
    bleak, _, _ = fake_bleak()
    entered = threading.Event()
    async def scan(**kwargs):
        entered.set()
        await asyncio.Event().wait()
    bleak.BleakScanner.discover.side_effect = scan
    adapter = GoveeBleAdapter(GoveeBleConfig(address="AA"))
    with patch("dreamsync.output.govee_ble._require_bleak", return_value=bleak), patch(
        "dreamsync.output.govee_ble._BLE_CONNECTIONS", coordinator,
    ):
        adapter.start()
        try:
            assert entered.wait(2)
        finally:
            adapter.stop()
        assert adapter._thread is None
        assert not coordinator._gate.locked()
        # Avoid waiting for the negative-cache throttle in this lifecycle test.
        coordinator._last_scan_at = float("-inf")
        entered.clear()
        adapter.start()
        try:
            assert entered.wait(2)
        finally:
            adapter.stop()
        assert adapter._thread is None


def test_stopped_waiter_never_connects():
    coordinator = _BleConnectionCoordinator()
    bleak, _, _ = fake_bleak()
    coordinator._gate.acquire()
    try:
        assert asyncio.run(coordinator.connect(
            bleak, GoveeBleConfig(address="AA"), lambda: False,
        )) is None
    finally:
        coordinator._gate.release()
    bleak.BleakClient.assert_not_called()
