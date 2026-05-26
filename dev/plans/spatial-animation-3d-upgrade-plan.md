# 3-D Spatial Animation Upgrade Plan

## Goal

Upgrade DreamSync so animations actually use the newer 3-D spatial mapping as the primary effect model, enabling room-native behaviors such as:

- ripple left-to-right across the room
- ripple from center
- wave top-to-bottom
- wave front-to-back
- flash top lights only
- region-targeted washes and blends

## Desired End State

The runtime should move from:

```text
legacy strip animation
-> optional spatial modulation
-> output
```

to:

```text
cue / reactive trigger
-> explicit spatial effect metadata
-> continuous 3-D spatial evaluation
-> per-device / per-section sampling
-> optional local strip texture
-> output
```

The primary requirement is that room-level motion be authored and resolved in `x/y/z`, not inferred after the fact from a 1-D strip animation.

## Core Principles

- `x` means left/right
- `y` means vertical height
- `z` means front/back depth
- section placement overrides parent placement when present
- one shared spatial runtime path should serve show and live playback
- legacy non-spatial behavior must remain unchanged when spatial mode is off
- old cues should still run deterministically through compatibility defaults

## Scope

### In scope

- spatial effect metadata normalization
- continuous 3-D directional and origin-based motion
- region-limited flashes and washes
- shared runtime integration
- section-aware sampling
- compatibility mapping from old cue/effect vocabulary
- test coverage for `x`, `y`, and `z` behaviors

### Out of scope

- GUI redesign beyond semantic cleanup
- arbitrary 3-D camera controls
- non-spatial compiler work unrelated to effect metadata
- automatic room discovery
- photoreal preview

## Implementation Strategy

### Phase 1: Canonical semantic cleanup

#### Objective

Remove ambiguity around axes and old grid-era terms before expanding behavior.

#### Files

- [src/dreamsync/spatial/models.py](/C:/Users/brian/dreamsync/src/dreamsync/spatial/models.py)
- [src/dreamsync/spatial/mapper.py](/C:/Users/brian/dreamsync/src/dreamsync/spatial/mapper.py)
- [src/dreamsync/gui/widgets/spatial_canvas.py](/C:/Users/brian/dreamsync/src/dreamsync/gui/widgets/spatial_canvas.py)
- [src/dreamsync/gui/main_window.py](/C:/Users/brian/dreamsync/src/dreamsync/gui/main_window.py)
- related tests and docs

#### Work

- standardize code comments and debug semantics on:
  - `x = left/right`
  - `y = vertical`
  - `z = front/back`
- audit any remaining places where old grid logic uses `y` as depth
- keep old compatibility shims only where needed, but stop using them as the basis for new effects

#### Completion criteria

- all new spatial behavior uses canonical `x/y/z`
- no new runtime feature depends on grid-era axis semantics

### Phase 2: Introduce a stable spatial effect metadata layer

#### Objective

Create one explicit metadata contract that both show cues and reactive effects can use.

#### Proposed metadata

```python
{
    "spatial_mode": "wave" | "emanation" | "blend" | "wash",
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

#### Files

- [src/dreamsync/spatial/mapper.py](/C:/Users/brian/dreamsync/src/dreamsync/spatial/mapper.py)
- [src/dreamsync/show/runtime.py](/C:/Users/brian/dreamsync/src/dreamsync/show/runtime.py)
- [src/dreamsync/live.py](/C:/Users/brian/dreamsync/src/dreamsync/live.py)
- cue/profile generation code as needed

#### Work

- formalize validation/defaulting for all spatial metadata
- keep shorthand aliases:
  - `x+`, `x-`
  - `y+`, `y-`
  - `z+`, `z-`
- convert legacy params like `spatial_axis` and `spatial_focus` into compatibility inputs, not primary authored controls
- ensure show playback and live playback both feed this metadata shape into the shared adapter seam

#### Completion criteria

- room-scale effects can be expressed entirely through explicit spatial metadata
- old cues without explicit metadata still resolve predictably

### Phase 3: Define first-class room effect presets

#### Objective

Provide stable effect semantics that map cleanly onto the metadata layer.

#### Initial presets

1. `ripple_left_to_right`

- `spatial_mode = "wave"`
- `spatial_direction = "x+"`

2. `ripple_right_to_left`

- `spatial_mode = "wave"`
- `spatial_direction = "x-"`

3. `ripple_from_center`

- `spatial_mode = "emanation"`
- `spatial_origin = {x: 0.0, y: 0.0, z: 0.0}`

4. `wave_top_to_bottom`

- `spatial_mode = "wave"`
- `spatial_direction = "y-"`

5. `wave_bottom_to_top`

- `spatial_mode = "wave"`
- `spatial_direction = "y+"`

6. `wave_front_to_back`

- `spatial_mode = "wave"`
- `spatial_direction = "z+"`

7. `wave_back_to_front`

- `spatial_mode = "wave"`
- `spatial_direction = "z-"`

8. `flash_top_only`

- `spatial_mode = "wash"`
- `spatial_extent` limited to high `y`

9. `flash_floor_only`

- `spatial_mode = "wash"`
- `spatial_extent` limited to low `y`

10. `blend_left_to_right`

- `spatial_mode = "blend"`
- `spatial_direction = "x+"`

#### Files

- reactive effect/controller code
- show cue generation / profile / compiler code if desired
- docs

#### Work

- decide where preset names live
- implement preset-to-metadata translation in one place
- keep the preset layer thin and declarative

#### Completion criteria

- users and runtime code can refer to stable room effects without re-specifying low-level metadata every time

### Phase 4: Rework reactive ripple/flash authoring

#### Objective

Make reactive effects emit explicit spatial behavior rather than legacy-only labels.

#### Files

- [src/dreamsync/basic_controller.py](/C:/Users/brian/dreamsync/src/dreamsync/basic_controller.py)
- [src/dreamsync/director.py](/C:/Users/brian/dreamsync/src/dreamsync/director.py)
- [src/dreamsync/live.py](/C:/Users/brian/dreamsync/src/dreamsync/live.py)

#### Work

- decide whether `EffectMode.RIPPLE` remains as a compatibility label or is retired
- if kept:
  - map it immediately into spatial preset metadata before rendering
- if retired:
  - replace it with spatial preset emission at the controller layer
- extend reactive flash behavior so region-constrained flashes are possible

#### Recommended approach

Keep `EffectMode.RIPPLE` only as an internal compatibility alias that resolves quickly to explicit spatial metadata.

#### Completion criteria

- reactive ripple no longer depends on a legacy-only label with no first-class spatial runtime meaning

### Phase 5: Promote continuous spatial evaluation to primary room choreography

#### Objective

Make the continuous spatial engine define room motion first, with `SegmentRenderer` acting as a local texture layer.

#### Files

- [src/dreamsync/output/govee_lan.py](/C:/Users/brian/dreamsync/src/dreamsync/output/govee_lan.py)
- [src/dreamsync/spatial/mapper.py](/C:/Users/brian/dreamsync/src/dreamsync/spatial/mapper.py)
- [src/dreamsync/render.py](/C:/Users/brian/dreamsync/src/dreamsync/render.py)

#### Work

- preserve the current shared adapter seam in `send_continuous_spatial_frame()`
- refine the contract so room effect timing and activation come from spatial evaluation first
- keep local strip rendering for texture and orientation-sensitive per-device visuals
- avoid making `SegmentRenderer` responsible for room-level directionality

#### Design rule

`SegmentRenderer` should answer:

- what does this device’s local pattern look like?

The spatial engine should answer:

- when, where, and how strongly should this part of the room be active?

#### Completion criteria

- left/right, top/down, and front/back sequencing are visibly driven by placement and spatial metadata rather than by local strip index alone

### Phase 6: Deepen section-aware behavior

#### Objective

Exploit the fact that section coordinates are already runtime-visible.

#### Files

- [src/dreamsync/output/govee_lan.py](/C:/Users/brian/dreamsync/src/dreamsync/output/govee_lan.py)
- [src/dreamsync/spatial/models.py](/C:/Users/brian/dreamsync/src/dreamsync/spatial/models.py)
- [src/dreamsync/output/auto_detect.py](/C:/Users/brian/dreamsync/src/dreamsync/output/auto_detect.py)

#### Work

- preserve section override authority
- ensure section positions are always used when present
- consider debug output that makes section ordering visible during spatial playback
- test mixed device setups:
  - bulbs
  - single-node strips
  - multi-section strips

#### Completion criteria

- a sectioned strip sequences according to real section coordinates
- parent placement is fallback only when section placement is absent

### Phase 7: Shared path validation across runtime surfaces

#### Objective

Use one spatial behavior model across all meaningful playback paths.

#### Paths

- show playback
- local preview / playlist preview if applicable
- streaming/show consumer path
- reactive/live path

#### Files

- [src/dreamsync/show/runtime.py](/C:/Users/brian/dreamsync/src/dreamsync/show/runtime.py)
- [src/dreamsync/show_playback_consumer.py](/C:/Users/brian/dreamsync/src/dreamsync/show_playback_consumer.py)
- [src/dreamsync/live.py](/C:/Users/brian/dreamsync/src/dreamsync/live.py)
- [src/dreamsync/output/govee_lan.py](/C:/Users/brian/dreamsync/src/dreamsync/output/govee_lan.py)

#### Work

- verify that each path passes spatial metadata through the shared adapter seam
- eliminate duplicated special-case spatial logic where possible
- keep spatial mode opt-in if needed during rollout

#### Completion criteria

- room-scale spatial behavior is consistent across supported playback surfaces

## Suggested File-Level Changes

### `src/dreamsync/spatial/mapper.py`

- keep `resolve_spatial_spec()` as the central metadata normalization point
- expand compatibility mapping from old params and render modes
- reduce reliance on old grid-scene logic for new effect behavior
- make `y` and `z` semantics unquestionably canonical

### `src/dreamsync/output/govee_lan.py`

- keep `send_continuous_spatial_frame()` as the shared adapter seam
- strengthen the distinction between:
  - room activation from spatial evaluation
  - local strip texture from renderer output
- maintain section-aware sampling path

### `src/dreamsync/render.py`

- keep local strip rendering
- do not add new room-scale spatial semantics here unless they are purely local texture helpers
- avoid baking room direction/origin logic into segment-index-only effects

### `src/dreamsync/basic_controller.py`

- convert reactive ripple/flash authoring to explicit spatial presets or metadata
- keep legacy labels only as compatibility shims

### `src/dreamsync/show/runtime.py`

- continue forwarding cue params into runtime send path
- ensure explicit spatial metadata is preserved and normalized for all relevant cue types

## Testing Plan

### Automated tests

Add or extend tests for:

- `x+` and `x-` room wave ordering
- `y+` and `y-` room wave ordering
- `z+` and `z-` room wave ordering
- center-origin emanation behavior
- region-limited washes using `spatial_extent`
- sectioned strip ordering using section positions
- legacy no-spatial playback staying unchanged when spatial mode is off
- show and live paths using the same spatial seam
- compatibility mapping from old cue params to new metadata

### Existing test files likely to expand

- [dev/tests/test_spatial_runtime.py](/C:/Users/brian/dreamsync/dev/tests/test_spatial_runtime.py)
- [dev/tests/test_spatial_mapper.py](/C:/Users/brian/dreamsync/dev/tests/test_spatial_mapper.py)
- [dev/tests/test_spatial_models.py](/C:/Users/brian/dreamsync/dev/tests/test_spatial_models.py)
- GUI consistency tests if axis labels change

### Manual validation scenarios

1. Run a left-to-right room ripple and confirm bulbs and strip sections sequence by `x`.
2. Run a top-to-bottom wave and confirm high `y` and low `y` devices differ visibly.
3. Run a front-to-back wave and confirm the result is materially different from top-to-bottom.
4. Run a center-origin ripple and confirm nearby nodes light before farther nodes.
5. Run a top-only flash and confirm low devices remain unaffected.
6. Run a legacy non-spatial configuration with spatial mode off and confirm behavior is unchanged.

## Risks

### Axis regression

If old `depth` semantics remain mixed with canonical `z`, top/down and front/back effects will be confusing and inconsistent.

### Double-defining motion

If both `SegmentRenderer` and the spatial engine try to define room-scale directionality, behavior will become hard to reason about.

### Legacy show regression

If compatibility defaults are not carefully preserved, older cues may change appearance unexpectedly.

### Uneven runtime behavior between playback modes

If show and live paths diverge, users will not trust what the spatial editor is showing them.

## Recommended Rollout Order

1. Canonical axis cleanup
2. Spatial metadata normalization
3. Reactive ripple compatibility mapping
4. First-class room effect presets
5. Shared-path continuous spatial behavior refinement
6. Section-heavy validation and regression testing

## Success Criteria

This upgrade is successful when:

- left/right, top/down, and front/back effects are all visibly distinct
- center-origin effects are first-class and reliable
- sectioned strips sequence according to real section coordinates
- region-constrained effects like top-only flash are straightforward to express
- show and live playback use the same spatial semantics
- old non-spatial behavior remains stable when spatial mode is off

## Summary

DreamSync already has the hard foundation work in place:

- canonical 3-D placement
- per-section strip coordinates
- continuous spatial sampling
- shared adapter integration

The missing step is to promote 3-D spatial evaluation from a downstream modifier into the primary definition of room-scale animation. This plan focuses on that shift while keeping legacy compatibility and preserving the existing local strip rendering strengths.
