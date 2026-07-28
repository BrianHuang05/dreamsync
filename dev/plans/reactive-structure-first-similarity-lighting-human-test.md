# Structure-First Reactive Lighting: Human Test Handoff

## Readiness

The implementation is ready for simulation-first human testing. Do not begin
hardware testing until the simulation checks below pass.

The new engine is opt-in, starts in shadow mode, and keeps ordinary bar,
phrase, and section actions independently disabled by default. Legacy harmonic
structure and structure similarity are mutually exclusive.

## Successor output implementation results (2026-07-27)

The optical-output successor work is implemented and ready for the manual
simulation checks below. Hardware testing remains gated on completing steps
11-16 in simulation.

Observed root causes and corrections:

- Pulse rendering had no minimum brightness floor. Its exponential envelope
  therefore approached black between beat frames; a white palette made the
  result look like a binary white/black alternation. Pulse now has a 12%
  documented default floor and an enforced 8% safety floor unless a profile
  explicitly supplies both `allow_blackout: true` and `pulse_floor: 0`.
- Breathe, wave, and single-color gradient modes could also reach an
  unintended whole-device black frame. Their default continuous envelopes now
  preserve a low palette-derived floor.
- Spatial mapping inferred a second moving room mask from ordinary
  scroll/wave renderer modes even when no spatial action was configured.
  Mapped devices could therefore be suppressed despite a valid renderer RGB
  frame. Ordinary renderer modes now use a static pass-through spatial layer;
  moving room masks require explicit spatial metadata.
- A structural preset could commit after the render loop captured
  `frame_intent`, combining an old palette color with new effect parameters on
  the commit frame. The intent color is now refreshed from the committed
  Director palette before route and runtime-control transforms.
- Hardware mirroring previously rendered the preview independently. The mirror
  now copies the hardware adapter's exact final per-device RGB frame after
  role and spatial transforms and reports numeric parity.
- A transient missing preview key rebuilt the snapshot without that node,
  exposing the canvas default color. Missing keys now retain their last valid
  RGB value.

Observed automated acceptance:

- The six base modes each completed 1,800 deterministic frames (60 seconds at
  30 FPS) using a non-white diagnostic palette with no unexplained whole-frame
  black or white conversion.
- Pulse floor, hue preservation, ordinary/downbeat target separation, role
  brightness, bounded trace sampling, missing-node retention, exact
  simulation/hardware-mirror RGB, stale-intent refresh, and all action-tier
  combinations have deterministic regression coverage.
- The focused output, preview, spatial, live-structure, prediction, and GUI
  diagnostic verification passed before this handoff.

The Preview panel now includes:

- **Freeze frame**, which holds the last valid per-device RGB for inspection;
- a compact frame line with effective render mode, color/intensity, beat
  accent/downbeat, last action outcome, spatial-change status, RGB range,
  achromatic/all-black warnings, and hardware-mirror parity;
- a bounded 240-frame provenance trace when structure diagnostics or telemetry
  are enabled. The trace is recorded in the render loop, never the audio
  callback.

## Manual timing and cycle controls

- **D** registers a manual downbeat goalpost. **S** registers a manual regular
  beat goalpost. Each keypress is mapped through the input device's PortAudio
  ADC clock, quantized to the nearest audio-analysis hop, and moves the nearest
  cycle-grid beat to that timestamp. GUI repaint and analysis-queue latency do
  not define the goalpost time.
- A goalpost labels the nearest audio-supported grid pulse; it never replaces
  the audio-tracked clock or suppresses later automatic beat evidence. The taps
  select the intended pulse and meter interpretation; audio recurrence selects
  the exact phase. Repeated taps can select supported half-time, double-time,
  or compound-meter pulse families without dragging the grid to keypress
  jitter.
- A sequence such as **D S S S D** establishes the beat latch and infers 4/4.
  The detector readout shows the tap-derived pulse BPM and confidence while it
  reconciles that interpretation with the audio.
- The waveform uses orange for automatic beats, red for automatic downbeats,
  blue for manual beats, and green for manual downbeats.
- **NOW** divides the waveform: captured input is on the left, while dashed
  predicted beats/downbeats and labeled armed or scheduled effects appear on
  the right.
- The simulation preview preserves rendered brightness. Pulse effects should
  visibly decay between beats rather than switching between full brightness
  and black.
- **N** clears beat history and starts a fresh song/session detection state.
  D/S/N do not auto-repeat when held. The detector readout reports automatic
  reset count and its last reason (`silence` or `crossfade`) so an unexpected
  reacquisition can be distinguished from a D/S structure realignment.
- **Shift+[** / **{** halves the detector cycle tempo; **Shift+]** / **}**
  doubles it. The adjacent 1/2x, 1x, and 2x cycle buttons perform the same
  operation, and the Beat Detector readout reports the adjusted cycle BPM.
- The separate 1/2x, 1x, and 2x effect-speed controls remain relative to the
  current cycle tempo.

## Simulation-first acceptance

1. Start DreamSync with simulation output and open Reactive Live mode.
   Confirm the waveform has a centered **NOW** line and a future half.
2. Leave **Legacy harmonic structure** disabled.
3. Enable **Structure similarity analysis**.
4. Enable **Log/show structure similarity evidence**.
5. Keep **Structure shadow mode (no optical actions)** enabled.
6. Leave all three action tiers disabled.
7. Play a song with an obvious verse/chorus/verse or A/B/A return.
8. In the structure readout, confirm:
   - the configured meter and completed bar index advance on downbeats;
   - top earlier-bar and sequence matches strengthen on repeated material;
   - anonymous section identities such as A and B become stable;
   - the leading phrase hypothesis says whether it is a duration prior or
     song-local recurrence;
   - a predicted target bar appears before a likely boundary.
   On the waveform, confirm the target effect is labeled on the future half
   before it fires, then crosses **NOW** at the displayed beat.
9. Confirm shadow mode does not change the current effect, palette, brightness,
   spatial state, or continuous animation.
10. Disable shadow mode and enable **Enable ordinary bar actions** only.
11. Confirm small actions occur on matching downbeats and never on secondary
    beats. Run at least 16 bars and use the frame line to confirm one action
    outcome per target, no all-black/achromatic warning, and no secondary-beat
    commit.
12. Enable **Enable phrase-boundary actions**.
13. Confirm phrase resets/palette movement occur on the displayed target
    downbeat. Inspect at least three phrase boundaries; freeze the commit frame
    when needed and confirm the palette color remains chromatic.
14. Enable **Enable section entrance/return actions**.
15. Confirm the requested effect and applied effect in the diagnostics agree.
16. Disable all action tiers again and confirm continuous Reactive rendering
    continues normally for at least 60 seconds. Confirm queued actions are
    canceled, animation phase does not reset, beat accents continue, and the
    frame line reports no all-black/achromatic or mirror-parity warning.

## Negative cases

Exercise these before moving to hardware:

- a one-chord passage whose instrumentation changes;
- harmonically busy material with stable instrumentation;
- a drum-only passage;
- silence, crowd noise, and a capture interruption;
- deliberately low or lost meter confidence;
- a predicted effect removed from the enabled effect bank;
- shadow mode enabled or all action tiers disabled immediately before a target
  downbeat.

Expected results:

- structure remains chord-label independent and multi-feature;
- high-impact actions abstain when timing or structure is uncertain;
- disabled effects are rejected rather than applied;
- reset/capture interruption cancels scheduled actions;
- a missed target does not fire late;
- no mood, drop, timer, chord, wall-clock, or secondary-beat path bypasses the
  bar lock;
- spatial settings and continuous rendering remain unchanged.

## Hardware gate

After simulation acceptance, repeat with hardware and verify:

- no audio monitoring/output stream is created;
- the visible action lands within one render interval of the target downbeat;
- output cadence and device brightness limits remain stable;
- stop/master-disable is immediate;
- spatial settings do not change;
- one boundary produces at most one discrete action.

Record the song, approximate timestamps, configured meter, displayed target,
requested/applied action, and whether the result was early, exact, late,
degraded, rejected, or missed for every anomaly.
