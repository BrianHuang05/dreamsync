"""Enumerate all GATT services and characteristics on a BLE device.

Usage:
    python scripts/ble_enumerate_gatt.py AA:BB:CC:DD:EE:FF > data/gatt_services.txt
"""
import asyncio
import sys

from bleak import BleakClient


async def main(address: str) -> None:
    async with BleakClient(address, timeout=15.0) as client:
        print(f"Connected: {client.is_connected}")
        print(f"Address:   {address}")
        for service in client.services:
            print(f"\nService: {service.uuid}  ({service.description})")
            for char in service.characteristics:
                props = ", ".join(char.properties)
                print(f"  Char: {char.uuid}  [{props}]  ({char.description})")
                for desc in char.descriptors:
                    val = await client.read_gatt_descriptor(desc.handle)
                    print(f"    Desc: {desc.uuid} = {val}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/ble_enumerate_gatt.py <BLE_ADDRESS>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
