"""Try the segment-addressed color command (33 05 15 01) over BLE.

Fallback test if the whole-device color command (33 05 02) doesn't work.

Usage:
    python scripts/ble_try_segment_color.py AA:BB:CC:DD:EE:FF
"""
import asyncio
import sys

from bleak import BleakClient
from dreamsync.output.govee_ble import GOVEE_BLE_CHAR_UUID, build_ble_manual_mode_packet
from dreamsync.output.govee_lan import build_ptreal_power_packet, build_ptreal_segment_packets


async def main(address: str) -> None:
    async with BleakClient(address, timeout=15.0) as client:
        print(f"Connected to {address}")

        # Power on
        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ptreal_power_packet(True), response=False)
        await asyncio.sleep(0.5)
        print("Power on sent")

        # Manual mode
        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ble_manual_mode_packet(), response=False)
        await asyncio.sleep(0.3)
        print("Manual mode sent")

        # Try segment color: all 15 segments red
        colors = [(255, 0, 0)] * 15
        packets = build_ptreal_segment_packets(colors)
        for i, pkt in enumerate(packets):
            await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, pkt, response=False)
            await asyncio.sleep(0.05)
            print(f"Segment packet {i+1}/{len(packets)} sent: {pkt.hex()}")

        print("\nDevice should be red. Waiting 10 seconds...")
        await asyncio.sleep(10)

        # Power off
        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ptreal_power_packet(False), response=False)
        print("Power off sent")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/ble_try_segment_color.py <BLE_ADDRESS>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
