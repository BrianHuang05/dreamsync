# Bug 2 Investigation: BPM Lock-in Time

**Date**: 2026-02-24
**Source data**: `long_bar_test.txt` (600s session, boundaries #5–#16)

## Original Claim

> After each song boundary reset, the time for BPM to lock onto a non-zero value
> increases monotonically: #5 ~4s, #8 ~16s, #12 ~33s, #15 ~51s.

## Finding: Lock-in Delay Is Constant

The lock-in delay (time from boundary to first non-zero BPM) is **3 seconds for every
boundary**, matching the `min_frames` parameter (`int(3.0 * 44100 / 512)` = 258 frames).

| B#  | Stream Time | Lock Delay | First BPM |
|-----|------------|------------|-----------|
| #5  | ~196s      | 3s         | 160.2     |
| #6  | ~227s      | 3s         | 138.5     |
| #7  | ~262s      | 3s         | 84.5      |
| #8  | ~300s      | 3s         | 109.1     |
| #9  | ~335s      | 3s         | 115.6     |
| #10 | ~379s      | 2s         | 153.8     |
| #11 | ~411s      | 3s         | 132.4     |
| #12 | ~444s      | 3s         | 109.0     |
| #13 | ~475s      | 3s         | 139.5     |
| #14 | ~506s      | 3s         | 170.6     |
| #15 | ~537s      | 3s         | 100.7     |
| #16 | ~570s      | 3s         | 91.1      |

`stream_t` not being reset at boundaries is **not a problem** — all time comparisons
in `LiveBpmEstimator` use relative differences, and `last_update_t`/`_last_onset_beat_t`
are set to `-1e9` on reset, so the first check always passes regardless of absolute
`stream_t` value.

## Actual Problem: Post-Lock BPM Instability

The real issue is that after locking, BPM values are **highly volatile** and the initial
estimate is **frequently 30–60 BPM wrong**.

### Per-Boundary Stability (first 30 seconds after lock)

| B#  | Lock BPM | Distinct Values | Jumps >3 BPM | Worst Jump | Settled Range |
|-----|----------|----------------|--------------|------------|---------------|
| #5  | 160.2    | 12             | 10           | -49.5      | 97–124        |
| #6  | 138.5    | 16             | 13           | -37.1      | 93–145        |
| #7  | 84.5     | 8              | 5            | +36.1      | 121–136       |
| #8  | 109.1    | 13             | 10           | +34.9      | 100–138       |
| #9  | 115.6    | 9              | 7            | +42.4      | 107–141       |
| #10 | 153.8    | 11             | 6            | -34.4      | 102–119       |
| #11 | 132.4    | 14             | 8            | -20.6      | 97–117        |
| #12 | 109.0    | 10             | 3            | +25.6      | 103–129       |
| #13 | 139.5    | 17             | 10           | -10.4      | 102–115       |
| #14 | 170.6    | 5              | 6            | -12.6      | 150–173       |
| #15 | 100.7    | 12             | 10           | +39.2      | 88–128        |
| #16 | 91.1     | 9              | 10           | +12.3      | 89–116        |

### Key observations

- **Average distinct BPM values per 30s window**: 11.3
- **Average jumps >3 BPM per 30s window**: 8.2
- **Instability is constant, not progressive** — #5–#6 are as volatile as #15–#16
- Only **#12** showed decent stability (3 jumps, held 112.4 for 15s)
- Only **#14** locked into a narrow range (150–173, ~20 BPM spread)

### Typical post-boundary BPM trajectory

Example — Boundary #13 (cascading drop):
```
t=478  bpm=139.5  <-- initial lock (wrong)
t=481  bpm=134.7
t=482  bpm=124.3
t=486  bpm=114.3
t=491  bpm=107.4
t=495  bpm=102.5
t=497  bpm=108.0  <-- starts oscillating
t=501  bpm=102.6
t=504  bpm=111.6
t=505  bpm=115.2
```

Example — Boundary #15 (chaotic):
```
t=540  bpm=100.7  <-- initial lock
t=548  bpm=106.4
t=552  bpm=96.8
t=557  bpm=88.5   <-- 18 BPM drop
t=560  bpm=127.7  <-- 39 BPM jump
t=562  bpm=118.8
t=567  bpm=122.4  <-- still wandering
```

## Root Causes (Code References)

1. **Initial estimate unreliable** — `min_frames = 3.0s` of onset data (`live.py:57`)
   is too short for autocorrelation to resolve BPM. First estimate is a coin flip.

2. **Short analysis window** — `window_seconds = 12.0` (`live.py:27`) means only 12s
   of onset data in the buffer. In a noisy bar environment, this produces jittery
   autocorrelation peaks.

3. **Inertia too weak** — `max_jump_bpm=6.0, confirm_updates=4` (`live.py:32-33`).
   At `min_update_interval=0.5s`, a bad BPM estimate only needs 2 seconds of
   consistency to override a good one. Jumps of 30–50 BPM pass through routinely.

4. **Noisy estimation blend** — `_estimate_bpm` (autocorrelation) and
   `_estimate_bpm_from_beats` (IOI) are blended 70/30 (`live.py:137-140`). Both
   methods are noisy on short windows, so blending doesn't help — it just averages
   two unreliable signals.
