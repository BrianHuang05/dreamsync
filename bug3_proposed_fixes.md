# Bug 3 Proposed Fixes: Mood Resets to HYPE After Song Boundary

Three root causes identified, with a proposed fix for each.

---

## Fix 1: Make warmup guard relative to reset

**Problem**: The Director warmup guard at `director.py:293` uses
`if t < self.config.warmup_seconds` which checks absolute `stream_t`. After the
first 8 seconds of the session, `stream_t > 8.0` forever, so the warmup guard
**never fires** after a boundary reset. During initial startup (t=0-8s) the guard
correctly forces `EffectMode.AMBIENT` and prevents energy-based mode switching,
but after boundary #1 at t~35s the warmup path is dead. This is why session
startup shows `mood=chill` but every boundary immediately shows `mood=hype`.

**File**: `src/dreamsync/director.py`, `Director.__init__`, `Director.reset`,
`Director.update`

**Changes**:

### 1a. Track reset time in `__init__`

```python
# Before (line 75)
self._last_t: float | None = None

# After
self._last_t: float | None = None
self._reset_t: float | None = None
```

### 1b. Clear reset time in `reset()`

```python
# Before (line 101)
self._last_t = None

# After
self._last_t = None
self._reset_t = None
```

### 1c. Use relative time in the warmup guard in `update()`

```python
# Before (line 293)
if t < self.config.warmup_seconds:

# After
if self._reset_t is None:
    self._reset_t = t
time_since_reset = t - self._reset_t
if time_since_reset < self.config.warmup_seconds:
```

**Effect**: The warmup guard now fires for 8 seconds after every reset, not just
session start. During those 8 seconds, the Director forces `EffectMode.AMBIENT`
with simple RMS-scaled intensity -- exactly matching the behavior during initial
session startup. This is the primary fix: even with inflated energy, the warmup
path prevents energy-based mode switching, so the MoodClassifier stays in CHILL
(its reset default) throughout the warmup period.

**Tradeoff**: None. The warmup path already exists and works correctly at session
start. This fix just makes it also work after boundary resets, which is the
original design intent.

---

## Fix 2: Seed normalization with reasonable defaults

**Problem**: `Director.reset()` zeroes the normalization state:
`_rms_floor=0.0`, `_rms_ceil=0.001`, `_flux_max=1e-6`, `_onset_max=1e-6`. The
first audio frame after reset with typical bar audio (`rms~0.06`,
`spectral_flux~5.0`, `onset~0.03`) normalizes to ~0.80 energy because the
denominators are near-zero. Mathematical proof from the investigation:

```
After reset: _rms_floor=0.0, _rms_ceil=0.001
First frame:
  _ema_rms     = 0.2 * 0.06 = 0.012
  _rms_ceil    = max(tiny, 0.02*0.012 + 0.98*0.001) = 0.00122
  span         = 0.00122 - 0.00005 = 0.00116
  norm_rms     = (0.012 - 0.00005) / 0.00116 = 10.3  --> clamped to 1.0
  norm_flux    = 1.0 (same near-zero denominator issue)
  norm_onset   = 1.0 (same)
  energy       = 0.25*1.0 + 0.30*1.0 + 0.20*0.06 + 0.25*1.0 = 0.812
```

This matches the observed 0.80 peak energy on first frame after every boundary.

**File**: `src/dreamsync/director.py`, `Director.reset`

**Change**: Seed with mid-range defaults instead of near-zero values.

```python
# Before (lines 108-111)
self._rms_floor = 0.0
self._rms_ceil = 0.001
self._flux_max = 1e-6
self._onset_max = 1e-6

# After
self._rms_floor = 0.01     # typical quiet audio floor
self._rms_ceil = 0.10      # typical moderate audio ceiling
self._flux_max = 10.0      # typical spectral flux range
self._onset_max = 0.05     # typical onset range
```

**Effect**: With these seeds, the first frame after reset normalizes sensibly
instead of spiking to 1.0. Example with the same bar audio:

```
After reset (with fix): _rms_floor=0.01, _rms_ceil=0.10
First frame:
  _ema_rms     = 0.2 * 0.06 = 0.012
  span         = 0.10 - 0.01 = 0.09
  norm_rms     = (0.012 - 0.01) / 0.09 = 0.022  (reasonable, not clamped)
  norm_flux    = 0.75 / 10.0 = 0.075   (reasonable)
  norm_onset   = 0.0045 / 0.05 = 0.09  (reasonable)
  energy       ~ 0.04                   (correctly low)
```

The normalization converges to actual audio levels within a few seconds via the
EMA tracking. This prevents the energy spike even outside the warmup period,
providing defense in depth.

**Tradeoff**: If the new song has dramatically different levels than the seed
defaults (e.g., much louder), the normalization will start slightly off but
converge within 2-3 seconds. This is strictly better than the current behavior
where normalization starts maximally wrong (0.80 energy) and takes 15+ seconds
to converge.

---

## Fix 3: Set dwell time properly after MoodClassifier reset

**Problem**: `MoodClassifier.reset()` sets `_mood_entered_at = -1e9`. The
`_can_switch()` check computes `(t - (-1e9)) >= 4.0` which is always True, so
the 4-second dwell guard is bypassed immediately after reset. The classifier
starts in CHILL (correct), but the very first `update()` call can transition to
HYPE because the dwell guard offers no protection.

**File**: `src/dreamsync/mood.py`, `MoodClassifier.reset`; `src/dreamsync/live.py`
line 607

**Changes**:

### 3a. Accept `t` parameter in `reset()`

```python
# Before (line 60)
def reset(self) -> None:
    """Clear accumulated state for a new song."""
    self.mood = Mood.CHILL
    self._mood_entered_at = -1e9

# After
def reset(self, t: float | None = None) -> None:
    """Clear accumulated state for a new song."""
    self.mood = Mood.CHILL
    self._mood_entered_at = t if t is not None else -1e9
```

### 3b. Pass `stream_t` at the call site in `live.py`

```python
# Before (line 607)
                        mood_classifier.reset()

# After
                        mood_classifier.reset(stream_t)
```

**Effect**: After reset, `_mood_entered_at = stream_t`, so `_can_switch()` returns
False for 4 seconds. Even if energy is inflated, the mood cannot switch from
CHILL for a full dwell period. This is a safety net: even if Fixes 1 and 2 were
not applied, the mood label would stay CHILL for 4 seconds.

**Tradeoff**: The `t` parameter is optional with a default of `None`, so existing
callers (tests, other code paths) continue to work without changes. The only
behavioral change is that the call site in `live.py` now passes the current
timestamp, giving CHILL a proper dwell hold.

---

## Implementation Priority

1. **Fix 1** (relative warmup guard) -- Highest impact, primary fix. This single
   change makes boundary resets behave identically to session startup. The warmup
   path already exists and handles everything correctly (AMBIENT mode, simple
   intensity scaling, no energy-based mode switching). Zero risk.

2. **Fix 2** (seed normalization defaults) -- High impact, defense in depth.
   Directly eliminates the 0.80 energy spike that is the root cause of the HYPE
   classification. Even if the warmup guard were somehow bypassed, energy would
   be correct. Low risk.

3. **Fix 3** (dwell time after reset) -- Minor, nice-to-have. A safety net that
   prevents mood switching for 4 seconds after reset regardless of energy values.
   Would not fix the user-visible problem alone (lights would still be driven by
   inflated energy even if mood label is correct), but adds another layer of
   protection.

## Testing

After applying fixes, re-run the bar test (5-10 minute session with multiple
song boundaries) and compare per-boundary mood data:

| Metric | Before (all fixes) | Target |
|---|---|---|
| Boundaries with immediate HYPE | 24/24 (100%) | 0/24 (0%) |
| First-frame energy after boundary | avg 0.74 (range 0.50-0.81) | <0.20 |
| Frames until non-HYPE after boundary | avg ~280 | N/A (should never enter HYPE) |
| First mood after boundary | HYPE (100%) | CHILL (100%) |
| Seconds in CHILL after boundary | 0 (immediate override) | >=8 (warmup period) |

**Verification steps**:

1. **Warmup guard active**: After each boundary, log output should show
   `mode=ambient` for the first 8 seconds (matching session startup behavior).

2. **Energy normalization**: First-frame energy after boundary should be <0.20,
   not 0.50-0.81. Energy should rise gradually as normalization calibrates to
   actual audio levels.

3. **Mood stability**: Mood should stay `chill` for at least 8 seconds after
   each boundary. Transition to `groove` or `hype` should only happen after
   warmup expires AND energy genuinely warrants it.

4. **No regression at session start**: The first 8 seconds of a new session
   should behave identically to before (ambient mode, chill mood, gradual
   energy ramp).
