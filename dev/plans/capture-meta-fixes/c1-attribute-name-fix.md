# C1 — Attribute Name Fix (`_fetch_timing()`)

## Component
Capture-Meta Fixes — C1

## Prerequisites
- None (independent fix)
- Understanding of `QueueSnapshot` dataclass (`src/dreamsync/spotify/models.py:36-42`)
- Understanding of `PlaybackState` dataclass (`src/dreamsync/spotify/models.py:23-33`)
- Understanding of `SpotifyQueueWatcher` properties (`src/dreamsync/spotify/queue_watcher.py`)

## Goal
Fix `_fetch_timing()` in both `cli.py` and `session.py` so that it correctly accesses `QueueSnapshot.currently_playing`, retrieves `progress_ms` from `PlaybackState` (not `SpotifyTrack`), handles `None` values safely, and uses correct list/tuple concatenation.

---

## Sub-Issues (all within `_fetch_timing()`)

### S1: `queue.current` → `queue.currently_playing`

`QueueSnapshot` defines the field as `currently_playing`, not `current`. Every access to `queue.current` raises `AttributeError`.

**Current (broken):**
```python
for t in [queue.current] + queue.queue
queue.current.progress_ms
queue.current.name
queue.current.artist
getattr(queue.current, "album", None)
```

**Fixed:**
```python
queue.currently_playing
```

### S2: `currently_playing.progress_ms` does not exist on `SpotifyTrack`

`SpotifyTrack` fields: `track_id`, `name`, `artist`, `album`, `duration_ms`, `uri`.
`progress_ms` is on `PlaybackState`, which is available via `spotify_watcher.playback_state`.

**Current (broken):**
```python
"current_playback_time": queue.current.progress_ms / 1000.0,
```

**Fixed:**
```python
pb = spotify_watcher.playback_state
"current_playback_time": (pb.progress_ms / 1000.0) if pb else 0.0,
```

### S3: `[list] + tuple` TypeError

`queue.queue` is `tuple[SpotifyTrack, ...]`. Concatenating `[SpotifyTrack] + tuple(...)` raises `TypeError: can only concatenate list (not "tuple") to list`.

**Current (broken):**
```python
for t in [queue.current] + queue.queue
```

**Fixed (use unpacking):**
```python
for t in [queue.currently_playing, *queue.queue]
```

### S4: `currently_playing` may be `None`

When nothing is playing, `QueueSnapshot.currently_playing` is `None`. Accessing `.progress_ms`, `.name`, `.artist` on `None` raises `AttributeError`. The function should return `None` early.

**Fix:** Add null guard after the existing `queue is None` check:
```python
if queue is None or queue.currently_playing is None:
    return None
```

---

## Implementation

### File: `src/dreamsync/cli.py` (~L1168-1184)

**Before:**
```python
def _fetch_timing():
    queue = spotify_watcher.queue
    if queue is None:
        return None
    return {
        "song_durations": [
            t.duration_ms / 1000.0
            for t in [queue.current] + queue.queue
        ],
        "current_playback_time": queue.current.progress_ms / 1000.0,
        "current_song": {
            "song_title": queue.current.name,
            "artist": queue.current.artist,
            "album": getattr(queue.current, "album", None),
        },
    }
```

**After:**
```python
def _fetch_timing():
    queue = spotify_watcher.queue
    if queue is None or queue.currently_playing is None:
        return None
    current = queue.currently_playing
    pb = spotify_watcher.playback_state
    return {
        "song_durations": [
            t.duration_ms / 1000.0
            for t in [current, *queue.queue]
        ],
        "current_playback_time": (pb.progress_ms / 1000.0) if pb else 0.0,
        "current_song": {
            "song_title": current.name,
            "artist": current.artist,
            "album": current.album,
        },
    }
```

### File: `src/dreamsync/session.py` (~L232-248)

**Before:**
```python
def _fetch_timing():
    queue = spotify_watcher.queue
    if queue is None:
        return None
    return {
        "song_durations": [
            t.duration_ms / 1000.0
            for t in [queue.current] + queue.queue
        ],
        "current_playback_time": queue.current.progress_ms / 1000.0,
        "current_song": {
            "song_title": queue.current.name,
            "artist": queue.current.artist,
            "album": getattr(queue.current, "album", None),
        },
    }
```

**After:**
```python
def _fetch_timing():
    queue = spotify_watcher.queue
    if queue is None or queue.currently_playing is None:
        return None
    current = queue.currently_playing
    pb = spotify_watcher.playback_state
    return {
        "song_durations": [
            t.duration_ms / 1000.0
            for t in [current, *queue.queue]
        ],
        "current_playback_time": (pb.progress_ms / 1000.0) if pb else 0.0,
        "current_song": {
            "song_title": current.name,
            "artist": current.artist,
            "album": current.album,
        },
    }
```

### Notes
- `getattr(queue.current, "album", None)` → `current.album`: `SpotifyTrack` always has `album` (defaults to `""` via `parse_track`), so `getattr` fallback is unnecessary.
- `spotify_watcher` is already in closure scope for both functions — accessing `.playback_state` requires no new imports or parameters.

---

## Tests

Test file: `dev/tests/test_fetch_timing.py`

### Unit tests (mock `spotify_watcher`):

- [ ] `test_fetch_timing_returns_none_when_queue_is_none` — Set `spotify_watcher.queue = None`. Assert `_fetch_timing()` returns `None`.

- [ ] `test_fetch_timing_returns_none_when_currently_playing_is_none` — Set `spotify_watcher.queue = QueueSnapshot(currently_playing=None, queue=(), fetched_at=0)`. Assert `_fetch_timing()` returns `None`.

- [ ] `test_fetch_timing_valid_queue` — Create `QueueSnapshot` with a `SpotifyTrack` as `currently_playing` and 2 tracks in `queue`. Set `spotify_watcher.playback_state = PlaybackState(progress_ms=30000, ...)`. Assert return dict has:
  - `song_durations` is a list of 3 floats (current + 2 queue)
  - `current_playback_time` == 30.0
  - `current_song["song_title"]` matches track name
  - `current_song["artist"]` matches track artist
  - `current_song["album"]` matches track album

- [ ] `test_fetch_timing_empty_queue` — Create `QueueSnapshot` with `currently_playing` set but `queue=()`. Assert `song_durations` has 1 entry.

- [ ] `test_fetch_timing_no_playback_state` — Set `spotify_watcher.playback_state = None`. Assert `current_playback_time` == 0.0 (fallback).

- [ ] `test_fetch_timing_unicode_track_names` — Create tracks with Japanese characters (e.g., `name="ロベリア"`, `artist="りぶ"`). Assert no exception raised and values appear in returned dict.

---

## Acceptance Criteria

- [ ] `queue.current` does not appear anywhere in `cli.py` or `session.py`
- [ ] `queue.currently_playing` is used in both `_fetch_timing()` functions
- [ ] `progress_ms` is sourced from `spotify_watcher.playback_state`, not from `SpotifyTrack`
- [ ] Null guard handles `queue is None` and `queue.currently_playing is None`
- [ ] No `TypeError` from list/tuple concatenation
- [ ] All 6 unit tests pass
- [ ] `_fetch_timing()` returns correct dict structure when queue and playback state are valid
- [ ] `_fetch_timing()` returns `None` safely when data is unavailable
