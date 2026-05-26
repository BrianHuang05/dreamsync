# GUI Runtime Control Room Plan

## Goal

Turn the GUI into the primary DreamSync operator surface for runtime control, so a user can:

- play a saved precompiled show
- play on-the-fly local shows from audio files or folders
- run the live capture pipeline in the background
- run live reactive audio mode
- switch which runtime currently owns output
- keep background producers alive while another mode is currently driving lights/audio

This is a control-room model rather than a single-session model.

## User Experience Target

The GUI should let the user do things like:

1. Start the capture pipeline and let it continuously capture and analyze incoming songs.
2. While that background pipeline is working, play a different saved show file immediately.
3. When a captured song is fully compiled, either:
   - auto-queue it for playback
   - preview it in simulation
   - manually switch output over to it
4. Drop back into live reactive audio mode without shutting the capture pipeline down.

In other words:

```text
capture/analyze/compile should be able to keep running
while output mode is switched independently
```

## Why This Is Bigger Than “Port session --pipeline”

Today the runtime surfaces are still mostly single-purpose entry points:

- `play --show ...` for explicit compiled show playback
- `play ...` / `run_local_session(...)` for local file/folder compile-and-play
- `session --pipeline --capture ...` for streaming capture -> analyze -> compile -> playback
- `govee-live` for reactive audio

Those paths each assume they mostly own the session lifecycle. The GUI behavior described here requires a higher-level orchestration layer that separates:

- background producers
- output ownership
- queueing
- transport state

## Core Architectural Shift

Move from:

```text
one command
-> one session
-> one output mode
```

to:

```text
multiple runtime services
-> shared GUI supervisor
-> one active output target at a time
-> optional background producers continuing in parallel
```

## Runtime Roles

### 1. Background producers

These can continue running even when they are not currently driving output.

- capture orchestrator
- pipeline analyze/compile worker
- pipeline ready queue
- local precompiler for queued files
- Spotify queue watcher

### 2. Output drivers

These actively drive devices and possibly audio playback.

- saved precompiled show playback
- local compile-and-play playback
- pipeline playback consumer
- live reactive audio mode

### 3. GUI supervisor

This decides:

- which producers are running
- which output driver is active
- what gets paused, stopped, or kept warm during a switch

## Required Control Model

The GUI needs separate controls for:

### Producer state

- `Capture: Off / Running`
- `Pipeline Worker: Idle / Processing / Ready Queue N`
- `Spotify Watcher: Off / Connected`

### Output state

- `Output Mode:`
  - `Saved Show`
  - `Local Playlist`
  - `Pipeline Playback`
  - `Reactive Live`
  - `Simulation Only`

### Routing actions

- `Switch To Saved Show`
- `Switch To Pipeline Playback`
- `Switch To Reactive`
- `Preview Ready Track`
- `Keep Capture Running`
- `Pause Output`

This is the key conceptual split:

- capture/pipeline can remain running
- output ownership can move independently

## Current Code Reality

### GUI-supported today

The GUI already wraps local preview through `run_local_session(...)` via [src/dreamsync/gui/services/session_service.py](/C:/Users/brian/dreamsync/src/dreamsync/gui/services/session_service.py:50).

That gives it:

- local audio/file playback
- cached analyze/compile/play
- playlist control
- simulation preview

### CLI-only today

- explicit precompiled show playback via `play --show ...`
- streaming capture pipeline via `session --pipeline`
- reactive live mode via `govee-live`

### Key limitation

The current session shapes are still too monolithic for dynamic runtime switching. The GUI needs a supervisor layer above them.

## Desired End State

The GUI should expose one unified runtime dashboard where:

- the left side manages runtime modes and background services
- the center/right side shows queue state, ready compiled tracks, and simulation preview
- the operator can switch output targets without tearing down every background process

## Scope

### In scope

- GUI support for precompiled show playback
- GUI support for starting/stopping capture pipeline
- GUI support for starting/stopping reactive live mode
- runtime output-mode switching
- persistent ready queue for compiled captured tracks
- simulation preview of any selected output source
- clear status and arbitration rules

### Out of scope

- multiple output modes driving the same physical devices at the same time
- fully arbitrary timeline editing
- remote multi-user control
- fully automatic DJ-style policy logic for every switch

## Non-Negotiable Rule

Only one runtime may own hardware output at a time unless the target is explicitly simulation-only.

That means:

- capture pipeline may continue running in background
- reactive analyzer may continue listening in background if useful
- but only one of:
  - saved show playback
  - local show playback
  - pipeline playback consumer
  - reactive live output

may drive physical devices at once

## Implementation Strategy

### Phase 1: Introduce a GUI runtime supervisor

#### Objective

Create a service/controller layer above individual sessions that can manage multiple runtime components together.

#### Files

- new:
  - `src/dreamsync/gui/services/runtime_supervisor.py`
  - `src/dreamsync/gui/models/runtime_mode_state.py`
- likely updates:
  - `src/dreamsync/gui/services/session_service.py`
  - `src/dreamsync/gui/main_window.py`

#### Work

- define the concepts:
  - producer
  - output driver
  - active output owner
  - simulation target
- add supervisor APIs such as:
  - `start_capture_pipeline(...)`
  - `start_reactive_live(...)`
  - `start_saved_show(...)`
  - `switch_output_mode(...)`
  - `stop_output_only()`
  - `stop_all()`
- maintain a unified runtime state snapshot for GUI rendering

#### Completion criteria

- GUI has one stateful runtime supervisor instead of isolated ad hoc session launches

### Phase 2: Port precompiled show playback into the supervisor

#### Objective

Integrate the precompiled-show player as one output driver in the new model.

#### Work

- reuse the dedicated precompiled-show session plan
- register saved-show playback as a switchable output mode
- ensure simulation and real-device routing work through the supervisor

#### Completion criteria

- saved shows are first-class GUI output modes

### Phase 3: Port streaming capture pipeline into the GUI as background services

#### Objective

Bring `session --pipeline --capture ...` into the GUI without forcing it to own the whole app.

#### Files

- [src/dreamsync/session.py](/C:/Users/brian/dreamsync/src/dreamsync/session.py)
- [src/dreamsync/show_pipeline_worker.py](/C:/Users/brian/dreamsync/src/dreamsync/show_pipeline_worker.py)
- [src/dreamsync/show_playback_consumer.py](/C:/Users/brian/dreamsync/src/dreamsync/show_playback_consumer.py)
- new GUI service/controller wrappers

#### Work

- split capture pipeline into separately controllable components:
  - capture orchestrator
  - pipeline worker
  - playback consumer
- let the GUI choose whether pipeline playback consumer is:
  - active output owner
  - paused
  - simulation-only
- keep ready compiled tracks visible even when pipeline playback is not active

#### Completion criteria

- the GUI can run capture and compilation in the background
- captured compiled shows accumulate in a visible ready queue
- pipeline playback can be activated or left idle independently

### Phase 4: Port live reactive mode into the supervisor

#### Objective

Make reactive live output a switchable GUI output mode alongside saved shows and pipeline playback.

#### Files

- `src/dreamsync/live.py`
- `src/dreamsync/director.py`
- `src/dreamsync/gui/services/session_service.py`
- new reactive session wrappers as needed

#### Work

- create a GUI-manageable reactive runtime handle
- expose:
  - current effect mode
  - palette/profile status
  - input device/source status
- make reactive output pausable/stoppable by the supervisor

#### Completion criteria

- the GUI can enter and leave reactive live output without requiring CLI

### Phase 5: Add output arbitration and switching rules

#### Objective

Make transitions between modes predictable and safe.

#### Rules to support

1. `Switch to Saved Show`

- if reactive output is active, pause/stop reactive output
- leave capture pipeline running if requested
- start show playback

2. `Switch to Reactive`

- stop current show playback
- leave capture and compile workers running if requested
- start reactive output

3. `Switch to Pipeline Playback`

- if compiled-ready queue has items, start consumer as output owner
- otherwise arm pipeline playback and wait for next ready track

4. `Simulation Preview`

- may preview any available timeline without taking hardware ownership if simulation-only mode is selected

#### Completion criteria

- mode switching has explicit, testable policies
- the GUI never leaves two real output drivers fighting over devices

### Phase 6: Unified queue and source panels

#### Objective

Expose three distinct but related queues/sources clearly:

- local playlist queue
- saved show selection / recent shows
- pipeline ready queue of captured compiled tracks

#### Work

- add a panel for `Captured Ready Shows`
- show per-track state such as:
  - captured
  - analyzing
  - compiling
  - ready
  - playing
  - failed
- allow actions:
  - `Preview`
  - `Play Now`
  - `Send To Top`
  - `Discard`

#### Completion criteria

- the operator can see and choose among all current sources of playable content

### Phase 7: Session telemetry and diagnostics

#### Objective

Make this operable in real use.

#### Expose

- capture status
- current input/output device routing
- queue depths
- compile latency
- current output mode
- active playback track
- current simulation target
- errors and warnings

#### Completion criteria

- the operator can understand what DreamSync is doing without consulting terminal output

## UI Proposal

### Top runtime bar

- `Reactive`
- `Saved Show`
- `Local Playlist`
- `Pipeline`
- `Simulation`

These are not just tabs. They are mode selectors and status indicators.

### Left control rail

- `Capture`
  - start/stop
  - capture dir
  - buffer size
  - Spotify track-boundary toggle
- `Reactive`
  - input source
  - profile
  - start/stop
- `Pipeline`
  - worker status
  - ready queue count
  - auto-play toggle

### Center content

- current source queue
- current track/show details
- ready compiled captures

### Right content

- simulation canvas
- runtime diagnostics
- logs

## Service-Level Refactors Needed

### Capture pipeline extraction

The CLI `run_session(...)` path should be decomposed into reusable services:

- `CaptureService`
- `PipelineCompileService`
- `PipelinePlaybackService`

instead of one giant CLI-oriented flow.

### Reactive runtime extraction

Similarly, `govee-live` behavior should become a reusable GUI service/handle rather than a CLI-only launch path.

### Adapter ownership model

The supervisor will likely need a shared adapter manager or output lease model so output drivers can acquire/release device ownership cleanly.

## Suggested Internal Concepts

### Output lease

```python
class OutputLease:
    owner: str
    mode: str
    simulation_only: bool
```

Only one non-simulation lease may be active at once.

### Runtime component handles

Each runtime component should expose:

- `start()`
- `stop()`
- `status_snapshot()`
- optional `pause()` / `resume()`

### Ready track model

For captured compiled tracks:

```python
CapturedShowItem:
    mp3_path: Path
    analysis_path: Path | None
    show_path: Path | None
    state: "captured" | "analyzing" | "compiling" | "ready" | "playing" | "failed"
    title: str
    artist: str
    duration: float | None
    error: str | None
```

## Testing Plan

### Automated

Add or extend tests for:

- runtime supervisor state transitions
- switching output owners cleanly
- pipeline services continuing while another output mode is active
- ready queue population from captured songs
- reactive-mode stop/start through GUI services
- simulation preview against saved shows and pipeline-ready shows

### Likely test files

- new:
  - `dev/tests/test_gui_runtime_supervisor.py`
  - `dev/tests/test_gui_pipeline_services.py`
  - `dev/tests/test_gui_mode_switching.py`
- existing:
  - `dev/tests/test_preview_simulation.py`
  - `dev/tests/test_show_pipeline_worker.py`
  - `dev/tests/test_show_playback_consumer.py`

### Manual validation scenarios

1. Start capture pipeline in GUI and confirm captured tracks move through `captured -> analyzing -> compiling -> ready`.
2. While pipeline is running, switch output to a saved precompiled show and confirm capture/compile continues.
3. While saved show is playing, confirm a newly ready captured show appears in the queue without interrupting output.
4. Stop saved show and switch output to pipeline playback.
5. Switch from pipeline playback to reactive live mode without tearing down capture/compile workers.
6. Run simulation-only preview of a ready captured show while reactive mode owns physical output.

## Risks

### Hidden coupling in current session entry points

The current CLI flows assume broader ownership than the GUI control-room model allows. Refactoring them cleanly is the biggest architectural risk.

### Output contention

Without an explicit lease/arbitration layer, saved show playback, reactive mode, and pipeline playback could all try to drive the same devices.

### UI complexity

If everything is exposed at once without hierarchy, the GUI will feel overwhelming. The control-room surface should distinguish background services from active output clearly.

### Race conditions during switching

Stopping one output mode and starting another while keeping producers alive will require careful thread/stop-event handling.

## Recommended Rollout Order

1. Precompiled-show player in GUI
2. Runtime supervisor skeleton
3. Background capture/pipeline services in GUI
4. Pipeline-ready queue UI
5. Reactive live mode in GUI
6. Output switching/arbitration
7. Polish, telemetry, simulation parity

## Success Criteria

This work is successful when:

- the GUI can run saved shows, local compile-and-play, pipeline capture/compile, and reactive live mode
- capture/analyze/compile can continue running while a different output mode is active
- the operator can switch output modes from the GUI without dropping the whole runtime
- simulation preview works across saved and captured compiled shows
- the CLI is no longer required for normal runtime orchestration

## Relationship To Other Plans

- [gui-precompiled-show-player-plan.md](/C:/Users/brian/dreamsync/dev/plans/gui-precompiled-show-player-plan.md)
  - narrower first slice for explicit `.show.json` playback
- [streaming-show-pipeline.md](/C:/Users/brian/dreamsync/dev/plans/streaming-show-pipeline.md)
  - existing CLI-oriented streaming pipeline foundation
- [gui-desktop-migration.md](/C:/Users/brian/dreamsync/dev/plans/gui-desktop-migration.md)
  - broad GUI program plan that this runtime-control work plugs into
