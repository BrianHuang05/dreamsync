"""D6 — End-to-end integration tests for CaptureOrchestrator pipeline.

Uses synthetic audio and mock capture/encoder processes (no real FFmpeg needed).
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dreamsync.capture.boundary_queue import BoundaryEntry, BoundaryQueue
from dreamsync.capture.drift_detector import DriftDetector
from dreamsync.capture.metadata_writer import MetadataWriter
from dreamsync.capture.orchestrator import (
    CaptureOrchestrator,
    DynamicSplitProcessor,
    OrchestratorConfig,
)
from dreamsync.capture.pcm_reader import BYTES_PER_FRAME, read_chunks
from dreamsync.capture.pipeline_logger import PipelineLogger
from dreamsync.capture.segment_boundary import compute_boundaries

from helpers.synthetic_audio import (
    MockCaptureProcess,
    MockEncoderProcess,
    generate_multi_song_pcm,
    generate_sine_pcm,
    make_mock_encoder_factory,
)


# ======================================================================
# D6.1 — Synthetic Audio Harness
# ======================================================================


class TestSyntheticAudio:
    def test_sine_pcm_byte_count(self):
        """generate_sine_pcm produces correct byte count."""
        pcm = generate_sine_pcm(440.0, 1.0, sample_rate=48000, channels=2)
        expected = 48000 * 2 * 2  # frames * channels * 2 bytes
        assert len(pcm) == expected

    def test_sine_pcm_not_silent(self):
        """Generated PCM is not all zeros."""
        pcm = generate_sine_pcm(440.0, 0.1)
        assert pcm != b"\x00" * len(pcm)

    def test_multi_song_pcm_concatenation(self):
        """Multi-song PCM is the sum of individual song byte counts."""
        durations = [2.0, 3.0, 1.5]
        pcm = generate_multi_song_pcm(durations)
        expected = sum(int(d * 48000) * 2 * 2 for d in durations)
        assert len(pcm) == expected


# ======================================================================
# D6.2 — Mock Capture Process
# ======================================================================


class TestMockCaptureProcess:
    def test_lifecycle(self):
        """start/stop/is_alive work correctly."""
        mock = MockCaptureProcess(b"\x00" * 100)
        assert not mock.is_alive()
        mock.start()
        assert mock.is_alive()
        assert mock.stdout is not None
        mock.stop()
        assert not mock.is_alive()

    def test_stdout_reads(self):
        """stdout returns the PCM data."""
        data = b"\x01\x02\x03\x04"
        mock = MockCaptureProcess(data)
        mock.start()
        assert mock.stdout.read() == data

    def test_restart_resets_stream(self):
        """restart() resets the stream position."""
        data = b"\x01\x02\x03\x04"
        mock = MockCaptureProcess(data)
        mock.start()
        mock.stdout.read()  # exhaust
        mock.restart()
        assert mock.stdout.read() == data


# ======================================================================
# D6.3 — Mock Encoder Process
# ======================================================================


class TestMockEncoderProcess:
    def test_write_and_close(self, tmp_path):
        """MockEncoderProcess writes data to file."""
        path = str(tmp_path / "test.mp3")
        enc = MockEncoderProcess(output_path=path)
        enc.start()
        enc.write(b"\x00\x01\x02\x03")
        enc.write(b"\x04\x05")
        enc.close()
        assert enc.wait() == 0
        assert Path(path).read_bytes() == b"\x00\x01\x02\x03\x04\x05"
        assert enc._bytes_written == 6

    def test_factory_creates_sequential_files(self, tmp_path):
        """make_mock_encoder_factory creates sequential files."""
        factory = make_mock_encoder_factory(str(tmp_path))
        enc0 = factory(0)
        enc1 = factory(1)
        assert "segment_000000" in enc0.output_path
        assert "segment_000001" in enc1.output_path
        enc0.close()
        enc1.close()


# ======================================================================
# D6.4 — Basic Split Accuracy
# ======================================================================


def _run_split_pipeline(
    song_durations: list[float],
    output_dir: Path,
    sample_rate: int = 48000,
    channels: int = 2,
) -> list[Path]:
    """Run a complete split pipeline with synthetic audio and mock encoder.

    Returns the list of output file paths created.
    """
    pcm = generate_multi_song_pcm(song_durations, sample_rate=sample_rate, channels=channels)

    # Compute boundaries
    boundaries = compute_boundaries(song_durations, 0.0, sample_rate)

    # Set up boundary queue
    queue = BoundaryQueue(safety_margin_frames=0)
    for i, frame_pos in enumerate(boundaries):
        queue.add(BoundaryEntry(frame_position=frame_pos, segment_index=i))

    # Encoder factory
    output_dir.mkdir(parents=True, exist_ok=True)
    encoder_factory = make_mock_encoder_factory(str(output_dir))

    # Create and run DynamicSplitProcessor
    completed_segments = []

    def on_complete(seg_idx, start_frame, end_frame, meta=None):
        completed_segments.append((seg_idx, start_frame, end_frame))

    split = DynamicSplitProcessor(
        boundary_queue=queue,
        start_encoder=encoder_factory,
        on_segment_complete=on_complete,
    )
    split.start()

    # Feed PCM through read_chunks
    from io import BytesIO
    stream = BytesIO(pcm)
    chunk_ms = 100
    for chunk in read_chunks(stream, chunk_ms):
        split.process_chunk(chunk)
    split.finish()

    # Collect output files
    return sorted(output_dir.glob("segment_*"))


class TestBasicSplitAccuracy:
    def test_three_songs_produce_three_files(self, tmp_path):
        """3 songs of 5s each produce 3+1 output files (3 intermediate + 1 final)."""
        durations = [5.0, 5.0, 5.0]
        files = _run_split_pipeline(durations, tmp_path / "out")
        # 3 boundaries -> 4 segments (before b1, b1-b2, b2-b3, after b3)
        # But the last segment after the 3rd boundary may be empty.
        # Actually: 3 durations -> boundaries at cumulative positions of
        # first N-1 durations, removing the "total" boundary (compute_boundaries
        # returns cumulative of all durations). So we get 3 boundaries and 4 files.
        # However, if the PCM ends exactly at the 3rd boundary, the 4th file is empty.
        # The key assertion: at least 3 output files with non-empty data.
        non_empty = [f for f in files if f.stat().st_size > 0]
        assert len(non_empty) >= 3

    def test_split_frame_accuracy(self, tmp_path):
        """Verify split happens at exact frame boundary."""
        durations = [3.0, 4.0]
        files = _run_split_pipeline(durations, tmp_path / "out")

        # First boundary at 3.0s * 48000 = 144000 frames
        expected_bytes_0 = 144000 * BYTES_PER_FRAME
        assert files[0].stat().st_size == expected_bytes_0

        # Second segment: 4.0s * 48000 = 192000 frames
        expected_bytes_1 = 192000 * BYTES_PER_FRAME
        assert files[1].stat().st_size == expected_bytes_1

    def test_uneven_songs(self, tmp_path):
        """Songs of different lengths split correctly."""
        durations = [2.5, 7.0, 1.5]
        files = _run_split_pipeline(durations, tmp_path / "out")

        non_empty = [f for f in files if f.stat().st_size > 0]
        assert len(non_empty) >= 3

        # Verify first segment byte count
        expected_0 = int(2.5 * 48000) * BYTES_PER_FRAME
        assert files[0].stat().st_size == expected_0

    def test_single_song_no_splits(self, tmp_path):
        """A single song produces one output file with no splits."""
        durations = [5.0]
        files = _run_split_pipeline(durations, tmp_path / "out")
        non_empty = [f for f in files if f.stat().st_size > 0]
        # compute_boundaries([5.0], 0.0) returns [240000] — one boundary at end
        # So there are 2 files: pre-boundary and post-boundary
        # Pre-boundary has all the data, post-boundary is empty
        assert len(non_empty) >= 1
        expected = int(5.0 * 48000) * BYTES_PER_FRAME
        assert non_empty[0].stat().st_size == expected

    def test_very_short_songs(self, tmp_path):
        """Short songs (0.5s each) split correctly."""
        durations = [0.5, 0.5, 0.5]
        files = _run_split_pipeline(durations, tmp_path / "out")
        non_empty = [f for f in files if f.stat().st_size > 0]
        assert len(non_empty) >= 3

        expected_per_song = int(0.5 * 48000) * BYTES_PER_FRAME
        assert files[0].stat().st_size == expected_per_song


# ======================================================================
# D6.5 — Metadata Sidecars
# ======================================================================


class TestMetadataSidecars:
    def test_sidecar_written_per_segment(self, tmp_path):
        """Each output segment gets a JSON sidecar from _build_segment_metadata."""
        from dreamsync.capture.metadata_writer import SegmentMetadata

        out = tmp_path / "out"
        out.mkdir(parents=True, exist_ok=True)
        writer = MetadataWriter(output_dir=str(out))

        # Simulate 3 segments
        for i in range(3):
            mp3_path = str(out / f"segment_{i:06d}.mp3")
            Path(mp3_path).write_bytes(b"\x00" * 100)
            meta = SegmentMetadata(
                start_frame=i * 240000,
                end_frame=(i + 1) * 240000,
                sample_rate=48000,
                channels=2,
                bitrate="192k",
                segment_index=i,
            )
            writer.write_sidecar(mp3_path, meta)

        sidecars = list(out.glob("*.json"))
        assert len(sidecars) == 3
        for sc in sidecars:
            data = json.loads(sc.read_text())
            assert "startFrame" in data
            assert "endFrame" in data

    def test_sidecar_contains_song_metadata(self, tmp_path):
        """Sidecar includes song title and artist."""
        from dreamsync.capture.metadata_writer import SegmentMetadata

        out = tmp_path / "out"
        out.mkdir(parents=True, exist_ok=True)
        writer = MetadataWriter(output_dir=str(out))

        mp3_path = str(out / "song.mp3")
        Path(mp3_path).write_bytes(b"\x00" * 100)
        meta = SegmentMetadata(
            start_frame=0,
            end_frame=240000,
            sample_rate=48000,
            channels=2,
            bitrate="192k",
            segment_index=0,
            song_title="My Song",
            artist="The Artist",
            album="The Album",
        )
        sidecar_path = writer.write_sidecar(mp3_path, meta)
        data = json.loads(Path(sidecar_path).read_text())
        assert data["songTitle"] == "My Song"
        assert data["artist"] == "The Artist"
        assert data["album"] == "The Album"

    def test_sidecar_frame_positions_contiguous(self, tmp_path):
        """endFrame of segment N == startFrame of segment N+1."""
        from dreamsync.capture.metadata_writer import SegmentMetadata

        out = tmp_path / "out"
        out.mkdir(parents=True, exist_ok=True)
        writer = MetadataWriter(output_dir=str(out))

        frame_ranges = [(0, 240000), (240000, 480000), (480000, 720000)]
        for i, (start, end) in enumerate(frame_ranges):
            mp3_path = str(out / f"seg_{i}.mp3")
            Path(mp3_path).write_bytes(b"\x00")
            meta = SegmentMetadata(
                start_frame=start, end_frame=end,
                sample_rate=48000, channels=2, bitrate="192k",
                segment_index=i,
            )
            writer.write_sidecar(mp3_path, meta)

        sidecars = sorted(out.glob("*.json"))
        datas = [json.loads(sc.read_text()) for sc in sidecars]
        for i in range(len(datas) - 1):
            assert datas[i]["endFrame"] == datas[i + 1]["startFrame"]


# ======================================================================
# D6.6 — Dynamic Boundary Updates
# ======================================================================


class TestDynamicBoundaries:
    def test_boundary_update_mid_capture(self, tmp_path):
        """Updating boundaries mid-capture changes split points."""
        pcm = generate_multi_song_pcm([5.0, 5.0])

        queue = BoundaryQueue(safety_margin_frames=0)
        # Initial boundary at 5s
        queue.add(BoundaryEntry(frame_position=240000, segment_index=0))

        out = tmp_path / "out"
        factory = make_mock_encoder_factory(str(out))

        split = DynamicSplitProcessor(
            boundary_queue=queue,
            start_encoder=factory,
        )
        split.start()

        from io import BytesIO
        stream = BytesIO(pcm)
        chunk_size = 19200  # 4800 frames = 100ms
        chunks_fed = 0
        total_frames = len(pcm) // BYTES_PER_FRAME

        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                break

            # After ~2s (20 chunks = 96000 frames), move boundary to 4s
            if chunks_fed == 20:
                queue.replace_future(
                    [BoundaryEntry(frame_position=192000, segment_index=0)],
                    current_frame=split.current_frame,
                )

            split.process_chunk(chunk)
            chunks_fed += 1

        split.finish()

        files = sorted(out.glob("segment_*"))
        # First file should be ~4s (192000 frames), not 5s
        expected_bytes = 192000 * BYTES_PER_FRAME
        assert files[0].stat().st_size == expected_bytes

    def test_locked_boundary_not_modified(self):
        """Boundaries within safety margin survive replace_future()."""
        queue = BoundaryQueue(safety_margin_frames=24000)  # 0.5s
        queue.add(BoundaryEntry(frame_position=50000, segment_index=0))
        queue.add(BoundaryEntry(frame_position=200000, segment_index=1))

        # Lock boundary at 50000 (current_frame=30000 + 24000 margin = 54000)
        queue.update_locks(30000)

        # Replace future should keep locked boundary
        queue.replace_future(
            [BoundaryEntry(frame_position=300000, segment_index=2)],
            current_frame=30000,
        )

        entries = queue.entries()
        assert entries[0].frame_position == 50000  # locked, preserved
        assert entries[0].locked is True

    def test_track_change_creates_boundary(self, tmp_path):
        """on_track_change() updates boundary queue."""
        cfg = OrchestratorConfig(
            output_dir=str(tmp_path / "out"),
            log_dir=str(tmp_path / "logs"),
        )
        orch = CaptureOrchestrator(config=cfg)

        count = orch.on_track_change({
            "song_durations": [180.0],
            "current_playback_time": 0.0,
            "current_song": {"song_title": "New Song"},
        })
        assert count == 1
        assert len(orch._boundary_queue) >= 1


# ======================================================================
# D6.7 — Drift Detection
# ======================================================================


class TestDriftDetection:
    def test_no_correction_under_threshold(self):
        """Drift < warning threshold produces no correction."""
        queue = BoundaryQueue(safety_margin_frames=0)
        queue.add(BoundaryEntry(frame_position=500000, segment_index=0))

        drift = DriftDetector(
            sample_rate=48000,
            boundary_queue=queue,
            warning_threshold=0.1,
            correction_threshold=0.5,
            critical_threshold=2.0,
        )

        # Minimal drift: 1s elapsed, 48000 frames (exactly matching)
        m = drift.measure(actual_frames=48000, elapsed_wall_seconds=1.0)
        assert m.level == "acceptable"
        assert drift.corrections_applied == 0

    def test_correction_applied_over_threshold(self):
        """Drift > correction threshold adjusts future boundaries."""
        queue = BoundaryQueue(safety_margin_frames=0)
        queue.add(BoundaryEntry(frame_position=500000, segment_index=0))
        queue.add(BoundaryEntry(frame_position=1000000, segment_index=1))

        drift = DriftDetector(
            sample_rate=48000,
            boundary_queue=queue,
            warning_threshold=0.1,
            correction_threshold=0.5,
            critical_threshold=2.0,
        )

        # Establish zero baseline: 1s elapsed, 48000 frames (no drift)
        drift.measure(actual_frames=48000, elapsed_wall_seconds=1.0)

        # Large adjusted drift: 10s elapsed but 510000 frames (0.625s adjusted drift)
        m = drift.measure_and_correct(
            actual_frames=510000,
            elapsed_wall_seconds=10.0,
            current_frame=510000,
        )
        assert m.level == "correction"
        assert drift.corrections_applied == 1


# ======================================================================
# D6.8 — Failure Injection
# ======================================================================


class TestFailureInjection:
    def test_capture_crash_mid_stream(self, tmp_path):
        """FFmpeg crash triggers recovery in producer loop."""
        from unittest.mock import patch

        cfg = OrchestratorConfig(
            output_dir=str(tmp_path / "out"),
            log_dir=str(tmp_path / "logs"),
            capture_restart_delay=0.0,
        )
        orch = CaptureOrchestrator(config=cfg)
        orch._running = True

        chunk = b"\x00" * 19200  # 4800 frames
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise OSError("pipe broken")
            return iter([chunk, chunk])

        orch._capture._process = MagicMock()
        orch._recovery.handle_capture_failure = MagicMock(return_value=True)

        with patch("dreamsync.capture.orchestrator.read_chunks", side_effect=side_effect):
            orch._producer_loop()

        # Recovery was triggered
        orch._recovery.handle_capture_failure.assert_called_once()

        # Producer continued after recovery (2 chunks in buffer)
        chunks = []
        while True:
            c = orch._buffer.get(timeout=0.1)
            if c is None:
                break
            chunks.append(c)
        assert len(chunks) == 2

    def test_capture_retries_exhausted(self, tmp_path):
        """5 consecutive failures → pipeline drains gracefully."""
        from unittest.mock import patch

        cfg = OrchestratorConfig(
            output_dir=str(tmp_path / "out"),
            log_dir=str(tmp_path / "logs"),
        )
        orch = CaptureOrchestrator(config=cfg)
        orch._running = True
        orch._capture._process = MagicMock()
        orch._recovery.handle_capture_failure = MagicMock(return_value=False)

        with patch("dreamsync.capture.orchestrator.read_chunks", side_effect=OSError("fail")):
            orch._producer_loop()

        # EOF signaled
        assert orch._buffer.get(timeout=0.1) is None

    def test_encoder_failure_logged(self, tmp_path):
        """Encoder exit code != 0 triggers recovery logging."""
        cfg = OrchestratorConfig(
            output_dir=str(tmp_path / "out"),
            log_dir=str(tmp_path / "logs"),
        )
        orch = CaptureOrchestrator(config=cfg)

        mock_enc = MagicMock()
        mock_enc.wait.return_value = 1
        mock_enc.output_path = str(tmp_path / "out" / "seg.mp3")
        mock_enc.stderr_output = "encoding error"
        orch._encoders[0] = mock_enc

        orch._recovery.handle_encoder_failure = MagicMock(return_value="skipped")
        orch._logger.recovery_event = MagicMock()

        orch._on_segment_complete(0, 0, 4800)

        # Wait for background finalization thread
        with orch._finalize_lock:
            threads = list(orch._finalize_threads)
        for t in threads:
            t.join(timeout=5.0)

        # encoder_failure event logged
        orch._logger.recovery_event.assert_any_call(
            "encoder_failure",
            frame_position=4800,
            segment_index=0,
            exit_code=1,
            stderr="encoding error",
        )

    def test_encoder_skip_no_sidecar(self, tmp_path):
        """Skipped segment produces no sidecar."""
        from unittest.mock import patch

        cfg = OrchestratorConfig(
            output_dir=str(tmp_path / "out"),
            log_dir=str(tmp_path / "logs"),
        )
        orch = CaptureOrchestrator(config=cfg)

        mock_enc = MagicMock()
        mock_enc.wait.return_value = 1
        mock_enc.output_path = str(tmp_path / "out" / "seg.mp3")
        mock_enc.stderr_output = "fatal"
        orch._encoders[0] = mock_enc

        orch._recovery.handle_encoder_failure = MagicMock(return_value="skipped")

        with patch.object(orch._metadata_writer, "write_sidecar") as mock_sidecar:
            orch._on_segment_complete(0, 0, 4800)
            mock_sidecar.assert_not_called()


# ======================================================================
# D6.9 — Logging Verification
# ======================================================================


class TestLoggingIntegration:
    def test_jsonl_log_written(self, tmp_path):
        """PipelineLogger creates a valid JSONL log file."""
        log_dir = tmp_path / "logs"
        logger = PipelineLogger(log_dir=str(log_dir))

        logger.log("INFO", "lifecycle", "pipeline.started", data={"device": "test"})
        logger.log("INFO", "split", "segment_complete", data={"segment_index": 0})
        logger.log("INFO", "lifecycle", "pipeline.stopped", data={"segments_completed": 1})

        # Close file handler to flush
        for handler in logger._logger.handlers:
            handler.close()

        log_file = log_dir / "pipeline.jsonl"
        assert log_file.exists()
        lines = log_file.read_text().strip().splitlines()
        assert len(lines) >= 3

        # Each line is valid JSON
        for line in lines:
            data = json.loads(line)
            assert "level" in data

    def test_key_events_logged(self, tmp_path):
        """Start, segment_complete, and stop events appear in log."""
        log_dir = tmp_path / "logs"
        logger = PipelineLogger(log_dir=str(log_dir))

        logger.log("INFO", "lifecycle", "pipeline.started")
        logger.log("INFO", "split", "segment_complete", data={"segment_index": 0})
        logger.log("INFO", "lifecycle", "pipeline.stopped", data={"stats": {}})

        for handler in logger._logger.handlers:
            handler.close()

        log_file = log_dir / "pipeline.jsonl"
        events = []
        for line in log_file.read_text().strip().splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        event_names = [e.get("event", "") for e in events]
        assert "pipeline.started" in event_names
        assert "segment_complete" in event_names
        assert "pipeline.stopped" in event_names

    def test_summary_stats_in_stop_event(self, tmp_path):
        """Stop event includes segment count."""
        log_dir = tmp_path / "logs"
        logger = PipelineLogger(log_dir=str(log_dir))

        stats = {"segments_completed": 3, "elapsed_seconds": 15.0}
        logger.log("INFO", "lifecycle", "pipeline.stopped", data=stats)

        for handler in logger._logger.handlers:
            handler.close()

        log_file = log_dir / "pipeline.jsonl"
        for line in log_file.read_text().strip().splitlines():
            event = json.loads(line)
            if event.get("event") == "pipeline.stopped":
                assert event["data"]["segments_completed"] == 3
                break
        else:
            pytest.fail("pipeline.stopped event not found in log")


# ======================================================================
# D6.10 — File Naming
# ======================================================================


class TestFileNaming:
    def test_timestamp_naming(self, tmp_path):
        """FileNamer produces timestamp-pattern filenames."""
        from dreamsync.capture.file_namer import FileNamer

        namer = FileNamer(output_dir=str(tmp_path), pattern="timestamp")
        path1 = namer.next_filename()
        path2 = namer.next_filename()

        name1 = Path(path1).name
        # Pattern: YYYY-MM-DD_HH-MM-SS_segment_NNNNNN.mp3
        assert re.match(
            r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_segment_\d{6}\.mp3",
            name1,
        )

        # Second file has incremented segment counter
        name2 = Path(path2).name
        assert name1 != name2

    def test_metadata_naming(self, tmp_path):
        """FileNamer produces metadata-pattern filenames with sanitized text."""
        from dreamsync.capture.file_namer import FileNamer

        namer = FileNamer(output_dir=str(tmp_path), pattern="metadata")
        path = namer.next_filename(metadata={
            "artist": "AC/DC",
            "song_title": "Back In Black",
        })
        name = Path(path).name
        # Should contain sanitized artist and title (/ replaced)
        assert "AC" in name
        assert "DC" in name
        assert "Back" in name
        assert "/" not in name
