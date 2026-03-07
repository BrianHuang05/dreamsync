# Directory-Based Analysis/Compile/Play Pipeline

## Context

The capture pipeline produces a directory of per-song MP3 files with JSON sidecar metadata. Today, the analysis/compile/play path works on individual files (`play`, `compile-and-play`) or directories without sidecar awareness (`play <dir>`). There is no unified command to take a capture output directory and run the full pipeline: scan tracks, analyze each song, compile lighting shows, and play them back with synchronized lighting.

**Goal:** A file-to-file pipeline that reads MP3s from a capture directory on disk, enriches analysis with sidecar metadata, compiles lighting shows (cached), and plays them back. This decouples capture from playback — a crash in the analyzer doesn't lose captured audio, and you can re-run without re-capturing.

## Files to Create

| File | Purpose |
|------|---------|
| `src/dreamsync/capture/scanner.py` | `CaptureTrack` dataclass + `CaptureDirectoryScanner` class |
| `src/dreamsync/dir_pipeline.py` | `DirectoryPipeline` class — scan → analyze → compile → play |
| `dev/tests/test_capture_scanner.py` | Scanner unit tests |
| `dev/tests/test_dir_pipeline.py` | Pipeline unit tests |

## Files to Modify

| File | Change |
|------|--------|
| `src/dreamsync/cache.py` | Add `sidecar_track_id()` function |
| `src/dreamsync/cli.py` | Add `pipeline` subcommand |
| `src/dreamsync/playlist.py` | Add `PlaylistManager.from_tracks()` factory (list[Path] direct constructor shorthand) |

**No changes to:** `analyzer/analyze.py`, `compiler/compile.py`, `show/runtime.py`, `local_session.py` — the pipeline composes these existing modules, it does not modify them.

## Design

### Data Flow

```
capture_dir/
  ├── 2026-03-07_10-23-45_artist_-_song_a.mp3
  ├── 2026-03-07_10-23-45_artist_-_song_a.json    (sidecar)
  ├── 2026-03-07_10-27-02_artist_-_song_b.mp3
  ├── 2026-03-07_10-27-02_artist_-_song_b.json    (sidecar)
  └── ...

         ┌──────────────────────────────────┐
Step 1   │  CaptureDirectoryScanner.scan()  │
         │  → list[CaptureTrack]            │
         └───────────────┬──────────────────┘
                         │
         ┌───────────────▼──────────────────┐
Step 2   │  For each CaptureTrack:          │
         │    analyze_song(mp3, metadata=   │
         │      sidecar.metadata)           │
         │    → SongStructure               │
         │    → write .analysis.json        │
         └───────────────┬──────────────────┘
                         │
         ┌───────────────▼──────────────────┐
Step 3   │  For each SongStructure:         │
         │    cached_compile_show(           │
         │      structure, profile,          │
         │      track_id=sidecar_track_id)  │
         │    → ShowTimeline                │
         └───────────────┬──────────────────┘
                         │
         ┌───────────────▼──────────────────┐
Step 4   │  LocalPlaylistSession.run()      │
         │    (reuses existing playback)    │
         └──────────────────────────────────┘
```

### CaptureTrack (dataclass)

```python
@dataclass(frozen=True)
class CaptureTrack:
    mp3_path: Path               # Absolute path to MP3 file
    sidecar_path: Path | None    # Absolute path to JSON sidecar (if exists)
    segment_index: int           # From sidecar, or inferred from filename sort order
    song_title: str | None       # From sidecar songTitle field
    artist: str | None           # From sidecar artist field
    album: str | None            # From sidecar album field
    duration_seconds: float      # From sidecar or 0.0 if missing
    metadata: dict               # Full parsed sidecar content (empty dict if no sidecar)
```

### Cache Key Strategy

When a sidecar has `songTitle` + `artist`, use `sidecar_track_id(title, artist)` → deterministic hash. This means re-capturing the same Spotify track produces a cache hit (same song → same analysis → same show). Falls back to `path_based_track_id()` when sidecar is missing or lacks metadata.

```python
def sidecar_track_id(song_title: str, artist: str) -> str:
    """Cache-stable ID from sidecar metadata. Same song → same ID across captures."""
    key = f"{artist.strip().lower()}:{song_title.strip().lower()}"
    return f"sidecar_{hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]}"
```

---

## Steps

### Step 1: `CaptureTrack` dataclass + `CaptureDirectoryScanner` class ✅

**Prerequisites:** None — standalone new module.

**Deliverables:**
- `CaptureTrack` dataclass in `src/dreamsync/capture/scanner.py`
- `CaptureDirectoryScanner` class in same file
- `TestCaptureDirectoryScanner` test class in `dev/tests/test_capture_scanner.py`

**Implementation details:**

`CaptureDirectoryScanner.__init__(directory: Path)` — stores resolved directory path.

`CaptureDirectoryScanner.scan() -> list[CaptureTrack]`:
1. Glob `*.mp3` in directory (non-recursive)
2. For each MP3, look for matching `.json` sidecar (same stem)
3. If sidecar exists, parse it and extract: `segmentIndex`, `songTitle`, `artist`, `album`, `segmentDurationSeconds`
4. If sidecar missing, infer segment_index from sorted filename position, metadata fields are `None`
5. Build `CaptureTrack` for each MP3
6. Sort by `segment_index`, then by filename (for stable ordering)
7. Return sorted list

Edge cases:
- Empty directory → return `[]`
- MP3 without sidecar → `CaptureTrack` with `sidecar_path=None`, metadata fields `None`
- Corrupt/unreadable sidecar → log warning, treat as missing
- Non-MP3 files ignored

**Tests (10):**
1. `test_empty_directory` — no MP3s → empty list
2. `test_single_mp3_no_sidecar` — one MP3, no JSON → CaptureTrack with None metadata
3. `test_single_mp3_with_sidecar` — paired MP3+JSON → metadata populated
4. `test_multiple_tracks_sorted_by_index` — 3 tracks with sidecars → sorted by segmentIndex
5. `test_missing_sidecar_for_some` — mix of paired and unpaired → all returned, unpaired have None metadata
6. `test_corrupt_sidecar_logged` — invalid JSON sidecar → warning logged, metadata None
7. `test_non_mp3_files_ignored` — .wav, .txt files present → only .mp3 returned
8. `test_directory_not_found` — nonexistent dir → raises FileNotFoundError
9. `test_sidecar_fields_extracted` — verify all fields: songTitle, artist, album, duration, segmentIndex
10. `test_sort_falls_back_to_filename` — no sidecars → sorted by filename alphabetically

**Completion criteria:**
- All 10 tests pass
- `CaptureDirectoryScanner` handles all edge cases without crashing

**Verify:**
```bash
python -m pytest dev/tests/test_capture_scanner.py -v
```

---

### Step 2: `sidecar_track_id()` cache function ✅

**Prerequisites:** None — independent of Step 1.

**Deliverables:**
- `sidecar_track_id(song_title: str, artist: str) -> str` function in `src/dreamsync/cache.py`
- `track_id_for_capture(capture_track) -> str` convenience function in same file
- Tests added to existing cache test file

**Implementation details:**

`sidecar_track_id(song_title, artist)`:
- Lowercase and strip both inputs
- Combine: `f"{artist}:{song_title}"`
- SHA256 hash → first 16 hex chars
- Return `f"sidecar_{hash}"`
- Raises `ValueError` if both inputs are empty/whitespace

`track_id_for_capture(capture_track)`:
- If `capture_track.song_title` and `capture_track.artist` are both non-None and non-empty: return `sidecar_track_id(song_title, artist)`
- Otherwise: return `path_based_track_id(capture_track.mp3_path)`

**Tests (5):**
1. `test_sidecar_track_id_deterministic` — same inputs → same output
2. `test_sidecar_track_id_case_insensitive` — "Artist" vs "artist" → same ID
3. `test_sidecar_track_id_strips_whitespace` — "  Title  " → same as "Title"
4. `test_track_id_for_capture_with_metadata` — CaptureTrack with title+artist → sidecar ID
5. `test_track_id_for_capture_without_metadata` — CaptureTrack with None title → falls back to path-based

**Completion criteria:**
- All 5 tests pass
- Existing cache tests still pass (no regressions)

**Verify:**
```bash
python -m pytest tests/ -v -k "cache or sidecar_track"
python -m pytest dev/tests/ -v -k "cache or sidecar_track"
```

---

### Step 3: `DirectoryPipeline` class ✅

**Prerequisites:** Steps 1 and 2 (scanner + cache key functions exist).

**Deliverables:**
- `DirectoryPipeline` class in `src/dreamsync/dir_pipeline.py`
- `PipelineResult` dataclass in same file
- `TestDirectoryPipeline` test class in `dev/tests/test_dir_pipeline.py`

**Implementation details:**

```python
@dataclass
class TrackResult:
    track: CaptureTrack
    structure: SongStructure | None  # None if analysis failed
    timeline: ShowTimeline | None    # None if compile failed or skipped
    error: str | None                # Error message if failed
    from_cache: bool                 # Whether show was a cache hit

@dataclass
class PipelineResult:
    tracks: list[TrackResult]
    analyzed: int
    compiled: int
    cache_hits: int
    errors: int
```

`DirectoryPipeline.__init__(capture_dir, *, cache, profile=None, sample_rate=44100, on_progress=None)`:
- `capture_dir` (Path) — directory to scan
- `cache` (ShowCache) — for compiled show caching
- `profile` (ProfileConfig | None) — lighting profile
- `sample_rate` (int) — for analysis
- `on_progress` (callable | None) — `(step: str, index: int, total: int, track: CaptureTrack) -> None`

`DirectoryPipeline.prepare() -> PipelineResult`:
1. Call `CaptureDirectoryScanner(capture_dir).scan()` → tracks
2. For each track:
   a. Compute track_id via `track_id_for_capture(track)`
   b. Check cache: if `cache.has(track_id, profile)` → load timeline, mark `from_cache=True`, skip analysis
   c. Cache miss: call `analyze_song(track.mp3_path, metadata={"track_name": track.song_title, "artist": track.artist, "album": track.album})`
   d. Optionally write `.analysis.json` alongside MP3 (if not already present)
   e. Call `cached_compile_show(structure, profile, cache=cache, track_id=track_id)` → timeline
   f. Build `TrackResult`
   g. Call `on_progress` callback
3. Return `PipelineResult` with aggregate stats

`DirectoryPipeline.playable_tracks() -> list[Path]`:
- After `prepare()`, return list of MP3 paths for tracks that have valid timelines
- Ordered same as scan order

Error handling:
- Analysis failure for one track does not abort the pipeline
- Failed tracks have `error` set in their `TrackResult`
- Progress callback fires for every track regardless of success/failure

**Tests (12):**
1. `test_prepare_empty_dir` — no tracks → empty result
2. `test_prepare_single_track` — one MP3 + sidecar → analyzed + compiled
3. `test_prepare_uses_sidecar_metadata` — verify analyze_song receives sidecar metadata
4. `test_prepare_cache_hit_skips_analysis` — pre-cached track → no analyze_song call
5. `test_prepare_cache_miss_analyzes_and_caches` — uncached → analyze + compile + cache.put
6. `test_prepare_analysis_failure_continues` — bad MP3 → error in result, other tracks still processed
7. `test_prepare_multiple_tracks` — 3 tracks → all processed, correct counts
8. `test_prepare_progress_callback` — on_progress called for each track
9. `test_playable_tracks_excludes_errors` — failed tracks not in playable list
10. `test_playable_tracks_preserves_order` — order matches scan order
11. `test_prepare_writes_analysis_json` — .analysis.json written alongside MP3
12. `test_prepare_skips_existing_analysis_json` — if .analysis.json already exists, still re-analyzes (analysis is fast relative to capture, and cache handles the expensive compile)

Note: Tests should mock `analyze_song` and `compile_show` to avoid real audio processing. Use a mock MP3 file (empty or minimal) and mock returns.

**Completion criteria:**
- All 12 tests pass
- `DirectoryPipeline.prepare()` processes a directory of capture files end-to-end
- Failed tracks don't abort the pipeline

**Verify:**
```bash
python -m pytest dev/tests/test_dir_pipeline.py -v
```

---

### Step 4: `pipeline` CLI subcommand ✅

**Prerequisites:** Step 3 (DirectoryPipeline exists).

**Deliverables:**
- `pipeline` subcommand added to `build_parser()` in `src/dreamsync/cli.py`
- Handler in `main()` for `args.command == "pipeline"`

**Implementation details:**

Parser arguments:
```
dreamsync pipeline <capture_dir>
    --config <devices.yaml>       # Required for play mode
    --profile <name>              # Optional lighting profile
    --cache-dir <path>            # Default: ~/.dreamsync/cache
    --sample-rate <int>           # Default: 44100
    --audio-device <int>          # Output audio device ID
    --mode <analyze|compile|play> # Default: play (full pipeline)
    --shuffle                     # Randomize track order
    --repeat                      # Loop playlist after last track
    --debug                       # Verbose output
    --fps <int>                   # Device frame rate (default: 30)
    --brightness <float>          # 0-1 (default: 1.0)
    --mirror / --no-mirror        # Scroll direction
```

`--mode` controls how far the pipeline runs:
- `analyze` — scan + analyze only (writes .analysis.json files, no devices needed, `--config` not required)
- `compile` — scan + analyze + compile (writes cached shows, no devices needed, `--config` not required)
- `play` (default) — scan + analyze + compile + play (requires `--config`)

Handler implementation:
1. Create `ShowCache(cache_dir)`
2. Resolve profile (if provided)
3. Create `DirectoryPipeline(capture_dir, cache=cache, profile=profile, ...)`
4. Call `pipeline.prepare()` → `PipelineResult`
5. Print summary: `N tracks scanned, M analyzed, K compiled (J cache hits), E errors`
6. For each track with an error, print warning
7. If `--mode play`:
   a. Load device config, detect devices, build multi_adapter
   b. Get `pipeline.playable_tracks()` → list[Path]
   c. Create `PlaylistManager(tracks, shuffle=..., repeat=...)`
   d. Create `LocalPlaylistSession(multi_adapter, playlist, cache=cache, profile=profile, ...)`
   e. Set up SIGINT handler + stop_event
   f. Call `session.run(stop_event)`
   g. Print playback summary
8. Return 0

**Tests:**
- No dedicated CLI tests — the CLI handler is thin glue. Correctness is covered by DirectoryPipeline tests (Step 3) and existing LocalPlaylistSession tests. Manual verification via the verify commands below.

**Completion criteria:**
- `dreamsync pipeline --help` shows all arguments
- `dreamsync pipeline <dir> --mode analyze` scans and writes .analysis.json files
- `dreamsync pipeline <dir> --mode compile` scans, analyzes, and caches compiled shows
- `dreamsync pipeline <dir> --config devices.yaml` runs full playback
- All existing tests still pass

**Verify:**
```bash
# Verify CLI parses correctly
python -m dreamsync pipeline --help

# Analyze-only mode (no devices needed)
python -m dreamsync pipeline out/capture-test/ --mode analyze --debug

# Compile-only mode (no devices needed)
python -m dreamsync pipeline out/capture-test/ --mode compile --debug

# Full pipeline (requires devices)
python -m dreamsync pipeline out/capture-test/ --config devices.yaml --debug

# Regression
python -m pytest tests/ -v
python -m pytest dev/tests/ -v
```

---

### Step 5: `PlaylistManager.from_tracks()` factory + integration tests ✅

**Prerequisites:** Steps 1-4 (all pipeline components exist).

**Deliverables:**
- `PlaylistManager.from_tracks(tracks: list[Path], **kwargs)` class method in `src/dreamsync/playlist.py`
- Integration test class `TestPipelineCLI` in `dev/tests/test_dir_pipeline.py`

**Implementation details:**

`PlaylistManager.from_tracks(tracks, *, shuffle=False, repeat=False)`:
- Direct constructor from an explicit list of Path objects
- Validates all paths exist (raises FileNotFoundError for missing)
- This avoids the caller needing to construct a PlaylistManager manually from a list (currently the `__init__` is the only way, but having a named factory is consistent with `from_file`, `from_directory`, `from_m3u`, `from_path`)

Integration tests (5):
1. `test_pipeline_analyze_only_writes_analysis_files` — Create temp dir with 2 mock MP3s + sidecars, run `DirectoryPipeline.prepare()`, verify .analysis.json files written
2. `test_pipeline_compile_caches_shows` — After prepare(), verify ShowCache contains entries for each track
3. `test_pipeline_playable_tracks_order` — Verify playable_tracks() returns paths in scan order
4. `test_pipeline_sidecar_cache_reuse` — Prepare twice with same sidecar metadata → second run all cache hits
5. `test_pipeline_mixed_success_failure` — One good MP3 + one corrupt → result has 1 success + 1 error, playable_tracks() has 1 entry

**Completion criteria:**
- `from_tracks()` factory works and is used by the pipeline CLI handler
- All 5 integration tests pass
- Full test suite passes

**Verify:**
```bash
python -m pytest dev/tests/test_dir_pipeline.py -v
python -m pytest dev/tests/test_capture_scanner.py -v
python -m pytest dev/tests/ -v
python -m pytest tests/ -v
```

---

## Final Verification

After all steps are complete:

```bash
# Run all new tests
python -m pytest dev/tests/test_capture_scanner.py -v
python -m pytest dev/tests/test_dir_pipeline.py -v

# Run full dev + core test suite
python -m pytest dev/tests/ -v
python -m pytest tests/ -v

# Manual: analyze a capture directory
python -m dreamsync pipeline out/capture-test/ --mode analyze --debug

# Manual: compile a capture directory
python -m dreamsync pipeline out/capture-test/ --mode compile --debug

# Manual: full pipeline with devices
python -m dreamsync pipeline out/capture-test/ --config devices.yaml --debug

# Manual: verify analysis files written alongside MP3s
ls out/capture-test/*.analysis.json

# Manual: verify cache populated
python -m dreamsync cache-list
```

---

## Summary

| Step | Deliverable | Tests | Dependencies |
|------|-------------|-------|-------------|
| 1 | CaptureDirectoryScanner + CaptureTrack | 10 | None |
| 2 | sidecar_track_id() + track_id_for_capture() | 5 | None |
| 3 | DirectoryPipeline + PipelineResult | 12 | Steps 1, 2 |
| 4 | `pipeline` CLI subcommand | 0 (manual) | Step 3 |
| 5 | PlaylistManager.from_tracks() + integration tests | 5 | Steps 1-4 |
| **Total** | | **32** | |
