# Capture-Meta Log Analysis (Test 5.5)

**Run:** 600s (10 min), `--capture-naming metadata --spotify`, playing Rib songs (Japanese artist)

---

## Issue 1: `QueueSnapshot.current` should be `currently_playing` (~120 occurrences)

The `_fetch_timing()` function in both `cli.py:1175` and `session.py:239` references `queue.current`, but the `QueueSnapshot` dataclass (`spotify/models.py:40`) uses `currently_playing`. Every timing refresh tick failed with:

```
AttributeError: 'QueueSnapshot' object has no attribute 'current'
```

The timing integrator never successfully refreshed boundaries throughout the entire 10-minute run.

**Files:** `cli.py` (~L1175), `session.py` (~L239)
**Fix:** `queue.current` → `queue.currently_playing`

---

## Issue 2: `charmap` UnicodeEncodeError on track change (2 occurrences)

The `on_track_changed` callback wraps the original lambda that does `print(f"Spotify: now playing '{new.name}' by {new.artist}")`. When track names/artists contain Japanese characters (Rib's songs), Windows `charmap` codec can't encode them:

```
on_track_changed callback error: 'charmap' codec can't encode characters in position 22-29: character maps to <undefined>
```

The error happens inside `_capture_track_changed` → `_orig(new, old)`. Because the exception propagates up, the `capture_orchestrator.on_track_change()` call is **skipped**, silently losing those song boundaries.

**Files:** `cli.py` (~L1146-1160)
**Fix:** Use `errors="replace"` or encode-safe print; also move `_orig()` into its own try/except so a print failure doesn't skip the orchestrator call.

---

## Issue 3: Song change detection partially works but capture splitting is broken

- "Spotify: now playing" messages appear only **twice**: `'Lobelia' by Rib` (line 28) and `'Otome Kaibou' by Rib` (line 10287)
- `song_boundaries: 3` in final JSON — 3 boundaries detected, only 2 segments saved
- First saved file (`segment_000001.mp3`) has **no metadata** — just a timestamp name
- Second saved file (`segment_000002.mp3`) has metadata: `(Rib - Otome Kaibou)`
- `--capture-naming metadata` fell back to timestamp naming for the first segment because the charmap error prevented the track-change callback from reaching the orchestrator

---

## Fix Summary

| Issue | Impact | Fix |
|---|---|---|
| `queue.current` → `queue.currently_playing` | Timing refresh 100% broken, every tick fails | Fix attribute name in `cli.py` and `session.py` |
| `charmap` encode error on `print()` | Silently drops song boundaries for non-ASCII track names | Use `errors="replace"` or encode-safe print |
| Callback exception skips `on_track_change` | Capture orchestrator misses song boundaries when print fails | Move `_orig()` call into its own try/except, or call orchestrator first |

---

## Previously Fixed (before this run)

- `spotify_watcher.get_queue()` → `spotify_watcher.queue` (property, not method) — fixed in both `cli.py` and `session.py`
