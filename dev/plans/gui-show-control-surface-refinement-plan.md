# GUI Show Control Surface Refinement Plan

## Goal

Turn the desktop GUI into the primary operator-facing surface for:

- direct color and palette control
- effect and transition editing
- EQ and instrument route editing
- per-song and per-show visual overrides
- live runtime overrides during playback or reactive sessions
- readable telemetry for understanding why the show currently looks the way it does

This plan assumes the backend control surface is already implemented and treats
that work as the foundation rather than re-opening backend architecture.

## Relationship To Existing Plans

This plan is the next UI-facing phase after:

- `dev/plans/backend-show-control-surface-plan.md`

It should also be treated as the GUI-side companion to:

- `dev/plans/gui-runtime-control-room-plan.md`
- `dev/plans/gui-runtime-control-room-phase2-plan.md`
- `dev/plans/gui-live-palette-pairing.md`

Practical rule:

- those plans still contain useful orchestration and queue ideas
- this document is the source of truth for the next control-surface refinement pass

## Problem Statement

The backend can now do substantially more than the GUI exposes:

- profiles can be edited structurally
- compiled shows can receive `ShowControlPatch` overrides
- active sessions can receive runtime hot overrides
- runtime telemetry now exposes dominant band/proxy, active routes, scene layers, pan, palette, and render mode

But the GUI still does not make those capabilities easy to use. Today the main
gap is not engine capability. It is operator access.

## Desired End State

By the end of this phase, a user should be able to:

1. Open a profile and directly edit:
   - palettes
   - mood effect pools
   - mood params
   - EQ routes
   - instrument routes
   - transitions
2. Select a song or compiled show and apply non-destructive overrides without
   mutating the base profile.
3. While a show is playing or reactive mode is active, push temporary visual
   overrides live from the GUI.
4. See which routes, layers, palettes, and overrides are active in the current
   moment.
5. Use local preview/simulation to validate changes before sending them to real
   devices.

## Non-Goals

- Do not implement optional true multi-effect compositing in this phase.
- Do not redesign the compiler/music-analysis stack in this phase.
- Do not replace profile YAML with a database or a different storage model.
- Do not attempt full arbitrary timeline editing in this first control-surface pass.

## Design Principles

### 1. Make the safe path the easy path

The GUI should favor non-destructive edits:

- profile edits are explicit and saved intentionally
- song/show overrides are separate from profile authoring
- runtime overrides are clearly temporary and reversible

### 2. Keep authoring and operating distinct

There are three different kinds of control and they should not feel like one
mixed form:

- profile authoring
- show/song override authoring
- live operating during playback

### 3. Explain the current output state

If lights look a certain way, the GUI should make it possible to answer:

- which palette is active
- which render mode is active
- which EQ or instrument routes are active
- whether a show patch is applied
- whether a temporary runtime override is in effect

### 4. Preview before hardware

Every major control path should be exercisable in simulation/local preview
before requiring real devices.

## UX Areas

### A. Profile editor

Purpose:

- edit long-lived profile behavior

Controls needed:

- palette list and palette editor
- mood effect pool editor with weights
- mood params editor
- EQ route table/editor
- instrument route table/editor
- transitions editor

Important behaviors:

- unsaved changes indicator
- reset/revert for the current profile file
- validation errors shown inline before save
- preserve deterministic YAML round-tripping

### B. Show override editor

Purpose:

- apply non-destructive per-song or per-show tweaks on top of compiled behavior

Controls needed:

- rule list for `ShowControlPatch`
- match editor:
  - cue index/time range
  - render mode
  - transition
  - has EQ band
  - has instrument
- override editor:
  - palette override
  - color bias
  - render mode
  - intensity/speed trim
  - transition override
  - route disable/mute rules
  - spatial preset/origin/width overrides

Important behaviors:

- patch can be enabled/disabled without deleting it
- patch effects should be previewable in simulation
- patch should clearly indicate it does not modify the source profile

### C. Runtime control panel

Purpose:

- temporary operator control during active playback/live use

Controls needed:

- palette override picker
- color bias quick-select
- render mode override
- global intensity trim
- global speed trim
- EQ route mute toggles
- instrument route mute toggles
- spatial preset/origin/width override
- clear all overrides

Important behaviors:

- clearly labeled as temporary/live controls
- one-click revert/clear
- active override badges in the session header
- changes apply without restarting the session

### D. Telemetry and explanation panels

Purpose:

- help the user understand the current musical and visual state

Panels needed:

- current palette / render mode
- dominant band / dominant proxy
- active EQ routes
- active instrument routes
- active scene layers
- pan center / pan width
- active runtime overrides
- current output mode:
  - simulation
  - saved show
  - local playback
  - reactive live

Important behaviors:

- telemetry should be human-readable first, raw second
- values should update frequently enough to feel live without becoming unreadable
- route/layer panels should make precedence understandable

### E. Preview and simulation integration

Purpose:

- let the user validate changes before using real devices

Needed behaviors:

- preview profile edits in simulation
- preview show patch edits in simulation
- preview runtime override changes in simulation
- show whether a change affects:
  - future compile only
  - current compiled show
  - active running session immediately

## Implementation Phases

## Phase 1: Profile Editor Expansion

### Deliverables

- extend the current palette/profile GUI into a structured profile editor
- add tabs or panes for:
  - effects
  - params
  - EQ routes
  - instrument routes
  - transitions
- wire the editor to `ProfileService` update methods

### Likely touchpoints

- `src/dreamsync/gui/controllers/palette_controller.py`
- `src/dreamsync/gui/services/profile_service.py`
- profile editor widgets/models under `src/dreamsync/gui/`

### Tests

- controller tests for loading/editing/saving each section
- widget/model validation tests for malformed route data
- round-trip tests proving profile changes persist and reload correctly

## Phase 2: Song/Show Override Authoring

### Deliverables

- add GUI models for editable `ShowControlPatch` rules
- allow attaching a patch to a song/show from the queue or show detail view
- add simulation preview for patched output

### Suggested GUI concepts

- `ShowPatchState`
- `CueOverrideRuleState`
- patch-enabled / patch-disabled toggle

### Tests

- controller tests for add/edit/remove/disable patch rules
- service tests confirming GUI state compiles/applies patches correctly
- preview tests verifying patched shows differ from unpatched ones

## Phase 3: Runtime Override Panel

### Deliverables

- expose `RuntimeSupervisor.update_runtime_control(...)`
- add a dedicated runtime override panel for active sessions
- surface active override state in the session header / status area

### Controls

- palette override
- color bias
- render mode
- intensity/speed trims
- route mute toggles
- spatial overrides
- clear overrides

### Tests

- GUI service/controller tests for live override updates
- session tests proving active session snapshot reflects the new override state
- simulation tests proving runtime override changes visibly affect output state

## Phase 4: Telemetry-Driven Control Room Refinement

### Deliverables

- redesign diagnostics/status panels around the richer runtime telemetry model
- add panels for active routes, scene layers, dominant band/proxy, and pan
- make active override state obvious

### Tests

- telemetry presentation/state tests
- runtime-supervisor integration tests for session switching and state carryover
- smoke tests for simulation-only workflows

## Phase 5: Preview-Centric Validation Workflow

### Deliverables

- make simulation/local preview the standard validation path for control edits
- add clear affordances for:
  - preview current profile
  - preview patched show
  - preview active runtime override
- clarify when a change is:
  - saved to profile
  - stored as a show patch
  - temporary live state only

### Tests

- preview-state tests across profile edits, patch edits, and runtime overrides
- GUI workflow tests covering edit -> preview -> save/apply/clear

## Phase 6: Fit-and-Finish for Real Operator Use

### Deliverables

- keyboard-safe operator flows
- clearer error states and validation messaging
- dirty-state warnings before closing or switching
- visual polish for high-signal route/layer/override displays

### Tests

- GUI state tests for dirty-state handling
- regression tests around session switching, simulation mode, and patch persistence

## Data and State Model Guidance

The GUI should keep these layers clearly separate:

### Persistent profile state

- source-of-truth profile YAML
- edited through `ProfileService`

### Persistent song/show patch state

- GUI-owned patch model or persisted patch file
- applied on top of compiled cues

### Temporary runtime override state

- session-local only
- driven through `RuntimeControlBus`
- never silently written back into profile or patch state

## Precedence Model To Communicate In UI

The UI should explain the visual control order as:

1. compiler defaults
2. profile config
3. compiled cue params
4. show patch
5. runtime override

This matters because users will otherwise assume a profile change should
immediately overwrite a temporary live override, which is not how the runtime is
designed.

## Risks

### Risk: UI becomes too dense

Mitigation:

- separate authoring, patching, and live operating into distinct panes/tabs
- prefer progressive disclosure over one giant form

### Risk: users confuse temporary vs persistent changes

Mitigation:

- label each control surface clearly:
  - `Profile`
  - `Show Override`
  - `Live Override`
- add save/apply/clear language that matches the actual scope

### Risk: preview does not match hardware enough to build trust

Mitigation:

- keep simulation as the default validation path
- add targeted hardware checks only after preview behavior is trusted

### Risk: route/effect editing becomes hard to understand

Mitigation:

- provide structured forms with constrained options, not raw YAML
- show inline summaries such as:
  - `When vocals dominate -> gradient, warm bias, front-center pan follow`

## Recommended Implementation Order

1. Phase 1: Profile Editor Expansion
2. Phase 2: Song/Show Override Authoring
3. Phase 3: Runtime Override Panel
4. Phase 4: Telemetry-Driven Control Room Refinement
5. Phase 5: Preview-Centric Validation Workflow
6. Phase 6: Fit-and-Finish for Real Operator Use

## Validation Checklist

Before this phase is considered complete:

- a user can edit effects, routes, transitions, and palettes without touching YAML
- a user can create and preview a show-specific override patch from the GUI
- a user can apply and clear live runtime overrides without restarting a session
- telemetry makes active palette/render/routes/layers/overrides visible
- simulation/local preview is good enough to validate most control changes before hardware

## Next Phase After This Plan

If this phase is successful, the next decision point should be:

- either continue into real-device operator polish and workflow tuning
- or, if control still feels too limited, evaluate optional true multi-effect compositing
