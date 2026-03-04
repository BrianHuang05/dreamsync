# DreamSync — Next Steps

## Quick Reference

- **Project**: Local audio-reactive Govee LED controller (LAN UDP / BLE). v2 complete, building v3.
- **v3 vision**: Pre-sequenced show engine. See `dev/plans/v3-show-sequencer.md`.
- **Platform**: Windows 11, bash/Unix shell syntax, Python 3.11+
- **Install**: `pip install -e ".[session]"`
- **Tests**: `pytest dev/tests/` (870 tests)
- **Lint**: `ruff check src/`
- **Branch**: `govee-lan-direct` (primary)

---

## v3 Feature Checklist

| # | Feature | Status | Plan |
|---|---------|--------|------|
| 1 | Spotify Queue Watcher | **DONE** (`7c3372b`) | `dev/plans/feature-1-spotify-queue-watcher.md` |
| 2 | Song Structure Analyzer | **DONE** (C3+C4+C5 code complete — 213 tests) | `dev/plans/feature-2-song-structure-analyzer.md` |
| 3 | Show Compiler | **DONE** (C1–C5 complete — 58 tests) | `dev/plans/feature-3-show-compiler.md` |
| 4 | Show Cache | Not started | — |
| 5 | Playback Runtime | Not started | — |
| 6 | v2 Fallback Switch | Not started | — |

### Feature 3 Component Status

| # | Component | Status | Tests |
|---|-----------|--------|-------|
| C1 | NarrativeArcPlanner | **DONE** | 12 tests (`test_compiler_arc.py`) |
| C2 | TreatmentSelector | **DONE** | 15 tests (`test_compiler_treatments.py`) |
| C3 | TransitionPlanner | **DONE** | 10 tests (`test_compiler_transitions.py`) |
| C4 | TimelineAssembler | **DONE** | 8 tests (`test_compiler_assemble.py`) |
| C5 | compile_show() Orchestrator + CLI | **DONE** | 13 tests (`test_compiler_compile.py`) |

### Feature 2 Component Status

| # | Component | Status | Plan |
|---|-----------|--------|------|
| 3 | mp3 Storage | **Code complete** (68 unit tests) — needs manual validation, see `dev/VALIDATION_TESTS.md` Phase 5 | `dev/plans/feature-2-component-3-mp3-storage.md` |
| 4 | Analyzer | **Code complete** (80 unit tests) — needs manual validation with real mp3 files | `dev/plans/feature-2-component-4-analyzer.md` |
| 5 | Show Player | **Code complete** (65 unit tests) — needs manual validation with real devices | `dev/plans/feature-2-component-5-show-player.md` |

### Feature 1 Deferred Items

These are implemented in code and unit-tested, but not manually validated end-to-end. They will be exercised during normal development:

- Graceful degradation (no token → v2 fallback message, session continues)
- Token auto-refresh during sessions longer than 1 hour
- Lint pass (`ruff check src/` — ruff not currently installed)

---

## Next: Feature 4 — Show Cache

**Status**: Not started — needs plan.

The Show Cache stores compiled shows by Spotify track ID so that re-analysis can be skipped for songs that have been compiled before. Cache invalidation triggers on profile change or manual flush.

---

## Previous: Feature 3 — Show Compiler ✅

Plan: `dev/plans/feature-3-show-compiler.md`

**Status**: Code complete (58 unit tests across 5 files). Ready for manual validation.

### What it does

Takes a `SongStructure` (from the Analyzer) and a `ProfileConfig` (active lighting profile) and compiles a complete `ShowTimeline` — a self-contained JSON file of timestamped lighting cues. The compiler selects lighting treatments per section (effect mode, color palette, intensity, speed), shapes a narrative arc across the song, plans transitions (cut vs. fade), and assembles everything into a playable timeline.

### Architecture

```
SongStructure → NarrativeArcPlanner → TreatmentSelector → TransitionPlanner → TimelineAssembler → ShowTimeline
```

Orchestrated by `compile_show(structure, profile, **kwargs)` — single function call, structure in, timeline out.

### Files created

```
src/dreamsync/compiler/
├── __init__.py          # Re-export compile_show
├── arc.py               # NarrativeArcPlanner (C1)
├── treatments.py        # TreatmentSelector (C2)
├── transitions.py       # TransitionPlanner (C3)
├── assemble.py          # TimelineAssembler (C4)
└── compile.py           # compile_show() orchestrator + format_summary() (C5)
```

### Tests (58 total)

```
dev/tests/test_compiler_arc.py          — 12 tests
dev/tests/test_compiler_treatments.py   — 15 tests
dev/tests/test_compiler_transitions.py  — 10 tests
dev/tests/test_compiler_assemble.py     —  8 tests
dev/tests/test_compiler_compile.py      — 13 tests
```

### CLI subcommands added

- `dreamsync compile structure.json --output show.json [--profile NAME] [--seed N] [--summary]`
- `dreamsync compile-and-play song.mp3 --config devices.yaml [--profile NAME] [--seed N] [--debug]`

---

## Previous: Feature 2, Component 5 — Show Playback Runtime ✅

Plan: `dev/plans/feature-2-component-5-show-player.md`

**Status**: Code complete (65 unit tests). Ready for manual validation with real devices.

### What it does

Takes an mp3 audio file and a compiled show file (timestamped lighting cues with a beat grid), plays the audio through the system's default speaker output, and simultaneously dispatches lighting commands to Govee devices in real-time sync. This is the pre-sequenced counterpart to `run_live_to_govee()`.

### Key design decisions

- **Reuses entire v2 rendering stack** — SegmentRenderer, MultiGoveeLanAdapter, GoveeBleAdapter, device roles, all unchanged.
- **Self-contained show file format** — `ShowTimeline` JSON embeds beat grid + cues. Player needs no other data source at runtime.
- **Audio playback via sounddevice** — already a dependency for audio input. `Mp3Decoder` (C4) decodes mp3 → PCM, `sounddevice.OutputStream` plays to system speakers.
- **No Director / DSP in the loop** — the main loop is: read position → find cue → build intent → render → send. Orders of magnitude simpler than `run_live_to_govee()`.
- **Fade transitions** — intensity and speed interpolate over N beats between cues. Render mode switches immediately.
- **No new pip deps** — sounddevice + numpy + ffmpeg only.

### Files created

```
src/dreamsync/show/
├── __init__.py        # Package init
├── models.py          # ShowTimeline, ShowCue (D5.1)
├── player.py          # AudioPlayer (D5.2)
└── runtime.py         # ShowPlaybackRuntime + run_show_playback() (D5.3)
```

### Tests (65 total)

```
dev/tests/test_show_models.py      — 26 tests
dev/tests/test_show_player.py      — 13 tests
dev/tests/test_show_runtime.py     — 19 tests
dev/tests/test_show_cli.py         —  7 tests
```

### CLI subcommand

```bash
dreamsync play song.mp3 --show show.json --config devices.yaml [--debug] [--audio-device N]
```

---

## Component 3 Summary (for context)

mp3 capture pipeline is live. Key files:

- `src/dreamsync/capture/buffer.py` — `AudioStreamBuffer` (thread-safe growable PCM buffer)
- `src/dreamsync/capture/boundary.py` — `CompositeBoundaryDetector` (Spotify + silence + crossfade signals)
- `src/dreamsync/capture/writer.py` — `SongFileWriter` (PCM→mp3 via ffmpeg, metadata/timestamp naming)
- `src/dreamsync/capture/pipeline.py` — `StreamCapturePipeline` (buffer→detector→writer orchestrator)
- `src/dreamsync/cli.py` — `--capture`, `--capture-dir`, `--capture-naming` flags on `session`
- `src/dreamsync/session.py` — pipeline wired with Spotify callback bridge, flush on shutdown
- `src/dreamsync/live.py` — `capture_pipeline` param on `run_live_to_govee()`, feeds per-frame

Callback for Component 4 to hook into:
- `on_song_saved(path, metadata)` — fires after each song is written to disk

---

## Feature 1 Summary (for context)

Spotify integration is live. Key files:

- `src/dreamsync/spotify/auth.py` — OAuth PKCE flow, `TokenStore`, token refresh
- `src/dreamsync/spotify/client.py` — `SpotifyClient` wrapping `/me/player` and `/me/player/queue`
- `src/dreamsync/spotify/models.py` — `SpotifyTrack`, `PlaybackState`, `QueueSnapshot`
- `src/dreamsync/spotify/queue_watcher.py` — `SpotifyQueueWatcher` (daemon thread, dual-interval polling)
- `src/dreamsync/cli.py` — `spotify-auth` subcommand, `--spotify` / `--spotify-client-id` / `--spotify-poll-interval` on `session`
- `src/dreamsync/session.py` — watcher wired into `run_session()` with graceful v2 fallback

Callbacks available:
- `on_track_changed(new_track, old_track)` — fires within ~4s of a song change
- `on_queue_updated(queue_snapshot)` — fires within ~15s, or immediately after a track change
