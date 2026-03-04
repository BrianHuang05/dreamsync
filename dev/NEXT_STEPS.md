# DreamSync — Next Steps

## Quick Reference

- **Project**: Local audio-reactive Govee LED controller (LAN UDP / BLE). v2 complete, building v3.
- **v3 vision**: Pre-sequenced show engine. See `dev/plans/v3-show-sequencer.md`.
- **Platform**: Windows 11, bash/Unix shell syntax, Python 3.11+
- **Install**: `pip install -e ".[session]"`
- **Tests**: `pytest dev/tests/` (812 tests)
- **Lint**: `ruff check src/`
- **Branch**: `govee-lan-direct` (primary)

---

## v3 Feature Checklist

| # | Feature | Status | Plan |
|---|---------|--------|------|
| 1 | Spotify Queue Watcher | **DONE** (`7c3372b`) | `dev/plans/feature-1-spotify-queue-watcher.md` |
| 2 | Song Structure Analyzer | **DONE** (C3+C4+C5 code complete — 213 tests) | `dev/plans/feature-2-song-structure-analyzer.md` |
| 3 | Show Compiler | Not started | — |
| 4 | Show Cache | Not started | — |
| 5 | Playback Runtime | Not started | — |
| 6 | v2 Fallback Switch | Not started | — |

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

## Next: Feature 3 — Show Compiler

**Status**: Not started — needs plan.

The Show Compiler takes a `SongStructure` (from C4 Analyzer) and produces a `ShowTimeline` (consumed by C5 Show Player). This is the creative engine that maps song sections, energy profiles, and moods to lighting cues (render mode, color palette, intensity, speed, transitions).

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

## Previous: Feature 2, Component 4 — Song Structure Analyzer ✅

Plan: `dev/plans/feature-2-component-4-analyzer.md`

**Status**: Code complete (80 unit tests). Ready for manual validation with real mp3 files.

### What it does

Takes an mp3 file (from Component 3 capture or direct input) and produces a `SongStructure` — global BPM, beat grid, section boundaries with labels (intro, verse, chorus, bridge, drop, outro), and per-section energy/mood profiles. This feeds the Show Compiler (Feature 3).

### Key decisions made

- **All-local analysis** — no Spotify Audio Analysis API dependency. Analysis uses the existing `LiveBpmEstimator`, `Director`, `MoodClassifier` etc. run offline over the complete file.
- **Section detection** — spectral self-similarity matrix + novelty curve (standard MIR technique). No ML models.
- **No new pip deps** — numpy + ffmpeg only.
- **No live.py refactoring needed** — all helper functions (`_prepare_bass_window`, `_spectral_features`, `_compute_whitened_flux`, `_feature_row_from_frame`) and classes were already importable.

### Files created

```
src/dreamsync/analyzer/
├── __init__.py        # Package init
├── decode.py          # Mp3Decoder (D4.1)
├── features.py        # OfflineFeaturePipeline (D4.2)
├── bpm.py             # GlobalBpmEstimator (D4.3)
├── sections.py        # SectionSegmenter (D4.4)
├── models.py          # SongStructure, Section, BeatGrid (D4.5)
└── analyze.py         # analyze_song() orchestrator (D4.5)
```

### CLI subcommands added

- `dreamsync analyze song.mp3` — full analysis, JSON to stdout
- `dreamsync analyze song.mp3 --output structure.json` — write JSON to file
- `dreamsync analyze song.mp3 --summary` — human-readable summary
- `dreamsync analyze-dir captured_songs/ --output-dir analysis/` — batch mode

### Tests (80 total)

```
dev/tests/test_analyzer_decode.py      — 13 tests
dev/tests/test_analyzer_features.py    — 15 tests
dev/tests/test_analyzer_bpm.py         — 18 tests
dev/tests/test_analyzer_sections.py    — 20 tests
dev/tests/test_analyzer_models.py      — 14 tests
```

### Next: manual validation

See `dev/VALIDATION_TESTS.md` Phase 6 for manual validation steps with real mp3 files.

Features 3–6 follow in order. See `dev/plans/v3-show-sequencer.md` for the full vision.

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
