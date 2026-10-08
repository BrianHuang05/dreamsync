"""Gate fallback on sustained LAN rate, preserving the measurement source."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math

LAN_FALLBACK_THRESHOLD_HZ = 4.0


@dataclass(frozen=True)
class LanDeliverySample:
    """A complete window with an explicit host, device or visual source.

    expected_frames is the changing-frame workload offered within the configured
    output cap. A static scene/intentional low rate must not masquerade as link loss.
    Timestamp values use the owning adapter's monotonic clock. Neither UDP send
    success nor scan replies confirm delivery. For host_send, delivered_frames
    means successful local sends only; never report it as confirmed delivery.
    """

    device_id: str
    address: str
    started_at: float
    ended_at: float
    expected_frames: int
    delivered_frames: int
    source: str  # "device_ack", "visual", or explicitly selected "host_send"

    def validate(self):
        if self.source not in {"device_ack", "visual", "host_send"}:
            raise ValueError("LAN rate requires device_ack, visual or host_send evidence")
        if not all(math.isfinite(value) for value in (self.started_at, self.ended_at)):
            raise ValueError("LAN delivery timestamps must be finite")
        if self.ended_at <= self.started_at:
            raise ValueError("LAN delivery window must have positive duration")
        if any(type(value) is not int or value < 0 for value in (self.expected_frames, self.delivered_frames)):
            raise ValueError("LAN frame counts must be nonnegative integers")
        if self.delivered_frames > self.expected_frames:
            raise ValueError("Delivered frames cannot exceed offered frames")

    @property
    def duration(self):
        return self.ended_at - self.started_at

    @property
    def delivered_hz(self):
        return self.delivered_frames / self.duration


class LanRatePolicy:
    """Require three contiguous windows of >=5s below 4 Hz; exactly 4 Hz passes.

    Unknown, stale, idle and out-of-order evidence never initiates fallback.
    A fresh zero-success window under active demand is treated as 0 Hz.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self._low_windows = deque(maxlen=3)
        self.latest = None

    def record(self, sample: LanDeliverySample, now: float) -> bool:
        sample.validate()
        if not math.isfinite(now):
            raise ValueError("LAN delivery observation time must be finite")
        if sample.ended_at > now or now - sample.ended_at > 5.0:
            return False
        if self.latest is not None and sample.started_at < self.latest.ended_at:
            return False  # Duplicate/overlapping evidence cannot satisfy persistence.
        if sample.duration < 5.0:
            self._low_windows.clear()
            return False
        if self.latest is not None and (sample.started_at - self.latest.ended_at > 1e-6
                                      or sample.source != self.latest.source):
            self._low_windows.clear()
        self.latest = sample
        if sample.expected_frames / sample.duration < LAN_FALLBACK_THRESHOLD_HZ:
            self._low_windows.clear()
            return True  # Deliberately low demand is valid evidence, not a failure.
        if sample.delivered_hz < LAN_FALLBACK_THRESHOLD_HZ:
            self._low_windows.append(sample)
        else:
            self._low_windows.clear()
        return True

    def should_fallback(self, now: float) -> bool:
        return (len(self._low_windows) == 3 and self.latest is not None
                and 0 <= now - self.latest.ended_at <= 5.0)
