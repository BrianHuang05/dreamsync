"""Tests for dreamsync.capture.recovery_manager — failure handling & recovery."""

from unittest.mock import MagicMock, patch

import pytest

from dreamsync.capture.recovery_manager import RecoveryConfig, RecoveryManager, GapRecord


class MockCapture:
    """Simulates CaptureProcessManager for testing."""

    def __init__(self, fail_on_start=False):
        self._fail_on_start = fail_on_start
        self.started = 0
        self.stopped = 0

    def start(self, device_name=None):
        if self._fail_on_start:
            raise RuntimeError("Device unavailable")
        self.started += 1

    def stop(self):
        self.stopped += 1

    def is_alive(self):
        return self.started > self.stopped


class TestCaptureRecovery:
    def test_successful_restart(self):
        cap = MockCapture()
        rm = RecoveryManager(
            capture_manager=cap,
            config=RecoveryConfig(capture_restart_delay=0.0),
        )
        ok = rm.handle_capture_failure(frame_position=100000)
        assert ok
        assert cap.started == 1
        assert cap.stopped == 1
        assert rm.total_capture_restarts == 1
        assert len(rm.gaps) == 1
        assert rm.gaps[0].start_frame == 100000

    def test_max_retries_exhausted(self):
        cap = MockCapture(fail_on_start=True)
        rm = RecoveryManager(
            capture_manager=cap,
            config=RecoveryConfig(
                capture_restart_delay=0.0,
                max_capture_retries=2,
            ),
        )
        ok = rm.handle_capture_failure(frame_position=0)
        assert not ok

    def test_reset_failure_count(self):
        cap = MockCapture()
        rm = RecoveryManager(
            capture_manager=cap,
            config=RecoveryConfig(capture_restart_delay=0.0),
        )
        rm.handle_capture_failure(frame_position=0)
        rm.reset_capture_failure_count()
        # Should be able to retry again after reset
        ok = rm.handle_capture_failure(frame_position=1000)
        assert ok

    def test_gap_recorded(self):
        cap = MockCapture()
        rm = RecoveryManager(
            capture_manager=cap,
            config=RecoveryConfig(capture_restart_delay=0.0),
        )
        rm.handle_capture_failure(frame_position=50000)
        assert len(rm.gaps) == 1
        gap = rm.gaps[0]
        assert gap.start_frame == 50000


class TestEncoderRecovery:
    def test_retry_succeeds(self):
        rm = RecoveryManager(config=RecoveryConfig(encoder_retry_count=1))
        mock_encoder = MagicMock()

        result = rm.handle_encoder_failure(
            segment_info={"segment_index": 3},
            start_encoder_fn=lambda info: mock_encoder,
        )
        assert result == "retried"
        assert rm.total_encoder_failures == 1

    def test_fallback_to_raw(self, tmp_path):
        rm = RecoveryManager(config=RecoveryConfig(encoder_retry_count=1))
        raw_path = str(tmp_path / "segment.mp3")

        def fail_encoder(info):
            raise RuntimeError("encode failed")

        result = rm.handle_encoder_failure(
            segment_info={
                "segment_index": 1,
                "output_path": raw_path,
                "pcm_data": b"\x00" * 1000,
            },
            start_encoder_fn=fail_encoder,
        )
        assert result == "fallback"

        expected_raw = raw_path.replace(".mp3", ".raw")
        with open(expected_raw, "rb") as f:
            assert len(f.read()) == 1000

    def test_skip_when_no_data(self):
        rm = RecoveryManager(config=RecoveryConfig(encoder_retry_count=1))

        def fail_encoder(info):
            raise RuntimeError("encode failed")

        result = rm.handle_encoder_failure(
            segment_info={"segment_index": 5},
            start_encoder_fn=fail_encoder,
        )
        assert result == "skipped"

    def test_skip_without_encoder_fn(self):
        rm = RecoveryManager(config=RecoveryConfig(encoder_retry_count=0))
        result = rm.handle_encoder_failure(
            segment_info={"segment_index": 0},
        )
        assert result == "skipped"


class TestConfig:
    def test_defaults(self):
        cfg = RecoveryConfig()
        assert cfg.capture_restart_delay == 0.2
        assert cfg.max_capture_retries == 5
        assert cfg.encoder_retry_count == 1
        assert cfg.gap_strategy == "leave"

    def test_custom(self):
        cfg = RecoveryConfig(
            capture_restart_delay=0.5,
            max_capture_retries=10,
        )
        assert cfg.capture_restart_delay == 0.5
        assert cfg.max_capture_retries == 10
