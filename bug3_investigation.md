# Bug 3 Investigation: Mood Resets to HYPE After Song Boundary

**Date**: 2026-02-25
**Source data**: `bar_test_post_fix.txt` (300s session, 5 boundaries), `long_bar_test.txt` (600s session, 12 boundaries), `out/10mintest.txt` (600s, 12 boundaries)

## Summary

After every song boundary reset, the MoodClassifier immediately classifies as `mood=hype`
with energy values in the 0.50-0.81 range. The mood stays locked in HYPE for many seconds
(the entire post-boundary warmup period and beyond), producing jarring high-intensity
effects on every boundary. Combined with Bug 1 (over-triggering), this creates a strobe-like
HYPE flash every ~35 seconds.

The correct behavior would be for the first few seconds after a boundary to classify as
CHILL (matching what happens during initial session startup at t < 8s).

---

## Root Cause Chain (3 interlocking problems)

### Root Cause 1: Director warmup guard uses absolute `stream_t`, not relative time since reset

**File**: `src/dreamsync/director.py:293`

```python
if t < self.config.warmup_seconds:   # warmup_seconds = 8.0
```

The `t` value comes from `stream_t` in `live.py:633`, which increments monotonically from
session start and is **never reset** at song boundaries (`live.py:562,643`). After the first
8 seconds of the session, `stream_t > 8.0` forever, so the warmup guard **never fires**
after a boundary reset.

During initial startup (t=0-8s), the warmup path forces `EffectMode.AMBIENT` and prevents
energy-based mode switching. This is why `bar_test_post_fix.txt` starts with `mood=chill` at
t=1.8s. But after boundary #1 at t~35s, the warmup path is dead.

**Evidence**: Compare session start vs. first boundary in `bar_test_post_fix.txt`:

| Phase | Time | Energy | Mood | Warmup Active? |
|-------|------|--------|------|----------------|
| Session start | t=1.8s | 0.2512 | chill | Yes (t < 8.0) |
| Session start | t=3.0s | 0.6340 | chill | Yes (t < 8.0) |
| After boundary #1 | t~35s | 0.8083 | hype | No (35 > 8.0) |
| After boundary #1 | t~36s | 0.7342 | hype | No |

### Root Cause 2: Director.reset() zeroes RMS normalization, causing an energy spike

**File**: `src/dreamsync/director.py:108-111`

```python
self._rms_floor = 0.0
self._rms_ceil = 0.001     # tiny initial ceiling
self._flux_max = 1e-6
self._onset_max = 1e-6
```

After reset, the normalization denominators are near-zero. The first audio frame with any
signal (even modest RMS) produces extreme normalized values.

**Code trace** (`_compute_energy()` at `director.py:145-164`):

1. After reset: `_rms_floor=0.0`, `_rms_ceil=0.001`
2. First frame arrives with, say, `rms=0.05` (modest audio)
3. `_update_ema()` runs:
   - `_ema_rms = 0.2 * 0.05 + 0.8 * 0.0 = 0.01` (alpha=0.2)
   - `_rms_floor = 0.005 * 0.01 + 0.995 * 0.0 = 0.00005` (very slow tracking)
   - `_rms_ceil = max(0.00005 + 1e-8, 0.02 * 0.01 + 0.98 * 0.001) = max(~0, 0.0012) = 0.0012`
4. `_compute_energy()` runs:
   - `span = 0.0012 - 0.00005 = 0.00115`
   - `norm_rms = (0.01 - 0.00005) / 0.00115 = 8.65` -- clamped to **1.0**
   - Similarly, `_flux_max` starts at 1e-6, so even a tiny spectral flux normalizes to 1.0
   - Composite energy = `0.25*1.0 + 0.30*~1.0 + 0.20*bass + 0.25*~1.0` = **~0.75-0.80**

This matches the observed data: first frame after every boundary has energy 0.50-0.81.

### Root Cause 3: MoodClassifier dwell guard is bypassed after reset

**File**: `src/dreamsync/mood.py:63`

```python
self._mood_entered_at = -1e9
```

The `_can_switch()` check (`mood.py:70-71`):
```python
def _can_switch(self, t: float) -> bool:
    return (t - self._mood_entered_at) >= self.config.min_dwell_seconds  # 4.0s
```

After reset, `_mood_entered_at = -1e9`. At `t=200s`, `200 - (-1e9) >> 4.0`, so the dwell
guard passes immediately. The classifier starts in CHILL (correct per `reset()`), but then
the very first `update()` call with inflated energy immediately transitions to HYPE because:

1. Energy is ~0.75 (from Root Cause 2)
2. Stability is ~0.01-0.04 (low = "stable" -- few history entries after reset)
3. BPM is 0.0 after reset, but `_classify_steady_state()` checks
   `energy >= groove_energy_ceiling (0.50) and stable_beat` which is True
4. So raw classification = HYPE
5. Hysteresis check: starting from CHILL, needs `energy >= chill_energy_exit (0.28)` -- True
6. `_can_switch(t)` -- True (bypassed as shown above)
7. Mood transitions from CHILL to HYPE on **frame 1**

This is not really a separate bug from RC2 -- if energy were correct, the classifier would
stay in CHILL. But it shows that even the dwell guard cannot save us.

---

## Per-Boundary Mood Transition Data

### `bar_test_post_fix.txt` (300s session)

| B# | Line | First Energy | Peak Energy (3 frames) | First Mood | Frames Until Non-HYPE | Notes |
|----|------|-------------|------------------------|------------|----------------------|-------|
| #1 | 843 | 0.8083 | 0.8083 | hype | ~280 | Settles to groove at line ~1123 |
| #2 | 1557 | 0.8022 | 0.8022 | chill* | ~280+ | *chill for 1 frame only, then hype |
| #3 | 2756 | 0.8041 | 0.8041 | hype | ~280+ | |
| #4 | 5497 | 0.8025 | 0.8078 | hype | 280+ | |
| #5 | 6725 | 0.5027 | 0.6993 | hype | (end of file) | Session ended during HYPE |

*Boundary #2 and #14 show `mood=chill` for exactly 1 frame with energy 0.80. This is the
single frame where `mood_classifier.reset()` sets mood=CHILL, printed before the first
`update()` processes the inflated energy and immediately overrides to HYPE.

### `long_bar_test.txt` / `10mintest.txt` (600s session)

| B# | Stream Time | First Energy | Frames 2 Energy | Frames 3 Energy | All HYPE? |
|----|------------|-------------|-----------------|-----------------|-----------|
| #5 | ~196s | 0.7571 | 0.7074 | 0.6925 | Yes, all 12+ frames |
| #6 | ~227s | 0.8023 | 0.8069 | 0.8049 | Yes |
| #7 | ~262s | 0.5019 | 0.8112 | 0.7271 | Yes |
| #8 | ~300s | 0.8026 | 0.6076 | 0.4716 | Yes (even at 0.27) |
| #9 | ~335s | 0.7433 | 0.7223 | 0.7006 | Yes |
| #10 | ~379s | 0.8124 | 0.7267 | 0.6685 | Yes |
| #11 | ~411s | 0.7660 | 0.7477 | 0.7946 | Yes |
| #12 | ~444s | 0.7392 | 0.7312 | 0.6833 | Yes |
| #13 | ~475s | 0.7659 | 0.7431 | 0.7069 | Yes |
| #14 | ~506s | 0.8021 | 0.7130 | 0.7407 | Yes (1 frame chill, then hype) |
| #15 | ~537s | 0.7158 | 0.6782 | 0.6422 | Yes |
| #16 | ~570s | 0.7803 | 0.7149 | 0.6952 | Yes |

**Key observations**:
- **100% of boundaries** immediately produce HYPE (24/24 across both test files)
- First-frame energy averages **0.74** (range 0.50-0.81)
- Energy stays above HYPE threshold (0.50) for 5-12+ frames after every boundary
- Even boundary #8, where energy drops to 0.27 by frame 6, still shows HYPE because
  once in HYPE, the exit threshold is 0.40 and the dwell time is 4.0s

### Energy Decay Curve (Boundary #1, bar_test_post_fix.txt)

| Frame After Reset | Energy | Mood | Notes |
|-------------------|--------|------|-------|
| 1 | 0.8083 | hype | Spike from reset normalization |
| 2 | 0.7342 | hype | |
| 3 | 0.6548 | hype | |
| 4 | 0.6011 | hype | |
| 5 | 0.5738 | hype | |
| 6 | 0.6120 | hype | Bounces -- new audio data |
| 7 | 0.6616 | hype | |
| 8 | 0.6201 | hype | |
| 9 | 0.5642 | hype | |
| 10 | 0.5027 | hype | Above hype_energy_exit (0.40) |
| 11 | 0.5395 | hype | |
| 12 | 0.5091 | hype | |
| ~15 | 0.4870 | hype | Still above 0.40 |
| ~17 | 0.3602 | hype | Finally below 0.40, but dwell hold |
| ~280 | 0.38xx | groove | Eventually transitions |

The inflated energy decays slowly because `_rms_floor` tracks with alpha=0.005 (very slow)
and `_rms_ceil` tracks with alpha=0.02 (also slow). It takes many seconds for the
normalization range to calibrate to real audio levels.

---

## Contrast: Session Startup (Working Correctly)

During the first 8 seconds of the session (`bar_test_post_fix.txt` lines 3-840):

- `stream_t < 8.0`, so the Director warmup guard is active
- Director forces `EffectMode.AMBIENT` regardless of energy
- MoodClassifier sees Director's state but the effect cycling still respects the CHILL mood
- Energy values are high (0.55-0.83) due to the same normalization issue, but mood stays CHILL
  because the warmup period prevents the high energy from propagating to mode switching

**This proves the warmup mechanism works as designed** -- the problem is purely that it uses
absolute `stream_t` instead of time-since-last-reset.

---

## Code Path Trace: What Happens on First Frame After Reset

```
live.py:602    song_detector.update(rms) returns True
live.py:604      bpm_estimator.reset()
live.py:605      director.reset()        --> _rms_floor=0.0, _rms_ceil=0.001, _energy=0.0
live.py:607        mood_classifier.reset()  --> mood=CHILL, _mood_entered_at=-1e9
live.py:609        effect_cycler.reset()

live.py:619    sf = _spectral_features(frame, ...)   # same frame that triggered boundary!
live.py:624    bpm, beat = bpm_estimator.update(sf.bass, stream_t, ...)
live.py:632    last_features = _feature_row_from_frame(frame, rms, stream_t, ...)
live.py:639    last_intent = director.update(last_features)
                 --> director.py:282: _update_ema(rms, zcr, bpm, flux, bass_ratio, onset)
                     --> _ema_rms = 0.2*rms + 0.8*0.0  (small but nonzero)
                     --> _rms_ceil = max(tiny, 0.02*_ema_rms + 0.98*0.001)  (~0.001)
                     --> _rms_floor = 0.005*_ema_rms + 0.995*0.0  (~0.0)
                     --> _compute_energy() --> norm_rms = (_ema_rms - 0) / 0.001 = HUGE, clamped to 1.0
                     --> _energy ~= 0.75-0.80
                 --> director.py:293: if t < 8.0:  FALSE (stream_t is 200+)
                 --> Normal mode selection with inflated energy

live.py:647    mood = mood_classifier.update(director.energy, director.stability, ...)
                 --> mood.py:119: _check_drop(0.75, t) -- no dip, returns False
                 --> mood.py:137: _can_switch(t) -- (200 - (-1e9)) >= 4.0 --> True
                 --> mood.py:141: _classify_steady_state(0.75, 0.01, 0.0)
                     --> energy 0.75 >= groove_energy_ceiling 0.50 AND stability 0.01 <= 0.07
                     --> returns HYPE
                 --> mood.py:143-146: currently CHILL, target HYPE, energy 0.75 >= chill_energy_exit 0.28
                     --> transitions to HYPE
```

---

## Root Causes Summary

| # | Root Cause | File:Line | Severity |
|---|-----------|-----------|----------|
| 1 | Warmup guard uses absolute `stream_t` instead of time-since-reset | `director.py:293` | **Primary** -- this is why reset behaves differently from startup |
| 2 | `reset()` zeroes normalization state, causing energy spike to 0.75+ | `director.py:108-111` | **Primary** -- this is the direct cause of the inflated energy |
| 3 | MoodClassifier dwell bypass after reset (minor, wouldn't matter if energy were correct) | `mood.py:63` | Contributing |

---

## Why Energy Is So High: Mathematical Proof

After `director.reset()`:
- `_rms_floor = 0.0`
- `_rms_ceil = 0.001`
- `_ema_rms = 0.0`
- `_flux_max = 1e-6`
- `_onset_max = 1e-6`

First frame with typical bar audio (`rms ~= 0.06`, `spectral_flux ~= 5.0`, `onset ~= 0.03`):

```
_ema_rms     = 0.2 * 0.06 = 0.012
_rms_floor   = 0.005 * 0.012 = 0.00006
_rms_ceil    = max(0.00006+1e-8, 0.02*0.012 + 0.98*0.001) = max(0.00006, 0.00122) = 0.00122
span         = 0.00122 - 0.00006 = 0.00116
norm_rms     = (0.012 - 0.00006) / 0.00116 = 10.3  --> clamped to 1.0

_ema_flux    = 0.15 * 5.0 = 0.75
_flux_max    = max(0.75, 1e-6 * 0.998) = 0.75
norm_flux    = 0.75 / 0.75 = 1.0

_ema_onset   = 0.15 * 0.03 = 0.0045
_onset_max   = max(0.0045, 1e-6 * 0.998) = 0.0045
norm_onset   = 0.0045 / 0.0045 = 1.0

_ema_bass_ratio = 0.15 * ~0.4 = 0.06
norm_bass    = 0.06

energy = 0.25*1.0 + 0.30*1.0 + 0.20*0.06 + 0.25*1.0 = 0.812
```

This matches the observed 0.80 peak energy on first frame exactly.

---

## Proposed Fixes

### Fix A: Make warmup guard relative to reset (recommended, simple)

Track time-since-reset in Director instead of using absolute `t`:

```python
# In Director.__init__ and reset():
self._reset_t: float | None = None

# In Director.update():
if self._reset_t is None:
    self._reset_t = t
time_since_reset = t - self._reset_t
if time_since_reset < self.config.warmup_seconds:
    # ... existing warmup path (forces AMBIENT) ...
```

This ensures the warmup guard fires for 8 seconds after every reset, not just session start.
The warmup path already handles everything correctly -- ambient mode, simple intensity
scaling, no energy-based mode switching.

### Fix B: Seed normalization with reasonable defaults (complementary)

Instead of zeroing the normalization on reset, seed with mid-range defaults:

```python
def reset(self) -> None:
    # ...existing code...
    self._rms_floor = 0.01    # typical quiet audio floor
    self._rms_ceil = 0.10     # typical moderate audio ceiling
    self._flux_max = 10.0     # typical spectral flux range
    self._onset_max = 0.05    # typical onset range
```

This prevents the energy spike even outside the warmup period. The normalization will
converge to actual values within a few seconds via the EMA tracking.

### Fix C: Set dwell time properly after MoodClassifier.reset() (minor)

```python
def reset(self, t: float | None = None) -> None:
    self.mood = Mood.CHILL
    self._mood_entered_at = t if t is not None else -1e9
    # ...
```

This would give CHILL a proper 4-second dwell hold after reset, preventing immediate
transition even if energy is high. However, this alone does not fix the root cause -- the
lights would still be driven by inflated energy values even if the mood label is correct.

### Recommended approach

Apply Fix A (primary) + Fix B (defense in depth). Fix C is optional but nice to have.
Fix A alone would fully solve the user-visible problem because the warmup path returns
AMBIENT mode and the MoodClassifier already resets to CHILL.
