# D5 — CLI Integration

## Prerequisites

- D2 (Capture Data Path) — data pipeline wired and tested
- D3 (Boundary Control Path) — timing/boundary pipeline wired and tested
- D4 (Recovery & Logging) — recovery and logging wired and tested

## Overview

Update the CLI to use `CaptureOrchestrator` instead of `StreamCapturePipeline`. Two integration points:

1. **`dreamsync session --capture`** — Replace StreamCapturePipeline with CaptureOrchestrator in `session.py`
2. **`dreamsync capture`** — Update the standalone capture subcommand to run CaptureOrchestrator directly

---

## Implementation Items

### 5.1 Update `dreamsync session --capture` (session.py)

Replace the current StreamCapturePipeline integration (session.py lines 168-207) with CaptureOrchestrator:

```python
# Current code (to be replaced):
from dreamsync.capture.pipeline import CaptureConfig, StreamCapturePipeline
...
capture_pipeline = StreamCapturePipeline(config=cap_cfg, on_song_saved=...)

# New code:
from dreamsync.capture.orchestrator import CaptureOrchestrator, OrchestratorConfig

orch_cfg = OrchestratorConfig(
    sample_rate=48000,       # PCM capture at 48kHz (independent of feature extraction rate)
    channels=2,              # Stereo capture
    output_dir=capture_dir,
    naming=capture_naming,
    log_dir=str(Path(capture_dir) / "logs"),
)
capture_orchestrator = CaptureOrchestrator(
    config=orch_cfg,
    on_segment_saved=lambda path, meta: print(
        f"Capture: saved {Path(path).name} ({meta.get('artist', 'unknown')} - {meta.get('song_title', 'unknown')})"
    ),
)
```

### 5.2 Start/stop wiring in session.py

Start the orchestrator before the live loop, stop on shutdown:

```python
# Before live loop:
if capture_orchestrator:
    capture_orchestrator.start()
    if spotify_watcher:
        def _fetch_timing():
            queue = spotify_watcher.get_queue()
            if queue is None:
                return None
            return {
                "song_durations": [t.duration_ms / 1000.0 for t in [queue.current] + queue.queue],
                "current_playback_time": queue.current.progress_ms / 1000.0,
                "current_song": {
                    "song_title": queue.current.name,
                    "artist": queue.current.artist,
                    "album": queue.current.album,
                },
            }
        capture_orchestrator.start_periodic_timing(_fetch_timing)

# In shutdown (finally block):
if capture_orchestrator:
    capture_orchestrator.shutdown()
    stats = capture_orchestrator.stats
    print(f"Capture: {stats['segments_completed']} songs saved, "
          f"{stats['elapsed_seconds']:.0f}s captured, "
          f"{stats['drift_corrections']} drift corrections")
```

### 5.3 Spotify track change integration

Connect Spotify track changes to the orchestrator:

```python
if spotify_watcher and capture_orchestrator:
    _orig_on_track = spotify_watcher._on_track_changed

    def _capture_track_changed(new, old, _orig=_orig_on_track):
        if _orig:
            _orig(new, old)
        capture_orchestrator.on_track_change({
            "song_durations": [new.duration_ms / 1000.0],
            "current_playback_time": 0.0,
            "current_song": {
                "song_title": new.name,
                "artist": new.artist,
                "album": getattr(new, "album", None),
            },
        })

    spotify_watcher._on_track_changed = _capture_track_changed
```

### 5.4 Remove `capture_pipeline` pass-through to `run_live_to_govee()`

The current code passes `capture_pipeline` to `run_live_to_govee()` which calls `capture_pipeline.feed()` per audio frame. With the new orchestrator, capture runs independently (FFmpeg → PCM → SplitProcessor) and does not depend on the feature extraction audio loop. Remove the `capture_pipeline` parameter from `run_live_to_govee()`.

### 5.5 Update `dreamsync capture` subcommand

The current `capture` subcommand does feature extraction, not MP3 capture. Add new arguments to support standalone pipeline capture:

```python
# Add to existing capture subcommand arguments:
capture.add_argument(
    "--mp3",
    action="store_true",
    default=False,
    help="Enable MP3 capture pipeline (records per-song MP3 files).",
)
capture.add_argument(
    "--output-dir",
    type=str,
    default="captured_songs",
    help="Output directory for captured MP3 files (default: captured_songs).",
)
capture.add_argument(
    "--naming",
    choices=["timestamp", "metadata"],
    default="timestamp",
    help="Filename scheme for captured songs (default: timestamp).",
)
capture.add_argument(
    "--device-pattern",
    type=str,
    default="CABLE Output",
    help="DirectShow audio device name pattern (default: CABLE Output).",
)
```

### 5.6 Capture subcommand handler

```python
elif args.command == "capture":
    if getattr(args, "mp3", False):
        from dreamsync.capture.orchestrator import CaptureOrchestrator, OrchestratorConfig

        orch_cfg = OrchestratorConfig(
            sample_rate=args.sample_rate,
            channels=getattr(args, "channels", 2),
            device_pattern=args.device_pattern,
            output_dir=args.output_dir,
            naming=args.naming,
        )
        orchestrator = CaptureOrchestrator(
            config=orch_cfg,
            on_segment_saved=lambda path, meta: print(
                f"Saved: {Path(path).name}"
            ),
        )
        orchestrator.start()
        print(f"Capturing MP3 to {args.output_dir}/ (Ctrl+C to stop)")

        import signal, threading
        stop = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        stop.wait(timeout=args.duration)

        orchestrator.shutdown()
        stats = orchestrator.stats
        print(f"Done: {stats['segments_completed']} segments, "
              f"{stats['elapsed_seconds']:.0f}s captured")
    else:
        # Existing feature extraction capture (unchanged)
        ...
```

### 5.7 CLI help text updates

Update help strings to reflect new capabilities:

```
dreamsync capture --duration 300 --mp3 --output-dir ./songs --naming metadata
dreamsync session --config devices.yaml --capture --capture-dir ./songs --capture-naming metadata --spotify
```

### 5.8 Govee-live capture flags

The `govee-live` command also supports `--capture` via the session flow. Add the same capture flags to `govee-live`:

```python
govee_live.add_argument("--capture", action="store_true", default=False, ...)
govee_live.add_argument("--capture-dir", type=str, default="captured_songs", ...)
govee_live.add_argument("--capture-naming", choices=["timestamp", "metadata"], ...)
```

These are already partially present; ensure they route to `CaptureOrchestrator` instead of `StreamCapturePipeline`.

---

## Files Modified

```
src/dreamsync/session.py     — Replace StreamCapturePipeline with CaptureOrchestrator
src/dreamsync/cli.py         — Add --mp3 flag to capture subcommand, update help
src/dreamsync/live.py        — Remove capture_pipeline parameter (if present)
```

## Tests

Added to `dev/tests/test_orchestrator.py` or `dev/tests/test_cli_capture.py`:

- [x] `test_orchestrator_config_from_session_args` — verify OrchestratorConfig constructed correctly from session args
- [x] `test_orchestrator_shutdown_idempotent` — verify orchestrator.shutdown() safe without start()
- [x] `test_capture_mp3_flag_parses` — `dreamsync capture --duration 60 --mp3` parses correctly
- [x] `test_capture_mp3_with_all_options` — all MP3 capture options parse
- [x] `test_capture_mp3_defaults` — MP3 capture defaults correct
- [x] `test_capture_without_mp3_unchanged` — existing feature extraction behavior preserved
- [x] `test_govee_live_capture_flag` — govee-live --capture flag parses
- [x] `test_govee_live_capture_dir_and_naming` — govee-live capture options parse
- [x] `test_govee_live_capture_defaults` — govee-live capture defaults correct
- [x] `test_track_change_data_format` — timing data dict format matches
- [x] `test_track_change_reaches_orchestrator` — on_track_change() receives timing data

---

## Passing Criteria

- [x] `dreamsync session --capture` uses `CaptureOrchestrator` (not StreamCapturePipeline)
- [x] Capture runs independently of the feature extraction audio loop (FFmpeg → PCM, not PortAudio → PCM)
- [x] Spotify track changes trigger `orchestrator.on_track_change()`
- [x] Periodic timing refresh fetches Spotify queue and updates boundary queue
- [x] `dreamsync capture --mp3` runs a standalone capture pipeline without Govee devices
- [x] Orderly shutdown prints summary stats (segments saved, elapsed time, drift corrections)
- [x] Existing `dreamsync capture` (feature extraction) still works unchanged
- [x] `govee-live --capture` routes to the new orchestrator
- [x] All D5 tests pass (13/13)
