from __future__ import annotations

import base64
import json
import logging
import socket
import time
from dataclasses import dataclass
from typing import Callable

from dreamsync.director import LightingIntent
from dreamsync.output.roles import DeviceRole, transform_intent
from dreamsync.render import RenderMode, SegmentRenderer

_logger = logging.getLogger(__name__)

GOVEE_COMMAND_PORT = 4003


@dataclass(frozen=True)
class GoveeLanConfig:
    device_ip: str
    segments: int = 15
    fps: int = 30
    variant: int = 0xFA  # dreamview
    stretch: int = 0x01  # interpolate between segments
    port: int = GOVEE_COMMAND_PORT
    brightness: float = 1.0  # global brightness multiplier 0-1
    use_razer: bool = True  # False = fall back to colorwc (whole-strip single color)


def _xor_checksum(data: bytes) -> int:
    result = 0
    for b in data:
        result ^= b
    return result


def build_razer_packet(
    colors: list[tuple[int, int, int]],
    variant: int = 0xFA,
    stretch: int = 0x01,
) -> bytes:
    """Build the binary razer packet for per-segment color control.

    Packet layout:
        Byte 0:    0xBB           magic
        Byte 1:    0x00           reserved
        Byte 2:    variant        0xFA=dreamview, 0x0E=chroma, 0x20=govee
        Byte 3:    0xB0           reserved
        Byte 4:    stretch        0x00=discrete, 0x01=interpolate
        Byte 5:    count          number of RGB triplets
        Bytes 6+:  R,G,B, ...
        Last byte: XOR checksum of all preceding bytes
    """
    count = len(colors)
    header = bytes([0xBB, 0x00, variant, 0xB0, stretch, count])
    rgb_data = b""
    for r, g, b in colors:
        rgb_data += bytes([r & 0xFF, g & 0xFF, b & 0xFF])
    body = header + rgb_data
    checksum = _xor_checksum(body)
    return body + bytes([checksum])


def build_razer_json(packet: bytes) -> bytes:
    """Wrap a razer binary packet in the JSON envelope for UDP transmission."""
    encoded = base64.b64encode(packet).decode("ascii")
    msg = {"msg": {"cmd": "razer", "data": {"pt": encoded}}}
    return json.dumps(msg, separators=(",", ":")).encode("utf-8")


def build_command_json(cmd: str, data: dict) -> bytes:
    """Build a generic Govee LAN command JSON payload."""
    msg = {"msg": {"cmd": cmd, "data": data}}
    return json.dumps(msg, separators=(",", ":")).encode("utf-8")


def _default_udp_transport(payload: bytes, ip: str, port: int) -> None:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(payload, (ip, port))
        sock.close()
    except OSError as exc:
        _logger.warning("Govee UDP send error to %s:%d: %s", ip, port, exc)


def _parse_hex_color(color: str) -> tuple[int, int, int]:
    h = color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


class GoveeLanAdapter:
    """Sends per-segment RGB frames to a Govee device over UDP."""

    def __init__(
        self,
        config: GoveeLanConfig,
        transport: Callable[[bytes, str, int], None] | None = None,
        monotonic_fn: Callable[[], float] | None = None,
    ) -> None:
        self.config = config
        self._transport = transport or _default_udp_transport
        self._monotonic = monotonic_fn or time.monotonic
        self._min_frame_interval = 1.0 / max(1, config.fps)
        self._last_frame_at = -1e9

    def send_frame(self, colors: list[tuple[int, int, int]]) -> bool:
        """Send a frame of RGB segment colors to the device.

        Returns True if the frame was sent, False if rate-limited.
        """
        now = self._monotonic()
        if (now - self._last_frame_at) < self._min_frame_interval:
            return False

        # Apply global brightness
        if self.config.brightness < 1.0:
            factor = max(0.0, min(1.0, self.config.brightness))
            colors = [
                (int(r * factor), int(g * factor), int(b * factor))
                for r, g, b in colors
            ]

        if self.config.use_razer:
            packet = build_razer_packet(colors, self.config.variant, self.config.stretch)
            payload = build_razer_json(packet)
        else:
            # Fallback: pick center pixel and send as whole-strip colorwc
            mid = len(colors) // 2
            r, g, b = colors[mid] if colors else (0, 0, 0)
            payload = build_command_json(
                "colorwc", {"color": {"r": r, "g": g, "b": b}, "colorTemInKelvin": 0}
            )
        self._transport(payload, self.config.device_ip, self.config.port)
        self._last_frame_at = now
        return True

    def turn_on(self) -> None:
        payload = build_command_json("turn", {"value": 1})
        self._transport(payload, self.config.device_ip, self.config.port)

    def turn_off(self) -> None:
        payload = build_command_json("turn", {"value": 0})
        self._transport(payload, self.config.device_ip, self.config.port)

    def set_brightness(self, value: int) -> None:
        """Set device hardware brightness (0-100)."""
        value = max(0, min(100, value))
        payload = build_command_json("brightness", {"value": value})
        self._transport(payload, self.config.device_ip, self.config.port)

    def set_solid_color(self, r: int, g: int, b: int) -> None:
        """Set the entire strip to a single color via the colorwc command."""
        payload = build_command_json(
            "colorwc", {"color": {"r": r, "g": g, "b": b}, "colorTemInKelvin": 0}
        )
        self._transport(payload, self.config.device_ip, self.config.port)

    def emit(self, t: float, intent: LightingIntent) -> bool:
        """OutputAdapter-compatible emit: renders intent as a solid color frame.

        For full visual effects, use a SegmentRenderer (Phase 2) to convert
        intents into frame buffers and call send_frame() directly.
        """
        del t
        if intent.color is not None:
            r, g, b = _parse_hex_color(intent.color)
        else:
            # Default to warm white when no color specified
            r, g, b = 255, 180, 100

        # Scale by intent intensity
        factor = max(0.0, min(1.0, intent.intensity))
        r = int(r * factor)
        g = int(g * factor)
        b = int(b * factor)

        colors = [(r, g, b)] * self.config.segments
        return self.send_frame(colors)


@dataclass(frozen=True)
class GoveeDeviceSpec:
    """Parsed --device spec: IP:SEGMENTS[:ROLE]."""

    ip: str
    segments: int
    role: DeviceRole = DeviceRole.PRIMARY


def parse_device_spec(spec: str) -> GoveeDeviceSpec:
    """Parse 'IP:SEGMENTS' or 'IP:SEGMENTS:ROLE' into a GoveeDeviceSpec."""
    parts = spec.split(":")
    if len(parts) < 2:
        raise ValueError(
            f"Device spec must be IP:SEGMENTS or IP:SEGMENTS:ROLE, got: {spec!r}"
        )
    ip = parts[0]
    segments = int(parts[1])
    role = DeviceRole(parts[2]) if len(parts) >= 3 else DeviceRole.PRIMARY
    return GoveeDeviceSpec(ip=ip, segments=segments, role=role)


class MultiGoveeLanAdapter:
    """Drives multiple Govee devices simultaneously with per-device roles and renderers."""

    def __init__(
        self,
        devices: list[tuple[GoveeLanAdapter, SegmentRenderer, DeviceRole]],
    ) -> None:
        self.devices = devices

    def activate(self, brightness: int = 100) -> None:
        """Turn on all devices and set brightness."""
        for adapter, _renderer, _role in self.devices:
            adapter.turn_on()
            adapter.set_brightness(brightness)

    def send_frame(
        self, t: float, intent: LightingIntent, beat: bool = False
    ) -> bool:
        """Render and send one frame to all devices, applying role transforms."""
        any_sent = False
        for adapter, renderer, role in self.devices:
            device_intent = transform_intent(intent, role)
            colors = renderer.render(t, device_intent, beat=beat)
            if adapter.send_frame(colors):
                any_sent = True
        return any_sent
