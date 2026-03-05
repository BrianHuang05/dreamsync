# D1 — Orchestrator Core

## Prerequisites

- All 20 capture pipeline modules implemented (`src/dreamsync/capture/`)
- 185 unit tests passing

## Overview

Create `CaptureOrchestrator` in `src/dreamsync/capture/orchestrator.py`. This class aggregates configuration for all pipeline modules, instantiates them, and manages the full lifecycle (start → run → stop → shutdown).

---

## Implementation Items

### 1.1 OrchestratorConfig dataclass

Frozen dataclass that composes all sub-module configs into a single object.

```python
@dataclass(frozen=True)
class OrchestratorConfig:
    # Capture process
    sample_rate: int = 48000
    channels: int = 2
    device_pattern: str = "CABLE Output"

    # PCM reader
    chunk_ms: int = 100

    # AudioBuffer
    buffer_max_chunks: int = 10

    # Encoding
    bitrate: str = "192k"

    # Output
    output_dir: str = "./captured_songs"
    naming: str = "timestamp"      # "timestamp" or "metadata"
    log_dir: str = "./logs"

    # Timing
    safety_margin_frames: int = 24_000   # 0.5s at 48kHz
    timing_refresh_interval: float = 5.0

    # Drift
    drift_warning_threshold: float = 0.1
    drift_correction_threshold: float = 0.5
    drift_critical_threshold: float = 2.0

    # Recovery
    capture_restart_delay: float = 0.2
    max_capture_retries: int = 5
    encoder_retry_count: int = 1
```

### 1.2 CaptureOrchestrator class

```python
class CaptureOrchestrator:
    def __init__(
        self,
        config: OrchestratorConfig | None = None,
        on_segment_saved: Callable[[str, dict], None] | None = None,
    ) -> None: ...
```

Constructor instantiates all sub-modules but does **not** start anything:

| Module | Constructor args | Source |
|--------|-----------------|--------|
| `PipelineLogger` | `log_dir` | `pipeline_logger.py` |
| `CaptureProcessManager` | `CaptureConfig(sample_rate, channels, device_pattern)` | `capture_process.py` |
| `AudioBuffer` | `max_chunks, sample_rate` | `pcm_buffer.py` |
| `BoundaryQueue` | `safety_margin_frames` | `boundary_queue.py` |
| `FileNamer` | `output_dir, naming` | `file_namer.py` |
| `MetadataWriter` | `output_dir` | `metadata_writer.py` |
| `DriftDetector` | `sample_rate, boundary_queue, thresholds` | `drift_detector.py` |
| `TimingIntegrator` | `boundary_queue, get_current_frame, sample_rate, refresh_interval` | `timing_integrator.py` |
| `RecoveryManager` | `capture_manager, RecoveryConfig(...)` | `recovery_manager.py` |
| `SplitProcessor` | Deferred — created in `start()` once boundaries are known | `split_logic.py` |

### 1.3 Lifecycle methods

#### `start(device_name: str | None = None) -> None`

1. Log `pipeline.started` event
2. Call `CaptureProcessManager.start(device_name)`
3. Create producer thread: reads chunks from `pcm_reader.read_chunks(capture.stdout)` → `AudioBuffer.put(chunk)`
4. Create consumer thread: `AudioBuffer.get()` → `SplitProcessor.process_chunk(chunk)`
5. Start both threads
6. Record `_start_time = time.monotonic()` for drift measurement

#### `stop() -> None`

1. Log `pipeline.stopping` event
2. Call `CaptureProcessManager.stop()` — this closes FFmpeg stdout, which causes `read_chunks()` to hit EOF
3. Producer thread exits naturally on EOF, calls `AudioBuffer.signal_eof()`
4. Consumer thread reads remaining chunks, then gets `None` (EOF sentinel)
5. Call `SplitProcessor.finish()` to close the final encoder
6. Write final sidecar via `MetadataWriter`
7. Stop `TimingIntegrator` periodic refresh
8. Join both threads with timeout
9. Log `pipeline.stopped` event with summary stats

#### `shutdown() -> None`

Safe shutdown wrapper: calls `stop()` in a try/except, logs any exceptions, and always cleans up resources. Idempotent — safe to call multiple times.

### 1.4 Properties / status

```python
@property
def is_running(self) -> bool: ...

@property
def stats(self) -> dict:
    """Return a snapshot of pipeline statistics."""
    return {
        "segments_completed": ...,
        "frames_processed": self._buffer.frames_processed,
        "elapsed_seconds": self._buffer.elapsed_seconds(),
        "drift_corrections": self._drift.corrections_applied,
        "capture_restarts": self._recovery.total_capture_restarts,
        "encoder_failures": self._recovery.total_encoder_failures,
        "gaps": len(self._recovery.gaps),
    }
```

### 1.5 Callback wiring

- `on_segment_saved(mp3_path: str, metadata: dict)` — called after each segment is finalized (encoder closed + sidecar written). Fires on the consumer thread.

---

## File Output

```
src/dreamsync/capture/orchestrator.py
```

## Tests

```
dev/tests/test_orchestrator.py
```

### Test cases

- [x] `test_config_defaults` — OrchestratorConfig has sensible defaults matching module defaults
- [x] `test_instantiation` — CaptureOrchestrator can be created without starting
- [x] `test_lifecycle_not_started` — calling `stop()` before `start()` is a no-op
- [x] `test_shutdown_idempotent` — calling `shutdown()` twice doesn't raise
- [x] `test_is_running_false_before_start` — `is_running` returns False initially
- [x] `test_stats_initial` — `stats` returns zeros before start

---

## Passing Criteria

- [x] `OrchestratorConfig` composes all sub-module parameters into a single frozen dataclass
- [x] `CaptureOrchestrator.__init__()` instantiates all modules without side effects (no threads, no subprocesses)
- [x] `start()` spawns FFmpeg and both pipeline threads
- [x] `stop()` performs orderly shutdown: drain buffer, finalize last encoder, write sidecar, join threads
- [x] `shutdown()` is idempotent and exception-safe
- [x] `stats` property returns a dict with all relevant counters
- [x] All D1 tests pass (23/23)
