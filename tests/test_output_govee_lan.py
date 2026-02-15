import base64
import json
import unittest

from dreamsync.director import EffectMode, LightingIntent
from dreamsync.output.govee_lan import (
    GoveeLanAdapter,
    GoveeLanConfig,
    GoveeDeviceSpec,
    MultiGoveeLanAdapter,
    TransportMode,
    build_razer_packet,
    build_razer_json,
    build_command_json,
    build_ptreal_segment_packets,
    build_ptreal_json,
    build_ptreal_power_packet,
    build_ptreal_brightness_packet,
    _ptreal_checksum,
    _segment_bitmask,
    parse_device_spec,
    _xor_checksum,
    _parse_hex_color,
)
from dreamsync.output.roles import DeviceRole
from dreamsync.render import RenderMode, SegmentRenderer


class XorChecksumTests(unittest.TestCase):
    def test_empty(self) -> None:
        self.assertEqual(_xor_checksum(b""), 0)

    def test_single_byte(self) -> None:
        self.assertEqual(_xor_checksum(b"\xAB"), 0xAB)

    def test_two_identical_bytes_cancel(self) -> None:
        self.assertEqual(_xor_checksum(b"\xFF\xFF"), 0)

    def test_known_value(self) -> None:
        self.assertEqual(_xor_checksum(b"\x01\x02\x03"), 0x01 ^ 0x02 ^ 0x03)


class BuildRazerPacketTests(unittest.TestCase):
    def test_header_structure(self) -> None:
        colors = [(255, 0, 0)]
        packet = build_razer_packet(colors, variant=0xFA, stretch=0x01)

        self.assertEqual(packet[0], 0xBB)  # magic
        self.assertEqual(packet[1], 0x00)  # reserved
        self.assertEqual(packet[2], 0xFA)  # variant
        self.assertEqual(packet[3], 0xB0)  # reserved
        self.assertEqual(packet[4], 0x01)  # stretch
        self.assertEqual(packet[5], 1)     # count

    def test_rgb_data_follows_header(self) -> None:
        colors = [(255, 128, 0), (0, 255, 64)]
        packet = build_razer_packet(colors)

        self.assertEqual(packet[5], 2)     # count
        self.assertEqual(packet[6], 255)   # R1
        self.assertEqual(packet[7], 128)   # G1
        self.assertEqual(packet[8], 0)     # B1
        self.assertEqual(packet[9], 0)     # R2
        self.assertEqual(packet[10], 255)  # G2
        self.assertEqual(packet[11], 64)   # B2

    def test_checksum_is_last_byte(self) -> None:
        colors = [(100, 200, 50)]
        packet = build_razer_packet(colors)

        body = packet[:-1]
        expected_checksum = _xor_checksum(body)
        self.assertEqual(packet[-1], expected_checksum)

    def test_packet_length(self) -> None:
        # 6 header + 3*N rgb + 1 checksum
        for n in [1, 5, 15, 20]:
            colors = [(0, 0, 0)] * n
            packet = build_razer_packet(colors)
            self.assertEqual(len(packet), 6 + 3 * n + 1)

    def test_variant_byte_configurable(self) -> None:
        packet = build_razer_packet([(0, 0, 0)], variant=0x0E)
        self.assertEqual(packet[2], 0x0E)

    def test_stretch_byte_configurable(self) -> None:
        packet = build_razer_packet([(0, 0, 0)], stretch=0x00)
        self.assertEqual(packet[4], 0x00)

    def test_rgb_values_clamped_to_byte(self) -> None:
        colors = [(300, -10, 256)]
        packet = build_razer_packet(colors)
        # 300 & 0xFF = 44, -10 & 0xFF = 246, 256 & 0xFF = 0
        self.assertEqual(packet[6], 300 & 0xFF)
        self.assertEqual(packet[7], (-10) & 0xFF)
        self.assertEqual(packet[8], 256 & 0xFF)


class BuildRazerJsonTests(unittest.TestCase):
    def test_json_structure(self) -> None:
        packet = build_razer_packet([(255, 0, 0)])
        payload = build_razer_json(packet)
        parsed = json.loads(payload)

        self.assertIn("msg", parsed)
        self.assertEqual(parsed["msg"]["cmd"], "razer")
        self.assertIn("pt", parsed["msg"]["data"])

    def test_base64_roundtrip(self) -> None:
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
        packet = build_razer_packet(colors)
        payload = build_razer_json(packet)
        parsed = json.loads(payload)

        decoded = base64.b64decode(parsed["msg"]["data"]["pt"])
        self.assertEqual(decoded, packet)


class BuildCommandJsonTests(unittest.TestCase):
    def test_turn_on(self) -> None:
        payload = build_command_json("turn", {"value": 1})
        parsed = json.loads(payload)
        self.assertEqual(parsed["msg"]["cmd"], "turn")
        self.assertEqual(parsed["msg"]["data"]["value"], 1)

    def test_brightness(self) -> None:
        payload = build_command_json("brightness", {"value": 75})
        parsed = json.loads(payload)
        self.assertEqual(parsed["msg"]["cmd"], "brightness")
        self.assertEqual(parsed["msg"]["data"]["value"], 75)


class ParseHexColorTests(unittest.TestCase):
    def test_with_hash(self) -> None:
        self.assertEqual(_parse_hex_color("#ff8800"), (255, 136, 0))

    def test_without_hash(self) -> None:
        self.assertEqual(_parse_hex_color("00ff80"), (0, 255, 128))

    def test_black(self) -> None:
        self.assertEqual(_parse_hex_color("#000000"), (0, 0, 0))

    def test_white(self) -> None:
        self.assertEqual(_parse_hex_color("#ffffff"), (255, 255, 255))


class GoveeLanAdapterFrameTests(unittest.TestCase):
    def _make_adapter(
        self, sent: list, clock: dict | None = None, **config_kwargs
    ) -> GoveeLanAdapter:
        if clock is None:
            clock = {"t": 0.0}

        def _transport(payload: bytes, ip: str, port: int) -> None:
            sent.append((payload, ip, port))

        def _monotonic() -> float:
            return clock["t"]

        config_kwargs.setdefault("device_ip", "192.168.1.100")
        config_kwargs.setdefault("segments", 5)
        return GoveeLanAdapter(
            GoveeLanConfig(**config_kwargs),
            transport=_transport,
            monotonic_fn=_monotonic,
        )

    def test_send_frame_sends_udp_packet(self) -> None:
        sent: list = []
        adapter = self._make_adapter(sent)
        colors = [(255, 0, 0)] * 5
        result = adapter.send_frame(colors)

        self.assertTrue(result)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][1], "192.168.1.100")
        self.assertEqual(sent[0][2], 4003)

    def test_send_frame_payload_is_valid_json(self) -> None:
        sent: list = []
        adapter = self._make_adapter(sent)
        adapter.send_frame([(255, 0, 0)] * 5)

        parsed = json.loads(sent[0][0])
        self.assertEqual(parsed["msg"]["cmd"], "razer")
        decoded = base64.b64decode(parsed["msg"]["data"]["pt"])
        self.assertEqual(decoded[0], 0xBB)
        self.assertEqual(decoded[5], 5)  # 5 segments

    def test_send_frame_rate_limited(self) -> None:
        sent: list = []
        clock = {"t": 0.0}
        adapter = self._make_adapter(sent, clock, fps=30)

        self.assertTrue(adapter.send_frame([(0, 0, 0)] * 5))
        clock["t"] = 0.01  # 10ms later — too soon
        self.assertFalse(adapter.send_frame([(0, 0, 0)] * 5))
        clock["t"] = 0.04  # 40ms later — past 33ms interval
        self.assertTrue(adapter.send_frame([(0, 0, 0)] * 5))
        self.assertEqual(len(sent), 2)

    def test_brightness_scales_colors(self) -> None:
        sent: list = []
        adapter = self._make_adapter(sent, brightness=0.5)
        adapter.send_frame([(200, 100, 50)] * 5)

        parsed = json.loads(sent[0][0])
        decoded = base64.b64decode(parsed["msg"]["data"]["pt"])
        # RGB starts at byte 6
        self.assertEqual(decoded[6], 100)   # 200 * 0.5
        self.assertEqual(decoded[7], 50)    # 100 * 0.5
        self.assertEqual(decoded[8], 25)    # 50 * 0.5

    def test_turn_on(self) -> None:
        sent: list = []
        adapter = self._make_adapter(sent)
        adapter.turn_on()

        parsed = json.loads(sent[0][0])
        self.assertEqual(parsed["msg"]["cmd"], "turn")
        self.assertEqual(parsed["msg"]["data"]["value"], 1)

    def test_turn_off(self) -> None:
        sent: list = []
        adapter = self._make_adapter(sent)
        adapter.turn_off()

        parsed = json.loads(sent[0][0])
        self.assertEqual(parsed["msg"]["cmd"], "turn")
        self.assertEqual(parsed["msg"]["data"]["value"], 0)

    def test_set_brightness(self) -> None:
        sent: list = []
        adapter = self._make_adapter(sent)
        adapter.set_brightness(75)

        parsed = json.loads(sent[0][0])
        self.assertEqual(parsed["msg"]["cmd"], "brightness")
        self.assertEqual(parsed["msg"]["data"]["value"], 75)

    def test_set_brightness_clamped(self) -> None:
        sent: list = []
        adapter = self._make_adapter(sent)
        adapter.set_brightness(150)

        parsed = json.loads(sent[0][0])
        self.assertEqual(parsed["msg"]["data"]["value"], 100)

    def test_set_solid_color(self) -> None:
        sent: list = []
        adapter = self._make_adapter(sent)
        adapter.set_solid_color(255, 128, 0)

        parsed = json.loads(sent[0][0])
        self.assertEqual(parsed["msg"]["cmd"], "colorwc")
        self.assertEqual(parsed["msg"]["data"]["color"], {"r": 255, "g": 128, "b": 0})
        self.assertEqual(parsed["msg"]["data"]["colorTemInKelvin"], 0)


class GoveeLanAdapterEmitTests(unittest.TestCase):
    def _make_adapter(self, sent: list, clock: dict | None = None) -> GoveeLanAdapter:
        if clock is None:
            clock = {"t": 0.0}

        def _transport(payload: bytes, ip: str, port: int) -> None:
            sent.append((payload, ip, port))

        def _monotonic() -> float:
            return clock["t"]

        return GoveeLanAdapter(
            GoveeLanConfig(device_ip="192.168.1.100", segments=3),
            transport=_transport,
            monotonic_fn=_monotonic,
        )

    def test_emit_with_color(self) -> None:
        sent: list = []
        adapter = self._make_adapter(sent)
        intent = LightingIntent(
            mode=EffectMode.PULSE, intensity=1.0, speed=0.5, bpm=120.0,
            color="#ff0000",
        )
        result = adapter.emit(0.0, intent)

        self.assertTrue(result)
        parsed = json.loads(sent[0][0])
        decoded = base64.b64decode(parsed["msg"]["data"]["pt"])
        # 3 segments, each (255, 0, 0) at full intensity
        self.assertEqual(decoded[5], 3)
        self.assertEqual(decoded[6], 255)
        self.assertEqual(decoded[7], 0)
        self.assertEqual(decoded[8], 0)

    def test_emit_without_color_uses_warm_white(self) -> None:
        sent: list = []
        adapter = self._make_adapter(sent)
        intent = LightingIntent(
            mode=EffectMode.AMBIENT, intensity=1.0, speed=0.2, bpm=90.0,
        )
        adapter.emit(0.0, intent)

        parsed = json.loads(sent[0][0])
        decoded = base64.b64decode(parsed["msg"]["data"]["pt"])
        self.assertEqual(decoded[6], 255)
        self.assertEqual(decoded[7], 180)
        self.assertEqual(decoded[8], 100)

    def test_emit_scales_by_intensity(self) -> None:
        sent: list = []
        adapter = self._make_adapter(sent)
        intent = LightingIntent(
            mode=EffectMode.PULSE, intensity=0.5, speed=0.5, bpm=120.0,
            color="#ff0000",
        )
        adapter.emit(0.0, intent)

        parsed = json.loads(sent[0][0])
        decoded = base64.b64decode(parsed["msg"]["data"]["pt"])
        # 255 * 0.5 = 127
        self.assertEqual(decoded[6], 127)
        self.assertEqual(decoded[7], 0)
        self.assertEqual(decoded[8], 0)

    def test_emit_fills_all_segments(self) -> None:
        sent: list = []
        adapter = self._make_adapter(sent)
        intent = LightingIntent(
            mode=EffectMode.PULSE, intensity=1.0, speed=0.5, bpm=120.0,
            color="#00ff00",
        )
        adapter.emit(0.0, intent)

        parsed = json.loads(sent[0][0])
        decoded = base64.b64decode(parsed["msg"]["data"]["pt"])
        # All 3 segments should be green
        for i in range(3):
            offset = 6 + i * 3
            self.assertEqual(decoded[offset], 0)
            self.assertEqual(decoded[offset + 1], 255)
            self.assertEqual(decoded[offset + 2], 0)

    def test_emit_rate_limited(self) -> None:
        sent: list = []
        clock = {"t": 0.0}
        adapter = self._make_adapter(sent, clock)
        intent = LightingIntent(
            mode=EffectMode.PULSE, intensity=1.0, speed=0.5, bpm=120.0,
            color="#ff0000",
        )

        self.assertTrue(adapter.emit(0.0, intent))
        clock["t"] = 0.01
        self.assertFalse(adapter.emit(0.01, intent))
        self.assertEqual(len(sent), 1)


class PtRealPacketTests(unittest.TestCase):
    def test_segment_bitmask_single(self) -> None:
        mask = _segment_bitmask([0])
        self.assertEqual(mask[0], 0x01)
        self.assertEqual(mask[1], 0x00)

    def test_segment_bitmask_multi(self) -> None:
        mask = _segment_bitmask([0, 1, 2, 8])
        self.assertEqual(mask[0], 0x07)  # bits 0,1,2
        self.assertEqual(mask[1], 0x01)  # bit 8

    def test_segment_bitmask_all_15(self) -> None:
        mask = _segment_bitmask(list(range(15)))
        self.assertEqual(mask[0], 0xFF)  # bits 0-7
        self.assertEqual(mask[1], 0x7F)  # bits 8-14

    def test_ptreal_checksum(self) -> None:
        packet = [0x33, 0x05, 0x15, 0x01] + [0x00] * 15
        chk = _ptreal_checksum(packet)
        expected = 0x33 ^ 0x05 ^ 0x15 ^ 0x01
        self.assertEqual(chk, expected)

    def test_segment_packet_length(self) -> None:
        packets = build_ptreal_segment_packets([(255, 0, 0)] * 3)
        self.assertEqual(len(packets), 1)  # all same color → 1 packet
        self.assertEqual(len(packets[0]), 20)

    def test_segment_packet_header(self) -> None:
        packets = build_ptreal_segment_packets([(255, 0, 0)])
        pkt = packets[0]
        self.assertEqual(pkt[0], 0x33)
        self.assertEqual(pkt[1], 0x05)
        self.assertEqual(pkt[2], 0x15)
        self.assertEqual(pkt[3], 0x01)
        self.assertEqual(pkt[4], 255)  # R
        self.assertEqual(pkt[5], 0)    # G
        self.assertEqual(pkt[6], 0)    # B

    def test_segment_packet_bitmask(self) -> None:
        # 3 segments all red → one packet with bitmask for 0,1,2
        packets = build_ptreal_segment_packets([(255, 0, 0)] * 3)
        pkt = packets[0]
        self.assertEqual(pkt[12], 0x07)  # bits 0,1,2 set

    def test_segment_packet_groups_by_color(self) -> None:
        # 5 segments: red, blue, red, blue, red
        colors = [(255, 0, 0), (0, 0, 255), (255, 0, 0), (0, 0, 255), (255, 0, 0)]
        packets = build_ptreal_segment_packets(colors)
        self.assertEqual(len(packets), 2)  # red + blue

    def test_segment_packet_checksum_valid(self) -> None:
        packets = build_ptreal_segment_packets([(128, 64, 32)] * 5)
        pkt = list(packets[0])
        expected_chk = 0
        for b in pkt[:19]:
            expected_chk ^= b
        self.assertEqual(pkt[19], expected_chk)

    def test_power_packet(self) -> None:
        pkt = build_ptreal_power_packet(True)
        self.assertEqual(len(pkt), 20)
        self.assertEqual(pkt[0], 0x33)
        self.assertEqual(pkt[1], 0x01)
        self.assertEqual(pkt[2], 0x01)

        pkt_off = build_ptreal_power_packet(False)
        self.assertEqual(pkt_off[2], 0x00)

    def test_brightness_packet(self) -> None:
        pkt = build_ptreal_brightness_packet(75)
        self.assertEqual(len(pkt), 20)
        self.assertEqual(pkt[0], 0x33)
        self.assertEqual(pkt[1], 0x04)
        # 75% of 255 = 191
        self.assertEqual(pkt[2], 191)

    def test_brightness_100_maps_to_255(self) -> None:
        pkt = build_ptreal_brightness_packet(100)
        self.assertEqual(pkt[2], 255)

    def test_brightness_0_maps_to_0(self) -> None:
        pkt = build_ptreal_brightness_packet(0)
        self.assertEqual(pkt[2], 0)

    def test_brightness_clamped(self) -> None:
        pkt = build_ptreal_brightness_packet(150)
        self.assertEqual(pkt[2], 255)

    def test_ptreal_json_structure(self) -> None:
        packets = build_ptreal_segment_packets([(255, 0, 0)] * 3)
        payload = build_ptreal_json(packets)
        parsed = json.loads(payload)
        self.assertEqual(parsed["msg"]["cmd"], "ptReal")
        self.assertIn("command", parsed["msg"]["data"])
        commands = parsed["msg"]["data"]["command"]
        self.assertEqual(len(commands), 1)
        # Verify base64 roundtrip
        decoded = base64.b64decode(commands[0])
        self.assertEqual(len(decoded), 20)

    def test_ptreal_json_multi_packet(self) -> None:
        colors = [(255, 0, 0), (0, 255, 0)]  # 2 colors → 2 packets
        packets = build_ptreal_segment_packets(colors)
        payload = build_ptreal_json(packets)
        parsed = json.loads(payload)
        self.assertEqual(len(parsed["msg"]["data"]["command"]), 2)


class AdapterPtRealTests(unittest.TestCase):
    def _make_adapter(self, sent: list, segments: int = 5) -> GoveeLanAdapter:
        clock = {"t": 0.0}

        def _transport(payload: bytes, ip: str, port: int) -> None:
            sent.append((payload, ip, port))

        def _monotonic() -> float:
            return clock["t"]

        return GoveeLanAdapter(
            GoveeLanConfig(
                device_ip="192.168.1.100", segments=segments,
                transport=TransportMode.PTREAL,
            ),
            transport=_transport,
            monotonic_fn=_monotonic,
        )

    def test_send_frame_uses_ptreal(self) -> None:
        sent: list = []
        adapter = self._make_adapter(sent)
        adapter.send_frame([(255, 0, 0)] * 5)

        parsed = json.loads(sent[0][0])
        self.assertEqual(parsed["msg"]["cmd"], "ptReal")
        commands = parsed["msg"]["data"]["command"]
        self.assertEqual(len(commands), 1)  # all same color

    def test_send_frame_multi_color(self) -> None:
        sent: list = []
        adapter = self._make_adapter(sent, segments=3)
        adapter.send_frame([(255, 0, 0), (0, 255, 0), (0, 0, 255)])

        parsed = json.loads(sent[0][0])
        commands = parsed["msg"]["data"]["command"]
        self.assertEqual(len(commands), 3)  # 3 distinct colors

    def test_turn_on_ptreal(self) -> None:
        sent: list = []
        adapter = self._make_adapter(sent)
        adapter.turn_on()

        parsed = json.loads(sent[0][0])
        self.assertEqual(parsed["msg"]["cmd"], "ptReal")
        pkt = base64.b64decode(parsed["msg"]["data"]["command"][0])
        self.assertEqual(pkt[0], 0x33)
        self.assertEqual(pkt[1], 0x01)
        self.assertEqual(pkt[2], 0x01)

    def test_turn_off_ptreal(self) -> None:
        sent: list = []
        adapter = self._make_adapter(sent)
        adapter.turn_off()

        parsed = json.loads(sent[0][0])
        pkt = base64.b64decode(parsed["msg"]["data"]["command"][0])
        self.assertEqual(pkt[2], 0x00)

    def test_set_brightness_ptreal(self) -> None:
        sent: list = []
        adapter = self._make_adapter(sent)
        adapter.set_brightness(80)

        parsed = json.loads(sent[0][0])
        pkt = base64.b64decode(parsed["msg"]["data"]["command"][0])
        self.assertEqual(pkt[0], 0x33)
        self.assertEqual(pkt[1], 0x04)
        # 80% of 255 = 204
        self.assertEqual(pkt[2], 204)


class ParseDeviceSpecTests(unittest.TestCase):
    def test_ip_and_segments(self) -> None:
        spec = parse_device_spec("192.168.1.23:15")
        self.assertEqual(spec.ip, "192.168.1.23")
        self.assertEqual(spec.segments, 15)
        self.assertEqual(spec.role, DeviceRole.PRIMARY)

    def test_ip_segments_and_role(self) -> None:
        spec = parse_device_spec("192.168.1.24:10:accent")
        self.assertEqual(spec.ip, "192.168.1.24")
        self.assertEqual(spec.segments, 10)
        self.assertEqual(spec.role, DeviceRole.ACCENT)

    def test_primary_role_explicit(self) -> None:
        spec = parse_device_spec("10.0.0.1:20:primary")
        self.assertEqual(spec.role, DeviceRole.PRIMARY)

    def test_missing_segments_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_device_spec("192.168.1.23")

    def test_invalid_role_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_device_spec("192.168.1.23:15:invalid")


class MultiGoveeLanAdapterTests(unittest.TestCase):
    def _make_adapter(
        self, sent: list, ip: str = "192.168.1.100", segments: int = 5
    ) -> GoveeLanAdapter:
        clock = {"t": 0.0}

        def _transport(payload: bytes, dest_ip: str, port: int) -> None:
            sent.append((payload, dest_ip, port))

        def _monotonic() -> float:
            return clock["t"]

        return GoveeLanAdapter(
            GoveeLanConfig(device_ip=ip, segments=segments),
            transport=_transport,
            monotonic_fn=_monotonic,
        )

    def test_send_frame_fans_out_to_all_devices(self) -> None:
        sent1: list = []
        sent2: list = []
        a1 = self._make_adapter(sent1, ip="192.168.1.10", segments=5)
        a2 = self._make_adapter(sent2, ip="192.168.1.11", segments=3)
        r1 = SegmentRenderer(segments=5, mode=RenderMode.SOLID)
        r2 = SegmentRenderer(segments=3, mode=RenderMode.SOLID)

        multi = MultiGoveeLanAdapter([
            (a1, r1, DeviceRole.PRIMARY),
            (a2, r2, DeviceRole.ACCENT),
        ])

        intent = LightingIntent(
            mode=EffectMode.PULSE, intensity=1.0, speed=0.5, bpm=120.0,
            color="#ff0000",
        )
        result = multi.send_frame(0.0, intent, beat=False)
        self.assertTrue(result)
        # Both devices should have received a frame
        self.assertEqual(len(sent1), 1)
        self.assertEqual(len(sent2), 1)

    def test_accent_device_gets_transformed_intent(self) -> None:
        sent1: list = []
        sent2: list = []
        a1 = self._make_adapter(sent1, ip="192.168.1.10", segments=3)
        a2 = self._make_adapter(sent2, ip="192.168.1.11", segments=3)
        r1 = SegmentRenderer(segments=3, mode=RenderMode.SOLID)
        r2 = SegmentRenderer(segments=3, mode=RenderMode.SOLID)

        multi = MultiGoveeLanAdapter([
            (a1, r1, DeviceRole.PRIMARY),
            (a2, r2, DeviceRole.ACCENT),
        ])

        intent = LightingIntent(
            mode=EffectMode.PULSE, intensity=1.0, speed=0.5, bpm=120.0,
            color="#ff0000",
        )
        multi.send_frame(0.0, intent, beat=False)

        # Primary: full intensity (255, 0, 0)
        parsed1 = json.loads(sent1[0][0])
        decoded1 = base64.b64decode(parsed1["msg"]["data"]["pt"])
        self.assertEqual(decoded1[6], 255)

        # Accent: 60% intensity -> 255 * 0.6 = 153
        parsed2 = json.loads(sent2[0][0])
        decoded2 = base64.b64decode(parsed2["msg"]["data"]["pt"])
        self.assertEqual(decoded2[6], 153)

    def test_activate_turns_on_all_devices(self) -> None:
        sent1: list = []
        sent2: list = []
        a1 = self._make_adapter(sent1, ip="192.168.1.10")
        a2 = self._make_adapter(sent2, ip="192.168.1.11")
        r1 = SegmentRenderer(segments=5, mode=RenderMode.SOLID)
        r2 = SegmentRenderer(segments=5, mode=RenderMode.SOLID)

        multi = MultiGoveeLanAdapter([
            (a1, r1, DeviceRole.PRIMARY),
            (a2, r2, DeviceRole.ACCENT),
        ])
        multi.activate(brightness=80)

        # Each device should have received turn_on + set_brightness
        self.assertEqual(len(sent1), 2)
        self.assertEqual(len(sent2), 2)
        # First message: turn on
        p1 = json.loads(sent1[0][0])
        self.assertEqual(p1["msg"]["cmd"], "turn")
        self.assertEqual(p1["msg"]["data"]["value"], 1)
        # Second message: brightness
        p2 = json.loads(sent1[1][0])
        self.assertEqual(p2["msg"]["cmd"], "brightness")
        self.assertEqual(p2["msg"]["data"]["value"], 80)

    def test_different_segment_counts_per_device(self) -> None:
        sent1: list = []
        sent2: list = []
        a1 = self._make_adapter(sent1, ip="192.168.1.10", segments=15)
        a2 = self._make_adapter(sent2, ip="192.168.1.11", segments=10)
        r1 = SegmentRenderer(segments=15, mode=RenderMode.SOLID)
        r2 = SegmentRenderer(segments=10, mode=RenderMode.SOLID)

        multi = MultiGoveeLanAdapter([
            (a1, r1, DeviceRole.PRIMARY),
            (a2, r2, DeviceRole.ACCENT),
        ])

        intent = LightingIntent(
            mode=EffectMode.PULSE, intensity=1.0, speed=0.5, bpm=120.0,
            color="#ff0000",
        )
        multi.send_frame(0.0, intent, beat=False)

        # Verify different segment counts in packets
        parsed1 = json.loads(sent1[0][0])
        decoded1 = base64.b64decode(parsed1["msg"]["data"]["pt"])
        self.assertEqual(decoded1[5], 15)  # count byte

        parsed2 = json.loads(sent2[0][0])
        decoded2 = base64.b64decode(parsed2["msg"]["data"]["pt"])
        self.assertEqual(decoded2[5], 10)  # count byte

    def test_beat_passed_to_all_renderers(self) -> None:
        sent1: list = []
        sent2: list = []
        a1 = self._make_adapter(sent1, ip="192.168.1.10", segments=5)
        a2 = self._make_adapter(sent2, ip="192.168.1.11", segments=5)
        r1 = SegmentRenderer(segments=5, mode=RenderMode.PULSE)
        r2 = SegmentRenderer(segments=5, mode=RenderMode.PULSE)

        multi = MultiGoveeLanAdapter([
            (a1, r1, DeviceRole.PRIMARY),
            (a2, r2, DeviceRole.PRIMARY),
        ])

        intent = LightingIntent(
            mode=EffectMode.PULSE, intensity=1.0, speed=0.5, bpm=120.0,
            color="#ff0000",
        )
        multi.send_frame(1.0, intent, beat=True)

        # Both should show pulse at full brightness on beat
        parsed1 = json.loads(sent1[0][0])
        decoded1 = base64.b64decode(parsed1["msg"]["data"]["pt"])
        self.assertEqual(decoded1[6], 255)

        parsed2 = json.loads(sent2[0][0])
        decoded2 = base64.b64decode(parsed2["msg"]["data"]["pt"])
        self.assertEqual(decoded2[6], 255)

    def test_single_device_multi_adapter(self) -> None:
        """MultiGoveeLanAdapter works fine with just one device."""
        sent: list = []
        adapter = self._make_adapter(sent, ip="192.168.1.10", segments=3)
        renderer = SegmentRenderer(segments=3, mode=RenderMode.SOLID)

        multi = MultiGoveeLanAdapter([
            (adapter, renderer, DeviceRole.PRIMARY),
        ])

        intent = LightingIntent(
            mode=EffectMode.PULSE, intensity=1.0, speed=0.5, bpm=120.0,
            color="#00ff00",
        )
        result = multi.send_frame(0.0, intent)
        self.assertTrue(result)
        self.assertEqual(len(sent), 1)


if __name__ == "__main__":
    unittest.main()
