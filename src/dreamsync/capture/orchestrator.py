"""Capture pipeline orchestrator — unified config and lifecycle management."""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass
from typing import Callable

from dreamsync.capture.boundary_queue import BoundaryEntry, BoundaryQueue
from dreamsync.capture.capture_process import CaptureConfig, CaptureProcessManager
from dreamsync.capture.drift_detector import DriftDetector
from dreamsync.capture.encoder_process import EncoderProcess
from dreamsync.capture.file_namer import FileNamer
from dreamsync.capture.metadata_writer import MetadataWriter, SegmentMetadata
from dreamsync.capture.pcm_buffer import AudioBuffer
from dreamsync.capture.pcm_reader import BYTES_PER_FRAME, read_chunks
from dreamsync.capture.pipeline_logger import PipelineLogger
from dreamsync.capture.recovery_manager import RecoveryConfig, RecoveryManager
from dreamsync.capture.timing_integrator import TimingIntegrator


@dataclass(frozen=True)
class OrchestratorConfig:
    """Unified configuration for the entire capture pipeline.

    Every field has a default that matches the corresponding sub-module
    default so that ``OrchestratorConfig()`` is immediately usable.
    """

    # --- Capture process (-> CaptureConfig) ---
    sample_rate: int = 44100
    channels: int = 2
    device_pattern: str = "CABLE Output"

    # --- PCM reader ---
    chunk_ms: int = 100

    # --- AudioBuffer ---
    buffer_max_chunks: int = 10

    # --- Encoding (-> EncoderProcess) ---
    bitrate: str = "192k"

    # --- Output paths (-> FileNamer, MetadataWriter) ---
    output_dir: str = "./captured_songs"
    naming: str = "timestamp"  # "timestamp" | "metadata"
    log_dir: str = "./logs"

    # --- Timing (-> BoundaryQueue, TimingIntegrator) ---
    safety_margin_frames: int = 22_050  # 0.5 s at 44.1 kHz
    timing_refresh_interval: float = 5.0

    # --- Drift (-> DriftDetector) ---
    drift_warning_threshold: float = 0.1
    drift_correction_threshold: float = 0.5
    drift_critical_threshold: float = 2.0

    # --- Recovery (-> RecoveryManager) ---
    capture_restart_delay: float = 0.2
    max_capture_retries: int = 5
    encoder_retry_count: int = 1

    def to_capture_config(self) -> CaptureConfig:
        """Derive a CaptureConfig for CaptureProcessManager."""
        return CaptureConfig(
            sample_rate=self.sample_rate,
            channels=self.channels,
            device_pattern=self.device_pattern,
        )

    def to_recovery_config(self) -> RecoveryConfig:
        """Derive a RecoveryConfig for RecoveryManager."""
        return RecoveryConfig(
            capture_restart_delay=self.capture_restart_delay,
            max_capture_retries=self.max_capture_retries,
            encoder_retry_count=self.encoder_retry_count,
        )


DRIFT_CHECK_INTERVAL_CHUNKS = 100  # ~10 seconds at 100 ms chunks


class PcmAccumulator:
    """Duck-types ``EncoderProcess`` to absorb PCM writes while the real
    encoder spawns in a background thread.

    Two modes:

    1. **Buffering** — before ``attach_encoder()`` is called, ``write()``
       appends raw PCM bytes to an internal list.
    2. **Pass-through** — after ``attach_encoder()``, ``write()`` delegates
       directly to the real encoder.

    Thread safety: a ``threading.Lock`` protects the buffer↔encoder swap.
    A ``threading.Event`` gates ``wait()`` and ``output_path`` (only called
    from background finalization, never the consumer thread).
    """

    def __init__(self) -> None:
        self._buffer: list[bytes] = []
        self._encoder: object | None = None
        self._lock = threading.Lock()
        self._attached = threading.Event()
        self._close_requested = False
        self._closed = False

    # -- Consumer-thread API (non-blocking) --------------------------------

    def write(self, data: bytes) -> None:
        """Buffer *data* or pass through to the real encoder."""
        with self._lock:
            if self._encoder is not None:
                self._encoder.write(data)  # type: ignore[union-attr]
            else:
                self._buffer.append(data)

    def close(self) -> None:
        """Signal end-of-input.  Non-blocking in both modes."""
        with self._lock:
            if self._encoder is not None:
                self._encoder.close()  # type: ignore[union-attr]
                self._closed = True
            else:
                self._close_requested = True

    # -- Background-thread API ---------------------------------------------

    def attach_encoder(self, encoder: object) -> None:
        """Drain the buffer into *encoder* and switch to pass-through mode."""
        with self._lock:
            for chunk in self._buffer:
                encoder.write(chunk)  # type: ignore[union-attr]
            self._buffer.clear()
            self._encoder = encoder
            self._attached.set()
            if self._close_requested:
                encoder.close()  # type: ignore[union-attr]
                self._closed = True

    def wait(self, timeout: float = 30.0) -> int:
        """Block until the real encoder is attached, then delegate ``wait``."""
        self._attached.wait(timeout=timeout)
        if self._encoder is None:
            return -1
        return self._encoder.wait(timeout=timeout)  # type: ignore[union-attr]

    @property
    def output_path(self) -> str:
        """Block until the real encoder is attached, then return its path."""
        self._attached.wait()
        if self._encoder is None:
            return ""
        return self._encoder.output_path  # type: ignore[union-attr]

    @property
    def stderr_output(self) -> list[str]:
        if self._encoder is None:
            return []
        return self._encoder.stderr_output  # type: ignore[union-attr]


class DynamicSplitProcessor:
    """SplitProcessor that reads boundaries from a BoundaryQueue.

    Instead of a static list of frame boundaries, this processor checks
    the BoundaryQueue on every chunk for the next split point.  Boundaries
    may be added, replaced, or adjusted by TimingIntegrator and
    DriftDetector while the processor is running.

    Parameters
    ----------
    boundary_queue:
        Thread-safe queue of upcoming segment boundaries.
    start_encoder:
        Callable that receives ``(segment_index,)`` and returns an object
        with ``write(bytes)`` and ``close()`` methods.
    on_segment_complete:
        Optional callback ``(segment_index, start_frame, end_frame)``
        invoked when a segment is finalized.
    """

    def __init__(
        self,
        boundary_queue: BoundaryQueue,
        start_encoder: Callable[[int], object],
        on_segment_complete: Callable[[int, int, int], None] | None = None,
    ) -> None:
        self._queue = boundary_queue
        self._start_encoder = start_encoder
        self._on_segment_complete = on_segment_complete
        self._current_frame: int = 0
        self._segment_index: int = 0
        self._segment_start_frame: int = 0
        self._encoder = None
        self._finished = False

    @property
    def current_frame(self) -> int:
        """Total audio frames processed so far."""
        return self._current_frame

    @property
    def segment_index(self) -> int:
        """Index of the currently active segment (0-based)."""
        return self._segment_index

    def start(self) -> None:
        """Initialize the first encoder (segment 0)."""
        self._encoder = self._start_encoder(self._segment_index)

    def process_chunk(self, chunk: bytes) -> None:
        """Route *chunk* to the correct encoder(s), splitting at boundaries."""
        if self._encoder is None:
            raise RuntimeError("Call start() before processing chunks")

        chunk_frames = len(chunk) // BYTES_PER_FRAME
        offset = 0

        while chunk_frames > 0:
            next_boundary = self._queue.peek_next()

            if next_boundary is None:
                # No upcoming boundaries — write everything to current encoder
                self._encoder.write(chunk[offset:])
                self._current_frame += chunk_frames
                break

            frames_to_boundary = next_boundary.frame_position - self._current_frame

            if frames_to_boundary <= 0:
                # Boundary is at or behind current position — consume and rotate
                self._queue.pop_next()
                self._rotate_encoder()
                continue

            if chunk_frames <= frames_to_boundary:
                # Entire remaining chunk fits before the next boundary
                self._encoder.write(chunk[offset:])
                self._current_frame += chunk_frames
                chunk_frames = 0
            else:
                # Chunk spans the boundary — split at the exact frame
                split_bytes = frames_to_boundary * BYTES_PER_FRAME
                self._encoder.write(chunk[offset:offset + split_bytes])
                self._current_frame += frames_to_boundary
                offset += split_bytes
                chunk_frames -= frames_to_boundary
                self._queue.pop_next()
                self._rotate_encoder()

    def finish(self) -> None:
        """Close the final encoder and fire the segment-complete callback."""
        if self._encoder is not None and not self._finished:
            self._encoder.close()
            if self._on_segment_complete:
                self._on_segment_complete(
                    self._segment_index,
                    self._segment_start_frame,
                    self._current_frame,
                )
            self._finished = True

    def _rotate_encoder(self) -> None:
        """Close the current encoder, fire callback, and start a new one."""
        if self._encoder is not None:
            self._encoder.close()
            if self._on_segment_complete:
                self._on_segment_complete(
                    self._segment_index,
                    self._segment_start_frame,
                    self._current_frame,
                )

        self._segment_index += 1
        self._segment_start_frame = self._current_frame
        self._encoder = self._start_encoder(self._segment_index)


class CaptureOrchestrator:
    """Top-level orchestrator for the audio capture pipeline.

    Aggregates all sub-modules and manages the full capture lifecycle.
    Construction is side-effect-free — no threads or processes are spawned
    until :meth:`start` is called.

    Parameters
    ----------
    config:
        Unified pipeline configuration.  Defaults to ``OrchestratorConfig()``.
    on_segment_saved:
        Optional callback invoked after each segment is finalized.
        Signature: ``(mp3_path: str, metadata: dict) -> None``.
    """

    def __init__(
        self,
        config: OrchestratorConfig | None = None,
        on_segment_saved: Callable[[str, dict], None] | None = None,
    ) -> None:
        self._config = config or OrchestratorConfig()
        self._on_segment_saved = on_segment_saved

        # --- Module instantiation (no side effects) ---

        # Logging — must be first so other modules can log during init
        self._logger = PipelineLogger(log_dir=self._config.log_dir)

        # Capture subprocess manager
        self._capture = CaptureProcessManager(
            config=self._config.to_capture_config(),
        )

        # Thread-safe PCM buffer (producer/consumer bridge)
        self._buffer = AudioBuffer(
            max_chunks=self._config.buffer_max_chunks,
            sample_rate=self._config.sample_rate,
        )

        # Boundary queue for segment split points
        self._boundary_queue = BoundaryQueue(
            safety_margin_frames=self._config.safety_margin_frames,
        )

        # Output file naming
        self._file_namer = FileNamer(
            output_dir=self._config.output_dir,
            pattern=self._config.naming,
        )

        # JSON sidecar writer
        self._metadata_writer = MetadataWriter(
            output_dir=self._config.output_dir,
        )

        # Drift detection and correction
        self._drift = DriftDetector(
            sample_rate=self._config.sample_rate,
            boundary_queue=self._boundary_queue,
            warning_threshold=self._config.drift_warning_threshold,
            correction_threshold=self._config.drift_correction_threshold,
            critical_threshold=self._config.drift_critical_threshold,
        )

        # External timing source integration
        self._timing = TimingIntegrator(
            boundary_queue=self._boundary_queue,
            get_current_frame=lambda: self._buffer.frames_processed,
            sample_rate=self._config.sample_rate,
            refresh_interval=self._config.timing_refresh_interval,
        )

        # Failure recovery
        self._recovery = RecoveryManager(
            capture_manager=self._capture,
            config=self._config.to_recovery_config(),
        )

        # --- Runtime state (set by start/stop) ---
        self._running = False
        self._producer_thread: threading.Thread | None = None
        self._consumer_thread: threading.Thread | None = None
        self._start_time: float | None = None
        self._segments_completed: int = 0
        self._encoders: dict[int, object] = {}
        self._split: DynamicSplitProcessor | None = None
        self._finalize_threads: list[threading.Thread] = []
        self._finalize_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """Whether the capture pipeline is actively running."""
        return self._running

    @property
    def stats(self) -> dict:
        """Return a snapshot of pipeline statistics."""
        elapsed = 0.0
        if self._start_time is not None:
            elapsed = time.monotonic() - self._start_time

        return {
            "segments_completed": self._segments_completed,
            "frames_processed": self._buffer.frames_processed,
            "elapsed_seconds": round(elapsed, 3),
            "drift_corrections": self._drift.corrections_applied,
            "capture_restarts": self._recovery.total_capture_restarts,
            "encoder_failures": self._recovery.total_encoder_failures,
            "gaps": len(self._recovery.gaps),
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, device_name: str | None = None) -> None:
        """Start the capture pipeline.

        Raises RuntimeError if the pipeline is already running.
        """
        if self._running:
            raise RuntimeError("Pipeline is already running")

        self._logger.log(
            "INFO", "lifecycle", "pipeline.started",
            data={"device": device_name or self._config.device_pattern},
        )

        self._capture.start(device_name)
        self._start_time = time.monotonic()

        # Create DynamicSplitProcessor with live boundary queue
        self._split = DynamicSplitProcessor(
            boundary_queue=self._boundary_queue,
            start_encoder=self._start_encoder,
            on_segment_complete=self._on_segment_complete,
        )
        self._split.start()

        self._producer_thread = threading.Thread(
            target=self._producer_loop,
            name="capture-producer",
            daemon=True,
        )
        self._consumer_thread = threading.Thread(
            target=self._consumer_loop,
            name="capture-consumer",
            daemon=True,
        )

        self._running = True
        self._producer_thread.start()
        self._consumer_thread.start()

    def stop(self) -> None:
        """Stop the capture pipeline gracefully.

        No-op if the pipeline is not running.
        """
        if not self._running:
            return

        self._logger.log("INFO", "lifecycle", "pipeline.stopping")
        self._running = False

        try:
            self._capture.stop()
        except Exception as exc:
            self._logger.log(
                "WARNING", "lifecycle", "capture.stop_error",
                data={"error": str(exc)},
            )

        try:
            self._timing.stop()
        except Exception:
            pass

        join_timeout = 10.0

        if self._producer_thread is not None:
            self._producer_thread.join(timeout=join_timeout)
            if self._producer_thread.is_alive():
                self._logger.log(
                    "WARNING", "lifecycle", "producer.join_timeout",
                )

        if self._consumer_thread is not None:
            self._consumer_thread.join(timeout=join_timeout)
            if self._consumer_thread.is_alive():
                self._logger.log(
                    "WARNING", "lifecycle", "consumer.join_timeout",
                )

        # Join all background finalization threads
        with self._finalize_lock:
            pending_threads = list(self._finalize_threads)
        for ft in pending_threads:
            ft.join(timeout=10.0)
            if ft.is_alive():
                self._logger.log(
                    "WARNING", "lifecycle", "finalize_thread.join_timeout",
                    data={"thread_name": ft.name},
                )

        self._logger.log(
            "INFO", "lifecycle", "pipeline.stopped",
            data=self.stats,
        )

        self._producer_thread = None
        self._consumer_thread = None
        self._start_time = None

    def shutdown(self) -> None:
        """Safe shutdown wrapper — idempotent, exception-safe."""
        try:
            self.stop()
        except Exception as exc:
            self._logger.log(
                "ERROR", "lifecycle", "shutdown.error",
                data={"error": str(exc)},
            )
        finally:
            self._running = False

    # ------------------------------------------------------------------
    # External timing API
    # ------------------------------------------------------------------

    def update_timing(self, timing_data: dict) -> int:
        """Push new timing data and refresh the boundary queue.

        Returns the number of new boundaries applied.
        """
        return self._timing.update(timing_data)

    def on_track_change(self, timing_data: dict) -> int:
        """Handle a track-change event with immediate boundary refresh."""
        return self._timing.on_track_change(timing_data)

    def start_periodic_timing(self, fetch_fn: Callable[[], dict | None]) -> None:
        """Start background periodic timing refresh."""
        self._timing.start_periodic_refresh(fetch_fn)

    # ------------------------------------------------------------------
    # Callback dispatch
    # ------------------------------------------------------------------

    def _on_segment_complete(
        self,
        segment_index: int,
        start_frame: int,
        end_frame: int,
    ) -> None:
        """Handle segment completion — capture metadata, dispatch finalization to background."""
        self._segments_completed += 1

        # Capture boundary metadata on the consumer thread (before queue can change)
        boundary_meta = self._get_segment_metadata(segment_index)

        # Dispatch all blocking work to a background thread
        t = threading.Thread(
            target=self._finalize_segment,
            args=(segment_index, start_frame, end_frame, boundary_meta),
            name=f"finalize-segment-{segment_index}",
            daemon=True,
        )
        t.start()
        with self._finalize_lock:
            self._finalize_threads.append(t)

    def _finalize_segment(
        self,
        segment_index: int,
        start_frame: int,
        end_frame: int,
        boundary_meta: dict | None,
    ) -> None:
        """Background thread: wait for encoder, write sidecar, fire callback."""
        # Retrieve and wait for encoder
        encoder = self._encoders.get(segment_index)
        if encoder is not None:
            try:
                rc = encoder.wait(timeout=30.0)
            except Exception:
                rc = -1

            if rc != 0:
                self._logger.recovery_event(
                    "encoder_failure",
                    frame_position=end_frame,
                    segment_index=segment_index,
                    exit_code=rc,
                    stderr=encoder.stderr_output,
                )
                result = self._recovery.handle_encoder_failure(
                    segment_info={
                        "segment_index": segment_index,
                        "output_path": encoder.output_path,
                    },
                    start_encoder_fn=lambda info: self._retry_encoder(info),
                )
                self._logger.recovery_event(
                    "encoder_recovery_result",
                    frame_position=end_frame,
                    segment_index=segment_index,
                    result=result,
                )
                if result == "skipped":
                    return

            mp3_path = encoder.output_path
        else:
            mp3_path = ""

        # Build segment metadata (using pre-captured boundary_meta)
        seg_meta = self._build_segment_metadata(
            segment_index, start_frame, end_frame, boundary_meta=boundary_meta,
        )

        # Write JSON sidecar
        try:
            sidecar_path = self._metadata_writer.write_sidecar(mp3_path, seg_meta)
        except Exception as exc:
            sidecar_path = ""
            self._logger.log(
                "WARNING", "output", "sidecar.write_error",
                data={"error": str(exc), "segment_index": segment_index},
            )

        # Log segment completion
        self._logger.log(
            "INFO", "split", "segment_complete",
            data={
                "segment_index": segment_index,
                "mp3_path": mp3_path,
                "sidecar_path": sidecar_path,
                "duration_frames": end_frame - start_frame,
            },
            frame_position=end_frame,
        )

        # Fire user callback
        if self._on_segment_saved is not None:
            metadata_dict = asdict(seg_meta)
            try:
                self._on_segment_saved(mp3_path, metadata_dict)
            except Exception as exc:
                self._logger.log(
                    "WARNING", "output", "callback.error",
                    data={"error": str(exc), "segment_index": segment_index},
                )

    # ------------------------------------------------------------------
    # Thread loops
    # ------------------------------------------------------------------

    def _producer_loop(self) -> None:
        """Read PCM chunks from FFmpeg stdout and feed them into the buffer.

        On FFmpeg failure, attempts automatic recovery via RecoveryManager.
        If recovery succeeds, re-enters the loop with the new stdout pipe.
        If retries are exhausted, signals EOF so the consumer drains gracefully.
        """
        try:
            for chunk in read_chunks(self._capture.stdout, self._config.chunk_ms):
                if not self._running:
                    break
                self._buffer.put(chunk)
                self._recovery.reset_capture_failure_count()
        except Exception as exc:
            self._logger.recovery_event(
                "capture_failure",
                frame_position=self._buffer.frames_processed,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            recovered = self._recovery.handle_capture_failure(
                frame_position=self._buffer.frames_processed,
            )
            if recovered:
                self._logger.recovery_event(
                    "capture_restarted",
                    frame_position=self._buffer.frames_processed,
                )
                self._producer_loop()
                return
            else:
                self._logger.log(
                    "ERROR", "recovery", "capture_unrecoverable",
                    data={"retries_exhausted": True},
                    frame_position=self._buffer.frames_processed,
                )
        finally:
            self._buffer.signal_eof()

    def _consumer_loop(self) -> None:
        """Read chunks from the buffer and route to the split processor."""
        chunks_since_drift_check = 0
        try:
            while True:
                chunk = self._buffer.get()
                if chunk is None:
                    break
                if self._split is not None:
                    self._split.process_chunk(chunk)
                self._boundary_queue.update_locks(self._buffer.frames_processed)
                chunks_since_drift_check += 1
                if chunks_since_drift_check >= DRIFT_CHECK_INTERVAL_CHUNKS:
                    self._check_drift()
                    chunks_since_drift_check = 0
        except Exception as exc:
            self._logger.log(
                "ERROR", "lifecycle", "consumer.error",
                data={"error": str(exc)},
            )
        finally:
            if self._split is not None:
                try:
                    self._split.finish()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Drift measurement
    # ------------------------------------------------------------------

    def _check_drift(self) -> None:
        """Measure current drift and auto-correct if threshold exceeded."""
        if self._start_time is None:
            return
        elapsed = time.monotonic() - self._start_time
        m = self._drift.measure_and_correct(
            actual_frames=self._buffer.frames_processed,
            elapsed_wall_seconds=elapsed,
            current_frame=self._buffer.frames_processed,
        )
        self._logger.timing_event(
            "drift_check",
            frame_position=self._buffer.frames_processed,
            drift_seconds=m.drift_seconds,
            drift_frames=m.drift_frames,
            level=m.level,
        )

    # ------------------------------------------------------------------
    # Encoder factory & metadata
    # ------------------------------------------------------------------

    def _retry_encoder(self, segment_info: dict) -> object | None:
        """Attempt to re-encode a failed segment.

        Returns None since the pipeline does not buffer PCM data for re-encoding.
        """
        return None

    def _start_encoder(self, segment_index: int) -> PcmAccumulator:
        """Create a PcmAccumulator and spawn the real encoder in the background.

        Returns immediately — the accumulator buffers writes until the real
        ``EncoderProcess`` is attached by the background spawn thread.
        """
        accumulator = PcmAccumulator()
        self._encoders[segment_index] = accumulator

        # Capture metadata now (consumer thread) before the queue can change
        metadata = self._get_segment_metadata(segment_index)

        def _spawn() -> None:
            mp3_path = self._file_namer.next_filename(metadata)
            encoder = EncoderProcess(
                output_path=mp3_path,
                sample_rate=self._config.sample_rate,
                channels=self._config.channels,
                bitrate=self._config.bitrate,
            )
            encoder.start()
            accumulator.attach_encoder(encoder)

            self._logger.log(
                "INFO", "encoder", "encoder_started",
                data={
                    "segment_index": segment_index,
                    "output_path": mp3_path,
                },
                frame_position=self._buffer.frames_processed,
            )

        t = threading.Thread(target=_spawn, name=f"spawn-encoder-{segment_index}", daemon=True)
        t.start()

        return accumulator

    def _get_segment_metadata(self, segment_index: int) -> dict | None:
        """Retrieve metadata from BoundaryQueue for the given segment."""
        entry = self._boundary_queue.peek_next()
        if entry is not None and entry.metadata is not None:
            return entry.metadata
        return None

    def _build_segment_metadata(
        self, segment_index: int, start_frame: int, end_frame: int,
        boundary_meta: dict | None = None,
    ) -> SegmentMetadata:
        """Construct metadata for a completed segment."""
        if boundary_meta is None:
            boundary_meta = self._get_segment_metadata(segment_index)
        gaps = [
            {
                "start_frame": g.start_frame,
                "end_frame": g.end_frame,
                "duration_frames": g.duration_frames,
                "timestamp": g.timestamp,
            }
            for g in self._recovery.gaps
        ]

        output_file = None
        enc = self._encoders.get(segment_index)
        if enc is not None:
            output_file = enc.output_path

        return SegmentMetadata(
            start_frame=start_frame,
            end_frame=end_frame,
            sample_rate=self._config.sample_rate,
            channels=self._config.channels,
            bitrate=self._config.bitrate,
            segment_index=segment_index,
            song_title=boundary_meta.get("song_title") if boundary_meta else None,
            artist=boundary_meta.get("artist") if boundary_meta else None,
            album=boundary_meta.get("album") if boundary_meta else None,
            output_file=output_file,
            gaps=gaps,
        )
