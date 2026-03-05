"""Tests for dreamsync.capture.drift_detector — timing drift detection & correction."""

import pytest

from dreamsync.capture.boundary_queue import BoundaryEntry, BoundaryQueue
from dreamsync.capture.drift_detector import DriftDetector, DriftMeasurement


class TestMeasure:
    def test_no_drift(self):
        dd = DriftDetector(sample_rate=48000)
        m = dd.measure(actual_frames=480000, elapsed_wall_seconds=10.0)
        assert m.drift_frames == 0
        assert m.drift_seconds == 0.0
        assert m.level == "acceptable"

    def test_small_drift_acceptable(self):
        dd = DriftDetector(sample_rate=48000, warning_threshold=0.1)
        # 0.05s drift = 2400 frames
        m = dd.measure(actual_frames=482400, elapsed_wall_seconds=10.0)
        assert m.level == "acceptable"
        assert m.drift_seconds == pytest.approx(0.05)

    def test_warning_drift(self):
        dd = DriftDetector(sample_rate=48000, warning_threshold=0.1)
        # 0.2s drift = 9600 frames
        m = dd.measure(actual_frames=489600, elapsed_wall_seconds=10.0)
        assert m.level == "warning"

    def test_correction_drift(self):
        dd = DriftDetector(sample_rate=48000, correction_threshold=0.5)
        # 1.0s drift = 48000 frames
        m = dd.measure(actual_frames=528000, elapsed_wall_seconds=10.0)
        assert m.level == "correction"

    def test_critical_drift(self):
        dd = DriftDetector(sample_rate=48000, critical_threshold=2.0)
        # 3.0s drift = 144000 frames
        m = dd.measure(actual_frames=624000, elapsed_wall_seconds=10.0)
        assert m.level == "critical"

    def test_negative_drift(self):
        dd = DriftDetector(sample_rate=48000, correction_threshold=0.5)
        # -1.0s drift
        m = dd.measure(actual_frames=432000, elapsed_wall_seconds=10.0)
        assert m.level == "correction"
        assert m.drift_seconds < 0

    def test_last_measurement_stored(self):
        dd = DriftDetector(sample_rate=48000)
        assert dd.last_measurement is None
        m = dd.measure(actual_frames=480000, elapsed_wall_seconds=10.0)
        assert dd.last_measurement is m


class TestCorrect:
    def test_correction_adjusts_boundaries(self):
        q = BoundaryQueue(safety_margin_frames=100)
        q.add(BoundaryEntry(frame_position=500000, segment_index=0))
        q.add(BoundaryEntry(frame_position=1000000, segment_index=1))

        dd = DriftDetector(sample_rate=48000, boundary_queue=q, correction_threshold=0.5)
        m = DriftMeasurement(drift_frames=48000, drift_seconds=1.0, level="correction")

        ok = dd.correct(m, current_frame=0)
        assert ok
        assert dd.corrections_applied == 1

        entries = q.entries()
        # Boundaries should be adjusted by -48000
        assert entries[0].frame_position == 452000
        assert entries[1].frame_position == 952000

    def test_no_correction_for_acceptable(self):
        q = BoundaryQueue()
        dd = DriftDetector(boundary_queue=q)
        m = DriftMeasurement(drift_frames=100, drift_seconds=0.002, level="acceptable")
        assert not dd.correct(m, current_frame=0)
        assert dd.corrections_applied == 0

    def test_no_correction_without_queue(self):
        dd = DriftDetector()
        m = DriftMeasurement(drift_frames=48000, drift_seconds=1.0, level="correction")
        assert not dd.correct(m, current_frame=0)


class TestMeasureAndCorrect:
    def test_auto_corrects_large_drift(self):
        q = BoundaryQueue(safety_margin_frames=100)
        q.add(BoundaryEntry(frame_position=500000, segment_index=0))

        dd = DriftDetector(
            sample_rate=48000,
            boundary_queue=q,
            correction_threshold=0.5,
        )
        # 1s drift
        m = dd.measure_and_correct(
            actual_frames=528000,
            elapsed_wall_seconds=10.0,
            current_frame=0,
        )
        assert m.level == "correction"
        assert dd.corrections_applied == 1

    def test_no_correction_for_small_drift(self):
        q = BoundaryQueue()
        dd = DriftDetector(sample_rate=48000, boundary_queue=q)
        m = dd.measure_and_correct(
            actual_frames=480000,
            elapsed_wall_seconds=10.0,
            current_frame=0,
        )
        assert m.level == "acceptable"
        assert dd.corrections_applied == 0

    def test_critical_triggers_correction(self):
        q = BoundaryQueue(safety_margin_frames=100)
        q.add(BoundaryEntry(frame_position=500000, segment_index=0))
        dd = DriftDetector(
            sample_rate=48000,
            boundary_queue=q,
            critical_threshold=2.0,
        )
        # 3s drift = critical
        m = dd.measure_and_correct(
            actual_frames=624000,
            elapsed_wall_seconds=10.0,
            current_frame=0,
        )
        assert m.level == "critical"
        assert dd.corrections_applied == 1
