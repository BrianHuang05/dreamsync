"""Measure maximum sustained BLE write throughput to a Govee device.

Writes as fast as possible for 10 seconds (no sleep between writes)
to find the ceiling write rate.

Usage:
    python scripts/ble_throughput_bench.py AA:BB:CC:DD:EE:FF > data/ble_throughput_results.json
"""
import asyncio
import json
import sys
import time

from bleak import BleakClient
from dreamsync.output.govee_ble import (
    GOVEE_BLE_CHAR_UUID,
    build_ble_color_packet,
    build_ble_manual_mode_packet,
)
from dreamsync.output.govee_lan import build_ptreal_power_packet


async def main(address: str) -> None:
    async with BleakClient(address, timeout=15.0) as client:
        print(f"Connected to {address}", file=sys.stderr)

        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ptreal_power_packet(True), response=False)
        await asyncio.sleep(0.5)
        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ble_manual_mode_packet(), response=False)
        await asyncio.sleep(0.3)

        # Sustained writes for 10 seconds — no sleep between writes
        count = 0
        errors = 0
        t_start = time.perf_counter()
        while time.perf_counter() - t_start < 10.0:
            color = (count % 256, (count * 3) % 256, (count * 7) % 256)
            try:
                await client.write_gatt_char(
                    GOVEE_BLE_CHAR_UUID,
                    build_ble_color_packet(*color),
                    response=False,
                )
                count += 1
            except Exception as e:
                errors += 1
                print(f"Write error #{errors}: {e}", file=sys.stderr)
                await asyncio.sleep(0.1)
        elapsed = time.perf_counter() - t_start

        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ptreal_power_packet(False), response=False)

        print(json.dumps({
            "address": address,
            "writes": count,
            "errors": errors,
            "elapsed_sec": round(elapsed, 2),
            "writes_per_sec": round(count / elapsed, 1),
        }, indent=2))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/ble_throughput_bench.py <BLE_ADDRESS>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
