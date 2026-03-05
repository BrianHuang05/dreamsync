# C4: Segment Metadata, Startup Drift, Unicode Console

Three bugs found from live testing on 2026-03-05 (`out/capture-meta/`).

## Bug 1: First segment gets null metadata on track-change split

### Problem

When a track change triggers a split, the OLD segment (being finalized) gets `songTitle: null` while the NEW segment gets the correct metadata.

Observed in session 2 (10-min run):
- `segment_000001`: 286s, `songTitle: null, artist: null, album: null`
- `segment_000002`: 314s, `songTitle: "Otome Kaibou", artist: "Rib"`

### Root cause

`_get_segment_metadata()` at `orchestrator.py:652` peeks at the NEXT boundary in the queue:

```python
def _get_segment_metadata(self, segment_index: int) -> dict | None:
    entry = self._boundary_queue.peek_next()
    if entry is not None and entry.metadata is not None:
        return entry.metadata
    return None
```

When a track change fires:
1. `_capture_track_changed()` calls `on_track_change()` with the NEW track's metadata
2. `TimingIntegrator.update()` creates a `BoundaryEntry` at the frame where the NEW track will end, with `metadata={"song_title": new.name, ...}`
3. The split processor hits the boundary and calls `_on_segment_complete()` for the OLD segment
4. `_get_segment_metadata()` peeks at the queue — the boundary that was just consumed (old track's end) is gone, the next entry is for the NEW track's end
5. Result: OLD segment gets the NEW track's metadata, or null if no further boundary exists

The metadata is associated with **boundaries** (where songs end) rather than **segments** (what was playing).

### Fix

Track "current song" metadata separately in `CaptureOrchestrator`. When a track change arrives, save the NEW track's metadata as current, and use the PREVIOUS value for the segment being finalized.

**Files to modify:**

1. **`capture/orchestrator.py`** — `CaptureOrchestrator.__init__()`:
   - Add `self._current_song_meta: dict | None = None` (tracks what's currently playing)
   - Add `self._previous_song_meta: dict | None = None` (tracks what was playing before last track change)

2. **`capture/orchestrator.py`** — `CaptureOrchestrator.on_track_change()`:
   ```python
   def on_track_change(self, timing_data: dict) -> int:
       self._previous_song_meta = self._current_song_meta
       self._current_song_meta = timing_data.get("current_song")
       return self._timing.on_track_change(timing_data)
   ```

3. **`capture/orchestrator.py`** — `CaptureOrchestrator.update_timing()`:
   - Also update `_current_song_meta` from timing_data (for periodic refresh and initial fetch)

4. **`capture/orchestrator.py`** — `_get_segment_metadata()`:
   ```python
   def _get_segment_metadata(self, segment_index: int) -> dict | None:
       # If we have previous song metadata (from a track change that just happened),
       # use it for the segment that just completed
       if self._previous_song_meta is not None:
           meta = self._previous_song_meta
           self._previous_song_meta = None  # consume it
           return meta
       # Fallback: use current song metadata (for final segment on stop, or no track change)
       return self._current_song_meta
   ```

5. **`cli.py`** — In `_fetch_timing()` (line 1183), the periodic timing refresh already sends `current_song` — this will populate `_current_song_meta` on first poll, fixing the "no metadata at all" case for the initial segment.

### Test cases to add

- Segment finalized by track change gets OLD track's metadata
- Segment finalized by stop (Ctrl+C) gets CURRENT track's metadata
- First segment gets metadata from initial timing fetch (no track change needed)
- Multiple sequential track changes: each segment gets correct metadata

---

## Bug 2: Startup drift offset (~0.4s)

### Problem

Every drift check shows a consistent negative offset of -0.35 to -0.47 seconds. The offset appears on the very first check and never grows — it's a fixed startup delay, not actual clock drift.

### Root cause

In `orchestrator.py:332-333`:
```python
self._capture.start(device_name)
self._start_time = time.monotonic()
```

`_start_time` is set immediately after spawning the FFmpeg process, before it has initialized and delivered the first audio frame. The ~400ms FFmpeg startup delay means `elapsed_wall_seconds` is always ~0.4s ahead of actual audio data.

Drift calculation in `drift_detector.py`:
```
expected_frames = elapsed_wall_seconds * sample_rate
drift = actual_frames - expected_frames  (always negative by ~0.4s worth of frames)
```

### Fix

Defer `_start_time` until the first PCM frame arrives in the buffer.

**Files to modify:**

1. **`capture/pcm_buffer.py`** — `AudioBuffer`:
   - Add `self._first_frame_time: float | None = None`
   - In `put()`, on first call: `if self._first_frame_time is None: self._first_frame_time = time.monotonic()`
   - Add property `first_frame_time -> float | None`

2. **`capture/orchestrator.py`** — `_check_drift()` (line 597):
   - Use `self._buffer.first_frame_time` instead of `self._start_time` for elapsed calculation
   - Skip drift check if `first_frame_time` is None (no data yet)

### Test cases to add

- `AudioBuffer.first_frame_time` is None before first put, set after first put
- Drift measurement using `first_frame_time` eliminates startup offset
- Drift check gracefully skips when no frames received yet

---

## Bug 3: Unicode console display

### Problem

Console output: `Spotify: now playing '??????' by YOASOBI`
The song title 海のまにまに (6 CJK chars) becomes 6 question marks on Windows cp1252 terminal.

### Root cause

`_safe_track_msg()` in `cli.py:1114-1118`:
```python
enc = sys.stdout.encoding or "utf-8"
name = new.name.encode(enc, errors="replace").decode(enc, errors="replace")
```

On Windows, `sys.stdout.encoding` is `cp1252`. CJK characters can't be represented in cp1252, so `errors="replace"` substitutes each one with `?`. This is working as designed, but the UX is poor — every non-Latin character becomes a question mark.

### Fix (low priority)

Force UTF-8 for the track display message only, bypassing the terminal encoding:

```python
def _safe_track_msg(new):
    try:
        sys.stdout.buffer.write(f"Spotify: now playing '{new.name}' by {new.artist}\n".encode("utf-8"))
        sys.stdout.buffer.flush()
        return None  # already printed
    except Exception:
        enc = sys.stdout.encoding or "utf-8"
        name = new.name.encode(enc, errors="replace").decode(enc, errors="replace")
        artist = new.artist.encode(enc, errors="replace").decode(enc, errors="replace")
        return f"Spotify: now playing '{name}' by {artist}"
```

Or, simpler: set `PYTHONIOENCODING=utf-8` in the environment before subprocess start, or call `sys.stdout.reconfigure(encoding="utf-8")` at CLI startup.

**Note**: Windows Terminal (default on Win 11) supports UTF-8 natively. The issue only affects legacy `cmd.exe` or older terminal emulators. A pragmatic fix is to call `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` once at the top of `main()`.

### Files to modify

1. **`cli.py`** — Near top of `main()`, add:
   ```python
   if sys.platform == "win32":
       try:
           sys.stdout.reconfigure(encoding="utf-8", errors="replace")
       except Exception:
           pass
   ```

This fixes all Unicode output globally, not just the track message.

---

## Implementation order

1. Bug 1 (metadata) — highest impact, causes incorrect output files
2. Bug 2 (drift) — moderate impact, causes false drift warnings
3. Bug 3 (unicode) — low impact, cosmetic only
