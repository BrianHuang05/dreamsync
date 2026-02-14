from __future__ import annotations

import json
import logging
import socket
from dataclasses import dataclass

_logger = logging.getLogger(__name__)

MCAST_GRP = "239.255.255.250"
MCAST_PORT = 4001
LISTEN_PORT = 4002

_SCAN_MSG = json.dumps(
    {"msg": {"cmd": "scan", "data": {"account_topic": "reserve"}}},
    separators=(",", ":"),
).encode("utf-8")


@dataclass(frozen=True)
class GoveeDevice:
    ip: str
    sku: str
    device_id: str
    raw: dict


def scan_devices(timeout: float = 5.0) -> list[GoveeDevice]:
    """Scan the local network for Govee LAN-capable devices.

    Sends a multicast scan packet and listens for UDP replies.
    Returns a list of discovered devices.
    """
    # Listen for replies on UDP 4002
    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("", LISTEN_PORT))
    listener.settimeout(2)

    # Send scan packet to multicast group
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sender.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sender.sendto(_SCAN_MSG, (MCAST_GRP, MCAST_PORT))
    sender.close()

    devices: list[GoveeDevice] = []
    seen: set[str] = set()
    import time

    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            data, addr = listener.recvfrom(4096)
            ip = addr[0]
            if ip in seen:
                continue
            seen.add(ip)
            try:
                raw = json.loads(data.decode("utf-8", errors="replace"))
                msg = raw.get("msg", {})
                device_data = msg.get("data", {})
                sku = device_data.get("sku", "")
                device_id = device_data.get("device", "")
                devices.append(GoveeDevice(ip=ip, sku=sku, device_id=device_id, raw=raw))
                _logger.info("Discovered Govee device: %s (%s) at %s", sku, device_id, ip)
            except (json.JSONDecodeError, KeyError) as exc:
                _logger.warning("Bad scan response from %s: %s", ip, exc)
        except socket.timeout:
            break
        except OSError as exc:
            _logger.warning("Socket error during scan: %s", exc)
            break

    listener.close()
    return devices
