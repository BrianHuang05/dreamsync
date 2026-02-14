"""Diagnostic: test ptReal (BLE-over-LAN) per-segment control on Govee devices."""

import base64
import json
import socket
import time
import sys

PORT = 4003


def xor_checksum(packet: list[int]) -> int:
    result = 0
    for b in packet[:19]:
        result ^= b
    return result


def build_segment_color_packet(r: int, g: int, b: int, bitmask: list[int]) -> bytes:
    """Build a 33 05 15 01 per-segment color packet (20 bytes).

    bitmask: 7 bytes, each bit = one segment (little-endian).
    """
    packet = [
        0x33, 0x05, 0x15, 0x01,        # header: segment color command
        r & 0xFF, g & 0xFF, b & 0xFF,   # RGB
        0x00, 0x00, 0x00, 0x00, 0x00,   # padding
    ] + bitmask[:7]                      # 7-byte segment bitmask
    # Pad to exactly 19 bytes
    while len(packet) < 19:
        packet.append(0x00)
    packet = packet[:19]
    packet.append(xor_checksum(packet))
    return bytes(packet)


def build_power_packet(on: bool) -> bytes:
    """Build power on/off packet: 33 01 01/00."""
    packet = [0x33, 0x01, 0x01 if on else 0x00] + [0x00] * 16
    packet.append(xor_checksum(packet))
    return bytes(packet)


def build_brightness_packet(value: int) -> bytes:
    """Build global brightness packet: 33 04 XX."""
    packet = [0x33, 0x04, max(0, min(100, value))] + [0x00] * 16
    packet.append(xor_checksum(packet))
    return bytes(packet)


def send_ptreal(ip: str, packets: list[bytes], label: str) -> None:
    commands = [base64.b64encode(p).decode("ascii") for p in packets]
    msg = {"msg": {"cmd": "ptReal", "data": {"command": commands}}}
    payload = json.dumps(msg, separators=(",", ":")).encode("utf-8")
    print(f"  [{label}] sending {len(packets)} packet(s), {len(payload)} bytes to {ip}:{PORT}")
    for i, cmd in enumerate(commands):
        raw = base64.b64decode(cmd)
        hex_str = " ".join(f"{b:02x}" for b in raw)
        print(f"    pkt[{i}]: {hex_str}")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(payload, (ip, PORT))
    sock.close()


def segment_bitmask(segments: list[int]) -> list[int]:
    """Convert list of 1-based segment indices to 7-byte bitmask."""
    mask = [0] * 7
    for seg in segments:
        idx = seg - 1  # 0-based
        byte_idx = idx // 8
        bit_idx = idx % 8
        if 0 <= byte_idx < 7:
            mask[byte_idx] |= (1 << bit_idx)
    return mask


def all_segments_mask(count: int) -> list[int]:
    """Bitmask with segments 1..count set."""
    return segment_bitmask(list(range(1, count + 1)))


def run_diag(ip: str, name: str, seg_count: int = 15) -> None:
    print(f"\n{'='*60}")
    print(f"DEVICE: {name} @ {ip} ({seg_count} segments)")
    print(f"{'='*60}")

    print("\n--- Step 1: ptReal power ON ---")
    send_ptreal(ip, [build_power_packet(True)], "power ON")
    time.sleep(1.0)

    print("\n--- Step 2: ptReal brightness 100 ---")
    send_ptreal(ip, [build_brightness_packet(100)], "brightness 100")
    time.sleep(0.5)

    print("\n--- Step 3: All segments RED ---")
    mask_all = all_segments_mask(seg_count)
    pkt = build_segment_color_packet(255, 0, 0, mask_all)
    send_ptreal(ip, [pkt], f"all {seg_count} segs = RED")
    time.sleep(3.0)

    print("\n--- Step 4: All segments GREEN ---")
    pkt = build_segment_color_packet(0, 255, 0, mask_all)
    send_ptreal(ip, [pkt], f"all {seg_count} segs = GREEN")
    time.sleep(3.0)

    print("\n--- Step 5: Segment 1 = RED, rest = BLACK ---")
    pkts = [
        build_segment_color_packet(255, 0, 0, segment_bitmask([1])),
        build_segment_color_packet(0, 0, 0, segment_bitmask(list(range(2, seg_count + 1)))),
    ]
    send_ptreal(ip, pkts, "seg1=RED, rest=BLACK")
    time.sleep(3.0)

    print("\n--- Step 6: Odd segs RED, even segs BLUE ---")
    odds = list(range(1, seg_count + 1, 2))
    evens = list(range(2, seg_count + 1, 2))
    pkts = [
        build_segment_color_packet(255, 0, 0, segment_bitmask(odds)),
        build_segment_color_packet(0, 0, 255, segment_bitmask(evens)),
    ]
    send_ptreal(ip, pkts, "odd=RED, even=BLUE")
    time.sleep(3.0)

    print("\n--- Step 7: Rainbow walk (1 seg at a time) ---")
    colors = [
        (255, 0, 0), (255, 127, 0), (255, 255, 0), (0, 255, 0),
        (0, 255, 255), (0, 0, 255), (127, 0, 255),
    ]
    for seg in range(1, seg_count + 1):
        r, g, b = colors[(seg - 1) % len(colors)]
        pkt = build_segment_color_packet(r, g, b, segment_bitmask([seg]))
        send_ptreal(ip, [pkt], f"seg{seg}=({r},{g},{b})")
        time.sleep(0.15)
    print("  (Rainbow should be visible)")
    time.sleep(3.0)

    print("\n--- Step 8: Rapid frame test (all segs, 10 color changes) ---")
    test_colors = [
        (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0),
        (255, 0, 255), (0, 255, 255), (255, 128, 0), (128, 0, 255),
        (0, 255, 128), (255, 255, 255),
    ]
    for i, (r, g, b) in enumerate(test_colors):
        pkt = build_segment_color_packet(r, g, b, mask_all)
        send_ptreal(ip, [pkt], f"flash {i+1}/10")
        time.sleep(0.3)

    print("\n--- Step 9: ptReal power OFF ---")
    send_ptreal(ip, [build_power_packet(False)], "power OFF")

    print(f"\nDone with {name}.\n")


if __name__ == "__main__":
    devices = [
        ("10.126.166.180", "H612F", 15),
        ("10.126.166.156", "H808A", 15),
    ]

    print("Govee ptReal (BLE-over-LAN) Per-Segment Diagnostic")
    print("Watch each device and note which steps produce visible changes.")
    print("Press Ctrl+C to abort.\n")

    for ip, name, segs in devices:
        run_diag(ip, name, segs)
        print("--- Pausing 2s before next device ---")
        time.sleep(2.0)

    print("\nAll done. Report which steps worked for each device.")
