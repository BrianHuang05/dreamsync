# Backend Show Control Surface Plan

## Goal

Expand the backend control surface so DreamSync can support much richer show
authoring and operator control from the GUI without forcing direct YAML edits or
requiring recompilation for every small experiment.

This phase is specifically about backend capabilities, not the GUI widgets
themselves.

## Status

As of 2026-05-22, Phases 1 through 6 in this plan are implemented:

- structured profile edit APIs
- per-show control patches
- generic `scene_layers` with `eq_layers` compatibility
- runtime control bus for playback and live sessions
- richer telemetry exposure through GUI-facing service snapshots
- reactive mode alignment for `wave` and `gradient`

Remaining from this roadmap:

- optional Phase 7: true multi-effect compositing, only if the current layered
  control surface still proves too limiting
- GUI/editor work that consumes these backend surfaces, which belongs in the
  desktop GUI roadmap rather than this backend plan

## Problem Statement

The current backend already supports a surprising amount of control at compile
and runtime:

- profile palettes, weighted mood effects, params, transitions
- EQ routes and instrument routes
- cue params flowing into playback/runtime
- spatial presets, spatial origins, widths, extents, and layered spatial color
  compositing
- live/offline instrument and EQ metadata

But the control surface is still incomplete in four important ways:

1. The GUI persistence layer can only edit palettes cleanly.
2. There is no first-class per-show override model separate from profiles.
3. Layering is still represented narrowly through `eq_layers`-style params.
4. Runtime output still renders one base effect pattern per device, then tints
   or weights it, rather than composing multiple independent effect engines.

## Non-Goals

- Do not redesign analyzer or compiler music features in this phase.
- Do not implement the GUI editor widgets in this phase.
- Do not assume true isolated stems are available.
- Do not replace the current profile YAML system.

## Desired End State

By the end of this work, the backend should support:

- structured editing of palettes, effects, mood params, EQ routes,
  instrument routes, and transitions
- non-destructive per-show control patches
- generic scene/effect layers instead of EQ-specific layer naming
- runtime hot overrides for active sessions
- richer runtime telemetry for control-room visibility
- an optional path to true multi-effect compositing if the simpler control
  surface proves insufficient

## Current Constraints

### Constraint 1: Profile persistence is palette-only

The GUI profile service currently exposes palette updates but not structured
editing for effects/routes/transitions/params.

Implication:

- richer GUI controls would either be blocked or would need to mutate YAML
  ad hoc, which is brittle

### Constraint 2: Show compilation has no patch layer

The GUI show service currently does "analyze then compile" directly, with no
backend abstraction for song-specific or show-specific overrides.

Implication:

- a user cannot cleanly say "keep the profile, but make this song's vocals more
  blue and less pulsey"

### Constraint 3: Layer representation is too narrow

Runtime spatial layering currently works, but the shape is still rooted in
`eq_layers` and route-generated params instead of a first-class generic scene
layer model.

Implication:

- authoring remains awkward
- instrument, EQ, and future manual layers are harder to unify

### Constraint 4: Only one base effect pattern is rendered per device at a time

The output path renders one base effect frame, then applies spatial weighting
and color blending on top.

Implication:

- current layering is useful, but it is not true "independent concurrent
  effects" for drums, vocals, bass, and ambience at the same time

### Constraint 5: Active sessions are not hot-patchable

Reactive/live and saved-show sessions start with settings, but there is no
general runtime control bus for pushing updated visual overrides into an
already-running session.

Implication:

- greater operator control will remain restart-heavy unless this is added

## Implementation Phases

## Phase 1: Structured Profile Edit API

### Deliverables

- Add backend service methods for updating:
  - mood effect pools
  - mood params
  - transition rules
  - profile-level EQ routes
  - mood-level EQ routes
  - profile-level instrument routes
  - mood-level instrument routes
- Add validation helpers so GUI/editor code never writes malformed route data.
- Keep profile round-trip behavior stable and deterministic.

### Suggested backend additions

- `ProfileService.update_mood_effects(...)`
- `ProfileService.update_mood_params(...)`
- `ProfileService.update_eq_routes(...)`
- `ProfileService.update_instrument_routes(...)`
- `ProfileService.update_transitions(...)`

### Tests

- service round-trip tests for each update path
- invalid route payload rejection tests
- profile serialization stability tests

## Phase 2: Per-Show Control Patch Model

### Deliverables

- Introduce a first-class backend model for show-specific overrides.
- Allow patch application on top of compiled cues without mutating the original
  profile.
- Support patch scopes such as:
  - whole show
  - section type
  - phrase type
  - cue index/time range
  - route match

### Suggested model

- `ShowControlPatch`
- `CueOverrideRule`
- `RouteOverrideRule`

### Initial supported override fields

- palette override
- color bias override
- render mode override
- intensity multiplier/offset
- speed multiplier/offset
- transition override
- route enable/disable
- spatial preset/origin/width override

### Tests

- patch application tests against compiled timelines
- precedence tests: patch vs profile vs compiler defaults
- serialization tests if patches are persisted

## Phase 3: Generalize Scene Layers

### Deliverables

- Replace EQ-specific layer naming with a generic layer model.
- Allow the compiler, live runtime, and future GUI overrides to emit the same
  layer structure.
- Preserve backward compatibility for existing `eq_layers` payloads during
  migration.

### Suggested model

- `scene_layers` or `effect_layers`
- layer fields:
  - source type: `eq`, `instrument`, `manual`, `ambient`
  - source key
  - blend mode
  - color override
  - layer weight
  - spatial params
  - optional effect params

### Tests

- backward compatibility tests for old compiled shows
- mapper resolution tests for generic layers
- runtime output compositing tests

## Phase 4: Runtime Control Bus

### Deliverables

- Add a backend override channel for active sessions.
- Allow active sessions to receive and apply control changes without restart.
- Support both saved-show playback and reactive/live sessions.

### Minimum commands

- set active palette
- set color bias override
- set global intensity/speed trim
- enable/disable EQ route groups
- enable/disable instrument route groups
- override render mode
- override spatial preset/origin/width
- clear overrides

### Suggested architecture

- session-local `RuntimeControlState`
- thread-safe override store
- runtime merge step just before render/send

### Tests

- session-state merge tests
- thread-safety tests around live updates
- playback/runtime tests proving changes take effect mid-session

## Phase 5: Richer Telemetry Surface

### Deliverables

- Expand GUI telemetry snapshots to expose active musical/control state.
- Surface fields already available in live/runtime paths rather than keeping
  them buried in raw telemetry files.

### Useful snapshot fields

- dominant band
- dominant proxy
- active EQ routes
- active instrument routes
- active scene layers
- pan center / width
- selected palette
- selected effect/render mode
- active overrides

### Tests

- telemetry snapshot service tests
- GUI service-level tests for populated runtime diagnostics payloads

## Phase 6: Reactive Control Surface Alignment

### Deliverables

- Expand backend-accepted reactive render modes to match the runtime/compiler
  vocabulary already in use.
- Ensure saved-show, live reactive, and preview paths share a compatible set of
  render/effect concepts.

### Alignment targets

- `wave`
- `gradient`
- any other currently valid runtime render modes not exposed through the
  reactive settings model

### Tests

- validation model tests
- runtime supervisor session-start tests
- preview/runtime smoke tests for newly allowed modes

## Phase 7: Optional True Multi-Effect Compositing

This phase should only happen if phases 1 through 6 still leave show control
feeling too limited.

### Deliverables

- Allow more than one independently rendered effect frame per device.
- Composite those effect frames before transport.
- Support layer-specific render modes and params, not only color/spatial
  overlays.

### Why it is optional

The current stack may already be good enough once structured edits, per-show
patches, generic layers, and runtime overrides exist.

### Risks

- significantly more runtime complexity
- harder testing/debugging
- performance cost on dense multi-device setups

### Tests

- deterministic compositing tests
- performance/regression tests
- device simulation tests with overlapping independent layers

## Recommended Implementation Order

1. Phase 1: Structured Profile Edit API
2. Phase 2: Per-Show Control Patch Model
3. Phase 3: Generalize Scene Layers
4. Phase 4: Runtime Control Bus
5. Phase 5: Richer Telemetry Surface
6. Phase 6: Reactive Control Surface Alignment
7. Phase 7 only if needed

## Risks and Mitigations

### Risk: Too many overlapping override sources

Mitigation:

- define a strict precedence order early:
  - compiler defaults
  - profile config
  - compiled cue params
  - show patch
  - runtime override

### Risk: Breaking old compiled shows

Mitigation:

- keep backward-compatible parsing for existing cue params
- add compatibility tests before renaming layer fields

### Risk: GUI outruns backend semantics

Mitigation:

- finish Phases 1 through 4 before broad GUI control work
- keep editor widgets bound to typed backend APIs, not raw YAML munging

### Risk: False promise of "fully independent effects"

Mitigation:

- clearly separate "layered control and overrides" from "true multi-effect
  compositing"
- keep Phase 7 explicitly optional

## Validation Checklist

Before this work is considered complete:

- profiles can be edited structurally without hand-editing YAML
- one compiled song can receive per-show overrides without mutating its profile
- runtime accepts hot overrides during playback/live sessions
- diagnostics show which routes/layers/overrides are active
- existing compiled shows still play correctly
- simulation tests cover route, patch, and runtime override precedence

## Outcome

This backend phase is complete.

Recommended next phase:

1. Build GUI controls on top of the new backend APIs and session override bus.
2. Validate operator workflows with real songs and local preview.
3. Revisit Phase 7 only if layered overrides still do not provide enough show
   control.
