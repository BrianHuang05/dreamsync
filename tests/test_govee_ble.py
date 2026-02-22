"""Tests for the Govee BLE adapter module.

All tests mock ``bleak`` to avoid requiring real BLE hardware or the bleak
package itself.
"""
import asyncio
import queue
import threading
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from dreamsync.director import EffectMode, LightingIntent
from dreamsync.output.govee_ble import (
    GOVEE_BLE_CHAR_UUID,
    GOVEE_BLE_SERVICE_UUID,
    GOVEE_NAME_PREFIXES,
    GoveeBleAdapter,
    GoveeBleConfig,
    GoveeBleDevice,
    MultiBleAdapter,
    _SHUTDOWN,
    build_ble_color_packet,
    build_ble_manual_mode_packet,
)
from dreamsync.output.govee_lan import (
    _ptreal_checksum,
    build_ptreal_brightness_packet,
    build_ptreal_power_packet,
)


# ---------------------------------------------------------------------------
# Packet builder tests
# ---------------------------------------------------------------------------


class BuildBleColorPacketTests(unittest.TestCase):
    def test_packet_length(self) -> None:
        pkt = build_ble_color_packet(255, 0, 0)
        self.assertEqual(len(pkt), 20)

    def test_header_bytes(self) -> None:
        pkt = build_ble_color_packet(0, 0, 0)
        self.assertEqual(pkt[0], 0x33)
        self.assertEqual(pkt[1], 0x05)
        self.assertEqual(pkt[2], 0x02)

    def test_rgb_bytes(self) -> None:
        pkt = build_ble_color_packet(100, 200, 50)
        self.assertEqual(pkt[3], 100)
        self.assertEqual(pkt[4], 200)
        self.assertEqual(pkt[5], 50)

    def test_padding_is_zero(self) -> None:
        pkt = build_ble_color_packet(255, 255, 255)
        for i in range(6, 19):
            self.assertEqual(pkt[i], 0, f"byte {i} should be zero padding")

    def test_checksum(self) -> None:
        pkt = build_ble_color_packet(0xAA, 0xBB, 0xCC)
        expected = _ptreal_checksum(list(pkt[:19]))
        self.assertEqual(pkt[19], expected)

    def test_clamped_to_byte(self) -> None:
        pkt = build_ble_color_packet(300, -10, 256)
        # 300 & 0xFF = 44, -10 & 0xFF = 246, 256 & 0xFF = 0
        self.assertEqual(pkt[3], 300 & 0xFF)
        self.assertEqual(pkt[4], (-10) & 0xFF)
        self.assertEqual(pkt[5], 256 & 0xFF)


class BuildBleManualModePacketTests(unittest.TestCase):
    def test_packet_length(self) -> None:
        pkt = build_ble_manual_mode_packet()
        self.assertEqual(len(pkt), 20)

    def test_header_bytes(self) -> None:
        pkt = build_ble_manual_mode_packet()
        self.assertEqual(pkt[0], 0x33)
        self.assertEqual(pkt[1], 0x05)
        self.assertEqual(pkt[2], 0x01)

    def test_checksum(self) -> None:
        pkt = build_ble_manual_mode_packet()
        expected = _ptreal_checksum(list(pkt[:19]))
        self.assertEqual(pkt[19], expected)


class PtRealPacketReuseTests(unittest.TestCase):
    """Verify that ptreal power/brightness packets are correctly reused for BLE."""

    def test_power_on_packet_for_ble(self) -> None:
        pkt = build_ptreal_power_packet(True)
        self.assertEqual(len(pkt), 20)
        self.assertEqual(pkt[0], 0x33)
        self.assertEqual(pkt[1], 0x01)
        self.assertEqual(pkt[2], 0x01)

    def test_power_off_packet_for_ble(self) -> None:
        pkt = build_ptreal_power_packet(False)
        self.assertEqual(pkt[2], 0x00)

    def test_brightness_packet_for_ble(self) -> None:
        pkt = build_ptreal_brightness_packet(50)
        self.assertEqual(len(pkt), 20)
        self.assertEqual(pkt[0], 0x33)
        self.assertEqual(pkt[1], 0x04)
        # 50% → raw = int(50*255/100) = 127
        self.assertEqual(pkt[2], 127)


# ---------------------------------------------------------------------------
# GoveeBleDevice / discovery data tests
# ---------------------------------------------------------------------------


class GoveeBleDeviceTests(unittest.TestCase):
    def test_dataclass_fields(self) -> None:
        dev = GoveeBleDevice(name="Govee_H6159_ABCD", address="AA:BB:CC:DD:EE:FF", rssi=-60)
        self.assertEqual(dev.name, "Govee_H6159_ABCD")
        self.assertEqual(dev.address, "AA:BB:CC:DD:EE:FF")
        self.assertEqual(dev.rssi, -60)

    def test_default_rssi(self) -> None:
        dev = GoveeBleDevice(name="test", address="00:00:00:00:00:00")
        self.assertEqual(dev.rssi, 0)


class NamePrefixTests(unittest.TestCase):
    def test_govee_prefix(self) -> None:
        self.assertTrue(any("Govee_H6159".startswith(p) for p in GOVEE_NAME_PREFIXES))

    def test_ihoment_prefix(self) -> None:
        self.assertTrue(any("ihoment_H6127".startswith(p) for p in GOVEE_NAME_PREFIXES))

    def test_unknown_prefix(self) -> None:
        self.assertFalse(any("SomeOther_Device".startswith(p) for p in GOVEE_NAME_PREFIXES))


# ---------------------------------------------------------------------------
# GoveeBleConfig tests
# ---------------------------------------------------------------------------


class GoveeBleConfigTests(unittest.TestCase):
    def test_defaults(self) -> None:
        cfg = GoveeBleConfig(address="AA:BB:CC:DD:EE:FF")
        self.assertEqual(cfg.address, "AA:BB:CC:DD:EE:FF")
        self.assertEqual(cfg.name, "")
        self.assertEqual(cfg.segments, 15)
        self.assertEqual(cfg.max_fps, 5.0)
        self.assertEqual(cfg.reconnect_delay, 2.0)
        self.assertEqual(cfg.connect_timeout, 10.0)

    def test_frozen(self) -> None:
        cfg = GoveeBleConfig(address="AA:BB:CC:DD:EE:FF")
        with self.assertRaises(AttributeError):
            cfg.address = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# GoveeBleAdapter tests (mocked BLE)
# ---------------------------------------------------------------------------


class GoveeBleAdapterSendColorTests(unittest.TestCase):
    """Test the queue-based send_color method without starting BLE thread."""

    def setUp(self) -> None:
        self.config = GoveeBleConfig(address="AA:BB:CC:DD:EE:FF")
        self.adapter = GoveeBleAdapter(self.config)

    def test_send_color_queues_update(self) -> None:
        self.adapter.send_color(255, 0, 0, 100)
        item = self.adapter._queue.get_nowait()
        self.assertEqual(item, (255, 0, 0, 100))

    def test_duplicate_color_skipped(self) -> None:
        self.adapter.send_color(255, 0, 0, 100)
        self.adapter.send_color(255, 0, 0, 100)  # duplicate
        self.assertEqual(self.adapter._queue.qsize(), 1)

    def test_different_color_queued(self) -> None:
        self.adapter.send_color(255, 0, 0, 100)
        self.adapter.send_color(0, 255, 0, 100)
        self.assertEqual(self.adapter._queue.qsize(), 2)

    def test_queue_overflow_drops_oldest(self) -> None:
        # Queue maxsize is 4
        for i in range(6):
            self.adapter.send_color(i, 0, 0, 100)
        # Should not raise, and queue should have recent items
        self.assertLessEqual(self.adapter._queue.qsize(), 4)

    def test_connected_default_false(self) -> None:
        self.assertFalse(self.adapter.connected)


class GoveeBleAdapterEmitTests(unittest.TestCase):
    """Test the OutputAdapter-compatible emit method."""

    def setUp(self) -> None:
        self.config = GoveeBleConfig(address="AA:BB:CC:DD:EE:FF")
        self.adapter = GoveeBleAdapter(self.config)

    def test_emit_with_color(self) -> None:
        intent = LightingIntent(
            mode=EffectMode.AMBIENT, intensity=0.8, speed=0.2, bpm=120.0, color="#ff0000",
        )
        self.adapter.emit(0.0, intent)
        item = self.adapter._queue.get_nowait()
        r, g, b, brightness = item
        # 255 * 0.8 = 204
        self.assertEqual(r, 204)
        self.assertEqual(g, 0)
        self.assertEqual(b, 0)
        self.assertEqual(brightness, 80)  # 0.8 * 100

    def test_emit_without_color(self) -> None:
        intent = LightingIntent(
            mode=EffectMode.AMBIENT, intensity=1.0, speed=0.2, bpm=120.0,
        )
        self.adapter.emit(0.0, intent)
        item = self.adapter._queue.get_nowait()
        r, g, b, brightness = item
        # Default warm white: (255, 180, 100)
        self.assertEqual(r, 255)
        self.assertEqual(g, 180)
        self.assertEqual(b, 100)
        self.assertEqual(brightness, 100)

    def test_emit_zero_intensity(self) -> None:
        intent = LightingIntent(
            mode=EffectMode.AMBIENT, intensity=0.0, speed=0.0, bpm=0.0, color="#ffffff",
        )
        self.adapter.emit(0.0, intent)
        item = self.adapter._queue.get_nowait()
        r, g, b, brightness = item
        self.assertEqual(r, 0)
        self.assertEqual(g, 0)
        self.assertEqual(b, 0)
        self.assertEqual(brightness, 30)  # minimum brightness floor

    def test_emit_clamps_intensity(self) -> None:
        intent = LightingIntent(
            mode=EffectMode.AMBIENT, intensity=2.0, speed=0.0, bpm=0.0, color="#ff0000",
        )
        self.adapter.emit(0.0, intent)
        item = self.adapter._queue.get_nowait()
        r, g, b, brightness = item
        self.assertEqual(r, 255)
        self.assertEqual(brightness, 100)


# ---------------------------------------------------------------------------
# MultiBleAdapter tests
# ---------------------------------------------------------------------------


class MultiBleAdapterTests(unittest.TestCase):
    def test_send_color_fans_out(self) -> None:
        a1 = GoveeBleAdapter(GoveeBleConfig(address="AA:BB:CC:DD:EE:01"))
        a2 = GoveeBleAdapter(GoveeBleConfig(address="AA:BB:CC:DD:EE:02"))
        multi = MultiBleAdapter([a1, a2])

        multi.send_color(100, 200, 50, 80)
        self.assertEqual(a1._queue.get_nowait(), (100, 200, 50, 80))
        self.assertEqual(a2._queue.get_nowait(), (100, 200, 50, 80))

    def test_emit_fans_out(self) -> None:
        a1 = GoveeBleAdapter(GoveeBleConfig(address="AA:BB:CC:DD:EE:01"))
        a2 = GoveeBleAdapter(GoveeBleConfig(address="AA:BB:CC:DD:EE:02"))
        multi = MultiBleAdapter([a1, a2])

        intent = LightingIntent(
            mode=EffectMode.PULSE, intensity=0.5, speed=0.3, bpm=128.0, color="#00ff00",
        )
        multi.emit(1.0, intent)

        item1 = a1._queue.get_nowait()
        item2 = a2._queue.get_nowait()
        self.assertEqual(item1, item2)
        self.assertEqual(item1[0], 0)    # R: 0 * 0.5
        self.assertEqual(item1[1], 127)  # G: 255 * 0.5 ≈ 127
        self.assertEqual(item1[2], 0)    # B: 0 * 0.5


# ---------------------------------------------------------------------------
# GATT UUID constants
# ---------------------------------------------------------------------------


class GattUuidTests(unittest.TestCase):
    def test_service_uuid_format(self) -> None:
        # Standard UUID format: 8-4-4-4-12
        parts = GOVEE_BLE_SERVICE_UUID.split("-")
        self.assertEqual(len(parts), 5)
        self.assertEqual(len(parts[0]), 8)

    def test_char_uuid_format(self) -> None:
        parts = GOVEE_BLE_CHAR_UUID.split("-")
        self.assertEqual(len(parts), 5)

    def test_uuids_differ(self) -> None:
        self.assertNotEqual(GOVEE_BLE_SERVICE_UUID, GOVEE_BLE_CHAR_UUID)


# ---------------------------------------------------------------------------
# MultiGoveeLanAdapter BLE follower integration
# ---------------------------------------------------------------------------


class MultiAdapterBleFollowerTests(unittest.TestCase):
    """Test that MultiGoveeLanAdapter correctly forwards to BLE followers."""

    def test_send_frame_pushes_to_ble_followers(self) -> None:
        from dreamsync.output.govee_lan import (
            GoveeLanAdapter,
            GoveeLanConfig,
            MultiGoveeLanAdapter,
        )
        from dreamsync.output.roles import DeviceRole
        from dreamsync.render import SegmentRenderer

        sent: list[bytes] = []
        lan_config = GoveeLanConfig(device_ip="10.0.0.1", segments=3, fps=30)
        lan_adapter = GoveeLanAdapter(lan_config, transport=lambda p, ip, port: sent.append(p))
        renderer = SegmentRenderer(segments=3)

        ble_adapter = GoveeBleAdapter(GoveeBleConfig(address="AA:BB:CC:DD:EE:FF"))

        multi = MultiGoveeLanAdapter(
            [(lan_adapter, renderer, DeviceRole.PRIMARY)],
            ble_followers=[ble_adapter],
        )

        intent = LightingIntent(
            mode=EffectMode.AMBIENT, intensity=0.6, speed=0.2, bpm=120.0, color="#ff4400",
        )
        multi.send_frame(0.0, intent)

        # LAN adapter should have sent
        self.assertTrue(len(sent) > 0)

        # BLE adapter should have received intent
        item = ble_adapter._queue.get_nowait()
        r, g, b, brightness = item
        self.assertGreater(r, 0)

    def test_no_ble_followers_by_default(self) -> None:
        from dreamsync.output.govee_lan import MultiGoveeLanAdapter

        multi = MultiGoveeLanAdapter([])
        self.assertEqual(len(multi._ble_followers), 0)

    def test_deactivate_is_safe_without_followers(self) -> None:
        from dreamsync.output.govee_lan import MultiGoveeLanAdapter

        multi = MultiGoveeLanAdapter([])
        multi.deactivate()  # should not raise


# ---------------------------------------------------------------------------
# Stop/start lifecycle tests
# ---------------------------------------------------------------------------


class GoveeBleAdapterLifecycleTests(unittest.TestCase):
    def test_stop_without_start(self) -> None:
        adapter = GoveeBleAdapter(GoveeBleConfig(address="AA:BB:CC:DD:EE:FF"))
        adapter.stop()  # should not raise

    def test_double_start(self) -> None:
        adapter = GoveeBleAdapter(GoveeBleConfig(address="AA:BB:CC:DD:EE:FF"))
        # Mock the thread to avoid real BLE
        adapter._run_loop = lambda: None
        adapter.start()
        adapter.start()  # second call should be no-op
        self.assertTrue(adapter._started)
        adapter._started = False  # let thread exit


if __name__ == "__main__":
    unittest.main()
