"""One writer per confirmed physical light, with conservative session fallback."""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from dreamsync.output.discovery import probe_devices
from dreamsync.output.govee_ble import BleProtocol, GoveeBleAdapter, GoveeBleConfig
from dreamsync.output.govee_lan import GoveeLanAdapter, GoveeLanConfig, TransportMode
from dreamsync.output.transport_selection import candidates_for

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LanObservation:
    available: bool
    response_ms: float | None = None

    def __bool__(self):
        return self.available


def identity_replies(config):
    """Discovery/status is reachability evidence, never optical delivery."""
    replies = probe_devices([config.lan_address], timeout=1.0)
    for device in replies:
        if device.ip == config.lan_address and device.device_id.upper() == config.device_id.upper():
            return LanObservation(True, device.response_ms)
    return LanObservation(False)


class AdaptiveGoveeAdapter(GoveeLanAdapter):
    """Render under the original address; switch only confirmed, validated routes.

    LAN is checked every five seconds. Three missed identity replies, three local
    frame errors, slow/inconsistent replies, or disconnected BLE checks trigger fallback.
    Candidates
    are attempted once per activation, preventing automatic switch-back/flapping.
    """

    def __init__(self, config, *, fps=30, brightness=1.0, lan_factory=GoveeLanAdapter,
                 ble_factory=GoveeBleAdapter, verify_lan=None, clock=time.monotonic,
                 lan_slow_ms=200.0, lan_jitter_ms=100.0):
        logical = GoveeLanConfig(config.address, segments=config.segments, fps=min(fps, config.max_fps),
                                 brightness=brightness, transport=TransportMode(config.transport))
        super().__init__(logical, transport=lambda *_args: None)
        self.identity = config
        self._lan_factory = lan_factory
        self._ble_factory = ble_factory
        self._verify_lan = verify_lan or (lambda: identity_replies(config))
        self._clock = clock
        self._lan_slow_ms = lan_slow_ms
        self._lan_jitter_ms = lan_jitter_ms
        self._last_response_ms = None
        self._candidates = candidates_for(config)
        self._attempted = set()
        self._active = None
        self._kind = ""
        self._activated_at = 0.0
        self._failures = 0
        self._local_errors = 0
        self._brightness = 100
        self._error = ""
        self._state_lock = threading.RLock()
        self._switch_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._monitor = None

    def _release_current(self):
        with self._state_lock:
            active, kind = self._active, self._kind
            self._active, self._kind = None, ""
        if active is None:
            return
        if kind == "ble":
            active.stop()
            if active.worker_running:
                # Retain ownership so close() can retry. Never start a second writer.
                with self._state_lock:
                    self._active, self._kind = active, kind
                raise RuntimeError("BLE writer did not stop; fallback withheld")
        else:
            active.release_stream()

    def _try_next(self, reason):
        with self._switch_lock:
            if self._stop_event.is_set():
                return False
            for candidate in self._candidates:
                if candidate.kind in self._attempted:
                    continue
                self._attempted.add(candidate.kind)
                try:
                    if candidate.kind == "lan":
                        observation = self._verify_lan()
                        available = observation.available if isinstance(observation, LanObservation) else observation
                        if not available:
                            continue
                    self._release_current()
                    if self._stop_event.is_set():
                        return False
                    if candidate.kind == "lan":
                        active = self._lan_factory(GoveeLanConfig(
                            candidate.address, segments=self.config.segments,
                            fps=min(self.config.fps, candidate.validated_fps),
                            brightness=self.config.brightness, transport=self.config.transport,
                        ))
                        try:
                            active.turn_on()
                            active.set_brightness(self._brightness)
                        except Exception:
                            active.release_stream()
                            raise
                    else:
                        active = self._ble_factory(GoveeBleConfig(
                            candidate.address, name=self.identity.name,
                            protocol=BleProtocol(self.identity.protocol), segments=self.config.segments,
                            max_fps=min(self.identity.max_fps, candidate.validated_fps),
                        ))
                        active.start()
                    with self._state_lock:
                        self._active, self._kind = active, candidate.kind
                        self._activated_at = self._clock()
                        self._failures = self._local_errors = 0
                        self._last_response_ms = None
                        self._error = "" if reason == "activation" else f"Fallback after {reason}"
                        self.last_send_ok = True
                    _logger.info("%s selected %s at validated %.2f Hz: %s", self.identity.name,
                                 candidate.kind, candidate.validated_fps, reason)
                    return True
                except Exception as exc:
                    with self._state_lock:
                        self._error = str(exc)
                    _logger.warning("Transport selection for %s failed: %s", self.identity.name, exc)
                    if self._active is not None:
                        # A previous BLE writer could not be stopped.
                        return False
            with self._state_lock:
                self._error = f"No available validated fallback after {reason}"
                self.last_send_ok = False
            return False

    def turn_on(self):
        self.close()
        self._stop_event = threading.Event()
        self._attempted.clear()
        if not self._try_next("activation"):
            raise OSError(self._error or "No available validated transport")
        self._monitor = threading.Thread(target=self._monitor_loop,
                                         args=(self._stop_event,),
                                         name=f"transport-{self.identity.name}", daemon=True)
        self._monitor.start()

    def _monitor_loop(self, stop_event):
        while not stop_event.wait(5.0):
            if stop_event is self._stop_event:
                self.check_health()

    def check_health(self):
        """One passive/identity health check; public for deterministic diagnostics."""
        with self._state_lock:
            active, kind = self._active, self._kind
            local_errors = self._local_errors
            age = self._clock() - self._activated_at
        if active is None or self._stop_event.is_set():
            return
        try:
            if local_errors >= 3:
                healthy = False
            elif kind == "lan":
                observation = self._verify_lan()
                if isinstance(observation, LanObservation):
                    healthy = observation.available
                    if healthy and observation.response_ms is not None:
                        rtt = observation.response_ms
                        inconsistent = (self._last_response_ms is not None
                                        and abs(rtt - self._last_response_ms) > self._lan_jitter_ms)
                        healthy = rtt <= self._lan_slow_ms and not inconsistent
                        self._last_response_ms = rtt
                else:
                    healthy = observation
            else:
                if age < active.config.connect_timeout + 10.0:
                    return  # Allow discovery and serialized fleet setup.
                healthy = active.connected
        except Exception as exc:
            # A busy UDP listener/probe failure is not proof the light is offline.
            with self._state_lock:
                self._error = f"Health check unavailable: {exc}"
            return
        with self._state_lock:
            if active is not self._active:
                return
            self._failures = 0 if healthy else self._failures + 1
            if healthy and self._error.startswith("Health check unavailable:"):
                self._error = ""
            failed = self._failures >= 3 or local_errors >= 3
        if failed:
            self._try_next(f"{kind} health failures")

    def set_brightness(self, value):
        with self._state_lock:
            self._brightness = max(0, min(100, value))
            if self._kind == "lan" and self._active is not None:
                self._active.set_brightness(self._brightness)

    def send_frame(self, colors):
        if self.paused:
            return False
        with self._state_lock:
            active = self._active
            if active is None or self._stop_event.is_set():
                return False
            if self._kind == "lan":
                errors_before = active.send_error_count
                result = active.send_frame(colors)
                if active.send_error_count > errors_before:
                    self._local_errors += 1
                    self.last_send_ok = False
                elif result:
                    self._local_errors = 0
                    self.last_send_ok = True
                return result
            factor = max(0.0, min(1.0, self.config.brightness))
            if factor != 1.0:
                colors = [tuple(int(value * factor) for value in color) for color in colors]
            active.send_segment_colors(colors, brightness=self._brightness)
            return True  # Queued to BLE, not device-confirmed delivery.

    def set_solid_color(self, r, g, b):
        self.send_frame([(r, g, b)] * self.config.segments)

    def close(self):
        self._stop_event.set()
        monitor = self._monitor
        if monitor is not None and monitor is not threading.current_thread():
            monitor.join(timeout=7.0)
        with self._switch_lock:
            self._release_current()
        self._monitor = None

    def turn_off(self):
        try:
            with self._state_lock:
                if self._kind == "lan" and self._active is not None:
                    self._active.turn_off()
        finally:
            self.close()

    def health_snapshot(self):
        with self._state_lock:
            active, kind = self._active, self._kind
            if active is None:
                status = "degraded" if self._error else "unknown"
            elif kind == "ble":
                status = active.health_snapshot()["status"]
            else:
                status = "degraded" if self._failures or not self.last_send_ok else "unknown"
            return {"status": status, "error": self._error, "active_transport": kind,
                    "device_id": self.identity.device_id or ""}
