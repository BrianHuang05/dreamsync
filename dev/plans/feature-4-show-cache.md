# Feature 4 — Show Cache

**Status**: **DONE** (D4.1–D4.4 complete — 36 tests)

---

## Overview

- The Show Cache stores compiled `ShowTimeline`s on disk, keyed by Spotify track ID + profile fingerprint, so that re-analysis and re-compilation can be skipped for songs that have been compiled before.
- When a song is about to play, the runtime checks the cache first. Cache hit → load the timeline directly (< 1 ms). Cache miss → run the full `analyze_song()` + `compile_show()` pipeline, then store the result.
- Cache keys are composite: `(track_id, profile_fingerprint)`. The same song compiled with different profiles gets separate cache entries. When a profile changes, existing entries for other profiles are preserved (they'll be hits if the user switches back).
- Manual flush via CLI (`cache-clear`) deletes all or per-track entries.
- The cache is file-based (one JSON per compiled show) — no database, no new dependencies.

---

## Pain Points

| Pain Point | Description |
|---|---|
| Profile identity | Need a stable, deterministic fingerprint of a `ProfileConfig`'s *content* (not its object identity or file path) so that profile edits produce a different cache key but unchanged profiles always match |
| Corrupt files | Cache files on disk can be corrupted (partial write, disk error, user tampering). Reads must gracefully degrade to cache miss, not crash |
| Track ID availability | The cache is keyed by Spotify track ID, but `SongStructure` doesn't always have one (e.g. offline mp3). Need a clear fallback strategy (path hash) or explicit track ID parameter |
| Atomic writes | Writing a JSON file is not atomic — a crash mid-write leaves a corrupt file. Need write-to-temp + rename |

---

## Architecture

```
                    ┌──────────────────────────────────┐
                    │        Caller (session /           │
                    │        CLI / Feature 5)            │
                    │                                    │
                    │   track_id + ProfileConfig +       │
                    │   SongStructure                    │
                    └───────────────┬──────────────────┘
                                    │
                                    ▼
                    ┌──────────────────────────────────┐
                    │       cached_compile_show()        │
                    │                                    │
                    │  1. Compute profile fingerprint    │
                    │  2. cache.get(track_id, profile)   │
                    │     ├── HIT  → return timeline     │
                    │     └── MISS →                     │
                    │         3. compile_show(struct, p)  │
                    │         4. cache.put(track_id, tl)  │
                    │         5. return timeline          │
                    └───────────────┬──────────────────┘
                                    │
                    ┌───────────────┴──────────────────┐
                    │           ShowCache                │
                    │                                    │
                    │  cache_dir/                        │
                    │  ├── <track_id>/                   │
                    │  │   ├── <fp>.show.json            │
                    │  │   └── <fp>.show.json            │
                    │  └── <track_id>/                   │
                    │      └── <fp>.show.json            │
                    │                                    │
                    │  get() / put() / has()             │
                    │  invalidate() / clear()            │
                    │  list_entries() / stats()          │
                    └──────────────────────────────────┘
```

### File Layout

```
src/dreamsync/
├── cache.py                  # ShowCache, CacheEntry, CacheStats,
│                             # profile_fingerprint(), cached_compile_show()
```

Single module — the cache is fundamentally one concern (file-based KV store with composite keys). No need for a package.

### On-Disk Format

```
<cache_dir>/
├── <track_id>/                          # one directory per Spotify track
│   ├── <profile_fingerprint>.show.json  # ShowTimeline JSON (to_json format)
│   └── <profile_fingerprint>.show.json  # different profile → different file
└── <track_id>/
    └── <profile_fingerprint>.show.json
```

- **Directory per track** — makes per-track operations (`invalidate`, list) fast via `os.scandir`.
- **Filename = profile fingerprint** — 8 hex chars + `.show.json` suffix.
- **File contents = ShowTimeline JSON** — the exact format produced by `ShowTimeline.to_json()`. The existing `metadata` dict inside the timeline is augmented with cache-specific fields (`_cache_profile_name`, `_cache_compiled_at`) before writing.
- **Default cache directory**: `~/.dreamsync/cache/`. Overridable via `--cache-dir` CLI flag.

### Integration Points

- **Feature 3 (Show Compiler)** — `compile_show()` is the function whose output is cached. `cached_compile_show()` wraps it with a cache layer.
- **Feature 2 (Analyzer)** — `SongStructure` is the input to compilation. On a cache hit, analysis results are not needed (the timeline is self-contained). On a cache miss, the caller must have already produced a `SongStructure`.
- **Feature 1 (Spotify)** — `SpotifyTrack.track_id` is the primary cache key.
- **Profile system** — `ProfileConfig` feeds the profile fingerprint. Changes to profile content produce a different fingerprint → automatic cache miss.
- **Feature 5 (Playback Runtime)** — future consumer. Will call `cached_compile_show()` when a track is about to play.
- **cli.py** — `cache-list`, `cache-clear`, `cache-info` subcommands. `--cache-dir` flag on `compile` and `compile-and-play`.

---

## D4.1: Profile Fingerprint — `profile_fingerprint()`

### Design

Computes a short, deterministic hex string from a `ProfileConfig`'s content. Two profiles with identical palettes, moods, effects, and transitions produce the same fingerprint — regardless of `source_path`, `description`, or `author` (those are cosmetic and don't affect compilation output).

```python
def profile_fingerprint(profile: ProfileConfig | None) -> str:
    """Compute an 8-char hex fingerprint of a profile's compilation-relevant content.

    Returns "00000000" for None (built-in defaults).
    """
```

**Algorithm:**

1. If `profile is None`, return the sentinel `"00000000"` (built-in defaults are always the same).
2. Build a canonical representation of the compilation-relevant fields:
   - `profile.name`
   - `profile.palettes` — sorted by key, values as sorted tuples
   - `profile.moods` — sorted by key; for each mood: sorted palette names, sorted effect entries `(name, weight)`, sorted params
   - `profile.transitions` — sorted tuples of `(from_mood, to_mood, palette)`
3. Serialize to a canonical JSON string (sorted keys, no whitespace).
4. Hash with SHA-256, take the first 8 hex characters.

**Why 8 hex chars?** 32 bits → ~4 billion combinations. Collision probability is negligible for a personal cache with < 1000 entries. Short enough for readable filenames.

**Why exclude `source_path`, `description`, `author`, `tags`?** These don't affect `compile_show()` output. Only the palettes, moods (effects + params), and transitions flow into the compiler pipeline.

### Implementation Steps

1. Add `profile_fingerprint()` to `src/dreamsync/cache.py`:
   - Handle `None` → `"00000000"`.
   - Build canonical dict from `profile.name`, `profile.palettes`, `profile.moods`, `profile.transitions`.
   - For moods: extract `(palette_names, [(effect.name, effect.weight), ...], params_items)`.
   - For transitions: extract `[(rule.from_mood, rule.to_mood, rule.palette), ...]`.
   - `json.dumps(canonical, sort_keys=True, separators=(",", ":"))`.
   - `hashlib.sha256(json_bytes).hexdigest()[:8]`.

### Done When

- [x] `profile_fingerprint(None)` returns `"00000000"`
- [x] Same profile object produces the same fingerprint on repeated calls
- [x] Two distinct `ProfileConfig` objects with identical content produce the same fingerprint
- [x] Changing any palette, mood effect, mood param, or transition rule produces a different fingerprint
- [x] Changing only `description`, `author`, `tags`, or `source_path` does NOT change the fingerprint
- [x] The fingerprint is exactly 8 lowercase hex characters
- [x] Output is deterministic across Python sessions (no set ordering issues)
- [x] 8 unit tests covering: None profile, determinism, content equality, palette change, mood effect change, mood param change, transition change, cosmetic field immunity

---

## D4.2: ShowCache — Core Cache Class

### Design

File-based cache with composite keys `(track_id, profile_fingerprint)`. One JSON file per compiled show, organized in per-track directories.

```python
@dataclass(frozen=True)
class CacheEntry:
    """Metadata about one cached show (for listing/display)."""
    track_id: str
    profile_fingerprint: str
    profile_name: str              # human-readable, from metadata
    track_name: str                # from timeline metadata
    artist: str                    # from timeline metadata
    duration: float                # song duration in seconds
    compiled_at: str               # ISO 8601 timestamp
    file_path: Path                # absolute path to the .show.json
    file_size: int                 # bytes on disk

@dataclass(frozen=True)
class CacheStats:
    """Aggregate cache statistics."""
    entry_count: int               # total compiled shows
    track_count: int               # unique track IDs
    total_bytes: int               # total disk usage
    cache_dir: Path

class ShowCache:
    def __init__(
        self,
        cache_dir: Path | str = "~/.dreamsync/cache",
    ) -> None:
        """Initialize cache. Creates cache_dir if it doesn't exist."""

    @property
    def cache_dir(self) -> Path:
        """Resolved, expanded cache directory path."""

    def get(
        self,
        track_id: str,
        profile: ProfileConfig | None = None,
    ) -> ShowTimeline | None:
        """Look up a cached show. Returns None on miss or corrupt file."""

    def put(
        self,
        track_id: str,
        timeline: ShowTimeline,
        profile: ProfileConfig | None = None,
    ) -> Path:
        """Store a compiled show. Returns the written file path.

        Augments timeline.metadata with cache fields before writing.
        Uses atomic write (temp file + rename) to prevent corruption.
        """

    def has(
        self,
        track_id: str,
        profile: ProfileConfig | None = None,
    ) -> bool:
        """Check if a cache entry exists without loading it."""

    def invalidate(self, track_id: str) -> int:
        """Delete all cached shows for a track (all profiles). Returns count deleted."""

    def clear(self) -> int:
        """Delete all cache entries. Returns count deleted."""

    def list_entries(self) -> list[CacheEntry]:
        """List all cached shows with metadata. Sorted by track name."""

    def stats(self) -> CacheStats:
        """Aggregate cache statistics."""

    # --- Internal helpers ---

    def _entry_path(self, track_id: str, fp: str) -> Path:
        """Resolve path: cache_dir / track_id / {fp}.show.json"""

    def _track_dir(self, track_id: str) -> Path:
        """Resolve path: cache_dir / track_id /"""

    def _read_timeline(self, path: Path) -> ShowTimeline | None:
        """Load a ShowTimeline from a JSON file. Returns None on any error."""

    def _write_timeline(self, path: Path, timeline: ShowTimeline, profile: ProfileConfig | None) -> None:
        """Atomic write: write to .tmp, then rename."""
```

**get() logic:**

1. Compute `fp = profile_fingerprint(profile)`.
2. Resolve path: `cache_dir / track_id / {fp}.show.json`.
3. If file doesn't exist → return `None`.
4. Try `ShowTimeline.from_json(path)`. On any exception (corrupt JSON, missing fields, validation error) → log warning, delete the corrupt file, return `None`.
5. Return the loaded `ShowTimeline`.

**put() logic:**

1. Compute `fp = profile_fingerprint(profile)`.
2. Augment `timeline.metadata` with cache fields:
   - `_cache_profile_name`: `profile.name` if profile else `"(built-in defaults)"`
   - `_cache_profile_fp`: `fp`
   - `_cache_compiled_at`: current ISO 8601 timestamp
3. Create track directory if needed: `cache_dir / track_id /`.
4. Write to temp file: `{path}.tmp`.
5. Rename temp → final path (atomic on most filesystems).
6. Return the final path.

**Metadata augmentation note:** `ShowTimeline` is frozen, so we can't modify `metadata` in place. Instead, `put()` creates a new dict merging the original metadata with cache fields, then writes the JSON manually using `timeline.to_dict()` (which returns a mutable dict we can patch before `json.dump`).

**invalidate() logic:**

1. Resolve track directory: `cache_dir / track_id /`.
2. If directory doesn't exist → return `0`.
3. Delete all `.show.json` files in the directory.
4. Remove the empty directory.
5. Return count of deleted files.

**clear() logic:**

1. Scan `cache_dir` for all track directories.
2. For each, delete all `.show.json` files and the directory.
3. Return total count of deleted files.

**list_entries() logic:**

1. Scan `cache_dir` for track directories.
2. For each `.show.json` file, load it and extract metadata fields.
3. Build a `CacheEntry` for each.
4. Sort by `track_name` alphabetically.
5. Return the list.

**Performance note:** `list_entries()` loads each JSON file to extract metadata. For a personal cache (tens to hundreds of songs), this is instant. If needed later, an index file can be added as an optimization — but YAGNI for now.

### Implementation Steps

1. Create `src/dreamsync/cache.py`:
   - Import: `dataclasses`, `hashlib`, `json`, `logging`, `os`, `shutil`, `tempfile`, `datetime`, `Path`.
   - Import: `ShowTimeline` from `dreamsync.show.models`, `ProfileConfig` from `dreamsync.profile`.
   - `CacheEntry` frozen dataclass.
   - `CacheStats` frozen dataclass.
   - `ShowCache`:
     - `__init__(cache_dir)`:
       1. Expand `~` and resolve path.
       2. Store as `self._cache_dir`.
       3. Create directory if not exists (`mkdir(parents=True, exist_ok=True)`).
     - `get(track_id, profile)`:
       1. Compute fingerprint.
       2. Check file exists.
       3. Try to load; handle errors.
     - `put(track_id, timeline, profile)`:
       1. Compute fingerprint.
       2. Build augmented dict from `timeline.to_dict()`.
       3. Add `_cache_*` fields to `metadata`.
       4. Atomic write via tempfile + `os.replace()`.
     - `has(track_id, profile)`:
       1. Compute fingerprint.
       2. Return `path.exists()`.
     - `invalidate(track_id)`:
       1. Scan and delete track directory.
     - `clear()`:
       1. Scan and delete all track directories.
     - `list_entries()`:
       1. Scan all `.show.json` files.
       2. Load each, extract metadata.
       3. Build `CacheEntry` list.
     - `stats()`:
       1. Count entries, unique tracks, total bytes.
     - `_entry_path`, `_track_dir`, `_read_timeline`, `_write_timeline` helpers.

### Done When

- [x] `put()` then `get()` round-trips a `ShowTimeline` correctly
- [x] `get()` returns `None` for a cache miss (unknown track ID)
- [x] `get()` returns `None` for a known track but different profile
- [x] `get()` returns `None` and cleans up when the file is corrupt JSON
- [x] `has()` returns `True` after `put()`, `False` before
- [x] `put()` uses atomic writes (temp + rename) — no partial files on crash
- [x] `put()` augments metadata with `_cache_profile_name`, `_cache_profile_fp`, `_cache_compiled_at`
- [x] `invalidate(track_id)` removes all profile variants for that track
- [x] `invalidate()` returns 0 for unknown track ID (no error)
- [x] `clear()` removes all entries, returns correct count
- [x] `list_entries()` returns correct metadata for all cached shows
- [x] `stats()` reports accurate entry_count, track_count, total_bytes
- [x] Cache directory is created automatically on first use
- [x] Works with `profile=None` (built-in defaults)
- [x] 14 unit tests covering: put/get round-trip, miss (unknown track), miss (different profile), corrupt file handling, has() true/false, atomic write, metadata augmentation, invalidate (known track), invalidate (unknown track), clear, list_entries, stats, auto-create dir, None profile

---

## D4.3: `cached_compile_show()` — Cache-Aware Compilation

### Design

A thin wrapper around `compile_show()` that checks the cache before compiling. Returns both the timeline and a boolean indicating whether it was a cache hit.

```python
def cached_compile_show(
    structure: SongStructure,
    profile: ProfileConfig | None = None,
    *,
    cache: ShowCache,
    track_id: str,
    **compile_kwargs,
) -> tuple[ShowTimeline, bool]:
    """Compile a show, using the cache when possible.

    Args:
        structure: Song structure from the analyzer.
        profile: Active profile (or None for defaults).
        cache: ShowCache instance.
        track_id: Spotify track ID (or path-based hash for offline files).
        **compile_kwargs: Passed through to compile_show() on miss
            (seed, intro_intensity, etc.).

    Returns:
        (timeline, from_cache) — the compiled show and whether it was a cache hit.
    """
```

**Logic:**

1. `timeline = cache.get(track_id, profile)`.
2. If hit → return `(timeline, True)`.
3. If miss → `timeline = compile_show(structure, profile, **compile_kwargs)`.
4. `cache.put(track_id, timeline, profile)`.
5. Return `(timeline, False)`.

**`track_id` parameter:** Passed explicitly by the caller. In a Spotify-connected session, this is `SpotifyTrack.track_id`. For offline mp3 files (no Spotify), the caller can derive a stable ID from the file path or audio hash. The cache itself doesn't care about the format — it just needs a filesystem-safe string.

**`track_id` validation:** The cache should validate that `track_id` is a safe directory name: alphanumeric + underscores/hyphens only, 1–128 chars. Reject or sanitize anything else.

### Helper: `path_based_track_id()`

For non-Spotify files, provide a helper that generates a stable cache key from a file path:

```python
def path_based_track_id(file_path: Path) -> str:
    """Generate a cache-safe track ID from a file path.

    Uses the filename stem + a short hash of the absolute path.
    Example: 'my_song_a1b2c3d4'
    """
```

This enables caching for offline mp3 files used with the `compile` and `compile-and-play` CLI commands.

### Implementation Steps

1. Add to `src/dreamsync/cache.py`:
   - `cached_compile_show(structure, profile, *, cache, track_id, **compile_kwargs)`:
     1. Validate `track_id` (safe chars, length).
     2. `cache.get(track_id, profile)`.
     3. On hit → log info, return `(timeline, True)`.
     4. On miss → `compile_show(structure, profile, **compile_kwargs)`.
     5. `cache.put(track_id, timeline, profile)`.
     6. Return `(timeline, False)`.
   - `path_based_track_id(file_path)`:
     1. Resolve path to absolute.
     2. `stem = path.stem` (sanitize to alphanumeric + underscore).
     3. `hash = hashlib.sha256(str(path).encode()).hexdigest()[:8]`.
     4. Return `f"{stem}_{hash}"`.
   - `_sanitize_track_id(track_id)`:
     1. Replace non-alphanumeric chars (except `-_`) with `_`.
     2. Truncate to 128 chars.
     3. Raise `ValueError` if empty after sanitization.

### Done When

- [x] Cache hit returns the stored timeline and `from_cache=True`
- [x] Cache miss calls `compile_show()`, stores result, returns `from_cache=False`
- [x] `compile_kwargs` are passed through to `compile_show()` (seed, arc params, etc.)
- [x] Works with `profile=None`
- [x] `track_id` validation rejects empty strings
- [x] `path_based_track_id()` produces stable, filesystem-safe IDs
- [x] `path_based_track_id()` produces different IDs for different paths
- [x] Logging indicates cache hit vs. miss
- [x] 5 unit tests covering: cache hit, cache miss + store, compile_kwargs passthrough, path_based_track_id determinism, track_id validation

---

## D4.4: CLI Subcommands — Cache Management

### Design

Three new subcommands for cache inspection and management, plus `--cache-dir` flag on existing compile commands.

#### `dreamsync cache-list [--cache-dir DIR]`

Displays a table of all cached shows:

```
Show Cache: 12 entries (3.2 MB) in ~/.dreamsync/cache

Track                        Artist           Profile          Compiled At           Size
────────────────────────────────────────────────────────────────────────────────────────────
Blinding Lights              The Weeknd       midnight_rave    2026-03-04 14:23:01   4.8 KB
Bohemian Rhapsody            Queen            midnight_rave    2026-03-03 22:10:45   8.1 KB
Bohemian Rhapsody            Queen            chill_ambient    2026-03-03 22:15:12   7.9 KB
...
```

#### `dreamsync cache-clear [--track-id ID] [--cache-dir DIR] [--yes]`

- No `--track-id` → clear entire cache (prompts for confirmation unless `--yes`).
- With `--track-id` → clear all entries for that track only.
- Prints count of deleted entries.

#### `dreamsync cache-info [--cache-dir DIR]`

```
Show Cache Info
  Directory:   ~/.dreamsync/cache
  Entries:     12
  Tracks:      8
  Disk usage:  3.2 MB
```

#### `--cache-dir` on compile / compile-and-play

Add an optional `--cache-dir` flag to the existing `compile` and `compile-and-play` subcommands. When provided:

- `compile`: After compiling, also store the result in the cache. Track ID is derived via `path_based_track_id()` (since the CLI may not have a Spotify track ID).
- `compile-and-play`: Check cache before compiling. Print "Cache hit — skipping compilation" or "Cache miss — compiling...".

### Implementation Steps

1. Add subcommand parsers to `build_parser()` in `cli.py`:
   - `cache-list`:
     - `--cache-dir` (optional, default `~/.dreamsync/cache`).
   - `cache-clear`:
     - `--track-id` (optional).
     - `--cache-dir` (optional).
     - `--yes` / `-y` (skip confirmation prompt).
   - `cache-info`:
     - `--cache-dir` (optional).
2. Add `--cache-dir` to existing `compile` and `compile-and-play` subcommands.
3. Add handler logic in `main()`:
   - `cache-list`:
     1. Create `ShowCache(args.cache_dir)`.
     2. `entries = cache.list_entries()`.
     3. Print header with `cache.stats()`.
     4. Print table rows.
   - `cache-clear`:
     1. Create `ShowCache(args.cache_dir)`.
     2. If `args.track_id` → `cache.invalidate(track_id)`.
     3. Else → confirm with user (unless `--yes`) → `cache.clear()`.
     4. Print count.
   - `cache-info`:
     1. Create `ShowCache(args.cache_dir)`.
     2. `s = cache.stats()`.
     3. Print formatted stats.
4. Update `compile` handler:
   - If `--cache-dir` is set:
     1. Create `ShowCache(args.cache_dir)`.
     2. Derive `track_id = path_based_track_id(structure_path)`.
     3. Use `cached_compile_show()` instead of `compile_show()`.
     4. Print hit/miss status.
5. Update `compile-and-play` handler:
   - Same as `compile`, but also check cache before analyzing:
     - If cache hit → skip both analysis and compilation.
     - If cache miss → analyze → compile → cache → play.

### Done When

- [x] `dreamsync cache-list` shows all entries in a readable table
- [x] `dreamsync cache-list` shows "Cache is empty" when no entries
- [x] `dreamsync cache-clear` deletes all entries with confirmation prompt
- [x] `dreamsync cache-clear --track-id X` deletes only that track
- [x] `dreamsync cache-clear --yes` skips confirmation
- [x] `dreamsync cache-info` shows accurate stats
- [x] `dreamsync compile --cache-dir DIR` stores result in cache
- [x] `dreamsync compile-and-play --cache-dir DIR` checks cache before compile
- [x] 5 unit tests covering: CLI argument parsing for all 3 subcommands, --cache-dir wiring on compile, --cache-dir wiring on compile-and-play

---

## End-to-End Validation

The Show Cache is considered complete when:

| # | Criterion | Verified By |
|---|-----------|-------------|
| 1 | `put()` + `get()` round-trips a `ShowTimeline` without data loss | Round-trip unit test |
| 2 | Same song + same profile = cache hit | Hit/miss unit test |
| 3 | Same song + different profile = cache miss | Composite key unit test |
| 4 | Profile change → new compilation → new cache entry | Profile fingerprint test |
| 5 | `invalidate(track_id)` removes all profile variants | Invalidate unit test |
| 6 | `clear()` empties the entire cache | Clear unit test |
| 7 | Corrupt JSON files degrade gracefully to cache miss | Corrupt file test |
| 8 | `cached_compile_show()` integrates cache + compiler correctly | Integration unit test |
| 9 | CLI `cache-list`, `cache-clear`, `cache-info` work correctly | CLI arg parsing tests |
| 10 | `compile --cache-dir` stores and retrieves from cache | CLI integration test |
| 11 | Full test suite passes (32 tests) | `pytest dev/tests/test_cache*.py` |

---

## Tests

All tests in `dev/tests/test_cache*.py`. Target: **32 tests** across 3 files.

| File | Area | Count |
|------|------|-------|
| `dev/tests/test_cache_fingerprint.py` | `profile_fingerprint()`: determinism, content equality, field changes, cosmetic immunity, None profile | 8 |
| `dev/tests/test_cache.py` | `ShowCache`: put/get, miss, corrupt file, has, invalidate, clear, list, stats, atomic write, auto-create dir | 14 |
| `dev/tests/test_cache_compile.py` | `cached_compile_show()` + CLI: hit/miss, kwargs passthrough, path ID, CLI arg parsing | 10 |

### Test Strategy

- **Fingerprint tests**: Construct `ProfileConfig` objects with known palettes/moods. Verify fingerprint stability, sensitivity to content changes, immunity to cosmetic changes. All in-memory — no disk I/O.
- **Cache tests**: Use `tmp_path` fixture for isolated cache directories. Construct `ShowTimeline` objects manually (reuse test helpers from `test_show_models.py`). Test all CRUD operations. For corrupt file tests, write invalid JSON directly to the cache directory and verify graceful miss.
- **Compile/CLI tests**: Mock `compile_show()` to avoid needing real `SongStructure` analysis. Verify `cached_compile_show()` calls `compile_show()` on miss and skips it on hit. Test CLI argument parsing with `build_parser().parse_args()`. Verify `--cache-dir` flag is wired correctly.

---

## Parameters

| Parameter | Default | Rationale |
|-----------|---------|-----------|
| `cache_dir` | `~/.dreamsync/cache` | User-level cache, survives project directory changes, hidden directory avoids clutter |
| `fingerprint length` | 8 hex chars | 32 bits — collision-safe for personal cache (< 1000 entries) |
| `None profile sentinel` | `"00000000"` | Visually distinct, always 8 chars like a real fingerprint |
| `track_id max length` | 128 chars | Spotify IDs are 22 chars; generous limit for path-based IDs |

---

## Build Order

D4.1 (fingerprint) is independent. D4.2 (ShowCache) depends on D4.1. D4.3 (cached_compile_show) depends on D4.2. D4.4 (CLI) depends on D4.2 and D4.3.

| Phase | Step | Deliverable | Files Created/Modified |
|-------|------|-------------|----------------------|
| 1 | Implement `profile_fingerprint()` | D4.1 | `src/dreamsync/cache.py` (new) |
| 2 | Write fingerprint tests | Tests | `dev/tests/test_cache_fingerprint.py` (new) |
| 3 | Implement `ShowCache` | D4.2 | `src/dreamsync/cache.py` |
| 4 | Write cache tests | Tests | `dev/tests/test_cache.py` (new) |
| 5 | Implement `cached_compile_show()` + `path_based_track_id()` | D4.3 | `src/dreamsync/cache.py` |
| 6 | Write compile wrapper tests | Tests | `dev/tests/test_cache_compile.py` (new) |
| 7 | Add CLI subcommands + `--cache-dir` | D4.4 | `src/dreamsync/cli.py` |
| 8 | Write CLI tests | Tests | `dev/tests/test_cache_compile.py` (append) |

Note: Phases 1–2 can be done first, then 3–4, then 5–6, then 7–8 — strictly sequential due to dependencies.

---

## Non-Goals

- **Automatic cache size management** — No LRU eviction, no max-size cap. The cache grows unboundedly. Users can run `cache-clear` manually. A music library of 1000 songs cached at ~10 KB each = ~10 MB — negligible. Future enhancement if needed.
- **Compiler parameter sensitivity** — The cache does not account for changes to `compile_show()`'s keyword args (intro_intensity, peak_boost, etc.). If a user changes these tuning parameters, they must manually clear the cache. This is acceptable because these params are rarely changed, and the defaults are baked into the plan. Future enhancement: include params in the fingerprint.
- **Network/cloud sync** — The cache is local only. No syncing between machines.
- **SongStructure caching** — The cache stores compiled `ShowTimeline`s, not intermediate `SongStructure`s. Analysis is the expensive step (FFT, beat tracking), but caching raw analysis output is a separate concern. The `ShowTimeline` is the final, playable artifact — caching it avoids both analysis AND compilation on repeat plays.
- **Concurrent writes** — The cache assumes single-writer (one `dreamsync` session at a time). Atomic writes prevent corruption, but concurrent `put()` calls for the same key may overwrite each other. This is acceptable for a personal tool.
- **Cache warming / pre-population** — No background job to pre-compile the user's Spotify library. The cache is populated lazily as songs play. Pre-compilation could be added later as a `cache-warm` CLI command.

---

## Dependencies

| Dependency | Type | Notes |
|------------|------|-------|
| `ShowTimeline`, `ShowCue` | Existing code | `src/dreamsync/show/models.py` — to_dict/from_dict/to_json/from_json |
| `compile_show` | Existing code | `src/dreamsync/compiler/compile.py` — wrapped by `cached_compile_show()` |
| `ProfileConfig` | Existing code | `src/dreamsync/profile.py` — fingerprinted for cache keys |
| `SongStructure` | Existing code | `src/dreamsync/analyzer/models.py` — input to `compile_show()` on cache miss |
| `hashlib` | Stdlib | SHA-256 for fingerprinting |
| `json` | Stdlib | Canonical serialization for fingerprints, ShowTimeline I/O |
| `tempfile` | Stdlib | Atomic writes (NamedTemporaryFile + os.replace) |
| `shutil` | Stdlib | Directory removal in `invalidate()` and `clear()` |
| `datetime` | Stdlib | ISO 8601 timestamps for `_cache_compiled_at` |
| `os` | Stdlib | `os.replace()` for atomic rename, `os.scandir()` for listing |
| `pathlib` | Stdlib | Path operations throughout |
| `logging` | Stdlib | Cache hit/miss logging |

No new pip dependencies. The cache is pure file I/O + hashing — no database, no network, no new libraries.
