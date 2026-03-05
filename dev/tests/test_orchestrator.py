"""Tests for dreamsync.capture.orchestrator — D1/D2/D3."""

import io
import re
import threading
import time
from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock, patch

import pytest

from dreamsync.capture.boundary_queue import BoundaryEntry, BoundaryQueue
from dreamsync.capture.capture_process import CaptureConfig, CaptureProcessManager
from dreamsync.capture.drift_detector import DriftDetector
from dreamsync.capture.encoder_process import EncoderProcess
from dreamsync.capture.file_namer import FileNamer
from dreamsync.capture.metadata_writer import MetadataWriter, SegmentMetadata
from dreamsync.capture.orchestrator import (
    DRIFT_CHECK_INTERVAL_CHUNKS,
    CaptureOrchestrator,
    DynamicSplitProcessor,
    OrchestratorConfig,
)
from dreamsync.capture.pcm_buffer import AudioBuffer
from dreamsync.capture.pcm_reader import BYTES_PER_FRAME, chunk_bytes_for_ms
from dreamsync.capture.pipeline_logger import PipelineLogger
from dreamsync.capture.recovery_manager import GapRecord, RecoveryConfig, RecoveryManager
from dreamsync.capture.split_logic import SplitProcessor
from dreamsync.capture.timing_integrator import TimingIntegrator


def _make_orch(tmp_path, **cfg_overrides):
    """Helper to create an orchestrator with temp dirs."""
    defaults = {
        "output_dir": str(tmp_path / "out"),
        "log_dir": str(tmp_path / "logs"),
    }
    defaults.update(cfg_overrides)
    cfg = OrchestratorConfig(**defaults)
    return CaptureOrchestrator(config=cfg)


def _make_orch_with_cb(tmp_path, on_segment_saved=None, **cfg_overrides):
    defaults = {
        "output_dir": str(tmp_path / "out"),
        "log_dir": str(tmp_path / "logs"),
    }
    defaults.update(cfg_overrides)
    cfg = OrchestratorConfig(**defaults)
    return CaptureOrchestrator(config=cfg, on_segment_saved=on_segment_saved)


# ======================================================================
# D1.1 — OrchestratorConfig
# ======================================================================


class TestOrchestratorConfig:
    def test_config_defaults(self):
        cfg = OrchestratorConfig()
        assert cfg.sample_rate == 48000
        assert cfg.channels == 2
        assert cfg.device_pattern == "CABLE Output"
        assert cfg.chunk_ms == 100
        assert cfg.buffer_max_chunks == 10
        assert cfg.bitrate == "192k"
        assert cfg.output_dir == "./captured_songs"
        assert cfg.naming == "timestamp"
        assert cfg.log_dir == "./logs"
        assert cfg.safety_margin_frames == 24_000
        assert cfg.timing_refresh_interval == 5.0
        assert cfg.drift_warning_threshold == 0.1
        assert cfg.drift_correction_threshold == 0.5
        assert cfg.drift_critical_threshold == 2.0
        assert cfg.capture_restart_delay == 0.2
        assert cfg.max_capture_retries == 5
        assert cfg.encoder_retry_count == 1

    def test_config_frozen(self):
        cfg = OrchestratorConfig()
        with pytest.raises(FrozenInstanceError):
            cfg.sample_rate = 44100  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            cfg.bitrate = "320k"  # type: ignore[misc]

    def test_config_custom_values(self):
        cfg = OrchestratorConfig(
            sample_rate=44100, channels=1, bitrate="320k", output_dir="/tmp/test",
        )
        assert cfg.sample_rate == 44100
        assert cfg.channels == 1
        assert cfg.bitrate == "320k"
        assert cfg.output_dir == "/tmp/test"
        assert cfg.chunk_ms == 100

    def test_config_creates_capture_config(self):
        cfg = OrchestratorConfig()
        cc = cfg.to_capture_config()
        assert isinstance(cc, CaptureConfig)
        assert cc.sample_rate == 48000
        assert cc.channels == 2
        assert cc.device_pattern == "CABLE Output"
        assert OrchestratorConfig(sample_rate=44100).to_capture_config().sample_rate == 44100

    def test_config_creates_recovery_config(self):
        cfg = OrchestratorConfig()
        rc = cfg.to_recovery_config()
        assert isinstance(rc, RecoveryConfig)
        assert rc.capture_restart_delay == 0.2
        assert rc.max_capture_retries == 5
        assert rc.encoder_retry_count == 1
        assert OrchestratorConfig(max_capture_retries=10).to_recovery_config().max_capture_retries == 10


# ======================================================================
# D1.2 — CaptureOrchestrator class
# ======================================================================


class TestCaptureOrchestrator:
    def test_instantiation(self, tmp_path):
        orch = _make_orch(tmp_path)
        assert isinstance(orch, CaptureOrchestrator)

    def test_instantiation_custom_config(self, tmp_path):
        orch = _make_orch(tmp_path, sample_rate=44100, bitrate="320k")
        assert orch._config.sample_rate == 44100
        assert orch._config.bitrate == "320k"
        assert orch._capture.config.sample_rate == 44100

    def test_no_side_effects(self, tmp_path):
        thread_count_before = threading.active_count()
        orch = _make_orch(tmp_path)
        assert orch._running is False
        assert orch._producer_thread is None
        assert orch._consumer_thread is None
        assert orch._start_time is None
        assert threading.active_count() == thread_count_before

    def test_modules_instantiated(self, tmp_path):
        orch = _make_orch(tmp_path)
        assert isinstance(orch._logger, PipelineLogger)
        assert isinstance(orch._capture, CaptureProcessManager)
        assert isinstance(orch._buffer, AudioBuffer)
        assert isinstance(orch._boundary_queue, BoundaryQueue)
        assert isinstance(orch._file_namer, FileNamer)
        assert isinstance(orch._metadata_writer, MetadataWriter)
        assert isinstance(orch._drift, DriftDetector)
        assert isinstance(orch._timing, TimingIntegrator)
        assert isinstance(orch._recovery, RecoveryManager)

    def test_on_segment_saved_stored(self, tmp_path):
        cb = lambda path, meta: None
        orch = _make_orch_with_cb(tmp_path, on_segment_saved=cb)
        assert orch._on_segment_saved is cb
        assert _make_orch(tmp_path)._on_segment_saved is None


# ======================================================================
# D1.3 — Lifecycle methods
# ======================================================================


class TestLifecycle:
    def test_lifecycle_not_started(self, tmp_path):
        orch = _make_orch(tmp_path)
        orch.stop()
        assert orch._running is False

    def test_shutdown_idempotent(self, tmp_path):
        orch = _make_orch(tmp_path)
        orch.shutdown()
        orch.shutdown()
        assert orch._running is False

    @patch("dreamsync.capture.capture_process.discover_audio_device")
    @patch("dreamsync.capture.capture_process.subprocess.Popen")
    @patch("dreamsync.capture.encoder_process.subprocess.Popen")
    def test_start_creates_threads(self, mock_enc_popen, mock_cap_popen, mock_discover, tmp_path):
        mock_discover.return_value = "Test Device"
        mock_proc = MagicMock()
        mock_proc.stdout = io.BytesIO(b"\x00" * 19200)
        mock_proc.stderr = io.BytesIO(b"")
        mock_proc.poll.return_value = None
        mock_cap_popen.return_value = mock_proc
        # Mock encoder process
        mock_enc = MagicMock()
        mock_enc.stdin = MagicMock()
        mock_enc.stderr = io.BytesIO(b"")
        mock_enc.poll.return_value = None
        mock_enc_popen.return_value = mock_enc

        orch = _make_orch(tmp_path)
        orch.start()
        assert orch._running is True
        assert orch._producer_thread is not None
        assert orch._consumer_thread is not None
        orch.stop()

    @patch("dreamsync.capture.capture_process.discover_audio_device")
    @patch("dreamsync.capture.capture_process.subprocess.Popen")
    @patch("dreamsync.capture.encoder_process.subprocess.Popen")
    def test_stop_joins_threads(self, mock_enc_popen, mock_cap_popen, mock_discover, tmp_path):
        mock_discover.return_value = "Test Device"
        mock_proc = MagicMock()
        mock_proc.stdout = io.BytesIO(b"\x00" * 19200)
        mock_proc.stderr = io.BytesIO(b"")
        mock_proc.poll.return_value = None
        mock_cap_popen.return_value = mock_proc
        mock_enc = MagicMock()
        mock_enc.stdin = MagicMock()
        mock_enc.stderr = io.BytesIO(b"")
        mock_enc.poll.return_value = None
        mock_enc_popen.return_value = mock_enc

        orch = _make_orch(tmp_path)
        orch.start()
        time.sleep(0.2)
        orch.stop()
        assert orch._producer_thread is None
        assert orch._consumer_thread is None
        assert orch._running is False

    @patch("dreamsync.capture.capture_process.discover_audio_device")
    @patch("dreamsync.capture.capture_process.subprocess.Popen")
    @patch("dreamsync.capture.encoder_process.subprocess.Popen")
    def test_shutdown_catches_exceptions(self, mock_enc_popen, mock_cap_popen, mock_discover, tmp_path):
        mock_discover.return_value = "Test Device"
        mock_proc = MagicMock()
        mock_proc.stdout = io.BytesIO(b"\x00" * 19200)
        mock_proc.stderr = io.BytesIO(b"")
        mock_proc.poll.return_value = None
        mock_proc.terminate.side_effect = RuntimeError("ffmpeg crash")
        mock_cap_popen.return_value = mock_proc
        mock_enc = MagicMock()
        mock_enc.stdin = MagicMock()
        mock_enc.stderr = io.BytesIO(b"")
        mock_enc.poll.return_value = None
        mock_enc_popen.return_value = mock_enc

        orch = _make_orch(tmp_path)
        orch.start()
        time.sleep(0.2)
        orch.shutdown()
        assert orch._running is False


# ======================================================================
# D1.4 — Properties & Status
# ======================================================================


class TestPropertiesStatus:
    def test_is_running_false_before_start(self, tmp_path):
        assert _make_orch(tmp_path).is_running is False

    @patch("dreamsync.capture.capture_process.discover_audio_device")
    @patch("dreamsync.capture.capture_process.subprocess.Popen")
    @patch("dreamsync.capture.encoder_process.subprocess.Popen")
    def test_is_running_true_after_start(self, mock_enc, mock_cap, mock_disc, tmp_path):
        mock_disc.return_value = "Dev"
        p = MagicMock(); p.stdout = io.BytesIO(b"\x00" * 19200); p.stderr = io.BytesIO(b""); p.poll.return_value = None
        mock_cap.return_value = p
        e = MagicMock(); e.stdin = MagicMock(); e.stderr = io.BytesIO(b""); e.poll.return_value = None
        mock_enc.return_value = e
        orch = _make_orch(tmp_path)
        orch.start()
        assert orch.is_running is True
        orch.stop()

    @patch("dreamsync.capture.capture_process.discover_audio_device")
    @patch("dreamsync.capture.capture_process.subprocess.Popen")
    @patch("dreamsync.capture.encoder_process.subprocess.Popen")
    def test_is_running_false_after_stop(self, mock_enc, mock_cap, mock_disc, tmp_path):
        mock_disc.return_value = "Dev"
        p = MagicMock(); p.stdout = io.BytesIO(b"\x00" * 19200); p.stderr = io.BytesIO(b""); p.poll.return_value = None
        mock_cap.return_value = p
        e = MagicMock(); e.stdin = MagicMock(); e.stderr = io.BytesIO(b""); e.poll.return_value = None
        mock_enc.return_value = e
        orch = _make_orch(tmp_path)
        orch.start()
        orch.stop()
        assert orch.is_running is False

    def test_stats_initial(self, tmp_path):
        stats = _make_orch(tmp_path).stats
        assert stats["segments_completed"] == 0
        assert stats["frames_processed"] == 0
        assert stats["elapsed_seconds"] == 0.0
        assert stats["drift_corrections"] == 0
        assert stats["capture_restarts"] == 0
        assert stats["encoder_failures"] == 0
        assert stats["gaps"] == 0

    def test_stats_reflects_module_state(self, tmp_path):
        orch = _make_orch(tmp_path)
        orch._segments_completed = 3
        orch._buffer.put(b"\x00" * 400)  # 100 frames
        orch._start_time = time.monotonic() - 10.0
        stats = orch.stats
        assert stats["segments_completed"] == 3
        assert stats["frames_processed"] == 100
        assert stats["elapsed_seconds"] >= 9.9


# ======================================================================
# D1.5 — Callback wiring
# ======================================================================


class TestCallbackWiring:
    @patch("dreamsync.capture.metadata_writer.MetadataWriter.write_sidecar")
    def test_callback_none_by_default(self, mock_sidecar, tmp_path):
        orch = _make_orch(tmp_path)
        assert orch._on_segment_saved is None
        orch._on_segment_complete(0, 0, 48000)
        assert orch._segments_completed == 1

    def test_callback_stored(self, tmp_path):
        cb = lambda path, meta: None
        orch = _make_orch_with_cb(tmp_path, on_segment_saved=cb)
        assert orch._on_segment_saved is cb

    @patch("dreamsync.capture.metadata_writer.MetadataWriter.write_sidecar")
    def test_callback_receives_path_and_metadata(self, mock_sidecar, tmp_path):
        calls = []
        cb = lambda path, meta: calls.append((path, meta))
        orch = _make_orch_with_cb(tmp_path, on_segment_saved=cb)

        orch._on_segment_complete(0, 0, 48000)
        assert len(calls) == 1
        path, meta = calls[0]
        assert meta["segment_index"] == 0
        assert meta["start_frame"] == 0
        assert meta["end_frame"] == 48000
        assert meta["sample_rate"] == 48000
        assert meta["channels"] == 2
        assert meta["bitrate"] == "192k"

        orch._on_segment_complete(1, 48000, 96000)
        assert len(calls) == 2
        assert calls[1][1]["segment_index"] == 1


# ======================================================================
# D2.1 — Producer thread
# ======================================================================


class TestProducerThread:
    def test_producer_reads_chunks_into_buffer(self, tmp_path):
        orch = _make_orch(tmp_path)
        orch._running = True
        chunk_size = chunk_bytes_for_ms(100)  # 19200
        data = b"\xAB" * (chunk_size * 5)
        orch._capture._process = MagicMock()
        orch._capture._process.stdout = io.BytesIO(data)
        orch._capture._process.poll.return_value = None

        orch._producer_loop()

        chunks = []
        while True:
            c = orch._buffer.get(timeout=0.1)
            if c is None:
                break
            chunks.append(c)
        assert len(chunks) == 5
        for c in chunks:
            assert len(c) == chunk_size

    def test_producer_signals_eof_on_stream_end(self, tmp_path):
        orch = _make_orch(tmp_path)
        orch._running = True
        chunk_size = chunk_bytes_for_ms(100)
        orch._capture._process = MagicMock()
        orch._capture._process.stdout = io.BytesIO(b"\x00" * (chunk_size * 2))
        orch._capture._process.poll.return_value = None

        orch._producer_loop()

        c1 = orch._buffer.get(timeout=0.1)
        c2 = orch._buffer.get(timeout=0.1)
        eof = orch._buffer.get(timeout=0.1)
        assert c1 is not None
        assert c2 is not None
        assert eof is None

    def test_producer_resets_failure_count(self, tmp_path):
        orch = _make_orch(tmp_path)
        orch._running = True
        chunk_size = chunk_bytes_for_ms(100)
        orch._capture._process = MagicMock()
        orch._capture._process.stdout = io.BytesIO(b"\x00" * (chunk_size * 3))
        orch._capture._process.poll.return_value = None
        orch._recovery.reset_capture_failure_count = MagicMock()

        orch._producer_loop()

        assert orch._recovery.reset_capture_failure_count.call_count == 3

    def test_producer_signals_eof_on_exception(self, tmp_path):
        orch = _make_orch(tmp_path)
        orch._running = True
        chunk_size = chunk_bytes_for_ms(100)
        # Stream that raises after 1 chunk
        stream = io.BytesIO(b"\x00" * chunk_size)
        original_read = stream.read

        call_count = [0]

        def failing_read(n):
            call_count[0] += 1
            if call_count[0] > 2:  # After first chunk's reads
                raise OSError("pipe broken")
            return original_read(n)

        stream.read = failing_read
        orch._capture._process = MagicMock()
        orch._capture._process.stdout = stream
        orch._capture._process.poll.return_value = None

        orch._producer_loop()

        # Should still get EOF
        chunks = []
        while True:
            c = orch._buffer.get(timeout=0.1)
            if c is None:
                break
            chunks.append(c)
        # EOF was signaled (we got None)

    def test_producer_empty_stream_signals_eof(self, tmp_path):
        orch = _make_orch(tmp_path)
        orch._running = True
        orch._capture._process = MagicMock()
        orch._capture._process.stdout = io.BytesIO(b"")
        orch._capture._process.poll.return_value = None

        orch._producer_loop()

        assert orch._buffer.get(timeout=0.1) is None

    def test_producer_backpressure(self, tmp_path):
        cfg = OrchestratorConfig(
            output_dir=str(tmp_path / "out"),
            log_dir=str(tmp_path / "logs"),
            buffer_max_chunks=2,
        )
        orch = CaptureOrchestrator(config=cfg)
        chunk_size = chunk_bytes_for_ms(100)

        # Pre-fill buffer
        orch._buffer.put(b"\x00" * chunk_size)
        orch._buffer.put(b"\x00" * chunk_size)

        # Start producer in thread with 1 more chunk
        orch._running = True
        orch._capture._process = MagicMock()
        orch._capture._process.stdout = io.BytesIO(b"\x00" * chunk_size)
        orch._capture._process.poll.return_value = None

        t = threading.Thread(target=orch._producer_loop)
        t.start()

        time.sleep(0.05)
        assert t.is_alive()  # blocked on put

        orch._buffer.get()  # free a slot for the data chunk
        time.sleep(0.05)
        orch._buffer.get()  # free a slot for the EOF sentinel
        t.join(timeout=2.0)
        assert not t.is_alive()


# ======================================================================
# D2.2 — Consumer thread
# ======================================================================


class TestConsumerThread:
    def test_consumer_routes_chunks_to_split(self, tmp_path):
        orch = _make_orch(tmp_path)
        mock_split = MagicMock(spec=SplitProcessor)
        orch._split = mock_split

        chunk = b"\x00" * 19200
        orch._buffer.put(chunk)
        orch._buffer.put(chunk)
        orch._buffer.put(chunk)
        orch._buffer.signal_eof()

        orch._consumer_loop()

        assert mock_split.process_chunk.call_count == 3
        mock_split.finish.assert_called_once()

    def test_eof_triggers_finish(self, tmp_path):
        orch = _make_orch(tmp_path)
        mock_split = MagicMock(spec=SplitProcessor)
        orch._split = mock_split

        orch._buffer.put(b"\x00" * 19200)
        orch._buffer.signal_eof()

        orch._consumer_loop()

        mock_split.process_chunk.assert_called_once()
        mock_split.finish.assert_called_once()

    def test_consumer_processes_all_chunks(self, tmp_path):
        orch = _make_orch(tmp_path, buffer_max_chunks=20)
        mock_split = MagicMock(spec=SplitProcessor)
        orch._split = mock_split

        for _ in range(10):
            orch._buffer.put(b"\x00" * 19200)
        orch._buffer.signal_eof()

        orch._consumer_loop()

        assert mock_split.process_chunk.call_count == 10

    def test_consumer_exits_on_eof(self, tmp_path):
        orch = _make_orch(tmp_path)
        mock_split = MagicMock(spec=SplitProcessor)
        orch._split = mock_split

        orch._buffer.signal_eof()

        t = threading.Thread(target=orch._consumer_loop)
        t.start()
        t.join(timeout=1.0)

        assert not t.is_alive()
        mock_split.finish.assert_called_once()

    def test_consumer_blocks_on_empty_buffer(self, tmp_path):
        orch = _make_orch(tmp_path)
        mock_split = MagicMock(spec=SplitProcessor)
        orch._split = mock_split

        t = threading.Thread(target=orch._consumer_loop)
        t.start()
        time.sleep(0.05)
        assert t.is_alive()

        orch._buffer.put(b"\x00" * 19200)
        orch._buffer.signal_eof()
        t.join(timeout=1.0)
        assert not t.is_alive()
        assert mock_split.process_chunk.call_count == 1


# ======================================================================
# D2.3 — Encoder factory
# ======================================================================


class TestEncoderFactory:
    @patch("dreamsync.capture.encoder_process.subprocess.Popen")
    def test_encoder_factory_creates_encoder(self, mock_popen, tmp_path):
        mock_proc = MagicMock()
        mock_proc.stderr = io.BytesIO(b"")
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        orch = _make_orch(tmp_path)
        encoder = orch._start_encoder(0)

        assert isinstance(encoder, EncoderProcess)
        assert encoder.output_path.endswith(".mp3")
        mock_popen.assert_called_once()

    @patch("dreamsync.capture.encoder_process.subprocess.Popen")
    def test_encoder_factory_uses_file_namer(self, mock_popen, tmp_path):
        mock_proc = MagicMock()
        mock_proc.stderr = io.BytesIO(b"")
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        orch = _make_orch(tmp_path)
        orch._file_namer.next_filename = MagicMock(return_value=str(tmp_path / "out" / "test.mp3"))
        encoder = orch._start_encoder(0)

        orch._file_namer.next_filename.assert_called_once()
        assert encoder.output_path == str(tmp_path / "out" / "test.mp3")

    @patch("dreamsync.capture.encoder_process.subprocess.Popen")
    def test_encoder_factory_stores_encoder(self, mock_popen, tmp_path):
        mock_proc = MagicMock()
        mock_proc.stderr = io.BytesIO(b"")
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        orch = _make_orch(tmp_path)
        e0 = orch._start_encoder(0)
        assert orch._encoders[0] is e0

        e1 = orch._start_encoder(1)
        assert orch._encoders[1] is e1
        assert e0 is not e1

    @patch("dreamsync.capture.encoder_process.subprocess.Popen")
    def test_encoder_factory_passes_audio_config(self, mock_popen, tmp_path):
        mock_proc = MagicMock()
        mock_proc.stderr = io.BytesIO(b"")
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        orch = _make_orch(tmp_path, sample_rate=44100, channels=1, bitrate="128k")
        encoder = orch._start_encoder(0)

        assert encoder._sample_rate == 44100
        assert encoder._channels == 1
        assert encoder._bitrate == "128k"

    @patch("dreamsync.capture.encoder_process.subprocess.Popen")
    def test_encoder_factory_no_metadata(self, mock_popen, tmp_path):
        mock_proc = MagicMock()
        mock_proc.stderr = io.BytesIO(b"")
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        orch = _make_orch(tmp_path)
        # BoundaryQueue is empty — no metadata
        encoder = orch._start_encoder(0)
        assert encoder is not None


# ======================================================================
# D2.4 — Segment completion callback
# ======================================================================


class TestSegmentCompletion:
    @patch("dreamsync.capture.metadata_writer.MetadataWriter.write_sidecar")
    def test_segment_complete_writes_sidecar(self, mock_sidecar, tmp_path):
        orch = _make_orch(tmp_path)
        mock_enc = MagicMock(spec=EncoderProcess)
        mock_enc.wait.return_value = 0
        mock_enc.output_path = str(tmp_path / "out" / "song.mp3")
        orch._encoders[0] = mock_enc

        orch._on_segment_complete(0, 0, 4800)

        mock_sidecar.assert_called_once()
        args = mock_sidecar.call_args
        assert args[0][0] == str(tmp_path / "out" / "song.mp3")
        meta = args[0][1]
        assert isinstance(meta, SegmentMetadata)
        assert meta.start_frame == 0
        assert meta.end_frame == 4800

    @patch("dreamsync.capture.metadata_writer.MetadataWriter.write_sidecar")
    def test_segment_complete_fires_callback(self, mock_sidecar, tmp_path):
        calls = []
        orch = _make_orch_with_cb(tmp_path, on_segment_saved=lambda p, m: calls.append((p, m)))
        mock_enc = MagicMock(spec=EncoderProcess)
        mock_enc.wait.return_value = 0
        mock_enc.output_path = "/tmp/song.mp3"
        orch._encoders[0] = mock_enc

        orch._on_segment_complete(0, 0, 4800)

        assert len(calls) == 1
        path, meta = calls[0]
        assert path == "/tmp/song.mp3"
        assert meta["start_frame"] == 0
        assert meta["end_frame"] == 4800

    @patch("dreamsync.capture.metadata_writer.MetadataWriter.write_sidecar")
    def test_segment_complete_encoder_failure(self, mock_sidecar, tmp_path):
        orch = _make_orch(tmp_path)
        mock_enc = MagicMock(spec=EncoderProcess)
        mock_enc.wait.return_value = 1
        mock_enc.output_path = "/tmp/song.mp3"
        mock_enc.stderr_output = ["error"]
        orch._encoders[0] = mock_enc

        orch._on_segment_complete(0, 0, 4800)

        mock_sidecar.assert_not_called()

    @patch("dreamsync.capture.metadata_writer.MetadataWriter.write_sidecar")
    def test_segment_complete_no_callback(self, mock_sidecar, tmp_path):
        orch = _make_orch(tmp_path)
        mock_enc = MagicMock(spec=EncoderProcess)
        mock_enc.wait.return_value = 0
        mock_enc.output_path = "/tmp/song.mp3"
        orch._encoders[0] = mock_enc

        orch._on_segment_complete(0, 0, 4800)
        # No exception, sidecar still written
        mock_sidecar.assert_called_once()


# ======================================================================
# D2.5 — Encoder lifecycle tracking
# ======================================================================


class TestEncoderLifecycleTracking:
    def test_encoder_dict_empty_initially(self, tmp_path):
        orch = _make_orch(tmp_path)
        assert orch._encoders == {}
        assert len(orch._encoders) == 0

    @patch("dreamsync.capture.encoder_process.subprocess.Popen")
    def test_encoder_stored_on_creation(self, mock_popen, tmp_path):
        mock_proc = MagicMock()
        mock_proc.stderr = io.BytesIO(b"")
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        orch = _make_orch(tmp_path)
        e = orch._start_encoder(0)
        assert 0 in orch._encoders
        assert orch._encoders[0] is e

    @patch("dreamsync.capture.encoder_process.subprocess.Popen")
    def test_multiple_encoders_tracked(self, mock_popen, tmp_path):
        mock_proc = MagicMock()
        mock_proc.stderr = io.BytesIO(b"")
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        orch = _make_orch(tmp_path)
        e0 = orch._start_encoder(0)
        e1 = orch._start_encoder(1)
        e2 = orch._start_encoder(2)

        assert len(orch._encoders) == 3
        assert orch._encoders[0] is e0
        assert orch._encoders[1] is e1
        assert orch._encoders[2] is e2

    @patch("dreamsync.capture.metadata_writer.MetadataWriter.write_sidecar")
    def test_encoder_preserved_after_completion(self, mock_sidecar, tmp_path):
        orch = _make_orch(tmp_path)
        mock_enc = MagicMock(spec=EncoderProcess)
        mock_enc.wait.return_value = 0
        mock_enc.output_path = "/tmp/song.mp3"
        orch._encoders[0] = mock_enc

        orch._on_segment_complete(0, 0, 4800)
        assert 0 in orch._encoders

    def test_encoder_dict_key_error_on_missing(self, tmp_path):
        orch = _make_orch(tmp_path)
        with pytest.raises(KeyError):
            _ = orch._encoders[99]


# ======================================================================
# D2.6 — Build SegmentMetadata
# ======================================================================


class TestBuildSegmentMetadata:
    def test_build_metadata_includes_frame_positions(self, tmp_path):
        orch = _make_orch(tmp_path)
        mock_enc = MagicMock()
        mock_enc.output_path = "/tmp/song.mp3"
        orch._encoders[0] = mock_enc

        meta = orch._build_segment_metadata(0, 0, 230400)
        assert meta.start_frame == 0
        assert meta.end_frame == 230400
        assert meta.segment_duration_frames == 230400
        assert meta.segment_duration_seconds == 4.8

    def test_build_metadata_includes_song_info(self, tmp_path):
        orch = _make_orch(tmp_path)
        mock_enc = MagicMock()
        mock_enc.output_path = "/tmp/song.mp3"
        orch._encoders[0] = mock_enc

        entry = BoundaryEntry(
            frame_position=4800, segment_index=0,
            metadata={"song_title": "Test Song", "artist": "Test Artist", "album": "Test Album"},
        )
        orch._boundary_queue.add(entry)

        meta = orch._build_segment_metadata(0, 0, 4800)
        assert meta.song_title == "Test Song"
        assert meta.artist == "Test Artist"
        assert meta.album == "Test Album"

    def test_build_metadata_includes_gaps(self, tmp_path):
        orch = _make_orch(tmp_path)
        mock_enc = MagicMock()
        mock_enc.output_path = "/tmp/song.mp3"
        orch._encoders[0] = mock_enc

        orch._recovery._gaps.append(GapRecord(start_frame=1000, end_frame=1000, timestamp=1234567890.0))
        orch._recovery._gaps.append(GapRecord(start_frame=5000, end_frame=5000, timestamp=1234567900.0))

        meta = orch._build_segment_metadata(0, 0, 9600)
        assert len(meta.gaps) == 2
        assert meta.gaps[0]["start_frame"] == 1000
        assert meta.gaps[1]["start_frame"] == 5000

    def test_build_metadata_no_song_info(self, tmp_path):
        orch = _make_orch(tmp_path)
        mock_enc = MagicMock()
        mock_enc.output_path = "/tmp/song.mp3"
        orch._encoders[0] = mock_enc

        meta = orch._build_segment_metadata(0, 0, 4800)
        assert meta.song_title is None
        assert meta.artist is None
        assert meta.album is None

    def test_build_metadata_includes_output_file(self, tmp_path):
        orch = _make_orch(tmp_path)
        mock_enc = MagicMock()
        mock_enc.output_path = "/tmp/output/song.mp3"
        orch._encoders[0] = mock_enc

        meta = orch._build_segment_metadata(0, 0, 4800)
        assert meta.output_file == "/tmp/output/song.mp3"

    def test_build_metadata_audio_config(self, tmp_path):
        orch = _make_orch(tmp_path, sample_rate=44100, channels=1, bitrate="128k")
        mock_enc = MagicMock()
        mock_enc.output_path = "/tmp/song.mp3"
        orch._encoders[0] = mock_enc

        meta = orch._build_segment_metadata(0, 0, 4410)
        assert meta.sample_rate == 44100
        assert meta.channels == 1
        assert meta.bitrate == "128k"
        assert meta.segment_duration_seconds == pytest.approx(0.1)

    def test_file_namer_timestamp_pattern(self, tmp_path):
        namer = FileNamer(output_dir=str(tmp_path), pattern="timestamp")
        path = namer.next_filename(None)
        assert re.search(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_segment_\d{6}\.mp3$", path)
        assert str(tmp_path) in path

    def test_file_namer_metadata_pattern(self, tmp_path):
        namer = FileNamer(output_dir=str(tmp_path), pattern="metadata")
        path = namer.next_filename({"artist": "Queen", "song_title": "Bohemian Rhapsody"})
        assert re.search(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_Queen_-_Bohemian Rhapsody\.mp3$", path)


# ======================================================================
# D3.1 — DynamicSplitProcessor
# ======================================================================


def _mock_encoder():
    """Create a mock encoder with write() and close()."""
    enc = MagicMock()
    enc.write = MagicMock()
    enc.close = MagicMock()
    return enc


class TestDynamicSplitProcessor:
    def test_dynamic_split_no_boundaries(self):
        queue = BoundaryQueue()
        encoders = []
        completions = []

        def start_enc(idx):
            enc = _mock_encoder()
            encoders.append((idx, enc))
            return enc

        def on_complete(idx, start, end):
            completions.append((idx, start, end))

        dsp = DynamicSplitProcessor(
            boundary_queue=queue,
            start_encoder=start_enc,
            on_segment_complete=on_complete,
        )
        dsp.start()

        chunk = b"\x00" * (4800 * BYTES_PER_FRAME)
        dsp.process_chunk(chunk)
        dsp.process_chunk(chunk)
        dsp.process_chunk(chunk)

        assert dsp.current_frame == 14400
        assert dsp.segment_index == 0
        assert len(encoders) == 1
        # No rotations — close not called yet
        encoders[0][1].close.assert_not_called()

        dsp.finish()
        encoders[0][1].close.assert_called_once()
        assert completions == [(0, 0, 14400)]

    def test_dynamic_split_at_boundary(self):
        queue = BoundaryQueue()
        queue.add(BoundaryEntry(frame_position=4800, segment_index=0))

        encoders = []
        completions = []

        def start_enc(idx):
            enc = _mock_encoder()
            encoders.append((idx, enc))
            return enc

        def on_complete(idx, start, end):
            completions.append((idx, start, end))

        dsp = DynamicSplitProcessor(queue, start_enc, on_complete)
        dsp.start()

        chunk = b"\xAB" * (4800 * BYTES_PER_FRAME)
        dsp.process_chunk(chunk)

        # Chunk ends exactly at boundary — data written, boundary not yet consumed
        assert len(encoders) == 1
        assert dsp.current_frame == 4800

        # Second chunk triggers rotation (boundary at current_frame, frames_to_boundary=0)
        dsp.process_chunk(chunk)

        assert len(encoders) == 2
        encoders[0][1].close.assert_called_once()
        assert completions == [(0, 0, 4800)]
        assert dsp.segment_index == 1
        assert encoders[1][1].write.call_count >= 1

    def test_dynamic_split_spanning_boundary(self):
        queue = BoundaryQueue()
        queue.add(BoundaryEntry(frame_position=2400, segment_index=0))

        encoders = []
        completions = []

        def start_enc(idx):
            enc = _mock_encoder()
            encoders.append((idx, enc))
            return enc

        def on_complete(idx, start, end):
            completions.append((idx, start, end))

        dsp = DynamicSplitProcessor(queue, start_enc, on_complete)
        dsp.start()

        chunk = b"\x00" * (4800 * BYTES_PER_FRAME)
        dsp.process_chunk(chunk)

        assert len(encoders) == 2
        # Encoder 0 got 2400 frames = 9600 bytes
        first_write = encoders[0][1].write.call_args_list[0][0][0]
        assert len(first_write) == 2400 * BYTES_PER_FRAME
        # Encoder 1 got 2400 frames = 9600 bytes
        second_write = encoders[1][1].write.call_args_list[0][0][0]
        assert len(second_write) == 2400 * BYTES_PER_FRAME
        assert completions == [(0, 0, 2400)]
        assert dsp.current_frame == 4800

    def test_dynamic_split_multiple_boundaries(self):
        queue = BoundaryQueue()
        queue.add(BoundaryEntry(frame_position=1000, segment_index=0))
        queue.add(BoundaryEntry(frame_position=2000, segment_index=1))
        queue.add(BoundaryEntry(frame_position=3000, segment_index=2))

        encoders = []
        completions = []

        def start_enc(idx):
            enc = _mock_encoder()
            encoders.append((idx, enc))
            return enc

        def on_complete(idx, start, end):
            completions.append((idx, start, end))

        dsp = DynamicSplitProcessor(queue, start_enc, on_complete)
        dsp.start()

        chunk = b"\x00" * (4800 * BYTES_PER_FRAME)
        dsp.process_chunk(chunk)

        # 4 encoders: segments 0, 1, 2, 3
        assert len(encoders) == 4
        # Check data sizes
        assert len(encoders[0][1].write.call_args_list[0][0][0]) == 1000 * BYTES_PER_FRAME
        assert len(encoders[1][1].write.call_args_list[0][0][0]) == 1000 * BYTES_PER_FRAME
        assert len(encoders[2][1].write.call_args_list[0][0][0]) == 1000 * BYTES_PER_FRAME
        assert len(encoders[3][1].write.call_args_list[0][0][0]) == 1800 * BYTES_PER_FRAME
        # 3 completions for segments 0, 1, 2
        assert completions == [(0, 0, 1000), (1, 1000, 2000), (2, 2000, 3000)]
        assert dsp.segment_index == 3

    def test_dynamic_split_start_creates_encoder(self):
        queue = BoundaryQueue()
        encoders = []

        def start_enc(idx):
            enc = _mock_encoder()
            encoders.append((idx, enc))
            return enc

        dsp = DynamicSplitProcessor(queue, start_enc)
        dsp.start()

        assert len(encoders) == 1
        assert encoders[0][0] == 0

    def test_dynamic_split_finish_closes_encoder(self):
        queue = BoundaryQueue()
        encoders = []
        completions = []

        def start_enc(idx):
            enc = _mock_encoder()
            encoders.append((idx, enc))
            return enc

        def on_complete(idx, start, end):
            completions.append((idx, start, end))

        dsp = DynamicSplitProcessor(queue, start_enc, on_complete)
        dsp.start()

        chunk = b"\x00" * (4800 * BYTES_PER_FRAME)
        dsp.process_chunk(chunk)

        dsp.finish()
        encoders[0][1].close.assert_called_once()
        assert completions == [(0, 0, 4800)]

        # Idempotent
        dsp.finish()
        encoders[0][1].close.assert_called_once()
        assert len(completions) == 1


# ======================================================================
# D3.3 — External Timing API
# ======================================================================


class TestExternalTimingAPI:
    def test_timing_update_adds_boundaries(self, tmp_path):
        orch = _make_orch(tmp_path)
        timing_data = {
            "song_durations": [180.0, 240.0],
            "current_playback_time": 0.0,
            "current_song": {"song_title": "Test", "artist": "Artist"},
        }
        count = orch.update_timing(timing_data)
        assert count == 2
        assert len(orch._boundary_queue) == 2

        entries = orch._boundary_queue.entries()
        # First boundary at 180s * 48000 = 8_640_000 frames
        assert entries[0].frame_position == 8_640_000
        # Second boundary at (180+240)s * 48000 = 20_160_000 frames
        assert entries[1].frame_position == 20_160_000

    def test_track_change_immediate_refresh(self, tmp_path):
        orch = _make_orch(tmp_path)
        # Initial timing
        initial = {
            "song_durations": [100.0, 200.0, 300.0],
            "current_playback_time": 0.0,
        }
        orch.update_timing(initial)
        assert len(orch._boundary_queue) == 3

        # Track change with new timing
        new_timing = {
            "song_durations": [150.0, 250.0],
            "current_playback_time": 0.0,
        }
        count = orch.on_track_change(new_timing)
        assert count == 2
        # Old unlocked boundaries replaced
        entries = orch._boundary_queue.entries()
        assert entries[0].frame_position == round(150.0 * 48000)
        assert entries[1].frame_position == round((150.0 + 250.0) * 48000)

    def test_start_periodic_timing_delegates(self, tmp_path):
        orch = _make_orch(tmp_path)
        fetch_fn = MagicMock(return_value=None)
        orch.start_periodic_timing(fetch_fn)

        assert orch._timing._running is True
        assert orch._timing._fetch_fn is fetch_fn
        orch._timing.stop()

    def test_update_timing_returns_count(self, tmp_path):
        orch = _make_orch(tmp_path)

        # 0 durations
        assert orch.update_timing({"song_durations": [], "current_playback_time": 0.0}) == 0

        # 1 duration
        assert orch.update_timing({"song_durations": [60.0], "current_playback_time": 0.0}) == 1

        # 5 durations
        assert orch.update_timing({
            "song_durations": [60.0, 120.0, 180.0, 240.0, 300.0],
            "current_playback_time": 0.0,
        }) == 5


# ======================================================================
# D3.5 — Periodic Drift Measurement
# ======================================================================


class TestDriftMeasurement:
    def test_drift_check_acceptable(self, tmp_path):
        orch = _make_orch(tmp_path)
        # Add boundaries to queue
        orch._boundary_queue.add(BoundaryEntry(frame_position=500_000, segment_index=0))
        orch._boundary_queue.add(BoundaryEntry(frame_position=1_000_000, segment_index=1))

        # Simulate matching frame count and wall clock (zero drift)
        orch._start_time = time.monotonic() - 1.0  # 1 second ago
        orch._buffer.put(b"\x00" * (48000 * BYTES_PER_FRAME))  # exactly 48000 frames = 1s

        orch._check_drift()

        assert orch._drift.corrections_applied == 0
        # Boundaries unchanged
        entries = orch._boundary_queue.entries()
        assert entries[0].frame_position == 500_000
        assert entries[1].frame_position == 1_000_000

    def test_drift_check_correction(self, tmp_path):
        orch = _make_orch(tmp_path)
        # Add unlocked boundaries
        orch._boundary_queue.add(BoundaryEntry(frame_position=500_000, segment_index=0))
        orch._boundary_queue.add(BoundaryEntry(frame_position=1_000_000, segment_index=1))

        # Simulate drift > 0.5s: actual frames exceed expected by 30000 frames
        # If elapsed = 10s, expected = 480_000. We put 510_000 frames -> drift = 30_000 frames = 0.625s
        orch._start_time = time.monotonic() - 10.0
        orch._buffer.put(b"\x00" * (510_000 * BYTES_PER_FRAME))

        orch._check_drift()

        assert orch._drift.corrections_applied == 1
        m = orch._drift.last_measurement
        assert m.level == "correction"

    def test_drift_check_interval(self, tmp_path):
        orch = _make_orch(tmp_path, buffer_max_chunks=300)
        mock_split = MagicMock()
        mock_split.process_chunk = MagicMock()
        mock_split.finish = MagicMock()
        orch._split = mock_split
        orch._start_time = time.monotonic()
        orch._check_drift = MagicMock()

        # Put 250 chunks + EOF
        chunk = b"\x00" * (4800 * BYTES_PER_FRAME)
        for _ in range(250):
            orch._buffer.put(chunk)
        orch._buffer.signal_eof()

        orch._consumer_loop()

        # Should be called at chunk 100 and chunk 200
        assert orch._check_drift.call_count == 2

    def test_drift_check_logs_event(self, tmp_path):
        orch = _make_orch(tmp_path)
        orch._start_time = time.monotonic() - 1.0
        orch._buffer.put(b"\x00" * (48000 * BYTES_PER_FRAME))

        orch._logger.timing_event = MagicMock()

        orch._check_drift()

        orch._logger.timing_event.assert_called_once()
        call_kwargs = orch._logger.timing_event.call_args
        assert call_kwargs[0][0] == "drift_check"
        assert "drift_seconds" in call_kwargs[1]
        assert "drift_frames" in call_kwargs[1]
        assert "level" in call_kwargs[1]


# ======================================================================
# D3.6 — BoundaryQueue Lock Updates
# ======================================================================


class TestBoundaryQueueLockUpdates:
    def test_boundary_lock_prevents_modification(self):
        queue = BoundaryQueue(safety_margin_frames=24_000)
        queue.add(BoundaryEntry(frame_position=100_000, segment_index=0))

        # Lock the boundary (threshold = 80_000 + 24_000 = 104_000)
        queue.update_locks(80_000)

        # Replace future — locked boundary should survive
        queue.replace_future(
            [BoundaryEntry(frame_position=200_000, segment_index=1)],
            current_frame=80_000,
        )

        entries = queue.entries()
        assert len(entries) == 2
        assert entries[0].frame_position == 100_000  # preserved
        assert entries[1].frame_position == 200_000  # added

    def test_safety_margin_respected(self):
        queue = BoundaryQueue(safety_margin_frames=24_000)
        queue.add(BoundaryEntry(frame_position=50_000, segment_index=0))
        queue.add(BoundaryEntry(frame_position=100_000, segment_index=1))
        queue.add(BoundaryEntry(frame_position=200_000, segment_index=2))

        # threshold = 60_000 + 24_000 = 84_000
        queue.update_locks(60_000)
        entries = queue.entries()
        assert entries[0].locked is True   # 50_000 <= 84_000
        assert entries[1].locked is False  # 100_000 > 84_000
        assert entries[2].locked is False  # 200_000 > 84_000

        # threshold = 80_000 + 24_000 = 104_000
        queue.update_locks(80_000)
        entries = queue.entries()
        assert entries[0].locked is True   # still locked
        assert entries[1].locked is True   # 100_000 <= 104_000
        assert entries[2].locked is False  # 200_000 > 104_000

    def test_lock_updates_called_in_consumer(self, tmp_path):
        orch = _make_orch(tmp_path, buffer_max_chunks=20)
        mock_split = MagicMock()
        mock_split.process_chunk = MagicMock()
        mock_split.finish = MagicMock()
        orch._split = mock_split

        orch._boundary_queue.update_locks = MagicMock()

        chunk = b"\x00" * (4800 * BYTES_PER_FRAME)
        for _ in range(5):
            orch._buffer.put(chunk)
        orch._buffer.signal_eof()

        orch._consumer_loop()

        assert orch._boundary_queue.update_locks.call_count == 5


# ======================================================================
# D4.1 — RecoveryManager Wiring
# ======================================================================


class TestRecoveryManagerWiring:
    def test_recovery_manager_wired(self, tmp_path):
        orch = _make_orch(tmp_path)
        assert isinstance(orch._recovery, RecoveryManager)

    def test_recovery_config_from_orchestrator(self, tmp_path):
        orch = _make_orch(tmp_path, capture_restart_delay=0.5, max_capture_retries=10, encoder_retry_count=3)
        assert orch._recovery._config.capture_restart_delay == 0.5
        assert orch._recovery._config.max_capture_retries == 10
        assert orch._recovery._config.encoder_retry_count == 3

        # Default config
        orch2 = _make_orch(tmp_path)
        assert orch2._recovery._config.capture_restart_delay == 0.2
        assert orch2._recovery._config.max_capture_retries == 5
        assert orch2._recovery._config.encoder_retry_count == 1

    def test_recovery_shares_capture_manager(self, tmp_path):
        orch = _make_orch(tmp_path)
        assert orch._recovery._capture is orch._capture


# ======================================================================
# D4.2 — Capture Failure Recovery
# ======================================================================


class TestCaptureFailureRecovery:
    @patch("dreamsync.capture.orchestrator.read_chunks")
    def test_capture_failure_triggers_recovery(self, mock_read_chunks, tmp_path):
        orch = _make_orch(tmp_path)
        orch._running = True
        orch._capture._process = MagicMock()
        mock_read_chunks.side_effect = OSError("pipe broken")
        orch._recovery.handle_capture_failure = MagicMock(return_value=False)
        orch._logger.recovery_event = MagicMock()

        orch._producer_loop()

        orch._recovery.handle_capture_failure.assert_called_once()
        orch._logger.recovery_event.assert_any_call(
            "capture_failure",
            frame_position=0,
            error="pipe broken",
            error_type="OSError",
        )

    @patch("dreamsync.capture.orchestrator.read_chunks")
    def test_capture_recovery_restarts_producer(self, mock_read_chunks, tmp_path):
        orch = _make_orch(tmp_path)
        orch._running = True
        orch._capture._process = MagicMock()
        chunk = b"\x00" * 19200  # 4800 frames
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise OSError("pipe broken")
            return iter([chunk, chunk, chunk])

        mock_read_chunks.side_effect = side_effect
        orch._recovery.handle_capture_failure = MagicMock(return_value=True)

        orch._producer_loop()

        # Should have gotten 3 chunks + EOF
        chunks = []
        while True:
            c = orch._buffer.get(timeout=0.1)
            if c is None:
                break
            chunks.append(c)
        assert len(chunks) == 3

    @patch("dreamsync.capture.orchestrator.read_chunks")
    def test_capture_retries_exhausted_signals_eof(self, mock_read_chunks, tmp_path):
        orch = _make_orch(tmp_path)
        orch._running = True
        orch._capture._process = MagicMock()
        mock_read_chunks.side_effect = OSError("pipe broken")
        orch._recovery.handle_capture_failure = MagicMock(return_value=False)

        orch._producer_loop()

        # EOF was signaled
        assert orch._buffer.get(timeout=0.1) is None

    @patch("dreamsync.capture.orchestrator.read_chunks")
    def test_capture_failure_logged(self, mock_read_chunks, tmp_path):
        orch = _make_orch(tmp_path)
        orch._running = True
        orch._capture._process = MagicMock()
        mock_read_chunks.side_effect = OSError("device disconnected")
        orch._recovery.handle_capture_failure = MagicMock(return_value=False)
        orch._logger.recovery_event = MagicMock()
        orch._logger.log = MagicMock()

        orch._producer_loop()

        # capture_failure logged
        orch._logger.recovery_event.assert_called_with(
            "capture_failure",
            frame_position=0,
            error="device disconnected",
            error_type="OSError",
        )
        # Unrecoverable logged
        orch._logger.log.assert_called_with(
            "ERROR", "recovery", "capture_unrecoverable",
            data={"retries_exhausted": True},
            frame_position=0,
        )


# ======================================================================
# D4.3 — Encoder Failure Recovery
# ======================================================================


class TestEncoderFailureRecovery:
    @patch("dreamsync.capture.metadata_writer.MetadataWriter.write_sidecar")
    def test_encoder_failure_retry_succeeds(self, mock_sidecar, tmp_path):
        orch = _make_orch(tmp_path)
        mock_enc = MagicMock(spec=EncoderProcess)
        mock_enc.wait.return_value = 1
        mock_enc.output_path = str(tmp_path / "out" / "song.mp3")
        mock_enc.stderr_output = "error"
        orch._encoders[0] = mock_enc

        orch._recovery.handle_encoder_failure = MagicMock(return_value="retried")
        orch._logger.recovery_event = MagicMock()

        orch._on_segment_complete(0, 0, 4800)

        orch._recovery.handle_encoder_failure.assert_called_once()
        # Sidecar still written for retried segments
        mock_sidecar.assert_called_once()

    @patch("dreamsync.capture.metadata_writer.MetadataWriter.write_sidecar")
    def test_encoder_failure_skip(self, mock_sidecar, tmp_path):
        orch = _make_orch(tmp_path)
        mock_enc = MagicMock(spec=EncoderProcess)
        mock_enc.wait.return_value = 1
        mock_enc.output_path = "/tmp/song.mp3"
        mock_enc.stderr_output = "fatal error"
        orch._encoders[0] = mock_enc

        orch._recovery.handle_encoder_failure = MagicMock(return_value="skipped")

        orch._on_segment_complete(0, 0, 4800)

        # No sidecar for skipped segments
        mock_sidecar.assert_not_called()

    @patch("dreamsync.capture.metadata_writer.MetadataWriter.write_sidecar")
    def test_encoder_failure_logged(self, mock_sidecar, tmp_path):
        orch = _make_orch(tmp_path)
        mock_enc = MagicMock(spec=EncoderProcess)
        mock_enc.wait.return_value = 1
        mock_enc.output_path = "/tmp/song.mp3"
        mock_enc.stderr_output = "error details"
        orch._encoders[0] = mock_enc

        orch._recovery.handle_encoder_failure = MagicMock(return_value="skipped")
        orch._logger.recovery_event = MagicMock()

        orch._on_segment_complete(0, 0, 4800)

        # encoder_failure event
        calls = orch._logger.recovery_event.call_args_list
        assert calls[0] == (
            ("encoder_failure",),
            {"frame_position": 4800, "segment_index": 0, "exit_code": 1, "stderr": "error details"},
        )
        # encoder_recovery_result event
        assert calls[1] == (
            ("encoder_recovery_result",),
            {"frame_position": 4800, "segment_index": 0, "result": "skipped"},
        )


# ======================================================================
# D4.4 — PipelineLogger Wiring
# ======================================================================


class TestPipelineLoggerWiring:
    def test_pipeline_logger_wired(self, tmp_path):
        orch = _make_orch(tmp_path)
        assert isinstance(orch._logger, PipelineLogger)

    def test_logger_log_dir(self, tmp_path):
        log_dir = str(tmp_path / "custom_logs")
        orch = _make_orch(tmp_path, log_dir=log_dir)
        from pathlib import Path
        assert orch._logger._log_dir == Path(log_dir)


# ======================================================================
# D4.7 — Shutdown Logging
# ======================================================================


class TestShutdownLogging:
    def test_shutdown_summary_logged(self, tmp_path):
        orch = _make_orch(tmp_path)
        orch._start_time = time.monotonic() - 5.0
        orch._running = True
        orch._logger.log = MagicMock()

        orch.stop()

        # Find the pipeline.stopped call
        stopped_calls = [
            c for c in orch._logger.log.call_args_list
            if len(c[0]) >= 3 and c[0][2] == "pipeline.stopped"
        ]
        assert len(stopped_calls) == 1
        data = stopped_calls[0][1].get("data") if stopped_calls[0][1] else stopped_calls[0][0][3]
        assert isinstance(data, dict)

    def test_shutdown_log_contains_stats(self, tmp_path):
        orch = _make_orch(tmp_path)
        orch._start_time = time.monotonic() - 10.0
        orch._segments_completed = 2
        orch._buffer.put(b"\x00" * (48000 * BYTES_PER_FRAME))  # 1s of audio

        stats = orch.stats
        assert stats["segments_completed"] == 2
        assert stats["frames_processed"] == 48000
        assert stats["elapsed_seconds"] >= 9.9
        assert stats["drift_corrections"] == 0
        assert stats["capture_restarts"] == 0
        assert stats["encoder_failures"] == 0
        assert stats["gaps"] == 0
