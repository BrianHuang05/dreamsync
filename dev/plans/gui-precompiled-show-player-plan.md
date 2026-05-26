# GUI Precompiled Show Player Plan

## Goal

Bring the CLI's precompiled show playback flow into the desktop GUI so a user can:

- load an audio file plus a specific `.show.json`
- preview that exact compiled timeline in simulation mode
- run that same compiled show against real configured devices
- inspect playback state and queue behavior from the existing GUI shell

After this lands, the next major GUI gap is the live streaming pipeline and runtime mode switching:

```text
capture -> analyze -> compile -> playback
```

That pipeline currently lives under `session --pipeline` and is separate from the local GUI preview path.

## Current State

### GUI today

The GUI starts local preview sessions through `SessionService.start_local_preview_session()` in [src/dreamsync/gui/services/session_service.py](/C:/Users/brian/dreamsync/src/dreamsync/gui/services/session_service.py:50). That path:

- accepts an `audio_path`
- builds a `PlaylistManager`
- selects `NullMultiAdapter` or `SimulationMultiAdapter`
- calls `run_local_session(...)`

This means the GUI currently supports:

- local file/folder playback
- on-the-fly analyze/compile/play
- cache reuse
- playlist controls
- simulation preview

### GUI limitation today

The GUI does not expose the CLI's direct precompiled show path:

```text
audio file + show json -> run_show_playback(...)
```

That path is currently only wired in CLI `play --show ...` in [src/dreamsync/cli.py](/C:/Users/brian/dreamsync/src/dreamsync/cli.py:1971) and implemented by [src/dreamsync/show/runtime.py](/C:/Users/brian/dreamsync/src/dreamsync/show/runtime.py:246).

### Important nuance

The GUI may reuse cached compiled timelines indirectly through `run_local_session(...)`, but it cannot explicitly load an arbitrary `.show.json` chosen by the user.

## Desired End State

The GUI should support two distinct local playback modes:

1. `Compile On The Fly`

- current behavior
- audio file or folder
- analyze/compile/play through `run_local_session(...)`

2. `Play Precompiled Show`

- audio file plus chosen `.show.json`
- no compilation step
- playback through `run_show_playback(...)`
- simulation preview or real configured devices using the same GUI shell

The user should be able to tell which mode is active from the queue/session panel.

## Scope

### In scope

- GUI support for loading a `.show.json`
- background session support for precompiled show playback
- simulation preview of precompiled shows
- real-device playback of precompiled shows from GUI
- playback state / error reporting / stop behavior
- light regression coverage

### Out of scope

- porting `session --pipeline`
- porting `govee-live`
- redesigning the entire queue system
- editing `.show.json` files in the GUI
- replacing the existing local preview path

## Implementation Strategy

### Phase 1: Add a dedicated GUI session mode for precompiled shows

#### Objective

Teach the GUI session layer to run:

```text
mp3 + show json -> run_show_playback(...)
```

without going through `run_local_session(...)`.

#### Files

- [src/dreamsync/gui/services/session_service.py](/C:/Users/brian/dreamsync/src/dreamsync/gui/services/session_service.py)
- [src/dreamsync/show/runtime.py](/C:/Users/brian/dreamsync/src/dreamsync/show/runtime.py)

#### Work

- add a new session service entrypoint, for example:
  - `start_precompiled_show_session(audio_path, show_path, ...)`
- keep the same adapter-selection behavior as local preview:
  - `NullMultiAdapter` when no config
  - `SimulationMultiAdapter` when config exists
- run playback in a background thread, matching the current `SessionHandle` pattern
- plumb a `stop_event` through to `run_show_playback(...)`

#### Completion criteria

- the GUI can launch a background session for a specific show file
- stop behavior matches current GUI preview semantics

### Phase 2: Expose show-file loading in the GUI

#### Objective

Give the user a clear way to attach a `.show.json` to local playback.

#### Files

- [src/dreamsync/gui/main_window.py](/C:/Users/brian/dreamsync/src/dreamsync/gui/main_window.py)
- [src/dreamsync/gui/widgets/queue_panel.py](/C:/Users/brian/dreamsync/src/dreamsync/gui/widgets/queue_panel.py)
- GUI state/controller files as needed

#### Work

- add UI affordances such as:
  - `Load Show`
  - `Clear Show`
  - mode label like `Playback Mode: Precompiled Show`
- store selected show path in GUI state
- validate that:
  - audio file is selected
  - show file exists
  - show file is only used for single-track playback initially
- disable or hide incompatible controls when a precompiled show is active

#### Recommended constraint

For the first version, support:

- one audio file
- one `.show.json`

Do not try to support folder playlists plus externally chosen show files in the same first pass.

#### Completion criteria

- users can select an audio file and matching show file from the GUI
- the GUI clearly indicates when precompiled playback mode is armed

### Phase 3: Integrate simulation preview

#### Objective

Ensure precompiled show playback drives the same simulation canvas the GUI already uses.

#### Files

- [src/dreamsync/output/null_adapter.py](/C:/Users/brian/dreamsync/src/dreamsync/output/null_adapter.py)
- [src/dreamsync/gui/main_window.py](/C:/Users/brian/dreamsync/src/dreamsync/gui/main_window.py)

#### Work

- verify `run_show_playback(...)` updates adapters in the same way local preview does
- ensure `preview_frame_snapshot()` remains available during precompiled playback
- reuse existing timer polling and simulation rendering paths
- confirm session status labels reflect:
  - current track
  - playback time
  - preview mode / device mode

#### Completion criteria

- a precompiled show can be visually previewed in the GUI simulation canvas
- no special-case simulation path is needed beyond the new session mode

### Phase 4: Integrate real-device playback

#### Objective

Allow the same GUI flow to run on configured devices, not just simulation.

#### Files

- [src/dreamsync/gui/services/session_service.py](/C:/Users/brian/dreamsync/src/dreamsync/gui/services/session_service.py)
- [src/dreamsync/output/auto_detect.py](/C:/Users/brian/dreamsync/src/dreamsync/output/auto_detect.py)
- relevant GUI settings/state files

#### Work

- reuse existing config loading rules for device-backed preview
- make sure the adapter chosen for precompiled playback matches the one used by normal GUI preview
- preserve graceful fallback to simulation when configs are dummy/unreachable

#### Completion criteria

- the GUI can run a chosen `.show.json` against configured devices
- simulation fallback still works when devices are unavailable

### Phase 5: Status, errors, and UX polish

#### Objective

Make the mode understandable and safe to use.

#### Work

- label current mode explicitly:
  - `On-the-fly compile`
  - `Precompiled show`
- show the selected `.show.json` filename
- guard against mismatched inputs with readable errors
- consider warning if the selected show does not appear to match the audio stem
- update status strings to distinguish:
  - compiling
  - cached show
  - precompiled show playback

#### Completion criteria

- users can tell whether they are previewing a generated show or an explicit show file
- errors are surfaced in the existing GUI status area

## Data and State Design

### Suggested GUI state additions

- `selected_show_path: Path | None`
- `playback_mode: "local_compile" | "precompiled_show"`

### Suggested session handle mode values

- keep existing `mode="local"`
- add `mode="precompiled_show"`

### Behavioral rule

If `selected_show_path` is present and valid, `Start Preview` should launch precompiled playback instead of `run_local_session(...)`.

## Testing Plan

### Automated tests

Add or extend tests for:

- `SessionService.start_precompiled_show_session()` launches and stops cleanly
- GUI start-preview path dispatches to the correct session mode when a show path is present
- simulation preview updates during precompiled playback
- invalid/missing show file errors surface cleanly
- local compile mode remains unchanged when no show path is selected

### Likely test files

- `dev/tests/test_preview_simulation.py`
- GUI service/controller tests if present
- new session service tests if needed

### Manual validation

1. Launch GUI with dummy config.
2. Load an MP3 and a matching `.show.json`.
3. Start preview and confirm the simulation view runs without a compile step.
4. Stop preview and confirm session cleanup is clean.
5. Repeat with a real device config and confirm device playback works.
6. Clear the show path, start preview again, and confirm the GUI falls back to on-the-fly compile behavior.

## Risks

### Two local playback stacks drifting apart

If GUI precompiled playback and CLI `play --show` diverge semantically, debugging becomes confusing.

### Queue UX ambiguity

The current queue model is file/folder oriented. Precompiled playback is naturally single-track oriented at first, so the UI must make that constraint obvious.

### Simulation assumptions

If simulation polling assumes `run_local_session(...)`-specific session objects, precompiled sessions may need a thin compatibility snapshot layer.

### Mode confusion

If the GUI does not clearly indicate whether it is compiling or using an explicit show file, users may misread test results.

## Recommended Rollout Order

1. Add background session support for precompiled playback
2. Add minimal GUI controls for selecting/clearing a show file
3. Wire start-preview dispatch between compile mode and precompiled mode
4. Verify simulation preview and real-device playback
5. Polish labels, validation, and tests

## Success Criteria

This work is successful when:

- the GUI can play an explicitly chosen `.show.json`
- the same precompiled show can run in simulation or on real devices
- existing GUI local preview still works unchanged
- the GUI makes playback mode explicit
- the only major playback workflow still missing from the GUI is the live streaming pipeline

## Follow-up

Once this plan is complete, the main remaining CLI-only port should be:

- `session --pipeline --capture ...`
- dynamic switching between saved-show playback, pipeline-backed playback, and live reactive mode

That follow-up would bring:

- live capture
- rolling buffer / capture directory management
- background analyze/compile worker
- queued playback consumer
- playback-device routing
- Spotify-assisted track boundary capture
- runtime mode arbitration between multiple active producers

into the GUI shell.
