# Bug 2 Proposed Fixes: BPM Post-Lock Instability

Four root causes identified, with a proposed fix for each.

---

## Fix 1: Extend minimum frames for initial estimate

**Problem**: `min_frames = 3.0s` produces unreliable first estimates (frequently 30–60
BPM off). The autocorrelation needs more data to resolve tempo.

**File**: `src/dreamsync/live.py`, `LiveBpmEstimator.__init__`

**Change**: Raise the minimum data requirement from 3.0s to 5.0s.

```python
# Before (line 57)
self.min_frames = max(8, int(3.0 * sample_rate / hop_size))

# After
self.min_frames = max(8, int(5.0 * sample_rate / hop_size))
```

**Tradeoff**: Lock-in delay goes from 3s to 5s. This is acceptable — 5 seconds of
silence/ambient after a song boundary is unnoticeable, and the first estimate will be
far more reliable. Currently the 3s lock produces values that are wrong for the next
5–10s anyway, so the effective lock-in is already ~10s of bad data.

---

## Fix 2: Widen the analysis window

**Problem**: `window_seconds = 12.0` limits the onset buffer to 12s of data. In a noisy
bar environment with crowd noise, system audio crosstalk, and variable dynamics, this
is too short for stable autocorrelation.

**File**: `src/dreamsync/live.py`, `LiveBpmEstimator.__init__`

**Change**: Increase the window from 12s to 20s.

```python
# Before (line 26)
window_seconds: float = 12.0,

# After
window_seconds: float = 20.0,
```

**Effect**: The onset buffer (`max_frames`) grows from ~1034 to ~1722 frames. This gives
the autocorrelation more data to work with, reducing noise. The adaptive beat detection
median window also benefits from more context.

**Tradeoff**: Slightly more memory (~2.7KB more for the deque), negligible CPU impact.
The autocorrelation in `_estimate_bpm` operates on the full buffer, but the cost is
still O(n log n) via numpy and well within the ~200Hz tick budget. However, the wider
window means BPM is slower to react to genuine tempo changes mid-song (takes ~10s to
flush old data instead of ~6s). This is fine — mid-song tempo changes are rare in DJ
sets, and stability matters more than reactivity.

---

## Fix 3: Strengthen inertia against large jumps

**Problem**: `max_jump_bpm=6.0` with `confirm_updates=4` at `min_update_interval=0.5s`
means a bad estimate only needs 2 seconds of consistency to override a good value.
Jumps of 30–50 BPM pass through routinely in the test data.

**File**: `src/dreamsync/live.py`, `LiveBpmEstimator.__init__` and `_apply_inertia`

**Changes**:

### 3a. Raise confirmation requirement

```python
# Before (line 33)
confirm_updates: int = 4,

# After
confirm_updates: int = 6,
```

This raises the confirmation window from 2s to 3s, making it harder for transient
noise to override a stable BPM.

### 3b. Scale confirmation by jump size

Replace the flat confirmation count with a jump-proportional requirement. Bigger jumps
need more proof.

```python
# Before
def _apply_inertia(self, bpm: float) -> float:
    if self.last_bpm <= 0.0:
        self._candidate_bpm = 0.0
        self._candidate_hits = 0
        return bpm
    if abs(bpm - self.last_bpm) <= self.max_jump_bpm:
        self._candidate_bpm = 0.0
        self._candidate_hits = 0
        return bpm
    if self._candidate_bpm <= 0.0 or abs(bpm - self._candidate_bpm) > self.max_jump_bpm:
        self._candidate_bpm = bpm
        self._candidate_hits = 1
        return self.last_bpm
    self._candidate_hits += 1
    if self._candidate_hits >= self.confirm_updates:
        self._candidate_bpm = 0.0
        self._candidate_hits = 0
        return bpm
    return self.last_bpm

# After
def _apply_inertia(self, bpm: float) -> float:
    if self.last_bpm <= 0.0:
        self._candidate_bpm = 0.0
        self._candidate_hits = 0
        return bpm
    if abs(bpm - self.last_bpm) <= self.max_jump_bpm:
        self._candidate_bpm = 0.0
        self._candidate_hits = 0
        return bpm
    if self._candidate_bpm <= 0.0 or abs(bpm - self._candidate_bpm) > self.max_jump_bpm:
        self._candidate_bpm = bpm
        self._candidate_hits = 1
        return self.last_bpm
    self._candidate_hits += 1
    # Scale confirmation requirement by jump magnitude:
    # 6 BPM jump → base confirms, 30+ BPM jump → 2x confirms
    jump_ratio = min(2.0, abs(bpm - self.last_bpm) / (self.max_jump_bpm * 3))
    required = int(self.confirm_updates * (1.0 + jump_ratio))
    if self._candidate_hits >= required:
        self._candidate_bpm = 0.0
        self._candidate_hits = 0
        return bpm
    return self.last_bpm
```

**Effect on test data**: The 30–50 BPM jumps that currently pass with 4 confirmations
(2s) would now need 8–12 confirmations (4–6s). Genuine tempo changes (new song) still
get through but noisy autocorrelation spikes get filtered.

---

## Fix 4: Weight the estimation blend by confidence

**Problem**: Autocorrelation (`_estimate_bpm`) and inter-onset interval
(`_estimate_bpm_from_beats`) are blended at a fixed 70/30 ratio. When both are noisy,
the blend just averages two bad signals.

**File**: `src/dreamsync/live.py`, `LiveBpmEstimator.update`

**Change**: Weight the blend by each estimator's confidence (number of beats detected
and strength of autocorrelation peak).

```python
# Before (lines 133-140)
bpm_from_beats = _estimate_bpm_from_beats(
    beat_idx, self.hop_size, self.sample_rate
)
bpm_from_corr = _estimate_bpm(onset_arr, self.hop_size, self.sample_rate)
if bpm_from_beats > 0 and bpm_from_corr > 0:
    bpm = 0.7 * bpm_from_beats + 0.3 * bpm_from_corr
else:
    bpm = bpm_from_beats if bpm_from_beats > 0 else bpm_from_corr

# After
bpm_from_beats = _estimate_bpm_from_beats(
    beat_idx, self.hop_size, self.sample_rate
)
bpm_from_corr = _estimate_bpm(onset_arr, self.hop_size, self.sample_rate)
if bpm_from_beats > 0 and bpm_from_corr > 0:
    # Weight beat-based estimate by number of detected beats.
    # Fewer than 4 beats → low confidence, lean on autocorrelation.
    # 8+ beats → high confidence, trust beat intervals.
    beat_confidence = min(1.0, beat_idx.size / 8.0)
    w_beats = 0.5 + 0.4 * beat_confidence   # 0.5–0.9
    w_corr = 1.0 - w_beats                   # 0.1–0.5
    bpm = w_beats * bpm_from_beats + w_corr * bpm_from_corr
else:
    bpm = bpm_from_beats if bpm_from_beats > 0 else bpm_from_corr
```

**Effect**: With a nearly-full 20s window containing 30+ beats, `beat_confidence` → 1.0
and the blend is 90/10 in favor of the more reliable IOI method. Right after reset
with only 4–6 beats detected, the blend is ~75/25, leaning more on autocorrelation
which does better with sparse data.

---

## Implementation Priority

1. **Fix 3** (strengthen inertia) — Highest impact, lowest risk. Directly prevents the
   30–50 BPM jumps visible in every boundary. No effect on lock-in time.

2. **Fix 2** (widen window) — High impact. More data → more stable autocorrelation.
   Minimal downside.

3. **Fix 1** (extend min_frames) — Medium impact. Trades 2s more lock-in delay for a
   much better first estimate. Worth it given the current first estimate is wrong
   anyway.

4. **Fix 4** (confidence-weighted blend) — Lower impact but improves quality at the
   margins. The blend weights only matter when both estimators disagree, which is
   common but the effect is smaller than the other fixes.

## Testing

After applying fixes, re-run the bar test and compare:
- Distinct BPM values per 30s post-boundary window (target: <5, currently avg 11.3)
- Jumps >3 BPM per window (target: <3, currently avg 8.2)
- Max single-jump magnitude (target: <15 BPM, currently 30–50)
- Lock-in delay (expected: 5s with Fix 1, acceptable)
