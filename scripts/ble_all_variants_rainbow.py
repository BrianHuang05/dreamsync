"""Test all 9 command variants with distinct colors for easy identification.

Set the device to white in the Govee app, close the app, then run this.
Each variant gets 6 seconds. Note which color you see for each.

Color key:
  1. RED        = mode: 33 05 01
  2. ORANGE     = mode: 33 05 02
  3. YELLOW     = color: 33 05 02 RR GG BB
  4. GREEN      = color: 33 05 04 RR GG BB
  5. CYAN       = color: 33 05 02 01 RR GG BB
  6. BLUE       = color: 33 05 02 02 RR GG BB
  7. PURPLE     = color: 33 05 01 RR GG BB
  8. PINK       = color: 33 05 0D 01 RR GG BB
  9. WHITE(seg) = segment: 33 05 15 01 + bitmask (teal)

Usage:
    python scripts/ble_all_variants_rainbow.py C7:90:80:C6:44:74
"""
import asyncio
import sys

from bleak import BleakClient
from dreamsync.output.govee_ble import GOVEE_BLE_CHAR_UUID
from dreamsync.output.govee_lan import (
    build_ptreal_power_packet,
    build_ptreal_segment_packets,
    _ptreal_checksum,
)


def _build_packet(cmd_bytes: list[int]) -> bytes:
    packet = list(cmd_bytes)
    while len(packet) < 19:
        packet.append(0x00)
    packet = packet[:19]
    packet.append(_ptreal_checksum(packet))
    return bytes(packet)


TESTS = [
    ("1. mode: 33 05 01",                       [0x33, 0x05, 0x01],                               "RED",    None),
    ("2. mode: 33 05 02",                        [0x33, 0x05, 0x02],                               "ORANGE", None),
    ("3. color: 33 05 02 RR GG BB",              [0x33, 0x05, 0x02, 0xFF, 0xFF, 0x00],             "YELLOW", None),
    ("4. color: 33 05 04 RR GG BB",              [0x33, 0x05, 0x04, 0x00, 0xFF, 0x00],             "GREEN",  None),
    ("5. color: 33 05 02 01 RR GG BB",           [0x33, 0x05, 0x02, 0x01, 0x00, 0xFF, 0xFF],       "CYAN",   None),
    ("6. color: 33 05 02 02 RR GG BB",           [0x33, 0x05, 0x02, 0x02, 0x00, 0x00, 0xFF],       "BLUE",   None),
    ("7. color: 33 05 01 RR GG BB",              [0x33, 0x05, 0x01, 0x80, 0x00, 0xFF],             "PURPLE", None),
    ("8. color: 33 05 0D 01 RR GG BB",           [0x33, 0x05, 0x0D, 0x01, 0xFF, 0x00, 0x80],       "PINK",   None),
    ("9. segment: 33 05 15 01 + bitmask",         None,                                             "TEAL",   (0, 0x80, 0x80)),
]


async def main(address: str) -> None:
    async with BleakClient(address, timeout=15.0) as client:
        print(f"Connected to {address}")
        print(f"Make sure device is set to WHITE in app, app is closed.\n")

        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ptreal_power_packet(True), response=False)
        await asyncio.sleep(1.0)
        print("Power ON sent\n")
        print("=" * 65)

        for name, cmd, expected_color, seg_color in TESTS:
            print(f"\n>>> {name}")
            print(f"    Expected color: {expected_color}")

            if cmd is not None:
                pkt = _build_packet(cmd)
                print(f"    Packet: {pkt.hex()}")
                await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, pkt, response=False)
            else:
                # Segment variant
                packets = build_ptreal_segment_packets([seg_color] * 15)
                for i, p in enumerate(packets):
                    print(f"    Packet {i+1}: {p.hex()}")
                    await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, p, response=False)
                    await asyncio.sleep(0.05)

            print(f"    >>> OBSERVE for 6 seconds <<<")
            await asyncio.sleep(6.0)

        print("\n" + "=" * 65)
        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ptreal_power_packet(False), response=False)
        print("\nPower OFF sent. Done.\n")
        print("RESULTS — fill in what you actually saw:")
        print("-" * 65)
        for name, _, expected_color, _ in TESTS:
            print(f"  {name}")
            print(f"    Expected: {expected_color}")
            print(f"    Actual:   _______________")
            print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/ble_all_variants_rainbow.py <BLE_ADDRESS>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
