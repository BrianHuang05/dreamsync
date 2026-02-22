"""Raw BLE scan — shows ALL Bluetooth LE devices, not just Govee.

Usage:
    python scripts/raw_ble_scan.py > data/raw_ble_scan.txt
"""
import asyncio
from bleak import BleakScanner


async def main() -> None:
    devices = await BleakScanner.discover(timeout=10.0)
    for d in sorted(devices, key=lambda x: x.rssi or -999, reverse=True):
        print(f"  {d.address}  RSSI={d.rssi:>4}  {d.name or '(unnamed)'}")
    print(f"\nTotal: {len(devices)} devices")


if __name__ == "__main__":
    asyncio.run(main())
