# Desktop GUI Migration — Detailed Implementation Plan

## Context

DreamSync has grown beyond the point where a CLI-only surface is the best control
plane. The project now has several subsystems that are individually useful, but
awkward to operate together from flags and YAML edits alone:

- multi-device config and detection
- profile/palette generation and profile hot-reload
- coordinate-based spatial mapping
- local playlist queue control
- Spotify playback/queue polling
- streaming capture → analyze → compile → play sessions

The next major step is to add a **desktop GUI** that sits on top of the existing
engine and orchestration code. The GUI is meant to improve three practical workflows
first:

1. **Spatial management**
   - edit device placement visually instead of only through YAML
   - show an extremely simple “3-D” room map using fixed-camera projection
   - make zone/placement debugging faster while the spatial runtime is still maturing

2. **Palette management**
   - inspect generated palettes visually
   - assign colors to devices / areas more directly
   - tweak hex colors with sliders and live previews instead of round-tripping by hand

3. **Queue management**
   - view current + upcoming tracks in one place
   - reorder / remove / play-now / skip where the underlying runtime supports it
   - expose existing local-playlist controls without requiring signals or shell commands

There is also a clear stretch goal:

4. **Show preview**
   - simulate device output in real time on the room map
   - preview compiled shows before sending frames to real hardware

This plan assumes the existing CLI remains supported. The GUI becomes the preferred
operator surface, not a rewrite that discards the current command-line workflows.

## Decision

Build a **Windows-first native desktop GUI in PySide6**, while keeping the current
CLI and runtime modules as the source of truth.

### Why PySide6

- DreamSync is already a local Python desktop application, so a browser/Electron
  stack would add unnecessary process and packaging complexity.
- The planned UX needs:
  - drag-reorder lists
  - custom color widgets
  - timers and background worker threads
  - a 2-D canvas for room mapping and show preview
- Qt provides those primitives out of the box and is a better long-term fit than
  trying to stretch `tkinter` into a richer control surface.
- The project is Windows-first, and Qt is mature there.

### Architectural rule

The GUI should **call reusable services**, not shell out to CLI commands and not
re-implement core logic in widget code.

The desired layering is:

```text
Qt widgets / models
    -> GUI state + controller layer
    -> application services
    -> existing DreamSync runtime modules
    -> adapters / playback / Spotify / capture / compiler
```

That keeps the CLI and GUI aligned and makes testing possible without booting the
full window for every behavior.

## Non-Goals for the First GUI Release

- No attempt to replace every CLI subcommand on day one
- No arbitrary 3-D camera controls, orbiting, or perspective editor
- No full DAW-style timeline editor for compiled shows
- No device discovery over a browser or remote multi-user control
- No hard dependency on real hardware for GUI development
- No full Spotify queue editor unless the backing API supports the action cleanly
- No EXE packaging / installer work in the first implementation pass

## Important Scope Guardrails

### 1. Keep the CLI intact

The GUI should launch alongside the existing CLI (`dreamsync gui`), not replace it.
That gives us:

- a stable fallback during migration
- easier automated testing
- a clear headless path for debugging capture/pipeline issues

### 2. Reuse existing queue controls where they already exist

The local playback path already has most of the queue behavior the GUI wants:

- `LocalPlaylistSession.signal_next()`
- `LocalPlaylistSession.remove_track()`
- `LocalPlaylistSession.move_track()`
- `LocalPlaylistSession.play_now()`
- `LocalPlaylistSession.queue_snapshot()`

The GUI should wrap those controls instead of inventing a second queue runtime.

### 3. Treat Spotify queue editing as a constrained surface

The current Spotify client in-repo supports:

- `get_playback_state()`
- `get_queue()`
- `add_to_queue()`
- `skip_to_next()`
- `set_shuffle()`

That means the first GUI pass can safely provide:

- queue display
- add-to-queue
- skip
- shuffle toggle

But **full delete/reorder against Spotify’s remote queue should not be promised**
until the backing API and client surface support it. Full reorder/delete belongs
first on the local playlist / local show queue, where DreamSync already controls
the ordering.

### 4. Keep the spatial runtime ahead of the visual editor

The GUI should not invent spatial semantics that the runtime cannot use.

Current direction in `dev/plans/space-zone-mapping.md` is:

- normalized device placement
- a 3x3 spatial scene
- coordinate-aware routing at runtime

The GUI should build on that foundation. For the GUI MVP:

- `x` and `y` remain the authoritative runtime placement inputs
- optional `z` is added primarily for editor/view projection and future preview work
- first release preview can ignore advanced interpolation and still be useful

## Phase 0 — Checkpoint Commit and Documentation Sync

### Goal

Create a clean pre-GUI checkpoint so the GUI work does not get mixed together with
the current spatial / queue / pipeline changes.

### What to update before the checkpoint

- `dev/plans/gui-desktop-migration.md` — this plan
- `dev/NEXT_STEPS.md` — point the roadmap at the GUI workstream
- optionally `README.md` only if we decide to mention the upcoming GUI entry point

### Commit intent

This checkpoint commit should explicitly acknowledge:

- spatial mapping is in progress
- queue control is in progress / partially validated
- GUI work begins after that checkpoint, not mixed into the same change set

### Completion Criteria

- [ ] GUI migration plan added to `dev/plans/`
- [ ] `dev/NEXT_STEPS.md` references the new GUI workstream
- [ ] checkpoint commit created before GUI code starts

---

## Deliverable 1 — GUI Foundation and Application Shell

### Goal

Introduce the GUI runtime without entangling it with widget code too early.

### Files to Create

| File | Purpose |
|------|---------|
| `src/dreamsync/gui/__init__.py` | GUI package marker |
| `src/dreamsync/gui/app.py` | Qt application bootstrap |
| `src/dreamsync/gui/main_window.py` | top-level main window / layout shell |
| `src/dreamsync/gui/state.py` | app-wide immutable/mutable state models |
| `src/dreamsync/gui/events.py` | signals / event dataclasses between workers and UI |
| `src/dreamsync/gui/workers.py` | background worker wrappers for long-running tasks |
| `src/dreamsync/gui/settings.py` | persisted GUI preferences (last config, window layout, etc.) |

### Files to Modify

| File | Change |
|------|--------|
| `pyproject.toml` | add optional `gui` dependency group (`PySide6`) |
| `src/dreamsync/cli.py` | add `gui` subcommand to launch the desktop app |
| `README.md` | document GUI install + launch once the shell works |

### Design

#### App shell layout

Use a conservative desktop layout:

- left nav or tab bar:
  - Devices / Spatial
  - Palettes
  - Queue
  - Session / Playback
  - Logs / Diagnostics
- central stacked content area
- bottom status bar:
  - current profile
  - session state
  - active output mode
  - device count / health

#### Threading rule

Anything that can block must stay off the UI thread:

- device probing
- analyze / compile
- playlist precompile
- Spotify polling handoffs
- capture/session startup and shutdown

Qt signals should be used to ship results back to the UI safely.

#### Persistence

Persist only GUI preferences in Deliverable 1:

- last opened config path
- last selected profile path
- window geometry
- splitter sizes
- last used tab

Do **not** create a second authoritative device/project config format yet.

### Testing

- Unit tests for GUI settings serialization
- Unit tests for app state transitions
- Smoke test: `dreamsync gui` launches and closes cleanly
- Smoke test: launching GUI does not require real Govee devices

### Completion Criteria

- [ ] `dreamsync gui` launches a window
- [ ] app shell renders without hardware
- [ ] long-running tasks use worker threads, not the UI thread
- [ ] GUI preferences persist across restarts

---

## Deliverable 2 — Service Layer Extraction

### Goal

Expose the existing CLI/runtime functionality as reusable application services that
the GUI can call directly.

### Why this step matters

Right now a lot of DreamSync’s orchestration lives in command handlers and session
entry points. The GUI needs stable programmatic APIs for:

- loading configs
- probing devices
- starting/stopping sessions
- loading profiles
- building queue snapshots
- compiling and previewing tracks

### Files to Create

| File | Purpose |
|------|---------|
| `src/dreamsync/gui/services/device_service.py` | config load, probe, adapter build |
| `src/dreamsync/gui/services/profile_service.py` | load/generate/save profiles and palettes |
| `src/dreamsync/gui/services/queue_service.py` | local queue + Spotify queue orchestration |
| `src/dreamsync/gui/services/session_service.py` | start/stop live, local, and pipeline sessions |
| `src/dreamsync/gui/services/show_service.py` | analyze/compile/load timeline summary helpers |

### Files to Modify

| File | Change |
|------|--------|
| `src/dreamsync/session.py` | factor reusable session startup pieces out of CLI-only flow |
| `src/dreamsync/local_session.py` | expose queue/session control hooks cleanly |
| `src/dreamsync/output/auto_detect.py` | expose config validation errors in GUI-friendly form |
| `src/dreamsync/profile.py` | expose palette/profile validation results cleanly |

### Design

Each service should:

- return structured data, not print to stdout
- raise domain errors the GUI can display usefully
- avoid direct Qt imports
- remain reusable by CLI commands

Example service split:

```text
DeviceService
    load_config(path)
    validate_config(path)
    probe_devices(configs)
    build_adapter(...)

QueueService
    local_queue_snapshot(...)
    spotify_queue_snapshot(...)
    move_local_track(...)
    remove_local_track(...)
    skip_current(...)
```

### Testing

- Unit tests for service return shapes and error paths
- Regression tests proving CLI still works after refactor
- No Qt dependency required for service-layer tests

### Completion Criteria

- [ ] GUI no longer depends on parsing CLI stdout
- [ ] service layer covers config, profile, queue, and session operations
- [ ] CLI remains functional after refactor

---

## Deliverable 3 — Spatial Editor with Fixed-Camera “3-D” Projection

### Goal

Add a visual device placement editor that makes spatial mapping understandable and
editable without hand-authoring coordinates.

### Proposed coordinate model

For GUI/editor purposes, move to:

```python
DevicePlacement:
    x: float   # left/right
    y: float   # height
    z: float   # front/back depth
```

But preserve MVP runtime compatibility as follows:

- runtime spatial mapping continues to use the current normalized room-plane logic
- GUI projection uses all three axes
- if runtime logic is not yet `z`-aware, `z` can initially be editor/preview-only

### Projection rule

Use a fixed oblique/isometric-style projection, not a movable camera:

```text
screen_x = origin_x + (x * scale_x) + (z * depth_x)
screen_y = origin_y - (y * scale_y) + (z * depth_y)
```

This is intentionally simple. The goal is readability, not 3-D realism.

### Files to Create

| File | Purpose |
|------|---------|
| `src/dreamsync/gui/widgets/spatial_canvas.py` | custom room-map canvas |
| `src/dreamsync/gui/models/spatial_scene.py` | editor-side placement/view models |
| `src/dreamsync/gui/controllers/spatial_controller.py` | drag/update/apply placement changes |

### Files to Modify

| File | Change |
|------|--------|
| `src/dreamsync/spatial/models.py` | extend placement model for optional `z` / editor metadata |
| `src/dreamsync/output/auto_detect.py` | parse/save extended placement values |
| `devices-template.yaml` | document editor-friendly placement fields |
| `README.md` | add spatial editor configuration notes |

### Spatial editor features

- draw room axes and basic depth guides
- render each device as a labeled node
- color nodes by device/profile assignment
- drag device positions on the projected map
- numeric inputs for exact x/y/z edits
- snap-to-grid toggle
- highlight device selection and config validation errors

### Important constraint

The editor should save placements back into the existing YAML config, not into a
GUI-only shadow file.

### Testing

- Unit tests for projection math
- Unit tests for placement serialization/parsing
- Integration test: edit placement in GUI model → YAML round-trip preserves values
- Manual test with `dev/devices-dummy.yaml`

### Completion Criteria

- [ ] devices can be placed visually
- [ ] placement edits persist back to YAML
- [ ] projection is deterministic and stable
- [ ] spatial editor works without real hardware

---

## Deliverable 4 — Palette Manager and Color Editing Surface

### Goal

Make palette/profile work visual and interactive, while reusing the current profile
and generator infrastructure.

### Features

- show palette swatches for built-in and generated profiles
- preview hex colors instantly
- edit colors with:
  - hex input
  - RGB sliders
  - brightness/value slider
  - hue/saturation square or gradient strip
- assign colors or palettes to:
  - devices
  - spatial regions
  - mood buckets

### Files to Create

| File | Purpose |
|------|---------|
| `src/dreamsync/gui/widgets/color_picker.py` | custom picker with hex/RGB/brightness controls |
| `src/dreamsync/gui/widgets/palette_strip.py` | swatch strip + drag/select behavior |
| `src/dreamsync/gui/controllers/palette_controller.py` | palette edit/apply/save actions |
| `src/dreamsync/gui/models/palette_state.py` | palette editing state |

### Files to Modify

| File | Change |
|------|--------|
| `src/dreamsync/profile.py` | expose friendlier save/update helpers |
| `src/dreamsync/profile_generator.py` | support GUI-friendly preview metadata if needed |
| `src/dreamsync/color_utils.py` | add small conversion helpers for picker widgets |

### Design

The palette manager should treat existing `ProfileConfig` as the authoritative save
format. GUI-only state is temporary until the user applies/saves.

Recommended editing flow:

1. load profile
2. select palette or mood binding
3. tweak a color visually
4. see live swatch + hex/RGB values update
5. apply to profile in-memory
6. save back to YAML

### Nice-to-have, but still in scope if cheap

- copy hex from swatch
- duplicate palette
- generate palette variations from a selected seed color
- mark unsaved changes in the UI

### Testing

- Unit tests for hex/RGB/HSV conversion helpers
- Unit tests for palette mutation/save logic
- Round-trip tests: load profile → edit → save → reload → values preserved

### Completion Criteria

- [ ] profile palettes can be edited visually
- [ ] color previews update immediately
- [ ] saved YAML remains valid under current profile validator
- [ ] existing profile workflows still work from CLI

---

## Deliverable 5 — Queue Manager

### Goal

Expose both local playlist control and Spotify queue visibility in one GUI panel,
without overstating what remote Spotify control can do.

## Queue surface split

### A. Local queue (full control target)

Backed by `PlaylistManager` + `LocalPlaylistSession`.

Desired actions:

- drag to reorder upcoming tracks
- remove upcoming tracks
- play selected track now
- skip current
- jump to previous track
- shuffle upcoming
- queue new local files or directories

### B. Spotify queue (observability + limited control target)

Backed by `SpotifyClient` + `SpotifyQueueWatcher`.

Safe first-pass actions:

- show current track + upcoming queue
- add a Spotify URI to queue
- skip current
- toggle shuffle
- refresh queue snapshot

Explicitly defer:

- remote queue reorder
- remote queue delete

unless a later API-backed solution exists.

### Files to Create

| File | Purpose |
|------|---------|
| `src/dreamsync/gui/widgets/queue_panel.py` | queue UI with local/Spotify sections |
| `src/dreamsync/gui/models/queue_state.py` | queue snapshot view models |
| `src/dreamsync/gui/controllers/queue_controller.py` | local queue and Spotify actions |

### Files to Modify

| File | Change |
|------|--------|
| `src/dreamsync/local_session.py` | ensure control actions are stable for GUI callers |
| `src/dreamsync/playlist.py` | add any missing helper methods for insertion/appending |
| `src/dreamsync/spotify/client.py` | optional small API helpers for GUI actions |
| `src/dreamsync/spotify/queue_watcher.py` | surface state updates cleanly to the GUI service layer |

### Likely code addition

`PlaylistManager` will probably need an explicit append/insert API so “queue new”
does not require rebuilding the whole playlist from scratch.

### Testing

- Unit tests for local queue mutations:
  - move
  - remove
  - play now
  - append
- Integration tests around `LocalPlaylistSession` control signals
- Mocked Spotify client tests for queue refresh/add/skip/shuffle

### Completion Criteria

- [ ] local queue can be reordered by click/drag
- [ ] local queue supports delete / play now / skip
- [ ] Spotify queue is visible and refreshable
- [ ] supported Spotify actions are wired without blocking the UI

---

## Deliverable 6 — Session Control, Telemetry, and Diagnostics

### Goal

Turn the GUI into a practical runtime control center, not just a config editor.

### Features

- start/stop:
  - live session
  - local playback session
  - streaming pipeline session
- show current runtime state:
  - playing track
  - current mood/effect/palette
  - current profile
  - frames sent
  - queue depth / cache stats where applicable
- show device state:
  - reachable / unreachable
  - role
  - transport
  - placement summary
- surface logs/errors in a visible panel

### Files to Create

| File | Purpose |
|------|---------|
| `src/dreamsync/gui/widgets/session_panel.py` | runtime controls and live status |
| `src/dreamsync/gui/widgets/log_panel.py` | log stream viewer |
| `src/dreamsync/gui/models/session_state.py` | state for session lifecycle + telemetry |

### Files to Modify

| File | Change |
|------|--------|
| `src/dreamsync/live.py` | emit structured events the GUI can subscribe to |
| `src/dreamsync/session.py` | return lifecycle callbacks/handles instead of only blocking control flow |
| `src/dreamsync/show_playback_consumer.py` | expose consumer stats/event hooks |
| `src/dreamsync/show_pipeline_worker.py` | expose worker stats/event hooks |

### Design

The GUI should manage sessions via controller/service handles, for example:

```text
start session
    -> get session handle
    -> subscribe to events
    -> update UI state on signal
stop session
    -> set stop event
    -> await worker shutdown
```

Avoid any design that forces the GUI to scrape terminal text.

### Testing

- Unit tests for session state transitions
- Integration tests with dummy devices / null adapter
- Manual smoke test:
  - start local playback
  - skip track
  - stop session
  - verify GUI remains responsive

### Completion Criteria

- [ ] GUI can start and stop major session types
- [ ] status/telemetry updates render live
- [ ] errors are surfaced without crashing the app

---

## Deliverable 7 — Stretch Goal: Simulated Show Preview

### Goal

Preview compiled shows on the room map before driving real devices.

### Scope for stretch-goal MVP

- playback a `ShowTimeline` against a simulated room/device scene
- each addressable device rendered as:
  - single node (bulbs)
  - segmented strip (strips)
- interpolate or step colors according to current cue/runtime
- optional simple additive blending when projected devices overlap visually

### Out of scope even for the stretch goal

- photoreal lighting
- physically accurate light falloff
- volumetric bloom
- arbitrary camera control

### Files to Create

| File | Purpose |
|------|---------|
| `src/dreamsync/gui/preview/show_preview.py` | simulated playback controller |
| `src/dreamsync/gui/widgets/preview_canvas.py` | device rendering surface |
| `src/dreamsync/gui/models/preview_state.py` | preview timeline + frame state |

### Files to Modify

| File | Change |
|------|--------|
| `src/dreamsync/show/runtime.py` | expose reusable cue/frame calculation hooks |
| `src/dreamsync/render.py` | optional pure frame-generation helpers for simulation |
| `src/dreamsync/spatial/mapper.py` | optional preview-oriented scene sampling helpers |

### Design

The best version of this feature reuses the same frame/timeline logic used for real
playback, but swaps the hardware adapter for a preview renderer.

That means:

```text
ShowTimeline
    -> ShowPlaybackRuntime or shared frame evaluator
    -> simulated device frame buffer
    -> preview canvas
```

If this cannot be shared cleanly in the first pass, defer rather than forking the
runtime logic into a preview-only implementation.

### Testing

- Unit tests for preview frame sampling
- Snapshot-style tests for deterministic device-frame output
- Manual test with dummy devices and a known short show

### Completion Criteria

- [ ] a compiled show can be previewed without real hardware
- [ ] preview follows current spatial placements
- [ ] preview reuses runtime logic instead of duplicating effect behavior

---

## Recommended Implementation Order

1. Phase 0 checkpoint commit and doc sync
2. Deliverable 1 GUI shell
3. Deliverable 2 service extraction
4. Deliverable 5 queue manager
5. Deliverable 4 palette manager
6. Deliverable 3 spatial editor
7. Deliverable 6 session control
8. Deliverable 7 preview stretch goal

### Why this order

- Queue control has the strongest existing backend support and gives immediate UX value.
- Palette editing is mostly presentation over already-existing data models.
- Spatial editing is valuable, but it should land after the service/app shell exists.
- Session control depends on the shell and services being stable.
- Show preview is intentionally last because it is the most likely scope-creep vector.

## Risks and Mitigations

| Risk | Why it matters | Mitigation |
|------|----------------|------------|
| GUI code grows its own business logic | CLI and GUI drift apart | enforce service-layer boundary |
| UI thread blocking | app feels unreliable immediately | put probing/playback/compile in workers |
| Spatial model churn | editor saves fields the runtime ignores | keep runtime-compatible placement fields and phase `z` carefully |
| Spotify queue expectations exceed API support | UX promises controls we cannot honor | separate local queue editing from remote Spotify queue actions |
| Preview becomes a second renderer | simulation diverges from hardware output | reuse runtime/frame logic or defer |
| Packaging complexity arrives too early | slows feature work | defer installer/EXE work until GUI behavior is proven |

## Validation Plan

### Automated

- `dev/tests/test_gui_state.py`
- `dev/tests/test_gui_services.py`
- `dev/tests/test_gui_spatial_projection.py`
- `dev/tests/test_gui_palette_editor.py`
- `dev/tests/test_gui_queue_controller.py`
- existing regression suites for:
  - playlist
  - local session
  - spatial parsing
  - Spotify client/watcher
  - profile load/save

### Manual

1. Launch GUI without hardware and load `dev/devices-dummy.yaml`
2. Edit placements and save back to YAML
3. Load a built-in profile, tweak colors, save, reload, confirm persistence
4. Queue local files, drag to reorder, play now, skip, delete upcoming
5. Start a dummy local session and confirm the GUI remains responsive
6. Connect Spotify, confirm queue display/add/skip/shuffle behavior
7. If stretch goal lands, preview a compiled show on the simulated room map

## Completion Criteria

- [ ] DreamSync has a stable `dreamsync gui` entry point
- [ ] CLI remains supported and functional
- [ ] spatial placement is visually editable
- [ ] palette/profile editing is visual and persistent
- [ ] local queue control is fully usable from the GUI
- [ ] Spotify queue support is present within supported API limits
- [ ] major session types can be started/stopped from the GUI
- [ ] show preview is either implemented cleanly or explicitly deferred
