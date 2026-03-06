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
        # Establish zero baseline
        dd.measure(actual_frames=480000, elapsed_wall_seconds=10.0)
        # 0.2s drift = 9600 frames
        m = dd.measure(actual_frames=969600, elapsed_wall_seconds=20.0)
        assert m.level == "warning"

    def test_correction_drift(self):
        dd = DriftDetector(sample_rate=48000, correction_threshold=0.5)
        # Establish zero baseline
        dd.measure(actual_frames=480000, elapsed_wall_seconds=10.0)
        # 1.0s drift = 48000 frames adjusted
        m = dd.measure(actual_frames=1008000, elapsed_wall_seconds=20.0)
        assert m.level == "correction"

    def test_critical_drift(self):
        dd = DriftDetector(sample_rate=48000, critical_threshold=2.0)
        # Establish zero baseline
        dd.measure(actual_frames=480000, elapsed_wall_seconds=10.0)
        # 3.0s drift = 144000 frames adjusted
        m = dd.measure(actual_frames=1104000, elapsed_wall_seconds=20.0)
        assert m.level == "critical"

    def test_negative_drift(self):
        dd = DriftDetector(sample_rate=48000, correction_threshold=0.5)
        # Establish zero baseline
        dd.measure(actual_frames=480000, elapsed_wall_seconds=10.0)
        # -1.0s adjusted drift
        m = dd.measure(actual_frames=912000, elapsed_wall_seconds=20.0)
        assert m.level == "correction"
        assert m.adjusted_drift_seconds < 0

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
        m = DriftMeasurement(drift_frames=48000, drift_seconds=1.0, level="correction",
                             adjusted_drift_frames=48000, adjusted_drift_seconds=1.0)

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
        m = DriftMeasurement(drift_frames=100, drift_seconds=0.002, level="acceptable",
                             adjusted_drift_frames=100, adjusted_drift_seconds=0.002)
        assert not dd.correct(m, current_frame=0)
        assert dd.corrections_applied == 0

    def test_no_correction_without_queue(self):
        dd = DriftDetector()
        m = DriftMeasurement(drift_frames=48000, drift_seconds=1.0, level="correction",
                             adjusted_drift_frames=48000, adjusted_drift_seconds=1.0)
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
        # Establish zero baseline
        dd.measure(actual_frames=480000, elapsed_wall_seconds=10.0)
        # 1s adjusted drift
        m = dd.measure_and_correct(
            actual_frames=1008000,
            elapsed_wall_seconds=20.0,
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
        # Establish zero baseline
        dd.measure(actual_frames=480000, elapsed_wall_seconds=10.0)
        # 3s adjusted drift = critical
        m = dd.measure_and_correct(
            actual_frames=1104000,
            elapsed_wall_seconds=20.0,
            current_frame=0,
        )
        assert m.level == "critical"
        assert dd.corrections_applied == 1


class TestBaselineTracking:
    """Baseline tracking: constant startup offset should not trigger corrections."""

    def test_constant_offset_stays_acceptable(self):
        """A constant -0.5s startup offset should be acceptable after baseline is set."""
        dd = DriftDetector(sample_rate=48000, correction_threshold=0.5)
        # First measurement: -0.5s offset — sets baseline
        m1 = dd.measure(actual_frames=456000, elapsed_wall_seconds=10.0)
        # Raw drift = 456000 - 480000 = -24000 frames = -0.5s
        assert m1.drift_frames == -24000
        assert m1.adjusted_drift_frames == 0  # baseline subtracted
        assert m1.level == "acceptable"

        # Second measurement: same constant offset
        m2 = dd.measure(actual_frames=936000, elapsed_wall_seconds=20.0)
        # Raw drift = 936000 - 960000 = -24000
        assert m2.drift_frames == -24000
        assert m2.adjusted_drift_frames == 0
        assert m2.level == "acceptable"

    def test_growing_drift_detected(self):
        """Drift that grows beyond baseline triggers correction."""
        dd = DriftDetector(sample_rate=48000, correction_threshold=0.5)
        # First measurement: -0.5s offset — sets baseline
        dd.measure(actual_frames=456000, elapsed_wall_seconds=10.0)

        # Second measurement: -1.5s (baseline -0.5, so adjusted = -1.0 → correction)
        m = dd.measure(actual_frames=888000, elapsed_wall_seconds=20.0)
        # Raw = 888000 - 960000 = -72000 frames = -1.5s
        assert m.drift_frames == -72000
        # Adjusted = -72000 - (-24000) = -48000 = -1.0s
        assert m.adjusted_drift_frames == -48000
        assert m.adjusted_drift_seconds == pytest.approx(-1.0)
        assert m.level == "correction"

    def test_correction_uses_adjusted_offset(self):
        """Corrections should use adjusted drift, not raw drift."""
        q = BoundaryQueue(safety_margin_frames=100)
        q.add(BoundaryEntry(frame_position=500000, segment_index=0))

        dd = DriftDetector(sample_rate=48000, boundary_queue=q, correction_threshold=0.5)
        # Set baseline at -0.5s
        dd.measure(actual_frames=456000, elapsed_wall_seconds=10.0)

        # Growing drift: -1.5s raw, -1.0s adjusted → correction
        m = dd.measure(actual_frames=888000, elapsed_wall_seconds=20.0)
        dd.correct(m, current_frame=0)

        entries = q.entries()
        # Boundary adjusted by -(-48000) = +48000 (adjusted offset is -48000)
        assert entries[0].frame_position == 548000

    def test_reset_clears_baseline(self):
        """reset_baseline() clears the stored baseline."""
        dd = DriftDetector(sample_rate=48000, correction_threshold=0.5)
        # Set baseline at -0.5s
        dd.measure(actual_frames=456000, elapsed_wall_seconds=10.0)
        assert dd._baseline_drift_frames == -24000

        dd.reset_baseline()
        assert dd._baseline_drift_frames is None

        # Next measurement establishes new baseline
        m = dd.measure(actual_frames=912000, elapsed_wall_seconds=20.0)
        # Raw = 912000 - 960000 = -48000, new baseline = -48000, adjusted = 0
        assert m.adjusted_drift_frames == 0
        assert m.level == "acceptable"
