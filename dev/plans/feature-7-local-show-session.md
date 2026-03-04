# Feature 7 — Local Show Session

**Status**: Not started

---

## Overview

- The Local Show Session is the core new feature that enables fully offline, service-agnostic light show playback. It replaces `SpotifyShowSession` for local audio files — same lifecycle (analyze, compile, cache, play), but driven entirely by `AudioPlayer` for both audio output and position tracking.
- When a local file is loaded, the session checks the `ShowCache`, compiles on cache miss via `analyze_song()` + `compile_show()`, creates a `ShowPlaybackRuntime`, starts `AudioPlayer`, and ticks the runtime at ~200 Hz using `AudioPlayer.position_seconds` as the time source.
- No `PositionInterpolator` is needed — `AudioPlayer` tracks position natively via sounddevice frame counting, providing sub-millisecond accuracy with zero drift.
- The session handles pause, resume, seek, and end-of-track detection through `AudioPlayer`'s native controls.
- When integrated with Feature 8 (PlaylistManager), the session supports multi-track playback with background precompilation of upcoming tracks.

---

## Pain Points

| Pain Point | Description |
|---|---|
| Analysis latency | `analyze_song()` takes 2–10s depending on song length. The first play of an uncached song will have a startup delay before lights begin. Need to show status and fall back gracefully during compilation |
| AudioPlayer lifecycle | `AudioPlayer` opens a sounddevice stream on `play()` and must be properly closed on `stop()`. Stream errors (device unavailable, format mismatch) must be caught and reported cleanly |
| End-of-track detection | `AudioPlayer.finished` becomes True when the sounddevice callback has consumed all samples. The session must detect this and either stop or advance to the next track (Feature 8) |
| Thread safety | `AudioPlayer.position_seconds` is read from the main tick thread at ~200 Hz. `AudioPlayer`'s internal `_audio_callback` runs in the sounddevice thread. Position reads are inherently thread-safe (atomic int read + division) but the session must guard runtime swaps on track change |
| Cache key for local files | Unlike Spotify tracks (which have stable track IDs), local files need a stable, rename-resistant identifier. `path_based_track_id()` exists but uses filename — Feature 8 introduces content-hash IDs for better stability |

---

## Architecture

```
                    +------------------------------------+
                    |         dreamsync play song.mp3     |
                    |         --config devices.yaml       |
                    +----------------+-------------------+
                                     |
                    +----------------v-------------------+
                    |        LocalShowSession             |
                    |                                     |
                    |  1. Check ShowCache                  |
                    |     cache.has(track_id, profile)?    |
                    |     +--- HIT  --> timeline           |
                    |     +--- MISS --> analyze + compile  |
                    |                                     |
                    |  2. Create ShowPlaybackRuntime       |
                    |     ShowPlaybackRuntime(timeline,    |
                    |                        multi_adapter)|
                    |                                     |
                    |  3. Create AudioPlayer               |
                    |     AudioPlayer(audio_path)          |
                    |     player.play()                    |
                    |                                     |
                    |  4. Main tick loop (~200 Hz)         |
                    |     t = player.position_seconds      |
                    |     runtime.tick(t)                  |
                    |     if player.finished --> stop      |
                    |                                     |
                    |  5. Cleanup                          |
                    |     player.stop()                    |
                    |     adapter.deactivate()             |
                    +------------------------------------+
```

### File Layout

```
src/dreamsync/
+-- local_session.py              # LocalShowSession, run_local_session()
```

Single module. The session orchestrator is one concern — wiring local audio playback to the existing ShowPlaybackRuntime + cache + devices.

### Integration Points

- **Feature 2 (Analyzer)** — `analyze_song()` produces `SongStructure` on cache miss. Accepts any audio format via ffmpeg decode.
- **Feature 3 (Compiler)** — `compile_show()` is called via `cached_compile_show()`.
- **Feature 4 (Cache)** — `ShowCache` + `cached_compile_show()` provide cache-aware compilation. `path_based_track_id()` generates cache keys from file paths.
- **Feature 5 (Show Runtime)** — `ShowPlaybackRuntime` is reused directly. `tick(t)` does not care where `t` comes from.
- **Feature 2 C5 (AudioPlayer)** — `AudioPlayer` provides both audio output and `position_seconds` for the tick loop.
- **v2 stack** — `MultiGoveeLanAdapter`, `SegmentRenderer`, device roles — all unchanged.
- **session.py** — `run_session()` gains a `--local` path that delegates to `run_local_session()`.
- **cli.py** — `play` subcommand enhanced with `--config` for device-driven playback. New `--local` flag on `session` subcommand.
- **Feature 8 (Playlist)** — `LocalShowSession` exposes `load_track(path)` for playlist-driven track changes with background precompilation.

---

## D7.1: LocalShowSession — Core Session Class

### Design

The core session class that orchestrates single-file local playback. Takes an audio file path, device adapter, cache, and profile. Handles the full lifecycle: check cache → analyze → compile → cache → create AudioPlayer → create ShowPlaybackRuntime → main tick loop → cleanup.

```python
class LocalShowSession:
    """Standalone local show session: offline playback with pre-compiled timelines.

    Manages the lifecycle of:
    - Audio playback via AudioPlayer (position tracking + audio output)
    - Show compilation on track load (cached_compile_show)
    - ShowPlaybackRuntime tick loop
    - End-of-track detection
    """

    def __init__(
        self,
        multi_adapter,                       # MultiGoveeLanAdapter
        *,
        cache: ShowCache,
        profile: ProfileConfig | None = None,
        sample_rate: int = 44100,
        audio_device: int | None = None,
        debug: bool = False,
    ) -> None: ...

    def run(
        self,
        audio_path: Path | str,
        stop_event: threading.Event,
    ) -> dict[str, Any]:
        """Play a single track. Blocks until finished or stop_event is set. Returns summary."""

    def load_track(self, audio_path: Path | str) -> ShowTimeline | None:
        """Compile (or retrieve from cache) a show for a track.
        Called internally by run() and externally by PlaylistManager (Feature 8)."""

    # -- Internal --

    def _compile_for_file(self, audio_path: Path) -> ShowTimeline | None:
        """Compile (or retrieve from cache) a show for a local file.
        Returns None if analysis fails."""

    def _track_id_for_file(self, audio_path: Path) -> str:
        """Generate a cache-safe track ID from a file path."""
```

**Main loop (`run()`):**

```
1. Activate devices (brightness=100)
2. Compile show for audio_path (cache-aware)
3. If compilation fails → deactivate, return error summary
4. Create AudioPlayer(audio_path)
5. Create ShowPlaybackRuntime(timeline, multi_adapter)
6. player.play()
7. Main loop at ~200 Hz:
   a. If stop_event is set → break
   b. If player.finished → break
   c. If not player.playing → sleep 50ms, continue (paused)
   d. t = player.position_seconds
   e. runtime.tick(t)
   f. sleep 5ms
8. player.stop()
9. Deactivate devices
10. Return summary
```

### Implementation Steps

1. Create `src/dreamsync/local_session.py` with `LocalShowSession`:
   - `__init__`: store deps, init stats counters.
   - `run()`: full lifecycle for single-track playback.
   - `load_track()`: public method for cache-aware compilation (used by Feature 8).
   - `_compile_for_file()`: check cache → analyze → compile → cache.
   - `_track_id_for_file()`: delegate to `path_based_track_id()`.

### Done When

- [ ] Single mp3 file plays audio + drives lights via `ShowPlaybackRuntime.tick()`
- [ ] Cache hit skips re-analysis (instant start)
- [ ] Cache miss triggers `analyze_song()` + `compile_show()` + cache store
- [ ] `AudioPlayer.position_seconds` feeds the tick loop accurately
- [ ] End-of-track detected via `player.finished` — session stops cleanly
- [ ] Analysis/compilation errors produce a clear message, no crash
- [ ] `run()` returns summary dict with stats (track_name, cache_hit, frames_sent, duration, etc.)
- [ ] 8 unit tests: cache hit, cache miss + compile, compile error, end-of-track, pause handling, position accuracy, summary stats, load_track public API

---

## D7.2: run_local_session() Entry Point + Session Wiring

### Design

Top-level entry point called from `run_session()` and `main()`. Creates `ShowCache` and `LocalShowSession`, delegates to `session.run()`.

```python
def run_local_session(
    multi_adapter,
    audio_path: Path | str,
    *,
    cache_dir: str = "~/.dreamsync/cache",
    profile: ProfileConfig | None = None,
    sample_rate: int = 44100,
    audio_device: int | None = None,
    stop_event: threading.Event,
    debug: bool = False,
) -> dict[str, Any]:
    """Top-level entry point for local session. Called from run_session() or CLI."""
```

#### session.py changes

In `run_session()`, add a `local` branch alongside the existing `v3` branch:
- New params: `local: bool = False`, `local_audio: str | None = None`.
- If `local=True` and `local_audio is not None`: call `run_local_session()` instead of `run_live_to_govee()`.

### Implementation Steps

1. Add `run_local_session()` to `src/dreamsync/local_session.py`.
2. Update `run_session()` in `session.py`: add `local` and `local_audio` params, add branching logic.

### Done When

- [ ] `run_local_session()` creates `ShowCache` and `LocalShowSession`, returns summary
- [ ] `run_session(local=True, local_audio="song.mp3")` delegates to local session
- [ ] `local=True` without `local_audio` prints a warning and falls back to v2
- [ ] 3 unit tests: run_local_session wiring, session.py local branch, fallback when no audio path

---

## D7.3: CLI Integration

### Design

Two CLI entry points for local show playback:

#### 1. Enhanced `play` subcommand

The existing `play` subcommand requires `--show` (pre-compiled JSON). Enhance it to compile on-the-fly when `--show` is omitted:

```
dreamsync play song.mp3 --config devices.yaml [--profile NAME] [--cache-dir DIR]
```

- If `--show` is provided: use existing behavior (load pre-compiled timeline).
- If `--show` is omitted: run `LocalShowSession` (analyze → compile → cache → play).

#### 2. `session --local` mode

```
dreamsync session --config devices.yaml --local song.mp3 [--profile NAME] [--cache-dir DIR]
```

- `--local <path>` enables local mode with the given audio file.
- Mutually exclusive with `--spotify` + `--v3`.

### Implementation Steps

1. Update `play` subcommand in `cli.py`:
   - Make `--show` optional (currently required).
   - When `--show` is omitted and `--config` is provided: use `LocalShowSession`.
   - When `--show` is provided: use existing `run_show_playback()` behavior.
2. Add `--local` flag to `session` subcommand in `cli.py`.
3. Wire `--local` through to `run_session()`.

### Done When

- [ ] `dreamsync play song.mp3 --config devices.yaml` plays with on-the-fly compilation
- [ ] `dreamsync play song.mp3 --show compiled.json --config devices.yaml` uses pre-compiled show (existing behavior)
- [ ] `dreamsync session --config devices.yaml --local song.mp3` launches a local session
- [ ] `--local` with `--spotify --v3` produces a clear error
- [ ] `--profile` and `--cache-dir` are respected in local mode
- [ ] 5 unit tests: CLI arg parsing (--local, --show optional), play without --show, session --local, mutual exclusion validation, cache-dir forwarding

---

## End-to-End Validation

The Local Show Session is considered complete when:

| # | Criterion | Verified By |
|---|-----------|-------------|
| 1 | Local mp3 plays audio through system speakers | AudioPlayer integration test |
| 2 | Lights drive via ShowPlaybackRuntime.tick() synchronized to audio | Position accuracy test |
| 3 | Cache hit skips re-analysis (instant start) | Cache hit test |
| 4 | Cache miss triggers full pipeline (analyze → compile → cache) | Cache miss test |
| 5 | End-of-track stops session cleanly | End-of-track test |
| 6 | Compilation error produces a message, no crash | Error handling test |
| 7 | `dreamsync play song.mp3 --config devices.yaml` works | CLI integration test |
| 8 | `dreamsync session --local song.mp3 --config devices.yaml` works | Session integration test |
| 9 | Full test suite passes | `pytest dev/tests/test_local_session*.py` |

---

## Tests

All tests in `dev/tests/test_local_session*.py`. Target: **16 tests** across 2 files.

| File | Area | Count |
|------|------|-------|
| `dev/tests/test_local_session.py` | `LocalShowSession`: cache hit, cache miss, compile error, end-of-track, pause, position, summary, load_track | 8 |
| `dev/tests/test_local_session.py` | `run_local_session` wiring, session.py integration, CLI args, play without --show, session --local, validation | 8 |

### Test Strategy

- **Session tests**: Mock `AudioPlayer` (return canned `position_seconds`, control `finished` flag), mock `MultiGoveeLanAdapter` (record `send_frame` calls), mock `analyze_song` and `compile_show` (return fixture timelines), mock `ShowCache` (in-memory dict). Test lifecycle flows without real audio or devices.
- **CLI tests**: Use `build_parser().parse_args()` to verify flags. Test validation logic.

---

## Parameters

| Parameter | Default | Rationale |
|-----------|---------|-----------|
| `tick_rate` | 200 Hz (5ms sleep) | Matches `SpotifyShowSession` and `run_show_playback()`. Sufficient for smooth lighting |
| `idle_rate` | 20 Hz (50ms sleep) | Reduced tick rate when paused. Saves CPU |
| `cache_dir` | `~/.dreamsync/cache` | Same default as Feature 4 and Feature 5 |
| `sample_rate` | 44100 | Standard audio sample rate, matches AudioPlayer default |

---

## Build Order

D7.1 is independent. D7.2 depends on D7.1. D7.3 depends on D7.2.

| Phase | Step | Deliverable | Files Created/Modified |
|-------|------|-------------|----------------------|
| 1 | Implement `LocalShowSession` | D7.1 | `src/dreamsync/local_session.py` (new) |
| 2 | Write session tests | Tests | `dev/tests/test_local_session.py` (new) |
| 3 | Implement `run_local_session()` + session.py wiring | D7.2 | `src/dreamsync/local_session.py`, `src/dreamsync/session.py` |
| 4 | Write wiring tests | Tests | `dev/tests/test_local_session.py` (append) |
| 5 | Add CLI flags + play enhancement | D7.3 | `src/dreamsync/cli.py` |
| 6 | Write CLI tests | Tests | `dev/tests/test_local_session.py` (append) |

Strictly sequential: 1-2, then 3-4, then 5-6.

---

## Non-Goals

- **Playlist / multi-track sequencing** — Feature 8 scope. Feature 7 handles single-file playback only. `load_track()` provides the hook for Feature 8 to drive track changes.
- **Interactive controls (next/prev/stop)** — Feature 8 scope. Feature 7 plays until end-of-track or Ctrl+C.
- **Shuffle / repeat** — Feature 8 scope. Requires a playlist concept.
- **Content-hash track IDs** — Feature 8 introduces SHA256-based track IDs. Feature 7 uses `path_based_track_id()` (filename-based).
- **Audio format validation** — If ffmpeg can't decode the file, `analyze_song()` will raise an error. No pre-validation of file format.
- **New effect modes or device protocols** — Same render modes and Govee LAN/BLE stack.

---

## Dependencies

| Dependency | Type | Notes |
|------------|------|-------|
| `AudioPlayer` | Existing code | `src/dreamsync/show/player.py` — playback + position tracking |
| `ShowPlaybackRuntime` | Existing code | `src/dreamsync/show/runtime.py` — tick-based timeline playback |
| `ShowTimeline` | Existing code | `src/dreamsync/show/models.py` — compiled show data |
| `ShowCache`, `cached_compile_show` | Existing code | `src/dreamsync/cache.py` — cache-aware compilation |
| `path_based_track_id` | Existing code | `src/dreamsync/cache.py` — file path → cache key |
| `analyze_song` | Existing code | `src/dreamsync/analyzer/analyze.py` — audio → SongStructure |
| `compile_show` | Existing code | `src/dreamsync/compiler/compile.py` — SongStructure → ShowTimeline |
| `MultiGoveeLanAdapter` | Existing code | `src/dreamsync/output/govee_lan.py` — device output |
| `ProfileConfig` | Existing code | `src/dreamsync/profile.py` — active lighting profile |
| `run_session` | Existing code | `src/dreamsync/session.py` — session orchestrator (modified) |
| `threading` | Stdlib | Event for stop signal |
| `time` | Stdlib | `sleep()` for tick rate control |
| `logging` | Stdlib | Track loading, cache hit/miss, error logging |
| `pathlib` | Stdlib | `Path` for file handling |

No new pip dependencies.
