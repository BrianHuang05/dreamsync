# C3 — Callback Exception Isolation

## Component
Capture-Meta Fixes — C3

## Prerequisites
- None (independent fix, but applies cleanly after C2)
- Understanding of the callback wrapping pattern in `cli.py` and `session.py`
- Understanding that `_capture_track_changed` wraps the original display callback and the orchestrator call

## Goal
Restructure `_capture_track_changed()` so that an exception in the display callback (`_orig`) can never prevent `capture_orchestrator.on_track_change()` from being called. The orchestrator call is the critical path; the display print is informational only.

---

## Problem

### Current Code (`cli.py` L1146-1160):
```python
def _capture_track_changed(new, old, _orig=_orig_on_track):
    if _orig:
        _orig(new, old)          # <-- Exception here...
    try:
        capture_orchestrator.on_track_change({  # <-- ...prevents this from executing
            "song_durations": [new.duration_ms / 1000.0],
            "current_playback_time": 0.0,
            "current_song": {
                "song_title": new.name,
                "artist": new.artist,
                "album": getattr(new, "album", None),
            },
        })
    except Exception:
        pass
```

### Identical pattern in `session.py` L196-210.

### What Goes Wrong
1. `_orig(new, old)` calls `print(f"Spotify: now playing '{new.name}' by {new.artist}")`
2. On Windows with non-ASCII track names, this raises `UnicodeEncodeError`
3. The exception propagates **before** `capture_orchestrator.on_track_change()` is reached
4. The `SpotifyQueueWatcher` catches the exception at the top level and logs it as a warning, but the orchestrator never receives the song boundary
5. The capture pipeline misses the track change entirely

### Why This Matters Even With C2
C2 fixes the specific `UnicodeEncodeError`, but `_orig` could fail for other unforeseen reasons (e.g., stdout closed, pipe broken, future callback changes). The orchestrator call must be resilient to **any** `_orig` failure, not just Unicode errors.

---

## Design Decision

**Approach: Call orchestrator first, then display callback.**

The orchestrator call is the critical-path operation. The display print is purely informational. By reordering and wrapping `_orig` in its own try/except, we guarantee the orchestrator always receives track changes regardless of display failures.

**Alternative considered:** Wrap only `_orig` in try/except while keeping the current order. This works but is less clear about priority. Putting the critical call first makes the intent explicit.

**Chosen approach:** Orchestrator first, then `_orig` in try/except.

---

## Implementation

### File: `src/dreamsync/cli.py` (~L1146-1160)

**Before:**
```python
def _capture_track_changed(new, old, _orig=_orig_on_track):
    if _orig:
        _orig(new, old)
    try:
        capture_orchestrator.on_track_change({
            "song_durations": [new.duration_ms / 1000.0],
            "current_playback_time": 0.0,
            "current_song": {
                "song_title": new.name,
                "artist": new.artist,
                "album": getattr(new, "album", None),
            },
        })
    except Exception:
        pass
```

**After:**
```python
def _capture_track_changed(new, old, _orig=_orig_on_track):
    # Critical path: notify orchestrator of track change
    try:
        capture_orchestrator.on_track_change({
            "song_durations": [new.duration_ms / 1000.0],
            "current_playback_time": 0.0,
            "current_song": {
                "song_title": new.name,
                "artist": new.artist,
                "album": new.album,
            },
        })
    except Exception:
        pass
    # Informational: display track change to user
    if _orig:
        try:
            _orig(new, old)
        except Exception:
            pass
```

### File: `src/dreamsync/session.py` (~L196-210)

**Before:**
```python
def _capture_track_changed(new, old, _orig=_orig_on_track):
    if _orig:
        _orig(new, old)
    try:
        capture_orchestrator.on_track_change({
            "song_durations": [new.duration_ms / 1000.0],
            "current_playback_time": 0.0,
            "current_song": {
                "song_title": new.name,
                "artist": new.artist,
                "album": getattr(new, "album", None),
            },
        })
    except Exception:
        pass
```

**After:**
```python
def _capture_track_changed(new, old, _orig=_orig_on_track):
    # Critical path: notify orchestrator of track change
    try:
        capture_orchestrator.on_track_change({
            "song_durations": [new.duration_ms / 1000.0],
            "current_playback_time": 0.0,
            "current_song": {
                "song_title": new.name,
                "artist": new.artist,
                "album": new.album,
            },
        })
    except Exception:
        pass
    # Informational: display track change to user
    if _orig:
        try:
            _orig(new, old)
        except Exception:
            pass
```

### Notes
- `getattr(new, "album", None)` → `new.album`: `SpotifyTrack` always has an `album` field (defaults to `""` via `parse_track`), so the `getattr` fallback is unnecessary.
- The `except Exception: pass` on the orchestrator call is retained as-is since the orchestrator has its own internal error handling and logging.
- The `except Exception: pass` on `_orig` is intentionally broad — display failures should never disrupt the capture pipeline.

---

## Tests

Test file: `dev/tests/test_callback_isolation.py`

- [ ] `test_orchestrator_called_when_orig_succeeds` — Create mock `_orig` that succeeds and mock `capture_orchestrator`. Call `_capture_track_changed(track, None)`. Assert `capture_orchestrator.on_track_change` was called with correct dict. Assert `_orig` was also called.

- [ ] `test_orchestrator_called_when_orig_raises` — Create mock `_orig` that raises `UnicodeEncodeError`. Call `_capture_track_changed(track, None)`. Assert `capture_orchestrator.on_track_change` was still called. Assert no exception propagates.

- [ ] `test_orchestrator_called_when_orig_raises_any_exception` — Create mock `_orig` that raises `RuntimeError`. Assert `capture_orchestrator.on_track_change` was still called.

- [ ] `test_orig_called_when_orchestrator_raises` — Create mock `capture_orchestrator.on_track_change` that raises. Assert `_orig` is still called afterward. Assert no exception propagates.

- [ ] `test_both_raise_no_propagation` — Both `_orig` and `capture_orchestrator.on_track_change` raise exceptions. Assert no exception propagates out of `_capture_track_changed`.

- [ ] `test_orig_none_skips_display` — Set `_orig=None`. Assert `capture_orchestrator.on_track_change` is called. Assert no `NoneType` error.

- [ ] `test_orchestrator_receives_correct_metadata` — Pass a `SpotifyTrack` with known values. Assert the dict passed to `on_track_change` has correct `song_title`, `artist`, `album`, `song_durations`, and `current_playback_time`.

---

## Acceptance Criteria

- [ ] `capture_orchestrator.on_track_change()` is called **before** `_orig(new, old)` in both files
- [ ] `_orig(new, old)` is wrapped in its own `try/except Exception`
- [ ] An exception in `_orig` does not prevent the orchestrator call
- [ ] An exception in the orchestrator call does not prevent `_orig` from running
- [ ] No exceptions propagate out of `_capture_track_changed()`
- [ ] The metadata dict passed to `on_track_change` uses `new.album` directly (no `getattr` fallback)
- [ ] All 7 tests pass
- [ ] Pattern is applied identically in both `cli.py` and `session.py`
