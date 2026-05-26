# 3-D Spatial Animation Structure Analysis

## Goal

Document where DreamSync currently creates animation behavior, how that behavior flows through the runtime, and what prevents the newer 3-D spatial model from being fully used for effects such as:

- left-to-right ripple
- center-origin ripple
- top-to-bottom wave
- top-lights-only flash

## Executive Summary

The codebase already has the critical 3-D placement foundation:

- canonical `x/y/z` device placement
- per-section strip placement
- a continuous spatial sampling path
- shared adapter wiring used by both show and live playback paths

However, the system still creates most visible animation behavior using legacy 1-D strip renderers first, then applies spatial modulation afterward. That means the runtime can scale or recolor effects according to 3-D placement, but it does not yet define room-scale motion from 3-D coordinates as the primary effect source.

In short:

- strip-local animation exists
- 3-D spatial placement exists
- continuous spatial sampling exists
- room-scale 3-D animation authoring is still incomplete

## Current Structure

### 1. Legacy animation creation happens in `render.py`

Primary file:

- [src/dreamsync/render.py](/C:/Users/brian/dreamsync/src/dreamsync/render.py)

The `SegmentRenderer` is still the main place where visible strip motion is generated.

Important entry points:

- `SegmentRenderer.render()` at [src/dreamsync/render.py:65](/C:/Users/brian/dreamsync/src/dreamsync/render.py:65)
- `_render_scroll()` at [src/dreamsync/render.py:131](/C:/Users/brian/dreamsync/src/dreamsync/render.py:131)
- `_render_wave()` at [src/dreamsync/render.py:209](/C:/Users/brian/dreamsync/src/dreamsync/render.py:209)
- `_render_pulse()` at [src/dreamsync/render.py:97](/C:/Users/brian/dreamsync/src/dreamsync/render.py:97)
- `_render_breathe()` at [src/dreamsync/render.py:114](/C:/Users/brian/dreamsync/src/dreamsync/render.py:114)
- `_render_gradient()` at [src/dreamsync/render.py:234](/C:/Users/brian/dreamsync/src/dreamsync/render.py:234)

These functions are all segment-index-driven. They depend on:

- segment count
- time
- BPM
- mirror/orientation behavior
- simple per-effect params

They do not depend on:

- device world position
- section world position
- room origin
- room direction vector
- 3-D extents or regions

That makes them fundamentally intra-device renderers, not room-scale spatial effect generators.

### 2. Reactive beat-triggered effects are authored in `basic_controller.py`

Primary file:

- [src/dreamsync/basic_controller.py](/C:/Users/brian/dreamsync/src/dreamsync/basic_controller.py)

Important entry points:

- `BeatRippleController` at [src/dreamsync/basic_controller.py:18](/C:/Users/brian/dreamsync/src/dreamsync/basic_controller.py:18)
- `BeatFlashController` at [src/dreamsync/basic_controller.py:59](/C:/Users/brian/dreamsync/src/dreamsync/basic_controller.py:59)

Current behavior:

- `BeatRippleController.update()` emits `LightingIntent(mode=EffectMode.RIPPLE, ...)` at [src/dreamsync/basic_controller.py:38](/C:/Users/brian/dreamsync/src/dreamsync/basic_controller.py:38)
- `BeatFlashController.update()` emits `LightingIntent(mode=EffectMode.PULSE, ...)` at [src/dreamsync/basic_controller.py:74](/C:/Users/brian/dreamsync/src/dreamsync/basic_controller.py:74)

This layer decides when effects should happen, but not how a 3-D room-scale motion should be constructed.

### 3. Show playback builds intents and forwards cue params in `show/runtime.py`

Primary file:

- [src/dreamsync/show/runtime.py](/C:/Users/brian/dreamsync/src/dreamsync/show/runtime.py)

Important pieces:

- `_MODE_MAP` at [src/dreamsync/show/runtime.py:15](/C:/Users/brian/dreamsync/src/dreamsync/show/runtime.py:15)
- `tick()` at [src/dreamsync/show/runtime.py:67](/C:/Users/brian/dreamsync/src/dreamsync/show/runtime.py:67)
- `_build_intent()` at [src/dreamsync/show/runtime.py:171](/C:/Users/brian/dreamsync/src/dreamsync/show/runtime.py:171)

The show runtime currently:

- converts cue `render_mode` into `EffectMode` and `RenderMode`
- builds a `LightingIntent`
- forwards cue params through `runtime_params`
- injects `_render_mode` and `_spatial_palette`
- calls `multi_adapter.send_frame(...)` at [src/dreamsync/show/runtime.py:108](/C:/Users/brian/dreamsync/src/dreamsync/show/runtime.py:108)

This is a strong seam for future upgrade because it already passes metadata into the shared adapter path.

### 4. Canonical 3-D placement already exists in `spatial/models.py`

Primary file:

- [src/dreamsync/spatial/models.py](/C:/Users/brian/dreamsync/src/dreamsync/spatial/models.py)

Important definitions:

- `SectionPlacement` at [src/dreamsync/spatial/models.py:54](/C:/Users/brian/dreamsync/src/dreamsync/spatial/models.py:54)
- `DevicePlacement` at [src/dreamsync/spatial/models.py:66](/C:/Users/brian/dreamsync/src/dreamsync/spatial/models.py:66)
- `parse_device_placement()` at [src/dreamsync/spatial/models.py:90](/C:/Users/brian/dreamsync/src/dreamsync/spatial/models.py:90)

Current canonical axis semantics in this file are:

- `x` = left/right
- `y` = height
- `z` = front/back depth

This file already supports:

- top-level device coordinates
- per-section strip coordinates
- orientation
- per-node weight
- enabled/disabled nodes

This is the right source of truth for 3-D spatial animation.

### 5. Continuous spatial sampling already exists in `spatial/mapper.py`

Primary file:

- [src/dreamsync/spatial/mapper.py](/C:/Users/brian/dreamsync/src/dreamsync/spatial/mapper.py)

Important pieces:

- `resolve_spatial_spec()` at [src/dreamsync/spatial/mapper.py:109](/C:/Users/brian/dreamsync/src/dreamsync/spatial/mapper.py:109)
- `sample_point()` at [src/dreamsync/spatial/mapper.py:145](/C:/Users/brian/dreamsync/src/dreamsync/spatial/mapper.py:145)
- `_wave_activation()` and `_emanation_activation()` later in the file

The mapper already understands explicit spatial metadata such as:

- `spatial_mode`
- `spatial_origin`
- `spatial_direction`
- `spatial_width`
- `spatial_blend`
- `spatial_extent`
- `spatial_delay_ms`

It can already sample a `DevicePlacement` or section placement in true `x/y/z` space and compute:

- intensity scaling
- color override for blending

This is the current best foundation for room-scale effects.

### 6. Shared runtime spatial send path exists in `output/govee_lan.py`

Primary file:

- [src/dreamsync/output/govee_lan.py](/C:/Users/brian/dreamsync/src/dreamsync/output/govee_lan.py)

Important pieces:

- `send_frame()` at [src/dreamsync/output/govee_lan.py:427](/C:/Users/brian/dreamsync/src/dreamsync/output/govee_lan.py:427)
- `send_continuous_spatial_frame()` at [src/dreamsync/output/govee_lan.py:490](/C:/Users/brian/dreamsync/src/dreamsync/output/govee_lan.py:490)
- `_spatialize_colors()` at [src/dreamsync/output/govee_lan.py:617](/C:/Users/brian/dreamsync/src/dreamsync/output/govee_lan.py:617)

Current continuous path:

1. render a normal strip frame with `SegmentRenderer`
2. resolve spatial metadata into a `SpatialSpec`
3. sample each device or section placement in 3-D
4. spatially scale or recolor the rendered frame
5. send the result

This is the most important current limitation:

the runtime is still doing:

```text
legacy strip animation
-> per-device/per-section spatial modulation
-> output
```

instead of:

```text
spatial effect definition in room coordinates
-> per-device/per-section sampling
-> optional local strip texture
-> output
```

### 7. Per-section placement is already honored in the send path

Inside `_spatialize_colors()` at [src/dreamsync/output/govee_lan.py:629](/C:/Users/brian/dreamsync/src/dreamsync/output/govee_lan.py:629), the adapter already:

- checks `placement.sections`
- constructs per-section sampling placements
- samples each section independently using `sample_point()`

This is a major positive result: the runtime already has the correct authority rule for section-aware animation sampling.

## Architectural Mismatch

### Legacy 1-D creation vs continuous 3-D sampling

The repo has two different animation models living side by side:

1. old render-mode generation
2. newer continuous spatial sampling

The old model:

- creates visible motion using segment index and local strip geometry
- assumes effects like scroll and wave are strip-local

The new model:

- uses room coordinates
- understands direction vectors and origins
- can spatially sequence devices and sections

At the moment, the new model is mostly downstream from the old one. That is why effects do not yet fully feel room-native.

### Residual grid-era logic is still present

`SpatialMapper.map_reactive()` and `map_show_cue()` at [src/dreamsync/spatial/mapper.py:71](/C:/Users/brian/dreamsync/src/dreamsync/spatial/mapper.py:71) and [src/dreamsync/spatial/mapper.py:91](/C:/Users/brian/dreamsync/src/dreamsync/spatial/mapper.py:91) still support the older 3x3-scene mindset.

That code uses:

- `horizontal`
- `depth`
- `diagonal`
- `radial`
- `GridCell`
- coarse `cell_coordinates()`

This is useful for compatibility, but it is not the best primary home for new 3-D room effects.

### Axis mismatch still exists

There is still a semantics mismatch between:

- canonical placement parsing in `spatial/models.py`
- older grid-era spatial logic in `spatial/mapper.py`

In the canonical model:

- `y` means vertical height
- `z` means front/back depth

In the older grid logic:

- `y` is still used as front/back in several places, including `cell_coordinates()`-derived logic

That mismatch must be resolved before expanding 3-D animation semantics further.

## Specific Problems Blocking True 3-D Effects

### Left-to-right ripple across the room

Possible today only in a limited sense.

The continuous sampler can already do directional `x+` or `x-` behavior, but the underlying visible frame is still generated by local strip renderers. So the room sequencing is real, but the effect is not defined natively as one coherent room ripple.

### Ripple from center

Partially possible using `spatial_mode="emanation"` and `spatial_origin={0,0,0}` in the continuous spatial path, but this is not exposed as a first-class authored effect preset and is not the default reactive/show authoring model.

### Wave top to bottom

The continuous sampler supports `y+` and `y-` directions, so the math path exists. But the higher-level code still has legacy axis assumptions and does not yet define top-down motion as a stable, explicit room effect interface.

### Flash top lights only

This wants a region mask, not just a direction. The existing `spatial_extent` support in `SpatialSpec` is already the correct primitive for this, but it is not yet surfaced as a clear authored runtime behavior.

## Ripple Is Not Yet A First-Class Spatial Runtime Effect

`BeatRippleController` emits `EffectMode.RIPPLE` at [src/dreamsync/basic_controller.py:39](/C:/Users/brian/dreamsync/src/dreamsync/basic_controller.py:39), but `SegmentRenderer` does not have a `RenderMode.RIPPLE`.

This means:

- ripple exists at the intent/controller vocabulary level
- ripple does not exist as a primary renderer/spatial effect mode
- ripple behavior is not yet normalized into the newer explicit spatial metadata model

This is a sign that the old reactive vocabulary and the newer spatial runtime vocabulary still need to be unified.

## What Should Change

### 1. Move room-scale motion definition into the continuous spatial engine

New room-scale effects should be defined primarily using:

- `spatial_mode`
- `spatial_origin`
- `spatial_direction`
- `spatial_width`
- `spatial_extent`
- `spatial_delay_ms`

These should be resolved before device rendering, not after a legacy strip effect has already defined the motion.

### 2. Treat `SegmentRenderer` as local texture, not room choreography

`SegmentRenderer` should remain responsible for:

- local strip texture
- local per-device wave texture
- local gradients
- local pulse/breathe behavior

It should not remain the main source of room-scale directionality.

### 3. Normalize authored spatial presets into explicit metadata

Examples:

- left-to-right ripple:
  - `spatial_mode: wave`
  - `spatial_direction: x+`
- center ripple:
  - `spatial_mode: emanation`
  - `spatial_origin: {x: 0.0, y: 0.0, z: 0.0}`
- top-to-bottom wave:
  - `spatial_mode: wave`
  - `spatial_direction: y-`
- top-lights-only flash:
  - `spatial_mode: wash`
  - `spatial_extent` constrained to high `y`

### 4. Reconcile axis semantics repo-wide

The runtime should consistently use:

- `x` = left/right
- `y` = vertical
- `z` = front/back

The old grid-scene compatibility layer can remain if needed, but it should no longer define the semantics of new room effects.

### 5. Unify legacy reactive effect naming with spatial runtime controls

`EffectMode.RIPPLE` should either:

- become a compatibility label that resolves into explicit spatial metadata

or

- be retired in favor of explicit spatial presets

Right now it sits awkwardly between the old and new systems.

## Recommended Direction

The strongest architecture is:

```text
show cue / reactive trigger
-> normalize explicit spatial metadata
-> continuous room-scale spatial effect evaluation
-> sample each device / section in x/y/z
-> optionally apply local strip texture
-> send final colors
```

This lets the system support:

- room-wide directionality
- center-origin effects
- vertical and depth-aware sequencing
- section-level strip choreography
- region-restricted flashes and washes

without forcing all motion semantics through old 1-D segment renderers.

## Conclusion

DreamSync already has enough 3-D infrastructure to support materially better spatial effects. The main remaining gap is not placement storage or section sampling; it is effect authorship and runtime ownership.

Today:

- 3-D placement is available
- continuous spatial sampling is available
- shared playback wiring is available
- effect generation is still mostly legacy and strip-local

So the next upgrade should focus on making the continuous spatial engine the primary place where room-scale animation is defined, while keeping `SegmentRenderer` as a local rendering layer rather than the master source of spatial behavior.
