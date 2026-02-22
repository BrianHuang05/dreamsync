"""Diagnose H6006 bulb — enumerate GATT and try known bulb command variants.

H6006 bulbs use the "ihoment_" prefix and may have a different GATT
characteristic or command format than strip devices.

Usage:
    python scripts/ble_h6006_diag.py D0:C9:07:C5:14:45
"""
import asyncio
import sys

from bleak import BleakClient
from dreamsync.output.govee_lan import _ptreal_checksum


def _build_packet(cmd_bytes: list[int]) -> bytes:
    packet = list(cmd_bytes)
    while len(packet) < 19:
        packet.append(0x00)
    packet = packet[:19]
    packet.append(_ptreal_checksum(packet))
    return bytes(packet)


# Known Govee BLE characteristic UUIDs across different product lines
CHAR_CANDIDATES = [
    "00010203-0405-0607-0809-0a0b0c0d2b11",  # standard (strips)
    "00010203-0405-0607-0809-0a0b0c0d2b10",  # seen on some bulbs
    "494e5445-4c4c-495f-524f-434b535f2011",   # older ihoment bulbs
    "494e5445-4c4c-495f-524f-434b535f2012",   # older ihoment variant
]

# Command variants for bulbs
BULB_COMMANDS = [
    ("power on: 33 01 01",           [0x33, 0x01, 0x01]),
    ("mode manual: 33 05 01",        [0x33, 0x05, 0x01]),
    ("color RED: 33 05 02 FF 00 00", [0x33, 0x05, 0x02, 0xFF, 0x00, 0x00]),
    ("color RED: 33 05 04 FF 00 00", [0x33, 0x05, 0x04, 0xFF, 0x00, 0x00]),
    # Some bulbs use a different color command with mode prefix
    ("color RED: 33 05 0D 02 FF 00 00", [0x33, 0x05, 0x0D, 0x02, 0xFF, 0x00, 0x00]),
    # H6xxx bulb format from reverse engineering repos
    ("color RED: 33 05 15 02 FF 00 00", [0x33, 0x05, 0x15, 0x02, 0xFF, 0x00, 0x00]),
]


async def main(address: str) -> None:
    async with BleakClient(address, timeout=15.0) as client:
        print(f"Connected to {address}")
        print(f"\n{'='*60}")
        print(f"GATT SERVICE ENUMERATION")
        print(f"{'='*60}")

        write_chars: list[tuple[str, str]] = []
        for service in client.services:
            print(f"\nService: {service.uuid}")
            print(f"  Desc: {service.description}")
            for char in service.characteristics:
                props = ", ".join(char.properties)
                print(f"  Char: {char.uuid}  [{props}]")
                if "write" in char.properties or "write-without-response" in char.properties:
                    write_chars.append((char.uuid, props))

        print(f"\n{'='*60}")
        print(f"WRITABLE CHARACTERISTICS FOUND: {len(write_chars)}")
        print(f"{'='*60}")
        for uuid, props in write_chars:
            known = " <-- STANDARD" if uuid in CHAR_CANDIDATES else ""
            print(f"  {uuid}  [{props}]{known}")

        # Try each writable characteristic with each command
        print(f"\n{'='*60}")
        print(f"TRYING COMMANDS ON EACH WRITABLE CHARACTERISTIC")
        print(f"{'='*60}")

        for char_uuid, props in write_chars:
            use_response = "write" in props and "write-without-response" not in props
            print(f"\n--- Characteristic: {char_uuid} ---")

            for name, cmd in BULB_COMMANDS:
                pkt = _build_packet(cmd)
                print(f"  {name}")
                print(f"    Packet: {pkt.hex()}")
                try:
                    await client.write_gatt_char(char_uuid, pkt, response=use_response)
                    print(f"    Result: OK")
                except Exception as e:
                    print(f"    Result: FAILED ({e})")
                await asyncio.sleep(1.5)
                print(f"    >>> Did the bulb change? <<<")

        # Power off attempt
        print(f"\n--- Power off ---")
        for char_uuid, props in write_chars:
            use_response = "write" in props and "write-without-response" not in props
            try:
                await client.write_gatt_char(char_uuid, _build_packet([0x33, 0x01, 0x00]), response=use_response)
            except Exception:
                pass

        print(f"\nDone. Note which characteristic + command worked (if any).")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/ble_h6006_diag.py <BLE_ADDRESS>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
