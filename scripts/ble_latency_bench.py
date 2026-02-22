"""Measure per-write BLE latency to a Govee device.

Writes 100 alternating red/blue color commands at ~20 Hz and records
the time for each write_gatt_char call.

Usage:
    python scripts/ble_latency_bench.py AA:BB:CC:DD:EE:FF > data/ble_latency_results.json
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
from dreamsync.output.govee_lan import build_ptreal_brightness_packet, build_ptreal_power_packet


async def main(address: str) -> None:
    async with BleakClient(address, timeout=15.0) as client:
        print(f"Connected to {address}", file=sys.stderr)

        # Init
        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ptreal_power_packet(True), response=False)
        await asyncio.sleep(0.5)
        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ble_manual_mode_packet(), response=False)
        await asyncio.sleep(0.3)
        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ptreal_brightness_packet(100), response=False)
        await asyncio.sleep(0.1)

        # Benchmark: 100 color writes, alternating red/blue
        latencies: list[float] = []
        for i in range(100):
            color = (255, 0, 0) if i % 2 == 0 else (0, 0, 255)
            t0 = time.perf_counter()
            await client.write_gatt_char(
                GOVEE_BLE_CHAR_UUID,
                build_ble_color_packet(*color),
                response=False,
            )
            t1 = time.perf_counter()
            latencies.append((t1 - t0) * 1000)  # ms
            await asyncio.sleep(0.05)  # ~20 Hz

        # Power off
        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ptreal_power_packet(False), response=False)

        # Report
        results = {
            "address": address,
            "writes": len(latencies),
            "min_ms": round(min(latencies), 2),
            "max_ms": round(max(latencies), 2),
            "avg_ms": round(sum(latencies) / len(latencies), 2),
            "median_ms": round(sorted(latencies)[len(latencies) // 2], 2),
            "p95_ms": round(sorted(latencies)[int(len(latencies) * 0.95)], 2),
            "total_sec": round(sum(latencies) / 1000, 2),
        }
        print(json.dumps(results, indent=2))

        # Also dump raw latencies
        with open("data/ble_latencies_raw.json", "w") as f:
            json.dump(latencies, f)
        print(f"Raw latencies saved to data/ble_latencies_raw.json", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/ble_latency_bench.py <BLE_ADDRESS>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
