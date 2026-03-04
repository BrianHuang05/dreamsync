# Feature 8 — Local Playlist Manager

**Status**: Not started

---

## Overview

- The Playlist Manager adds multi-track sequencing to the Local Show Session (Feature 7). It manages a queue of audio files, advances tracks on end-of-track detection, and precompiles upcoming tracks in the background so they're cached before they start playing.
- Playlists can be loaded from three sources: a single file, a directory scan (all audio files in a folder), or an M3U/M3U8 playlist file. The manager handles track ordering, shuffle, repeat, and interactive controls (next/prev/stop).
- Track identification uses content-hash-based IDs (SHA256 of first 64KB + file size) for cache stability — files can be renamed without invalidating cached shows.
- The manager integrates with `LocalShowSession` by calling `load_track()` for each track and providing an `on_track_ended` callback to trigger advancement.

---

## Pain Points

| Pain Point | Description |
|---|---|
| Directory scan ordering | Audio files in a directory have no inherent order. Alphabetical sorting is the default, but users may want shuffle or custom ordering. Must handle mixed file types (mp3, wav, flac, ogg) |
| M3U parsing | M3U files can contain relative paths, absolute paths, and extended metadata (`#EXTINF`). Must handle all variants robustly, skip non-existent files, and support UTF-8 encoding |
| Content hashing performance | SHA256 of first 64KB is fast (~1ms per file), but scanning a large directory (hundreds of files) should not block startup. Hash lazily on first use |
| Precompilation ordering | Background compilation should prioritize the next track, not compile all tracks at once. A single-worker thread pool ensures sequential, ordered precompilation |
| Track advancement timing | When `AudioPlayer.finished` fires, the session must seamlessly transition to the next track. The gap between tracks should be minimal (< 1s including AudioPlayer teardown + new AudioPlayer creation) |
| Interactive controls | Next/prev/stop commands need to interrupt the current tick loop. Use `threading.Event` signals or a command queue to communicate from the input thread to the main loop |

---

## Architecture

```
                    +------------------------------------+
                    |  dreamsync play ./music/            |
                    |  dreamsync play playlist.m3u        |
                    |  --config devices.yaml --shuffle    |
                    +----------------+-------------------+
                                     |
                    +----------------v-------------------+
                    |        PlaylistManager              |
                    |                                     |
                    |  Sources:                           |
                    |    - single file                    |
                    |    - directory scan                 |
                    |    - M3U/M3U8 file                  |
                    |                                     |
                    |  Track list + ordering              |
                    |  Shuffle / Repeat                   |
                    |  Content-hash track IDs             |
                    +----------------+-------------------+
                                     |
                    +----------------v-------------------+
                    |     LocalPlaylistSession            |
                    |                                     |
                    |  for track in playlist:             |
                    |    session.load_track(track)        |
                    |    player = AudioPlayer(track)      |
                    |    player.play()                    |
                    |    while not finished:              |
                    |      runtime.tick(player.pos)       |
                    |    --> advance to next track        |
                    |                                     |
                    |  Background precompilation:         |
                    |    compile next track while         |
                    |    current track plays              |
                    |                                     |
                    |  Interactive controls:              |
                    |    next / prev / stop               |
                    +------------------------------------+
```

### File Layout

```
src/dreamsync/
+-- playlist.py                   # PlaylistManager, content_hash_track_id()
+-- local_session.py              # LocalShowSession (Feature 7),
                                  # LocalPlaylistSession (Feature 8),
                                  # run_local_session() (enhanced)
```

Two modules. `playlist.py` handles playlist data and track discovery. `local_session.py` is extended with `LocalPlaylistSession` which wraps `LocalShowSession` with playlist-driven track advancement.

### Integration Points

- **Feature 7 (LocalShowSession)** — `LocalShowSession.load_track()` is called for each track. `LocalShowSession.run()` handles single-track playback.
- **Feature 4 (Cache)** — `ShowCache` stores compiled shows by content-hash track ID + profile fingerprint.
- **Feature 2 (Analyzer)** — `analyze_song()` for on-demand compilation on cache miss.
- **Feature 3 (Compiler)** — `compile_show()` via `cached_compile_show()`.
- **AudioPlayer** — `finished` property for end-of-track detection. `stop()` for cleanup before next track.
- **cli.py** — `play` subcommand accepts directories and M3U files. `--shuffle`, `--repeat` flags.

---

## D8.1: PlaylistManager — Playlist Data + Track Discovery

### Design

The playlist data structure. Manages an ordered list of audio file paths from various sources. Provides iteration, shuffle, repeat, and content-hash track ID generation.

```python
class PlaylistManager:
    """Manage an ordered list of audio files for sequential playback.

    Sources:
    - Single file: PlaylistManager.from_file(path)
    - Directory: PlaylistManager.from_directory(path)
    - M3U file: PlaylistManager.from_m3u(path)
    """

    def __init__(
        self,
        tracks: list[Path],
        *,
        shuffle: bool = False,
        repeat: bool = False,
    ) -> None: ...

    @classmethod
    def from_file(cls, path: Path | str, **kwargs) -> PlaylistManager: ...

    @classmethod
    def from_directory(
        cls,
        directory: Path | str,
        *,
        extensions: tuple[str, ...] = (".mp3", ".wav", ".flac", ".ogg", ".aac"),
        **kwargs,
    ) -> PlaylistManager: ...

    @classmethod
    def from_m3u(cls, path: Path | str, **kwargs) -> PlaylistManager: ...

    @classmethod
    def from_path(cls, path: Path | str, **kwargs) -> PlaylistManager:
        """Auto-detect source type (file, directory, or M3U) and create playlist."""

    @property
    def current(self) -> Path | None:
        """Current track path, or None if playlist is empty/exhausted."""

    @property
    def current_index(self) -> int:
        """0-based index of the current track."""

    def next(self) -> Path | None:
        """Advance to the next track. Returns new current, or None if exhausted."""

    def prev(self) -> Path | None:
        """Go back to the previous track. Returns new current, or None."""

    def peek_next(self, count: int = 1) -> list[Path]:
        """Preview upcoming tracks without advancing (for precompilation)."""

    def __len__(self) -> int:
        """Total number of tracks."""

    def __iter__(self): ...
```

```python
def content_hash_track_id(file_path: Path | str) -> str:
    """Generate a cache-stable track ID from file content.

    Uses SHA256 of first 64KB + file size. Stable across renames,
    unique across different files.

    Returns: 'contenthash_<hex16>'
    """
```

**M3U parsing:**

```
# Extended M3U
#EXTM3U
#EXTINF:213,Artist - Song Title
/path/to/song.mp3
#EXTINF:187,Artist - Another Song
relative/path/song.wav
```

- Lines starting with `#` are metadata (ignored for path extraction).
- Blank lines are skipped.
- Relative paths are resolved against the M3U file's directory.
- Non-existent files are skipped with a warning.

**Directory scan:**

- Recursively or non-recursively (default: non-recursive) scan for audio files.
- Filter by extension: `.mp3`, `.wav`, `.flac`, `.ogg`, `.aac`.
- Sort alphabetically by filename.

**Shuffle:**

- On init with `shuffle=True`, randomize track order using `random.shuffle()`.
- On repeat + shuffle, re-shuffle at the start of each cycle.

**Repeat:**

- When `next()` reaches the end and `repeat=True`, wrap to index 0.
- When `repeat=False`, return `None` at end.

### Implementation Steps

1. Create `src/dreamsync/playlist.py`:
   - `content_hash_track_id()` function.
   - `PlaylistManager` class with all constructors and iteration methods.

### Done When

- [ ] `PlaylistManager.from_file()` creates a single-track playlist
- [ ] `PlaylistManager.from_directory()` scans for audio files, sorted alphabetically
- [ ] `PlaylistManager.from_m3u()` parses M3U/M3U8 files with relative + absolute paths
- [ ] `PlaylistManager.from_path()` auto-detects source type
- [ ] `next()` advances, returns None at end (or wraps with repeat)
- [ ] `prev()` goes back, returns None at start
- [ ] `peek_next()` previews upcoming tracks without advancing
- [ ] `shuffle=True` randomizes track order
- [ ] `repeat=True` wraps at end of playlist
- [ ] `content_hash_track_id()` produces stable IDs across renames
- [ ] Non-existent files in M3U are skipped with warning
- [ ] 12 unit tests: from_file, from_directory, from_m3u (relative + absolute paths, comments, blank lines), from_path auto-detect, next/prev iteration, peek_next, shuffle, repeat, content_hash_track_id stability, empty playlist, non-existent files

---

## D8.2: Playlist Playback Integration

### Design

Wire `PlaylistManager` into the local session system. `LocalPlaylistSession` wraps `LocalShowSession` to add multi-track sequencing, end-of-track advancement, and background precompilation.

```python
class LocalPlaylistSession:
    """Multi-track local show session with playlist management.

    Wraps LocalShowSession with:
    - Track advancement on end-of-track (AudioPlayer.finished)
    - Background precompilation of upcoming tracks
    - Interactive control signals (next/prev/stop)
    """

    def __init__(
        self,
        multi_adapter,
        playlist: PlaylistManager,
        *,
        cache: ShowCache,
        profile: ProfileConfig | None = None,
        sample_rate: int = 44100,
        audio_device: int | None = None,
        debug: bool = False,
    ) -> None: ...

    def run(self, stop_event: threading.Event) -> dict[str, Any]:
        """Play through the playlist. Blocks until exhausted or stopped."""

    def signal_next(self) -> None:
        """Signal to skip to the next track (called from input thread)."""

    def signal_prev(self) -> None:
        """Signal to go to the previous track."""

    # -- Internal --

    def _play_track(self, audio_path: Path, stop_event: threading.Event) -> str:
        """Play a single track. Returns reason for stop: 'finished', 'next', 'prev', 'stopped'."""

    def _precompile_upcoming(self) -> None:
        """Compile the next 1-2 tracks in background."""
```

**Multi-track loop:**

```
1. Activate devices
2. Start precompilation of first + next tracks
3. While playlist has tracks and not stopped:
   a. current = playlist.current
   b. result = _play_track(current, stop_event)
   c. If result == 'finished' or 'next':
      - playlist.next() → if None, break (or repeat)
      - Start precompilation of next upcoming track
   d. If result == 'prev':
      - playlist.prev()
   e. If result == 'stopped':
      - break
4. Deactivate devices
5. Return summary
```

**Background precompilation:**

```python
def _precompile_upcoming(self):
    upcoming = self._playlist.peek_next(count=2)
    for track_path in upcoming:
        track_id = content_hash_track_id(track_path)
        if not self._cache.has(track_id, self._profile):
            self._executor.submit(self._compile_track, track_path, track_id)
```

Single-worker `ThreadPoolExecutor` ensures sequential, ordered precompilation (same pattern as `SpotifyShowSession`).

### Implementation Steps

1. Add `LocalPlaylistSession` to `src/dreamsync/local_session.py`.
2. Update `run_local_session()` to accept a `PlaylistManager` and choose between `LocalShowSession` (single file) and `LocalPlaylistSession` (playlist).

### Done When

- [ ] Playlist plays tracks sequentially from start to finish
- [ ] End-of-track detection advances to next track automatically
- [ ] `signal_next()` skips current track immediately
- [ ] `signal_prev()` restarts or goes to previous track
- [ ] Background precompilation compiles upcoming tracks while current plays
- [ ] `repeat=True` loops the playlist after the last track
- [ ] Session stops cleanly on `stop_event` or when playlist is exhausted
- [ ] `run()` returns summary dict with per-track stats
- [ ] Content-hash track IDs used for cache keys
- [ ] 8 unit tests: sequential playback, end-of-track advance, signal_next, signal_prev, precompilation, repeat, stop mid-playlist, summary stats

---

## D8.3: CLI Playlist Controls

### Design

Extend the CLI to support playlist sources and interactive controls during playback.

#### Enhanced `play` subcommand

```
dreamsync play ./music/           --config devices.yaml [--shuffle] [--repeat]
dreamsync play playlist.m3u       --config devices.yaml [--shuffle] [--repeat]
dreamsync play song1.mp3          --config devices.yaml
```

The `play` positional argument accepts a file, directory, or M3U path. `PlaylistManager.from_path()` auto-detects the type.

#### New flags

```python
play.add_argument(
    "--shuffle",
    action="store_true",
    default=False,
    help="Randomize playlist order.",
)
play.add_argument(
    "--repeat",
    action="store_true",
    default=False,
    help="Loop playlist after last track.",
)
```

#### Interactive controls

During a playlist session, a keyboard listener thread handles:
- `n` or `→` — next track
- `p` or `←` — previous track
- `q` or `Ctrl+C` — stop session

Implementation uses a simple stdin reader on a daemon thread that calls `session.signal_next()` / `session.signal_prev()`.

### Implementation Steps

1. Update `play` subcommand in `cli.py`:
   - Add `--shuffle` and `--repeat` flags.
   - In `main()`, detect if `mp3_path` is a directory or M3U file.
   - Use `PlaylistManager.from_path()` to create playlist.
   - If playlist has >1 track: use `LocalPlaylistSession`.
   - If playlist has 1 track: use `LocalShowSession` (Feature 7).
2. Add keyboard listener for interactive controls.
3. Wire interactive controls to `LocalPlaylistSession.signal_next()` / `signal_prev()`.

### Done When

- [ ] `dreamsync play ./music/ --config devices.yaml` plays all audio files in directory
- [ ] `dreamsync play playlist.m3u --config devices.yaml` plays M3U playlist
- [ ] `dreamsync play song.mp3 --config devices.yaml` plays single file (Feature 7 behavior)
- [ ] `--shuffle` randomizes playlist order
- [ ] `--repeat` loops playlist
- [ ] Keyboard `n`/`p`/`q` controls work during playlist playback
- [ ] Track name displayed on each track change
- [ ] 6 unit tests: CLI arg parsing (--shuffle, --repeat), directory detection, M3U detection, single file detection, playlist session wiring, keyboard control wiring

---

## End-to-End Validation

The Playlist Manager is considered complete when:

| # | Criterion | Verified By |
|---|-----------|-------------|
| 1 | Directory of mp3s plays sequentially with lights | Integration test |
| 2 | M3U playlist plays in specified order | M3U parsing + playback test |
| 3 | Shuffle randomizes order | Shuffle test |
| 4 | Repeat loops at end | Repeat test |
| 5 | Next/prev controls work mid-session | Interactive control test |
| 6 | Background precompilation caches upcoming tracks | Precompilation test |
| 7 | Content-hash IDs survive renames | Hash stability test |
| 8 | Empty directory or M3U produces clear message | Edge case test |
| 9 | Full test suite passes | `pytest dev/tests/test_playlist*.py dev/tests/test_local_session*.py` |

---

## Tests

All tests in `dev/tests/test_playlist.py` and `dev/tests/test_local_session.py`. Target: **26 tests** across 2 files.

| File | Area | Count |
|------|------|-------|
| `dev/tests/test_playlist.py` | `PlaylistManager`: constructors, iteration, shuffle, repeat, M3U parsing, content hash, edge cases | 12 |
| `dev/tests/test_local_session.py` | `LocalPlaylistSession`: sequential playback, track advance, next/prev, precompilation, repeat, stop, summary, CLI | 14 |

### Test Strategy

- **Playlist tests**: Pure in-memory. Create temp directories with dummy files. Test M3U parsing with fixture strings. Test content-hash stability by writing identical content to differently-named files.
- **Session tests**: Mock `AudioPlayer` and `MultiGoveeLanAdapter`. Control `finished` flag to simulate track endings. Verify `signal_next()` / `signal_prev()` trigger correct track changes.
- **CLI tests**: Use `build_parser().parse_args()` to verify flags. Test `PlaylistManager.from_path()` auto-detection.

---

## Parameters

| Parameter | Default | Rationale |
|-----------|---------|-----------|
| `extensions` | `.mp3`, `.wav`, `.flac`, `.ogg`, `.aac` | Standard audio formats ffmpeg can decode |
| `hash_bytes` | 65536 (64KB) | First 64KB of file for content hashing. Fast to read, sufficient for uniqueness |
| `precompile_lookahead` | 2 | Precompile next 2 tracks. Balances I/O with readiness |
| `precompile_workers` | 1 | Single background thread. Avoids CPU contention with playback |
| `inter_track_gap` | 0 ms | No gap between tracks. AudioPlayer teardown + creation is ~50ms |

---

## Build Order

D8.1 is independent. D8.2 depends on D8.1 + Feature 7. D8.3 depends on D8.2.

| Phase | Step | Deliverable | Files Created/Modified |
|-------|------|-------------|----------------------|
| 1 | Implement `PlaylistManager` + `content_hash_track_id` | D8.1 | `src/dreamsync/playlist.py` (new) |
| 2 | Write playlist tests | Tests | `dev/tests/test_playlist.py` (new) |
| 3 | Implement `LocalPlaylistSession` + update `run_local_session()` | D8.2 | `src/dreamsync/local_session.py` |
| 4 | Write playlist session tests | Tests | `dev/tests/test_local_session.py` (append) |
| 5 | Add CLI flags + auto-detection + keyboard controls | D8.3 | `src/dreamsync/cli.py` |
| 6 | Write CLI + integration tests | Tests | `dev/tests/test_local_session.py` (append) |

Strictly sequential: 1-2, then 3-4, then 5-6.

---

## Non-Goals

- **Audio streaming / network playback** — Local files only. No HTTP URLs in playlists.
- **Recursive directory scan** — Scan the given directory only, not subdirectories. Users can specify subdirectories explicitly or use M3U.
- **Playlist editing** — No GUI or CLI for reordering tracks in a playlist. Edit M3U files manually.
- **Gapless playback** — No crossfade between tracks. Gap is limited by AudioPlayer teardown + creation time (~50ms).
- **Album art / metadata display** — No visual display of track metadata beyond console output.
- **Spotify playlist import** — No conversion from Spotify playlists to local M3U files.

---

## Dependencies

| Dependency | Type | Notes |
|------------|------|-------|
| `LocalShowSession` | Feature 7 | `src/dreamsync/local_session.py` — single-track session |
| `AudioPlayer` | Existing code | `src/dreamsync/show/player.py` — `finished` property for track-end detection |
| `ShowCache`, `cached_compile_show` | Existing code | `src/dreamsync/cache.py` — cache-aware compilation |
| `path_based_track_id` | Existing code | `src/dreamsync/cache.py` — fallback track ID |
| `analyze_song` | Existing code | `src/dreamsync/analyzer/analyze.py` — audio analysis |
| `compile_show` | Existing code | `src/dreamsync/compiler/compile.py` — show compilation |
| `MultiGoveeLanAdapter` | Existing code | `src/dreamsync/output/govee_lan.py` — device output |
| `ProfileConfig` | Existing code | `src/dreamsync/profile.py` — lighting profile |
| `hashlib` | Stdlib | SHA256 for content hashing |
| `random` | Stdlib | `shuffle()` for playlist randomization |
| `threading` | Stdlib | Event signals, ThreadPoolExecutor |
| `pathlib` | Stdlib | Path handling |
| `logging` | Stdlib | Track transitions, errors |

No new pip dependencies.
