 # Streaming Show Pipeline: Capture → Analyze → Compile → Play → Purge

## Context

Today the capture pipeline and the show pipeline are **separate, sequential workflows**:

1. **Capture** (`govee-live --capture --spotify`) — records system audio as per-song MP3s in real time
2. **Play** (`pipeline <dir>` or `play <dir>`) — batch-processes a directory of MP3s: analyze → compile → play

There is no way to run these concurrently. You capture a session, stop, then play it back. This means:
- No light shows during the capture session (unless using `govee-live`'s reactive mode, which is unrelated to compiled shows)
- You must wait for the full capture to finish before any analysis begins
- The rotating buffer (`--capture-buffer`) can't know which files have already been played

**Goal:** A streaming pipeline where each MP3, as it's finalized by the capture system, flows through analyze → compile → play → purge automatically. All stages run concurrently: while song N plays back with its compiled show, song N+1 is being compiled, song N+2 is being analyzed, and song N+3 is being captured.

### Prerequisite: `play --dry-run` (test 26)

The `play` command currently requires `--config devices.yaml` and calls `load_device_config` → `detect_all_devices` → `build_multi_adapter`. Without real Govee devices on the LAN, this fails at device detection. For testing without hardware:

- Add a `--dry-run` flag to `play` that creates a **null adapter** (accepts `send_frame` calls, does nothing)
- This enables test 26 (audio playback without devices) and local development of the streaming pipeline
- The null adapter must satisfy the same interface as `MultiGoveeLanAdapter` (duck-typed: `activate`, `deactivate`, `send_frame`, `devices` attribute)

## Audio Routing

Capture and playback use **separate audio streams** and can run simultaneously:

```
Spotify → CABLE Input (VB-Cable) → CABLE Output → FFmpeg capture (system default)
                                                    ↓
                                              MP3 files on disk
                                                    ↓
AudioPlayer → aux output device (non-default) → speaker system (analog cable)
```

- **Capture** reads from VB-Cable (the system default playback device)
- **Playback** writes to a specific `--audio-device` (aux output) that is NOT the system default
- No feedback loop: playback audio goes to the physical speaker, not back into VB-Cable
- This means playback can happen **concurrently** with capture — no need to defer

### Device Contention: Govee Lights

`govee-live` drives lights reactively (real-time BPM/mood → lighting). The show player drives lights from a pre-compiled timeline. These two cannot write to the same devices simultaneously.

**Decision:** The streaming pipeline command (`session --pipeline`) is a **separate mode** from `govee-live`. It does NOT run reactive lighting. Instead it:
1. Captures audio via FFmpeg (same as `govee-live --capture`)
2. As each MP3 is finalized: analyze → compile in background
3. As each show becomes ready: play audio + drive lights from the compiled timeline
4. Purge played files from disk

The Govee devices are driven exclusively by the show player — no reactive mode conflict.

## Architecture

### Data Flow

```
Spotify plays music (system audio → VB-Cable → FFmpeg capture)
        |
        v
CaptureOrchestrator (FFmpeg → PCM → MP3)
        |
        | on_segment_saved(mp3_path, metadata)
        v
ShowPipelineWorker (thread pool: max_workers=2)
        |
        ├── analyze_song(mp3_path) → SongStructure
        ├── compile_show(structure) → ShowTimeline
        ├── enqueue (mp3_path, timeline) to ready_queue
        |
        v
ShowPlaybackConsumer (dedicated thread)
        |
        ├── blocks on ready_queue.get()
        ├── AudioPlayer(mp3_path, device=aux_output) → speakers
        ├── ShowPlaybackRuntime(timeline, multi_adapter) → Govee devices
        ├── tick loop until song ends
        ├── purge mp3 + sidecar
        └── loop back to queue.get()
```

### Key Insight: `on_segment_saved` is the Hook

`CaptureOrchestrator` already has an `on_segment_saved` callback that fires after each MP3 + sidecar is written to disk. Today it's used only for logging. The streaming pipeline plugs into this same callback to trigger analysis/compilation without modifying the capture pipeline itself.

### Concurrency Model

```
Thread/Process          Responsibility                    Audio path
──────────────────────  ────────────────────────────────  ──────────────
Main thread             Capture loop (PCM read + split)   VB-Cable input
Finalize threads (N)    Encoder wait + sidecar write      (none)
Pipeline workers (2)    Analyze + compile                 (none)
Playback thread         AudioPlayer + ShowPlaybackRuntime aux output → speakers
```

All four layers run concurrently. The pipeline workers and playback thread are **new**. Everything else already exists.

### Timing: Analysis Latency vs Song Duration

Analysis takes ~10-20s per song. A typical song is 3-5 minutes. With `max_workers=2`, the pipeline can analyze song N+1 while song N is playing — there's a ~3 minute buffer. The first song has a cold-start delay (no show ready yet), which is handled by:
- **Option A:** Skip the first song (start playback from song 2, after song 1's show is compiled)
- **Option B:** Play the first song with reactive lighting, then switch to compiled shows
- **Option C:** Buffer the first 1-2 songs before starting playback (accept a ~20-40s delay)

**Decision: Option C** — the playback consumer waits for the first show to be ready, then plays continuously. The ~20s startup delay is acceptable since the user is already listening to music via Spotify.

## Files to Create

| File | Purpose |
|------|---------|
| `src/dreamsync/show_pipeline_worker.py` | `ShowPipelineWorker` — background analyze + compile on segment save |
| `src/dreamsync/show_playback_consumer.py` | `ShowPlaybackConsumer` — dedicated playback thread, reads from ready queue |
| `src/dreamsync/output/null_adapter.py` | `NullMultiAdapter` — no-op adapter for `--dry-run` |
| `dev/tests/test_show_pipeline_worker.py` | Worker unit tests |
| `dev/tests/test_show_playback_consumer.py` | Consumer unit tests |
| `dev/tests/test_null_adapter.py` | Null adapter interface compliance tests |

## Files to Modify

| File | Change |
|------|--------|
| `src/dreamsync/cli.py` | Add `--dry-run` to `play`; add `session --pipeline` mode; wire worker + consumer |
| `src/dreamsync/capture/orchestrator.py` | No changes — uses existing `on_segment_saved` callback |
| `src/dreamsync/local_session.py` | No changes — `LocalShowSession.run()` reused by the consumer |

## Design

### NullMultiAdapter

```python
class NullMultiAdapter:
    """No-op device adapter for --dry-run / audio-only testing."""

    def __init__(self) -> None:
        self.devices = []
        self._frames_sent = 0

    def activate(self, brightness: int = 100) -> None:
        pass

    def deactivate(self) -> None:
        pass

    def send_frame(self, t, intent, beat=False, params=None) -> bool:
        self._frames_sent += 1
        return True
```

### ShowPipelineWorker

Plugs into `CaptureOrchestrator.on_segment_saved`. Runs analysis and compilation in a thread pool, pushing results to a `queue.Queue` for the playback consumer.

```python
class ShowPipelineWorker:
    """Background worker: analyze + compile MP3s as they're captured.

    Ready tracks are pushed to a queue.Queue for the playback consumer.
    """

    def __init__(
        self,
        cache: ShowCache,
        profile: ProfileConfig | None = None,
        sample_rate: int = 44100,
        max_workers: int = 2,
        ready_queue: queue.Queue | None = None,  # shared with ShowPlaybackConsumer
        debug: bool = False,
    ) -> None: ...

    def on_segment_saved(self, mp3_path: str, metadata: dict) -> None:
        """Callback for CaptureOrchestrator — submits work to thread pool."""

    def pending_count(self) -> int:
        """Number of tracks still being processed."""

    def stats(self) -> dict:
        """Return processed/failed/pending counts."""

    def shutdown(self) -> None:
        """Shut down the thread pool."""
```

Internal flow for `on_segment_saved`:
1. Submit to `ThreadPoolExecutor(max_workers)`
2. Worker calls `analyze_song(mp3_path)`
3. Worker calls `cached_compile_show(structure, profile, cache=cache, track_id=...)`
4. Worker calls `ready_queue.put((mp3_path, timeline))` — unblocks the consumer
5. If analysis or compilation fails, log error, skip track (do NOT enqueue)

### ShowPlaybackConsumer

Dedicated thread that blocks on the ready queue and plays each show as it arrives.

```python
class ShowPlaybackConsumer:
    """Playback thread: dequeue ready shows, play audio + drive lights.

    Runs in a dedicated thread. Blocks on ready_queue.get() between tracks.
    After each track, optionally purges files from disk.
    """

    def __init__(
        self,
        ready_queue: queue.Queue,  # shared with ShowPipelineWorker
        multi_adapter,             # MultiGoveeLanAdapter or NullMultiAdapter
        *,
        sample_rate: int = 44100,
        audio_device: int | None = None,  # aux output device ID
        purge: bool = False,              # delete files after playback
        debug: bool = False,
    ) -> None: ...

    def run(self, stop_event: threading.Event) -> dict:
        """Main loop — blocks on queue, plays tracks, purges. Returns summary."""

    def tracks_played(self) -> int: ...
    def tracks_purged(self) -> int: ...
```

Internal flow for `run`:
```python
while not stop_event.is_set():
    try:
        mp3_path, timeline = ready_queue.get(timeout=1.0)
    except queue.Empty:
        continue

    # Play
    player = AudioPlayer(mp3_path, device=audio_device)
    runtime = ShowPlaybackRuntime(timeline, multi_adapter)
    multi_adapter.activate(brightness=100)
    player.play()

    while not stop_event.is_set() and not player.finished:
        runtime.tick(player.position_seconds)
        time.sleep(0.005)

    player.stop()

    # Purge
    if purge:
        _purge_files(mp3_path)

    ready_queue.task_done()
```

### CLI Wiring

#### `play --dry-run`

```python
if args.dry_run:
    from dreamsync.output.null_adapter import NullMultiAdapter
    multi_adapter = NullMultiAdapter()
else:
    configs = load_device_config(config_path)
    detected = detect_all_devices(configs)
    multi_adapter = build_multi_adapter(detected, ...)
```

When `--dry-run` is set, `--config` becomes optional (not required).

#### `session --pipeline` (new mode)

The streaming pipeline is a new mode on the `session` command (not `govee-live`, since there's no reactive lighting):

```python
# In session handler, when --pipeline is set:
import queue
from dreamsync.show_pipeline_worker import ShowPipelineWorker
from dreamsync.show_playback_consumer import ShowPlaybackConsumer

ready_queue = queue.Queue()

# 1. Create pipeline worker
pipeline_worker = ShowPipelineWorker(
    cache=ShowCache(args.cache_dir),
    profile=profile,
    ready_queue=ready_queue,
    debug=args.debug,
)

# 2. Create capture orchestrator with worker as callback
capture_orch = CaptureOrchestrator(
    config=orch_cfg,
    on_segment_saved=pipeline_worker.on_segment_saved,
)

# 3. Create playback consumer
consumer = ShowPlaybackConsumer(
    ready_queue=ready_queue,
    multi_adapter=multi_adapter,
    audio_device=args.audio_device,  # aux output
    purge=args.capture_buffer > 0,
    debug=args.debug,
)

# 4. Start all — capture on main thread, consumer on background thread
consumer_thread = threading.Thread(target=consumer.run, args=(stop_event,), daemon=True)
consumer_thread.start()
capture_orch.start()

# Main capture loop runs here (blocking)...
# On Ctrl+C:
capture_orch.stop()
pipeline_worker.shutdown()
stop_event.set()  # signals consumer to drain and exit
consumer_thread.join(timeout=30)
```

### Purge After Playback

`ShowPlaybackConsumer` handles purge directly after each track finishes:

```python
def _purge_files(self, mp3_path: Path) -> None:
    """Delete MP3 + sidecar + analysis files after playback."""
    for suffix in [".mp3", ".json", ".analysis.json"]:
        p = mp3_path.with_suffix(suffix) if suffix != ".mp3" else mp3_path
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
```

This is opt-in via the `purge` flag, which is enabled when `--capture-buffer` is set.

---

## Steps

### Step 1: `NullMultiAdapter` + `play --dry-run`

**Prerequisites:** None.

**Deliverables:**
- `NullMultiAdapter` class in `src/dreamsync/output/null_adapter.py`
- `--dry-run` flag on `play` command (makes `--config` optional)
- `TestNullAdapter` in `dev/tests/test_null_adapter.py`

**Implementation details:**

`NullMultiAdapter`:
- Satisfies `MultiGoveeLanAdapter` duck-type interface
- `activate()` / `deactivate()` are no-ops
- `send_frame()` increments counter, returns `True`
- `devices` attribute is empty list (so renderer mode-set loops are safe)

CLI changes in `play` handler:
- Add `--dry-run` flag: `play_cmd.add_argument("--dry-run", action="store_true", help="Audio only, no device output.")`
- When `--dry-run`, skip device setup, create `NullMultiAdapter()` instead
- Make `--config` not required when `--dry-run` is set (post-parse validation)

**Tests (6):**
1. `test_activate_deactivate_noop` — no exception
2. `test_send_frame_returns_true` — always returns True
3. `test_send_frame_counts` — `_frames_sent` increments
4. `test_devices_empty_list` — `adapter.devices == []`
5. `test_compatible_with_show_runtime` — `ShowPlaybackRuntime(timeline, NullMultiAdapter())` ticks without error
6. `test_compatible_with_local_session` — `LocalShowSession(NullMultiAdapter(), ...)` can be constructed

**Completion criteria:**
- `python -m dreamsync play song.mp3 --dry-run` plays audio without devices (test 26)
- All 6 tests pass

**Verify:**
```bash
python -m pytest dev/tests/test_null_adapter.py -v
python -m dreamsync play --help | grep dry-run

# Test 26: audio playback without devices
python -m dreamsync play out/capture-boundary-2/2026-03-06_22-51-49_Pretty\ Patterns_-_Cloudy\ Hollow.mp3 --dry-run --debug
```

---

### Step 2: `ShowPipelineWorker` class + unit tests

**Prerequisites:** None (standalone, only depends on existing `analyze_song`, `cached_compile_show`, `ShowCache`).

**Deliverables:**
- `ShowPipelineWorker` class in `src/dreamsync/show_pipeline_worker.py`
- `TestShowPipelineWorker` in `dev/tests/test_show_pipeline_worker.py`

**Implementation details:**

```python
import queue
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

class ShowPipelineWorker:
    def __init__(self, cache, profile=None, sample_rate=44100,
                 max_workers=2, ready_queue=None, debug=False):
        self._cache = cache
        self._profile = profile
        self._sample_rate = sample_rate
        self._debug = debug
        self._ready_queue = ready_queue or queue.Queue()

        self._executor = ThreadPoolExecutor(max_workers=max_workers,
                                            thread_name_prefix="show-pipeline")
        self._lock = threading.Lock()
        self._futures: list[Future] = []
        self._processed: int = 0
        self._errors: list[tuple[Path, str]] = []

    def on_segment_saved(self, mp3_path: str, metadata: dict) -> None:
        """CaptureOrchestrator callback — submits analyze+compile to pool."""
        future = self._executor.submit(self._process, Path(mp3_path), metadata)
        with self._lock:
            self._futures.append(future)

    def _process(self, mp3_path: Path, metadata: dict) -> None:
        try:
            structure = analyze_song(mp3_path, sample_rate=self._sample_rate)
            track_id = path_based_track_id(mp3_path)
            timeline, _ = cached_compile_show(
                structure, self._profile, cache=self._cache, track_id=track_id)
            self._ready_queue.put((mp3_path, timeline))
            with self._lock:
                self._processed += 1
            if self._debug:
                print(f"[pipeline] Ready: {mp3_path.name} ({len(timeline.cues)} cues)")
        except Exception as exc:
            with self._lock:
                self._errors.append((mp3_path, str(exc)))
            if self._debug:
                print(f"[pipeline] Failed: {mp3_path.name}: {exc}")

    @property
    def ready_queue(self) -> queue.Queue:
        return self._ready_queue

    def pending_count(self) -> int:
        with self._lock:
            return sum(1 for f in self._futures if not f.done())

    def stats(self) -> dict:
        with self._lock:
            return {
                "processed": self._processed,
                "errors": len(self._errors),
                "pending": sum(1 for f in self._futures if not f.done()),
            }

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False)
```

**Tests (12):**
1. `test_on_segment_saved_triggers_processing` — mock analyze_song + compile, verify item appears on ready_queue
2. `test_ready_queue_empty_initially` — no submissions → queue empty
3. `test_ready_queue_receives_items_in_order` — 3 songs submitted sequentially → dequeued in order
4. `test_ready_queue_unblocks_consumer` — consumer thread blocked on `queue.get()` is unblocked
5. `test_analysis_failure_logged_not_fatal` — analyze_song raises → error recorded, nothing enqueued, worker continues
6. `test_compile_failure_logged_not_fatal` — compile raises → error recorded, nothing enqueued
7. `test_pending_count_tracks_in_flight` — submit 3, pending_count > 0 before completion
8. `test_stats_reflects_processed_and_errors` — process 2 good + 1 bad → stats correct
9. `test_cache_hit_skips_analysis` — pre-populate cache → no analyze_song call (compile returns cached)
10. `test_multiple_concurrent_submissions` — submit 5 rapidly, all end up on queue
11. `test_shutdown_idempotent` — shutdown() twice without error
12. `test_custom_ready_queue_used` — pass explicit queue.Queue, verify items go there

Note: All tests mock `analyze_song` and `cached_compile_show` to avoid real audio processing.

**Completion criteria:**
- All 12 tests pass
- Worker processes segments in background, pushes to queue without blocking the caller

**Verify:**
```bash
python -m pytest dev/tests/test_show_pipeline_worker.py -v
```

---

### Step 3: `ShowPlaybackConsumer` class + unit tests

**Prerequisites:** Step 1 (NullMultiAdapter for testing without devices).

**Deliverables:**
- `ShowPlaybackConsumer` class in `src/dreamsync/show_playback_consumer.py`
- `TestShowPlaybackConsumer` in `dev/tests/test_show_playback_consumer.py`

**Implementation details:**

```python
import queue
import threading
import time
from pathlib import Path

class ShowPlaybackConsumer:
    """Dedicated playback thread — blocks on ready_queue, plays shows, purges files."""

    def __init__(
        self,
        ready_queue: queue.Queue,
        multi_adapter,
        *,
        sample_rate: int = 44100,
        audio_device: int | None = None,
        purge: bool = False,
        debug: bool = False,
    ) -> None:
        self._queue = ready_queue
        self._adapter = multi_adapter
        self._sample_rate = sample_rate
        self._audio_device = audio_device
        self._purge = purge
        self._debug = debug

        self._tracks_played: int = 0
        self._tracks_purged: int = 0

    def run(self, stop_event: threading.Event) -> dict:
        """Main loop — blocks on queue, plays tracks, purges. Returns summary."""
        self._adapter.activate(brightness=100)

        try:
            while not stop_event.is_set():
                try:
                    mp3_path, timeline = self._queue.get(timeout=1.0)
                except queue.Empty:
                    continue

                self._play_one(mp3_path, timeline, stop_event)
                self._tracks_played += 1

                if self._purge:
                    self._purge_files(mp3_path)
                    self._tracks_purged += 1

                self._queue.task_done()
        finally:
            self._adapter.deactivate()

        return {
            "tracks_played": self._tracks_played,
            "tracks_purged": self._tracks_purged,
        }

    def _play_one(self, mp3_path, timeline, stop_event):
        from dreamsync.show.player import AudioPlayer
        from dreamsync.show.runtime import ShowPlaybackRuntime

        player = AudioPlayer(mp3_path, sample_rate=self._sample_rate,
                             device=self._audio_device)
        runtime = ShowPlaybackRuntime(timeline, self._adapter)

        if self._debug:
            print(f"[playback] Playing: {mp3_path.name} "
                  f"({timeline.duration:.1f}s, {len(timeline.cues)} cues)")

        player.play()
        try:
            while not stop_event.is_set() and not player.finished:
                runtime.tick(player.position_seconds)
                time.sleep(0.005)
        finally:
            player.stop()

        if self._debug:
            print(f"[playback] Finished: {mp3_path.name}")

    def _purge_files(self, mp3_path: Path) -> None:
        """Delete MP3 + sidecar + analysis after playback."""
        for path in [mp3_path,
                     mp3_path.with_suffix(".json"),
                     mp3_path.with_suffix(".analysis.json")]:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        if self._debug:
            print(f"[playback] Purged: {mp3_path.name}")
```

**Tests (10):**
1. `test_plays_track_from_queue` — enqueue one (mock mp3 + timeline), verify _play_one called
2. `test_blocks_until_item_available` — empty queue, consumer waits, then processes after put
3. `test_stop_event_exits_loop` — set stop_event, consumer exits even with items queued
4. `test_multiple_tracks_played_in_order` — enqueue 3, all played in FIFO order
5. `test_purge_deletes_files` — purge=True, verify mp3 + json + analysis.json deleted from tmp_path
6. `test_purge_false_retains_files` — purge=False, files still on disk after playback
7. `test_purge_missing_files_no_error` — purge files that don't exist, no exception
8. `test_summary_counts` — play 3, purge 3 → summary correct
9. `test_activates_and_deactivates_adapter` — verify activate called on start, deactivate on exit
10. `test_works_with_null_adapter` — NullMultiAdapter, full run without error

Note: Tests mock `AudioPlayer` and `ShowPlaybackRuntime` to avoid real audio.

**Completion criteria:**
- All 10 tests pass
- Consumer plays tracks as they arrive, purges after playback

**Verify:**
```bash
python -m pytest dev/tests/test_show_playback_consumer.py -v
```

---

### Step 4: Wire into `session --pipeline` CLI command

**Prerequisites:** Steps 1, 2, and 3.

**Deliverables:**
- `--pipeline` flag on `session` command (requires `--capture`)
- `--audio-device` flag on `session` (aux output for playback)
- `--purge` flag on `session` (delete files after playback)
- Concurrent capture + analyze + compile + play

**Implementation details:**

In `cli.py` `session` parser:
```python
session_cmd.add_argument("--pipeline", action="store_true",
    help="Stream captured songs through analyze → compile → play concurrently.")
session_cmd.add_argument("--audio-device", type=int, default=None,
    help="Output audio device ID for show playback (must differ from capture device).")
session_cmd.add_argument("--purge", action="store_true",
    help="Delete MP3 + sidecar after playback.")
```

In the `session` handler, when `--pipeline` is set:
```python
import queue
from dreamsync.show_pipeline_worker import ShowPipelineWorker
from dreamsync.show_playback_consumer import ShowPlaybackConsumer
from dreamsync.cache import ShowCache

ready_queue = queue.Queue()

# 1. Pipeline worker (analyze + compile in thread pool)
pipeline_worker = ShowPipelineWorker(
    cache=ShowCache(args.cache_dir),
    profile=profile,
    ready_queue=ready_queue,
    debug=args.debug_mood,
)

# 2. Capture orchestrator with worker as callback
capture_orch = CaptureOrchestrator(
    config=orch_cfg,
    on_segment_saved=pipeline_worker.on_segment_saved,
)

# 3. Playback consumer (plays shows on aux output)
consumer = ShowPlaybackConsumer(
    ready_queue=ready_queue,
    multi_adapter=multi_adapter,
    audio_device=args.audio_device,
    purge=args.purge,
    debug=args.debug_mood,
)

# 4. Start consumer thread + capture
consumer_thread = threading.Thread(
    target=consumer.run, args=(stop_event,),
    name="show-playback", daemon=True,
)
consumer_thread.start()
capture_orch.start()

# ... main capture loop ...

# On Ctrl+C:
capture_orch.stop()
pipeline_worker.shutdown()
# Consumer drains remaining queue items, then stop_event exits it
stop_event.set()
consumer_thread.join(timeout=60)
```

**Tests:**
- No new unit tests — CLI glue covered by manual verification
- Steps 2+3 unit tests cover the components

**Completion criteria:**
- `session --pipeline --capture --spotify --audio-device 5` runs the full concurrent pipeline
- Songs are analyzed/compiled in background as they're captured
- Playback starts after the first show is ready (~20s after first song finishes)
- `--purge` deletes files after playback
- Without `--pipeline`, session behavior is unchanged

**Verify:**
```bash
python -m dreamsync session --help | grep pipeline

# Dry-run (no devices, no Spotify)
python -m dreamsync session --config devices.yaml \
  --pipeline --capture --capture-dir out/pipeline-test \
  --dry-run --debug-mood

# Full test (requires Spotify + VB-Cable + devices + aux output)
python -m dreamsync session --config devices.yaml \
  --pipeline --capture --capture-dir out/pipeline-test \
  --capture-naming metadata --spotify \
  --audio-device 5 --purge --debug-mood
```

---

### Step 5: End-to-end validation

**Prerequisites:** Steps 1-4.

**Deliverables:**
- Manual test script covering the full pipeline
- Update `dev/NEXT_STEPS.md` with results

**Test plan:**

1. **Audio-only dry run (no devices, no Spotify):**
   ```bash
   # Pre-populate capture dir with existing MP3s
   cp out/capture-boundary-2/*.mp3 out/e2e-test/
   cp out/capture-boundary-2/*.json out/e2e-test/
   python -m dreamsync pipeline out/e2e-test/ --mode analyze --debug
   python -m dreamsync pipeline out/e2e-test/ --mode compile --debug
   python -m dreamsync play out/e2e-test/ --dry-run --debug
   ```
   Pass: Audio plays through all tracks, cue transitions in debug output.

2. **Concurrent pipeline with Spotify (full stack):**
   ```bash
   python -m dreamsync session --config devices.yaml \
     --pipeline --capture --capture-dir out/e2e-pipeline \
     --capture-naming metadata --spotify \
     --audio-device 5 --purge --debug-mood
   ```
   Pass: Songs captured, analyzed in background, shows play on aux output ~20s after each song finishes capture, files purged after playback.

3. **Verify purge:**
   ```bash
   ls out/e2e-pipeline/  # Should be empty or only contain in-progress files
   ```

**Completion criteria:**
- All validation tests pass
- Update `dev/NEXT_STEPS.md` to mark tests 23, 26, 30, 31 as complete

---

## Validation Tests Covered

| Test | Status | How |
|------|--------|-----|
| 23. Batch analysis | Ready | `analyze-dir` on capture output |
| 26. Audio playback (no devices) | Step 1 | `play --dry-run` |
| 30. Single file compile | Ready | `compile song.analysis.json --summary` |
| 31. Compile to JSON output | Ready | `compile song.analysis.json --output show.json` |
| Full pipeline | Step 4 | `session --pipeline --capture --spotify` |

---

## Summary

| Step | Deliverable | Tests | Dependencies |
|------|-------------|-------|-------------|
| 1 | `NullMultiAdapter` + `play --dry-run` | 6 | None |
| 2 | `ShowPipelineWorker` | 12 | None |
| 3 | `ShowPlaybackConsumer` | 10 | Step 1 |
| 4 | CLI wiring: `session --pipeline` | 0 (manual) | Steps 1-3 |
| 5 | End-to-end validation | 0 (manual) | Steps 1-4 |
| **Total** | | **28** | |

## Future Work (out of scope)

- **Live takeover mode** — pause reactive lighting mid-capture, play a compiled show, resume reactive (requires device-access arbitration)
- **Incremental analysis** — stream PCM directly to analyzer without waiting for MP3 finalization (saves ~song-duration of latency)
- **Web dashboard** — show pipeline status, queue depth, cache stats in a browser
- **Skip/replay controls** — keyboard input on the playback consumer thread (next track, replay, pause)
