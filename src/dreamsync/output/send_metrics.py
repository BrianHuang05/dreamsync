"""Bounded host I/O measurements; successful writes do not prove delivery."""
from collections import deque
import threading
import time


class HostSendMetrics:
    def __init__(self, clock=time.monotonic, window_seconds=10.0):
        self.clock = clock
        self.window_seconds = window_seconds
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        with self._lock:
            self._started = self.clock()
            self._samples = deque(maxlen=4096)

    def record(self, started, ended, success):
        with self._lock:
            self._samples.append((started, max(0.0, ended - started), success))

    def snapshot(self):
        now = self.clock()
        with self._lock:
            elapsed = min(self.window_seconds, max(0.0, now - self._started))
            samples = [s for s in self._samples if now - self.window_seconds <= s[0] <= now]
        successes = [s for s in samples if s[2]]
        durations = sorted(s[1] * 1000 for s in successes)
        gaps = [(b[0] - a[0]) * 1000 for a, b in zip(successes, successes[1:])]
        return {
            "host_send_hz": len(successes) / elapsed if elapsed > 0 else None,
            "host_send_ms_p95": durations[min(len(durations) - 1, int(.95 * len(durations)))] if durations else None,
            "host_send_gap_ms_max": max(gaps) if gaps else None,
            "host_send_errors": sum(not s[2] for s in samples),
            "host_measurement": "local sends/writes; delivery unconfirmed",
        }
