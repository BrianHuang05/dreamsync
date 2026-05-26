# DreamSync — Next Steps

## Planned Workstream

### GUI and operator validation

The control-surface implementation phase is now in place across backend and GUI
for:

- structured profile editing of palettes, mood effects, params, EQ routes,
  instrument routes, and transitions
- non-destructive per-song / per-show `ShowControlPatch` authoring from the GUI
- temporary runtime overrides during local preview, saved-show playback, and
  reactive/live sessions
- richer telemetry and diagnostics for active palette, render mode, routes,
  layers, pan, and override state
- preview-time patch application through the local session / runtime supervisor

The next product phase on this thread should be:

- **Validation Phase 1: GUI and simulation-first testing**

Immediate follow-through targets:

- validate the full GUI editing flow without lights:
  profile edit -> preview -> show override -> runtime override -> clear/revert
- verify local preview and saved-show playback reflect GUI edits and patches
- confirm diagnostics make active routes, layers, palette, pan, and overrides
  understandable in practice
- capture operator friction points before any more implementation work

After that, move to:

- **Validation Phase 2: physical-device and live-pipeline testing**

This second validation phase should focus on:

- confirming simulation behavior translates cleanly to real fixtures
- checking live-session override ergonomics while audio is running
- tuning route visibility, color control, and fallback behavior using actual
  lights in the room

See `dev/plans/gui-show-control-surface-refinement-plan.md` for the completed
GUI implementation phase and the operator-facing behaviors now available.

### Backend show control follow-through

The backend control-surface implementation phase is now in place for:

- structured profile editing of mood effects, params, EQ routes, instrument routes, and transitions
- non-destructive per-show control patches over compiled cues
- generic `scene_layers` with backward compatibility for older `eq_layers`
- runtime hot overrides for playback and live sessions
- richer runtime telemetry snapshots for active musical/control state
- reactive mode alignment for `wave` and `gradient`

The next product phase on this thread should be:

- **Completed**

Immediate follow-through targets:

- use the GUI implementation and validation phases above to determine whether
  the current layered control surface is sufficient before considering optional
  true multi-effect compositing

See `dev/plans/backend-show-control-surface-plan.md` for the completed backend phase and the remaining optional compositing track.

### Instrument-aware spatial follow-through

The core implementation phase is now in place for:

- offline stereo pan analysis
- live stereo preservation
- live mixed-source instrument proxy tracking
- live instrument-aware route activation
- pan-aware runtime layer emission
- separation seam metadata for mixed-source enhancement hooks

The next product phase on this thread should be:

- **Phase 7: real-material validation and tuning**

Immediate follow-through targets:

- validate compiled instrument panning against real stereo songs in local preview
- validate live stereo panning and mixed-source proxy behavior on physical devices
- tune proxy thresholds and precedence rules against real songs with obvious
  bass, drum, vocal, and wide-harmonic content
- document failure modes where the system should intentionally fall back toward
  EQ-driven behavior instead of over-claiming instrument confidence

Assumption going forward:

- true isolated stems are out of scope
- all current and future instrument-aware routing must begin from mixed-source
  audio and derive usable proxies from there

See `dev/plans/live-instrument-pan-and-mixed-separation.md` for the detailed
implementation history and remaining validation targets, and
`dev/plans/continuous-spatial-runtime-followup.md` for the shared runtime
spatial layer this work plugs into.

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

### Test 40. GUI control surface validation (no lights)

**Prerequisites:**
- `dev/devices-dummy.yaml`
- a few already-analyzed stereo MP3s or compiled shows
- the desktop GUI launch path you normally use for local testing

```bash
# GUI smoke/regression coverage
python -m pytest dev/tests/test_gui_mode_switching.py \
  dev/tests/test_gui_palette_editor.py \
  dev/tests/test_gui_runtime_supervisor.py \
  dev/tests/test_gui_services.py \
  dev/tests/test_gui_show_patch_store.py -q
```

**What to check manually in the GUI:**
- open a profile and edit:
  - palette colors
  - mood effects
  - EQ routes
  - instrument routes
  - transitions
- save and reload the profile, confirming edits persist
- load a song or show, add a show override rule, and preview it in simulation
- apply a live runtime override:
  - render mode
  - color bias
  - intensity/speed trim
  - route mutes
- clear the live override and confirm the session falls back cleanly
- verify diagnostics show:
  - active palette
  - render mode
  - dominant band / proxy
  - active EQ routes
  - active instrument routes
  - active scene layers
  - pan center / width
  - runtime override state

---

### Test 41. GUI control surface validation (with lights)

**Prerequisites:**
- Govee devices on LAN with `dev/devices.yaml` configured
- a few representative compiled songs and stereo tracks
- a known-good live or saved-show playback path

```bash
# Keep automation coverage handy while doing the hardware pass
python -m pytest dev/tests/test_runtime_control.py \
  dev/tests/test_show_control_patch.py \
  dev/tests/test_local_session.py -q
```

**What to check manually with hardware:**
- profile edits visibly affect saved-show playback as expected
- show overrides affect the targeted cues without mutating the base profile
- runtime overrides apply during playback without restarting the session
- pan-aware placement still feels correct on real fixtures
- route mute controls do what the operator expects in the room
- simulation expectations match hardware closely enough to trust preview-first workflows

---

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

### Test 38. Live stereo preservation and pan telemetry

**Prerequisites:**
- VB-Cable or another known stereo capture path
- A stereo-heavy track with obvious left/right motion
- `dev/devices-dummy.yaml` for dry verification or a real device config

```bash
# Run live mode with debug telemetry enabled
python -m dreamsync govee-live --config dev/devices-dummy.yaml --debug-mood

# Focused automation coverage
python -m pytest dev/tests/test_live_bpm.py dev/tests/test_telemetry.py -q
```

**What to check:**
- live ingest no longer averages left/right channels before feature extraction
- telemetry shows `pan_center`, `pan_width`, and active stereo-driven bands/routes
- hard-left / hard-right moments produce sensible `spatial_origin.x` movement
- behavior falls back cleanly on mono sources

---

### Test 39. Live mixed-source instrument routing

**Prerequisites:**
- A song with obvious drum hits, bass entrances, and centered vocals
- Real devices preferred for this test

```bash
# Live instrument/proxy validation run
python -m dreamsync session --config dev/devices.yaml --debug-mood
```

**What to check:**
- live runtime emits `active_instrument_routes` from mixed-source analysis
- drums and bass react independently enough to drive distinct spatial layers
- vocals bias toward center/front without requiring isolated stems
- simultaneous proxies blend consistently instead of flickering ownership

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

- GUI show control surface refinement (2026-05-22)
  - expanded the profile GUI into a structured editor for palettes, effects,
    params, EQ routes, instrument routes, and transitions
  - added GUI-managed `ShowControlPatch` authoring and preview-time application
  - added runtime override controls for palette, color bias, render mode,
    intensity/speed trims, route mutes, and spatial overrides
  - expanded diagnostics with active routes, scene layers, pan, palette, render
    mode, and override state
  - wired local preview / saved-show playback through timeline resolvers so GUI
    patches apply without mutating source timelines
  - focused and broad GUI/control-surface regression coverage passed:
    `70 passed`, then `88 passed`

- Backend show control surface implementation (2026-05-22)
  - added structured backend profile-edit APIs for effects, params, transitions, EQ routes, and instrument routes
  - added `ShowControlPatch` for non-destructive per-show cue overrides
  - generalized runtime/compiler/live layering around `scene_layers` while preserving `eq_layers` compatibility
  - added a runtime control bus for playback/live sessions plus richer GUI telemetry snapshots
  - aligned reactive settings with `wave` and `gradient`
  - focused and broad regression coverage passed: `459` tests in the final sweep

- Instrument-aware VFX and offline pan foundations (2026-05-20)
  - profile/compiler support for `instrument_routes`
  - stereo-preserving offline decode plus frame-level pan metadata
  - serialized proxy pan summaries in `instrument_proxies`
  - pan-aware compiled spatial layers for instrument routes
  - optional stem-backend seam in `analyzer/separation.py`
  - targeted/broad regression coverage passed: `390` tests in the final sweep

- Instrument-aware routing cache coverage and follow-on planning refresh (2026-05-21)
  - cache fingerprints now track `instrument_routes` at profile and mood level
  - regression sweep passed: `401` tests in the final sweep
  - next-step planning now assumes mixed-source analysis, not true isolated stems

- Live instrument pan and mixed-source separation implementation (2026-05-21)
  - live ingest now preserves stereo frames instead of immediately collapsing to mono
  - live feature frames now carry `pan_center`, `pan_width`, left/right energy, and band pan centers
  - live mixed-source instrument proxy tracker now emits stable `drums` / `bass` / `vocals` / `harmonic` / `percussive` scores
  - live runtime now resolves `active_instrument_routes`, emits pan-aware route layers, and applies deterministic route precedence
  - separation seam metadata now supports mixed-source enhancement hints such as confidence, envelopes, and stereo pan/width hints
  - broad regression sweep passed: `429` tests

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
