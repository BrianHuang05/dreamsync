# Structure-First Reactive Lighting: Human Test Handoff

## Readiness

The implementation is ready for simulation-first human testing. Do not begin
hardware testing until the simulation checks below pass.

The new engine is opt-in, starts in shadow mode, and keeps ordinary bar,
phrase, and section actions independently disabled by default. Legacy harmonic
structure and structure similarity are mutually exclusive.

## Simulation-first acceptance

1. Start DreamSync with simulation output and open Reactive Live mode.
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
