"""Confirm segment packets work on H617A with clear R, G, B sequence.

Usage:
    python scripts/ble_confirm_segment_rgb.py C7:90:80:C6:44:74
"""
import asyncio
import sys

from bleak import BleakClient
from dreamsync.output.govee_ble import GOVEE_BLE_CHAR_UUID
from dreamsync.output.govee_lan import (
    build_ptreal_power_packet,
    build_ptreal_brightness_packet,
    build_ptreal_segment_packets,
)


async def main(address: str) -> None:
    async with BleakClient(address, timeout=15.0) as client:
        print(f"Connected to {address}\n")

        # Power on + brightness
        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ptreal_power_packet(True), response=False)
        await asyncio.sleep(0.8)
        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ptreal_brightness_packet(100), response=False)
        await asyncio.sleep(0.3)
        print("Power ON + brightness 100\n")

        colors_to_test = [
            ("RED",    (255, 0, 0)),
            ("GREEN",  (0, 255, 0)),
            ("BLUE",   (0, 0, 255)),
            ("YELLOW", (255, 255, 0)),
            ("WHITE",  (255, 255, 255)),
        ]

        for name, (r, g, b) in colors_to_test:
            print(f">>> {name} ({r}, {g}, {b})")
            packets = build_ptreal_segment_packets([(r, g, b)] * 15)
            for pkt in packets:
                await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, pkt, response=False)
                await asyncio.sleep(0.05)
            print(f"    Observe for 5 seconds...")
            await asyncio.sleep(5.0)

        # Power off
        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ptreal_power_packet(False), response=False)
        print("\nPower OFF. Done.")
        print("\nDid you see: RED → GREEN → BLUE → YELLOW → WHITE?")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/ble_confirm_segment_rgb.py <BLE_ADDRESS>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
