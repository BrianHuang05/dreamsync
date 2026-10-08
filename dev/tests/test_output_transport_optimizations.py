"""Exercise output lifecycle, callback responsiveness and pacing without radios."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from dreamsync.output import govee_ble as ble, govee_lan as lan


def test_udp_reuse_failure_recovery_and_close(monkeypatch):
    sockets = []

    class Socket:
        fail = False
        closed = False

        def sendto(self, payload, destination):
            if self.fail:
                raise OSError("injected failure")
            return len(payload)

        def close(self):
            self.closed = True

    def factory(*args):
        sock = Socket()
        sockets.append(sock)
        return sock

    monkeypatch.setattr(lan.socket, "socket", factory)
    tick = [0.0]
    adapter = lan.GoveeLanAdapter(lan.GoveeLanConfig("127.0.0.1"), monotonic_fn=lambda: tick[0])
    adapter.turn_on()
    adapter.set_brightness(30)
    assert adapter.send_frame([(1, 2, 3)])
    assert len(sockets) == 1
    sockets[0].fail = True
    tick[0] += 1
    assert not adapter.send_frame([(1, 2, 3)])
    assert not adapter.last_send_ok
    assert sockets[0].closed
    tick[0] += 1
    assert adapter.send_frame([(1, 2, 3)])
    assert adapter.last_send_ok
    assert len(sockets) == 2
    sockets[1].fail = True
    with pytest.raises(OSError):
        adapter.set_brightness(50)
    assert not adapter.last_send_ok
    adapter.close()
    adapter.close()


def test_idle_queue_allows_callbacks_and_cancellation():
    async def run():
        adapter = ble.GoveeBleAdapter(ble.GoveeBleConfig(address="test"))
        task = asyncio.create_task(adapter._next_queued_frame())
        asyncio.get_running_loop().call_later(.02, adapter.send_color, 1, 2, 3)
        assert await asyncio.wait_for(task, .15) == (1, 2, 3, 100)
        task = asyncio.create_task(adapter._next_queued_frame())
        await asyncio.sleep(.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())


@pytest.mark.parametrize("write_duration", [.04, .25])
def test_deadlines_include_write_time_and_skip_overruns(monkeypatch, write_duration):
    clock = [100.0]
    starts = []
    adapter = ble.GoveeBleAdapter(ble.GoveeBleConfig(address="test", protocol=ble.BleProtocol.BULB, max_fps=5))
    adapter._started = True
    client = SimpleNamespace(is_connected=True, disconnect=AsyncMock())
    monkeypatch.setattr(ble, "_require_bleak", lambda: object())
    monkeypatch.setattr(ble, "_BLE_CONNECTIONS", SimpleNamespace(connect=AsyncMock(return_value=client)))
    monkeypatch.setattr(ble, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    async def sleep(delay):
        clock[0] += delay
        adapter.send_color(len(starts), 2, 3)

    async def write(client, packet):
        if packet[:3] == bytes([0x33, 5, 13]):
            starts.append(clock[0])
            clock[0] += write_duration
            adapter.send_color(len(starts), 2, 3)
            if len(starts) == 10:
                adapter._started = False

    monkeypatch.setattr(ble.asyncio, "sleep", sleep)
    adapter._ble_write = write
    asyncio.run(adapter._async_loop())
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert gaps == pytest.approx([.2 if write_duration < .2 else .4] * 9)
