"""Tests for minimum segment duration guard in CaptureOrchestrator."""

import os
import tempfile
import threading
from unittest.mock import MagicMock, patch

import pytest

from dreamsync.capture.orchestrator import CaptureOrchestrator, OrchestratorConfig


class FakeEncoder:
    """Minimal encoder mock for testing discard logic."""

    def __init__(self, output_path=""):
        self._output_path = output_path
        self._waited = False
        self._closed = False

    def write(self, data):
        pass

    def close(self):
        self._closed = True

    def wait(self, timeout=30.0):
        self._waited = True
        return 0

    @property
    def output_path(self):
        return self._output_path

    @property
    def stderr_output(self):
        return []


class TestMinSegmentGuard:
    """_on_segment_complete should discard segments shorter than min_segment_frames."""

    def test_short_segment_discarded(self):
        """Segment with fewer frames than min_segment_frames should be discarded."""
        config = OrchestratorConfig(
            min_segment_frames=220_500,
            output_dir=tempfile.mkdtemp(),
        )
        orch = CaptureOrchestrator(config=config)

        # Track whether finalization thread was spawned
        finalize_threads_before = len(orch._finalize_threads)

        # Insert a fake encoder
        fake_enc = FakeEncoder("/tmp/test_short.mp3")
        orch._encoders[5] = fake_enc

        # Trigger segment complete with only 1000 frames (way below 220500)
        orch._on_segment_complete(
            segment_index=5,
            start_frame=0,
            end_frame=1000,
            popped_meta={"song_title": "Test"},
        )

        # segments_completed should NOT have incremented
        assert orch._segments_completed == 0

        # The encoder should have been popped from _encoders
        assert 5 not in orch._encoders

        # A discard cleanup thread should have been spawned
        with orch._finalize_lock:
            assert len(orch._finalize_threads) > finalize_threads_before

    def test_normal_segment_proceeds(self):
        """Segment with enough frames should proceed to normal finalization."""
        config = OrchestratorConfig(
            min_segment_frames=220_500,
            output_dir=tempfile.mkdtemp(),
        )
        orch = CaptureOrchestrator(config=config)

        fake_enc = FakeEncoder("/tmp/test_normal.mp3")
        orch._encoders[1] = fake_enc

        # Trigger segment complete with 300000 frames (above 220500)
        orch._on_segment_complete(
            segment_index=1,
            start_frame=0,
            end_frame=300_000,
            popped_meta={"song_title": "Real Song"},
        )

        # segments_completed should have incremented
        assert orch._segments_completed == 1

    def test_exact_threshold_proceeds(self):
        """Segment at exactly min_segment_frames should proceed (not <, so equal is fine)."""
        config = OrchestratorConfig(
            min_segment_frames=220_500,
            output_dir=tempfile.mkdtemp(),
        )
        orch = CaptureOrchestrator(config=config)

        fake_enc = FakeEncoder("/tmp/test_exact.mp3")
        orch._encoders[2] = fake_enc

        orch._on_segment_complete(
            segment_index=2,
            start_frame=0,
            end_frame=220_500,
            popped_meta={"song_title": "Border Song"},
        )

        assert orch._segments_completed == 1

    def test_reused_track_segment_is_discarded_before_finalization(self):
        config = OrchestratorConfig(
            min_segment_frames=1,
            output_dir=tempfile.mkdtemp(),
        )
        orch = CaptureOrchestrator(config=config)
        orch.suppress_track("spotify-track")
        orch._encoders[3] = FakeEncoder("/tmp/duplicate.mp3")

        orch._on_segment_complete(
            segment_index=3,
            start_frame=0,
            end_frame=300_000,
            popped_meta={
                "song_title": "Already Captured",
                "spotify_track_id": "spotify-track",
            },
        )

        assert orch._segments_completed == 0
        assert 3 not in orch._encoders

    def test_discard_deletes_temp_file(self):
        """_discard_segment should delete the encoder's temp file."""
        config = OrchestratorConfig(output_dir=tempfile.mkdtemp())
        orch = CaptureOrchestrator(config=config)

        # Create an actual temp file
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp.write(b"fake mp3 data")
        tmp.close()

        fake_enc = FakeEncoder(tmp.name)
        orch._encoders[7] = fake_enc

        orch._discard_segment(7)

        # Wait for cleanup thread
        with orch._finalize_lock:
            threads = list(orch._finalize_threads)
        for t in threads:
            t.join(timeout=5.0)

        assert not os.path.exists(tmp.name)
        assert 7 not in orch._encoders

    def test_discard_no_encoder_noop(self):
        """_discard_segment with no encoder is a no-op."""
        config = OrchestratorConfig(output_dir=tempfile.mkdtemp())
        orch = CaptureOrchestrator(config=config)

        # Should not raise
        orch._discard_segment(999)


class TestMinSegmentIntegration:
    """Integration: rapid double-boundary should produce only one output."""

    def test_rapid_double_boundary_single_output(self):
        """Simulate two boundaries 0.7s apart — only the real segment should be finalized."""
        config = OrchestratorConfig(
            min_segment_frames=220_500,  # 5s at 44.1kHz
            output_dir=tempfile.mkdtemp(),
        )
        orch = CaptureOrchestrator(config=config)

        saved = []
        orch._on_segment_saved = lambda path, meta: saved.append(path)

        # Segment 0: real song (300,000 frames = ~6.8s)
        fake_enc_0 = FakeEncoder("/tmp/real_song.mp3")
        orch._encoders[0] = fake_enc_0
        orch._on_segment_complete(0, 0, 300_000, {"song_title": "Real Song"})

        # Segment 1: residual (32,810 frames = ~0.74s) — should be discarded
        fake_enc_1 = FakeEncoder("/tmp/residual.mp3")
        orch._encoders[1] = fake_enc_1
        orch._on_segment_complete(1, 300_000, 332_810, {"song_title": "Residual"})

        # Wait for finalization threads
        with orch._finalize_lock:
            threads = list(orch._finalize_threads)
        for t in threads:
            t.join(timeout=5.0)

        # Only the real song should have been finalized
        assert orch._segments_completed == 1
        # The residual encoder should have been removed
        assert 1 not in orch._encoders
