"""Test the last 3 command variants with distinct colors to identify which works.

Variant 7: 33 05 01 RR GG BB  (mode+color combined)  — RED
Variant 8: 33 05 0D 01 RR GG BB (scene-based)         — GREEN
Variant 9: 33 05 15 01 + segment bitmask               — BLUE

Usage:
    python scripts/ble_color_variants_rgb.py C7:90:80:C6:44:74
"""
import asyncio
import sys

from bleak import BleakClient
from dreamsync.output.govee_ble import GOVEE_BLE_CHAR_UUID
from dreamsync.output.govee_lan import (
    build_ptreal_power_packet,
    build_ptreal_segment_packets,
    _ptreal_checksum,
)


def _build_packet(cmd_bytes: list[int]) -> bytes:
    packet = list(cmd_bytes)
    while len(packet) < 19:
        packet.append(0x00)
    packet = packet[:19]
    packet.append(_ptreal_checksum(packet))
    return bytes(packet)


async def main(address: str) -> None:
    async with BleakClient(address, timeout=15.0) as client:
        print(f"Connected to {address}\n")

        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ptreal_power_packet(True), response=False)
        await asyncio.sleep(1.0)
        print("Power ON sent\n")

        # Variant 7: 33 05 01 RR GG BB — RED
        pkt = _build_packet([0x33, 0x05, 0x01, 0xFF, 0x00, 0x00])
        print(f">>> Variant 7: 33 05 01 RR GG BB — should be RED")
        print(f"    Packet: {pkt.hex()}")
        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, pkt, response=False)
        print(f"    Waiting 6 seconds — observe...")
        await asyncio.sleep(6.0)
        print()

        # Variant 8: 33 05 0D 01 RR GG BB — GREEN
        pkt = _build_packet([0x33, 0x05, 0x0D, 0x01, 0x00, 0xFF, 0x00])
        print(f">>> Variant 8: 33 05 0D 01 RR GG BB — should be GREEN")
        print(f"    Packet: {pkt.hex()}")
        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, pkt, response=False)
        print(f"    Waiting 6 seconds — observe...")
        await asyncio.sleep(6.0)
        print()

        # Variant 9: segment packets — BLUE
        print(f">>> Variant 9: 33 05 15 01 + segment bitmask — should be BLUE")
        packets = build_ptreal_segment_packets([(0, 0, 255)] * 15)
        for i, p in enumerate(packets):
            print(f"    Packet {i+1}: {p.hex()}")
            await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, p, response=False)
            await asyncio.sleep(0.05)
        print(f"    Waiting 6 seconds — observe...")
        await asyncio.sleep(6.0)
        print()

        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ptreal_power_packet(False), response=False)
        print("Power OFF sent. Done.")
        print()
        print("Which color did you see?")
        print("  RED   = Variant 7 (33 05 01 RR GG BB)")
        print("  GREEN = Variant 8 (33 05 0D 01 RR GG BB)")
        print("  BLUE  = Variant 9 (33 05 15 01 + segment bitmask)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/ble_color_variants_rgb.py <BLE_ADDRESS>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
