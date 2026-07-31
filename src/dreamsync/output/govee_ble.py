"""Govee BLE adapter for mood-follower devices.

Controls BLE-only Govee devices (bulbs, portables, older strips) that lack
LAN API support.  These devices receive a single (color, brightness) derived
from the current LightingIntent rather than per-segment RGB frames, making
them lightweight "mood followers."

Protocol notes
--------------
Govee BLE devices use the same 0x33-prefix command packets as the ptreal
(BLE-over-LAN) transport.  Over LAN those packets are base64-wrapped in JSON
and sent via UDP; over BLE we write the raw 20-byte packets directly to the
device's GATT write characteristic.

Known GATT identifiers (shared across most Govee BLE products):
    Service UUID:         00010203-0405-0607-0809-0a0b0c0d1910
    Write characteristic: 00010203-0405-0607-0809-0a0b0c0d2b11

Device advertisement names typically start with ``Govee_`` or ``ihoment_``.

Threading model
---------------
``bleak`` is an asyncio library.  DreamSync's main loop is synchronous, so
we run the BLE event loop in a dedicated daemon thread.  The main thread
pushes ``(r, g, b, brightness)`` tuples into a :class:`queue.Queue`; the BLE
thread drains the queue and writes to the device at a capped rate (~5 Hz).
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from dreamsync.director import LightingIntent
from dreamsync.output.govee_lan import (
    _parse_hex_color,
    build_ptreal_brightness_packet,
    build_ptreal_power_packet,
    build_ptreal_segment_packets,
)

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# GATT identifiers
# ---------------------------------------------------------------------------

GOVEE_BLE_SERVICE_UUID = "00010203-0405-0607-0809-0a0b0c0d1910"
GOVEE_BLE_CHAR_UUID = "00010203-0405-0607-0809-0a0b0c0d2b11"

# BLE device name prefixes used for discovery
GOVEE_NAME_PREFIXES = ("Govee_", "ihoment_")

# ---------------------------------------------------------------------------
# BLE protocol variants
# ---------------------------------------------------------------------------


class BleProtocol(str, Enum):
    """Which BLE command format to use for setting color."""

    SEGMENT = "segment"  # 33 05 15 01 + bitmask — strips (H617A, H612F family)
    BULB = "bulb"        # 33 05 0D RR GG BB — bulbs (H6006 family)


# ---------------------------------------------------------------------------
# BLE color commands
# ---------------------------------------------------------------------------


def _build_ble_packet(cmd_bytes: list[int]) -> bytes:
    """Build a 20-byte BLE packet: pad to 19 bytes + XOR checksum."""
    packet = list(cmd_bytes)
    while len(packet) < 19:
        packet.append(0x00)
    packet = packet[:19]
    chk = 0
    for byte in packet[:19]:
        chk ^= byte
    packet.append(chk)
    return bytes(packet)


def build_ble_color_packet(r: int, g: int, b: int) -> bytes:
    """Build a 20-byte BLE packet to set whole-device color.

    Uses command ``33 05 02`` which works on some device families.
    """
    return _build_ble_packet([0x33, 0x05, 0x02, r & 0xFF, g & 0xFF, b & 0xFF])


def build_ble_bulb_color_packet(r: int, g: int, b: int) -> bytes:
    """Build a 20-byte BLE packet for bulb color (H6006 family).

    Uses command ``33 05 0D RR GG BB`` — the direct color command for
    Govee bulbs that use the ihoment BLE protocol.
    """
    return _build_ble_packet([0x33, 0x05, 0x0D, r & 0xFF, g & 0xFF, b & 0xFF])


def build_ble_manual_mode_packet() -> bytes:
    """Build a packet to switch the device to manual/solid color mode.

    Command ``33 05 01`` tells the device to accept direct color commands
    rather than running a built-in scene or music mode.
    """
    return _build_ble_packet([0x33, 0x05, 0x01])


def build_ble_keepalive_packet() -> bytes:
    """Build a 20-byte BLE keep-alive packet (``AA 01``).

    Govee BLE devices disconnect after ~2-4 seconds of inactivity.
    Send this packet every ~2 seconds to maintain the connection
    during quiet periods when no color updates are being pushed.
    """
    return _build_ble_packet([0xAA, 0x01])


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GoveeBleDevice:
    """A discovered Govee BLE device."""

    name: str
    address: str  # MAC address or platform-specific identifier
    rssi: int = 0


def _require_bleak() -> Any:
    """Import and return the ``bleak`` package, raising a clear error if missing."""
    try:
        import bleak  # noqa: F811
        return bleak
    except ImportError:
        raise ImportError(
            "The 'bleak' package is required for BLE support. "
            "Install it with:  pip install dreamsync-music-sync[ble]"
        ) from None


async def _scan_ble_devices_async(
    timeout: float = 10.0,
    name_prefixes: tuple[str, ...] = GOVEE_NAME_PREFIXES,
) -> list[GoveeBleDevice]:
    """Scan for Govee BLE devices (async implementation)."""
    bleak = _require_bleak()
    scanner = bleak.BleakScanner()
    devices: list[GoveeBleDevice] = []
    seen: set[str] = set()

    try:
        discovered_with_advertisements = await scanner.discover(
            timeout=timeout,
            return_adv=True,
        )
    except TypeError:
        # Compatibility with older Bleak releases.
        discovered_with_advertisements = None

    if isinstance(discovered_with_advertisements, dict):
        discovered = [
            (device, advertisement)
            for device, advertisement in discovered_with_advertisements.values()
        ]
    else:
        legacy_devices = (
            discovered_with_advertisements
            if discovered_with_advertisements is not None
            else await scanner.discover(timeout=timeout)
        )
        discovered = [(device, None) for device in legacy_devices]

    for d, advertisement in discovered:
        name = d.name or ""
        if not any(name.startswith(p) for p in name_prefixes):
            continue
        if d.address in seen:
            continue
        seen.add(d.address)
        rssi = (
            getattr(advertisement, "rssi", None)
            if advertisement is not None
            else getattr(d, "rssi", None)
        )
        rssi = int(rssi or 0)
        devices.append(GoveeBleDevice(name=name, address=d.address, rssi=rssi))
        _logger.info("Discovered BLE device: %s (%s) RSSI=%d", name, d.address, rssi)

    return devices


def scan_ble_devices(
    timeout: float = 10.0,
    name_prefixes: tuple[str, ...] = GOVEE_NAME_PREFIXES,
) -> list[GoveeBleDevice]:
    """Scan for Govee BLE devices (synchronous wrapper).

    Runs the async BLE scan in a temporary event loop.
    """
    return asyncio.run(_scan_ble_devices_async(timeout=timeout, name_prefixes=name_prefixes))


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GoveeBleConfig:
    """Configuration for a single Govee BLE mood-follower device."""

    address: str  # BLE MAC address or platform identifier
    name: str = ""  # Human-readable name (from discovery)
    protocol: BleProtocol = BleProtocol.SEGMENT  # Command format
    segments: int = 15  # Segment count (only used by SEGMENT protocol)
    max_fps: float = 5.0  # Maximum color updates per second
    reconnect_delay: float = 2.0  # Seconds between reconnection attempts
    connect_timeout: float = 10.0  # BLE connection timeout


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


# Sentinel to signal the BLE thread to shut down
_SHUTDOWN = object()
_SEGMENT_FRAME = "segment_frame"


@dataclass
class _BleState:
    """Mutable state for the BLE background thread."""

    connected: bool = False
    last_send_at: float = 0.0
    reconnect_attempts: int = 0


class GoveeBleAdapter:
    """Controls a single Govee BLE device as a mood follower.

    Runs BLE I/O in a background daemon thread.  The main thread calls
    :meth:`send_color` or :meth:`emit` to push updates; the BLE thread
    drains the queue and writes to the device.
    """

    def __init__(self, config: GoveeBleConfig) -> None:
        self.config = config
        self._queue: queue.Queue = queue.Queue(maxsize=4)
        self._thread: threading.Thread | None = None
        self._started = False
        self._state = _BleState()
        self._lock = threading.Lock()
        self._ready_event = threading.Event()
        # Track last payload to avoid redundant writes
        self._last_payload: object | None = None
        self.paused: bool = False

    @property
    def connected(self) -> bool:
        return self._state.connected

    # -- Public API (called from main thread) -------------------------------

    def start(self) -> None:
        """Start the background BLE thread."""
        if self._started:
            return
        self._ready_event.clear()
        self._started = True
        self._thread = threading.Thread(
            target=self._run_loop,
            name=f"ble-{self.config.address}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the BLE thread to disconnect and exit."""
        if not self._started:
            return
        self._started = False
        # Clear the queue and push the shutdown sentinel
        try:
            while not self._queue.empty():
                self._queue.get_nowait()
        except queue.Empty:
            pass
        try:
            self._queue.put_nowait(_SHUTDOWN)
        except queue.Full:
            pass
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        self._ready_event.clear()

    def wait_until_connected(self, timeout: float | None = None) -> bool:
        """Wait until GATT setup is complete and color commands can be written."""

        return self._ready_event.wait(
            self.config.connect_timeout if timeout is None else max(0.0, float(timeout))
        )

    def send_color(self, r: int, g: int, b: int, brightness: int = 100) -> None:
        """Queue a color update for the BLE device.

        *brightness* is 0-100.  Drops oldest update if queue is full (we only
        care about the latest color).
        """
        if self.paused:
            return
        payload = (r, g, b, brightness)
        if payload == self._last_payload:
            return  # skip identical update
        self._last_payload = payload
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            # Drop oldest, enqueue latest
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(payload)
            except queue.Full:
                pass

    def send_segment_colors(
        self,
        colors: list[tuple[int, int, int]],
        brightness: int = 100,
    ) -> None:
        """Queue a per-segment color update for BLE strip devices."""
        if self.paused:
            return
        normalized = tuple(
            (int(r) & 0xFF, int(g) & 0xFF, int(b) & 0xFF)
            for r, g, b in colors
        )
        payload = (_SEGMENT_FRAME, normalized, int(max(0, min(100, brightness))))
        if payload == self._last_payload:
            return
        self._last_payload = payload
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(payload)
            except queue.Full:
                pass

    def emit(self, t: float, intent: LightingIntent) -> None:
        """OutputAdapter-compatible interface.

        Extracts color + intensity from the intent and queues a BLE update.
        """
        del t
        if intent.color is not None:
            r, g, b = _parse_hex_color(intent.color)
        else:
            r, g, b = 255, 180, 100  # warm white fallback

        factor = max(0.0, min(1.0, intent.intensity))
        r = int(r * factor)
        g = int(g * factor)
        b = int(b * factor)

        # Map intensity to hardware brightness (30-100 range for visibility)
        brightness = max(30, int(factor * 100))
        self.send_color(r, g, b, brightness)

    # -- Background thread --------------------------------------------------

    def _run_loop(self) -> None:
        """Entry point for the BLE background thread."""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._async_loop())
        except Exception:
            _logger.exception("BLE thread for %s crashed", self.config.address)
        finally:
            loop.close()

    async def _async_loop(self) -> None:
        """Main async loop: connect, process queue, reconnect on failure."""
        bleak = _require_bleak()
        min_interval = 1.0 / max(0.1, self.config.max_fps)

        while self._started:
            # Wait while paused — don't attempt connections
            while self.paused and self._started:
                await asyncio.sleep(1.0)
            if not self._started:
                return

            # Re-scan to get fresh advertisement data before connecting
            target_addr = self.config.address
            if self._state.reconnect_attempts > 0:
                try:
                    _logger.info(
                        "Running BLE scan before reconnecting to %s ...",
                        target_addr,
                    )
                    scanner = bleak.BleakScanner()
                    discovered = await scanner.discover(timeout=5.0)
                    found = any(
                        d.address.upper() == target_addr.upper()
                        for d in discovered
                    )
                    if not found:
                        _logger.warning(
                            "BLE device %s not found in scan, will retry",
                            target_addr,
                        )
                        await asyncio.sleep(self.config.reconnect_delay)
                        continue
                except Exception as exc:
                    _logger.debug("BLE pre-connect scan failed: %s", exc)

            client = bleak.BleakClient(
                target_addr,
                timeout=self.config.connect_timeout,
            )
            try:
                _logger.info("Connecting to BLE device %s ...", self.config.address)
                await client.connect()
                self._state.connected = True
                self._state.reconnect_attempts = 0
                _logger.info("Connected to BLE device %s", self.config.address)

                # Drain stale queue items from while we were disconnected
                while not self._queue.empty():
                    try:
                        self._queue.get_nowait()
                    except queue.Empty:
                        break

                # Reset duplicate-suppression so the first post-reconnect
                # color update always reaches the device, even if the mood
                # hasn't changed while we were disconnected.
                self._last_payload = None

                # Initialize: power on + brightness
                await self._ble_write(client, build_ptreal_power_packet(True))
                await asyncio.sleep(0.3)
                await self._ble_write(client, build_ptreal_brightness_packet(100))
                await asyncio.sleep(0.1)
                self._ready_event.set()

                # Process color updates
                last_brightness = 100
                keepalive_interval = 2.0  # seconds between keep-alive packets
                last_keepalive_at = time.monotonic()
                while self._started and client.is_connected:
                    try:
                        item = self._queue.get(timeout=0.5)
                    except queue.Empty:
                        # No color update — send keep-alive if needed
                        now = time.monotonic()
                        if (now - last_keepalive_at) >= keepalive_interval:
                            try:
                                await self._ble_write(client, build_ble_keepalive_packet())
                            except Exception:
                                break  # connection likely lost
                            last_keepalive_at = now
                        continue

                    if item is _SHUTDOWN:
                        # Power off before exiting
                        try:
                            await self._ble_write(client, build_ptreal_power_packet(False))
                        except Exception:
                            pass
                        return

                    is_segment_frame = (
                        isinstance(item, tuple)
                        and len(item) == 3
                        and item[0] == _SEGMENT_FRAME
                    )
                    if is_segment_frame:
                        _tag, colors, brightness = item
                    else:
                        r, g, b, brightness = item

                    # Rate limit
                    now = time.monotonic()
                    wait = min_interval - (now - self._state.last_send_at)
                    if wait > 0:
                        await asyncio.sleep(wait)

                    # Update brightness only when changed
                    if brightness != last_brightness:
                        await self._ble_write(client, build_ptreal_brightness_packet(brightness))
                        last_brightness = brightness

                    # Set color using the appropriate protocol
                    if is_segment_frame:
                        if self.config.protocol == BleProtocol.BULB:
                            colors_list = list(colors)
                            mid = colors_list[len(colors_list) // 2] if colors_list else (0, 0, 0)
                            await self._ble_write(client, build_ble_bulb_color_packet(*mid))
                        else:
                            packets = build_ptreal_segment_packets(list(colors))
                            for pkt in packets:
                                await self._ble_write(client, pkt)
                    elif self.config.protocol == BleProtocol.BULB:
                        await self._ble_write(client, build_ble_bulb_color_packet(r, g, b))
                    else:
                        # SEGMENT: ptreal segment packets (H617A, H612F family)
                        seg_count = self.config.segments
                        packets = build_ptreal_segment_packets([(r, g, b)] * seg_count)
                        for pkt in packets:
                            await self._ble_write(client, pkt)
                    self._state.last_send_at = time.monotonic()
                    last_keepalive_at = time.monotonic()

            except Exception as exc:
                _logger.warning(
                    "BLE connection to %s failed: %s", self.config.address, exc
                )
            finally:
                self._ready_event.clear()
                self._state.connected = False
                try:
                    if client.is_connected:
                        await client.disconnect()
                except Exception:
                    pass

            if not self._started:
                return

            # Reconnect with capped backoff (fast retries, max 5s)
            self._state.reconnect_attempts += 1
            delay = min(5.0, self.config.reconnect_delay * min(self._state.reconnect_attempts, 3))
            _logger.info(
                "Reconnecting to %s in %.1fs (attempt %d)",
                self.config.address, delay, self._state.reconnect_attempts,
            )
            await asyncio.sleep(delay)

    async def _ble_write(self, client: Any, data: bytes) -> None:
        """Write a packet to the Govee BLE characteristic."""
        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, data, response=False)


# ---------------------------------------------------------------------------
# Multi-device BLE coordinator
# ---------------------------------------------------------------------------


class MultiBleAdapter:
    """Manages multiple BLE mood-follower devices."""

    def __init__(self, adapters: list[GoveeBleAdapter]) -> None:
        self.adapters = adapters

    def start_all(self) -> None:
        """Start all BLE adapter threads."""
        for adapter in self.adapters:
            adapter.start()

    def stop_all(self) -> None:
        """Stop all BLE adapter threads."""
        for adapter in self.adapters:
            adapter.stop()

    def send_color(self, r: int, g: int, b: int, brightness: int = 100) -> None:
        """Push a color update to all BLE devices."""
        for adapter in self.adapters:
            adapter.send_color(r, g, b, brightness)

    def emit(self, t: float, intent: LightingIntent) -> None:
        """Push a LightingIntent to all BLE devices."""
        for adapter in self.adapters:
            adapter.emit(t, intent)
