# Live Instrument Pan and Mixed-Source Separation

## Status

This document is the source of truth for the next instrument-aware audio phase.
It covers the remaining work after offline stereo analysis and compiled
instrument routing landed.

Implementation status as of 2026-05-21:

- Deliverables 1-6 are now implemented in code
- the next active phase is Deliverable 7: validation and tuning on real tracks
  and real devices

It is a companion to:

- [continuous-spatial-runtime-followup.md](/C:/Users/brian/dreamsync/dev/plans/continuous-spatial-runtime-followup.md)
- [space-zone-mapping.md](/C:/Users/brian/dreamsync/dev/plans/space-zone-mapping.md)

Those documents describe the shared spatial runtime direction. This document
describes how live audio analysis should feed that runtime with stereo-aware,
instrument-oriented metadata.

## Decision

Assume true isolated stems are not available.

That means DreamSync should not plan around:

- clean vocal stems
- clean drum stems
- backend-provided perfect separation
- a future where panning depends on lossless isolated sources

Instead, every live and offline instrument-aware behavior must begin with mixed
source audio and derive usable proxies from that mix.

The goal is not studio-grade source separation. The goal is lighting-grade
instrument inference that is:

- stable
- explainable
- low-latency in live mode
- spatially useful

## Current State

Already implemented:

- offline stereo decode is preserved through analysis
- offline frame/window pan features are computed
- offline `instrument_proxies` include pan summaries
- compiled cues can react to `instrument_routes`
- compiled spatial layers can shift according to proxy pan metadata
- there is an analyzer separation seam for optional future backends

Not yet complete:

- live capture still needs true stereo preservation through the whole ingest path
- live runtime still leans more on EQ events than instrument proxies
- mixed-source proxy inference needs stronger runtime heuristics
- concurrent instrument ownership and blending needs stricter rules
- validation on real stereo-heavy tracks and devices is still incomplete

## Non-Goals

- no requirement for perfect stems
- no requirement for phase-perfect spatial localization
- no mandatory large-model or cloud separation backend
- no assumption that a future backend can replace mixed-source heuristics

If an optional backend ever exists, it should enhance the mixed-source path, not
replace the system design.

## Core Design Principles

### 1. Stereo survives by default

Live analysis should preserve stereo from capture through feature extraction.
Mono should become the fallback path, not the default path.

### 2. Mixed-source proxies are first-class

`drums`, `bass`, `vocals`, `harmonic`, and `percussive` should remain proxy
signals inferred from the mix. The system should expose confidence, dominance,
and pan behavior rather than pretending these are real stems.

### 3. Shared semantics between offline and live

The live path should emit the same kinds of routing inputs the compiled path
already understands:

- `instrument_proxies`
- `active_instrument_routes`
- `pan_center`
- `pan_width`
- `spatial_origin`
- `spatial_width`

### 4. Stable runtime ownership beats overreaction

When drums, bass, and vocals all appear active together, the runtime should
favor stable blending and priority rules over frame-to-frame thrash.

## Deliverable 1: Preserve Stereo Through Live Ingest

### Goal

Stop collapsing live audio to mono before analysis.

### Work

- audit the live capture path in `src/dreamsync/live.py`
- preserve two-channel windows where the input device provides stereo
- compute mono only as a derived analysis helper, not as the authoritative input
- make telemetry explicit about channel count and mono fallback

### Files

- `src/dreamsync/live.py`
- `dev/tests/test_live_bpm.py`
- `dev/tests/test_telemetry.py`

### Acceptance

- live feature extraction can consume stereo frames
- mono sources still work without regressions
- telemetry shows whether a frame/window was stereo-preserved or mono-fallback

## Deliverable 2: Add Live Pan Features That Match Offline Semantics

### Goal

Make live mode compute the same spatially useful pan features the offline path
already has.

### Work

- compute rolling `pan_center` and `pan_width`
- add left/right energy summaries
- add per-band pan summaries where latency cost is acceptable
- expose the data in live debug/telemetry payloads
- feed the data into the existing route enrichment path

### Files

- `src/dreamsync/live.py`
- `src/dreamsync/effects.py`
- `dev/tests/test_live_bpm.py`
- `dev/tests/test_telemetry.py`

### Acceptance

- hard-left and hard-right content move spatial origin sensibly
- center vocals stay near center unless the mix says otherwise
- mono content yields neutral or damped pan output instead of noise

## Deliverable 3: Strengthen Mixed-Source Instrument Proxy Inference for Live Use

### Goal

Upgrade live proxy inference from mostly band/event-driven behavior to a more
explicit mixed-source instrument model.

### Work

- port the useful offline heuristic ideas into a live-safe rolling estimator
- combine:
  - onset / transient energy
  - low-band envelopes
  - chroma or harmonic stability where cheap enough
  - percussive vs harmonic balance
  - stereo width and center bias
- emit confidence-weighted proxy states for:
  - `drums`
  - `bass`
  - `vocals`
  - `harmonic`
  - `percussive`

### Notes

This is still mixed-source analysis. The runtime should say "proxy" or
"confidence" in internal naming and telemetry where useful.

### Files

- `src/dreamsync/live.py`
- `src/dreamsync/analyzer/instruments.py`
- `dev/tests/test_live_bpm.py`
- `dev/tests/test_analyzer_instruments.py`

### Acceptance

- bass entrances and exits register separately from general low-end energy
- drum-heavy transients can drive distinct behavior from sustained bass
- centered vocal presence is distinguishable from wide harmonic wash often
  enough to be lighting-useful

## Deliverable 4: Live Instrument Routes and Pan Layers

### Goal

Have live mode emit `active_instrument_routes` and pan-aware instrument layers
through the same shared spatial/runtime seam as compiled shows.

### Work

- reuse profile `instrument_routes` in live mode
- enrich live routes with:
  - `pan_center`
  - `pan_width`
  - `spatial_origin`
  - `spatial_width`
- ensure the mapper/runtime path treats live instrument layers the same way as
  compiled instrument layers where practical
- keep EQ routes available as a fallback and complement

### Files

- `src/dreamsync/live.py`
- `src/dreamsync/effects.py`
- `src/dreamsync/spatial/mapper.py`
- `src/dreamsync/output/govee_lan.py`
- `dev/tests/test_live_bpm.py`
- `dev/tests/test_spatial_runtime.py`

### Acceptance

- live mode emits instrument-aware layers, not only EQ layers
- instrument routes can override or bias EQ routes when confidence is high
- existing non-instrument live behavior remains usable on weak or ambiguous audio

## Deliverable 5: Runtime Blending and Ownership Rules for Concurrent Proxies

### Goal

Make simultaneous mixed-source proxies behave coherently instead of fighting for
the same sectors.

### Work

- define priority and blending rules across:
  - dominant instrument routes
  - present instrument routes
  - EQ routes
  - section/base wash
- add short hold windows or hysteresis to reduce route flicker
- limit contradictory width/origin jumps across adjacent frames
- define fallback behavior when proxy confidence drops suddenly

### Files

- `src/dreamsync/compiler/assemble.py`
- `src/dreamsync/live.py`
- `src/dreamsync/spatial/mapper.py`
- `src/dreamsync/output/govee_lan.py`
- `dev/tests/test_compiler_assemble.py`
- `dev/tests/test_spatial_mapper.py`
- `dev/tests/test_spatial_runtime.py`

### Acceptance

- vocals can hold center while bass leans left/right and drums stay broad
- route ownership does not flicker wildly during dense choruses
- spatial output stays legible under multiple simultaneous proxies

## Deliverable 6: Better Mixed-Source Separation Hooks

### Goal

Improve the separation seam so future enhancements can plug in without changing
the routing model, while keeping mixed-source heuristics as the default.

### Work

- extend the separation seam to support mix-derived enhancement artifacts
- allow backends or local processors to provide:
  - masks
  - confidence maps
  - enhanced proxy envelopes
  - stereo emphasis hints
- do not require clean isolated audio streams
- ensure the runtime can consume partial enhancements without changing behavior
  contracts

### Files

- `src/dreamsync/analyzer/separation.py`
- `src/dreamsync/analyzer/analyze.py`
- `src/dreamsync/live.py`
- `dev/tests/test_analyzer_separation.py`

### Acceptance

- the seam can accept enhancement metadata that improves proxy inference
- the default mixed-source path remains the canonical implementation
- no plan step assumes perfect stem isolation

## Deliverable 7: Validation and Tuning on Real Material

### Goal

Prove the behavior is trustworthy on actual songs and real devices.

### Work

- validate compiled stereo behavior on offline tracks with clear left/right cues
- validate live stereo preservation with real capture hardware
- tune thresholds against at least:
  - bass-led music
  - drum-heavy rock or EDM
  - vocal-centered pop
  - dense wide choruses
- document failure modes where proxy confidence should intentionally back off

### Tests

- `dev/tests/test_preview_simulation.py`
- `dev/tests/test_live_bpm.py`
- `dev/tests/test_spatial_runtime.py`
- manual device validation with `dev/devices.yaml`

### Acceptance

- compiled and live pan behavior feel directionally consistent
- obvious stereo placements are reflected in spatial routing
- bad or ambiguous content degrades gracefully instead of hallucinating stems

## Recommended Order

1. Deliverable 1: live stereo preservation
2. Deliverable 2: live pan features
3. Deliverable 3: stronger mixed-source live proxies
4. Deliverable 4: live instrument routes and pan layers
5. Deliverable 5: runtime blending and ownership rules
6. Deliverable 6: mixed-source separation seam upgrades
7. Deliverable 7: real-track and real-device tuning

## Risks

### Risk: Live CPU cost rises too quickly

Stereo and proxy analysis can become too expensive for low-latency mode.

Mitigation:

- keep the cheapest useful features on the hot path
- compute richer summaries on rolling windows instead of every sample block
- prefer damped/stable behavior over maximum feature count

### Risk: Proxy confidence looks stronger than it really is

Lighting behavior can feel wrong if the system acts too certain about ambiguous
audio.

Mitigation:

- keep confidence thresholds explicit
- fall back to EQ-driven behavior when confidence is weak
- expose proxy confidence in telemetry/debug output

### Risk: Offline and live semantics drift apart

If compiled shows and live mode interpret pan and proxy data differently, the
spatial editor and profile rules become harder to trust.

Mitigation:

- share field names and route enrichment logic wherever possible
- expand tests that compare compiled/live semantics at the route level
