"""Discover how many addressable segments a Govee BLE strip has.

Lights up segments one at a time with a walk pattern, so you can count
the individually addressable zones.

Usage:
    python scripts/ble_segment_discovery.py C7:90:80:C6:44:74
"""
import asyncio
import sys

from bleak import BleakClient
from dreamsync.output.govee_ble import GOVEE_BLE_CHAR_UUID
from dreamsync.output.govee_lan import (
    build_ptreal_brightness_packet,
    build_ptreal_power_packet,
    build_ptreal_segment_packets,
)

# Test up to 20 segments — most Govee strips have 7-25
MAX_SEGMENTS = 20

RAINBOW = [
    (255, 0, 0), (255, 127, 0), (255, 255, 0), (0, 255, 0),
    (0, 255, 255), (0, 0, 255), (127, 0, 255),
]


async def main(address: str) -> None:
    async with BleakClient(address, timeout=15.0) as client:
        print(f"Connected to {address}")

        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ptreal_power_packet(True), response=False)
        await asyncio.sleep(0.8)
        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ptreal_brightness_packet(100), response=False)
        await asyncio.sleep(0.3)

        # First: light all segments white so we see the full strip
        print(f"\n--- ALL WHITE (2 sec) ---")
        packets = build_ptreal_segment_packets([(255, 255, 255)] * MAX_SEGMENTS)
        for pkt in packets:
            await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, pkt, response=False)
            await asyncio.sleep(0.05)
        await asyncio.sleep(2.0)

        # Walk: light one segment at a time
        print(f"\n--- WALKING segments 0-{MAX_SEGMENTS - 1} (1.5 sec each) ---")
        print(f"Count how many distinct zones light up.\n")
        for seg in range(MAX_SEGMENTS):
            colors = [(0, 0, 0)] * MAX_SEGMENTS
            colors[seg] = RAINBOW[seg % len(RAINBOW)]
            packets = build_ptreal_segment_packets(colors)
            for pkt in packets:
                await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, pkt, response=False)
                await asyncio.sleep(0.05)
            print(f"  Segment {seg:2d}: {RAINBOW[seg % len(RAINBOW)]} — can you see it?")
            await asyncio.sleep(1.5)

        # Finish: rainbow to show all segments at once
        print(f"\n--- RAINBOW (all segments, 3 sec) ---")
        colors = [RAINBOW[i % len(RAINBOW)] for i in range(MAX_SEGMENTS)]
        packets = build_ptreal_segment_packets(colors)
        for pkt in packets:
            await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, pkt, response=False)
            await asyncio.sleep(0.05)
        await asyncio.sleep(3.0)

        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ptreal_power_packet(False), response=False)
        print(f"\nPower OFF. Done.")
        print(f"\nHow many distinct zones lit up during the walk?")
        print(f"That's your segment count for --ble-device config.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/ble_segment_discovery.py <BLE_ADDRESS>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
