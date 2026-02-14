"""Quick diagnostic: test each Govee LAN command type independently."""

import json
import socket
import time
import sys
import base64

PORT = 4003

def send_udp(ip: str, payload: bytes, label: str) -> None:
    print(f"  [{label}] sending {len(payload)} bytes to {ip}:{PORT}")
    print(f"    payload: {payload.decode('utf-8', errors='replace')}")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(payload, (ip, PORT))
    sock.close()


def test_turn_on(ip: str) -> None:
    msg = {"msg": {"cmd": "turn", "data": {"value": 1}}}
    send_udp(ip, json.dumps(msg).encode(), "turn ON")


def test_turn_off(ip: str) -> None:
    msg = {"msg": {"cmd": "turn", "data": {"value": 0}}}
    send_udp(ip, json.dumps(msg).encode(), "turn OFF")


def test_brightness(ip: str, value: int = 100) -> None:
    msg = {"msg": {"cmd": "brightness", "data": {"value": value}}}
    send_udp(ip, json.dumps(msg).encode(), f"brightness={value}")


def test_colorwc(ip: str, r: int, g: int, b: int) -> None:
    msg = {"msg": {"cmd": "colorwc", "data": {"color": {"r": r, "g": g, "b": b}, "colorTemInKelvin": 0}}}
    send_udp(ip, json.dumps(msg).encode(), f"colorwc rgb=({r},{g},{b})")


def test_razer(ip: str, segments: int, r: int, g: int, b: int, variant: int) -> None:
    header = bytes([0xBB, 0x00, variant, 0xB0, 0x01, segments])
    rgb_data = bytes([r, g, b]) * segments
    body = header + rgb_data
    checksum = 0
    for byte in body:
        checksum ^= byte
    packet = body + bytes([checksum])
    encoded = base64.b64encode(packet).decode("ascii")
    msg = {"msg": {"cmd": "razer", "data": {"pt": encoded}}}
    payload = json.dumps(msg).encode()
    variant_name = {0xFA: "dreamview", 0x0E: "chroma", 0x20: "govee"}.get(variant, f"0x{variant:02x}")
    send_udp(ip, payload, f"razer variant={variant_name} segs={segments} rgb=({r},{g},{b})")


def run_diag(ip: str, name: str) -> None:
    print(f"\n{'='*60}")
    print(f"DEVICE: {name} @ {ip}")
    print(f"{'='*60}")

    print("\n--- Step 1: Turn ON ---")
    test_turn_on(ip)
    time.sleep(1.0)

    print("\n--- Step 2: Set brightness 100 ---")
    test_brightness(ip, 100)
    time.sleep(0.5)

    print("\n--- Step 3: colorwc solid RED ---")
    print("  (Does the device turn red?)")
    test_colorwc(ip, 255, 0, 0)
    time.sleep(3.0)

    print("\n--- Step 4: colorwc solid GREEN ---")
    print("  (Does it change to green?)")
    test_colorwc(ip, 0, 255, 0)
    time.sleep(3.0)

    print("\n--- Step 5: razer variant=dreamview (0xFA), 15 segments RED ---")
    test_razer(ip, 15, 255, 0, 0, 0xFA)
    time.sleep(3.0)

    print("\n--- Step 6: razer variant=chroma (0x0E), 15 segments BLUE ---")
    test_razer(ip, 15, 0, 0, 255, 0x0E)
    time.sleep(3.0)

    print("\n--- Step 7: razer variant=govee (0x20), 15 segments GREEN ---")
    test_razer(ip, 15, 0, 255, 0, 0x20)
    time.sleep(3.0)

    # Try fewer segments
    print("\n--- Step 8: razer variant=dreamview, 1 segment RED ---")
    test_razer(ip, 1, 255, 0, 0, 0xFA)
    time.sleep(2.0)

    print("\n--- Step 9: razer variant=dreamview, 10 segments RED ---")
    test_razer(ip, 10, 255, 0, 0, 0xFA)
    time.sleep(2.0)

    print("\n--- Step 10: Turn OFF ---")
    test_turn_off(ip)

    print(f"\nDone with {name}.\n")


if __name__ == "__main__":
    devices = [
        ("10.126.166.180", "H612F"),
        ("10.126.166.156", "H808A"),
    ]

    print("Govee LAN Diagnostic")
    print("Watch each device and note which steps produce a visible change.")
    print("Press Ctrl+C to abort at any time.\n")

    for ip, name in devices:
        run_diag(ip, name)
        print("--- Pausing 2s before next device ---")
        time.sleep(2.0)

    print("\nAll done. Report which step numbers worked for each device.")
