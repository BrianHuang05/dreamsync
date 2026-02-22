"""Measure per-write BLE latency to a Govee device.

Supports both protocol variants:
  - segment: uses segment packets (33 05 15 01) — strips (H617A, H612F)
  - bulb:    uses bulb color command (33 05 0D) — bulbs (H6006)

Usage:
    python scripts/ble_latency_bench.py AA:BB:CC:DD:EE:FF [segment|bulb]
"""
import asyncio
import json
import sys
import time

from bleak import BleakClient
from dreamsync.output.govee_ble import (
    GOVEE_BLE_CHAR_UUID,
    build_ble_bulb_color_packet,
)
from dreamsync.output.govee_lan import (
    build_ptreal_brightness_packet,
    build_ptreal_power_packet,
    build_ptreal_segment_packets,
)

SEGMENTS = 15


async def main(address: str, protocol: str) -> None:
    async with BleakClient(address, timeout=15.0) as client:
        print(f"Connected to {address} (protocol={protocol})", file=sys.stderr)

        # Init
        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ptreal_power_packet(True), response=False)
        await asyncio.sleep(0.5)
        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ptreal_brightness_packet(100), response=False)
        await asyncio.sleep(0.3)

        # Benchmark: 100 color writes, alternating red/blue
        latencies: list[float] = []
        for i in range(100):
            color = (255, 0, 0) if i % 2 == 0 else (0, 0, 255)
            t0 = time.perf_counter()
            if protocol == "bulb":
                pkt = build_ble_bulb_color_packet(*color)
                await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, pkt, response=False)
            else:
                packets = build_ptreal_segment_packets([color] * SEGMENTS)
                for pkt in packets:
                    await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, pkt, response=False)
            t1 = time.perf_counter()
            latencies.append((t1 - t0) * 1000)  # ms
            await asyncio.sleep(0.05)  # ~20 Hz

        # Power off
        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ptreal_power_packet(False), response=False)

        # Report
        results = {
            "address": address,
            "protocol": protocol,
            "segments": SEGMENTS if protocol == "segment" else 1,
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
        suffix = f"_{protocol}" if protocol != "segment" else ""
        raw_path = f"data/ble_latencies_raw{suffix}.json"
        with open(raw_path, "w") as f:
            json.dump(latencies, f)
        print(f"Raw latencies saved to {raw_path}", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/ble_latency_bench.py <BLE_ADDRESS> [segment|bulb]")
        sys.exit(1)
    addr = sys.argv[1]
    proto = sys.argv[2] if len(sys.argv) > 2 else "segment"
    if proto not in ("segment", "bulb"):
        print(f"Unknown protocol: {proto}. Use 'segment' or 'bulb'.")
        sys.exit(1)
    asyncio.run(main(addr, proto))
