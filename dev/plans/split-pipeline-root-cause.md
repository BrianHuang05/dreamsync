# Split Pipeline Root Cause Analysis

**Date:** 2026-03-05
**Symptom:** Generated MP3s don't correspond to songs — not at song boundaries, not contiguous, splits at arbitrary points.

---

## Root Cause: Empty Boundary Queue

Bug 1 from `capture-meta-log-analysis.md` (`queue.current` → `queue.currently_playing`) caused `_fetch_timing()` to fail on every tick. This meant `TimingIntegrator` never received timing data, so `BoundaryQueue` was **permanently empty** for the entire capture session.

### Why an empty queue produces garbage splits

`DynamicSplitProcessor.process_chunk()` (orchestrator.py:133) checks `self._queue.peek_next()` on every chunk:

- **No boundary →** all audio writes to the current encoder indefinitely (line 144–148)
- **No splits ever fire →** segment 0 accumulates the entire session's audio
- The only splits that occurred came from the (mostly broken) track-change callback, which placed boundaries at essentially random positions relative to actual song boundaries

### Why track-change callbacks alone can't fix it

When `_capture_track_changed` fires, it sends:

```python
capture_orchestrator.on_track_change({
    "song_durations": [new.duration_ms / 1000.0],
    "current_playback_time": 0.0,
})
```

`compute_boundaries([duration], 0.0, sr)` returns `[round(duration * sr)]` — a single boundary at **the END of the new song**, not at the track-change point.

The design assumes periodic refresh has already placed a boundary at the end of the old song (which gets locked within the 0.5s safety margin). Without periodic refresh, that boundary doesn't exist, so:

- No split at the A→B transition
- One split at the end of song B (current_frame + B's full duration)
- Result: segment 0 = all of A + all of B, segment 1 = all of C

### Compounding factors (Bugs 2 & 3)

The `charmap` UnicodeEncodeError (Bug 2) and callback exception propagation (Bug 3) caused most track-change callbacks to fail entirely, so even the wrong-position boundaries weren't placed consistently.

---

## Fix Status

All three bugs from `capture-meta-log-analysis.md` have been applied:

1. **`queue.currently_playing`** — fixed in `cli.py:1185`
2. **Encode-safe print** — fixed with `errors="replace"` in `cli.py:1142–1143`
3. **Callback isolation** — orchestrator called first in `cli.py:1156–1175`, `_orig` in separate try/except

With Bug 1 fixed, periodic refresh populates the boundary queue every 5s with correct song-end positions from the Spotify queue. `DynamicSplitProcessor` then splits at those positions as audio flows through.

---

## Remaining Gap: No Initial Timing Fetch

`start_periodic_timing()` schedules the first tick after `refresh_interval` (5.0s). For the first 5 seconds of capture, the boundary queue is empty. If a song boundary falls within that window, it will be missed.

**Suggested fix:** Call `_fetch_timing()` immediately in `start_periodic_timing()` before scheduling the first timer tick, or have `TimingIntegrator._schedule_next` do an immediate first call.

---

## Verification Plan

After fixes, re-run a 10-minute capture session with `--spotify --capture-naming metadata` and verify:

1. `pipeline.jsonl` shows successful `timing_refresh` events (not AttributeError)
2. Boundary queue is populated within first few seconds
3. MP3 segments align with Spotify track changes (±1s)
4. All segments have metadata (no timestamp-only fallback names)
5. Segment count matches song count (±1 for first/last partial segments)
