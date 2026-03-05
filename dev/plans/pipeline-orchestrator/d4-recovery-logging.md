# D4 — Recovery & Logging

## Prerequisites

- D1 (Orchestrator Core) — `CaptureOrchestrator` shell with lifecycle methods
- `RecoveryManager` and `PipelineLogger` implemented and tested

## Overview

Wire `RecoveryManager` to handle capture process and encoder process failures within the orchestrator. Wire `PipelineLogger` to emit structured events for all pipeline state transitions.

---

## Implementation Items

### 4.1 RecoveryManager wiring

In `CaptureOrchestrator.__init__()`:

```python
self._recovery = RecoveryManager(
    capture_manager=self._capture,
    config=RecoveryConfig(
        capture_restart_delay=config.capture_restart_delay,
        max_capture_retries=config.max_capture_retries,
        encoder_retry_count=config.encoder_retry_count,
    ),
)
```

### 4.2 Capture failure recovery in producer thread

When the producer thread encounters an exception reading from FFmpeg stdout:

```python
def _producer_loop(self) -> None:
    try:
        for chunk in read_chunks(self._capture.stdout, self._config.chunk_ms):
            self._buffer.put(chunk)
            self._recovery.reset_capture_failure_count()
    except Exception as exc:
        self._logger.recovery_event(
            "capture_failure",
            frame_position=self._buffer.frames_processed,
            error=str(exc),
        )
        recovered = self._recovery.handle_capture_failure(
            frame_position=self._buffer.frames_processed,
        )
        if recovered:
            self._logger.recovery_event(
                "capture_restarted",
                frame_position=self._buffer.frames_processed,
            )
            # Restart the read loop with the new process stdout
            self._producer_loop()  # recursive re-entry
            return
        else:
            self._logger.log(
                "ERROR", "recovery", "capture_unrecoverable",
                data={"retries_exhausted": True},
                frame_position=self._buffer.frames_processed,
            )
    finally:
        self._buffer.signal_eof()
```

Recovery flow:
1. FFmpeg crashes → `read_chunks()` raises an exception
2. `RecoveryManager.handle_capture_failure()` calls `capture.stop()` then `capture.start()`
3. Records a `GapRecord` with the frame position of the failure
4. If successful, the producer restarts reading from the new `capture.stdout`
5. If retries exhausted (default 5), signals EOF and the pipeline drains gracefully

### 4.3 Encoder failure recovery in segment completion

When an encoder process exits with non-zero return code:

```python
def _on_segment_complete(
    self, segment_index: int, start_frame: int, end_frame: int
) -> None:
    encoder = self._encoders[segment_index]
    rc = encoder.wait(timeout=30.0)

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
            result=result,  # "retried", "fallback", or "skipped"
        )
        if result == "skipped":
            return  # No sidecar for skipped segments

    # ... write sidecar, fire callback (same as D2) ...
```

Recovery cascade:
1. **Retry** — spawn a new EncoderProcess for the same segment
2. **Fallback** — write raw PCM to `.raw` file (preserves data)
3. **Skip** — log error, continue to next segment

### 4.4 PipelineLogger wiring

In `CaptureOrchestrator.__init__()`:

```python
self._logger = PipelineLogger(
    log_dir=config.log_dir,
    console_level="INFO",
    file_level="DEBUG",
)
```

### 4.5 Event catalog

All events logged by the orchestrator:

| Category | Event | Level | When |
|----------|-------|-------|------|
| `pipeline` | `started` | INFO | `start()` called |
| `pipeline` | `stopping` | INFO | `stop()` called |
| `pipeline` | `stopped` | INFO | All threads joined, summary logged |
| `capture` | `ffmpeg_started` | INFO | CaptureProcessManager.start() succeeds |
| `capture` | `ffmpeg_stopped` | INFO | CaptureProcessManager.stop() returns |
| `capture` | `chunk_read` | DEBUG | Each PCM chunk read (high volume, file-only) |
| `encoder` | `encoder_started` | INFO | New EncoderProcess spawned |
| `encoder` | `encoder_finished` | INFO | EncoderProcess.wait() returns 0 |
| `split` | `segment_complete` | INFO | Segment finalized with MP3 + sidecar |
| `split` | `boundary_crossed` | INFO | SplitProcessor rotates encoder at boundary |
| `boundary` | `queue_updated` | INFO | BoundaryQueue.replace_future() called |
| `boundary` | `boundary_locked` | DEBUG | Boundary enters safety margin |
| `timing` | `timing_update` | INFO | TimingIntegrator.update() called |
| `timing` | `drift_check` | INFO | DriftDetector.measure() called |
| `timing` | `drift_correction` | WARNING | DriftDetector auto-corrects boundaries |
| `recovery` | `capture_failure` | WARNING | FFmpeg read fails |
| `recovery` | `capture_restarted` | INFO | FFmpeg restarted successfully |
| `recovery` | `capture_unrecoverable` | ERROR | Retries exhausted |
| `recovery` | `encoder_failure` | WARNING | Encoder exits non-zero |
| `recovery` | `encoder_recovery_result` | INFO | Recovery cascade result |

### 4.6 Gap records in metadata

When a capture restart occurs, the `GapRecord` is stored by `RecoveryManager`. These gaps are included in the `SegmentMetadata.gaps` field of all subsequent segments, allowing downstream consumers to know about data loss:

```json
{
  "gaps": [
    {
      "start_frame": 480000,
      "end_frame": 480000,
      "duration_frames": 0,
      "timestamp": 1709500000.0
    }
  ]
}
```

### 4.7 Shutdown logging

In `stop()`, log a summary event:

```python
self._logger.log(
    "INFO", "pipeline", "stopped",
    data=self.stats,
    frame_position=self._buffer.frames_processed,
)
```

---

## Log Output

```
./logs/pipeline.jsonl     — Structured JSON, one object per line
stderr                    — Human-readable console format
```

Example JSONL line:
```json
{"timestamp":"2026-03-04T15:30:00.000Z","level":"INFO","category":"split","event":"segment_complete","data":{"segment_index":3,"mp3_path":"captured_songs/2026-03-04_15-26-12_segment_000004.mp3","duration_frames":8640000},"frame_position":34560000}
```

Example console line:
```
15:30:00.000 [INFO   ] split:segment_complete  frame=34560000
```

---

## File Modified

```
src/dreamsync/capture/orchestrator.py  (add recovery wiring, logging calls)
```

## Tests

Added to `dev/tests/test_orchestrator.py`:

- [x] `test_capture_failure_triggers_recovery` — mock FFmpeg crash, verify RecoveryManager.handle_capture_failure() called
- [x] `test_capture_recovery_restarts_producer` — after recovery, producer resumes reading
- [x] `test_capture_retries_exhausted_signals_eof` — 5 consecutive failures → EOF signaled, pipeline drains
- [x] `test_capture_failure_logged` — capture failure + unrecoverable logged with correct args
- [x] `test_encoder_failure_retry_succeeds` — encoder exit code != 0, retry succeeds
- [x] `test_encoder_failure_skip` — retry + fallback fail, segment skipped
- [x] `test_encoder_failure_logged` — encoder_failure + encoder_recovery_result events logged
- [x] `test_pipeline_logger_wired` — PipelineLogger instance wired to orchestrator
- [x] `test_logger_log_dir` — custom log_dir propagated to PipelineLogger
- [x] `test_recovery_manager_wired` — RecoveryManager instance wired to orchestrator
- [x] `test_recovery_config_from_orchestrator` — OrchestratorConfig maps to RecoveryConfig
- [x] `test_recovery_shares_capture_manager` — RecoveryManager shares CaptureProcessManager
- [x] `test_shutdown_summary_logged` — stop() logs summary with stats dict
- [x] `test_shutdown_log_contains_stats` — stats dict contains all expected keys

---

## Passing Criteria

- [x] Capture process failure triggers `RecoveryManager.handle_capture_failure()` with automatic restart
- [x] After recovery, producer thread resumes reading from the new FFmpeg stdout
- [x] If max retries exhausted, pipeline drains gracefully (no crash)
- [x] Encoder failure triggers retry → fallback → skip cascade via `RecoveryManager.handle_encoder_failure()`
- [x] Gap records from capture restarts are included in segment sidecar metadata
- [x] All pipeline state transitions emit structured log events (JSONL + console)
- [x] `stop()` logs a summary event with pipeline statistics
- [x] All D4 tests pass (14/14)
