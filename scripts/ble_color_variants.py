"""Try multiple color command variants on a Govee BLE device.

Tests which command format the device actually responds to, and whether
the color holds or reverts.

Usage:
    python scripts/ble_color_variants.py C7:90:80:C6:44:74
"""
import asyncio
import sys
import time

from bleak import BleakClient
from dreamsync.output.govee_ble import GOVEE_BLE_CHAR_UUID
from dreamsync.output.govee_lan import build_ptreal_power_packet, _ptreal_checksum


def _build_packet(cmd_bytes: list[int]) -> bytes:
    """Build a 20-byte packet from a short command, pad + checksum."""
    packet = list(cmd_bytes)
    while len(packet) < 19:
        packet.append(0x00)
    packet = packet[:19]
    packet.append(_ptreal_checksum(packet))
    return bytes(packet)


# All the color-setting variants known from reverse engineering repos
VARIANTS = [
    # --- Mode switch commands (sent once before color) ---
    ("mode: 33 05 01 (manual/solid)", [0x33, 0x05, 0x01]),
    ("mode: 33 05 02 (color mode flag)", [0x33, 0x05, 0x02]),
    # Note: 33 05 02 may itself be the "set color mode" command, not "set color"

    # --- Color commands with different sub-commands ---
    ("color: 33 05 02 RR GG BB (whole-device)", [0x33, 0x05, 0x02, 0xFF, 0x00, 0x00]),
    ("color: 33 05 04 RR GG BB (alt color cmd)", [0x33, 0x05, 0x04, 0xFF, 0x00, 0x00]),
    ("color: 33 05 02 01 RR GG BB (with prefix byte)", [0x33, 0x05, 0x02, 0x01, 0xFF, 0x00, 0x00]),
    ("color: 33 05 02 02 RR GG BB (with prefix 02)", [0x33, 0x05, 0x02, 0x02, 0xFF, 0x00, 0x00]),

    # --- H61xx-family format: mode byte + color ---
    ("color: 33 05 01 RR GG BB (mode+color combined)", [0x33, 0x05, 0x01, 0xFF, 0x00, 0x00]),

    # --- Some devices use 0x33 0x01 for on, then 0x33 0x05 0x0D for color ---
    ("color: 33 05 0D 01 RR GG BB (scene-based)", [0x33, 0x05, 0x0D, 0x01, 0xFF, 0x00, 0x00]),

    # --- Legacy ihoment format ---
    ("color: 33 05 15 01 RR GG BB + bitmask (segment)", None),  # special case below
]


async def main(address: str) -> None:
    async with BleakClient(address, timeout=15.0) as client:
        print(f"Connected to {address}\n")

        # Power on first
        pkt = build_ptreal_power_packet(True)
        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, pkt, response=False)
        await asyncio.sleep(1.0)
        print("Power ON sent\n")

        for name, cmd in VARIANTS:
            if cmd is None:
                # Segment variant: 33 05 15 01 RR GG BB [pad] [bitmask all-on]
                from dreamsync.output.govee_lan import build_ptreal_segment_packets
                packets = build_ptreal_segment_packets([(255, 0, 0)] * 15)
                print(f">>> {name}")
                print(f"    Sending {len(packets)} segment packet(s)...")
                for p in packets:
                    await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, p, response=False)
                    await asyncio.sleep(0.05)
            else:
                pkt = _build_packet(cmd)
                print(f">>> {name}")
                print(f"    Packet: {pkt.hex()}")
                await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, pkt, response=False)

            print(f"    Waiting 4 seconds — observe the device...")
            await asyncio.sleep(4.0)
            print()

        # Power off
        await client.write_gatt_char(GOVEE_BLE_CHAR_UUID, build_ptreal_power_packet(False), response=False)
        print("Power OFF sent. Done.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/ble_color_variants.py <BLE_ADDRESS>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
