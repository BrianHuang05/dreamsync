# Continuous Spatial Runtime Follow-Up

## Status

This document is the **new source of truth** for the next spatial/runtime phase.
It does **not** replace [space-zone-mapping.md](/C:/Users/brian/dreamsync/dev/plans/space-zone-mapping.md) as historical context, and it does **not** rewrite [gui-desktop-migration.md](/C:/Users/brian/dreamsync/dev/plans/gui-desktop-migration.md). Instead, it supersedes the older 3x3-grid-first direction for the next implementation milestone.

For instrument-aware live stereo and mixed-source separation follow-through, see
[live-instrument-pan-and-mixed-separation.md](/C:/Users/brian/dreamsync/dev/plans/live-instrument-pan-and-mixed-separation.md).

The new milestone is:

- make spatial placement actually drive runtime output
- use **continuous geometry now**
- add **explicit spatial cue metadata**
- treat **per-section strip placement as authoritative** over whole-device placement when present

## Current State

DreamSync now has enough editor-side spatial functionality that the next bottleneck is no longer placement authoring. The bottleneck is runtime consumption.

Today:

- the GUI/editor can edit device and section positions with room-style `XY`, `XZ`, and `YZ` views
- the config/editor path can save per-section strip placement
- the runtime stores top-level placement and orientation, but does not yet consume section placement
- the runtime has dormant coarse spatial plumbing such as `SpatialMapper` and `send_spatial_scene()`
- that plumbing does not yet deliver true position-driven show behavior in the main playback paths
- the repo currently has an axis-model mismatch between older runtime planning and newer GUI/editor semantics

That mismatch must be resolved before runtime spatial rollout continues.

## Decision

Adopt a **continuous coordinate-driven runtime model** now, instead of treating the older 3x3 grid as the primary end state.

Canonical axes:

- `x` = left / right
- `y` = vertical height
- `z` = front / back depth

This meaning must become consistent across:

- config parsing
- runtime placement loading
- validation and debug output
- GUI labels and views
- spatial cue metadata

The older 3x3 grid model remains useful only as:

- historical context
- an optional compatibility/fallback mode if needed during migration

It is no longer the main runtime target.

## Non-Goals

- No Spotify, queue, palette, or diagnostics work in this plan
- No photoreal preview or arbitrary camera controls
- No automatic room discovery
- No requirement for perfect physical-unit calibration
- No requirement that every cue be compiler-authored with spatial metadata in v1

Normalized coordinates remain the authority. Relative ordering matters more than exact physical precision.

## Core Design Shift

The next runtime layer should move from:

```text
one intent -> optional mapper -> coarse bucket routing -> devices
```

to:

```text
compiled cue / live intent
    -> spatial metadata normalization
    -> continuous spatial engine
    -> per-device / per-section sampling from real coordinates
    -> adapter send path
```

This means the runtime must stop treating spatial behavior as optional side plumbing and start treating it as a first-class transformation stage when spatial mode is enabled.

## Canonical Spatial Model

### Coordinates

Use one normalized room model everywhere:

- `x = -1.0` far left
- `x =  0.0` center
- `x = +1.0` far right
- `y = -1.0` floor / low
- `y =  0.0` mid height
- `y = +1.0` ceiling / high
- `z = -1.0` front / near source
- `z =  0.0` room center
- `z = +1.0` back / far from source

### Placement authority rules

- bulbs and non-sectioned devices use device-level placement
- sectioned strips use section-level placement when present
- sectionless strips fall back to the parent device placement
- device-level placement remains in config for summary, debug, and fallback
- device-level placement must not override valid section positions at runtime

### Orientation

Keep orientation-based strip reversal, but treat it as a rendering detail inside the spatial runtime flow. It should no longer be the only placement-aware behavior in the system.

## Config and Runtime Placement Consumption

### Required changes

Extend runtime placement loading so it understands section placement from `devices.yaml`, not just top-level device placement.

This includes:

- parsing section coordinates in the runtime config path
- preserving those coordinates through adapter construction
- exposing section placement in debug/validation output
- defining clear precedence between device and section placement

### Backward compatibility

Legacy configs with no spatial fields must continue to work.

Rules:

- if spatial mode is off, behavior remains unchanged
- if spatial mode is on but a device has no usable placement, fall back cleanly
- existing config files without `z` or without `sections` remain valid

## Continuous Runtime Spatial Behavior

The first runtime behaviors must be based on actual coordinates, not 3x3 bucketing.

The initial supported spatial behaviors should cover:

- emanation from a point
- directional waves along `x`, `y`, or `z`
- top-down motion via `y`
- front-back motion via `z`
- left-right motion via `x`
- color zone blending across multiple regions

Implementation priority:

1. continuous ordering and delay across coordinates
2. continuous intensity / blend falloff
3. compatibility defaults for older cues

The main requirement is that devices and strip sections sequence according to relative position in the room, even if the falloff math stays simple in v1.

## Explicit Spatial Cue Metadata

Spatial behavior should become an explicit authored/runtime control surface.

Keep existing `spatial_axis` and `spatial_focus` only as compatibility shims or default inputs. Do not keep them as the long-term primary interface.

### Stable metadata shape

Compiled cues and runtime params should converge on a stable metadata model such as:

```python
{
    "spatial_mode": "emanation" | "wave" | "blend" | "wash",
    "spatial_origin": {"x": 0.0, "y": 0.0, "z": 0.0},
    "spatial_direction": {"x": 1.0, "y": 0.0, "z": 0.0},
    "spatial_width": 0.35,
    "spatial_blend": "linear" | "smoothstep" | "radial",
    "spatial_extent": {
        "min": {"x": -1.0, "y": -1.0, "z": -1.0},
        "max": {"x": 1.0, "y": 1.0, "z": 1.0},
    },
    "spatial_delay_ms": 120,
}
```

Allow `spatial_direction` shorthand aliases too:

- `x+`, `x-`
- `y+`, `y-`
- `z+`, `z-`

### Compatibility defaults

Legacy cues with no explicit spatial metadata must still run deterministically.

Recommended defaults:

- `scroll` and `wave` default to an `x+` wave
- `gradient` defaults to depth blending on `z`
- `breathe` and `solid` default to a whole-room wash
- `pulse` defaults to centered radial emanation

## Runtime Wiring

The spatial engine must be wired into the real playback paths that matter:

- compiled show playback
- local playlist preview and show playback
- streaming/show consumer path
- reactive/live path if it shares the same adapter seam cleanly

Preferred architecture:

- one shared spatial send path beneath show/live orchestration
- one continuous spatial engine used by all supported runtime modes
- one adapter-facing representation of already-spatialized output

Acceptable adapter contract options:

- per-device or per-section intents/colors already spatialized before adapter send
- or a continuous spatial scene abstraction that the adapter resolves directly from coordinates

Unacceptable end state:

- `SpatialMapper` remains optional and effectively unused
- show playback uses one spatial system while reactive mode uses another unrelated one
- section placement exists in config/editor but is ignored in the runtime path

## GUI and Editor Follow-Through

The current spatial editor is already good enough to support this phase, but its semantics must align with runtime meaning.

Required follow-through:

- relabel views and axis text to match canonical `x/y/z`
- keep `Room`, `XY`, `XZ`, and `YZ` views
- keep drag guides for the selected axis
- keep numeric coordinate editing

Do not broaden this plan into full 3D camera controls. The goal is semantic consistency, not a new rendering system.

## Detailed Execution Plan

### Step 0: Axis-model reconciliation and regression guardrails

**Goal**

Resolve the repo-wide axis mismatch before runtime spatial logic expands.

**Deliverables**

- inventory of current axis assumptions in:
  - runtime models
  - config parsing
  - debug output
  - GUI labels and projections
- explicit canonical-axis documentation adopted in code comments and validators
- regression checklist for:
  - local preview
  - `play`
  - `compile-and-play`
  - streaming consumer playback
  - reactive/live mode

**Completion criteria**

- there is one accepted meaning for `x`, `y`, `z`
- no remaining ambiguity between old runtime docs and GUI/editor behavior

### Step 1: Runtime placement model upgrade

**Goal**

Teach runtime config loading and adapter construction about section-aware placement.

**Deliverables**

- runtime placement models that can represent:
  - device-level placement
  - per-section placement
  - orientation and other rendering metadata
- config parsing that loads section coordinates from `devices.yaml`
- authority rules enforced in one place

**Implementation details**

- bulbs and single-node devices keep one spatial node
- strips may expand into multiple runtime-addressable spatial nodes
- section positions override parent placement when present
- parent device placement remains available as fallback/debug metadata

**Completion criteria**

- runtime can represent and carry section placement all the way to send time
- legacy configs still load without migration

### Step 2: Continuous spatial engine

**Goal**

Replace bucketed routing as the primary effect sequencer.

**Deliverables**

- continuous ordering helpers for:
  - directional waves
  - point-origin emanation
  - bounded blends / extents
- simple, deterministic falloff math
- clear validation/defaulting of spatial parameters

**Implementation details**

- prioritize deterministic coordinate ordering over complex interpolation
- preserve room-wide wash behavior for cues that do not need directional sequencing
- use `z` as a true depth axis, not a GUI-only editor field

**Completion criteria**

- runtime can produce visibly different behavior for `x`, `y`, and `z`
- a top-down wave is materially different from a front-back wave

### Step 3: Cue metadata normalization

**Goal**

Make authored and runtime spatial controls explicit and stable.

**Deliverables**

- metadata validation helpers
- direction alias normalization
- compatibility mapping from old cue params and render modes
- stable defaults for legacy cues

**Completion criteria**

- spatial metadata is predictable and documented
- legacy cues still run without manual edits

### Step 4: Shared runtime integration

**Goal**

Use the same spatial runtime path across the real playback surfaces.

**Deliverables**

- show playback integration
- local playlist preview/show integration
- streaming consumer integration
- reactive/live integration if the seam is shared cleanly

**Implementation details**

- prefer one shared spatial send path
- avoid duplicating spatial logic in each session/playback mode
- keep spatial mode opt-in until regression confidence is high

**Completion criteria**

- spatial mode affects real runtime output in the supported playback paths
- non-spatial mode remains behaviorally unchanged

### Step 5: Validation, docs, and rollout

**Goal**

Make the new runtime model testable, debuggable, and understandable.

**Deliverables**

- config/debug output that reflects canonical axes and section authority
- updated docs referencing this plan plus historical context
- example configs and acceptance scenarios

**Completion criteria**

- a user can understand how section placement, depth, and directional effects work
- debugging no longer depends on reading older 3x3-only planning docs

## Files Likely to Change

| File | Purpose |
|------|---------|
| `src/dreamsync/output/auto_detect.py` | parse runtime section placement and canonical coordinates |
| `src/dreamsync/spatial/models.py` | canonical spatial node/placement types |
| `src/dreamsync/spatial/mapper.py` | evolve or replace dormant coarse mapping path |
| `src/dreamsync/spatial/grid.py` | legacy/fallback grid support only if still needed |
| `src/dreamsync/output/govee_lan.py` | shared spatial send path and adapter integration |
| `src/dreamsync/show/runtime.py` | show-playback spatial integration |
| `src/dreamsync/live.py` | reactive/live spatial integration |
| `src/dreamsync/show_playback_consumer.py` | streaming/show consumer integration |
| `src/dreamsync/gui/widgets/spatial_canvas.py` | axis/view semantic cleanup only |
| `src/dreamsync/gui/main_window.py` | axis/view semantic cleanup only |
| `dev/tests/test_spatial_models.py` | canonical coordinate and section parsing coverage |
| `dev/tests/test_spatial_runtime.py` | runtime routing coverage |
| `dev/tests/test_gui_spatial_projection.py` | GUI axis/view consistency coverage |

## Test Plan

Automated tests should cover:

- config parsing of canonical `x/y/z` meaning
- section-aware runtime placement loading
- authority rules where sections override parent device placement
- legacy configs with no spatial fields remaining valid
- spatial cue metadata validation and defaulting
- continuous ordering behavior for `x`, `y`, and `z` directional waves
- point-origin emanation choosing nearer devices/sections first
- color blending across multiple positioned devices/sections
- show playback using the spatial runtime path when spatial mode is enabled
- local preview/show playback using the spatial runtime path
- reactive/live path using the same spatial semantics if included in v1
- runtime unchanged when spatial mode is off
- GUI labels/views matching the canonical axis model

Manual acceptance scenarios should include:

- a sectioned strip where different sections clearly sequence in physical order
- a top-down wave that differs visibly from a front-back wave
- a point-origin effect where nearby devices light first
- a mixed room setup with bulbs plus strips
- a legacy non-spatial config still behaving exactly as before with spatial mode off

## Risks and Mitigations

| Risk | Why it matters | Mitigation |
|------|----------------|------------|
| Axis semantics remain split across docs and code | Runtime behavior will stay confusing and tests will be flaky | Reconcile axis meanings before extending runtime spatial logic |
| Section placement is saved but not consumed | GUI edits appear to work while playback ignores them | Make runtime placement ingestion a first-class early step |
| Continuous spatial logic forks by runtime mode | Reactive and show behavior diverge | Use one shared spatial engine and adapter seam |
| Legacy cues regress | Existing shows/local playback lose predictability | Keep deterministic defaults and explicit compatibility mapping |
| 3x3 fallback lingers as the default path | New runtime never reaches true coordinate-driven behavior | Treat grid bucketing as compatibility-only, not the target design |

## Manual Validation Checklist

1. Load a real room config with sectioned strips and confirm section coordinates appear in debug output.
2. Run a directional `x` wave and confirm left-right ordering across bulbs and strip sections.
3. Run a directional `y` wave and confirm top-down behavior differs from the `x` wave.
4. Run a directional `z` wave and confirm front-back sequencing is visibly different from top-down.
5. Run a point-origin effect near one side of the room and confirm nearby nodes light first.
6. Run a legacy non-spatial config with spatial mode off and confirm behavior is unchanged.

## Summary

The previous spatial plan established a useful foundation, but it stopped short of making placement materially affect the real runtime. The GUI/editor has now moved far enough that section-aware and depth-aware placement can be authored directly, which means the runtime must catch up.

The next implementation phase should therefore:

- standardize canonical `x/y/z` semantics
- consume section placement in the runtime path
- replace coarse bucket-first behavior with continuous coordinate-driven sequencing
- introduce explicit spatial cue metadata with legacy defaults
- wire the shared spatial engine into real playback flows

That is the work this plan is intended to drive.
