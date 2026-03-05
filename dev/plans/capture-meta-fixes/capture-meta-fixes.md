# Capture-Meta Fixes — Remediation Plan

## Overview

Fix three bugs discovered during Test 5.5 (600s capture run with `--capture-naming metadata --spotify`, playing Japanese-language tracks by Rib). All three bugs prevent the capture pipeline from receiving Spotify timing/metadata, resulting in broken segment splitting and missing metadata on output files.

**Source analysis:** `dev/plans/capture-meta-log-analysis.md`

---

## Root Cause Summary

```
SpotifyQueueWatcher._poll_playback()
├── Detects track change
├── Calls _on_track_changed(new_track, old_track)
│   └── _capture_track_changed(new, old)
│       ├── _orig(new, old)                          ◄── C2: UnicodeEncodeError on non-ASCII
│       │   └── print(f"...'{new.name}' by {new.artist}")   (kills whole callback)
│       │                                              ◄── C3: Exception propagates, skips next line
│       └── capture_orchestrator.on_track_change(...)  ◄── NEVER REACHED
│
TimingIntegrator._tick()  (every 5s)
└── Calls _fetch_fn()
    └── _fetch_timing()
        └── queue.current                              ◄── C1: AttributeError (field is "currently_playing")
```

**Net effect:** The capture orchestrator receives zero timing updates and zero track-change events. Segments either lack metadata or are never split.

---

## Components

| # | Component | Files Modified | Description |
|---|-----------|---------------|-------------|
| C1 | Attribute Name Fix | `cli.py`, `session.py` | Fix 4 sub-issues in `_fetch_timing()`: attribute name (`queue.current` → `queue.currently_playing`), `progress_ms` source (from `PlaybackState` not `SpotifyTrack`), list+tuple concatenation, and null guard |
| C2 | Unicode-Safe Callback Output | `cli.py`, `session.py` | Make `print()` calls in track-change callbacks tolerant of non-ASCII characters on Windows |
| C3 | Callback Exception Isolation | `cli.py`, `session.py` | Restructure `_capture_track_changed` so a failure in the display callback cannot prevent the orchestrator call |

---

## Dependency Chain

```
C1 (Attribute Name Fix)         — independent, no prerequisites
C2 (Unicode-Safe Output)        — independent, no prerequisites
C3 (Callback Exception Isolation) — independent, no prerequisites
     │
     ▼
  Validation (run Test 5.5 again)
```

All three components are independent and can be implemented in any order or in parallel. They touch different code sections within the same files. Validation requires all three to be complete.

---

## Files Modified

| File | Components | Sections |
|------|-----------|----------|
| `src/dreamsync/cli.py` | C1, C2, C3 | `_fetch_timing()` (~L1168-1184), `_capture_track_changed()` (~L1146-1162), track-change lambda (~L1116-1118) |
| `src/dreamsync/session.py` | C1, C2, C3 | `_fetch_timing()` (~L232-248), `_capture_track_changed()` (~L196-210), track-change lambda (~L159-161) |

---

## Validation Criteria

- [ ] `_fetch_timing()` returns a valid dict when `QueueSnapshot.currently_playing` is populated
- [ ] `_fetch_timing()` returns `None` gracefully when `QueueSnapshot.currently_playing` is `None`
- [ ] Track-change callback prints track info for ASCII track names
- [ ] Track-change callback prints track info (with replacements) for non-ASCII track names (e.g., Japanese)
- [ ] Track-change callback always calls `capture_orchestrator.on_track_change()` even when `_orig()` raises
- [ ] Song boundaries are populated in capture output during a multi-track session
- [ ] Segment files receive metadata when `--capture-naming metadata` is used
- [ ] No `AttributeError` on `queue.current` in logs
- [ ] No `UnicodeEncodeError` in logs for non-ASCII track names

---

## Additional Bugs Discovered During Plan Analysis

The original analysis identified 3 issues. During plan creation, deeper code inspection revealed 2 additional bugs in `_fetch_timing()` that would have surfaced immediately after fixing the attribute name:

| Bug | Description | Component |
|-----|------------|-----------|
| `SpotifyTrack` has no `progress_ms` | `queue.currently_playing.progress_ms` → `AttributeError`. The `progress_ms` field lives on `PlaybackState`, accessible via `spotify_watcher.playback_state`. | C1 (S2) |
| `list + tuple` TypeError | `[queue.currently_playing] + queue.queue` fails because `queue.queue` is `tuple[SpotifyTrack, ...]` and Python can't concatenate `list + tuple`. | C1 (S3) |

Both are addressed in C1's implementation spec.

---

## Risk Assessment

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| `currently_playing` is `None` (no active playback) | Medium | Add null guard in `_fetch_timing()` before accessing `.progress_ms`, `.name`, `.artist` |
| Other non-ASCII edge cases beyond Japanese (emoji, RTL, etc.) | Low | `errors="replace"` handles all non-encodable characters generically |
| `session.py` has same callback pattern as `cli.py` | Medium | Audit `session.py` for equivalent track-change wiring; apply C2/C3 if present |

---

## Component Details

- `c1-attribute-name-fix.md` — Full specification for the `queue.current` → `queue.currently_playing` fix
- `c2-unicode-safe-output.md` — Full specification for encoding-safe print in callbacks
- `c3-callback-exception-isolation.md` — Full specification for restructuring the callback chain
