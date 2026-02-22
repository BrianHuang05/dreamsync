"""Orchestrated multi-device BLE + LAN latency benchmark.

Connects to all specified devices simultaneously, then sends coordinated
color updates and measures per-device latency at scale.

Usage:
    python scripts/ble_orchestrated_bench.py

Edit the DEVICES list below to match your setup.
"""
import asyncio
import json
import socket
import sys
import time
from dataclasses import dataclass

from bleak import BleakClient
from dreamsync.output.govee_ble import (
    GOVEE_BLE_CHAR_UUID,
    build_ble_bulb_color_packet,
)
from dreamsync.output.govee_lan import (
    build_ptreal_brightness_packet,
    build_ptreal_json,
    build_ptreal_power_packet,
    build_ptreal_segment_packets,
    build_razer_json,
    build_razer_packet,
)

# ---------------------------------------------------------------------------
# Device inventory — edit to match your setup
# ---------------------------------------------------------------------------

@dataclass
class BleDevice:
    name: str
    address: str
    protocol: str  # "segment" or "bulb"
    segments: int = 15

@dataclass
class LanDevice:
    name: str
    ip: str
    port: int
    transport: str  # "ptreal" or "razer"
    segments: int = 7

BLE_DEVICES = [
    BleDevice("H617A strip",   "C7:90:80:C6:44:74", "segment", segments=15),
    BleDevice("H6006 bulb #1", "D0:C9:07:C5:14:45", "bulb"),
    BleDevice("H6006 bulb #2", "98:17:3C:09:7E:ED", "bulb"),
    BleDevice("H6006 bulb #3", "D0:C9:07:95:15:DB", "bulb"),
    BleDevice("H6006 bulb #4", "D0:C9:07:70:AD:A3", "bulb"),
    BleDevice("H6006 bulb #5", "D0:C9:07:95:14:8B", "bulb"),
    BleDevice("H6006 bulb #6", "98:17:3C:07:BA:47", "bulb"),
]

LAN_DEVICES = [
    LanDevice("H612F overhead", "10.126.166.180", 4003, "ptreal", segments=7),
    LanDevice("H808A couch",    "10.126.166.156", 4003, "razer",  segments=25),
]

# Test parameters
ROUNDS = 30          # color updates to send
INTERVAL_SEC = 0.2   # 5 Hz target (matches mood-follower rate)

# Color sequence: cycle through distinct colors
COLORS = [
    (255, 0, 0),    # red
    (0, 255, 0),    # green
    (0, 0, 255),    # blue
    (255, 255, 0),  # yellow
    (0, 255, 255),  # cyan
    (255, 0, 255),  # magenta
]


# ---------------------------------------------------------------------------
# LAN send helpers
# ---------------------------------------------------------------------------

def _make_udp_socket() -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setblocking(False)
    return s


def _send_lan_frame(sock: socket.socket, dev: LanDevice, color: tuple[int, int, int]) -> None:
    """Send a single color frame to a LAN device."""
    if dev.transport == "ptreal":
        packets = build_ptreal_segment_packets([color] * dev.segments)
        payload = build_ptreal_json(packets)
        sock.sendto(payload, (dev.ip, dev.port))
    elif dev.transport == "razer":
        pkt = build_razer_packet([color] * dev.segments)
        payload = build_razer_json(pkt)
        sock.sendto(payload, (dev.ip, dev.port))


# ---------------------------------------------------------------------------
# BLE connection + benchmark
# ---------------------------------------------------------------------------

async def connect_ble_device(dev: BleDevice) -> BleakClient | None:
    """Connect to a single BLE device, return client or None on failure."""
    try:
        client = BleakClient(dev.address, timeout=15.0)
        await client.connect()
        # Init: power on + brightness
        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ptreal_power_packet(True), response=False)
        await asyncio.sleep(0.3)
        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ptreal_brightness_packet(100), response=False)
        await asyncio.sleep(0.1)
        print(f"  [OK] {dev.name} ({dev.address})")
        return client
    except Exception as e:
        print(f"  [FAIL] {dev.name} ({dev.address}): {e}")
        return None


async def send_ble_color(client: BleakClient, dev: BleDevice, color: tuple[int, int, int]) -> float:
    """Send a color to a BLE device, return latency in ms."""
    r, g, b = color
    t0 = time.perf_counter()
    if dev.protocol == "bulb":
        pkt = build_ble_bulb_color_packet(r, g, b)
        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, pkt, response=False)
    else:
        packets = build_ptreal_segment_packets([(r, g, b)] * dev.segments)
        for pkt in packets:
            await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, pkt, response=False)
    t1 = time.perf_counter()
    return (t1 - t0) * 1000


async def main() -> None:
    print(f"=" * 60)
    print(f"ORCHESTRATED MULTI-DEVICE LATENCY BENCHMARK")
    print(f"  BLE devices: {len(BLE_DEVICES)}")
    print(f"  LAN devices: {len(LAN_DEVICES)}")
    print(f"  Rounds: {ROUNDS} @ {1/INTERVAL_SEC:.0f} Hz")
    print(f"=" * 60)

    # Phase 1: Connect all BLE devices in parallel
    print(f"\nPhase 1: Connecting BLE devices...")
    connect_tasks = [connect_ble_device(dev) for dev in BLE_DEVICES]
    clients = await asyncio.gather(*connect_tasks)

    # Filter to successfully connected
    active: list[tuple[BleDevice, BleakClient]] = []
    for dev, client in zip(BLE_DEVICES, clients):
        if client is not None and client.is_connected:
            active.append((dev, client))

    if not active:
        print("\nNo BLE devices connected. Exiting.")
        return

    print(f"\n  Connected: {len(active)}/{len(BLE_DEVICES)} BLE devices")

    # Phase 2: Set up LAN
    lan_sock = _make_udp_socket()
    print(f"  LAN devices: {len(LAN_DEVICES)} (UDP, no connection needed)")

    # Phase 3: Coordinated color updates
    print(f"\nPhase 2: Sending {ROUNDS} coordinated color updates...")
    print(f"  {'Round':>5}  {'Color':>18}  {'BLE total ms':>12}  {'BLE max ms':>10}  {'LAN ms':>8}")
    print(f"  {'-'*5}  {'-'*18}  {'-'*12}  {'-'*10}  {'-'*8}")

    # Per-device latency tracking
    device_latencies: dict[str, list[float]] = {dev.name: [] for dev, _ in active}
    round_latencies: list[float] = []
    lan_latencies: list[float] = []

    for i in range(ROUNDS):
        color = COLORS[i % len(COLORS)]
        color_str = f"({color[0]:>3},{color[1]:>3},{color[2]:>3})"

        # Send to all BLE devices concurrently
        t_round_start = time.perf_counter()

        ble_tasks = [send_ble_color(client, dev, color) for dev, client in active]
        ble_results = await asyncio.gather(*ble_tasks, return_exceptions=True)

        t_ble_done = time.perf_counter()
        ble_total_ms = (t_ble_done - t_round_start) * 1000

        # Record per-device latencies
        ble_times = []
        for (dev, _), result in zip(active, ble_results):
            if isinstance(result, float):
                device_latencies[dev.name].append(result)
                ble_times.append(result)
            else:
                print(f"    [ERR] {dev.name}: {result}")

        ble_max = max(ble_times) if ble_times else 0

        # Send to LAN devices (synchronous UDP, very fast)
        t_lan_start = time.perf_counter()
        for lan_dev in LAN_DEVICES:
            try:
                _send_lan_frame(lan_sock, lan_dev, color)
            except Exception as e:
                print(f"    [ERR] LAN {lan_dev.name}: {e}")
        t_lan_done = time.perf_counter()
        lan_ms = (t_lan_done - t_lan_start) * 1000

        round_latencies.append(ble_total_ms)
        lan_latencies.append(lan_ms)

        print(f"  {i+1:>5}  {color_str:>18}  {ble_total_ms:>10.2f}ms  {ble_max:>8.2f}ms  {lan_ms:>6.2f}ms")

        # Wait for next interval
        elapsed = time.perf_counter() - t_round_start
        wait = INTERVAL_SEC - elapsed
        if wait > 0:
            await asyncio.sleep(wait)

    # Phase 4: Power off all BLE devices
    print(f"\nPhase 3: Powering off BLE devices...")
    for dev, client in active:
        try:
            await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ptreal_power_packet(False), response=False)
            await client.disconnect()
            print(f"  [OK] {dev.name}")
        except Exception as e:
            print(f"  [WARN] {dev.name}: {e}")

    lan_sock.close()

    # Phase 5: Report
    print(f"\n{'=' * 60}")
    print(f"RESULTS SUMMARY")
    print(f"{'=' * 60}")

    print(f"\nPer-device BLE latency (ms):")
    print(f"  {'Device':<20} {'Min':>7} {'Avg':>7} {'Med':>7} {'P95':>7} {'Max':>7} {'N':>4}")
    print(f"  {'-'*20} {'-'*7} {'-'*7} {'-'*7} {'-'*7} {'-'*7} {'-'*4}")

    all_device_stats = {}
    for name, lats in device_latencies.items():
        if not lats:
            continue
        s = sorted(lats)
        stats = {
            "min": round(s[0], 2),
            "avg": round(sum(s) / len(s), 2),
            "median": round(s[len(s) // 2], 2),
            "p95": round(s[int(len(s) * 0.95)], 2),
            "max": round(s[-1], 2),
            "count": len(s),
        }
        all_device_stats[name] = stats
        print(f"  {name:<20} {stats['min']:>7.2f} {stats['avg']:>7.2f} {stats['median']:>7.2f} {stats['p95']:>7.2f} {stats['max']:>7.2f} {stats['count']:>4}")

    if round_latencies:
        s = sorted(round_latencies)
        print(f"\nRound-trip (all BLE concurrent):")
        print(f"  Min: {s[0]:.2f}ms  Avg: {sum(s)/len(s):.2f}ms  Med: {s[len(s)//2]:.2f}ms  P95: {s[int(len(s)*0.95)]:.2f}ms  Max: {s[-1]:.2f}ms")

    if lan_latencies:
        s = sorted(lan_latencies)
        print(f"\nLAN (all devices, UDP):")
        print(f"  Min: {s[0]:.2f}ms  Avg: {sum(s)/len(s):.2f}ms  Med: {s[len(s)//2]:.2f}ms  Max: {s[-1]:.2f}ms")

    # Save results
    results = {
        "ble_devices_attempted": len(BLE_DEVICES),
        "ble_devices_connected": len(active),
        "lan_devices": len(LAN_DEVICES),
        "rounds": ROUNDS,
        "interval_sec": INTERVAL_SEC,
        "per_device": all_device_stats,
        "round_latencies_ms": [round(x, 2) for x in round_latencies],
        "lan_latencies_ms": [round(x, 2) for x in lan_latencies],
    }
    out_path = "data/ble_orchestrated_bench.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nFull results saved to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
