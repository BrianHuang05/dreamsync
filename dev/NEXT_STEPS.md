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

### 7. Auto-palette live test with tags (no devices needed)

```bash
# Dry-run auto-palette: watch tag-aware profile switching + cross-fade
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
- `[chain]` messages show ROYGBIVW tags, seed, and pool index
- Auto-generated seed is printed when `--auto-palette-seed` is omitted
- Profile switches only during CHILL/GROOVE moods
- Cross-fade produces smooth transitions (blend messages visible)
- No errors in console output

### 8. Profile export round-trip (no devices needed)

```bash
# Preview generated profiles with tags
python -m dreamsync profiles --generate 12 --seed 42

# Preview chain sequence with tag-based scoring
python -m dreamsync profiles --generate 12 --seed 42 --chain-preview 10

# Export a profile to YAML
python -m dreamsync profile-export --seed 42 --index 3 --output my_palette.yaml

# Validate the exported profile
python -m dreamsync profile-validate my_palette.yaml

# Use the exported profile for a live session
python -m dreamsync govee-live \
    --device 192.168.0.99:7:primary:ptreal \
    --duration 60 --profile my_palette.yaml --debug-mood
```

**What to check:**
- Profile preview shows ROYGBIVW tags for each profile
- Chain preview shows tag-based scoring with reasons
- Exported YAML loads and validates without errors
- Exported profile works with `--profile` flag


---

## Validation Tests

- [ ] **35. Compile-and-play** (full pipeline with devices) — see command in section 3 above

See the **Validation tests** section in `README.md` for full test details and commands.


## Completed

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
