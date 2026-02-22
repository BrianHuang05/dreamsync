"""Map the exact byte layout of the H6006 color command (33 05 0D).

Sends distinct patterns to determine which byte positions map to R, G, B.

Usage:
    python scripts/ble_h6006_color_map.py D0:C9:07:C5:14:45
"""
import asyncio
import sys

from bleak import BleakClient
from dreamsync.output.govee_ble import GOVEE_BLE_CHAR_UUID
from dreamsync.output.govee_lan import build_ptreal_power_packet, _ptreal_checksum


def _build_packet(cmd_bytes: list[int]) -> bytes:
    packet = list(cmd_bytes)
    while len(packet) < 19:
        packet.append(0x00)
    packet = packet[:19]
    packet.append(_ptreal_checksum(packet))
    return bytes(packet)


TESTS = [
    # First: confirm 33 05 0D is the right command, vary the prefix byte
    ("33 05 0D 01 FF 00 00  (mode=01, expect RED?)",  [0x33, 0x05, 0x0D, 0x01, 0xFF, 0x00, 0x00]),
    ("33 05 0D 02 FF 00 00  (mode=02, was GREEN)",    [0x33, 0x05, 0x0D, 0x02, 0xFF, 0x00, 0x00]),

    # Isolate channels: put FF in one position at a time
    ("33 05 0D 02 FF 00 00  (only byte4=FF)",         [0x33, 0x05, 0x0D, 0x02, 0xFF, 0x00, 0x00]),
    ("33 05 0D 02 00 FF 00  (only byte5=FF)",         [0x33, 0x05, 0x0D, 0x02, 0x00, 0xFF, 0x00]),
    ("33 05 0D 02 00 00 FF  (only byte6=FF)",         [0x33, 0x05, 0x0D, 0x02, 0x00, 0x00, 0xFF]),

    # Try without the 02 prefix — maybe RGB starts at byte 3
    ("33 05 0D FF 00 00     (no prefix, byte3=FF)",   [0x33, 0x05, 0x0D, 0xFF, 0x00, 0x00]),
    ("33 05 0D 00 FF 00     (no prefix, byte4=FF)",   [0x33, 0x05, 0x0D, 0x00, 0xFF, 0x00]),
    ("33 05 0D 00 00 FF     (no prefix, byte5=FF)",   [0x33, 0x05, 0x0D, 0x00, 0x00, 0xFF]),
]


async def main(address: str) -> None:
    async with BleakClient(address, timeout=15.0) as client:
        print(f"Connected to {address}\n")

        # Power on
        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, _build_packet([0x33, 0x01, 0x01]), response=False)
        await asyncio.sleep(1.0)

        for name, cmd in TESTS:
            pkt = _build_packet(cmd)
            print(f">>> {name}")
            print(f"    Packet: {pkt.hex()}")
            await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, pkt, response=False)
            print(f"    Observe for 4 sec — what color?")
            await asyncio.sleep(4.0)
            print()

        # Power off
        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, _build_packet([0x33, 0x01, 0x00]), response=False)
        print("Power OFF. Done.\n")
        print("Fill in what you saw:")
        print("-" * 50)
        for name, _ in TESTS:
            print(f"  {name}")
            print(f"    Color: ___________\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/ble_h6006_color_map.py <BLE_ADDRESS>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
