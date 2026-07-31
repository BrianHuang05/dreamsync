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


def _local_ipv4_addresses() -> list[str]:
    """Return usable local IPv4 addresses for multicast interface selection."""

    addresses: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if not address.startswith("127."):
                addresses.add(address)
    except OSError:
        pass
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            address = probe.getsockname()[0]
            if not address.startswith("127."):
                addresses.add(address)
        finally:
            probe.close()
    except OSError:
        pass
    return sorted(addresses)


def _send_multicast_scan(sender: socket.socket) -> None:
    """Transmit on each active IPv4 interface without failing an entire scan."""

    sent = False
    errors: list[OSError] = []
    for address in _local_ipv4_addresses() or [None]:
        try:
            if address is not None:
                sender.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(address))
            sender.sendto(_SCAN_MSG, (MCAST_GRP, MCAST_PORT))
            sent = True
        except OSError as exc:
            errors.append(exc)
    if not sent:
        detail = "; ".join(str(error) for error in errors) or "no active IPv4 interface"
        _logger.warning("Could not send Govee multicast scan: %s", detail)
        raise OSError(detail)


def scan_devices(timeout: float = 5.0) -> list[GoveeDevice]:
    """Scan the local network for Govee LAN-capable devices.

    Sends a multicast scan packet and listens for UDP replies.
    Returns a list of discovered devices.
    """
    # Listen for replies on UDP 4002
    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind(("", LISTEN_PORT))

        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sender.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            _send_multicast_scan(sender)
        finally:
            sender.close()

        devices: list[GoveeDevice] = []
        seen: set[str] = set()
        import time

        start = time.monotonic()
        while time.monotonic() - start < timeout:
            remaining = timeout - (time.monotonic() - start)
            listener.settimeout(min(0.5, max(0.01, remaining)))
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
                continue
            except OSError as exc:
                _logger.warning("Socket error during scan: %s", exc)
                break
        return devices
    finally:
        listener.close()


def probe_devices(
    ips: list[str],
    timeout: float = 1.0,
) -> list[GoveeDevice]:
    """Directly query known LAN IPs that may not answer multicast discovery."""

    unique_ips = list(dict.fromkeys(str(ip).strip() for ip in ips if str(ip).strip()))
    if not unique_ips:
        return []

    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind(("", LISTEN_PORT))
        listener.settimeout(max(0.01, float(timeout)))
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            for ip in unique_ips:
                sender.sendto(_SCAN_MSG, (ip, MCAST_PORT))
        finally:
            sender.close()

        devices: list[GoveeDevice] = []
        pending = set(unique_ips)
        import time

        deadline = time.monotonic() + max(0.01, float(timeout))
        while pending and time.monotonic() < deadline:
            listener.settimeout(max(0.01, deadline - time.monotonic()))
            try:
                data, addr = listener.recvfrom(4096)
            except socket.timeout:
                break
            ip = addr[0]
            if ip not in pending:
                continue
            try:
                raw = json.loads(data.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            device_data = raw.get("msg", {}).get("data", {})
            devices.append(
                GoveeDevice(
                    ip=ip,
                    sku=str(device_data.get("sku", "")),
                    device_id=str(device_data.get("device", "")),
                    raw=raw,
                )
            )
            pending.remove(ip)
        return devices
    finally:
        listener.close()
