"""Detect and correct timing drift between planned boundaries and capture clock."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from dreamsync.capture.boundary_queue import BoundaryQueue

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DriftMeasurement:
    """Result of a single drift measurement."""

    drift_frames: int
    drift_seconds: float
    level: str  # "acceptable", "warning", "correction", "critical"


class DriftDetector:
    """Measure and correct timing drift in the capture pipeline.

    Parameters
    ----------
    sample_rate:
        Audio sample rate for frame ↔ seconds conversion.
    boundary_queue:
        Queue whose future boundaries will be adjusted on correction.
    warning_threshold:
        Drift in seconds that triggers a warning log.
    correction_threshold:
        Drift in seconds that triggers automatic boundary adjustment.
    critical_threshold:
        Drift in seconds that triggers a force-resync error log.
    """

    def __init__(
        self,
        sample_rate: int = 48000,
        boundary_queue: BoundaryQueue | None = None,
        warning_threshold: float = 0.1,
        correction_threshold: float = 0.5,
        critical_threshold: float = 2.0,
    ) -> None:
        self._sample_rate = sample_rate
        self._queue = boundary_queue
        self._warn = warning_threshold
        self._correct = correction_threshold
        self._critical = critical_threshold
        self._corrections_applied: int = 0
        self._last_measurement: DriftMeasurement | None = None

    @property
    def corrections_applied(self) -> int:
        return self._corrections_applied

    @property
    def last_measurement(self) -> DriftMeasurement | None:
        return self._last_measurement

    def measure(
        self,
        actual_frames: int,
        elapsed_wall_seconds: float,
    ) -> DriftMeasurement:
        """Compare actual captured frames against wall-clock expectation.

        Parameters
        ----------
        actual_frames:
            Total frames reported by the capture frame counter.
        elapsed_wall_seconds:
            Wall-clock time since capture began.

        Returns
        -------
        DriftMeasurement with the drift magnitude and severity level.
        """
        expected = round(elapsed_wall_seconds * self._sample_rate)
        drift_frames = actual_frames - expected
        drift_seconds = drift_frames / self._sample_rate

        abs_drift = abs(drift_seconds)
        if abs_drift >= self._critical:
            level = "critical"
        elif abs_drift >= self._correct:
            level = "correction"
        elif abs_drift >= self._warn:
            level = "warning"
        else:
            level = "acceptable"

        m = DriftMeasurement(
            drift_frames=drift_frames,
            drift_seconds=drift_seconds,
            level=level,
        )
        self._last_measurement = m
        self._log_measurement(m)
        return m

    def correct(self, measurement: DriftMeasurement, current_frame: int) -> bool:
        """Adjust future boundaries to compensate for measured drift.

        Returns True if a correction was applied.
        """
        if measurement.level not in ("correction", "critical"):
            return False
        if self._queue is None:
            return False

        offset = measurement.drift_frames
        entries = self._queue.entries()
        for entry in entries:
            if not entry.locked:
                entry.frame_position -= offset

        # Replace with adjusted entries.
        unlocked = [e for e in entries if not e.locked]
        self._queue.replace_future(unlocked, current_frame)

        self._corrections_applied += 1
        logger.info(
            "Drift correction applied: offset=%d frames (%.3fs), corrections_total=%d",
            offset,
            measurement.drift_seconds,
            self._corrections_applied,
        )
        return True

    def measure_and_correct(
        self,
        actual_frames: int,
        elapsed_wall_seconds: float,
        current_frame: int,
    ) -> DriftMeasurement:
        """Convenience: measure drift and auto-correct if needed."""
        m = self.measure(actual_frames, elapsed_wall_seconds)
        if m.level in ("correction", "critical"):
            self.correct(m, current_frame)
        return m

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _log_measurement(self, m: DriftMeasurement) -> None:
        msg = "Drift: %.4fs (%d frames) [%s]" % (
            m.drift_seconds,
            m.drift_frames,
            m.level,
        )
        if m.level == "critical":
            logger.error(msg)
        elif m.level == "warning":
            logger.warning(msg)
        elif m.level == "correction":
            logger.warning(msg)
        else:
            logger.debug(msg)
