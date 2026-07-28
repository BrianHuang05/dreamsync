from __future__ import annotations

import base64
import json
import logging
import socket
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from dreamsync.director import EffectMode, LightingIntent
from dreamsync.output.roles import DeviceRole, DeviceType, adapt_render_mode, transform_intent
from dreamsync.render import RenderMode, SegmentRenderer
from dreamsync.spatial.grid import resolve_grid_cell
from dreamsync.spatial.models import DevicePlacement, GridCell, SpatialCellState

_logger = logging.getLogger(__name__)

GOVEE_COMMAND_PORT = 4003

_SPATIAL_RENDER_PARAM_KEYS = frozenset({
    "active_eq_routes",
    "active_instrument_routes",
    "color_bias",
    "duration_s",
    "effect_layer",
    "eq_layers",
    "eq_routes",
    "falloff",
    "instrument_routes",
    "intensity_scale",
    "layer_category",
    "layer_priority",
    "radius",
    "scene_layers",
    "spatial_blend",
    "spatial_delay_ms",
    "spatial_direction",
    "spatial_extent",
    "spatial_mode",
    "spatial_origin",
    "spatial_preset",
    "spatial_width",
    "speed_units_per_second",
    "time_offset_s",
    "trigger_mode",
})


class TransportMode(str, Enum):
    RAZER = "razer"      # DreamView per-segment (0xBB packets)
    PTREAL = "ptreal"    # BLE-over-LAN per-segment (0x33 packets)
    COLORWC = "colorwc"  # Whole-strip single color fallback


@dataclass(frozen=True)
class GoveeLanConfig:
    device_ip: str
    segments: int = 15
    fps: int = 30
    port: int = GOVEE_COMMAND_PORT
    brightness: float = 1.0  # global brightness multiplier 0-1
    transport: TransportMode = TransportMode.RAZER


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _xor_checksum(data: bytes) -> int:
    result = 0
    for b in data:
        result ^= b
    return result


def _parse_hex_color(color: str) -> tuple[int, int, int]:
    h = color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _scale_rgb(color: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    factor = max(0.0, min(1.5, factor))
    return (
        int(max(0, min(255, color[0] * factor))),
        int(max(0, min(255, color[1] * factor))),
        int(max(0, min(255, color[2] * factor))),
    )


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


# ---------------------------------------------------------------------------
# Razer / DreamView protocol
# ---------------------------------------------------------------------------

def build_razer_packet(
    colors: list[tuple[int, int, int]],
    gradient: int = 0x01,
) -> bytes:
    """Build the binary razer/DreamView packet for per-LED color control.

    Packet layout (from OpenRGB / SignalRGB reverse engineering):
        Byte 0:    0xBB           magic
        Byte 1:    size_hi        data payload size >> 8
        Byte 2:    size_lo        data payload size & 0xFF
        Byte 3:    0xB0           DreamView command
        Byte 4:    gradient       0x00=discrete, 0x01=interpolate/stretch
        Byte 5:    count          number of RGB triplets
        Bytes 6+:  R,G,B, ...
        Last byte: XOR checksum of all preceding bytes

    When *count* equals the device's LED count, every individual LED
    can be controlled.  Use gradient=1 to interpolate smoothly when
    sending fewer colors than LEDs.
    """
    count = len(colors)
    data_size = 2 + (3 * count)  # gradient + count + RGB data
    header = bytes([
        0xBB,
        (data_size >> 8) & 0xFF,
        data_size & 0xFF,
        0xB0,
        gradient & 0xFF,
        count & 0xFF,
    ])
    rgb_data = b""
    for r, g, b in colors:
        rgb_data += bytes([r & 0xFF, g & 0xFF, b & 0xFF])
    body = header + rgb_data
    checksum = _xor_checksum(body)
    return body + bytes([checksum])


def build_razer_activate_packet(enable: bool = True) -> bytes:
    """Build the razer mode activation/deactivation packet.

    Must be sent before any DreamView LED data.  The device reverts
    to its previous mode if no LED data is received within 60 seconds.
    """
    body = bytes([0xBB, 0x00, 0x01, 0xB1, 0x01 if enable else 0x00])
    checksum = _xor_checksum(body)
    return body + bytes([checksum])


def build_razer_json(packet: bytes) -> bytes:
    """Wrap a razer binary packet in the JSON envelope for UDP transmission."""
    encoded = base64.b64encode(packet).decode("ascii")
    msg = {"msg": {"cmd": "razer", "data": {"pt": encoded}}}
    return json.dumps(msg, separators=(",", ":")).encode("utf-8")


# ---------------------------------------------------------------------------
# ptReal / BLE-over-LAN protocol
# ---------------------------------------------------------------------------

def _ptreal_checksum(packet: list[int]) -> int:
    """XOR checksum over the first 19 bytes of a 20-byte BLE packet."""
    result = 0
    for b in packet[:19]:
        result ^= b
    return result


def _segment_bitmask(segment_indices: list[int]) -> list[int]:
    """Convert 0-based segment indices to a 7-byte little-endian bitmask."""
    mask = [0] * 7
    for idx in segment_indices:
        byte_idx = idx // 8
        bit_idx = idx % 8
        if 0 <= byte_idx < 7:
            mask[byte_idx] |= (1 << bit_idx)
    return mask


def build_ptreal_segment_packets(
    colors: list[tuple[int, int, int]],
) -> list[bytes]:
    """Build ptReal BLE packets for per-segment color control.

    Groups segments by color to minimize packet count. Returns one
    20-byte ``33 05 15 01`` packet per unique color.
    """
    # Group segment indices by color
    color_to_segments: dict[tuple[int, int, int], list[int]] = {}
    for idx, color in enumerate(colors):
        clamped = (color[0] & 0xFF, color[1] & 0xFF, color[2] & 0xFF)
        color_to_segments.setdefault(clamped, []).append(idx)

    packets: list[bytes] = []
    for (r, g, b), indices in color_to_segments.items():
        bitmask = _segment_bitmask(indices)
        packet = [
            0x33, 0x05, 0x15, 0x01,        # header: segment color command
            r, g, b,                         # RGB
            0x00, 0x00, 0x00, 0x00, 0x00,   # padding
        ] + bitmask                          # 7-byte segment bitmask
        # Pad to exactly 19 bytes
        while len(packet) < 19:
            packet.append(0x00)
        packet = packet[:19]
        packet.append(_ptreal_checksum(packet))
        packets.append(bytes(packet))

    return packets


def build_ptreal_power_packet(on: bool) -> bytes:
    """Build a ptReal power on/off packet: ``33 01 01/00``."""
    packet = [0x33, 0x01, 0x01 if on else 0x00] + [0x00] * 16
    packet.append(_ptreal_checksum(packet))
    return bytes(packet)


def build_ptreal_brightness_packet(value: int) -> bytes:
    """Build a ptReal global brightness packet: ``33 04 XX``.

    *value* is a percentage 0-100.  The BLE protocol byte uses a 0-255
    raw scale, so we convert internally.
    """
    raw = max(0, min(255, int(value * 255 / 100)))
    packet = [0x33, 0x04, raw] + [0x00] * 16
    packet.append(_ptreal_checksum(packet))
    return bytes(packet)


def build_ptreal_json(packets: list[bytes]) -> bytes:
    """Wrap one or more BLE packets in the ptReal JSON envelope."""
    commands = [base64.b64encode(p).decode("ascii") for p in packets]
    msg = {"msg": {"cmd": "ptReal", "data": {"command": commands}}}
    return json.dumps(msg, separators=(",", ":")).encode("utf-8")


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

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
        self.last_send_ok: bool = True
        self.paused: bool = False

    def send_frame(self, colors: list[tuple[int, int, int]]) -> bool:
        """Send a frame of RGB segment colors to the device.

        Returns True if the frame was sent, False if rate-limited or paused.
        """
        if self.paused:
            return False

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

        mode = self.config.transport
        if mode == TransportMode.RAZER:
            packet = build_razer_packet(colors)
            payload = build_razer_json(packet)
        elif mode == TransportMode.PTREAL:
            packets = build_ptreal_segment_packets(colors)
            payload = build_ptreal_json(packets)
        else:
            # COLORWC fallback: pick center pixel
            mid = len(colors) // 2
            r, g, b = colors[mid] if colors else (0, 0, 0)
            payload = build_command_json(
                "colorwc", {"color": {"r": r, "g": g, "b": b}, "colorTemInKelvin": 0}
            )
        try:
            self._transport(payload, self.config.device_ip, self.config.port)
            self.last_send_ok = True
        except OSError as exc:
            _logger.warning("send_frame failed for %s: %s", self.config.device_ip, exc)
            self.last_send_ok = False
        self._last_frame_at = now
        return True

    def turn_on(self) -> None:
        if self.config.transport == TransportMode.PTREAL:
            payload = build_ptreal_json([build_ptreal_power_packet(True)])
        else:
            payload = build_command_json("turn", {"value": 1})
        self._transport(payload, self.config.device_ip, self.config.port)
        # Activate razer/DreamView mode after power-on
        if self.config.transport == TransportMode.RAZER:
            activate = build_razer_json(build_razer_activate_packet(True))
            self._transport(activate, self.config.device_ip, self.config.port)

    def turn_off(self) -> None:
        # Deactivate razer mode before power-off
        if self.config.transport == TransportMode.RAZER:
            deactivate = build_razer_json(build_razer_activate_packet(False))
            self._transport(deactivate, self.config.device_ip, self.config.port)
        if self.config.transport == TransportMode.PTREAL:
            payload = build_ptreal_json([build_ptreal_power_packet(False)])
        else:
            payload = build_command_json("turn", {"value": 0})
        self._transport(payload, self.config.device_ip, self.config.port)

    def set_brightness(self, value: int) -> None:
        """Set device hardware brightness (0-100)."""
        value = max(0, min(100, value))
        if self.config.transport == TransportMode.PTREAL:
            payload = build_ptreal_json([build_ptreal_brightness_packet(value)])
        else:
            payload = build_command_json("brightness", {"value": value})
        self._transport(payload, self.config.device_ip, self.config.port)

    def set_solid_color(self, r: int, g: int, b: int) -> None:
        """Set the entire strip to a single color via the colorwc command."""
        payload = build_command_json(
            "colorwc", {"color": {"r": r, "g": g, "b": b}, "colorTemInKelvin": 0}
        )
        self._transport(payload, self.config.device_ip, self.config.port)

    def emit(self, t: float, intent: LightingIntent) -> bool:
        """OutputAdapter-compatible emit: renders intent as a solid color frame."""
        del t
        if intent.color is not None:
            r, g, b = _parse_hex_color(intent.color)
        else:
            r, g, b = 255, 180, 100

        factor = max(0.0, min(1.0, intent.intensity))
        r = int(r * factor)
        g = int(g * factor)
        b = int(b * factor)

        colors = [(r, g, b)] * self.config.segments
        return self.send_frame(colors)


# ---------------------------------------------------------------------------
# Multi-device & CLI helpers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GoveeDeviceSpec:
    """Parsed --device spec: IP:SEGMENTS[:ROLE[:TRANSPORT]]."""

    ip: str
    segments: int
    role: DeviceRole = DeviceRole.PRIMARY
    transport: TransportMode | None = None


def parse_device_spec(spec: str) -> GoveeDeviceSpec:
    """Parse 'IP:SEGMENTS[:ROLE[:TRANSPORT]]' into a GoveeDeviceSpec."""
    parts = spec.split(":")
    if len(parts) < 2 or len(parts) > 4:
        raise ValueError(
            f"Device spec must be IP:SEGMENTS[:ROLE[:TRANSPORT]], got: {spec!r}"
        )
    ip = parts[0]
    segments = int(parts[1])
    role = DeviceRole(parts[2]) if len(parts) >= 3 else DeviceRole.PRIMARY
    transport = TransportMode(parts[3]) if len(parts) >= 4 else None
    return GoveeDeviceSpec(ip=ip, segments=segments, role=role, transport=transport)


class MultiGoveeLanAdapter:
    """Drives multiple Govee devices simultaneously with per-device roles and renderers.

    Optionally includes BLE mood-follower devices that receive a single
    (color, brightness) derived from the current LightingIntent rather
    than per-segment RGB frames.
    """

    def __init__(
        self,
        devices: list[tuple],  # legacy tuples or (adapter, renderer, role, brightness_scale, placement)
        ble_followers: list | None = None,
        spatial_mapper=None,
    ) -> None:
        # Normalize to 5-tuples:
        # (adapter, renderer, role, brightness_scale, placement)
        self.devices: list[
            tuple[GoveeLanAdapter, SegmentRenderer, DeviceRole, float, DevicePlacement | None]
        ] = [self._normalize_device_tuple(d) for d in devices]
        # list of GoveeBleAdapter instances or tuples carrying follower metadata
        self._ble_followers: list = ble_followers or []
        self._spatial_mapper = spatial_mapper
        self._prepared_spatial_cues: dict[object, tuple[object, tuple]] = {}
        self._placement_section_cache: dict[tuple[int, int], tuple[DevicePlacement, ...]] = {}
        self._last_output_colors: dict[str, tuple[tuple[int, int, int], ...]] = {}
        self._last_frame_diagnostics: dict[str, Any] = {}
        self._frame_trace_enabled = False
        self._frame_trace_sample_every = 1
        self._frame_trace_counter = 0
        self._frame_trace: deque[dict[str, Any]] = deque(maxlen=120)

    def configure_frame_trace(
        self,
        *,
        enabled: bool,
        max_frames: int = 120,
        sample_every: int = 1,
    ) -> None:
        """Enable a bounded output-frame trace outside the audio callback."""

        bounded_max = max(1, min(3600, int(max_frames)))
        existing = tuple(self._frame_trace)[-bounded_max:]
        self._frame_trace = deque(existing, maxlen=bounded_max)
        self._frame_trace_enabled = bool(enabled)
        self._frame_trace_sample_every = max(1, int(sample_every))
        self._frame_trace_counter = 0

    def frame_trace_snapshot(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(row) for row in self._frame_trace)

    def final_frame_snapshot(self) -> dict[str, Any]:
        """Return the exact final per-device RGB values submitted to adapters."""

        node_colors: dict[str, str] = {}
        device_rgb: dict[str, tuple[tuple[int, int, int], ...]] = {}
        for address, colors in self._last_output_colors.items():
            device_rgb[address] = tuple(colors)
            if len(colors) == 1:
                node_colors[address] = self._rgb_hex(colors[0])
            else:
                for index, color in enumerate(colors):
                    node_colors[f"{address}#section:{index}"] = self._rgb_hex(color)
        return {
            "node_colors": node_colors,
            "device_rgb": device_rgb,
            "frame_diagnostics": dict(self._last_frame_diagnostics),
        }

    @staticmethod
    def _rgb_hex(color: tuple[int, int, int]) -> str:
        return "#{:02x}{:02x}{:02x}".format(*color)

    @staticmethod
    def _renderer_mode_name(renderer: Any) -> str:
        mode = getattr(renderer, "mode", "")
        return str(getattr(mode, "value", mode) or "")

    def _finish_frame_diagnostics(
        self,
        *,
        t: float,
        intent: LightingIntent,
        beat: bool,
        params: dict | None,
        devices: list[dict[str, Any]],
    ) -> None:
        pixels = [
            color
            for device in devices
            for color in device.get("post_spatial_rgb", ())
        ]
        levels = [max(color) for color in pixels]
        all_black = bool(pixels) and all(max(color) == 0 for color in pixels)
        achromatic = bool(pixels) and all(
            max(color) - min(color) <= 1 for color in pixels
        )
        base_rgb = (
            _parse_hex_color(intent.color)
            if intent.color
            else (255, 180, 100)
        )
        expected_chromatic = max(base_rgb) - min(base_rgb) > 1
        provenance = (
            dict(params.get("_frame_provenance", {}))
            if params and isinstance(params.get("_frame_provenance"), dict)
            else {}
        )
        row: dict[str, Any] = {
            **provenance,
            "output_t": float(t),
            "beat": bool(beat),
            "base_color": intent.color,
            "base_intensity": float(intent.intensity),
            "base_speed": float(intent.speed),
            "base_bpm": float(intent.bpm),
            "effective_render_mode": (
                str(params.get("_render_mode", ""))
                if params
                else ""
            ),
            "min_rgb_level": min(levels) if levels else 0,
            "max_rgb_level": max(levels) if levels else 0,
            "all_black": all_black,
            "unexpected_achromatic": bool(achromatic and expected_chromatic),
            "spatial_changed": any(
                tuple(device.get("pre_spatial_rgb", ()))
                != tuple(device.get("post_spatial_rgb", ()))
                for device in devices
            ),
            "devices": tuple(devices),
        }
        self._last_frame_diagnostics = row
        self._frame_trace_counter += 1
        if (
            self._frame_trace_enabled
            and self._frame_trace_counter % self._frame_trace_sample_every == 0
        ):
            self._frame_trace.append(row)

    def activate(self, brightness: int = 100) -> None:
        """Turn on all devices and set brightness.

        Includes delays between commands so devices have time to process
        power-on before receiving brightness and color data.
        BLE followers are started (background threads launched).
        """
        for adapter, _renderer, _role, _bs, _placement in self.devices:
            adapter.turn_on()
        time.sleep(0.8)
        for adapter, _renderer, _role, _bs, _placement in self.devices:
            adapter.set_brightness(brightness)
        time.sleep(0.3)
        # Start BLE follower threads
        for follower in self._ble_followers:
            self._ble_adapter_for(follower).start()

    def deactivate(self) -> None:
        """Stop BLE follower threads."""
        for follower in self._ble_followers:
            self._ble_adapter_for(follower).stop()

    def replace_devices(
        self,
        new_devices: list[tuple],
    ) -> None:
        """Atomically replace the device list (GIL-safe reference swap)."""
        self.devices = [self._normalize_device_tuple(d) for d in new_devices]
        self._placement_section_cache.clear()

    def replace_ble_followers(self, new_followers: list) -> None:
        """Atomically replace the BLE follower list (GIL-safe reference swap)."""
        self._ble_followers = new_followers

    def get_device_addresses(self) -> list[str]:
        """Return the IP/address of every LAN device currently in the list."""
        return [adapter.config.device_ip for adapter, _, _, _, _ in self.devices]

    def send_frame(
        self, t: float, intent: LightingIntent, beat: bool = False,
        params: dict | None = None,
    ) -> bool:
        """Render and send one frame to all devices, applying role transforms.

        BLE mood followers receive the intent's color + intensity directly.
        """
        if self._spatial_mapper is not None and getattr(self._spatial_mapper, "enabled", False):
            return self.send_continuous_spatial_frame(t, intent, beat=beat, params=params)

        any_sent = False
        render_params = self._public_render_params(params)
        diagnostic_devices: list[dict[str, Any]] = []
        for adapter, renderer, role, bs, _placement in self.devices:
            device_intent = transform_intent(intent, role, brightness_scale=bs)
            self._apply_render_mode_override(renderer, params)
            colors = renderer.render(t, device_intent, beat=beat, params=render_params)
            address = str(adapter.config.device_ip)
            self._last_output_colors[address] = tuple(colors)
            diagnostic_devices.append({
                "address": address,
                "role": getattr(role, "value", str(role)),
                "brightness_scale": float(bs),
                "render_mode": self._renderer_mode_name(renderer),
                "pre_spatial_rgb": tuple(colors),
                "post_spatial_rgb": tuple(colors),
            })
            if adapter.send_frame(colors):
                any_sent = True
        # Push to BLE followers (fire-and-forget, they rate-limit internally)
        for follower in self._ble_followers:
            self._ble_adapter_for(follower).emit(t, intent)
            any_sent = True
        self._finish_frame_diagnostics(
            t=t,
            intent=intent,
            beat=beat,
            params=params,
            devices=diagnostic_devices,
        )
        return any_sent

    def send_spatial_scene(
        self,
        t: float,
        scene: dict[GridCell, SpatialCellState],
        *,
        beat: bool = False,
        base_intent: LightingIntent | None = None,
    ) -> bool:
        """Render and send a spatial scene to all devices."""
        any_sent = False
        diagnostic_devices: list[dict[str, Any]] = []
        fallback_state = scene.get(GridCell.CENTER)
        if fallback_state is None:
            raise ValueError("Spatial scene must include GridCell.CENTER.")

        for adapter, renderer, role, bs, placement in self.devices:
            cell = resolve_grid_cell(placement)
            cell_state = scene.get(cell, fallback_state) if cell is not None else fallback_state
            device_intent = transform_intent(
                cell_state.intent,
                role,
                brightness_scale=bs,
            )
            self._apply_render_mode_override(renderer, cell_state.params)
            colors = self._render_with_orientation(
                renderer,
                t,
                device_intent,
                beat=beat,
                params=cell_state.params,
                placement=placement,
            )
            address = str(adapter.config.device_ip)
            self._last_output_colors[address] = tuple(colors)
            diagnostic_devices.append({
                "address": address,
                "role": getattr(role, "value", str(role)),
                "brightness_scale": float(bs),
                "render_mode": self._renderer_mode_name(renderer),
                "pre_spatial_rgb": tuple(colors),
                "post_spatial_rgb": tuple(colors),
            })
            if adapter.send_frame(colors):
                any_sent = True

        follower_intent = base_intent if base_intent is not None else fallback_state.intent
        for follower in self._ble_followers:
            self._ble_adapter_for(follower).emit(t, follower_intent)
            any_sent = True

        self._finish_frame_diagnostics(
            t=t,
            intent=base_intent if base_intent is not None else fallback_state.intent,
            beat=beat,
            params=fallback_state.params,
            devices=diagnostic_devices,
        )
        return any_sent

    def send_continuous_spatial_frame(
        self,
        t: float,
        intent: LightingIntent,
        *,
        beat: bool = False,
        params: dict | None = None,
    ) -> bool:
        if self._spatial_mapper is None:
            return self.send_frame(t, intent, beat=beat, params=params)

        spec, layers = self._resolve_runtime_spatial_layers(intent, params=params)
        spatial_t = self._spatial_time(t, params)
        render_params = self._public_render_params(params)
        any_sent = False
        diagnostic_devices: list[dict[str, Any]] = []

        for adapter, renderer, role, bs, placement in self.devices:
            device_intent = transform_intent(intent, role, brightness_scale=bs)
            self._apply_render_mode_override(renderer, params)
            pre_spatial_colors = self._render_with_orientation(
                renderer,
                t,
                device_intent,
                beat=beat,
                params=render_params,
                placement=placement,
            )
            colors = self._spatialize_colors(
                pre_spatial_colors,
                placement=placement,
                t=spatial_t,
                intent=device_intent,
                spec=spec,
                layers=layers,
            )
            address = str(adapter.config.device_ip)
            self._last_output_colors[address] = tuple(colors)
            diagnostic_devices.append({
                "address": address,
                "role": getattr(role, "value", str(role)),
                "brightness_scale": float(bs),
                "render_mode": self._renderer_mode_name(renderer),
                "pre_spatial_rgb": tuple(pre_spatial_colors),
                "post_spatial_rgb": tuple(colors),
            })
            if adapter.send_frame(colors):
                any_sent = True

        for follower in self._ble_followers:
            follower_adapter, follower_role, follower_bs, follower_placement, follower_renderer = self._normalize_ble_follower(follower)
            follower_intent = transform_intent(intent, follower_role, brightness_scale=follower_bs)
            if follower_renderer is not None:
                self._apply_render_mode_override(follower_renderer, params)
                follower_colors = self._render_with_orientation(
                    follower_renderer,
                    t,
                    follower_intent,
                    beat=beat,
                    params=render_params,
                    placement=follower_placement,
                )
                follower_colors = self._spatialize_colors(
                    follower_colors,
                    placement=follower_placement,
                    t=spatial_t,
                    intent=follower_intent,
                    spec=spec,
                    layers=layers,
                )
                brightness = max(30, min(100, int(max(0.0, min(1.0, follower_intent.intensity)) * 100)))
                follower_adapter.send_segment_colors(follower_colors, brightness=brightness)
            elif follower_placement is not None:
                sample = self._spatial_mapper.sample_point(spatial_t, follower_placement, follower_intent, spec)
                layer_samples = self._layer_samples(
                    t=spatial_t,
                    placement=follower_placement,
                    intent=follower_intent,
                    layers=layers,
                )
                color = self._resolve_sampled_color(
                    follower_intent.color,
                    sample,
                    layer_samples,
                )
                intensity_scale = self._resolve_sampled_intensity(sample, layer_samples)
                follower_intent = LightingIntent(
                    mode=follower_intent.mode,
                    intensity=max(0.0, min(1.0, follower_intent.intensity * intensity_scale)),
                    speed=follower_intent.speed,
                    bpm=follower_intent.bpm,
                    color=color,
                )
            follower_adapter.emit(t, follower_intent)
            any_sent = True

        self._finish_frame_diagnostics(
            t=t,
            intent=intent,
            beat=beat,
            params=params,
            devices=diagnostic_devices,
        )
        return any_sent

    def send_baked_frame(
        self,
        t: float,
        node_colors: dict[str, str],
        *,
        fallback_color: str = "#000000",
    ) -> bool:
        """Send precomputed logical node colors to devices.

        Section keys use ``"{address}#section:{index}"``. A device-level
        ``"{address}"`` color fills the entire device when section colors are
        absent. Missing devices receive *fallback_color*.
        """
        any_sent = False
        diagnostic_devices: list[dict[str, Any]] = []
        for adapter, renderer, _role, _bs, _placement in self.devices:
            address = str(adapter.config.device_ip)
            colors = self._baked_device_colors(
                address,
                max(1, int(getattr(renderer, "segments", 1))),
                node_colors,
                fallback_color=fallback_color,
            )
            self._last_output_colors[address] = tuple(colors)
            diagnostic_devices.append({
                "address": address,
                "role": "baked",
                "brightness_scale": 1.0,
                "render_mode": "baked",
                "pre_spatial_rgb": tuple(colors),
                "post_spatial_rgb": tuple(colors),
            })
            if adapter.send_frame(colors):
                any_sent = True

        fallback_intent = LightingIntent(
            mode=EffectMode.AMBIENT,
            intensity=1.0,
            speed=0.0,
            bpm=120.0,
            color=fallback_color,
        )
        for follower in self._ble_followers:
            self._ble_adapter_for(follower).emit(t, fallback_intent)
            any_sent = True

        self._finish_frame_diagnostics(
            t=t,
            intent=fallback_intent,
            beat=False,
            params={"_render_mode": "baked"},
            devices=diagnostic_devices,
        )
        return any_sent

    def prepare_spatial_cue(self, key: object, intent: LightingIntent, params: dict | None = None) -> bool:
        if self._spatial_mapper is None:
            return False
        self._prepared_spatial_cues[key] = self._spatial_mapper.resolve_spatial_layers(intent, params=params)
        return True

    def clear_prepared_spatial_cues(self) -> None:
        self._prepared_spatial_cues.clear()
        self._placement_section_cache.clear()

    @staticmethod
    def _normalize_device_tuple(device: tuple):
        if len(device) == 3:
            return (device[0], device[1], device[2], 1.0, None)
        if len(device) == 4:
            return (device[0], device[1], device[2], device[3], None)
        if len(device) == 5:
            return device
        raise ValueError(f"Device tuple must have length 3, 4, or 5; got {len(device)}")

    @staticmethod
    def _baked_device_colors(
        address: str,
        segments: int,
        node_colors: dict[str, str],
        *,
        fallback_color: str,
    ) -> list[tuple[int, int, int]]:
        device_color = node_colors.get(address, fallback_color)
        colors: list[tuple[int, int, int]] = []
        for index in range(max(1, int(segments))):
            key = f"{address}#section:{index}"
            colors.append(_parse_hex_color(node_colors.get(key, device_color)))
        return colors

    @staticmethod
    def _ble_adapter_for(follower):
        if isinstance(follower, tuple):
            return follower[0]
        return follower

    @staticmethod
    def _normalize_ble_follower(follower):
        if isinstance(follower, tuple):
            if len(follower) == 4:
                return follower[0], follower[1], follower[2], follower[3], None
            if len(follower) == 5:
                return follower
            raise ValueError(f"BLE follower tuple must have length 4 or 5; got {len(follower)}")
        return follower, DeviceRole.PRIMARY, 1.0, None, None

    def _resolve_runtime_spatial_layers(
        self,
        intent: LightingIntent,
        *,
        params: dict | None,
    ) -> tuple[object, tuple]:
        key = params.get("_prepared_spatial_key") if params else None
        if key is not None and key in self._prepared_spatial_cues:
            return self._prepared_spatial_cues[key]
        return self._spatial_mapper.resolve_spatial_layers(intent, params=params)

    @staticmethod
    def _public_render_params(params: dict | None) -> dict | None:
        if not params:
            return params
        return {
            key: value
            for key, value in params.items()
            if not str(key).startswith("_") and str(key) not in _SPATIAL_RENDER_PARAM_KEYS
        }

    @staticmethod
    def _spatial_time(t: float, params: dict | None) -> float:
        if not params or "_spatial_t" not in params:
            return t
        try:
            return max(0.0, float(params["_spatial_t"]))
        except (TypeError, ValueError):
            return t

    @staticmethod
    def _render_with_orientation(
        renderer: SegmentRenderer,
        t: float,
        intent: LightingIntent,
        *,
        beat: bool,
        params: dict | None,
        placement: DevicePlacement | None,
    ) -> list[tuple[int, int, int]]:
        if placement is None or placement.orientation.value == "left_to_right":
            return renderer.render(t, intent, beat=beat, params=params)

        original_mirror = renderer.mirror
        try:
            renderer.mirror = not original_mirror
            return renderer.render(t, intent, beat=beat, params=params)
        finally:
            renderer.mirror = original_mirror

    @staticmethod
    def _apply_render_mode_override(
        renderer: SegmentRenderer,
        params: dict | None,
    ) -> None:
        if not params or "_render_mode" not in params:
            return
        raw_mode = str(params.get("_render_mode", "")).strip().lower()
        if not raw_mode:
            return
        if renderer.device_type is not None:
            try:
                raw_mode = adapt_render_mode(raw_mode, DeviceType(renderer.device_type))
            except ValueError:
                pass
        try:
            renderer.mode = RenderMode(raw_mode)
        except ValueError:
            return

    def _spatialize_colors(
        self,
        colors: list[tuple[int, int, int]],
        *,
        placement: DevicePlacement | None,
        t: float,
        intent: LightingIntent,
        spec,
        layers: tuple = (),
    ) -> list[tuple[int, int, int]]:
        if placement is None or self._spatial_mapper is None:
            return colors

        if placement.sections:
            section_placements = self._section_placements_for(placement, len(colors))
            result: list[tuple[int, int, int]] = []
            for color, section_placement in zip(colors, section_placements):
                sample = self._spatial_mapper.sample_point(t, section_placement, intent, spec)
                layer_samples = self._layer_samples(
                    t=t,
                    placement=section_placement,
                    intent=intent,
                    layers=layers,
                )
                result.append(self._apply_spatial_sample(color, sample, layer_samples))
            return result

        sample = self._spatial_mapper.sample_point(t, placement, intent, spec)
        layer_samples = self._layer_samples(
            t=t,
            placement=placement,
            intent=intent,
            layers=layers,
        )
        return [self._apply_spatial_sample(color, sample, layer_samples) for color in colors]

    def _section_placements_for(
        self,
        placement: DevicePlacement,
        color_count: int,
    ) -> tuple[DevicePlacement, ...]:
        cache_key = (id(placement), color_count)
        cached = self._placement_section_cache.get(cache_key)
        if cached is not None:
            return cached
        section_map = {section.index: section for section in placement.sections}
        placements: list[DevicePlacement] = []
        for index in range(color_count):
            section = section_map.get(index)
            if section is None:
                placements.append(placement)
                continue
            placements.append(DevicePlacement(
                x=section.x,
                y=section.y,
                z=section.z,
                orientation=placement.orientation,
                weight=section.weight,
                enabled=section.enabled,
            ))
        prepared = tuple(placements)
        self._placement_section_cache[cache_key] = prepared
        return prepared

    def _layer_samples(
        self,
        *,
        t: float,
        placement: DevicePlacement,
        intent: LightingIntent,
        layers: tuple,
    ) -> list[tuple[object, object]]:
        if self._spatial_mapper is None:
            return []
        samples: list[tuple[object, object]] = []
        for resolved in layers:
            sample = self._spatial_mapper.sample_point(t, placement, intent, resolved.spec)
            samples.append((resolved.layer, sample))
        return samples

    @staticmethod
    def _apply_spatial_sample(
        color: tuple[int, int, int],
        sample,
        layer_samples: list[tuple[object, object]] | None = None,
    ) -> tuple[int, int, int]:
        base_color = color
        if sample.color_override:
            base_color = _parse_hex_color(sample.color_override)
        result = _scale_rgb(base_color, sample.intensity_scale)
        if not layer_samples:
            return result

        for layer, layer_sample in layer_samples:
            alpha = max(0.0, min(1.0, layer.weight * layer_sample.intensity_scale))
            if alpha <= 0.0:
                continue
            layer_color = color
            if layer_sample.color_override:
                layer_color = _parse_hex_color(layer_sample.color_override)
            elif layer.color_override:
                layer_color = _parse_hex_color(layer.color_override)
            tinted = _scale_rgb(layer_color, alpha)
            blend_mode = str(layer.blend_mode or "max").lower()
            if blend_mode == "add":
                result = (
                    min(255, result[0] + tinted[0]),
                    min(255, result[1] + tinted[1]),
                    min(255, result[2] + tinted[2]),
                )
            elif blend_mode == "mix":
                result = (
                    int(result[0] + ((tinted[0] - result[0]) * alpha)),
                    int(result[1] + ((tinted[1] - result[1]) * alpha)),
                    int(result[2] + ((tinted[2] - result[2]) * alpha)),
                )
            else:
                result = (
                    max(result[0], tinted[0]),
                    max(result[1], tinted[1]),
                    max(result[2], tinted[2]),
                )
        return result

    @classmethod
    def _resolve_sampled_color(
        cls,
        base_color: str | None,
        sample,
        layer_samples: list[tuple[object, object]],
    ) -> str | None:
        color = sample.color_override or base_color
        strongest_key = (0, 0.0)
        for layer, layer_sample in layer_samples:
            strength = max(0.0, min(1.0, layer.weight * layer_sample.intensity_scale))
            priority = int(getattr(layer, "priority", 0))
            candidate_key = (priority, strength)
            if candidate_key < strongest_key:
                continue
            candidate = layer_sample.color_override or layer.color_override
            if isinstance(candidate, str):
                color = candidate
                strongest_key = candidate_key
        return color

    @staticmethod
    def _resolve_sampled_intensity(
        sample,
        layer_samples: list[tuple[object, object]],
    ) -> float:
        peak = sample.intensity_scale
        for layer, layer_sample in layer_samples:
            peak = max(peak, layer.weight * layer_sample.intensity_scale)
        return max(0.0, min(1.5, peak))
