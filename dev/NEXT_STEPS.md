# DreamSync — Next Steps

## Planned Workstream

### Instrument-aware spatial follow-through

The biggest functional gap is no longer offline analysis or compiled routing.
Those foundations are now in place. The next product work on this thread should
focus on finishing the live/runtime side and validating the behavior on real
music.

Immediate follow-through targets:

- preserve stereo in live capture instead of collapsing to mono at ingest
- let live mode emit instrument-aware pan layers, not just EQ-driven layers
- validate compiled instrument panning against real stereo songs in local preview
- decide whether to add a real optional stem backend (Demucs-style) behind the
  new analyzer separation seam

### Desktop GUI migration

The next major product step is moving from a CLI-first operator workflow to a
desktop GUI while keeping the CLI intact as a supported fallback.

Primary GUI targets:

- spatial placement / zone editing with a fixed-camera projected room map
- palette and profile editing with live color controls
- queue management for local playback, plus Spotify queue visibility and supported controls
- session control and diagnostics from one runtime surface

See `dev/plans/gui-desktop-migration.md` for the detailed implementation plan.

## What to Test Next

### Test 37. Compiled instrument/pan validation (offline stereo songs)

**Prerequisites:**
- A known stereo track with obvious left/right placement and clear bass/vocal motion
- `dev/devices-dummy.yaml` or a real device config

```bash
# Re-run analysis/compile on a stereo-heavy song
python -m dreamsync analyze path\\to\\song.mp3 --output out\\instrument-pan-analysis.json

# Compile and preview with the current profile stack
python -m dreamsync compile path\\to\\song.mp3 --config dev/devices-dummy.yaml

# Optional: run local preview / simulation path if you want to inspect layering
python -m pytest dev/tests/test_preview_simulation.py -q
```

**What to check:**
- phrase-level `instrument_proxies` now include `pan_center` / `pan_width`
- compiled micro-cues emit `active_instrument_routes` when vocals/drums/bass dominate
- pan-aware instrument layers produce sensible `spatial_origin.x` shifts
- EQ and instrument routes coexist, with instrument routes taking precedence when active
- cached `.analysis.json` sidecars invalidate cleanly because cache version is now `4`

---

### Test 35. Full live pipeline (end-to-end with physical audio routing + devices)

**Prerequisites:**
- VB-Cable installed and audio routing configured (see README)
- Computer aux out connected to AVR/speakers
- Govee devices on LAN, `dev/devices.yaml` configured
- Music source playing (Spotify, etc.)

```bash
# Full live session: capture + lights + Spotify boundaries
python -m dreamsync session --config dev/devices.yaml \
  --capture --capture-dir out/live-test \
  --capture-naming metadata --spotify \
  --debug-mood

# Or with streaming pipeline (capture + analyze + compile + play concurrently)
python -m dreamsync session --config dev/devices.yaml \
  --pipeline --capture --capture-dir out/live-test \
  --capture-naming metadata --spotify \
  --playback-device pick --debug-mood
```

**What to check:**
- Audio plays through AVR/speakers without glitches or feedback loops
- VB-Cable routing works: capture sees audio, speakers get echo via "Listen to this device"
- Lights respond to music in real time (mood transitions, beat sync)
- Spotify track boundaries trigger segment splits (MP3 count ≈ song count)
- MP3 files are clean (no static at boundaries, no silence padding)
- Sidecar metadata has correct artist/title/album
- Console output shows mood/effect/BPM transitions without errors
- Ctrl+C shuts down cleanly (final segment flushed, no orphan processes)

---

## Validation Tests

- [x] **34. Compile-and-play** (full pipeline, audio only) — passed with dummy devices
- [ ] **35. Full live pipeline** (end-to-end with physical audio routing + devices) — see above
- [x] **36. MP3 archiver** (zip MP3s, leave JSON sidecars) — 8/8 unit tests passed

See the **Validation tests** section in `README.md` for full test details and commands.

### Test 36. MP3 archiver

**Prerequisites:**
- A capture directory with MP3 + JSON sidecar files (run a capture session first, or use `out/capture-boundary/`)

```bash
# Unit tests (8 tests)
python -m pytest dev/tests/test_archiver.py -v

# Dry run — show what would be archived without modifying files
python -m dreamsync archive out/capture-boundary/ --dry-run

# Archive with custom name, keep originals
python -m dreamsync archive out/capture-boundary/ --name "test-archive" --keep

# Confirm zip was created and contains MP3s
unzip -l out/capture-boundary/test-archive.zip

# Archive and delete originals
python -m dreamsync archive out/capture-boundary/

# Confirm JSON files remain, MP3s are gone
ls out/capture-boundary/*.json
ls out/capture-boundary/*.mp3  # should show no results

# Session with auto-archive on shutdown
python -m dreamsync session --config dev/devices.yaml \
  --capture --capture-dir out/archive-test \
  --capture-naming metadata --spotify \
  --archive --debug-mood
# After Ctrl+C: check out/archive-test/ for archived_*.zip
```

**What to check:**
- `--dry-run` lists MP3 files and sizes without creating a zip
- `--keep` creates zip but leaves original MP3s in place
- Default archive deletes original MP3s after zipping
- JSON sidecars (`.analysis.json`, `.show.json`, `.meta.json`) are never touched
- Zip file contains all MP3s (verify with `unzip -l`)
- Empty directory returns "No MP3 files found" (no error)
- `session --archive` creates a zip on clean Ctrl+C shutdown

---

## Completed

- Instrument-aware VFX and offline pan foundations (2026-05-20)
  - profile/compiler support for `instrument_routes`
  - stereo-preserving offline decode plus frame-level pan metadata
  - serialized proxy pan summaries in `instrument_proxies`
  - pan-aware compiled spatial layers for instrument routes
  - optional stem-backend seam in `analyzer/separation.py`
  - targeted/broad regression coverage passed: `390` tests in the final sweep

- Tests 7-8: Auto-palette live test with tags + profile export round-trip — passed

- Interactive Audio Device Picker — all 4 steps implemented and tested (15 new tests, 0 regressions)
  - Step 1: `is_capture_device()` + `format_device_table()` helpers (`audio/system_input.py`)
  - Step 2: `pick_output_device()` interactive menu (`audio/system_input.py`)
  - Step 3: CLI wiring — `--playback-device pick` / `--audio-device pick` on session, play, compile-and-play, pipeline (`cli.py`)
  - Step 4: Improved `devices` subcommand — formatted table output + `--json` flag for backward compat (`cli.py`)

- Tagged Profile Chaining — all 8 deliverables implemented and tested (65 new tests, 0 regressions)
  - 1A: ROYGBIVW hue-to-color classification (`color_utils.py`)
  - 1B: Semantic profile tag generation (`profile_generator.py`)
  - 2A: Tag parsing and scoring helpers (`profile_chain.py`)
  - 2B: Tag-aware profile selection with mood sensitivity (`profile_chain.py`)
  - 3A: Enhanced profile preview with tags and scoring (`cli.py`)
  - 3B: Auto-seed display on live session (`cli.py`)
  - 4A: Enhanced chain logging with seed/index/tags (`live.py`, `profile_chain.py`)
  - 4B: Profile export to YAML (`profile_generator.py`, `cli.py`)

- Test 34: Compile-and-play — passed (2026-03-13)
  - Full pipeline: analyze → compile → play with `dev/devices-dummy.yaml` (NullMultiAdapter fallback)
  - 20 cues fired, 34,062 frames sent, audio played full 194s duration
  - `build_multi_adapter` now falls back to `NullMultiAdapter` when all devices unreachable (no crash)
  - `--dry-run` added to `compile-and-play` for explicit audio-only mode

- Test 36: MP3 archiver — passed (2026-03-13)
  - 8/8 unit tests passed

- Compile summary fix (2026-03-13)
  - `format_summary()` now uses timestamp-based section lookup instead of index-based
  - Fixes misaligned labels when cue count ≠ section count

- All automated tests: 1,684 passed, 0 failures (2026-03-13)

## Out of Scope (deferred)

- 24 (Genre variety — analyzer tuning, not correctness)
- 35 (Compiler genre variety — quality polish, not a gate)
- 36-41 (Cache — premature optimization; add later if re-analysis latency is a problem)
