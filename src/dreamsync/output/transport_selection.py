"""Explicit physical identities and evidence-based LAN/BLE selection.

Validated rates are user/hardware observations, never UDP send durations or
BLE write-without-response timings. Selection only considers confirmed endpoints.
"""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import math
import re

from dreamsync.output.govee_ble import BLE_SAFE_MAX_FPS


@dataclass(frozen=True)
class TransportCandidate:
    kind: str
    address: str
    validated_fps: float


def candidates_for(config) -> tuple[TransportCandidate, ...]:
    """Prefer validated LAN; use validated BLE as a conservative fallback."""
    candidates = []
    lan = config.lan_address or (config.address if config.type == "lan" else None)
    ble = config.ble_address or (config.address if config.type == "ble" else None)
    if lan and config.lan_validated_fps is not None:
        candidates.append(TransportCandidate("lan", lan, min(config.max_fps, config.lan_validated_fps)))
    if ble and config.ble_validated_fps is not None:
        candidates.append(TransportCandidate("ble", ble, min(BLE_SAFE_MAX_FPS, config.max_fps, config.ble_validated_fps)))
    return tuple(sorted(candidates, key=lambda c: c.kind != "lan"))


def validate_transport_configs(configs) -> None:
    """Reject ambiguous identity/endpoint ownership before contacting hardware."""
    owners = {}
    for config in configs:
        if config.transport_policy not in {"fixed", "auto"}:
            raise ValueError(f"Unknown transport_policy for {config.name}")
        if config.device_id and not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5,7}[0-9A-Fa-f]{2}", config.device_id):
            raise ValueError(f"Invalid physical device_id for {config.name}")
        for name in ("lan_validated_fps", "ble_validated_fps"):
            rate = getattr(config, name)
            if rate is not None and (not math.isfinite(rate) or rate <= 0):
                raise ValueError(f"{name} must be finite and positive for {config.name}")
        if config.lan_address:
            ipaddress.IPv4Address(config.lan_address)
        if config.ble_address and not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", config.ble_address):
            raise ValueError(f"Invalid ble_address for {config.name}")
        if config.transport_policy == "auto":
            if not math.isfinite(config.max_fps) or config.max_fps <= 0:
                raise ValueError(f"max_fps must be finite and positive for {config.name}")
            if not config.device_id or not config.lan_address or not config.ble_address:
                raise ValueError(f"Automatic selection requires device_id and confirmed lan_address/ble_address for {config.name}")
            if not config.transport or not config.protocol:
                raise ValueError(f"Automatic selection requires explicit LAN transport and BLE protocol for {config.name}")
            if not candidates_for(config):
                raise ValueError(f"Automatic selection requires at least one validated rate for {config.name}")
        identities = [config.address, config.lan_address, config.ble_address]
        if config.device_id:
            identities.append("id:" + config.device_id)
        for identity in set(str(v).upper() for v in identities if v):
            previous = owners.get(identity)
            if previous is not None:
                raise ValueError(f"Endpoint/identity {identity} belongs to both {previous} and {config.name}")
            owners[identity] = config.name
