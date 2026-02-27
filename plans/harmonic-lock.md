# Plan: Harmonic-Lock BPM Stabilization

## Problem

The BPM estimator frequently jumps between harmonic ratios of the true tempo — half-time (0.5x), double-time (2x), 2/3x, 3/2x — causing high within-song variance even when the underlying beat is consistent. From the 15-minute stability test:

- Song-003: stdev=28.8, range 80.8–198.8, 31% off-center
- Song-004: stdev=19.3, 36% off-center
- Overall: 8.8% at 2/3x ratio, 30% "other" harmonic confusion
- Multiple 30s windows flagged HIGH-VARIANCE (stdev >15)

The root cause is in `_snap_to_last()` (live.py:232–248): it tries multiple harmonic ratios but has no principled way to choose between them when the raw estimate is noisy. The `_normalize_bpm()` doubling/halving (live.py:223–230) adds further octave ambiguity. The 4-update inertia gate treats harmonic jumps the same as any other BPM change — but harmonic confusion is far more likely than a genuine 2x tempo change mid-song.

## Goals

1. Within-song stdev < 10 for constant-tempo songs
2. Off-center harmonic ratio samples < 5% per song
3. Genuine tempo changes (song transitions, DJ blend) still tracked within ~8 seconds
4. No regression on noise-rejection (bar/coffee shop scenarios)

## Architecture

Three layers, each independently testable:

```
Raw estimates ──► IOI Histogram ──► Harmonic Lock ──► Output BPM
(autocorr +       (vote on true     (resist ratio
 beat-spacing)     period)            jumps once
                                      locked)
```

### Layer 1: Inter-Onset Interval (IOI) Histogram

**What:** Replace the current "70% beat-spacing + 30% autocorrelation" fusion with an IOI histogram that finds the dominant beat period before converting to BPM.

**Why:** Working in the time domain (onset intervals in seconds) avoids the BPM-range normalization that introduces octave ambiguity. A histogram naturally clusters at the true period and its harmonics, making it easy to identify which cluster is strongest.

**How:**

- Maintain a rolling buffer of recent onset timestamps (last ~8 seconds, ~700 frames)
- On each detected onset (percussive or spectral), record its timestamp
- Compute pairwise intervals between recent onsets (within a max window of ~2 seconds to limit to reasonable BPM range)
- Bin intervals into a histogram (bin width ~5ms, covering 300ms–1500ms = 40–200 BPM)
- Find the top-3 peaks in the histogram
- The peaks will naturally cluster at the true period (T), 2T (half-time), T/2 (double-time), etc.
- Select the peak with the highest count as the raw period estimate
- Convert: `bpm = 60.0 / period`

**Key parameter:** `ioi_buffer_seconds = 8.0` — how much history to consider. Longer = more stable but slower to adapt.

**File:** New class `IOIHistogram` in `live.py` (alongside existing `SpectralBeatTemplate`).

**Tests:**
- Synthetic onsets at exact 120 BPM → histogram peak at 500ms
- Onsets with occasional half-time skips → still picks 500ms over 1000ms
- Tempo change from 120→140 BPM → histogram shifts within ~6 seconds
- Random noise onsets → no dominant peak, returns 0.0

### Layer 2: Harmonic Ratio Classifier

**What:** When the raw BPM estimate differs from `last_bpm` by a harmonic ratio, classify the relationship and score its confidence instead of blindly snapping.

**Why:** The current `_snap_to_last()` tries all ratios and picks the closest to `last_bpm`. This means if the raw estimate is noisy, it will always snap to *something* — even if the raw estimate is just wrong. We need a way to say "this looks like half-time confusion, reject it" vs "this is a genuine tempo change."

**How:**

Replace `_snap_to_last()` with `_classify_harmonic()`:

```python
HARMONIC_RATIOS = {
    "1x":   1.0,
    "1/2x": 0.5,
    "2x":   2.0,
    "2/3x": 2.0 / 3.0,
    "3/2x": 3.0 / 2.0,
    "3/4x": 0.75,
    "4/3x": 4.0 / 3.0,
}

def _classify_harmonic(self, raw_bpm: float) -> tuple[str, float]:
    """Return (ratio_label, mapped_bpm) for the best-matching harmonic."""
    if self.last_bpm <= 0:
        return ("1x", raw_bpm)

    best_label = "1x"
    best_mapped = raw_bpm
    best_error = abs(raw_bpm - self.last_bpm)

    for label, ratio in HARMONIC_RATIOS.items():
        mapped = raw_bpm / ratio  # what last_bpm would be if raw is at this ratio
        error = abs(mapped - self.last_bpm)
        if error < best_error:
            best_error = error
            best_label = label
            best_mapped = mapped

    return (best_label, best_mapped)
```

This replaces the ad-hoc ratio list in `_snap_to_last()` with a principled classifier that tells you *which* harmonic relationship the new estimate has with the locked BPM.

**Tests:**
- raw=80, last=160 → classified as "1/2x", mapped to 160
- raw=240, last=160 → classified as "3/2x", mapped to 160
- raw=158, last=160 → classified as "1x", mapped to 158
- raw=107, last=160 → classified as "2/3x", mapped to 160.5

### Layer 3: Harmonic-Resistant Lock

**What:** Once BPM is locked, require significantly more evidence to accept a harmonic ratio jump than a gradual drift.

**Why:** The current inertia gate requires 4 confirmations for any jump > 6 BPM. But a harmonic jump (80→160) is almost always confusion, while a gradual drift (140→146) is usually real. These need different thresholds.

**How:**

Add a `harmonic_confirm_count` parameter (default: 12, ~1.5 seconds at ~86 fps with ~8 raw estimates/sec) separate from the existing `confirm_updates` (4) for non-harmonic jumps.

```python
def _apply_inertia(self, bpm: float) -> float:
    label, mapped = self._classify_harmonic(bpm)

    if label == "1x":
        # Normal inertia: accept after 4 confirmations
        # (existing logic unchanged)
        ...
    else:
        # Harmonic jump: require 12 confirmations at the SAME ratio
        # Reset counter if ratio label changes
        if label != self._pending_harmonic_label:
            self._pending_harmonic_label = label
            self._harmonic_confirm = 0

        self._harmonic_confirm += 1

        if self._harmonic_confirm >= self.harmonic_confirm_count:
            # Genuine tempo change — accept the NEW bpm (not mapped)
            self._pending_harmonic_label = None
            self._harmonic_confirm = 0
            return bpm
        else:
            # Reject — return mapped (i.e., snap back to last_bpm's octave)
            return mapped
```

The key insight: when rejecting a harmonic jump, we return `mapped` (the raw estimate corrected back to the locked octave) rather than `last_bpm`. This means the BPM still tracks small variations within the correct octave.

When confirming a genuine harmonic change (e.g., actual double-time section), we accept the raw estimate after sustained evidence.

**File:** Modify `_apply_inertia()` in `LiveBpmEstimator` (live.py:250–269).

**Tests:**
- Locked at 140, receive 5 frames of raw=70 → stays at ~140 (mapped 1/2x back)
- Locked at 140, receive 15 frames of raw=70 → accepts 70 (genuine half-time section)
- Locked at 140, alternating raw=70 and raw=140 → stays at ~140 (ratio label keeps resetting)
- Locked at 140, gradual drift to 146 over 10 frames → follows normally (1x path)

### Layer 3b: Phase Coherence Tiebreaker (optional, implement if Layer 3 alone is insufficient)

**What:** When multiple harmonic ratios are plausible, score each by how well detected onsets align with a grid at that tempo.

**Why:** If the raw estimate wavers between 80 and 160, the correct one will have onsets landing on grid lines. Half-time will have onsets on every other grid line (50% coherence). Double-time will have extra grid lines with no onsets (also 50% coherence). The correct ratio has the highest coherence.

**How:**

```python
def _phase_coherence(self, candidate_bpm: float, onset_times: list[float]) -> float:
    """Score 0.0–1.0: how well onsets align with a grid at candidate_bpm."""
    if candidate_bpm <= 0 or len(onset_times) < 4:
        return 0.0
    period = 60.0 / candidate_bpm
    phases = [(t % period) / period for t in onset_times]
    # Rayleigh statistic: R = |mean(e^{2πi·phase})| / n
    import cmath
    z = sum(cmath.exp(2j * cmath.pi * p) for p in phases) / len(phases)
    return abs(z)
```

Rayleigh statistic: 1.0 = all onsets perfectly on-grid, 0.0 = uniformly distributed (random). Use this to break ties when the harmonic classifier is uncertain.

**Trigger:** Only compute when `_harmonic_confirm` is between 4 and 12 (wavering zone). If coherence for mapped > coherence for raw, reset the confirm counter.

## Implementation Order

1. **Layer 2: Harmonic Ratio Classifier** — Pure function, easy to test, no state changes. Write the classifier and unit tests first.
2. **Layer 3: Harmonic-Resistant Lock** — Modify `_apply_inertia()` to use the classifier. This is the core fix. Unit test with synthetic BPM sequences.
3. **Integration test** — Re-run the 15-minute stability test (test 10 from VALIDATION_TESTS.md) and compare analysis output.
4. **Layer 1: IOI Histogram** — Only if Layers 2+3 don't bring stdev under 10. This is a bigger change to the estimation pipeline.
5. **Layer 3b: Phase Coherence** — Only if harmonic-resistant lock alone still has edge cases.

## Files Changed

| File | Change |
|---|---|
| `src/dreamsync/live.py` | Add `_classify_harmonic()`, modify `_apply_inertia()`, add state vars `_pending_harmonic_label`, `_harmonic_confirm`, `harmonic_confirm_count` |
| `tests/test_live_bpm.py` | Add tests for harmonic classifier, harmonic-resistant lock, and synthetic BPM sequences |
| `scripts/analyze_bpm.py` | Already has harmonic ratio analysis — no changes needed |

Layer 1 (if needed):
| `src/dreamsync/live.py` | Add `IOIHistogram` class, wire into `LiveBpmEstimator.update()` |
| `tests/test_live_bpm.py` | Add IOI histogram unit tests |

## Parameters

| Parameter | Default | Rationale |
|---|---|---|
| `harmonic_confirm_count` | 12 | ~1.5s at typical update rate. Long enough to filter momentary confusion, short enough to track genuine double-time sections. |
| `ioi_buffer_seconds` | 8.0 | Covers ~2 bars at 60 BPM. Balances stability vs. responsiveness. |
| `ioi_bin_width_ms` | 5 | Resolves BPM differences of ~1 BPM at 120 BPM range. |
| `phase_coherence_min_onsets` | 4 | Need at least 4 onsets for meaningful Rayleigh statistic. |

## Success Criteria

Run the same 15-minute test (varied playlist, 3+ songs):
- Per-song stdev < 10 for all constant-tempo songs
- Off-center harmonic ratio < 5% per song
- 0 HIGH-VARIANCE 30s windows (stdev < 15) for constant-tempo sections
- Song transitions cause at most 1 HIGH-VARIANCE window during re-acquisition
- No regression: bar/coffee-shop noise test still rejects noise (confidence gate + template selectivity unchanged)
