# DreamSync — Next Steps

## What to Test Next

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

### 7. Auto-palette live test (no devices needed)

```bash
# Dry-run auto-palette: watch mood-aware profile switching + cross-fade
python -m dreamsync govee-live \
    --device 192.168.0.99:7:primary:ptreal \
    --duration 300 --auto-palette --debug-mood

# With explicit seed for reproducibility
python -m dreamsync govee-live \
    --device 192.168.0.99:7:primary:ptreal \
    --duration 300 --auto-palette --auto-palette-seed 42 --debug-mood

# Smart rotation with built-in profiles
python -m dreamsync govee-live \
    --device 192.168.0.99:7:primary:ptreal \
    --duration 300 \
    --smart-rotation --profile-rotation aurora,neon_city,ocean_deep,warm_sunset \
    --chain-blend 10 --debug-mood
```

**What to check:**
- `[chain]` messages appear in debug output
- Profile switches only during CHILL/GROOVE moods
- Cross-fade produces smooth transitions (blend messages visible)
- No errors in console output

### 8. Profile generation preview

```bash
python -m dreamsync profiles --generate 12 --seed 42 --verbose
python -m dreamsync profiles --generate 12 --seed 42 --chain-preview 10
```

**What to check:**
- 12 profiles listed with hue spread
- Chain preview shows distance values between transitions
- Deterministic output with same seed

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

## Dynamic Profile Chaining — Complete

Procedural profile generation, smart chaining, and palette cross-fade (80 new tests, 1595+ total passing):

- [x] **Color utilities** (`color_utils.py`): HSL conversions, RGB/HSL interpolation, harmony generators, palette distance — 24 tests
- [x] **Profile generator** (`profile_generator.py`): Procedural ProfileConfig from color theory params, energy-tiered palettes, deterministic with seed — 14 tests
- [x] **Smart chain controller** (`profile_chain.py`): Distance matrix, neighbor selection, palette cross-fade, mood-aware state machine — 30 tests
- [x] **CLI integration**: `--auto-palette`, `--smart-rotation`, `--chain-blend`, `--chain-interval`, `profiles --generate --chain-preview` — 12 tests

Plan: `dev/plans/dynamic-profile-chaining.md`

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
- 35 (Compiler genre variety — quality polish, not a gate)
- 36-41 (Cache — premature optimization; add later if re-analysis latency is a problem)
