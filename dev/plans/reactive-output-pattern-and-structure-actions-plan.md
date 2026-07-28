# Reactive Output Pattern and Structure Actions: Successor Plan

## Purpose

This plan is the successor to
`reactive-structure-first-similarity-lighting-human-test.md`.

Beat, tempo-family, meter-intent, downbeat, and D/S ADC-clock behavior are now
working well enough for human use. Treat those systems as a protected baseline.
The next implementation task is to diagnose and fix the remaining optical
output defect, then complete simulation acceptance steps 11–16.

Do not begin hardware acceptance until every simulation gate in this plan
passes.

## Current status

### Accepted baseline

- Automatic beat detection follows the music correctly.
- D/S taps select an audio-supported pulse and meter interpretation.
- Audio recurrence owns exact phase.
- D/S markers snap to audio-supported beat lines.
- Half-time, double-time, slight BPM correction, and compound pulse selection
  have automated coverage.
- D/S timing uses the PortAudio ADC clock rather than GUI repaint time.
- The upcoming beat/effect diagnostic is available.

### Unfinished acceptance work

The original simulation steps 11–16 remain incomplete:

11. Confirm small actions occur on matching downbeats and never on secondary
    beats.
12. Enable phrase-boundary actions.
13. Confirm phrase resets or palette movement occur on the displayed target
    downbeat.
14. Enable section entrance/return actions.
15. Confirm requested and applied effects agree.
16. Disable all action tiers and confirm ordinary continuous Reactive rendering
    resumes unchanged.

### Blocking defect

The simulation currently appears to alternate room/device output between white
and black, and the optical pattern does not appear to correspond to the
detected rhythm. This remains visible even though beat detection itself is now
correct.

The defect must be resolved before step 11. Do not weaken or redesign beat
detection while fixing it.

## Required outcome

1. With all structure action tiers disabled, the selected continuous effect
   renders a stable, palette-preserving pattern that follows the corrected beat
   grid according to that effect's documented semantics.
2. The simulation preview represents the same per-device RGB frames that would
   be sent to hardware, subject only to explicitly documented display gamma.
3. No stage silently converts palette colors into grayscale, full white, or
   black.
4. Beat and downbeat accents do not create binary full-on/full-off output unless
   an explicitly selected effect or profile requests that behavior.
5. Bar, phrase, and section actions are introduced one tier at a time and fire
   only at eligible target downbeats.
6. Disabling every action tier returns to the exact continuous baseline without
   changing effect, palette, brightness, spatial state, or animation phase.

## Non-goals and protected behavior

- Do not retune BPM, cyclic recurrence, manual tap interpretation, ADC timing,
  meter inference, or the future beat grid unless a new deterministic test
  proves that the output defect originates there.
- Do not hide the problem by changing the preview background.
- Do not clamp all output to a constant brightness.
- Do not disable beat accents globally.
- Do not make simulation behavior diverge intentionally from hardware behavior.
- Do not continue to step 11 while the defect reproduces with all action tiers
  disabled.

## Likely fault domains

Investigate these as hypotheses, not conclusions:

1. **Continuous renderer semantics**
   - A fixed `PULSE` renderer may be decaying to zero between every beat and
     jumping to full brightness on every latched beat.
   - `beat_accent`, pulse decay, role brightness, or master brightness may be
     producing an unintended binary envelope.
   - `max_brightness` currently replaces frame intent intensity with `1.0`;
     verify whether this interacts with pulse decay or cue accents.

2. **Render-mode authority**
   - GUI Reactive mode uses a fixed render-mode policy.
   - Profile, structural actuator, runtime control, EQ routes, or instrument
     routes may still change parameters while the renderer mode remains fixed,
     creating incompatible mode/parameter combinations.
   - Confirm the effective mode at every rendered frame and every device.

3. **Palette and intent propagation**
   - Verify `last_intent`, `frame_intent`, Director palette changes, structural
     preset changes, runtime-control transforms, and role transforms use the
     same intended palette.
   - A structural preset is applied after `frame_intent` is selected; verify
     whether the current frame can combine stale intent with new params.

4. **Beat-latch consumption**
   - `pending_render_beat` is intentionally latched until an output frame
     consumes it.
   - Verify one detected beat produces at most one accented frame event and
     does not stay asserted across multiple output frames.
   - Verify secondary beats and downbeats receive the intended different
     accent strengths.

5. **Spatial processing**
   - Compare RGB before and after continuous spatialization.
   - Verify spatial falloff, layers, placement, orientation, and unmapped-node
     fallback do not zero whole devices or promote them to white.
   - Test spatial mapping both enabled and disabled with the same input frames.

6. **Simulation adapter and canvas**
   - Confirm `_PreviewDeviceAdapter.last_colors` retains the exact renderer or
     post-spatial RGB values.
   - Confirm preview snapshot conversion preserves brightness and hue.
   - Confirm the canvas does not replace missing node keys with default
     gray/white or black on alternating updates.
   - Compare simulation snapshot values directly with mirrored hardware payload
     values before assessing the drawn canvas.

7. **Structural or predictive actions**
   - Verify shadow mode and disabled tiers cannot mutate effect, palette,
     intensity, renderer mode, spatial state, or animation phase.
   - Verify stale committed cues are cleared when tiers are disabled.

## Phase 0: Freeze and reproduce

### Tasks

1. Record the exact Reactive configuration that reproduces the defect:
   - selected render mode;
   - active profile and effect;
   - active palette;
   - master/max brightness settings;
   - spatial mapping enabled/disabled;
   - EQ and instrument routes;
   - shadow mode;
   - each structure action tier;
   - simulation-only versus hardware-mirror mode.
2. Reproduce with all structure action tiers disabled.
3. Reproduce with shadow mode enabled.
4. Reproduce with each base render mode:
   - solid;
   - pulse;
   - scroll;
   - breathe;
   - wave;
   - gradient.
5. Use a non-white diagnostic palette such as red/blue so grayscale conversion
   is unmistakable.
6. Save a short frame trace and screenshot for every reproducing combination.

### Gate 0

- If the defect reproduces with all tiers disabled, classify it as a continuous
  rendering/preview defect and do not test structural actions yet.
- If it occurs only when an action tier is enabled, identify the first tier and
  the exact committed cue that begins the defect.

## Phase 1: Add frame provenance diagnostics

Add a bounded, opt-in frame trace. It must not log at unbounded rate or run in
the audio callback.

For each sampled output frame, record:

- stream timestamp and output timestamp;
- detected BPM and selected tap pulse;
- beat, downbeat, beat-in-bar, and beat accent;
- current effect/preset and effective render mode;
- base intent color, intensity, speed, and BPM;
- palette name and palette colors;
- harmonic and predictive intensity additions;
- structural action results;
- active EQ/instrument routes;
- runtime-control overrides;
- per-device role and brightness scale;
- renderer RGB before spatialization;
- RGB after spatialization;
- simulation snapshot hex;
- hardware-mirror RGB or payload, when applicable.

Expose a compact GUI readout containing:

- effective render mode;
- last frame palette/color and intensity;
- last beat/downbeat accent;
- last structural action outcome;
- whether spatialization changed the frame;
- minimum/maximum RGB level across devices;
- whether a frame was unexpectedly achromatic or all-black.

### Required provenance tests

1. One beat event is consumed once.
2. A downbeat retains its stronger accent without repeating on later frames.
3. Shadow mode produces identical output frames to all-tiers-disabled mode for
   the same deterministic input.
4. Simulation snapshot RGB equals the adapter's final per-device RGB.
5. Hardware mirror and simulation receive equivalent RGB.

## Phase 2: Define and enforce base-effect contracts

Create deterministic renderer fixtures using a fixed palette and beat timeline.

### Contract matrix

| Mode | Required behavior |
|---|---|
| Solid | Stable palette color; beat flags cannot cause black/white flipping |
| Pulse | Hue remains palette-derived; brightness follows a documented envelope rather than an accidental binary switch |
| Scroll | Palette-derived segments move at the configured cycle-relative speed |
| Breathe | Continuous palette-derived envelope; no beat-triggered reset unless configured |
| Wave | Continuous palette-derived wave with preserved hue |
| Gradient | Palette-derived gradient; no all-black frame unless intensity explicitly reaches zero |

For pulse specifically:

1. Decide and document the intended minimum brightness floor.
2. Distinguish ordinary-beat and downbeat target levels.
3. Apply decay continuously from the previous envelope.
4. Preserve hue throughout the envelope.
5. Ensure profile/route parameters cannot accidentally request a zero floor or
   full-white color without diagnostics showing that source.

Do not assume pulse is the only fault. Run the matrix through:

- `SegmentRenderer`;
- role transformation;
- spatial mapping;
- `SimulationMultiAdapter`;
- `PreviewMirrorAdapter`.

### Gate 2

With all structure tiers disabled, every mode must satisfy its contract for at
least 60 seconds of deterministic playback. No unexplained white or black frame
may occur.

## Phase 3: Spatial and preview parity

1. Test a bulb, one-segment device, and multi-segment strips.
2. Test mapped and unmapped placements.
3. Test room and strip views.
4. Test dark and light canvas backgrounds.
5. Verify missing node keys retain the last valid frame or an explicit
   unavailable state; they must not alternate between default colors.
6. Add an on-screen option to freeze the last preview frame for inspection.
7. Compare final per-device RGB numerically, not only visually.

### Gate 3

- Preview and hardware mirror agree on per-device RGB.
- Canvas background choice does not alter node RGB.
- No spatial layer creates whole-room white/black alternation unless explicitly
  configured and visible in frame provenance.

## Phase 4: Structural action isolation

Build deterministic replay fixtures with known:

- beat and downbeat indices;
- bar boundaries;
- phrase boundary;
- section entrance and return;
- enabled effect bank;
- palette queue;
- meter-confidence loss;
- late/missed target.

Run this action matrix:

| Shadow | Bar | Phrase | Section | Expected mutation |
|---|---:|---:|---:|---|
| On | Any | Any | Any | None |
| Off | Off | Off | Off | None |
| Off | On | Off | Off | Small bar action only |
| Off | On | On | Off | Bar plus eligible phrase action |
| Off | On | On | On | All eligible tiers, one winner per target |

For every applied or rejected action assert:

- cue ID;
- cue class;
- target bar and beat;
- requested effect;
- requested palette action;
- applied effect and palette;
- exact commit timestamp;
- commit downbeat identity;
- meter confidence;
- outcome and reason;
- before/after continuous renderer state.

## Phase 5: Resume human acceptance at step 11

Start from a fresh session after Gates 0–4 pass.

### Step 11: Ordinary bar actions

1. Disable shadow mode.
2. Enable ordinary bar actions only.
3. Confirm every small action:
   - was visible in the upcoming cue display before commitment;
   - crossed NOW on the matching downbeat;
   - committed once;
   - did not occur on a secondary beat;
   - preserved the base palette/effect contract afterward.
4. Test at least 16 bars.

Pass criteria:

- zero secondary-beat commits;
- zero duplicate commits;
- zero unexplained white/black frames;
- timing within one output render interval.

### Step 12: Enable phrase-boundary actions

1. Keep ordinary bar actions enabled.
2. Enable phrase-boundary actions.
3. Verify phrase cues are distinguishable from ordinary bar cues in the
   upcoming display and frame provenance.

### Step 13: Phrase reset or palette movement

For at least three predicted phrase boundaries, confirm:

- cue appears before the target;
- target remains stable or cancellation is explicit;
- reset/palette movement occurs on the displayed downbeat;
- palette remains within the approved palette set;
- a missed target does not fire late;
- continuous animation resumes without a black/white discontinuity.

### Step 14: Section entrance/return actions

1. Enable section actions.
2. Use an A/B/A or verse/chorus/verse track.
3. Confirm section entrances and returns are identified separately.
4. Confirm high-impact actions abstain when confidence is below threshold.
5. Confirm one boundary yields at most one high-impact action.

### Step 15: Requested versus applied effect

For every section action, compare:

- requested effect;
- enabled effect bank;
- applied effect;
- fallback or rejection reason;
- requested and applied palette;
- render mode before and after;
- frame colors before and after.

Pass criteria:

- requested and applied effects agree when enabled;
- unavailable effects are rejected or use the documented fallback;
- diagnostics never claim an effect that was not rendered;
- effect changes do not silently force white/black output.

### Step 16: Disable all action tiers

1. Disable section, phrase, and ordinary bar actions.
2. Leave Reactive playback running for at least 60 seconds.
3. Confirm:
   - no new structural action commits;
   - queued actions are canceled;
   - current effect remains unchanged;
   - palette remains unchanged;
   - brightness and spatial state remain unchanged;
   - continuous animation phase is not reset;
   - beat accents continue according to the base-effect contract;
   - no white/black flipping returns.

Compare the resulting trace against the all-tiers-disabled baseline from
Phase 0.

## Phase 6: Negative and recovery cases

Repeat the existing negative cases after step 16:

- one-chord passage with instrumentation changes;
- harmonically busy but structurally stable material;
- drum-only passage;
- silence and input interruption;
- low/lost meter confidence;
- disabled requested effect;
- shadow mode or tier disable immediately before target;
- manual D realignment while a cue is scheduled;
- N reset while a cue is scheduled;
- output-frame delay or dropped analysis block.

Required behavior:

- scheduled cues cancel when their beat/bar identity becomes invalid;
- no action fires late;
- no secondary path bypasses the downbeat lock;
- renderer returns to the same continuous base state;
- recovery cannot introduce a white/black frame unless the selected effect's
  documented envelope explicitly requires it.

## Phase 7: Hardware gate

Only after all simulation steps pass:

1. Mirror simulation and hardware simultaneously.
2. Compare sampled per-device RGB values and timestamps.
3. Confirm no audio output/monitor stream is opened.
4. Confirm output cadence and brightness limits.
5. Confirm stop and master disable are immediate.
6. Confirm spatial settings remain stable across actions.
7. Repeat steps 11–16 on hardware.

## Automated test additions

At minimum, add or extend tests for:

- base render-mode contract matrix;
- pulse floor, hue preservation, and ordinary/downbeat envelopes;
- one-shot beat latch consumption;
- stale `frame_intent` versus newly applied preset/params;
- max-brightness interaction;
- role brightness transformation;
- pre/post-spatial RGB parity;
- missing-node preview behavior;
- simulation/hardware mirror parity;
- shadow and all-disabled optical immutability;
- each action-tier combination;
- exact-downbeat commit;
- secondary-beat rejection;
- missed-target cancellation;
- requested/applied effect agreement;
- disabling tiers restores continuous baseline;
- D/N invalidates scheduled cues without resetting the base renderer.

Run focused tests after each phase, then the complete live, output, spatial,
prediction, GUI service, and GUI rendering suites before human testing.

## Definition of done

The successor task is complete only when:

1. the white/black defect has a demonstrated root cause;
2. a deterministic regression test fails before and passes after the fix;
3. continuous rendering passes the mode contract matrix;
4. simulation and hardware-mirror RGB agree;
5. steps 11–16 pass in simulation;
6. all negative cases pass;
7. beat/tap behavior remains unchanged;
8. the human-test handoff is updated with actual observed results, not only
   expected behavior;
9. no unrelated working-tree changes are modified or removed.

## Recommended first command sequence for the next chat

1. Read this plan and
   `reactive-structure-first-similarity-lighting-human-test.md`.
2. Inspect the current dirty worktree without cleaning it.
3. Run the existing focused live/output/preview tests to establish a baseline.
4. Reproduce the defect with all action tiers disabled and a red/blue palette.
5. Add bounded frame provenance before changing renderer behavior.
6. Stop and report if the defect cannot be reproduced or if the observed RGB
   already differs between renderer, spatial output, and canvas.
