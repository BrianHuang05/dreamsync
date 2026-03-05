# Pipeline Orchestrator — Integration Plan

## Overview

Wire the 20 existing capture pipeline modules (185 unit tests) into a single `CaptureOrchestrator` class, integrate with the CLI, and validate end-to-end.

The orchestrator replaces the current `StreamCapturePipeline` (buffer → boundary detector → writer) with a full-featured pipeline that uses frame-level splitting, per-segment encoding, timing integration, drift correction, failure recovery, and structured logging.

---

## Data Flow Diagram

```
                       ┌─────────────────────────────────────────────────┐
                       │              CaptureOrchestrator                │
                       │                                                 │
  Spotify/External ──► │  TimingIntegrator ──► BoundaryQueue             │
  Timing Data          │       │                   │                     │
                       │       │            DriftDetector                 │
                       │       │               (monitor)                 │
                       │       ▼                   ▼                     │
  DirectShow ────────► │  CaptureProcessManager                         │
  Audio Device         │       │                                         │
                       │       ▼                                         │
                       │  pcm_reader.read_chunks()                       │
                       │       │                                         │
                       │       ▼                                         │
                       │  AudioBuffer  (thread-safe queue, frame counter)│
                       │       │                                         │
                       │       ▼                                         │
                       │  SplitProcessor  ◄── boundaries from queue      │
                       │       │                                         │
                       │       ├──► EncoderProcess[0] ──► song_0.mp3    │
                       │       ├──► EncoderProcess[1] ──► song_1.mp3    │
                       │       └──► EncoderProcess[N] ──► song_N.mp3    │
                       │                  │                              │
                       │                  ▼                              │
                       │  FileNamer + MetadataWriter (per segment)       │
                       │                                                 │
                       │  RecoveryManager (wraps capture + encoder)      │
                       │  PipelineLogger  (all events → JSONL + console) │
                       └─────────────────────────────────────────────────┘
```

**Two independent paths converge at `SplitProcessor`:**

1. **Data path** (D2): CaptureProcessManager → pcm_reader → AudioBuffer → SplitProcessor → EncoderProcess → FileNamer + MetadataWriter
2. **Control path** (D3): TimingIntegrator → BoundaryQueue → SplitProcessor + DriftDetector

---

## Deliverables

| # | Deliverable | File | Description |
|---|-------------|------|-------------|
| D1 | Orchestrator Core | `d1-orchestrator-core.md` | `CaptureOrchestrator` class — config aggregation, module instantiation, lifecycle (start/stop/shutdown) |
| D2 | Capture Data Path | `d2-capture-data-path.md` | Wire the PCM data pipeline: CaptureProcessManager → pcm_reader → AudioBuffer → SplitProcessor → EncoderProcess → FileNamer + MetadataWriter |
| D3 | Boundary Control Path | `d3-boundary-control-path.md` | Wire the timing/boundary pipeline: TimingIntegrator → BoundaryQueue → SplitProcessor + DriftDetector monitoring |
| D4 | Recovery & Logging | `d4-recovery-logging.md` | Wire RecoveryManager (capture/encoder failures) + PipelineLogger (all events) |
| D5 | CLI Integration | `d5-cli-integration.md` | Update `dreamsync capture` subcommand; wire `--capture` flag in `session`/`govee-live` to use `CaptureOrchestrator` |
| D6 | Integration Tests | `d6-integration-tests.md` | Synthetic audio E2E tests, split accuracy validation, failure injection tests |

---

## Dependency Chain

```
D1 (Orchestrator Core)
 ├──► D2 (Capture Data Path)     — needs CaptureOrchestrator shell
 ├──► D3 (Boundary Control Path) — needs CaptureOrchestrator shell
 └──► D4 (Recovery & Logging)    — needs CaptureOrchestrator shell
        │
        ▼
      D5 (CLI Integration)       — needs D2 + D3 + D4 complete
        │
        ▼
      D6 (Integration Tests)     — needs D5 complete
```

D2, D3, D4 can proceed in parallel once D1 is done. D5 depends on all three. D6 depends on D5.

---

## Existing Modules (Input)

All modules are implemented and unit-tested in `src/dreamsync/capture/`:

| Module | File | Tests |
|--------|------|-------|
| FFmpeg Device Discovery | `ffmpeg_device.py` | `test_capture_process.py` |
| Audio Routing | `audio_router.py` | — |
| Capture Process Manager | `capture_process.py` | `test_capture_process.py` |
| PCM Reader | `pcm_reader.py` | `test_pcm_reader.py` |
| PCM Buffer (queue) | `pcm_buffer.py` | `test_pcm_buffer.py` |
| Audio Stream Buffer (growable) | `buffer.py` | `test_capture_buffer.py` |
| Song Boundary Detector | `boundary.py` | `test_capture_boundary.py` |
| Boundary Queue | `boundary_queue.py` | `test_boundary_queue.py` |
| Segment Boundary Computation | `segment_boundary.py` | `test_segment_boundary.py` |
| Split Processor | `split_logic.py` | `test_split_logic.py` |
| Encoder Process | `encoder_process.py` | `test_encoder_process.py` |
| File Namer | `file_namer.py` | `test_file_namer.py` |
| Song File Writer | `writer.py` | `test_capture_writer.py` |
| Metadata Writer | `metadata_writer.py` | `test_metadata_writer.py` |
| Timing Integrator | `timing_integrator.py` | `test_timing_integrator.py` |
| Drift Detector | `drift_detector.py` | `test_drift_detector.py` |
| Recovery Manager | `recovery_manager.py` | `test_recovery_manager.py` |
| Pipeline Logger | `pipeline_logger.py` | `test_pipeline_logger.py` |
| Stream Capture Pipeline (v1) | `pipeline.py` | `test_capture_pipeline.py` |

---

## Key Design Decisions

1. **CaptureOrchestrator replaces StreamCapturePipeline** — The v1 `StreamCapturePipeline` uses in-memory numpy accumulation + single-shot encoding. The new orchestrator uses streaming PCM through `SplitProcessor` with per-segment `EncoderProcess` instances — no full-song buffering required.

2. **Two-thread architecture** — Producer thread (pcm_reader → AudioBuffer), consumer thread (AudioBuffer → SplitProcessor → encoders). `AudioBuffer` decouples them with backpressure.

3. **Dynamic boundaries** — `BoundaryQueue` is mutable; `TimingIntegrator` can update future boundaries as Spotify queue data arrives. The `SplitProcessor` reads the next boundary from the queue instead of using a static list.

4. **Graceful shutdown** — `stop()` signals EOF on the capture process, drains the buffer, finalizes the last encoder, and writes the final sidecar. Ctrl+C triggers orderly shutdown via the existing signal handler in `session.py`.

---

## Files Created / Modified

| Action | Path |
|--------|------|
| **Create** | `src/dreamsync/capture/orchestrator.py` |
| **Modify** | `src/dreamsync/session.py` (replace StreamCapturePipeline with CaptureOrchestrator) |
| **Modify** | `src/dreamsync/cli.py` (update `capture` subcommand args) |
| **Create** | `dev/tests/test_orchestrator.py` |
| **Create** | `dev/tests/test_capture_integration.py` |

---

## Success Criteria

- [ ] `CaptureOrchestrator` starts FFmpeg capture, streams PCM through SplitProcessor, produces per-song MP3 files with JSON sidecars
- [ ] Spotify timing data dynamically updates boundary queue mid-capture
- [ ] Drift detection measures and auto-corrects boundary positions over 30+ minute sessions
- [ ] Capture process failure triggers automatic restart with gap recording
- [ ] Encoder process failure triggers retry → raw fallback → skip cascade
- [ ] All events logged to structured JSONL + console
- [ ] `dreamsync session --capture` uses the new orchestrator
- [ ] `dreamsync capture` subcommand runs standalone capture pipeline
- [ ] Integration tests pass with synthetic audio (no real audio device required)
- [ ] Existing 185 unit tests continue to pass
