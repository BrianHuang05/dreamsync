# Crossfade-Aware Song Boundary Detection — Implementation Plan

## Problem

The current `SongBoundaryDetector` only fires when RMS drops below a silence threshold for N seconds. Many music players (Spotify, foobar2000, etc.) crossfade between tracks — audio never goes silent, so boundaries are missed entirely.

## Goal

Add a second, parallel detector that catches song transitions in crossfaded playback by looking for **sudden discontinuities in audio character** rather than silence. The two detectors (silence + crossfade) run side-by-side; either one can trigger a boundary.

---

## Detection Strategy

A crossfade boundary is characterized by multiple audio features shifting simultaneously within a short window (~3-8 seconds). No single feature is reliable alone, but a **weighted vote** across several signals is robust.

### Candidate Signals

| Signal | Source | What it detects | Already available? |
|---|---|---|---|
| BPM jump | `LiveBpmEstimator._candidate_bpm` | Tempo change (e.g., 128→95) | Yes — `_apply_inertia()` rejects jumps > `max_jump_bpm` and tracks `_candidate_hits` |
| Spectral centroid shift | FFT magnitude spectrum | Timbre/genre change (bright → dark) | Computed in `dsp/features.py` but **hardcoded to 0.0** in `live.py` — needs wiring |
| Bass ratio shift | `SpectralFeatures.bass_ratio` | Low-end balance change | Yes — EMA'd in Director |
| Energy envelope break | `Director.energy` | Sudden energy discontinuity during crossfade blend | Yes |
| Onset pattern break | `LiveBpmEstimator.last_onset` | Rhythmic pattern change | Yes |
| Stability spike | `Director.stability` | BPM variance + ZCR std suddenly jumps | Yes |

### Scoring Model

Each signal contributes a vote when it exceeds its threshold. A boundary fires when the total score exceeds a combined threshold within a voting window.

```
score = (
    w_bpm     * bpm_discontinuity       # 0 or 1
  + w_centroid * centroid_discontinuity  # 0 or 1
  + w_bass    * bass_ratio_discontinuity # 0 or 1
  + w_energy  * energy_envelope_break    # 0 or 1
  + w_onset   * onset_pattern_break      # 0 or 1
)

boundary = score >= trigger_threshold
```

Proposed weights (tunable):

| Signal | Weight | Rationale |
|---|---|---|
| `w_bpm` | 0.35 | Strongest single indicator — different songs almost always have different BPM |
| `w_centroid` | 0.25 | Genre/timbre shifts are reliable but can also shift within a song (verse→chorus) |
| `w_bass` | 0.15 | Supports other signals, not strong alone |
| `w_energy` | 0.10 | Crossfades often produce a brief energy dip/bump in the blend zone |
| `w_onset` | 0.15 | Rhythmic pattern changes corroborate tempo shifts |

Trigger threshold: **0.50** (needs at least BPM + one other signal, or three weaker signals).

---

## Per-Signal Detection Logic

### 1. BPM Discontinuity

Track a rolling 10-second BPM median. Compare the most recent 3-second median against the prior 7-second median. Fire when the absolute difference exceeds a threshold.

```python
bpm_recent = median(bpm_values[-3s:])
bpm_prior  = median(bpm_values[-10s:-3s])
bpm_discontinuity = abs(bpm_recent - bpm_prior) > bpm_jump_threshold  # e.g., 12 BPM
```

The BPM estimator already has inertia (`max_jump_bpm=6.0`, `confirm_updates=6`) that smooths small fluctuations. A sustained jump that breaks through inertia is a strong signal.

**Edge case**: Half-time/double-time shifts within a song (e.g., breakdown at half tempo). Mitigated by requiring at least one corroborating signal.

### 2. Spectral Centroid Shift

Spectral centroid = weighted mean frequency of the magnitude spectrum. Currently computed in `dsp/features.py:128` but not wired into `live.py`.

```python
centroid = np.sum(freqs * mag) / (np.sum(mag) + 1e-8)
```

Track a rolling EMA of centroid. Fire when the frame-to-frame delta exceeds a threshold sustained over N frames.

```python
ema_centroid = alpha * centroid + (1 - alpha) * ema_centroid
centroid_delta = abs(centroid - ema_centroid) / (ema_centroid + 1e-8)
centroid_discontinuity = centroid_delta > 0.30  # 30% relative shift, sustained 3+ frames
```

### 3. Bass Ratio Shift

Already EMA'd in the Director. Track rolling 10-second window, same split as BPM.

```python
bass_recent = mean(bass_ratio_values[-3s:])
bass_prior  = mean(bass_ratio_values[-10s:-3s])
bass_discontinuity = abs(bass_recent - bass_prior) > 0.15
```

### 4. Energy Envelope Break

During a crossfade, energy often dips briefly (destructive interference of out-of-phase signals) or spikes (additive overlap). Detect a V-shaped or spike pattern in the composite energy over ~2 seconds.

```python
energy_range = max(energy_values[-2s:]) - min(energy_values[-2s:])
energy_envelope_break = energy_range > 0.30
```

### 5. Onset Pattern Break

Track the inter-onset interval (IOI) variance over the last 10 seconds. A sudden change in rhythmic regularity suggests a new pattern.

```python
ioi_recent_std = std(inter_onset_intervals[-3s:])
ioi_prior_std  = std(inter_onset_intervals[-10s:-3s])
onset_pattern_break = abs(ioi_recent_std - ioi_prior_std) > ioi_threshold
```

---

## Class Design

### New: `CrossfadeBoundaryDetector`

Lives in `src/dreamsync/live.py` alongside `SongBoundaryDetector`.

```python
@dataclass
class CrossfadeConfig:
    # Voting weights
    w_bpm: float = 0.35
    w_centroid: float = 0.25
    w_bass: float = 0.15
    w_energy: float = 0.10
    w_onset: float = 0.15
    trigger_threshold: float = 0.50

    # Per-signal thresholds
    bpm_jump_threshold: float = 12.0       # BPM
    centroid_shift_threshold: float = 0.30  # relative
    bass_shift_threshold: float = 0.15      # absolute
    energy_range_threshold: float = 0.30    # absolute
    ioi_std_threshold: float = 0.05         # seconds

    # Timing
    window_seconds: float = 10.0           # lookback for prior stats
    recent_seconds: float = 3.0            # lookback for recent stats
    min_song_seconds: float = 60.0         # min time between crossfade boundaries
    confirm_frames: int = 5                # sustain vote for N frames before firing

    # Centroid EMA
    centroid_ema_alpha: float = 0.10


class CrossfadeBoundaryDetector:
    def __init__(
        self,
        config: CrossfadeConfig | None = None,
        hop_size: int = 512,
        sample_rate: int = 44100,
    ) -> None:
        ...

    def update(
        self,
        bpm: float,
        centroid: float,
        bass_ratio: float,
        energy: float,
        onset_strength: float,
        beat: bool,
        t: float,
    ) -> bool:
        """Feed one frame. Returns True on crossfade boundary detection."""
        ...

    def reset(self) -> None:
        """Clear state after a boundary (called by either detector)."""
        ...
```

### Integration into `run_live_to_govee()`

```python
# Initialization (near SongBoundaryDetector setup)
crossfade_detector = CrossfadeBoundaryDetector(hop_size=hop_size, sample_rate=sample_rate)

# In the per-frame loop, after computing features:
crossfade_boundary = crossfade_detector.update(
    bpm=bpm,
    centroid=centroid,       # ← needs wiring from spectral computation
    bass_ratio=sf.bass_ratio,
    energy=director.energy,
    onset_strength=bpm_estimator.last_onset,
    beat=beat,
    t=stream_t,
)

# Boundary check: either detector triggers reset
if song_detector.update(rms) or crossfade_boundary:
    # existing reset logic (BPM, director, mood, effects, telemetry)
    crossfade_detector.reset()
    # ...
```

---

## Code Changes

### 1. Wire spectral centroid in `live.py`

The `_feature_row_from_frame()` function currently hardcodes `centroid: 0.0`. Compute it from the FFT magnitude:

```python
# In _spectral_features() or alongside it:
freqs = np.fft.rfftfreq(frame_size, d=1.0 / sample_rate)
centroid = float(np.sum(freqs * mag) / (np.sum(mag) + 1e-8))
```

Add `centroid` to `SpectralFeatures` dataclass. Cost: one dot product per frame (~negligible vs FFT).

### 2. Add `CrossfadeConfig` and `CrossfadeBoundaryDetector` to `live.py`

~100-150 lines. Maintains rolling deques of (bpm, centroid, bass_ratio, energy, onset_times) and computes the voting score each frame.

### 3. Integrate in `run_live_to_govee()` main loop

~10 lines of glue: instantiate detector, pass features each frame, check result alongside existing silence detector.

### 4. CLI flag

Add `--crossfade-detect` boolean flag (default: off initially, until tuned). When enabled, creates the `CrossfadeBoundaryDetector`. This keeps the feature opt-in while we validate thresholds.

### 5. Telemetry integration

When a crossfade boundary fires, log `"boundary_type": "crossfade"` vs `"silence"` in the telemetry frame so we can distinguish them in post-analysis.

---

## Tests: `tests/test_crossfade_boundary.py`

All unit tests, no hardware needed:

| Test | Description |
|---|---|
| `test_bpm_jump_triggers_vote` | Feed stable BPM then sudden shift, verify BPM signal fires |
| `test_centroid_shift_triggers_vote` | Feed stable centroid then jump, verify centroid signal fires |
| `test_single_signal_below_threshold` | One signal alone shouldn't trigger boundary |
| `test_two_signals_trigger_boundary` | BPM + centroid together should fire |
| `test_min_song_seconds_respected` | Boundary within cooldown period is suppressed |
| `test_confirm_frames_required` | Transient vote spike (1 frame) doesn't fire |
| `test_reset_clears_state` | After reset, detector starts fresh |
| `test_silence_and_crossfade_share_cooldown` | If silence detector fires, crossfade detector respects the cooldown |
| `test_gradual_drift_no_false_positive` | Slow BPM drift (1 BPM/min) doesn't trigger |
| `test_half_time_with_no_corroboration` | BPM halving alone (verse→breakdown) doesn't trigger without centroid/bass shift |

---

## Tuning Plan

1. **Dry run with telemetry**: Run a 30-minute crossfaded playlist with `--telemetry-dir` and `--debug-mood` to collect per-frame centroid, BPM, bass_ratio, energy data.
2. **Offline analysis**: Plot all signals across known song boundaries. Identify which signals shift and by how much.
3. **Threshold calibration**: Set thresholds at 2x the within-song variance for each signal.
4. **Live validation**: Enable `--crossfade-detect` and run the same playlist. Verify boundaries fire at transitions and not mid-song.

---

## Not In Scope

- Key detection (would require chromagram analysis, much heavier DSP)
- Machine learning classifier (want to keep this rule-based and tunable)
- Retroactive boundary correction (detect boundary after the fact and re-split telemetry)
- Crossfade duration estimation (how long the blend zone is)
