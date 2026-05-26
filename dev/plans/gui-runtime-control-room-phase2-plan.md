# GUI Runtime Control Room Phase 2 Plan

## Goal

Finish the control-room transition so the GUI is viable for normal operation without dropping back to the CLI for:

- hardware-output mode selection
- full non-Spotify capture/reactive configuration
- telemetry, diagnostics, and operator-facing source panels

This phase assumes the first control-room slice already exists:

- runtime supervisor
- local playlist playback
- saved-show playback
- background capture + compile pipeline
- reactive mode switching
- captured-ready queue

What remains is making that surface operationally complete and understandable in real use.

## Explicit Scope

### In scope

- hardware vs simulation output selection in the GUI
- audio input/output device selection in the GUI
- non-Spotify capture options in the GUI
- non-Spotify reactive/live options in the GUI
- better telemetry and diagnostics visibility
- clearer source panels for:
  - local playlist
  - saved precompiled shows
  - captured ready shows
- safe routing and switching rules for real hardware output

### Out of scope

- Spotify watcher integration
- Spotify-assisted track-boundary capture
- automatic boundary refresh from Spotify playback state
- multi-user / remote control
- arbitrary show editing

## Why A Separate Phase

The current GUI control-room slice proves the orchestration model, but it is still intentionally simulation-first and light on operational controls.

Today’s main gaps are:

1. Output routing is not an explicit operator decision.
2. The GUI only exposes a narrow subset of capture/reactive options.
3. Diagnostics are mostly status labels instead of true runtime telemetry.
4. Sources exist, but the UX still feels like a patched queue tab instead of a runtime dashboard.

This phase addresses those gaps without reopening the core supervisor architecture.

## Current State Summary

### Already present

- `RuntimeSupervisor` coordinates output ownership and background pipeline state.
- `SessionService` can start:
  - local playlist sessions
  - saved-show sessions
  - reactive live sessions
  - timeline playback sessions
- `PipelineCoordinator` tracks captured items through:
  - `captured`
  - `analyzing`
  - `compiling`
  - `ready`
  - `playing`
  - `failed`
- the queue/playback tab now includes:
  - output mode controls
  - capture start/stop
  - pipeline switch
  - reactive start
  - captured ready actions

### Still missing

- no explicit GUI concept of:
  - `Simulation Only`
  - `Configured Hardware`
  - selected output audio device
  - selected live input device
- no GUI parity for the major non-Spotify CLI flags
- no structured telemetry service feeding the diagnostics tab
- no dedicated source panels for saved shows and capture session context

## Primary Design Objective

Move from:

```text
queue tab + scattered buttons + status labels
```

to:

```text
runtime dashboard
-> routing controls
-> capture/reactive configuration
-> source panels
-> live telemetry and diagnostics
```

## Phase 2 Architecture

### 1. Output routing model

The GUI needs an explicit routing layer above `simulation_only=True/False`.

Suggested concepts:

```python
OutputTarget:
    mode: "simulation" | "hardware"
    config_path: Path | None
    output_audio_device: int | None
    live_input_device: int | None
```

```python
RuntimeRoutingState:
    output_target: OutputTarget
    available_output_devices: tuple[AudioDeviceOption, ...]
    available_input_devices: tuple[AudioDeviceOption, ...]
    selected_output_owner: str
```

This should become a first-class part of GUI runtime state rather than an implicit parameter passed ad hoc into session launches.

### 2. Runtime settings model

Split mode configuration from execution state.

Suggested settings groupings:

- `CaptureSettings`
- `ReactiveSettings`
- `PipelinePlaybackSettings`
- `OutputRoutingSettings`

These should live in GUI model/service code, not directly in widgets.

### 3. Telemetry and diagnostics service

Add a service that produces structured snapshots from:

- `RuntimeSupervisor`
- active session handles
- capture orchestrator stats
- pipeline worker stats
- playback summaries
- recent errors/warnings/log lines

Suggested concept:

```python
RuntimeTelemetrySnapshot:
    output_mode: str
    capture_state: str
    pipeline_state: str
    ready_queue_count: int
    current_track: str
    device_status: str
    audio_output: str
    input_device: str
    elapsed_seconds: float
    last_error: str
    warnings: tuple[str, ...]
    metrics: dict[str, object]
```

### 4. Source-panel UX split

The current queue tab should be reorganized into clearer source sections:

- `Local Playlist`
- `Saved Shows`
- `Captured Ready Shows`
- `Runtime / Routing`

This does not require a brand-new window, but it does require better panel boundaries and state ownership.

## Implementation Strategy

### Phase 2A: Hardware output routing and device selection

#### Objective

Make the GUI explicit about whether runtime modes are driving simulation or real hardware, and which audio devices are involved.

#### Files

- likely new:
  - `src/dreamsync/gui/models/runtime_routing_state.py`
  - `src/dreamsync/gui/services/audio_device_service.py`
- likely updates:
  - `src/dreamsync/gui/services/session_service.py`
  - `src/dreamsync/gui/services/runtime_supervisor.py`
  - `src/dreamsync/gui/main_window.py`
  - `src/dreamsync/gui/widgets/queue_panel.py`
  - `src/dreamsync/audio/system_input.py`

#### Work

- expose GUI-readable lists of:
  - output audio devices
  - input audio devices
- add routing controls:
  - `Simulation Only`
  - `Use Configured Hardware`
  - output audio device picker
  - live input device picker
- make routing state persist in GUI settings
- update session launches to use selected routing
- surface fallback behavior clearly:
  - if hardware config is invalid or devices are unreachable, show whether the mode:
    - fails fast
    - falls back to simulation

#### Rules

1. Simulation mode must never silently claim to be hardware mode.
2. Hardware mode must show which config and devices it resolved.
3. Output routing changes must be visible before the operator starts or switches modes.
4. Real output and simulation preview must remain distinguishable at all times.

#### Completion criteria

- the GUI can explicitly run any supported output mode in simulation or hardware mode
- the GUI can explicitly select output and input audio devices where relevant
- routing and fallback status are visible without reading terminal output

### Phase 2B: Full non-Spotify capture controls

#### Objective

Bring the non-Spotify capture pipeline options into the GUI.

#### Relevant current CLI surface

Capture-related settings already exist in CLI/runtime paths, including:

- capture enable/disable
- capture directory
- capture naming
- capture buffer
- device pattern
- sample rate / frame / hop / blocksize where applicable
- pipeline mode and playback behavior
- playback-device selection
- purge behavior

#### Files

- likely new:
  - `src/dreamsync/gui/models/capture_settings.py`
  - `src/dreamsync/gui/widgets/capture_control_panel.py`
- likely updates:
  - `src/dreamsync/gui/services/runtime_supervisor.py`
  - `src/dreamsync/gui/main_window.py`
  - `src/dreamsync/gui/settings.py`

#### Work

- add GUI controls for:
  - capture directory
  - naming mode
  - max capture buffer
  - device pattern
  - sample rate
  - frame size
  - hop size
  - blocksize
  - playback device for pipeline playback
  - purge after playback
- persist capture settings
- validate invalid combinations in GUI before start
- distinguish:
  - capture producer settings
  - pipeline playback consumer settings

#### Completion criteria

- the GUI can configure and launch capture/pipeline operation without CLI flags
- all non-Spotify capture options currently needed for normal operation are GUI-addressable

### Phase 2C: Full non-Spotify reactive/live controls

#### Objective

Bring the useful non-Spotify `govee-live` / reactive controls into the GUI.

#### Relevant current CLI surface

Reactive-related settings already exist for:

- render mode
- audio input device
- frame size
- hop size
- blocksize
- half-time
- max-brightness
- auto-cycle
- cycle interval
- debug-mood
- telemetry dir
- crossfade-detect
- profile
- auto-profile
- profile rotation / smart rotation / auto palette chaining

#### Work

- define a practical GUI layout for reactive settings:
  - `Core`
    - input device
    - render mode
    - frame/hop/blocksize
    - half-time
    - max-brightness
  - `Behavior`
    - auto-cycle
    - cycle interval
    - crossfade detect
    - debug mood
  - `Profile`
    - explicit profile
    - auto-profile
    - profile rotation
    - smart rotation
    - auto-palette options if supported
- persist reactive settings
- make reactive sessions start from GUI settings state rather than hardcoded defaults

#### UX requirement

Do not dump every option flat into a single toolbar. Group them into:

- core runtime
- mood/effect behavior
- profile strategy

#### Completion criteria

- the GUI can launch reactive mode with the same non-Spotify configuration breadth needed from CLI
- the operator can inspect and change reactive settings without editing command lines

### Phase 2D: Telemetry and diagnostics service

#### Objective

Turn the `Logs / Diagnostics` tab into a usable operator panel.

#### Files

- likely new:
  - `src/dreamsync/gui/models/runtime_telemetry_state.py`
  - `src/dreamsync/gui/services/runtime_telemetry_service.py`
  - `src/dreamsync/gui/widgets/runtime_diagnostics_panel.py`
- likely updates:
  - `src/dreamsync/gui/widgets/log_panel.py`
  - `src/dreamsync/gui/main_window.py`
  - `src/dreamsync/gui/services/runtime_supervisor.py`

#### Expose

- current output mode
- capture status
- pipeline worker status
- queue sizes
- current track/show
- selected routing target
- audio input/output device labels
- compile counts and errors
- last pipeline item state changes
- warnings
- recent runtime events
- elapsed runtime metrics

#### Recommended UX split

- `Status`
- `Metrics`
- `Recent Events`
- `Errors / Warnings`

The operator should not need to parse raw log spam to answer:

- what is running?
- what owns output?
- why is nothing playing?
- is capture progressing?
- did compile fail?

#### Completion criteria

- the diagnostics tab shows structured, live runtime state
- errors and warnings are visible even when the operator never opened a terminal

### Phase 2E: Source-panel UX and operator flow polish

#### Objective

Make the runtime dashboard legible and source-oriented.

#### Work

- split the current queue/playback surface into clearer sections:
  - `Runtime / Routing`
  - `Local Playlist`
  - `Saved Shows`
  - `Captured Ready`
  - `Simulation / Preview`
- add a saved-show source panel:
  - chosen file
  - recent saved shows
  - clear mode indication
- improve captured source panel:
  - stable state badges
  - timestamps / durations where available
  - failure visibility
- make runtime bar reflect:
  - active owner
  - armed owner
  - simulation vs hardware target

#### Completion criteria

- the operator can tell which source is selected, which source is active, and which source is merely available
- runtime routing and source selection feel like one coherent dashboard

## Testing Plan

### Automated

Add or extend tests for:

- routing state transitions
- simulation vs hardware adapter selection
- audio input/output device selection and persistence
- capture settings validation
- reactive settings validation
- diagnostics snapshot generation
- saved-show panel state
- captured-ready panel state rendering
- runtime controls dispatching through the supervisor with configured settings

### Likely test files

- new:
  - `dev/tests/test_gui_runtime_routing.py`
  - `dev/tests/test_gui_capture_settings.py`
  - `dev/tests/test_gui_reactive_settings.py`
  - `dev/tests/test_gui_runtime_telemetry.py`
  - `dev/tests/test_gui_source_panels.py`
- existing:
  - `dev/tests/test_gui_runtime_supervisor.py`
  - `dev/tests/test_gui_mode_switching.py`
  - `dev/tests/test_gui_services.py`
  - `dev/tests/test_preview_simulation.py`

### Manual validation scenarios

1. Select `Simulation Only`, run local playlist playback, and confirm only simulation updates.
2. Select `Configured Hardware`, run saved-show playback, and confirm real output is used with the chosen output audio device.
3. Start capture pipeline with non-default capture settings and confirm they persist across GUI restart.
4. Start capture pipeline with pipeline playback armed and confirm captured-ready items advance visibly through state changes.
5. Start reactive mode with custom frame/hop/blocksize and confirm the diagnostics panel shows the chosen configuration.
6. Switch from hardware reactive mode to simulation preview of a captured item and confirm hardware ownership is released cleanly.
7. Force a failure case, such as invalid config or bad capture directory, and confirm the diagnostics panel surfaces the error clearly.
8. Confirm the saved-show panel, local playlist panel, and captured-ready panel all remain usable while another mode owns output.

## Risks

### Routing ambiguity

If simulation vs hardware selection is not explicit enough, operators will misread test results or accidentally drive real devices.

### Settings overload

Reactive and capture modes have many options. A flat form will become overwhelming quickly.

### Hidden parity gaps

Some CLI options may rely on assumptions that are not yet modeled in the GUI state layer.

### Diagnostics drift

If telemetry snapshots are assembled ad hoc from multiple places, the GUI can display contradictory status.

## Recommended Rollout Order

1. Output routing and device selection
2. Capture settings model + controls
3. Reactive settings model + controls
4. Telemetry/diagnostics service
5. Source-panel UX polish
6. Final regression + real-hardware validation

## Success Criteria

This phase is successful when:

- the operator can choose simulation or hardware output directly in the GUI
- the operator can configure capture and reactive modes from the GUI without CLI flags
- the diagnostics panel explains current runtime behavior and failures clearly
- the source panels clearly separate local, saved-show, and captured-ready content
- normal runtime orchestration no longer depends on the CLI except for Spotify-specific workflows

## Relationship To Other Plans

- [gui-runtime-control-room-plan.md](/C:/Users/brian/dreamsync/dev/plans/gui-runtime-control-room-plan.md)
  - control-room architecture and first GUI orchestration slice
- [gui-precompiled-show-player-plan.md](/C:/Users/brian/dreamsync/dev/plans/gui-precompiled-show-player-plan.md)
  - saved-show playback slice
- [interactive-audio-device-picker.md](/C:/Users/brian/dreamsync/dev/plans/interactive-audio-device-picker.md)
  - foundational device-selection work that this phase should reuse for GUI routing controls
- [streaming-show-pipeline.md](/C:/Users/brian/dreamsync/dev/plans/streaming-show-pipeline.md)
  - capture/pipeline runtime foundation
