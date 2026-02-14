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

# LedFx effects that use color_lows / color_mids / color_high instead of a
# single ``color`` key.  The generic ``color`` field is silently ignored by
# these effects, so we must map the beat color to the band-specific keys.
_BAND_COLOR_EFFECTS = frozenset({"scroll", "wavelength"})


def _darken_hex(color: str, factor: float = 0.3) -> str:
    """Scale RGB values down by *factor* to produce a darker shade."""
    h = color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    r = int(r * factor)
    g = int(g * factor)
    b = int(b * factor)
    return f"#{r:02x}{g:02x}{b:02x}"


@dataclass(frozen=True)
class LedFxConfig:
    base_url: str
    virtual_id: str
    min_update_interval_seconds: float = 0.3
    timeout_seconds: float = 3.0
    debug: bool = False
    effect_type_override: str | None = None
    mirror: bool = True


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


def _default_put_transport(url: str, payload: dict, timeout_seconds: float) -> None:
    try:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        req = urllib.request.Request(
            url=url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="PUT",
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
        put_transport: Callable[[str, dict, float], None] | None = None,
        delete_transport: Callable[[str, float], None] | None = None,
        monotonic_fn: Callable[[], float] | None = None,
    ) -> None:
        self.config = config
        self._transport = transport or _default_transport
        self._put_transport = put_transport or _default_put_transport
        self._delete = delete_transport or _default_delete
        self._monotonic = monotonic_fn or time.monotonic
        self._last_sent_at = -1e9
        self._last_payload_key = ""
        self._active_effect_type: str | None = None

    def _endpoint(self) -> str:
        base = self.config.base_url.rstrip("/")
        return f"{base}/api/virtuals/{self.config.virtual_id}/effects"

    def _payload_from_intent(self, intent: LightingIntent) -> dict:
        if intent.mode == EffectMode.AMBIENT:
            effect_type = "magnitude"
        elif intent.mode == EffectMode.PULSE:
            effect_type = "energy"
        elif intent.mode == EffectMode.RIPPLE:
            effect_type = self.config.effect_type_override or "power"
        else:
            effect_type = "scroll"

        if effect_type in _BAND_COLOR_EFFECTS and intent.mode == EffectMode.RIPPLE:
            config: dict = {
                "brightness": round(intent.intensity, 4),
                "mirror": self.config.mirror,
            }
            if intent.color is not None:
                config["color_lows"] = intent.color
                config["color_mids"] = intent.color
                config["color_high"] = intent.color
                config["background_color"] = _darken_hex(intent.color)
        else:
            config = {
                "brightness": round(intent.intensity, 4),
                "speed": round(intent.speed, 4),
            }
            if intent.mode == EffectMode.RIPPLE:
                config["mirror"] = self.config.mirror
            if intent.color is not None:
                config["color"] = intent.color

        return {
            "type": effect_type,
            "config": config,
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

        effect_type = payload["type"]
        if self.config.debug:
            _logger.info("LedFx payload %s", payload_key)
        if self._active_effect_type == effect_type:
            self._put_transport(self._endpoint(), {"config": payload["config"]}, self.config.timeout_seconds)
        else:
            self._transport(self._endpoint(), payload, self.config.timeout_seconds)
            self._active_effect_type = effect_type
        self._last_sent_at = now
        self._last_payload_key = payload_key
        return True

    def clear_effect(self) -> None:
        if self.config.debug:
            _logger.info("LedFx clear effect %s", self._endpoint())
        self._delete(self._endpoint(), self.config.timeout_seconds)
        self._last_payload_key = ""
        self._last_sent_at = -1e9
        self._active_effect_type = None


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
