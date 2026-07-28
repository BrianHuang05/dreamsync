# Structure-First Reactive Lighting: Human Test Handoff

## Readiness

The implementation is ready for simulation-first human testing. Do not begin
hardware testing until the simulation checks below pass.

The new engine is opt-in, starts in shadow mode, and keeps ordinary bar,
phrase, and section actions independently disabled by default. Legacy harmonic
structure and structure similarity are mutually exclusive.

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
    beats.
12. Enable **Enable phrase-boundary actions**.
13. Confirm phrase resets/palette movement occur on the displayed target
    downbeat.
14. Enable **Enable section entrance/return actions**.
15. Confirm the requested effect and applied effect in the diagnostics agree.
16. Disable all action tiers again and confirm continuous Reactive rendering
    continues normally.

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
