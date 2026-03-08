# Plan: Issue 1 — Beat Alignment & Timing

Priority: P0 | Effort: High | Affects: every song, cascades into color cycling and pulse timing

---

## Deliverable 1: Onset-Based Beat Times (Hybrid Grid)

### Prerequisites
- Read `src/dreamsync/analyzer/bpm.py` — `GlobalBpmEstimator`, `_build_beat_grid()`, `_beat_alignment_score()`
- Read `src/dreamsync/analyzer/features.py` — `OfflineFeaturePipeline.extract()`, `FeatureRow.beat`
- Read `src/dreamsync/analyzer/bpm.py:BeatGrid` dataclass

### Problem
The current `_build_beat_grid()` generates a perfectly even grid at the global BPM, phase-aligned by testing 100 candidate offsets. Real music has micro-timing, syncopation, and rubato. The grid is off by 20-50ms on many beats, causing every downstream beat-triggered event (pulse, color cycle, cue transition) to misfire.

The phase search tests `n_candidates=100` offsets uniformly across one beat period. At 120 BPM (500ms period), that's 5ms resolution — adequate in theory, but the scoring uses a 15% tolerance window (75ms at 120 BPM), which means many different offsets score similarly and the "best" may not be the true phase.

### Solution

Replace the purely mathematical grid with a **hybrid onset-anchored grid**:

1. **Collect detected onset times** from `FeatureRow.beat == True` (the `PercussiveOnsetTracker` already fires these during feature extraction).

2. **Cluster onsets into beat candidates**: For each detected onset, check if it's within `±beat_period * 0.20` of a grid position. If yes, replace that grid position with the onset time. If no nearby grid position exists, consider it an off-beat event and skip it.

3. **New method `_build_hybrid_beat_grid()`**:
   ```python
   def _build_hybrid_beat_grid(
       self, features: list[FeatureRow], bpm: float,
   ) -> BeatGrid:
       """Build a beat grid anchored to detected onsets where available."""
       # Step 1: Build the base even grid (existing logic)
       base_grid = self._build_even_grid(bpm, duration)

       # Step 2: Collect detected onsets
       detected = [f.t for f in features if f.beat and f.t > self.warmup_skip_seconds]

       # Step 3: For each grid beat, snap to nearest detected onset if within tolerance
       snap_tolerance = (60.0 / bpm) * 0.20  # 20% of beat period
       hybrid_beats = []
       for grid_t in base_grid:
           # Find nearest onset
           best_onset = None
           best_dist = float('inf')
           for onset_t in detected:
               dist = abs(onset_t - grid_t)
               if dist < best_dist:
                   best_dist = dist
                   best_onset = onset_t
           if best_onset is not None and best_dist <= snap_tolerance:
               hybrid_beats.append(best_onset)
           else:
               hybrid_beats.append(grid_t)  # keep grid position as fallback

       # Step 4: Monotonicity enforcement — ensure beats are strictly increasing
       # (snapping can occasionally swap adjacent beats)
       for i in range(1, len(hybrid_beats)):
           if hybrid_beats[i] <= hybrid_beats[i-1]:
               hybrid_beats[i] = hybrid_beats[i-1] + 0.001

       return BeatGrid(bpm=bpm, beat_times=tuple(hybrid_beats), ...)
   ```

4. **Rename existing `_build_beat_grid` → `_build_even_grid`** and call it as a subroutine of the new hybrid method.

5. **Update `estimate()`** to call `_build_hybrid_beat_grid()` instead of `_build_beat_grid()`.

### Files to Modify
- `src/dreamsync/analyzer/bpm.py` — add `_build_hybrid_beat_grid()`, rename `_build_beat_grid` → `_build_even_grid`

### Unit Tests
- `python -m pytest dev/tests/test_analyzer_bpm.py -v`
- New tests:
  - `test_hybrid_grid_snaps_to_onsets` — synthetic features with beat=True at known times; verify grid times shift toward onsets
  - `test_hybrid_grid_falls_back_to_even` — features with no beats; verify output matches even grid
  - `test_hybrid_grid_monotonicity` — onsets that would cause non-monotonic beats are corrected
  - `test_hybrid_grid_preserves_count` — same number of beats as even grid

### Completion Criteria
- `_build_hybrid_beat_grid()` produces a grid where onset-matched beats are within 5ms of the detected onset
- All existing `test_analyzer_bpm.py` tests pass unchanged (backward compatibility)
- New tests pass

---

## Deliverable 2: Increase Phase Search Resolution

### Prerequisites
- Deliverable 1 merged (hybrid grid uses even grid as base)

### Problem
The base even grid's phase search uses only 100 candidates. At 120 BPM (500ms beat period), this is 5ms resolution — but the scoring's 15% tolerance window means multiple candidates tie, and the "best" may be 2-3ms off the true phase. This compounds across 200+ beats in a typical song.

### Solution

1. **Two-pass phase search** in `_build_even_grid()`:
   - Pass 1: coarse scan with `n_candidates=100` (existing behavior) → find approximate offset
   - Pass 2: fine scan with `n_candidates=50` in a ±10% window around the coarse result → sub-millisecond resolution

   ```python
   # Pass 1: coarse
   coarse_offset, _ = self._scan_offsets(beat_period, detected_beats, duration, n=100, lo=0.0, hi=beat_period)

   # Pass 2: fine — search ±10% of beat_period around coarse result
   fine_window = beat_period * 0.10
   fine_lo = max(0.0, coarse_offset - fine_window)
   fine_hi = coarse_offset + fine_window
   best_offset, _ = self._scan_offsets(beat_period, detected_beats, duration, n=50, lo=fine_lo, hi=fine_hi)
   ```

2. **Extract `_scan_offsets()` helper** from the current inline loop to avoid duplication.

### Files to Modify
- `src/dreamsync/analyzer/bpm.py` — refactor phase search in `_build_even_grid()` (renamed from `_build_beat_grid`), add `_scan_offsets()`

### Unit Tests
- `python -m pytest dev/tests/test_analyzer_bpm.py -v`
- New test: `test_two_pass_phase_more_accurate` — synthetic onsets at exact 500ms intervals with phase=37ms; verify detected offset is within 1ms of 37ms (old code finds ~35 or ~40ms)

### Completion Criteria
- Phase accuracy improves from ~5ms to <1ms on synthetic signals
- No performance regression (two-pass adds <50 candidates total, negligible cost)

---

## Deliverable 3: Widen Beat Tolerance in Playback Runtime

### Prerequisites
- None (independent of Deliverables 1-2, can be done in parallel)

### Problem
`ShowPlaybackRuntime.__init__()` sets `self._beat_tolerance = min(0.025, beat_interval * 0.25)`. At 120 BPM (500ms), this is 25ms. The 200Hz tick loop (5ms sleep) means only 5 ticks fall inside the tolerance window. If the tick loop jitters by even 3ms (common on Windows due to timer resolution), a beat can be missed entirely.

Additionally, `ShowTimeline.is_beat()` uses the same hard 25ms tolerance (default parameter). A beat 26ms away is completely invisible to the runtime.

### Solution

1. **Increase default tolerance** in `ShowPlaybackRuntime.__init__()`:
   ```python
   self._beat_tolerance = min(0.040, beat_interval * 0.30)  # was min(0.025, 0.25)
   ```
   At 120 BPM: 40ms window → 8 ticks at 200Hz → much more robust.

2. **Parameterize tolerance** so it can be tuned per-show:
   ```python
   def __init__(self, timeline, multi_adapter, *, beat_tolerance: float | None = None):
       ...
       if beat_tolerance is not None:
           self._beat_tolerance = beat_tolerance
       else:
           beat_interval = 60.0 / timeline.bpm
           self._beat_tolerance = min(0.040, beat_interval * 0.30)
   ```

3. **Update `ShowTimeline.is_beat()` default** from 0.025 to 0.040:
   ```python
   def is_beat(self, t: float, tolerance: float = 0.040) -> bool:
   ```

### Files to Modify
- `src/dreamsync/show/runtime.py` — `ShowPlaybackRuntime.__init__()`, add `beat_tolerance` parameter
- `src/dreamsync/show/models.py` — `ShowTimeline.is_beat()` default tolerance

### Unit Tests
- `python -m pytest dev/tests/test_show_runtime.py dev/tests/test_show_models.py -v`
- New tests:
  - `test_beat_tolerance_40ms_catches_nearby` — beat at t=10.000, tick at t=10.035 → beat=True (was False with 25ms)
  - `test_beat_tolerance_parameterized` — construct runtime with beat_tolerance=0.050, verify it uses that value

### Completion Criteria
- Default tolerance is 40ms / 30% of beat interval (whichever is smaller)
- Existing tests updated where they assert on the old 25ms value
- Beat hit rate in playback increases (tested by running a full show simulation and counting `_beats_hit`)

---

## Deliverable 4: Tempo Octave Detection (Half-Tempo)

### Prerequisites
- None (independent)

### Problem
`_resolve_harmonic_alias()` checks if the detected BPM falls in the preferred range (80-160). If it does, it returns as-is. But some songs (e.g., "I Feel Everything") have strong 8th-note patterns that register as 160+ BPM when the perceived tempo is 80 BPM. The current code catches this for BPM >160 (halves to land in range), but doesn't catch cases where a song at 155 BPM "feels" like 77.5 BPM because the beat density is double-time.

### Solution

1. **Add onset density analysis** to `_resolve_harmonic_alias()`:
   ```python
   def _resolve_harmonic_alias(self, peak_bpm: float, bpm_values: list[float],
                                onset_times: list[float] | None = None) -> float:
       lo, hi = self.preferred_bpm_range

       # If in preferred range but onset density suggests half-tempo is better...
       if lo <= peak_bpm <= hi and onset_times and len(onset_times) >= 8:
           beat_period = 60.0 / peak_bpm
           half_period = beat_period * 2

           # Count how many onsets fall on even vs odd beats
           even_count = 0
           odd_count = 0
           for t in onset_times:
               n = round(t / beat_period)
               if n % 2 == 0:
                   even_count += 1
               else:
                   odd_count += 1

           # If odd beats have <40% the energy of even beats, halve the tempo
           total = even_count + odd_count
           if total > 0 and odd_count / total < 0.35:
               half_bpm = peak_bpm * 0.5
               if half_bpm >= 40:  # don't go below 40 BPM
                   return half_bpm

       # ... existing logic for out-of-range BPM ...
   ```

2. **Pass detected onset times** from `estimate()` into `_resolve_harmonic_alias()`:
   ```python
   detected_beats = [f.t for f in features if f.beat and f.t > self.warmup_skip_seconds]
   global_bpm = self._resolve_harmonic_alias(global_bpm, bpm_values, detected_beats)
   ```

### Files to Modify
- `src/dreamsync/analyzer/bpm.py` — `_resolve_harmonic_alias()`, `estimate()`

### Unit Tests
- `python -m pytest dev/tests/test_analyzer_bpm.py -v`
- New tests:
  - `test_octave_detection_halves_double_time` — synthetic features at 160 BPM where only every-other beat has strong onset → returns 80 BPM
  - `test_octave_detection_keeps_true_fast` — features at 150 BPM with even onset distribution → stays 150
  - `test_octave_detection_no_onsets_noop` — no onset_times → falls back to existing behavior

### Completion Criteria
- Songs with perceived half-tempo (strong downbeats, weak upbeats) detect at the halved BPM
- Songs with genuine fast tempo retain their detected BPM
- All existing BPM tests still pass

---

## Summary

| # | Deliverable | Files | Effort |
|---|-------------|-------|--------|
| 1 | Onset-anchored hybrid beat grid | `bpm.py` | High |
| 2 | Two-pass phase search | `bpm.py` | Low |
| 3 | Widen beat tolerance in playback | `runtime.py`, `models.py` | Low |
| 4 | Tempo octave detection | `bpm.py` | Medium |

Recommended order: 3 (quick win) → 2 (quick win) → 4 (medium) → 1 (high effort, most impactful)
