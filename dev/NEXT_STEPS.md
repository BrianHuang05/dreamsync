# DreamSync — Next Steps

## What to Test Next

### Test 34. Compile-and-play with devices (the last unchecked validation test)

```bash
# Single song, full pipeline: analyze -> compile -> play with lights
python -m dreamsync compile-and-play out/capture-boundary/2026-03-06_00-15-02_Vacation\ Manor_-_If\ Only\ for\ Tonight\ -\ Midnight\ Version.mp3 \
  --config dev/devices.yaml --debug

# Or use any MP3:
python -m dreamsync play out/capture-boundary/2026-03-06_00-19-45_Pat\ Metheny\ Group_-_Last\ Train\ Home.mp3 \
  --config dev/devices.yaml --debug
```

**What to check:**
- Audio plays through speakers without glitches
- Lights follow cue transitions (visible mode/color changes)
- Bulbs get simplified effects (pulse/breathe instead of scroll/wave) — Issue 4
- Strips at reduced brightness vs bulbs — Issue 5
- Song ending fades to black gracefully — Issue 6
- No errors in console output

---

## Validation Tests

- [ ] **34. Compile-and-play** (full pipeline with devices) — see command above
- [ ] **35. Full live pipeline** (end-to-end with physical audio routing) — see below
- [ ] **36. MP3 archiver** (zip MP3s, leave JSON sidecars) — see below

See the **Validation tests** section in `README.md` for full test details and commands.

### Test 35. Full live pipeline (end-to-end with physical audio routing)

**Prerequisites:**
- VB-Cable installed and audio routing configured (see README)
- Computer aux out connected to AVR/speakers
- Govee devices on LAN, `devices.yaml` configured
- Music source playing (Spotify, etc.)

```bash
# Full live session: capture + lights + Spotify boundaries
python -m dreamsync session --config devices.yaml \
  --capture --capture-dir out/live-test \
  --capture-naming metadata --spotify \
  --debug-mood

# Or with streaming pipeline (capture + analyze + compile + play concurrently)
python -m dreamsync session --config devices.yaml \
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
python -m dreamsync session --config devices.yaml \
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

## Out of Scope (deferred)

- 24 (Genre variety — analyzer tuning, not correctness)
- 35 (Compiler genre variety — quality polish, not a gate)
- 36-41 (Cache — premature optimization; add later if re-analysis latency is a problem)
