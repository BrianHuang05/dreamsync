# Fix Plan: Residual Fragment + outputFile Bugs (Pre-Test 18 Rerun)

Two bugs remain from the test 18 run on 2026-03-06. Boundary detection itself is correct — the issues are a race condition creating tiny residual files and the JSON sidecar showing a stale temp path.

---

## Bug 1: Residual sub-1s fragment files at song boundaries

### Prerequisites

- Boundary detection is working correctly (confirmed in test 18 run on 2026-03-06).
- Capture naming off-by-one fix is in place: `_start_encoder()` uses temp names, `_finalize_segment()` renames (commit `454e6a3`).
- `on_track_change()` inserts immediate boundaries with old-song metadata and clears nearby stale boundaries via `remove_near()` (commit `454e6a3`).
- All existing tests pass: `python -m pytest tests/ dev/tests/ -v` (569 + 315+ tests).

### Goals

1. Eliminate tiny residual fragment files (< 5s) created at song transitions by stale periodic timer data.
2. Ensure exactly one MP3 file per song (no spurious extras between real tracks).
3. No regression in boundary accuracy — real song splits must still occur at the correct times.

### Problem

At some song transitions, a tiny residual file (~0.7s, ~17-19KB) is created between the real song files. The residual is named after the OLD song but contains audio from the start of the NEW song.

### Root Cause

Race between the periodic timer `_tick()` and `on_track_change()`:

```
1. Consumer pops periodic boundary → segment N finalized (correct)
2. _tick() fires with STALE Spotify data (old song, ~0.7s remaining)
3. _tick() → update() → replace_future() inserts boundary at current_frame + 0.7s
4. Consumer pops this boundary 0.7s later → tiny residual segment N+1
5. on_track_change() fires and corrects queue, but too late
```

When `on_track_change()` wins the race (fires before `_tick()`), no residual is created — the queue is correct from the start.

### Fix Strategy: Two-layer defense

Apply **both** a debounce in TimingIntegrator (prevents the bad boundary from being created) **and** a min-duration guard in CaptureOrchestrator (discards any residual that slips through).

#### Layer 1: Track-change debounce in TimingIntegrator

**File:** `src/dreamsync/capture/timing_integrator.py`

**Changes:**

1. Add instance state to track when `on_track_change()` last ran:

```python
# In __init__:
self._track_change_time: float = 0.0  # monotonic timestamp
self._track_change_debounce: float = 3.0  # seconds
```

2. Record the timestamp in `on_track_change()`:

```python
# At the top of on_track_change():
import time
self._track_change_time = time.monotonic()
```

3. Skip `_tick()` if a track change happened recently:

```python
def _tick(self) -> None:
    if not self._running or self._fetch_fn is None:
        return
    # Debounce: skip if on_track_change() fired recently
    if time.monotonic() - self._track_change_time < self._track_change_debounce:
        if self._logger is not None:
            self._logger.timing_event("tick.debounced")
        self._schedule_next()
        return
    # ... rest of existing _tick() logic
```

**Rationale:** When `on_track_change()` fires, it already places the correct immediate boundary and refreshes the queue with new song durations. A `_tick()` firing within the next few seconds would only have stale Spotify data. Suppressing it prevents the bad boundary from ever entering the queue.

**Testing:**

- Unit test: call `on_track_change()`, then immediately call `_tick()`. Assert `_tick()` is a no-op (queue unchanged).
- Unit test: call `on_track_change()`, wait > 3s (mock `time.monotonic`), then call `_tick()`. Assert `_tick()` processes normally.
- Unit test: call `_tick()` without any prior `on_track_change()`. Assert normal processing.

#### Layer 2: Minimum segment duration guard in CaptureOrchestrator

**File:** `src/dreamsync/capture/orchestrator.py`

**Changes:**

1. Add a config field for minimum segment duration:

```python
# In OrchestratorConfig:
min_segment_frames: int = 220_500  # 5s at 44.1kHz
```

2. In `_on_segment_complete()`, check duration before dispatching finalization:

```python
def _on_segment_complete(
    self,
    segment_index: int,
    start_frame: int,
    end_frame: int,
    popped_meta: dict | None = None,
) -> None:
    duration_frames = end_frame - start_frame

    # Discard residual fragments below minimum duration
    if duration_frames < self._config.min_segment_frames:
        self._logger.log(
            "INFO", "split", "segment_discarded.too_short",
            data={
                "segment_index": segment_index,
                "duration_frames": duration_frames,
                "min_frames": self._config.min_segment_frames,
            },
            frame_position=end_frame,
        )
        # Clean up the encoder (wait + delete temp file)
        self._discard_segment(segment_index)
        return

    # ... existing logic (increment counter, dispatch finalization thread)
```

3. Add the `_discard_segment()` helper:

```python
def _discard_segment(self, segment_index: int) -> None:
    """Discard a too-short segment: wait for encoder, delete temp file."""
    encoder = self._encoders.pop(segment_index, None)
    if encoder is None:
        return

    def _cleanup() -> None:
        try:
            encoder.wait(timeout=30.0)
            path = encoder.output_path
            if path and os.path.exists(path):
                os.unlink(path)
                self._logger.log(
                    "DEBUG", "output", "segment_discarded.file_deleted",
                    data={"path": path, "segment_index": segment_index},
                )
        except Exception as exc:
            self._logger.log(
                "WARNING", "output", "segment_discard.error",
                data={"error": str(exc), "segment_index": segment_index},
            )

    t = threading.Thread(
        target=_cleanup,
        name=f"discard-segment-{segment_index}",
        daemon=True,
    )
    t.start()
    with self._finalize_lock:
        self._finalize_threads.append(t)
```

**Note:** Add `import os` at the top of `orchestrator.py` (currently not imported).

**Rationale:** Even if the debounce fails (e.g., Spotify API latency > 3s, or an unforeseen race), any segment shorter than 5s is discarded. No real song segment should ever be that short — the shortest reasonable track is ~30s.

**Testing:**

- Unit test: `_on_segment_complete()` with `end_frame - start_frame = 1000` → verify no finalization thread is spawned and encoder temp file is deleted.
- Unit test: `_on_segment_complete()` with `end_frame - start_frame = 300_000` → verify normal finalization proceeds.
- Integration test: simulate a rapid double-boundary (0.7s apart) → verify only one output file is produced.

### Completion Criteria

All of the following must be true:

1. **Unit tests pass** — All new debounce and min-segment-guard tests pass (`dev/tests/test_timing_debounce.py`, `dev/tests/test_min_segment_guard.py`).
2. **No regressions** — Full test suite passes: `python -m pytest tests/ dev/tests/ -v`.
3. **Live validation (test 18 rerun)** — Run capture with 5+ songs and verify:
   - MP3 file count matches song count (±1 for first/last partial).
   - No output files shorter than 5 seconds exist in the capture directory.
   - Each MP3 contains approximately one song (audible spot-check).
   - Pipeline log shows `segment_discarded.too_short` or `tick.debounced` events at transitions where residuals previously occurred (proves the fix is active, not just lucky timing).
4. **No silent data loss** — No real song audio is discarded. The 5s threshold is well below the shortest expected track (~30s).

---

## Bug 2: `outputFile` in JSON sidecar shows pre-rename temp path

### Prerequisites

- Metadata-based file renaming is working: `_finalize_segment()` calls `rename_to_metadata()` and updates `mp3_path` (commit `454e6a3`).
- `_build_segment_metadata()` exists and constructs `SegmentMetadata` with an `output_file` field (line 854-888 in `orchestrator.py`).
- `MetadataWriter.write_sidecar()` serializes `output_file` as the `outputFile` JSON key (line 102 in `metadata_writer.py`).

### Goals

1. JSON sidecar `outputFile` field matches the actual filename on disk after rename.
2. When no rename occurs (timestamp naming mode), `outputFile` still shows the correct path.
3. No change to the rename logic itself — only the metadata reporting is fixed.

### Problem

The JSON sidecar's `outputFile` field shows `segment_000001.mp3` instead of the actual renamed filename (e.g., `Wasia Project_-_Is This What Love Is_.mp3`).

### Root Cause

In `_finalize_segment()` (line 651), `_build_segment_metadata()` is called **after** the rename, but `_build_segment_metadata()` reads `enc.output_path` (line 872-874) which still holds the original temp path — the encoder doesn't know about the rename.

### Fix

**File:** `src/dreamsync/capture/orchestrator.py`

**Change `_build_segment_metadata()` to accept an `output_file` parameter:**

```python
def _build_segment_metadata(
    self, segment_index: int, start_frame: int, end_frame: int,
    boundary_meta: dict | None = None,
    output_file: str | None = None,  # NEW parameter
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

    # Use the explicitly passed output_file (post-rename) if available,
    # otherwise fall back to the encoder's original path
    if output_file is None:
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
```

**Change `_finalize_segment()` to pass the renamed path:**

At line 651, change:
```python
# Before:
seg_meta = self._build_segment_metadata(
    segment_index, start_frame, end_frame, boundary_meta=boundary_meta,
)

# After:
seg_meta = self._build_segment_metadata(
    segment_index, start_frame, end_frame,
    boundary_meta=boundary_meta,
    output_file=mp3_path,  # post-rename path
)
```

`mp3_path` is already set to the renamed path by line 646 (`mp3_path = new_path`), so this naturally carries the correct value.

**Testing:**

- Unit test: mock encoder with `output_path="temp_segment_001.mp3"`, call `_build_segment_metadata()` with `output_file="Artist_-_Song.mp3"` → verify `seg_meta.output_file == "Artist_-_Song.mp3"`.
- Unit test: call `_build_segment_metadata()` without `output_file` → verify it falls back to `enc.output_path`.
- Integration test: run a segment through `_finalize_segment()` with metadata rename → verify JSON sidecar `outputFile` matches the renamed path on disk.

### Completion Criteria

All of the following must be true:

1. **Unit tests pass** — All new outputFile tests pass (`dev/tests/test_outputfile_metadata.py`).
2. **No regressions** — Full test suite passes: `python -m pytest tests/ dev/tests/ -v`.
3. **Live validation (test 18 rerun)** — For every JSON sidecar in the capture directory:
   - `outputFile` value matches the actual `.mp3` filename on disk (basename comparison).
   - No sidecar contains `segment_` prefix in `outputFile` when the corresponding MP3 was renamed to metadata format.

---

## Implementation Order

1. **Bug 2 first** (outputFile fix) — smallest change, no behavioral risk, easy to verify.
2. **Bug 1 Layer 1** (debounce) — add `_track_change_time` + debounce guard in `_tick()`.
3. **Bug 1 Layer 2** (min duration guard) — add `min_segment_frames` config + `_on_segment_complete()` guard + `_discard_segment()`.
4. **Run full test suite** — `python -m pytest tests/ dev/tests/ -v` — all existing tests must pass.
5. **Rerun test 18** — live boundary accuracy test with 5+ songs.

## Files Modified (Summary)

| File | Changes |
|------|---------|
| `capture/orchestrator.py` | Add `min_segment_frames` to config, add min-duration guard in `_on_segment_complete()`, add `_discard_segment()`, add `output_file` param to `_build_segment_metadata()`, pass `mp3_path` in `_finalize_segment()`, add `import os` |
| `capture/timing_integrator.py` | Add `_track_change_time` + `_track_change_debounce` fields, record timestamp in `on_track_change()`, skip `_tick()` if debounced |

## Test Files to Create/Modify

| File | Purpose |
|------|---------|
| `dev/tests/test_timing_debounce.py` | Unit tests for tick debounce after track change |
| `dev/tests/test_min_segment_guard.py` | Unit tests for short segment discard logic |
| `dev/tests/test_outputfile_metadata.py` | Unit tests for outputFile showing renamed path |
