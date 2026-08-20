"""Cloud preflight controls for Govee DreamView sessions.

The LAN and BLE protocols do not expose a way to exit an active Scenic
DreamView.  Govee's cloud API does: Scenic DreamViews appear as virtual
``DreamViewScenic`` devices, while Movie and Music sync centers advertise a
``dreamViewToggle`` capability.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.error import URLError
from urllib.request import Request, urlopen


_LOGGER = logging.getLogger(__name__)
_API_ROOT = "https://openapi.api.govee.com/router/api/v1"
_API_KEY_ENV = "GOVEE_API_KEY"
_SECRETS_FILE_ENV = "DREAMSYNC_SECRETS_FILE"

ApiRequest = Callable[[str, str, dict[str, str], dict[str, Any] | None], dict[str, Any]]


@dataclass(frozen=True)
class DreamViewShutdownResult:
    """Summary of a best-effort cloud DreamView shutdown preflight."""

    attempted: bool
    disabled: tuple[str, ...] = ()
    failures: tuple[str, ...] = ()


def shutdown_active_dreamviews(
    *,
    api_key: str | None = None,
    request: ApiRequest | None = None,
) -> DreamViewShutdownResult:
    """Disable all account-visible Scenic, Movie, and Music DreamViews.

    This is intentionally best-effort: a cloud outage must not prevent local
    LAN/BLE lighting from starting.  Scenic groups use Govee's virtual-device
    command semantics, for which value ``1`` exits the active Scenic session
    (verified against the platform API); physical sync centers use the
    documented ``dreamViewToggle`` value ``0``.
    """
    key = _resolve_api_key(api_key)
    if not key:
        return DreamViewShutdownResult(attempted=False)

    api_request = request or _platform_request
    try:
        payload = api_request("GET", "/user/devices", {"Govee-API-Key": key}, None)
    except (OSError, ValueError) as exc:
        _LOGGER.warning("Govee DreamView preflight discovery failed: %s", exc)
        return DreamViewShutdownResult(attempted=True, failures=(f"discovery: {exc}",))

    devices = payload.get("data")
    if not isinstance(devices, list):
        detail = str(payload.get("message") or payload.get("msg") or "invalid response")
        _LOGGER.warning("Govee DreamView preflight discovery returned no devices: %s", detail)
        return DreamViewShutdownResult(attempted=True, failures=(f"discovery: {detail}",))

    disabled: list[str] = []
    failures: list[str] = []
    for device in devices:
        if not isinstance(device, dict):
            continue
        sku = device.get("sku")
        device_id = device.get("device")
        if not isinstance(sku, str) or not isinstance(device_id, str):
            continue

        capability = _shutdown_capability(device)
        if capability is None:
            continue
        label = str(device.get("deviceName") or device_id)
        try:
            response = api_request(
                "POST",
                "/device/control",
                {"Govee-API-Key": key, "Content-Type": "application/json"},
                {
                    "requestId": str(uuid.uuid4()),
                    "payload": {
                        "sku": sku,
                        "device": device_id,
                        "capability": capability,
                    },
                },
            )
        except (OSError, ValueError) as exc:
            failures.append(f"{label}: {exc}")
            continue

        if response.get("code") == 200:
            disabled.append(label)
        else:
            detail = str(response.get("message") or response.get("msg") or response)
            failures.append(f"{label}: {detail}")

    if disabled:
        _LOGGER.info("Disabled Govee DreamView session(s): %s", ", ".join(disabled))
    for failure in failures:
        _LOGGER.warning("Could not disable Govee DreamView session: %s", failure)
    return DreamViewShutdownResult(True, tuple(disabled), tuple(failures))


def _resolve_api_key(api_key: str | None) -> str:
    """Resolve an explicit key, environment key, or local secrets-file key."""
    if api_key is not None:
        return api_key.strip()
    from_environment = os.environ.get(_API_KEY_ENV, "").strip()
    if from_environment:
        return from_environment
    raw_path = os.environ.get(_SECRETS_FILE_ENV, "").strip()
    if not raw_path:
        return ""
    try:
        return _read_api_key_file(Path(raw_path).expanduser())
    except OSError as exc:
        _LOGGER.warning("Could not read DreamSync secrets file: %s", exc)
        return ""


def _read_api_key_file(path: Path) -> str:
    """Read ``GOVEE_API_KEY=value`` from a simple dotenv-compatible file."""
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        if name.strip().removeprefix("export ").strip() != _API_KEY_ENV:
            continue
        return value.strip().strip("\"'")
    return ""


def _shutdown_capability(device: dict[str, Any]) -> dict[str, Any] | None:
    """Return the appropriate off command for one platform API device."""
    sku = device.get("sku")
    capabilities = device.get("capabilities")
    if not isinstance(capabilities, list):
        return None

    if sku == "DreamViewScenic":
        if _has_capability(capabilities, "devices.capabilities.on_off", "powerSwitch"):
            # Govee's virtual Scenic entity uses 1 to dismiss its active scene.
            return {
                "type": "devices.capabilities.on_off",
                "instance": "powerSwitch",
                "value": 1,
            }
        return None

    if _has_capability(capabilities, "devices.capabilities.toggle", "dreamViewToggle"):
        return {
            "type": "devices.capabilities.toggle",
            "instance": "dreamViewToggle",
            "value": 0,
        }
    return None


def _has_capability(capabilities: list[Any], capability_type: str, instance: str) -> bool:
    return any(
        isinstance(capability, dict)
        and capability.get("type") == capability_type
        and capability.get("instance") == instance
        for capability in capabilities
    )


def _platform_request(
    method: str,
    path: str,
    headers: dict[str, str],
    body: dict[str, Any] | None,
) -> dict[str, Any]:
    data = json.dumps(body, separators=(",", ":")).encode("utf-8") if body is not None else None
    request = Request(f"{_API_ROOT}{path}", data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=10) as response:
            parsed = json.loads(response.read().decode("utf-8"))
    except URLError as exc:
        raise OSError(exc.reason) from exc
    if not isinstance(parsed, dict):
        raise ValueError("Govee API returned a non-object JSON response")
    return parsed
