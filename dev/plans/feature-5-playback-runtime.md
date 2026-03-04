# Feature 5 — Playback Runtime

**Status**: Done

---

## Overview

- The Playback Runtime replaces the v2 Director in a Spotify-connected session. Instead of reacting to audio frame-by-frame, it reads the current Spotify playback position, seeks into a pre-compiled `ShowTimeline`, and emits the corresponding `LightingIntent` to the existing v2 `SegmentRenderer` / `MultiGoveeLanAdapter` stack.
- When a track changes (detected by `SpotifyQueueWatcher.on_track_changed`), the runtime triggers `cached_compile_show()` — which either returns a cached timeline instantly or runs the full `analyze_song()` → `compile_show()` pipeline, then caches the result.
- Lookahead compilation: when the queue watcher fires `on_queue_updated`, upcoming tracks are compiled in a background thread so they're cached before they start playing.
- The runtime handles Spotify pauses, seeks, and track skips by re-syncing to the polled playback position on every tick.
- If Spotify is unavailable or a track can't be compiled, the session falls back to the v2 Director seamlessly (Feature 6 scope, but Feature 5 provides the switching interface).

---

## Pain Points

| Pain Point | Description |
|---|---|
| Spotify position drift | Spotify's `progress_ms` is polled every ~2s. Between polls, the runtime must interpolate position using wall-clock time. Drift accumulates and must be corrected on each poll without visible jumps |
| Analysis latency | `analyze_song()` takes 2–10s depending on song length. The first play of an uncached song will have a startup delay. Need to show status and fall back gracefully during compilation |
| No local audio | Unlike `run_show_playback()` (which plays an mp3), the Spotify runtime has no local audio — Spotify plays through its own app. The runtime only drives lights. No `AudioPlayer` needed |
| Thread safety | The Spotify watcher fires callbacks from its daemon thread. The main session loop ticks on the main thread. Shared state (current timeline, playback position) must be synchronized |
| Pause/seek handling | When Spotify is paused, the runtime should freeze (no frame emission). On seek, the timeline position must jump without triggering false cue transitions |
| Mp3 availability for analysis | `analyze_song()` needs an mp3 file. In a Spotify session, the user may not have a local mp3. The capture pipeline (Feature 2 C3) can provide captured songs, but the first play of a never-captured song has no mp3. For v3, we require capture to be enabled alongside the runtime, or fall back to v2 |

---

## Architecture

```
                    ┌──────────────────────────────────┐
                    │        run_session()              │
                    │                                    │
                    │   --spotify + --v3                 │
                    └───────────────┬──────────────────┘
                                    │
                    ┌───────────────┴──────────────────┐
                    │      SpotifyShowSession           │
                    │                                    │
                    │  SpotifyQueueWatcher               │
                    │    ├── on_track_changed →          │
                    │    │   _compile_for_track()        │
                    │    └── on_queue_updated →          │
                    │        _precompile_queue()         │
                    │                                    │
                    │  PositionInterpolator              │
                    │    ├── update(progress_ms, mono)   │
                    │    └── position_seconds → float    │
                    │                                    │
                    │  ShowPlaybackRuntime (existing)    │
                    │    └── tick(t) → bool              │
                    │                                    │
                    │  MultiGoveeLanAdapter (existing)   │
                    │    └── send_frame(t, intent, ...)  │
                    └──────────────────────────────────┘
```

### File Layout

```
src/dreamsync/
├── v3_session.py              # SpotifyShowSession, PositionInterpolator,
│                              # run_v3_session()
```

Single module. The session orchestrator is one concern — wiring Spotify events to the existing ShowPlaybackRuntime + cache + devices.

### Integration Points

- **Feature 1 (Spotify)** — `SpotifyQueueWatcher` provides `on_track_changed`, `on_playback_state_changed`, `on_queue_updated` callbacks. `PlaybackState.progress_ms` is the position source.
- **Feature 2 (Analyzer)** — `analyze_song()` produces `SongStructure` on cache miss. Requires a local mp3 file (from capture pipeline or manual).
- **Feature 3 (Compiler)** — `compile_show()` is called via `cached_compile_show()`.
- **Feature 4 (Cache)** — `ShowCache` + `cached_compile_show()` provide cache-aware compilation. `path_based_track_id()` used if the track_id source is a file path.
- **Feature 2 C5 (Show Runtime)** — `ShowPlaybackRuntime` is reused directly. It already handles cue lookup, fade transitions, beat detection, color cycling, and LightingIntent emission.
- **v2 stack** — `MultiGoveeLanAdapter`, `SegmentRenderer`, device roles — all unchanged.
- **session.py** — `run_session()` gains a `--v3` flag that delegates to `run_v3_session()` instead of `run_live_to_govee()`.
- **cli.py** — `--v3` flag on the `session` subcommand.

---

## D5.1: PositionInterpolator — Spotify Position Tracking

### Design

Smooth, continuous playback position derived from periodic Spotify polls. Between polls, wall-clock time advances the position. Each poll corrects drift.

```python
class PositionInterpolator:
    """Derive a continuous playback position from periodic Spotify polls.

    On each poll, call update(progress_ms, is_playing, mono_time).
    Between polls, position_seconds interpolates forward using wall-clock time.
    """

    def __init__(self, max_correction_per_sec: float = 0.5) -> None:
        """
        Args:
            max_correction_per_sec: Maximum seconds of drift correction
                applied per second. Larger jumps (seeks) snap immediately.
        """

    def update(
        self, progress_ms: int, is_playing: bool, mono_time: float | None = None,
    ) -> None:
        """Feed a new Spotify playback state poll."""

    @property
    def position_seconds(self) -> float:
        """Current interpolated position in seconds."""

    @property
    def is_playing(self) -> bool:
        """Whether Spotify is currently playing."""
```

**Interpolation logic:**

1. On `update()`:
   - Store `anchor_position = progress_ms / 1000.0`.
   - Store `anchor_mono = mono_time` (or `time.monotonic()`).
   - Store `is_playing`.
2. On `position_seconds`:
   - If not playing → return `anchor_position` (frozen).
   - Else → `elapsed = time.monotonic() - anchor_mono`.
   - `interpolated = anchor_position + elapsed`.
   - Return `interpolated`.
3. **Drift correction:**
   - When a new poll arrives, compute `drift = new_anchor - interpolated_at_poll_time`.
   - If `|drift| > seek_threshold` (e.g., 2.0s) → treat as seek, snap immediately.
   - If `|drift| <= seek_threshold` → apply correction gradually via `max_correction_per_sec` (slew limiting). This avoids visible jumps on small timing variations.
4. **Pause → play transition:** On resume, snap to the new position (Spotify may have seeked while paused).

### Implementation Steps

1. Add `PositionInterpolator` to `src/dreamsync/v3_session.py`:
   - `__init__`: anchor_position=0, anchor_mono=0, _is_playing=False, _correction=0.
   - `update()`: compute drift, decide snap vs. slew, store new anchor.
   - `position_seconds` property: interpolate from anchor + elapsed + correction.
   - `is_playing` property.

### Done When

- [x] Interpolated position advances linearly between polls at 1x speed
- [x] `update()` corrects small drift gradually (no visible jump for <2s drift)
- [x] Large drift (>2s) snaps immediately (seek detection)
- [x] Position freezes when `is_playing=False`
- [x] Pause → play transition snaps to new position
- [x] 6 unit tests: linear advance, small drift correction, seek snap, pause freeze, resume snap, position accuracy over 10s

---

## D5.2: SpotifyShowSession — Session Orchestrator

### Design

Wires together the Spotify watcher, position interpolator, show cache, show runtime, and device adapter into a single event-driven session.

```python
class SpotifyShowSession:
    """Spotify-connected show session: pre-compiled timeline playback.

    Manages the lifecycle of:
    - Spotify position tracking (PositionInterpolator)
    - Show compilation on track change (cached_compile_show)
    - Background precompilation of queued tracks
    - ShowPlaybackRuntime tick loop
    """

    def __init__(
        self,
        multi_adapter,                       # MultiGoveeLanAdapter
        spotify_watcher: SpotifyQueueWatcher,
        *,
        cache: ShowCache,
        profile: ProfileConfig | None = None,
        capture_dir: Path | str = "captured_songs",
        debug: bool = False,
    ) -> None: ...

    def run(self, stop_event: threading.Event) -> dict[str, Any]:
        """Main loop. Blocks until stop_event is set. Returns summary."""

    # -- Callbacks (fired from SpotifyQueueWatcher thread) --

    def _on_track_changed(self, new_track: SpotifyTrack, old_track: SpotifyTrack | None) -> None:
        """Compile show for the new track. Runs in watcher thread."""

    def _on_playback_state(self, state: PlaybackState) -> None:
        """Update position interpolator. Runs in watcher thread."""

    def _on_queue_updated(self, snapshot: QueueSnapshot) -> None:
        """Precompile upcoming tracks in background. Runs in watcher thread."""

    # -- Internal --

    def _compile_for_track(self, track: SpotifyTrack) -> ShowTimeline | None:
        """Compile (or retrieve from cache) a show for a track.
        Returns None if no mp3 is available for analysis."""

    def _find_mp3_for_track(self, track: SpotifyTrack) -> Path | None:
        """Search capture_dir for a captured mp3 matching the track."""

    def _precompile_queue(self, queue: tuple[SpotifyTrack, ...]) -> None:
        """Compile upcoming tracks in a background thread pool."""
```

**Main loop (`run()`):**

```
1. Activate devices (brightness=100)
2. Wire callbacks into spotify_watcher
3. Main loop at ~200 Hz:
   a. If stop_event is set → break
   b. If not interpolator.is_playing → sleep 50ms, continue
   c. t = interpolator.position_seconds
   d. If runtime is None (no compiled show yet) → sleep 50ms, continue
   e. runtime.tick(t)
   f. sleep 5ms
4. Deactivate devices
5. Return summary
```

**Track change flow (`_on_track_changed()`):**

1. Log track change.
2. Check cache: `cache.has(track.track_id, profile)`.
3. If hit → `timeline = cache.get(track.track_id, profile)`.
4. If miss → find mp3 → `analyze_song()` → `cached_compile_show()`.
5. If no mp3 available → set `runtime = None` (fall back to idle / v2 in Feature 6).
6. Create new `ShowPlaybackRuntime(timeline, multi_adapter)`.
7. Store as `self._runtime` (thread-safe swap via lock).

**Precompilation flow (`_precompile_queue()`):**

1. For each track in queue:
   - If `cache.has(track.track_id, profile)` → skip.
   - Find mp3 → if available, submit `cached_compile_show()` to a `ThreadPoolExecutor(max_workers=1)`.
2. Only one precompilation runs at a time (queue-ordered, single worker).

**Thread safety:**

- `_runtime` is guarded by a `threading.Lock`. The main loop reads it; callbacks write it.
- `PositionInterpolator.update()` is called from the watcher thread; `position_seconds` is read from the main thread. Internal state uses a lock.
- The precompilation executor is a single-worker `ThreadPoolExecutor` — no concurrent compilations.

**Mp3 lookup (`_find_mp3_for_track()`):**

Search `capture_dir` for files matching the track. Strategy:
1. Glob for `*{track_name}*` (case-insensitive) in capture_dir.
2. If no match → glob for `*{track_id}*`.
3. If no match → return None.

This is a best-effort heuristic. The capture pipeline names files by metadata or timestamp. A more robust mapping (track_id → file path) is a future enhancement.

### Implementation Steps

1. Add `SpotifyShowSession` to `src/dreamsync/v3_session.py`:
   - `__init__`: store all deps, create `PositionInterpolator`, init `_runtime = None`, `_runtime_lock = Lock()`.
   - `run()`:
     1. Activate devices.
     2. Register callbacks on watcher (store originals for cleanup).
     3. Main tick loop (200 Hz with 5ms sleep).
     4. Deactivate + cleanup on exit.
     5. Return summary dict.
   - `_on_track_changed()`:
     1. Call `_compile_for_track()`.
     2. If timeline → create `ShowPlaybackRuntime`, swap under lock.
     3. If None → clear runtime.
   - `_on_playback_state()`:
     1. `self._interpolator.update(state.progress_ms, state.is_playing, state.timestamp)`.
   - `_on_queue_updated()`:
     1. Submit `_precompile_queue()` to executor.
   - `_compile_for_track()`:
     1. Check cache. On hit → return timeline.
     2. Find mp3. If not found → log, return None.
     3. `analyze_song(mp3_path)`.
     4. `cached_compile_show(structure, profile, cache=cache, track_id=track.track_id)`.
     5. Return timeline.
   - `_find_mp3_for_track()`:
     1. Glob capture_dir for matching files.
     2. Return first match or None.
   - `_precompile_queue()`:
     1. Iterate queue tracks.
     2. Skip if cached.
     3. Try `_compile_for_track()` for each. Log failures, don't crash.

### Done When

- [x] Track change triggers show compilation (cached or fresh)
- [x] Compiled show drives lights via `ShowPlaybackRuntime.tick()`
- [x] Position tracks Spotify playback with interpolation between polls
- [x] Spotify pause → lights freeze (no frame emission)
- [x] Spotify seek → timeline position jumps correctly
- [x] Spotify skip → new track's show compiles and starts
- [x] Queue precompilation runs in background for upcoming tracks
- [x] Session handles "no mp3 available" gracefully (runtime=None, no crash)
- [x] `run()` returns summary dict with stats (tracks_played, cache_hits, frames_sent, etc.)
- [x] Thread-safe runtime swap on track change
- [x] 12 unit tests: track change + cache hit, track change + cache miss, no mp3 fallback, pause handling, seek handling, skip handling, queue precompilation, position interpolation integration, summary stats, thread-safe runtime swap, callback wiring, graceful shutdown

---

## D5.3: CLI + Session Integration

### Design

Wire `SpotifyShowSession` into the existing `run_session()` and CLI.

#### CLI flag: `--v3`

```
dreamsync session --config devices.yaml --spotify --v3 [--capture --capture-dir DIR] [--profile NAME] [--cache-dir DIR]
```

- `--v3` enables the pre-sequenced playback runtime (requires `--spotify`).
- Without `--v3`, `--spotify` runs the v2 Director with Spotify track info only (current behavior).
- `--capture` is recommended with `--v3` (needed for mp3 analysis), but not enforced.

#### `run_v3_session()` helper

```python
def run_v3_session(
    multi_adapter,
    spotify_watcher: SpotifyQueueWatcher,
    *,
    cache_dir: str = "~/.dreamsync/cache",
    profile: ProfileConfig | None = None,
    capture_dir: str = "captured_songs",
    stop_event: threading.Event,
    debug: bool = False,
) -> dict[str, Any]:
    """Top-level entry point for v3 session. Called from run_session()."""
```

#### session.py changes

In `run_session()`:
- After watcher setup, if `v3=True`:
  - Create `ShowCache(cache_dir)`.
  - Create `SpotifyShowSession(multi_adapter, spotify_watcher, cache=cache, profile=profile, ...)`.
  - Call `session.run(stop_event)` instead of `run_live_to_govee()`.
  - Return session summary.

### Implementation Steps

1. Add to `cli.py`:
   - `--v3` flag on `session` subcommand (store_true, default False).
   - `--cache-dir` flag on `session` subcommand (default `~/.dreamsync/cache`).
   - Validation: `--v3` requires `--spotify` (parser error if `--v3` without `--spotify`).
2. Add `run_v3_session()` to `src/dreamsync/v3_session.py`:
   - Create `ShowCache`.
   - Create `SpotifyShowSession`.
   - Call `session.run(stop_event)`.
   - Return summary.
3. Update `run_session()` in `session.py`:
   - Add `v3: bool = False` and `cache_dir: str = "~/.dreamsync/cache"` parameters.
   - After spotify_watcher setup: if `v3` and `spotify_watcher is not None`:
     - Import `run_v3_session`.
     - Call it instead of `run_live_to_govee()`.
   - If `v3` but no spotify_watcher → print warning, fall back to v2.
4. Wire `capture_dir` from session to v3 session (for mp3 lookup).

### Done When

- [x] `dreamsync session --config devices.yaml --spotify --v3` launches a v3 session
- [x] `--v3` without `--spotify` produces a clear error message
- [x] `--v3` with `--spotify` but no token falls back to v2 with a message
- [x] `--cache-dir` is respected for show cache location
- [x] `--capture` + `--v3` enables mp3 capture for analysis
- [x] Session summary includes v3-specific stats
- [x] 5 unit tests: CLI arg parsing (--v3, --cache-dir), --v3 requires --spotify validation, run_v3_session wiring, session.py v3 branch, fallback when no Spotify

---

## End-to-End Validation

The Playback Runtime is considered complete when:

| # | Criterion | Verified By |
|---|-----------|-------------|
| 1 | Position interpolation tracks Spotify within ~50ms between polls | PositionInterpolator unit tests |
| 2 | Track change triggers compilation (cache hit < 1ms, miss = analyze + compile) | SpotifyShowSession track change tests |
| 3 | Compiled show drives lights via ShowPlaybackRuntime.tick() | Integration test with mock adapter |
| 4 | Spotify pause → lights freeze | Pause handling test |
| 5 | Spotify seek → timeline position jumps | Seek handling test |
| 6 | Queue precompilation caches upcoming tracks | Precompilation test |
| 7 | Missing mp3 → graceful degradation (no crash, log message) | No-mp3 fallback test |
| 8 | `--v3` CLI flag wires everything correctly | CLI + session integration tests |
| 9 | Thread-safe runtime swap on track change | Concurrent access test |
| 10 | Full test suite passes | `pytest dev/tests/test_v3_session*.py` |

---

## Tests

All tests in `dev/tests/test_v3_session*.py`. Target: **23 tests** across 2 files.

| File | Area | Count |
|------|------|-------|
| `dev/tests/test_v3_position.py` | `PositionInterpolator`: interpolation, drift correction, seek snap, pause, resume | 6 |
| `dev/tests/test_v3_session.py` | `SpotifyShowSession`: track change, cache hit/miss, no mp3, pause, seek, skip, precompile, stats, thread safety, CLI, integration | 17 |

### Test Strategy

- **Position tests**: Pure in-memory, mock `time.monotonic()`. Test interpolation accuracy, drift correction slew, seek snap threshold, pause freeze, resume snap.
- **Session tests**: Mock `SpotifyQueueWatcher` (fire callbacks directly), mock `MultiGoveeLanAdapter` (record `send_frame` calls), mock `analyze_song` and `compile_show` (return fixture timelines), mock `ShowCache` (in-memory dict). Test event-driven flows without real Spotify or devices.
- **CLI tests**: Use `build_parser().parse_args()` to verify `--v3`, `--cache-dir` flags. Test validation logic (--v3 requires --spotify).

---

## Parameters

| Parameter | Default | Rationale |
|-----------|---------|-----------|
| `tick_rate` | 200 Hz (5ms sleep) | Matches existing `run_show_playback()`. Sufficient for smooth lighting transitions |
| `idle_rate` | 20 Hz (50ms sleep) | Reduced tick rate when paused or no runtime. Saves CPU |
| `seek_threshold` | 2.0 s | Drift larger than 2s is treated as a seek (snap). Below 2s, slew correction. Matches typical Spotify poll jitter |
| `max_correction_per_sec` | 0.5 s/s | Drift correction rate. At most 0.5s correction per second of real time. Prevents visible jumps on small timing differences |
| `precompile_workers` | 1 | Single background thread for precompilation. Avoids CPU contention with the main tick loop |
| `cache_dir` | `~/.dreamsync/cache` | Same default as Feature 4 |

---

## Build Order

D5.1 is independent. D5.2 depends on D5.1. D5.3 depends on D5.2.

| Phase | Step | Deliverable | Files Created/Modified |
|-------|------|-------------|----------------------|
| 1 | Implement `PositionInterpolator` | D5.1 | `src/dreamsync/v3_session.py` (new) |
| 2 | Write position tests | Tests | `dev/tests/test_v3_position.py` (new) |
| 3 | Implement `SpotifyShowSession` | D5.2 | `src/dreamsync/v3_session.py` |
| 4 | Write session tests | Tests | `dev/tests/test_v3_session.py` (new) |
| 5 | Add CLI flags + `run_v3_session()` + session.py wiring | D5.3 | `src/dreamsync/v3_session.py`, `src/dreamsync/cli.py`, `src/dreamsync/session.py` |
| 6 | Write CLI + integration tests | Tests | `dev/tests/test_v3_session.py` (append) |

Strictly sequential: 1–2, then 3–4, then 5–6.

---

## Non-Goals

- **Local audio playback** — The v3 runtime drives lights only. Audio plays through Spotify's own app/device. The existing `run_show_playback()` + `AudioPlayer` (Feature 2 C5) handles the local mp3 playback case separately.
- **Automatic mp3 acquisition** — If no captured mp3 exists for a track, the runtime does not download or record audio automatically. The user must enable `--capture` to build up their mp3 library over time. Future enhancement: Spotify audio analysis API as a fallback (no mp3 needed).
- **v2 Director fallback switching** — Feature 6 scope. Feature 5 sets `runtime=None` when no show is available; Feature 6 will swap in the v2 Director during those gaps.
- **Multi-device sync correction** — All devices receive the same timestamp. No per-device latency compensation. The existing adapter handles this adequately for LAN/BLE devices.
- **Show editing / manual override** — No runtime modification of cues. The compiled timeline is immutable during playback.
- **Spotify Web API audio analysis** — Could use Spotify's `/audio-analysis` endpoint to get beats/sections without a local mp3. Deferred — it requires additional API scope and the data format differs from our analyzer. Future enhancement.

---

## Dependencies

| Dependency | Type | Notes |
|------------|------|-------|
| `SpotifyQueueWatcher` | Existing code | `src/dreamsync/spotify/queue_watcher.py` — event source |
| `PlaybackState` | Existing code | `src/dreamsync/spotify/models.py` — `progress_ms`, `is_playing` |
| `SpotifyTrack` | Existing code | `src/dreamsync/spotify/models.py` — `track_id`, `name`, `artist` |
| `ShowPlaybackRuntime` | Existing code | `src/dreamsync/show/runtime.py` — tick-based timeline playback |
| `ShowTimeline` | Existing code | `src/dreamsync/show/models.py` — compiled show data |
| `ShowCache`, `cached_compile_show` | Existing code | `src/dreamsync/cache.py` — cache-aware compilation |
| `analyze_song` | Existing code | `src/dreamsync/analyzer/analyze.py` — mp3 → SongStructure |
| `compile_show` | Existing code | `src/dreamsync/compiler/compile.py` — SongStructure → ShowTimeline |
| `MultiGoveeLanAdapter` | Existing code | `src/dreamsync/output/govee_lan.py` — device output |
| `ProfileConfig` | Existing code | `src/dreamsync/profile.py` — active lighting profile |
| `run_session` | Existing code | `src/dreamsync/session.py` — session orchestrator (modified) |
| `threading` | Stdlib | Thread safety, Event, Lock |
| `concurrent.futures` | Stdlib | ThreadPoolExecutor for precompilation |
| `time` | Stdlib | `monotonic()` for position interpolation |
| `logging` | Stdlib | Track change, cache hit/miss, error logging |

No new pip dependencies.
