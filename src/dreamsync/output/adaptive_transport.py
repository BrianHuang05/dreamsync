"""One writer per confirmed physical light, with conservative session fallback."""
from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass

from dreamsync.output.discovery import probe_devices
from dreamsync.output.govee_ble import BleProtocol, GoveeBleAdapter, GoveeBleConfig
from dreamsync.output.govee_lan import GoveeLanAdapter, GoveeLanConfig, TransportMode
from dreamsync.output.transport_selection import candidates_for
from dreamsync.output.lan_rate_policy import LanDeliverySample, LanRatePolicy

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

    Sustained successful host sends below 4 Hz trigger fallback under active
    demand. UDP success is not delivery confirmation. BLE ownership is released
    before a LAN recovery trial; cooldown and repeated identity probes limit flapping.
    """

    def __init__(self, config, *, fps=30, brightness=1.0, lan_factory=GoveeLanAdapter,
                 ble_factory=GoveeBleAdapter, verify_lan=None, clock=time.monotonic):
        logical = GoveeLanConfig(config.address, segments=config.segments, fps=min(fps, config.max_fps),
                                 brightness=brightness, transport=TransportMode(config.transport))
        super().__init__(logical, transport=lambda *_args: None)
        self.identity = config
        self._lan_factory = lan_factory
        self._ble_factory = ble_factory
        self._verify_lan = verify_lan or (lambda: identity_replies(config))
        self._clock = clock
        self._lan_rate_policy = LanRatePolicy()
        self._host_rate_policy = LanRatePolicy()
        self._host_window_at = self._clock()
        self._offered_frames = self._host_sent_frames = 0
        self._next_lan_retry_at = 0.0
        self._lan_retry_delay = 30.0
        self._lan_recovery_checks = 0
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

    def _try_next(self, reason, *, require_low_lan_rate=False, retry_lan=False):
        with self._switch_lock:
            if self._stop_event.is_set():
                return False
            if retry_lan:
                self._attempted.discard("lan")
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
                    with self._state_lock:
                        if retry_lan and candidate.kind == "lan" and (self.paused or self._kind != "ble"):
                            return False
                        if require_low_lan_rate and (self._kind != "lan" or self.paused
                                or not self._rate_requires_fallback()):
                            self._attempted.discard(candidate.kind)
                            return False
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
                        if retry_lan and candidate.kind == "lan":
                            self._attempted.discard("ble")
                        self._activated_at = self._clock()
                        self._failures = self._local_errors = 0
                        self._lan_rate_policy.reset()
                        self._reset_host_window()
                        self._lan_recovery_checks = 0
                        self._next_lan_retry_at = self._clock() + self._lan_retry_delay
                        self._error = "" if reason == "activation" or retry_lan else f"Fallback after {reason}"
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
                    if retry_lan:
                        # LAN activation failed after stopping BLE. Restore its writer.
                        self._attempted.discard("ble")
            with self._state_lock:
                self._error = f"No available validated fallback after {reason}"
                self.last_send_ok = False
            return False

    def turn_on(self):
        self.close()
        self._stop_event = threading.Event()
        self._attempted.clear()
        self._lan_retry_delay = 30.0
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
            if self.paused:
                self._lan_rate_policy.reset()
                self._reset_host_window()
                self._lan_recovery_checks = 0
                return
            active, kind = self._active, self._kind
            local_errors = self._local_errors
            age = self._clock() - self._activated_at
        if active is None or self._stop_event.is_set():
            return
        if kind == "ble":
            self._check_lan_recovery()
            with self._state_lock:
                if active is not self._active:
                    return
        with self._state_lock:
            if active is not self._active or self._stop_event.is_set():
                return
            if kind == "lan":
                self._record_host_window()
            below_threshold = kind == "lan" and self._rate_requires_fallback()
        if below_threshold:
            self._try_next("LAN rate below 4 Hz for at least 15 seconds",
                           require_low_lan_rate=True)
            return
        try:
            if local_errors >= 3:
                healthy = False
            elif kind == "lan":
                observation = self._verify_lan()
                if isinstance(observation, LanObservation):
                    healthy = observation.available
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
            failed = kind == "ble" and self._failures >= 3
            if kind == "lan" and (self._failures >= 3 or local_errors >= 3):
                self._error = "LAN health degraded; sustained rate below 4 Hz required before fallback"
            elif kind == "lan" and healthy and self._error.startswith("LAN health degraded;"):
                self._error = ""
        if failed:
            self._try_next(f"{kind} health failures")

    def _reset_host_window(self):
        self._host_rate_policy.reset()
        self._host_window_at = self._clock()
        self._offered_frames = self._host_sent_frames = 0

    def _record_host_window(self):
        now = self._clock()
        duration = now - self._host_window_at
        if duration < 5.0:
            return
        expected = min(self._offered_frames, math.ceil(self._active.config.fps * duration))
        self._host_rate_policy.record(LanDeliverySample(
            self.identity.device_id, self.identity.lan_address, self._host_window_at, now,
            expected, min(expected, self._host_sent_frames), "host_send"), now)
        self._host_window_at = now
        self._offered_frames = self._host_sent_frames = 0

    def _rate_requires_fallback(self):
        return (self._host_rate_policy.should_fallback(self._clock())
                or self._lan_rate_policy.should_fallback(self._clock()))

    def _check_lan_recovery(self):
        """Retry only the paired LAN identity; replies authorize a trial, not delivery claims."""
        with self._state_lock:
            now = self._clock()
            if (now < self._next_lan_retry_at or self.paused
                    or not any(c.kind == "lan" for c in self._candidates)):
                return
            self._next_lan_retry_at = now + 5.0
        try:
            healthy = bool(self._verify_lan())
        except Exception:
            healthy = False
        with self._state_lock:
            if self._kind != "ble" or self.paused or self._stop_event.is_set():
                return
            self._lan_recovery_checks = self._lan_recovery_checks + 1 if healthy else 0
            if not healthy:
                self._next_lan_retry_at = now + self._lan_retry_delay
                self._lan_retry_delay = min(120.0, self._lan_retry_delay * 2)
            ready = self._lan_recovery_checks >= 3
        if ready:
            self._lan_retry_delay = min(120.0, self._lan_retry_delay * 2)
            if not self._try_next("LAN recovery trial after three identity replies", retry_lan=True):
                with self._state_lock:
                    self._lan_recovery_checks = 0
                    self._next_lan_retry_at = self._clock() + self._lan_retry_delay

    def report_lan_delivery_sample(self, sample: LanDeliverySample) -> bool:
        """Accept explicit delivery evidence for this identity/current activation.

        Existing scan/send metrics must never call this API as delivery evidence.
        Acknowledgment/visual measurement acquisition is an external integration.
        """
        sample.validate()
        if sample.source == "host_send":
            return False  # Host samples come only from this owner's frame sends.
        with self._state_lock:
            if (self._kind != "lan" or self._stop_event.is_set() or self.paused
                    or sample.device_id.upper() != self.identity.device_id.upper()
                    or sample.address != self.identity.lan_address
                    or sample.started_at < self._activated_at):
                return False
            return self._lan_rate_policy.record(sample, self._clock())

    def set_brightness(self, value):
        with self._state_lock:
            self._brightness = max(0, min(100, value))
            if self._kind == "lan" and self._active is not None:
                self._active.set_brightness(self._brightness)

    def send_frame(self, colors):
        if self.paused:
            with self._state_lock:
                self._lan_rate_policy.reset()
                self._reset_host_window()
            return False
        with self._state_lock:
            active = self._active
            if active is None or self._stop_event.is_set():
                return False
            if self._kind == "lan":
                self._offered_frames += 1
                errors_before = active.send_error_count
                result = active.send_frame(colors)
                if active.send_error_count > errors_before:
                    self._local_errors += 1
                    self.last_send_ok = False
                elif result:
                    self._host_sent_frames += 1
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
            sample = self._lan_rate_policy.latest
            fresh = (kind == "lan" and sample is not None and not self.paused
                     and 0 <= self._clock() - sample.ended_at <= 5.0)
            metrics = getattr(active, "health_snapshot", lambda: {})() if active is not None else {}
            host_sample = self._host_rate_policy.latest
            return {**metrics, "status": status, "error": self._error, "active_transport": kind,
                    "device_id": self.identity.device_id or "",
                    "lan_delivery_rate_hz": f"{sample.delivered_hz:.3f}" if fresh else "unknown",
                    "lan_delivery_source": sample.source if fresh else "unknown",
                    "lan_fallback_threshold_hz": "4.000",
                    "lan_switch_rate_source": "host_send",
                    "lan_host_window_hz": host_sample.delivered_hz if kind == "lan" and host_sample
                        and not self.paused and 0 <= self._clock() - host_sample.ended_at <= 5.0 else None}
