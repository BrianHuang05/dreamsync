# Capture Pipeline Bug Fixes: Naming, Silence, Immediate Splits

## Context

Four bugs reported from live capture sessions:

1. **Silence gaps in files** — especially first file, occasionally second
2. **First file named generically** — "segment_000001" instead of track name
3. **5-10 seconds of silence at end** of files 1-2
4. **Off-by-one naming** — each file named after the PREVIOUS track (JSON metadata is correct)

## Root Cause Analysis

### Bugs 2 & 4 (naming off-by-one)
The filename is determined in `_start_encoder()` at segment START time by calling `_get_segment_metadata()` → `peek_next()` on the boundary queue. This is fragile because:
- At startup (segment 0), no boundaries exist yet → generic name
- After a split, `peek_next()` returns the boundary that will END the new segment, whose metadata could be stale or represent the wrong song due to race conditions between the consumer thread, Spotify thread, and periodic timer thread — all of which modify the boundary queue concurrently via `replace_future()`

Meanwhile, the JSON sidecar uses `popped_meta` from the boundary that ENDED the segment (captured at finalization), which is always correct.

**Fix**: Don't determine the final filename at encoder start. Use a temp name, then rename after finalization when correct metadata is known.

### Bug 3 (silence at end)
When Spotify detects a track change (song A → song B), `_capture_track_changed` calls `on_track_change()` which only creates a boundary at `current_frame + new_song_duration` (end of song B). It does NOT create an immediate boundary at `current_frame` to split right at the transition.

The actual split relies on a boundary placed by the periodic timer (every 5s), which can be off by seconds due to Spotify polling lag (2s) and progress reporting inaccuracy.

**Fix**: On track change, insert an immediate boundary at `current_frame` with the OLD song's metadata to force an immediate split.

### Bug 1 (silence gaps)
Likely caused by FFmpeg/VB-Cable startup delay (first file) and brief audio source gaps. The immediate-split fix will reduce the amount of wrong audio captured at segment boundaries. Residual gaps from the audio source can't be fixed in the pipeline code.

## Implementation Plan

### 1. Add `remove_near()` to BoundaryQueue
**File**: `src/dreamsync/capture/boundary_queue.py`

Add method to remove boundaries within a frame window of a position (to prevent duplicate boundaries when inserting an immediate split):
```python
def remove_near(self, frame_position: int, window_frames: int) -> int:
    """Remove all boundaries within window_frames of frame_position. Returns count removed."""
```

### 2. Add `rename_to_metadata()` to FileNamer
**File**: `src/dreamsync/capture/file_namer.py`

Add method to rename an existing MP3 file based on metadata:
```python
def rename_to_metadata(self, old_path: str, metadata: dict | None) -> str:
    """Rename MP3 using metadata. Returns new path (or old_path if rename not needed)."""
```
- Only renames if `self._pattern == "metadata"` and valid artist/title present
- Uses `_resolve_collision()` for dedup
- Thread-safe (uses existing `_lock`)

### 3. Modify `on_track_change()` in TimingIntegrator
**File**: `src/dreamsync/capture/timing_integrator.py`

Before calling `self.update(timing_data)`:
1. Get `current_frame`
2. If `previous_song` is in `timing_data`:
   - Call `self._queue.remove_near(current_frame, 2 * self._sample_rate)` to clear stale nearby boundaries
   - Add an immediate `BoundaryEntry(frame_position=current_frame, metadata=previous_song)`
   - Increment `_segment_counter`
3. Then call `self.update(timing_data)` as before (places future boundaries for the new song)

### 4. Modify `_start_encoder()` and `_finalize_segment()` in Orchestrator
**File**: `src/dreamsync/capture/orchestrator.py`

**`_start_encoder`**: Remove metadata capture. Always pass `None` to `next_filename()` (generates timestamp-based temp name). Remove `_get_segment_metadata()` call.

**`_finalize_segment`**: After encoder finishes, before writing JSON sidecar:
- If `boundary_meta` has valid metadata and naming pattern is "metadata":
  - Rename MP3 from temp path to metadata-based path via `file_namer.rename_to_metadata()`
  - Use the new path for the JSON sidecar

### 5. Pass `previous_song` in session.py
**File**: `src/dreamsync/session.py`

In `_capture_track_changed`, include old track info:
```python
if old is not None:
    timing_data["previous_song"] = {
        "song_title": old.name,
        "artist": old.artist,
        "album": old.album,
    }
```

## Files Modified
1. `src/dreamsync/capture/boundary_queue.py` — add `remove_near()`
2. `src/dreamsync/capture/file_namer.py` — add `rename_to_metadata()`
3. `src/dreamsync/capture/timing_integrator.py` — immediate boundary in `on_track_change()`
4. `src/dreamsync/capture/orchestrator.py` — defer naming to finalization
5. `src/dreamsync/session.py` — pass `previous_song` data

## Verification
1. Run `python -m pytest dev/tests/ -v` — update any tests that break due to API changes
2. Run `python -m pytest tests/ -v` — ensure core tests still pass
3. Live test with Spotify capture session to verify:
   - Files are named after the correct (current) song
   - JSON metadata matches filename
   - Segments split immediately on track change (no 5-10s silence at end)
   - First file gets correct name (if track info is available from Spotify before first split)
