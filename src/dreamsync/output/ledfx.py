from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

from dreamsync.director import EffectMode, LightingIntent
from dreamsync.output.roles import DeviceRole, transform_intent

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LedFxConfig:
    base_url: str
    virtual_id: str
    min_update_interval_seconds: float = 0.3
    timeout_seconds: float = 3.0
    debug: bool = False


def _default_transport(url: str, payload: dict, timeout_seconds: float) -> None:
    try:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        req = urllib.request.Request(
            url=url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_seconds):
            pass
    except urllib.error.HTTPError as exc:
        _logger.warning("LedFx HTTP error %s for %s: %s", exc.code, url, exc.reason)
    except urllib.error.URLError as exc:
        _logger.warning("LedFx connection error for %s: %s", url, exc.reason)
    except TimeoutError:
        _logger.warning("LedFx request timeout for %s", url)
    except OSError as exc:
        _logger.warning("LedFx network error for %s: %s", url, exc)


def _default_delete(url: str, timeout_seconds: float) -> None:
    try:
        req = urllib.request.Request(
            url=url,
            headers={"Content-Type": "application/json"},
            method="DELETE",
        )
        with urllib.request.urlopen(req, timeout=timeout_seconds):
            pass
    except urllib.error.HTTPError as exc:
        _logger.warning("LedFx HTTP error %s for %s: %s", exc.code, url, exc.reason)
    except urllib.error.URLError as exc:
        _logger.warning("LedFx connection error for %s: %s", url, exc.reason)
    except TimeoutError:
        _logger.warning("LedFx request timeout for %s", url)
    except OSError as exc:
        _logger.warning("LedFx network error for %s: %s", url, exc)


class LedFxOutputAdapter:
    def __init__(
        self,
        config: LedFxConfig,
        transport: Callable[[str, dict, float], None] | None = None,
        delete_transport: Callable[[str, float], None] | None = None,
        monotonic_fn: Callable[[], float] | None = None,
    ) -> None:
        self.config = config
        self._transport = transport or _default_transport
        self._delete = delete_transport or _default_delete
        self._monotonic = monotonic_fn or time.monotonic
        self._last_sent_at = -1e9
        self._last_payload_key = ""

    def _endpoint(self) -> str:
        base = self.config.base_url.rstrip("/")
        return f"{base}/api/virtuals/{self.config.virtual_id}/effects"

    def _payload_from_intent(self, intent: LightingIntent) -> dict:
        if intent.mode == EffectMode.AMBIENT:
            effect_type = "magnitude"
        elif intent.mode == EffectMode.PULSE:
            effect_type = "energy"
        else:
            effect_type = "scroll"

        return {
            "type": effect_type,
            "config": {
                "brightness": round(intent.intensity, 4),
                "speed": round(intent.speed, 4),
                "bpm_hint": round(intent.bpm, 2),
            },
        }

    def emit(self, t: float, intent: LightingIntent) -> bool:
        del t
        payload = self._payload_from_intent(intent)
        payload_key = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        now = self._monotonic()

        # Dedupe identical writes to avoid repeated no-op API calls.
        if payload_key == self._last_payload_key:
            return False
        # Rate-limit writes to avoid request spam.
        if (now - self._last_sent_at) < self.config.min_update_interval_seconds:
            return False

        if self.config.debug:
            _logger.info("LedFx payload %s", payload_key)
        self._transport(self._endpoint(), payload, self.config.timeout_seconds)
        self._last_sent_at = now
        self._last_payload_key = payload_key
        return True

    def clear_effect(self) -> None:
        if self.config.debug:
            _logger.info("LedFx clear effect %s", self._endpoint())
        self._delete(self._endpoint(), self.config.timeout_seconds)
        self._last_payload_key = ""
        self._last_sent_at = -1e9


class MultiLedFxOutputAdapter:
    """Wraps multiple LedFxOutputAdapters, each with a DeviceRole."""

    def __init__(self, devices: list[tuple[LedFxOutputAdapter, DeviceRole]]):
        self.devices = devices

    def emit(self, t: float, intent: LightingIntent) -> bool:
        any_sent = False
        for adapter, role in self.devices:
            device_intent = transform_intent(intent, role)
            if adapter.emit(t, device_intent):
                any_sent = True
        return any_sent

    def clear_effect(self) -> None:
        for adapter, _ in self.devices:
            adapter.clear_effect()
