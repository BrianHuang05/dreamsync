# Capture Pipeline: Fix Drift, Metadata, and Observability

## Context

Live testing (test 17, `out/capture-meta-4/`) revealed three interconnected issues:

1. **Audio splits at wrong times** — 60 drift corrections in 600s pushed boundaries ~30s off. A constant -0.5s startup offset triggers correction every 10s, but this is a static delay, not growing drift.
2. **Incorrect file naming** — All 3 segments named "Girl." because metadata comes from `peek_next()` after the relevant boundary was already popped by `pop_next()`.
3. **Undiagnosable** — No Spotify data, boundary operations, or timing events in `pipeline.jsonl`. Can't trace what happened.

The audio quality issue (cuts/skips in MP3) cannot be diagnosed without better logging. This plan also adds instrumentation for that.

---

## Step 1: Fix drift baseline tracking

**Files:** `src/dreamsync/capture/drift_detector.py`, `dev/tests/test_drift_detector.py`

The -0.5s startup delay is constant (not growing). Only growing drift needs correction.

- Add `_baseline_drift_frames: int | None` to `DriftDetector.__init__`
- In `measure()`: first call records baseline; subsequent calls compute `adjusted = drift - baseline`
- Use adjusted drift for level classification (acceptable/warning/correction/critical)
- Add `adjusted_drift_frames` and `adjusted_drift_seconds` to `DriftMeasurement`
- `correct()` uses `measurement.adjusted_drift_frames` as the offset
- Add `reset_baseline()` method for recovery/restart scenarios

**Tests to add:** `TestBaselineTracking` — constant offset stays acceptable, growing drift detected, correction uses adjusted offset, reset clears baseline.

**Tests to update:** 3 places construct `DriftMeasurement` directly (lines ~63, 77, 83) — add the two new fields.

**Verify:** `python -m pytest dev/tests/test_drift_detector.py -v`

---

## Step 2: Fix metadata flow (popped boundary → callback)

**File:** `src/dreamsync/capture/orchestrator.py`, `dev/tests/test_orchestrator.py`

Currently `process_chunk` pops the boundary, then `_rotate_encoder` calls `_on_segment_complete` which peeks the NEXT boundary (wrong metadata). The popped boundary's metadata should be used for the completed segment.

- `DynamicSplitProcessor._rotate_encoder(popped_meta: dict | None = None)` — new param
- `process_chunk`: capture `pop_next()` return value, pass `.metadata` to `_rotate_encoder`
- `_on_segment_complete` callback signature: add 4th param `popped_meta: dict | None = None`
- Orchestrator's `_on_segment_complete`: use `popped_meta` if available, fall back to `peek_next()`
- `finish()`: pass `None` (final segment has no popped boundary)
- `_start_encoder` stays unchanged — `peek_next()` is correct for the NEW segment

**Tests to update:** 5 `on_complete(idx, start, end)` callbacks at lines ~900, 937, 971, 1004, 1049 → add 4th param `meta`.

**Tests to add:** `TestMetadataFlow` — popped metadata reaches callback; multiple splits get correct per-boundary metadata; finish passes None.

**Verify:** `python -m pytest dev/tests/test_orchestrator.py -v -k "TestDynamicSplit or TestMetadataFlow"`

---

## Step 3: Per-song metadata in timing data

**Files:** `src/dreamsync/session.py`, `src/dreamsync/capture/timing_integrator.py`, `dev/tests/test_timing_integrator.py`, `dev/tests/test_fetch_timing.py`

Currently `timing_integrator.update()` assigns `current_song` metadata to ALL boundaries. When the Spotify queue has multiple tracks, each boundary should get its own track's metadata.

- `session.py` `_fetch_timing`: add `"songs": [{"song_title": t.name, ...} for t in tracks]`
- `session.py` `_capture_track_changed`: add matching `"songs"` list
- `timing_integrator.update()`: use `songs[i]` for boundary `i`, fall back to `current_song`

**Tests to add:** boundaries get per-song metadata; fallback when `songs` key absent; partial songs list.

**Verify:** `python -m pytest dev/tests/test_timing_integrator.py dev/tests/test_fetch_timing.py -v`

---

## Step 4: Add structured observability logging

**Files:** `boundary_queue.py`, `timing_integrator.py`, `orchestrator.py`, `session.py`

`PipelineLogger` already has `boundary_event()` and `timing_event()` methods. Pass logger to sub-modules.

**BoundaryQueue** — add optional `logger` param to `__init__`:
- `replace_future`: log num_locked, num_new, num_merged
- `pop_next`: log frame_position, metadata_present, remaining count

**TimingIntegrator** — add optional `logger` param to `__init__`:
- `update()`: log num_boundaries, durations_count, playback_time
- `_tick()`: log success/skip/failure of periodic refresh

**CaptureOrchestrator.__init__** — pass `self._logger` to BoundaryQueue and TimingIntegrator.

**`_on_segment_complete`** — log metadata source ("popped_boundary" vs "peek_next") and song_title.

**`_start_encoder` `_spawn` closure** — log PcmAccumulator drain: buffer_chunks, drain_ms.

**session.py `_capture_track_changed`** — print timestamped track change message.

**Verify:** `python -m pytest dev/tests/test_boundary_queue.py dev/tests/test_timing_integrator.py dev/tests/test_orchestrator.py -v`

---

## Step 5: Optimize PcmAccumulator lock (audio quality)

**File:** `src/dreamsync/capture/orchestrator.py`, `dev/tests/test_orchestrator.py`

`attach_encoder` holds the lock during drain, blocking the consumer thread if FFmpeg stdin blocks. Use two-phase attach:

```
Phase 1 (under lock): swap buffer list, keep encoder=None (still buffering)
Phase 2 (no lock):    drain old buffer to encoder
Phase 3 (under lock): drain any interim writes, set encoder, set attached event
```

This minimizes lock hold time. Consumer thread continues buffering during drain.

**Tests to update:** existing `TestPcmAccumulator` should still pass. Add `test_concurrent_write_during_attach`.

**Verify:** `python -m pytest dev/tests/test_orchestrator.py -v -k "TestPcmAccumulator"`

---

## Step 6: Buffer fullness instrumentation

**File:** `src/dreamsync/capture/orchestrator.py`

- In `_consumer_loop` at drift-check interval: log `buffer_status` (pending_chunks, full flag)
- In `_producer_loop` when buffer is full: log `backpressure` warning

This adds no behavioral changes — purely diagnostic for the audio quality issue.

**Verify:** `python -m pytest dev/tests/test_orchestrator.py -v`

---

## Step 7: Full regression test

```bash
python -m pytest dev/tests/ -v
python -m pytest tests/ -v
```

---

## Execution Order

Steps 1, 2, 3, 5 are independent. Step 4 depends on 2+3 (parameter names). Step 6 depends on 4 (logger plumbing). Step 7 is final gate.

| Step | Bug | Risk | Key files |
|------|-----|------|-----------|
| 1 | Drift correction | Low | `drift_detector.py` |
| 2 | Metadata flow | Medium | `orchestrator.py` (DynamicSplitProcessor, callbacks) |
| 3 | Per-song metadata | Low | `session.py`, `timing_integrator.py` |
| 4 | Observability | Low | `boundary_queue.py`, `timing_integrator.py`, `orchestrator.py` |
| 5 | Lock contention | Medium | `orchestrator.py` (PcmAccumulator) |
| 6 | Buffer logging | Low | `orchestrator.py` (consumer/producer loops) |
| 7 | Regression | — | All tests |

---

## Analysis Evidence

From `out/capture-meta-4/logs/pipeline.jsonl`:
- 60 drift corrections applied (`"drift_corrections": 60` in pipeline.stopped)
- Every drift_check shows `drift_seconds: -0.5` at "correction" level
- No boundary or timing events logged (only drift_check + segment_complete)
- 3 segment_complete events for 1 song played

From `out/capture-meta-4/console.log`:
- Only 1 Spotify track detected: "Girl." by Yoh kamiyama (line 323)
- Frequent Spotify poll errors (timeouts, DNS, SSL handshake)
- "Song boundary detected [silence]" at line 4953 (mood system, not capture pipeline)
- All 3 segments named "Girl." — metadata from stale queue state

From sidecar JSON files:
- All 3 segments have identical song metadata (`songTitle: "Girl."`)
- Segment durations: 206.9s, 272.6s, 120.5s (sum = 600s)
- `sourceTimingData: null` — no timing data captured in metadata
