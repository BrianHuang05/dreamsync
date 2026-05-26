# Spatial Effecting Layer Engine Plan

## Status

This document is the source of truth for the next spatial-engine upgrade.

It builds on, but does not replace:

- [C:\Users\brian\dreamsync\dev\plans\spatial-animation-3d-upgrade-plan.md](C:\Users\brian\dreamsync\dev\plans\spatial-animation-3d-upgrade-plan.md)
- [C:\Users\brian\dreamsync\dev\plans\continuous-spatial-runtime-followup.md](C:\Users\brian\dreamsync\dev\plans\continuous-spatial-runtime-followup.md)

The main change in this plan is conceptual:

- stop treating spatial behavior as mostly preset metadata plus per-device sampling
- introduce an explicit `effecting layer` that exists in 3-D room space
- let the layer move through the room and activate fixtures/sections as it intersects them

## Goal

Upgrade DreamSync so spatial effects are resolved through a render-layer model:

- an effect is authored as a spatial category plus effect parameters
- the engine builds an `effecting layer` in 3-D space
- that layer may be stationary or moving
- each fixture or strip section is sampled against the layer over time
- when the layer reaches a node, the node receives the intended effect payload:
  - color switch
  - brightness change
  - intensity ramp
  - effect-mode activation
  - palette blend or transition

This should make room-scale choreography feel like a true 3-D effect engine rather than a cue sending static per-device parameters.

## Required Mental Model

The spatial engine should answer two separate questions:

1. Where is the active effect layer in room space right now?
2. What happens to a fixture when that layer hits it?

That means each spatialized cue/effect should be resolved as:

```text
cue / live trigger
-> effect layer descriptor
-> layer position/state over time
-> fixture/section intersection tests
-> per-node activation result
-> output adapter
```

## Effect Categories

### 1. `static`

`static` means non-moving room effects.

Examples:

- `flash`
- `flash_low_only`
- `flash_high_only`
- `pulse`
- `breathe`
- whole-room wash
- top-only wash
- floor-only wash

Behavior:

- the effect layer is stationary
- it may still vary in intensity over time
- it does not translate through the room
- nodes inside the layer extent respond continuously
- nodes outside the extent do not

### 2. `slice`

`slice` means a moving plane or slab through the room.

Examples:

- `ripple_left_to_right`
- `ripple_right_to_left`
- `wave_top_to_bottom`
- `wave_bottom_to_top`
- `wave_front_to_back`
- `wave_back_to_front`

Behavior:

- the effect layer is directional
- it moves through the room along a single axis or vector
- it has thickness / width
- nodes become active when the moving layer intersects their coordinates
- the layer can leave a trail, trigger a one-shot response, or drive a timed transition

### 3. `expand`

`expand` means radial or origin-based propagation.

Examples:

- `ripple_from_center`
- `ripple_from_left_anchor`
- `burst_from_front`
- future contract/implode variants if added later

Behavior:

- the effect layer is defined by distance from an origin
- its active boundary expands outward over time
- nodes activate when their radius/distance falls within the layer band
- this is the canonical model for center-out and anchor-out effects

## Desired End State

The runtime should move from:

```text
cue params
-> preset normalization
-> per-device spatial weighting
-> output
```

to:

```text
cue params / live params
-> effect layer descriptor
-> layer simulation in x/y/z
-> node intersection sampling
-> per-node effect activation payload
-> output
```

## Core Principles

- `x` means left/right
- `y` means height
- `z` means front/back depth
- section placement is authoritative over parent strip placement
- one shared effect-layer runtime should serve show playback, preview, and live paths
- old behavior should remain available behind compatibility defaults during rollout
- static, slice, and expand are category semantics, not just user-facing labels
- local strip rendering remains useful, but it should be downstream of room-scale layer activation

## Non-Goals

- no photoreal room renderer
- no arbitrary 3-D camera tooling
- no redesign of the existing show editor beyond whatever metadata exposure is needed
- no automatic room discovery
- no requirement to implement full physically accurate light propagation

## Spatial Layer Data Model

Introduce a first-class descriptor for a room effect layer.

Illustrative shape:

```python
{
    "layer_category": "static" | "slice" | "expand",
    "effect_mode": "solid" | "pulse" | "breathe" | "wave" | "gradient" | ...,
    "trigger_mode": "continuous" | "oneshot" | "latched",
    "origin": {"x": 0.0, "y": 0.0, "z": 0.0},
    "direction": {"x": 1.0, "y": 0.0, "z": 0.0},
    "extent": {
        "min": {"x": -1.0, "y": -1.0, "z": -1.0},
        "max": {"x": 1.0, "y": 1.0, "z": 1.0},
    },
    "thickness": 0.25,
    "radius": 0.0,
    "speed_units_per_second": 1.0,
    "falloff": "hard" | "linear" | "smoothstep" | "radial",
    "palette": ["#ffffff", "#88ccff"],
    "color_bias": "#ffaa44",
    "intensity_scale": 1.0,
    "time_offset_s": 0.0,
    "duration_s": 0.0,
}
```

This does not need to be the exact final schema, but the runtime should converge on this kind of explicit layer contract.

## Effect Payload Model

The layer intersection should not directly “paint a final color” only.

Instead, it should yield an activation payload that can drive:

- color replacement
- palette choice
- intensity multiplier
- intensity envelope start/end
- render-mode swap or local effect trigger
- per-node delay/hold state

That allows one spatial engine to drive both:

- simple flashes
- more complex “carry this effect as the layer passes” behavior

## Proposed Architecture

### Phase 1: Canonical effect category layer

#### Objective

Create one normalized category model for `static`, `slice`, and `expand`.

#### Work

- define category enums / literals in the spatial model layer
- map existing presets into exactly one category
- normalize legacy names like:
  - `flash_top_only`
  - `blend_left_to_right`
  - `ripple_from_center`
- keep compatibility aliases, but stop treating preset names as the primary runtime abstraction

#### Likely files

- [C:\Users\brian\dreamsync\src\dreamsync\spatial\models.py](C:\Users\brian\dreamsync\src\dreamsync\spatial\models.py)
- [C:\Users\brian\dreamsync\src\dreamsync\spatial\mapper.py](C:\Users\brian\dreamsync\src\dreamsync\spatial\mapper.py)
- [C:\Users\brian\dreamsync\src\dreamsync\show\runtime.py](C:\Users\brian\dreamsync\src\dreamsync\show\runtime.py)
- [C:\Users\brian\dreamsync\src\dreamsync\live.py](C:\Users\brian\dreamsync\src\dreamsync\live.py)

#### Completion criteria

- every spatial preset resolves to a category-backed layer descriptor
- category semantics are testable independently of preset names

### Phase 2: First-class effecting layer descriptor

#### Objective

Replace preset-only spatial normalization with a richer effect-layer contract.

#### Work

- define a `SpatialEffectLayer` or equivalent model
- support category-specific fields:
  - `static`: extent, intensity envelope
  - `slice`: direction, thickness, speed
  - `expand`: origin, radius band, expansion speed
- formalize defaults and validation
- keep old params as compatibility inputs only

#### Completion criteria

- runtime can resolve an authored cue into a fully specified effect layer
- layers are serializable enough for show playback, preview, and tests

### Phase 3: Layer simulation over time

#### Objective

Make the effect layer evolve over time in room coordinates.

#### Work

- add functions that evaluate a layer state at time `t`
- for `static`:
  - treat the layer as spatially fixed
  - apply only envelope/time modulation
- for `slice`:
  - compute the moving plane/slab center from direction and speed
  - determine active band around that position
- for `expand`:
  - compute the current radius from origin and expansion speed
  - determine the active band around that radius

#### Completion criteria

- the runtime can answer “where is the active layer right now?” for all three categories

### Phase 4: Node intersection sampling

#### Objective

Determine whether each device/section is affected by the current layer state.

#### Work

- add geometry/intersection helpers for:
  - point-inside-extent tests
  - point-to-slice-band distance
  - point-to-origin radius comparison
- return a continuous activation value, not just boolean hit/miss
- let falloff shape determine activation strength near layer boundaries
- preserve section-level placement authority

#### Completion criteria

- each node gets a stable activation strength from the layer at any time `t`
- left/right, top/down, and front/back effects become visibly distinct through geometry, not naming only

### Phase 5: Activation payload application

#### Objective

Map intersection results into actual effect output per node.

#### Work

- define how activation affects:
  - brightness
  - intensity multiplier
  - color/palette selection
  - local effect mode
  - transient/hold behavior
- support both:
  - “instant hit changes color”
  - “while inside the layer, this node breathes/pulses”
- keep deterministic blending rules when multiple layers are active

#### Completion criteria

- an effect layer can “carry” a local effect through the room, not just scale a static color

### Phase 6: Multi-layer composition

#### Objective

Support multiple simultaneous spatial effect layers cleanly.

#### Work

- define layer priority / blend rules
- separate:
  - base room wash
  - instrument/EQ layers
  - cue-authored explicit layers
- standardize composition order
- avoid hidden overwrite behavior

#### Completion criteria

- multiple layers can coexist predictably
- a bass slice can pass through a static ambient wash without undefined results

### Phase 7: Shared runtime integration

#### Objective

Use the same effect-layer engine across all important playback surfaces.

#### Paths

- saved show playback
- GUI preview / dummy playback
- compile-and-play / local playback
- live / reactive path

#### Work

- ensure all these paths feed the same layer descriptor contract
- prevent forked spatial logic by playback mode
- keep backward compatibility where spatial mode is off

#### Completion criteria

- preview, show playback, and live runtime interpret spatial layers the same way

### Phase 8: Authoring and editor follow-through

#### Objective

Expose the new model without overwhelming the editor.

#### Work

- retain high-level presets for quick authoring
- add category-aware controls progressively:
  - `static`: extent, region, envelope
  - `slice`: direction, thickness, speed
  - `expand`: origin, radius, speed
- avoid making users type opaque raw params for common cases
- keep manual spatial arrangement mandatory, not autogenerated

#### Completion criteria

- users can author spatial layer behavior meaningfully in the GUI
- presets remain convenient but no longer hide the true runtime model

### Phase 9: Optional baked frame export and playback cache

#### Objective

Support a second, optional representation of a show as pre-rendered frame data without replacing the editable show format.

#### Primary rule

The canonical saved show should remain an editable cue/layer document.

That means:

- `.show.json` remains the source of truth for authored shows
- effect triggers, cue timing, palettes, and spatial layer descriptors remain editable
- room simulation normally happens at playback time

The baked representation should be optional and derived.

#### Why add a baked path

An optional baked path would help with:

- exact preview caching
- deterministic playback comparisons
- export for lower-power runtime targets
- debugging frame-by-frame spatial behavior
- future interchange/export workflows

#### Proposed model

Keep two tiers:

1. editable authored show
2. optional baked playback artifact

Illustrative examples:

- `song.show.json`
- `song.show.frames.json`
- or a binary cache format later if JSON is too large

#### Work

- define a baked frame schema or cache format
- add a compiler/export path that evaluates the effect layers over time into frame samples
- define playback rules for:
  - live-simulated authored show
  - baked frame playback
- preserve linkage between a baked artifact and the authored show/config/layout that produced it
- add invalidation rules when:
  - cue timing changes
  - palettes change
  - spatial layout changes
  - runtime layer semantics change

#### Non-goal

Do not switch the main saved-show workflow to baked-only frame data.

That would make shows:

- harder to edit
- larger on disk
- more brittle when room layouts change

#### Completion criteria

- authored shows remain editable as cue/layer data
- users can optionally export or cache a frame-by-frame playback artifact
- the runtime can choose between simulated and baked playback cleanly

## Mapping Existing Effects Into Categories

### Static

- `flash`
- `flash_top_only`
- `flash_floor_only`
- `pulse`
- `breathe`
- `solid`
- bounded wash / region wash

### Slice

- `ripple_left_to_right`
- `ripple_right_to_left`
- `wave_top_to_bottom`
- `wave_bottom_to_top`
- `wave_front_to_back`
- `wave_back_to_front`
- future diagonal sweeps

### Expand

- `ripple_from_center`
- future center bursts
- future anchor bursts

## Recommended Runtime Separation of Concerns

### Spatial engine

Responsible for:

- layer normalization
- layer simulation through x/y/z
- per-node intersection tests
- activation strength calculation

### Local renderer

Responsible for:

- what the local effect looks like on a device/section once activated
- strip texture/orientation details
- local palette interpolation

### Adapter/output path

Responsible for:

- sending the already spatialized result to bulbs/strips/sections
- preserving deterministic ordering and timing

## Files Likely to Change

| File | Purpose |
|------|---------|
| `src/dreamsync/spatial/models.py` | new effect-layer and activation models |
| `src/dreamsync/spatial/mapper.py` | layer normalization, category mapping, simulation helpers |
| `src/dreamsync/output/govee_lan.py` | application of layer activation to real device/section output |
| `src/dreamsync/show/runtime.py` | show playback integration |
| `src/dreamsync/live.py` | live/reactive integration |
| `src/dreamsync/render.py` | local effect application details if needed |
| `src/dreamsync/compiler/assemble.py` | emit richer spatial layer descriptors from cues/routes |
| `src/dreamsync/gui/main_window.py` | later editor exposure for category-aware parameters |
| `dev/tests/test_spatial_mapper.py` | layer normalization and geometry |
| `dev/tests/test_spatial_runtime.py` | runtime effect activation |
| `dev/tests/test_show_runtime.py` | show playback integration |
| `dev/tests/test_live_bpm.py` or related live tests | live spatial behavior coverage |

## Test Plan

### Automated tests

Add or extend tests for:

- category mapping:
  - `flash_top_only -> static`
  - `ripple_left_to_right -> slice`
  - `ripple_from_center -> expand`
- `static` extent behavior
- `slice` left-to-right activation ordering by `x`
- `slice` top-to-bottom activation ordering by `y`
- `slice` front-to-back activation ordering by `z`
- `expand` center-origin radial propagation
- sectioned strip intersection using section coordinates
- activation falloff near layer boundaries
- composition of multiple simultaneous layers
- identical semantics across preview/show/live paths where applicable
- compatibility behavior for legacy cues with spatial mode off

### Manual validation

1. Author a `blend_left_to_right` or equivalent slice-style cue and confirm nodes activate in `x` order.
2. Author a top-only static flash and confirm only high-`y` nodes react.
3. Author a center-origin expand cue and confirm near-center nodes fire before outer ones.
4. Test a mixed setup with bulbs and sectioned strips.
5. Confirm preview and saved-show playback agree on the same spatial cue.

## Risks

### Too much logic remains preset-specific

If preset names still drive the runtime directly, the engine will remain hard to extend.

### Local renderer and spatial engine both try to own motion

If both layers define motion independently, output will become confusing and inconsistent.

### Composition becomes non-deterministic

If multiple layers overwrite each other without stable rules, users will not trust the system.

### GUI exposure becomes too param-heavy

If the engine improves but authoring becomes unreadable, users will avoid the feature.

## Recommended Rollout Order

1. category normalization
2. effect-layer data model
3. layer simulation for `static`, `slice`, and `expand`
4. node intersection sampling
5. activation payload application
6. multi-layer composition
7. shared runtime integration
8. GUI/editor exposure
9. optional baked frame export / playback cache

## Success Criteria

This work is successful when:

- room-scale effects are defined by moving or stationary 3-D layers
- `static`, `slice`, and `expand` are real runtime semantics, not just labels
- a slice effect clearly activates fixtures in spatial order
- an expand effect clearly propagates from an origin
- a static extent effect clearly targets a region
- the same model works for preview, saved shows, and live playback
- the engine can carry local effect behavior through room space, not just apply a static color blend
- editable shows remain cue/layer based, with optional baked frame export available when needed

## Summary

The current spatial system is close, but it still thinks in terms of cue metadata and per-node weighting more than explicit room-traversing effect geometry.

The next step is to make the room effect itself first-class:

- define an `effecting layer`
- simulate that layer through 3-D space
- sample every fixture/section against it
- apply the intended visual behavior when the layer hits

That shift should make DreamSync’s spatial engine much more extensible, much more legible, and much closer to the kind of choreography the editor is already trying to author.
