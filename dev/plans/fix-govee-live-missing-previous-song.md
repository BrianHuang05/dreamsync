# Fix: govee-live missing `previous_song` in track change callback

## Bug

`cli.py:_capture_track_changed` does not pass `previous_song` in the timing data sent to `CaptureOrchestrator.on_track_change()`. Without it, `TimingIntegrator.on_track_change()` never inserts an immediate boundary at the transition point. Splits rely entirely on `_tick()` boundaries, which are vulnerable to a race condition.

## Evidence (capture-boundary-2, 2026-03-06)

9 Spotify track changes detected, but only 7 MP3 files produced. The last file (segment 7, named "Eleanor Rigby") contains 3 songs (KK2G, die for, Eleanor Rigby) totaling 609.9 seconds.

### Timeline of the failure

Pipeline log around the KK2G → die_for transition (~frame 60M):

```
L1131 fp=60,042,150  replace_future  num_locked=0, num_new=21  playback_time=169.006
L1134 fp=60,064,200  replace_future  num_locked=0, num_new=1   playback_time=0.0  ← on_track_change
L1136 fp=60,262,650  replace_future  num_locked=0, num_new=21  playback_time=4.582
```

1. **L1131** — `_tick()` computes 21 boundaries. KK2G at 169s playback. With Spotify duration ~171s, remaining ~2s. Boundary placed at `fp + ~88,000 frames` (~2 seconds ahead).
2. **L1134** — `on_track_change()` fires 0.5s later. No `previous_song` → no immediate boundary. Calls `replace_future()` with 1 new boundary (end-of-die_for, far future). The KK2G boundary at ~88K frames ahead is **outside the 24K-frame safety margin** → `num_locked=0` → **replaced**.
3. Consumer never reaches the KK2G boundary. No split. Same failure repeats for die_for → Eleanor Rigby.

### Why the first 6 splits worked

| Transition | Pop frame | on_track_change frame | Delta (frames) | Result |
|---|---|---|---|---|
| 1 → 2 | 9,241,816 | 9,305,100 | +63,284 | Consumer popped first (1.4s before callback) |
| 2 → 3 | 20,960,642 | 20,947,500 | -13,142 | Boundary locked (within 24K margin), survived replace_future |
| 3 → 4 | 30,337,845 | 30,318,750 | -19,095 | Locked |
| 4 → 5 | 37,994,576 | 38,036,250 | +41,674 | Consumer popped first |
| 5 → 6 | 44,915,056 | 44,893,800 | -21,256 | Locked |
| 6 → 7 | 52,516,794 | 52,545,150 | +28,356 | Consumer popped first |
| 7 → 8 | — | 60,064,200 | — | Boundary ~88K frames ahead, NOT locked, replaced |
| 8 → 9 | — | 69,237,000 | — | Same failure |

The race is won by the consumer when `_tick()` places a boundary close enough that the consumer passes it before `on_track_change` fires. When the boundary is >24K frames ahead and `on_track_change` fires first, the boundary is replaced and the split is lost.

## Root cause

`cli.py` line 1207 — the `old` track parameter is available but not used:

```python
def _capture_track_changed(new, old, _orig=_orig_on_track):
    try:
        capture_orchestrator.on_track_change({
            "song_durations": [new.duration_ms / 1000.0],
            "current_playback_time": 0.0,
            "current_song": {
                "song_title": new.name,
                "artist": new.artist,
                "album": new.album,
            },
            # BUG: missing "previous_song" key
        })
    except Exception:
        pass
```

`session.py` line 228 does this correctly:

```python
if old is not None:
    timing_data["previous_song"] = {
        "song_title": old.name,
        "artist": old.artist,
        "album": old.album,
    }
```

## Fix

Add `previous_song` to the `govee-live` callback in `cli.py`, matching `session.py`:

```python
def _capture_track_changed(new, old, _orig=_orig_on_track):
    try:
        timing_data = {
            "song_durations": [new.duration_ms / 1000.0],
            "current_playback_time": 0.0,
            "current_song": {
                "song_title": new.name,
                "artist": new.artist,
                "album": new.album,
            },
        }
        if old is not None:
            timing_data["previous_song"] = {
                "song_title": old.name,
                "artist": old.artist,
                "album": old.album,
            }
        capture_orchestrator.on_track_change(timing_data)
    except Exception:
        pass
```

This causes `TimingIntegrator.on_track_change()` to insert an immediate boundary at `current_frame` with the old song's metadata. The immediate boundary is consumed by the consumer in the same chunk iteration — no opportunity for `_tick()` or another `replace_future()` to remove it.
