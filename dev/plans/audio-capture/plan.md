# Audio Capture Pipeline - Implementation Plan

## Overview

Capture computer audio through a virtual speaker, record losslessly as PCM, and split into MP3 files based on externally supplied song timing data.

**Pipeline:**

```
Computer Audio -> VB-Audio Virtual Cable -> FFmpeg (capture PCM) -> Handler Program (split logic) -> FFmpeg (encode MP3 per segment)
```

**Audio Format:**

| Parameter       | Value                    |
|-----------------|--------------------------|
| Sample Rate     | 48,000 Hz                |
| Channels        | 2 (stereo)               |
| Bit Depth       | 16-bit signed LE (s16le) |
| Bytes Per Frame | 4                        |
| Bytes Per Second | 192,000                 |

---

## Phase 1: Audio Routing & Environment Setup — ✅ COMPLETE

**Goal:** Install virtual audio device and verify audio signal path from application output through virtual cable.

**Deliverables:**
- [1.1: Install VB-Audio Virtual Cable](phase1/p1.1-install-vb-cable.md) ✅
- [1.2: Configure Application Audio Routing](phase1/p1.2-configure-audio-routing.md) ✅
- [1.3: Verify Audio Signal Path](phase1/p1.3-verify-audio-signal.md) ✅

**Status:** VB-Audio Virtual Cable installed. Audio routing configured via `audio_router.py` (PowerShell-based Windows audio routing).

**Phase Plan:** [phase1-audio-routing.md](phase1-audio-routing.md)

---

## Phase 2: PCM Capture Engine — ✅ COMPLETE

**Goal:** Build the core capture subsystem that spawns FFmpeg, reads raw PCM from its stdout, and maintains a frame counter with configurable buffering.

**Deliverables:**
- [2.1: FFmpeg Device Discovery](phase2/p2.1-ffmpeg-device-discovery.md) ✅ → `ffmpeg_device.py`
- [2.2: Capture Process Manager](phase2/p2.2-capture-process-manager.md) ✅ → `capture_process.py`
- [2.3: PCM Stream Reader](phase2/p2.3-pcm-stream-reader.md) ✅ → `pcm_reader.py`
- [2.4: Frame Counter & Buffer](phase2/p2.4-frame-counter-buffer.md) ✅ → `pcm_buffer.py`

**Status:** 64 tests passing. All modules in `src/dreamsync/capture/`.

**Phase Plan:** [phase2-pcm-capture.md](phase2-pcm-capture.md)

---

## Phase 3: Static Segment Splitting — ✅ COMPLETE

**Goal:** Given a fixed list of song durations and current playback time, compute segment boundaries, spawn per-segment FFmpeg encoders, and split the PCM stream into correctly bounded MP3 files.

**Deliverables:**
- [3.1: Segment Boundary Computation](phase3/p3.1-segment-boundary-computation.md) ✅ → `segment_boundary.py`
- [3.2: Encoder Process Manager](phase3/p3.2-encoder-process-manager.md) ✅ → `encoder_process.py`
- [3.3: Split Logic Algorithm](phase3/p3.3-split-logic-algorithm.md) ✅ → `split_logic.py`
- [3.4: File Naming & Output](phase3/p3.4-file-naming-output.md) ✅ → `file_namer.py`

**Status:** 45 tests passing. Zero-loss split guarantee verified.

**Phase Plan:** [phase3-static-splitting.md](phase3-static-splitting.md)

---

## Phase 4: Dynamic Rolling Window — ✅ COMPLETE

**Goal:** Handle real-time updates to song timing data by maintaining a boundary queue, enforcing safety margins, and integrating external timing refresh events.

**Deliverables:**
- [4.1+4.2: Boundary Queue + Safety Margins](phase4/p4.1-boundary-queue-manager.md) ✅ → `boundary_queue.py`
- [4.3: External Timing Integration](phase4/p4.3-external-timing-integration.md) ✅ → `timing_integrator.py`

**Status:** 25 tests passing. Safety margin enforcement integrated into boundary queue. Thread-safe with locking.

**Phase Plan:** [phase4-dynamic-updates.md](phase4-dynamic-updates.md)

---

## Phase 5: Metadata, Logging & Recovery — ✅ COMPLETE

**Goal:** Add operational robustness through metadata sidecar files, drift detection, failure recovery, and structured logging.

**Deliverables:**
- [5.1: Metadata Sidecar Files](phase5/p5.1-metadata-sidecar-files.md) ✅ → `metadata_writer.py`
- [5.2: Drift Detection & Resync](phase5/p5.2-drift-detection-resync.md) ✅ → `drift_detector.py`
- [5.3: Failure Handling & Recovery](phase5/p5.3-failure-handling-recovery.md) ✅ → `recovery_manager.py`
- [5.4: Logging System](phase5/p5.4-logging-system.md) ✅ → `pipeline_logger.py`

**Status:** 46 tests passing. 4-level drift detection (acceptable/warning/correction/critical). Recovery cascade: retry → fallback (raw PCM) → skip.

**Phase Plan:** [phase5-metadata-recovery.md](phase5-metadata-recovery.md)

---

## Implementation Summary

All 5 phases complete. **185 unit tests** passing across 14 test files.

### Module inventory (`src/dreamsync/capture/`)

| Module | Phase | Tests | Description |
|--------|-------|-------|-------------|
| `ffmpeg_device.py` | 2.1 | 13 | FFmpeg DirectShow device discovery |
| `capture_process.py` | 2.2 | 13 | FFmpeg capture process lifecycle |
| `pcm_reader.py` | 2.3 | 14 | Frame-aligned PCM chunk reader |
| `pcm_buffer.py` | 2.4 | 24 | Thread-safe queue buffer with frame counter |
| `segment_boundary.py` | 3.1 | 11 | Boundary computation from timing data |
| `encoder_process.py` | 3.2 | 10 | Per-segment FFmpeg MP3 encoder |
| `split_logic.py` | 3.3 | 12 | Zero-loss PCM split at frame boundaries |
| `file_namer.py` | 3.4 | 12 | Deterministic file naming + sanitization |
| `boundary_queue.py` | 4.1+4.2 | 17 | Mutable boundary queue + safety margins |
| `timing_integrator.py` | 4.3 | 8 | External timing integration + refresh |
| `metadata_writer.py` | 5.1 | 12 | JSON sidecar files (atomic writes) |
| `drift_detector.py` | 5.2 | 12 | 4-level drift detection + boundary correction |
| `recovery_manager.py` | 5.3 | 10 | Capture/encoder failure recovery cascade |
| `pipeline_logger.py` | 5.4 | 12 | Structured JSON + console logging |
| `audio_router.py` | 1.x | — | Windows audio routing via PowerShell |

### Configuration (resolved)

| Decision             | Value                   |
|----------------------|-------------------------|
| MP3 bitrate          | 192 kbps                |
| Channel mode         | Stereo                  |
| File naming scheme   | Timestamp (default), Metadata (optional) |
| Plan refresh interval| 5 seconds               |
| Safety margin        | 0.5 seconds (24,000 frames) |
| Read chunk size      | 100 ms                  |

### Next steps

Integration phase: wire individual modules into an end-to-end capture pipeline orchestrator and connect to CLI commands. See `dev/NEXT_STEPS.md`.
