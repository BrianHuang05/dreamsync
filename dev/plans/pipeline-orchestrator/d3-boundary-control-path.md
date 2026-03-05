# D3 — Boundary Control Path

## Prerequisites

- D1 (Orchestrator Core) — `CaptureOrchestrator` shell with lifecycle methods
- `BoundaryQueue`, `TimingIntegrator`, `DriftDetector`, `SplitProcessor`, `segment_boundary.compute_boundaries` all implemented and tested

## Overview

Wire the timing and boundary control path. This path determines **where** the SplitProcessor splits the audio stream. Boundaries come from external timing data (Spotify queue) and are dynamically updated as new timing information arrives.

```
External Timing Source (Spotify)
        │
        ▼
  TimingIntegrator.update(timing_data)
        │
        ▼
  segment_boundary.compute_boundaries(durations, playback_time, sample_rate)
        │
        ▼
  BoundaryQueue.replace_future(entries, current_frame)
        │                              │
        ▼                              ▼
  SplitProcessor reads               DriftDetector.measure_and_correct()
  next boundary from queue            adjusts future boundaries
```

---

## Implementation Items

### 3.1 Dynamic boundary feeding to SplitProcessor

The current `SplitProcessor` takes a static `boundaries: list[int]` at construction. For the orchestrator, boundaries arrive dynamically from the `BoundaryQueue`. Two approaches:

**Approach: Adapter pattern** — Create a `DynamicSplitProcessor` wrapper or modify `SplitProcessor` to pull boundaries from the queue instead of a static list.

```python
class DynamicSplitProcessor:
    """SplitProcessor that reads boundaries from a BoundaryQueue."""

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

    def process_chunk(self, chunk: bytes) -> None:
        """Route chunk, checking BoundaryQueue for the next split point."""
        chunk_frames = len(chunk) // BYTES_PER_FRAME
        offset = 0

        while chunk_frames > 0:
            next_boundary = self._queue.peek_next()

            if next_boundary is None:
                # No boundaries — write everything to current encoder
                self._encoder.write(chunk[offset:])
                self._current_frame += chunk_frames
                break

            frames_to_boundary = next_boundary.frame_position - self._current_frame

            if frames_to_boundary <= 0:
                # Boundary at or behind current position — rotate
                self._queue.pop_next()
                self._rotate_encoder()
                continue

            if chunk_frames <= frames_to_boundary:
                # Entire chunk fits before boundary
                self._encoder.write(chunk[offset:])
                self._current_frame += chunk_frames
                chunk_frames = 0
            else:
                # Chunk spans boundary — split
                split_bytes = frames_to_boundary * BYTES_PER_FRAME
                self._encoder.write(chunk[offset:offset + split_bytes])
                self._current_frame += frames_to_boundary
                offset += split_bytes
                chunk_frames -= frames_to_boundary
                self._queue.pop_next()
                self._rotate_encoder()
```

### 3.2 TimingIntegrator wiring

In `CaptureOrchestrator.__init__()`:

```python
self._boundary_queue = BoundaryQueue(
    safety_margin_frames=config.safety_margin_frames,
)

self._timing = TimingIntegrator(
    boundary_queue=self._boundary_queue,
    get_current_frame=lambda: self._buffer.frames_processed,
    sample_rate=config.sample_rate,
    refresh_interval=config.timing_refresh_interval,
)
```

### 3.3 External timing API

The orchestrator exposes methods for the session layer to push timing data:

```python
def update_timing(self, timing_data: dict) -> int:
    """Push new timing data (e.g. from Spotify queue).

    Parameters
    ----------
    timing_data:
        Dict with keys: song_durations (list[float]),
        current_playback_time (float), current_song (dict).

    Returns
    -------
    Number of new boundaries applied.
    """
    return self._timing.update(timing_data)

def on_track_change(self, timing_data: dict) -> int:
    """Handle a track change with immediate boundary refresh."""
    return self._timing.on_track_change(timing_data)

def start_periodic_timing(self, fetch_fn: Callable[[], dict | None]) -> None:
    """Start background periodic timing refresh."""
    self._timing.start_periodic_refresh(fetch_fn)
```

### 3.4 DriftDetector wiring

In `CaptureOrchestrator.__init__()`:

```python
self._drift = DriftDetector(
    sample_rate=config.sample_rate,
    boundary_queue=self._boundary_queue,
    warning_threshold=config.drift_warning_threshold,
    correction_threshold=config.drift_correction_threshold,
    critical_threshold=config.drift_critical_threshold,
)
```

### 3.5 Periodic drift measurement

Add drift measurement to the consumer loop on a periodic basis (every N chunks):

```python
DRIFT_CHECK_INTERVAL_CHUNKS = 100  # every ~10 seconds at 100ms chunks

def _consumer_loop(self) -> None:
    chunks_since_drift_check = 0

    while True:
        chunk = self._buffer.get()
        if chunk is None:
            break
        self._split.process_chunk(chunk)

        chunks_since_drift_check += 1
        if chunks_since_drift_check >= DRIFT_CHECK_INTERVAL_CHUNKS:
            self._check_drift()
            chunks_since_drift_check = 0

    self._split.finish()

def _check_drift(self) -> None:
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
```

### 3.6 BoundaryQueue lock updates

Call `boundary_queue.update_locks(current_frame)` periodically (in consumer loop) so that approaching boundaries get locked before TimingIntegrator can modify them:

```python
self._boundary_queue.update_locks(self._buffer.frames_processed)
```

---

## Timing Data Format

The orchestrator expects timing data dicts with this shape (matching `TimingIntegrator.update()`):

```python
{
    "song_durations": [180.0, 240.0, 195.0],  # seconds: current + upcoming
    "current_playback_time": 45.0,              # seconds into current song
    "current_song": {                           # metadata for filename/sidecar
        "song_title": "Song Name",
        "artist": "Artist Name",
        "album": "Album Name",
    }
}
```

This is produced by the Spotify queue watcher and passed through `session.py`.

---

## File Modified

```
src/dreamsync/capture/orchestrator.py  (add DynamicSplitProcessor, timing/drift wiring)
```

## Tests

Added to `dev/tests/test_orchestrator.py`:

- [x] `test_dynamic_split_no_boundaries` — chunks pass through without splitting when queue is empty
- [x] `test_dynamic_split_at_boundary` — chunk at exact boundary triggers encoder rotation
- [x] `test_dynamic_split_spanning_boundary` — chunk spanning a boundary is split correctly
- [x] `test_timing_update_adds_boundaries` — `update_timing()` populates the boundary queue
- [x] `test_track_change_immediate_refresh` — `on_track_change()` updates boundaries immediately
- [x] `test_drift_check_acceptable` — drift < warning threshold logs debug, no correction
- [x] `test_drift_check_correction` — drift > correction threshold adjusts future boundaries
- [x] `test_boundary_lock_prevents_modification` — locked boundaries survive `replace_future()`
- [x] `test_safety_margin_respected` — boundaries within 0.5s of current frame are locked

---

## Passing Criteria

- [x] `DynamicSplitProcessor` reads boundaries from `BoundaryQueue` instead of a static list
- [x] `update_timing()` converts Spotify queue data to frame boundaries and populates the queue
- [x] `on_track_change()` triggers immediate boundary refresh
- [x] `start_periodic_timing()` starts a background timer that refreshes boundaries every N seconds
- [x] `DriftDetector` measures drift every ~10 seconds and auto-corrects when threshold exceeded
- [x] Safety margin prevents modification of boundaries within 0.5s of the current frame
- [x] Boundary queue updates are thread-safe (TimingIntegrator may update from timer thread while consumer reads)
- [x] All D3 tests pass (17/17)
