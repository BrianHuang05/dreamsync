# DreamSync — Next Steps

## What to Test Next

### 1. Re-analyze existing songs (Issues 1-2 changed analyzer output)

The hybrid beat grid, octave detection, and phrase segmentation produce different `.analysis.json` output. Re-analyze existing MP3s and re-compile to pick up the improvements:

```bash
# Re-analyze all captured songs (overwrites existing .analysis.json)
python -m dreamsync analyze-dir out/capture-boundary/ --output-dir out/analysis/

# Re-compile all analysis files (overwrites existing .show.json)
python -m dreamsync compile-dir out/analysis/ --output-dir out/shows/ --summary
```

**What to check:**
- BPM values should be same or better (no octave-doubled values)
- Section count should be same or slightly higher (phrases add sub-sections)
- Compile summary should show micro-cues (more cues per section than before)

### 2. Dry-run playback of a re-compiled show (no devices needed)

```bash
# Pick any MP3 with a matching show file
python -m dreamsync play out/capture-boundary/2026-03-06_00-15-02_Vacation\ Manor_-_If\ Only\ for\ Tonight\ -\ Midnight\ Version.mp3 --dry-run --debug
```

**What to check:**
- Cue transitions in debug output — should see more frequent cue changes (micro-cues from phrases)
- Outro should show intensity ramping down (intensity_start)
- Final cue should be fade-to-black (intensity=0.00)

### 3. Compile-and-play with devices (Test 34 — the last unchecked validation test)

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

### 4. Directory playback (multiple songs sequentially)

```bash
python -m dreamsync play out/capture-boundary/ --config dev/devices.yaml --debug
```

**What to check:**
- Each song analyzed/compiled/played in sequence
- Transitions between songs are clean
- Different songs get different palettes (Issue 3 song-level palette selection)

### 5. Streaming pipeline (live capture + concurrent playback)

```bash
# Full streaming: Spotify track changes trigger capture -> analyze -> compile -> play
python -m dreamsync session --config dev/devices.yaml \
  --pipeline --capture --capture-dir out/streaming \
  --capture-naming metadata --spotify \
  --playback-device 5 --purge --debug-mood
```

**What to check:**
- Songs captured and played back with compiled shows
- Concurrent pipeline: while song N plays, song N+1 compiles
- Device brightness_scale applied (strips dimmer than bulbs)
- Color cycling happens on downbeats, not every beat (Issue 3)

### 6. Device config with brightness_scale (manual verification)

Add `brightness_scale` to `dev/devices.yaml` to test per-device calibration:

```yaml
  - name: "Couch strip (H612F)"
    address: "10.126.166.180"
    type: lan
    segments: 7
    transport: ptreal
    role: primary
    brightness_scale: 0.4    # dim the close strip

  - name: "Overhead strip (H808A)"
    address: "10.126.166.156"
    type: lan
    segments: 25
    transport: razer
    role: primary
    brightness_scale: 0.6    # slightly dimmer
```

Then run any play command and verify strips are noticeably dimmer than bulbs.

---

## Validation Tests

- [x] **20. Analyzer unit tests** (101 tests) — `pytest dev/tests/test_analyzer_*.py -v`
- [x] **23. Batch analysis** (5+ songs in directory)
- [x] **25. Show Player unit tests** (81 tests)
- [x] **26. Audio playback test** (no devices) — `play --dry-run` with NullMultiAdapter
- [x] **27. Synchronized playback** (with real Govee devices)
- [x] **28. Show file round-trip** (optional)
- [x] **29. Compiler unit tests** (79 tests)
- [x] **30. Single file compile**
- [x] **31. Compile to JSON output**
- [ ] **34. Compile-and-play** (full pipeline with devices) — see command in section 3 above

See the **Validation tests** section in `README.md` for full test details and commands.

## Show Quality Improvements (Issues 1-6) — Complete

All six show quality issues implemented and tested (69 new tests, 1508 total passing):

- [x] **Issue 1 — Beat Alignment**: Two-pass phase search, hybrid beat grid (snap to onsets), harmonic alias octave detection
- [x] **Issue 2 — Sub-Section Granularity**: Phrase segmenter (4-bar phrases), instrument event detector (kick/bass), micro-cue insertion
- [x] **Issue 3 — Palette Coherence**: Song-level primary/accent palette, downbeat-only color cycling, smooth hex interpolation
- [x] **Issue 4 — Bulb vs Strip Behavior**: Device-type render mode mapping, faster pulse decay for single-color devices
- [x] **Issue 5 — Brightness Calibration**: Per-device `brightness_scale` in config, auto-role/brightness defaults by device type
- [x] **Issue 6 — Show End Fadeout**: Fade-to-black after last energetic beat, outro intensity ramp (`intensity_start`)

Plans: `dev/plans/issue1-beat-alignment.md` through `dev/plans/issue6-show-end-fadeout.md`

## Streaming Pipeline (session --pipeline)

Capture + analyze + compile + play concurrently. Implemented in:
- `NullMultiAdapter` (`src/dreamsync/output/null_adapter.py`) — 6 tests
- `ShowPipelineWorker` (`src/dreamsync/show_pipeline_worker.py`) — 12 tests
- `ShowPlaybackConsumer` (`src/dreamsync/show_playback_consumer.py`) — 10 tests
- CLI: `play --dry-run`, `session --pipeline --playback-device N --purge`

## Out of Scope (deferred)

- 24 (Genre variety — analyzer tuning, not correctness)
- 32 (Seed determinism — no reproducibility requirement)
- 33 (Profile override — not actively using multiple profiles)
- 35 (Compiler genre variety — quality polish, not a gate)
- 36-41 (Cache — premature optimization; add later if re-analysis latency is a problem)
