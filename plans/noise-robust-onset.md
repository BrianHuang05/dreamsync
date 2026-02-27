# Plan: Noise-Robust Onset Detection

## Problem

In a noisy room (bar with conversation, glasses, crowd), the beat detector fails:

- **`onset_strength` = 0.0 for >95% of frames** — the bass_diff signal is dead
- **`kick_flux` = 0.0 for >95% of frames** — kick band equally flat
- **`whitened_flux` occasionally spikes (100–600+)** but is 0.0 most of the time
- **RMS mean = 0.00089** — mic signal is extremely weak
- BPM drifts to ~120 with ±50 BPM volatility, never locks onto actual 77.5/155 BPM
- Template matching locks on (similarity ~0.75) but matches the *noise* spectral shape, not beats

### Root cause

The onset signal chain is:

```
mic audio (RMS ~0.001)
  → bass energy sum (~0.4–0.9 absolute)
    → bass_diff = max(0, energy[n] - energy[n-1])
      → ≈ 0.0 because frame-to-frame deltas on a weak signal are tiny
```

The music signal is buried in ambient noise. Frame-to-frame energy barely changes because the noise floor dominates. The adaptive threshold rises above the near-zero onset values and no beats are ever detected from onset peaks.

Meanwhile, `whitened_flux` (which divides by a running spectral mean before computing flux) *does* spike on transients — it saw values of 123, 255, 626 in the same session where bass_diff was flat zero. The normalization lets it see relative changes that absolute measures miss.

---

## Options

### Option A — Hybrid fallback to whitened flux (Quick Win)

**What:** Extend the hybrid onset mode to consider `whitened_flux` as a third signal. When both `bass_activity` and `kick_activity` are near-zero (dead signal), fall through to `whitened_flux`.

**Where:** `LiveBpmEstimator._hybrid_onset()` + `.update()` — add `whitened_flux` param, track `_wf_activity` EMA, switch when bass and kick are both dead.

**Effort:** ~20 lines changed in `live.py`.

**Pros:**
- Immediate improvement with minimal risk — whitened flux already fires on transients in the noisy session
- No new dependencies or preprocessing stages
- Fully backward-compatible (existing behavior unchanged when bass/kick have signal)

**Cons:**
- Whitened flux is noisy — spikes are huge (600+) and sporadic, not clean periodic pulses
- Doesn't actually reduce noise, just uses a signal that's less suppressed by it
- Won't help if the music is truly buried (no transients visible at all)

---

### Option B — Onset envelope normalization (Quick Win)

**What:** Normalize the onset envelope to zero-mean, unit-variance before feeding it to beat detection. Currently the onset values are absolute (spectral flux in raw magnitude units). In a quiet mic environment, these are tiny. The adaptive threshold and autocorrelation both work on absolute values.

**Where:** In `LiveBpmEstimator.update()`, after `self.onset_env.append(onset)` and before the beat detection pass, normalize the onset array: `onset_arr = (onset_arr - mean) / (std + eps)`.

**Effort:** ~5 lines.

**Pros:**
- Makes beat detection amplitude-invariant — works regardless of mic gain
- No new signal path, just rescales what's already there
- Helps all onset modes equally

**Cons:**
- Normalizing a signal that's mostly noise will amplify the noise
- Only helps if the onset signal has *some* periodic content buried in it — if onset is literally flat 0.0, normalizing zeros gives zeros
- Could make things worse in clean environments (already-good signal gets rescaled weirdly)

---

### Option C — Spectral noise floor subtraction (Medium)

**What:** Learn a per-frequency-bin noise floor from the running spectral mean (already computed for whitened flux), then subtract it from the magnitude spectrum before computing bass energy, spectral flux, and kick flux. This is classic spectral subtraction / spectral gating.

**Where:** New function `_spectral_denoise()` called in the frame loop between `_spectral_features()` and `bpm_estimator.update()`. Or integrated into `_spectral_features()` as an optional preprocessing step.

```
mag_clean = max(0, mag - alpha * noise_floor)
```

Where `noise_floor` is the EMA of magnitude spectra (we already track `spectral_mean` for whitened flux).

**Effort:** ~30–50 lines. New helper + wiring.

**Pros:**
- Directly removes the noise floor before onset computation — bass_diff would see music-only energy changes
- Uses infrastructure already in place (`spectral_mean` from whitened flux)
- Well-understood technique (spectral subtraction is standard in speech/audio processing)
- Improves ALL downstream signals (bass, kick, flux, template matching)

**Cons:**
- Subtraction can produce "musical noise" (random spectral holes) — mitigated by flooring at 0
- The EMA noise floor estimate includes both music and noise; during loud music the "noise floor" rises and subtracts too much
- Needs a way to estimate noise-only floor (e.g., from quiet frames, or use minimum statistics instead of mean)
- Tuning the subtraction strength (`alpha`) is environment-dependent

---

### Option D — Minimum-statistics noise estimation (Medium)

**What:** Instead of using the spectral mean as the noise floor (which includes music), track the *minimum* magnitude per frequency bin over a sliding window. In any environment, the noise floor is the quietest the signal gets per-bin. Music adds energy on top of this minimum.

**Where:** New class `NoiseFloorEstimator` that maintains a per-bin running minimum over ~2–5 seconds. Feed the result into spectral subtraction (Option C).

```python
class NoiseFloorEstimator:
    def __init__(self, n_bins, window_frames=200):
        self._ring = deque(maxlen=window_frames)  # ~2.3s at 86 fps

    def update(self, mag: np.ndarray) -> np.ndarray:
        self._ring.append(mag.copy())
        return np.min(np.stack(list(self._ring)), axis=0)
```

**Effort:** ~40 lines for estimator + integration.

**Pros:**
- Much better noise floor estimate than mean — the minimum tracks what's *always* present (noise), not what comes and goes (music)
- Standard technique in speech enhancement (Martin's minimum statistics, IMCRA)
- Pairs naturally with Option C for accurate subtraction

**Cons:**
- Per-bin minimum over a rolling window requires storing ~200 frames × 1025 bins × 4 bytes ≈ 800 KB
- The true noise floor during crowd noise varies — minimum may underestimate during loud crowd moments
- Adds latency to noise floor estimation (needs ~2s to fill window)

---

### Option E — HPSS percussive onset (Medium-High)

**What:** Harmonic/Percussive Source Separation — decompose each spectrogram frame into harmonic (sustained tones, crowd hum) and percussive (transients, kicks, snares) components. Compute onset from the percussive component only.

Already described in detail in `plans/bar-noise-beat-detection.md` (Fix 3).

**Effort:** ~80 lines. New class `PercussiveOnsetTracker` with rolling mag buffer + median filtering.

**Pros:**
- Principled separation of transients from sustained noise
- Crowd noise is mostly harmonic/broadband-sustained → goes to H, not P
- Kicks/snares are percussive → preserved in P
- Can stack with Options C/D for even cleaner signal

**Cons:**
- Median filter on 43×1025 matrix per frame — likely fine but needs profiling
- Adds ~5–10 frame latency (~50–100ms) from the median filter kernel
- Kernel size tuning matters: too small = noise leaks through, too large = kicks get smeared
- Most complex option to implement and tune

---

### Option F — Band-specific onset fusion (Medium)

**What:** Instead of one onset signal, compute separate onset envelopes in multiple frequency bands (sub-bass 20–80 Hz, kick 80–200 Hz, snare 200–1000 Hz, hi-hat 5–15 kHz) and fuse them with weighted voting. Beats show correlated onsets across bands; noise is uncorrelated.

**Where:** Modify `_spectral_features()` to return per-band flux, then add a fusion step in `LiveBpmEstimator.update()`.

**Effort:** ~60 lines.

**Pros:**
- Exploits the fact that a kick drum produces simultaneous energy increases in sub-bass + mid, while a glass clink only hits one band
- Cross-band correlation naturally rejects single-band noise events
- No noise floor estimation needed — uses correlation structure instead

**Cons:**
- Requires tuning band boundaries and weights
- Some songs have sparse instrumentation (only kick, no hi-hat) → fewer correlated bands
- Adds complexity to the onset signal path

---

## Recommended Implementation Order

### Phase 1: Quick wins (test immediately)

**1. Option A — Hybrid whitened-flux fallback**

Rationale: The telemetry proves whitened_flux fires on actual transients while bass_diff and kick_flux are dead. This is 20 lines of code and gives the system *something* to work with in the noisy environment. Even if the signal is noisy, it's better than flat zero.

**2. Option B — Onset envelope normalization**

Rationale: 5 lines, makes the detector amplitude-invariant. Won't fix flat-zero onset (Option A must come first), but once Option A provides a non-zero signal, normalization ensures the threshold and autocorrelation work regardless of absolute scale.

### Phase 2: Proper noise reduction

**3. Option D + C — Minimum-statistics noise estimation + spectral subtraction**

Rationale: This is the correct general solution. Learn what the noise floor looks like per-frequency-bin, subtract it, then compute features on the cleaned spectrum. Every downstream signal (bass, kick, flux, whitened flux, template) benefits. Option D provides an accurate noise estimate; Option C uses it.

Implement together as a single feature.

### Phase 3: Advanced (if Phase 2 insufficient)

**4. Option E — HPSS percussive onset**

Rationale: If spectral subtraction isn't enough (e.g., crowd noise has transient components that survive subtraction), HPSS provides a principled separation that spectral subtraction can't. More complex to implement and tune, so only pursue if Phases 1–2 leave residual problems.

**5. Option F — Band-specific onset fusion**

Rationale: Useful as an additional layer if single-band onset (even after denoising) is unreliable. The cross-band correlation provides an orthogonal noise rejection mechanism. Pursue last since it adds the most complexity.

---

## Decision flowchart

```
Start
  │
  ▼
Phase 1: whitened-flux fallback + normalization
  │
  ├─ Does BPM lock onto actual tempo in noisy room? → DONE
  │
  ▼ (no)
Phase 2: spectral subtraction with min-statistics noise floor
  │
  ├─ Does onset signal show clear periodic peaks above noise? → DONE
  │
  ▼ (no)
Phase 3a: HPSS percussive separation
  │
  ├─ Are transients cleanly separated from crowd noise? → DONE
  │
  ▼ (no)
Phase 3b: Band-specific onset fusion
  │
  └─ Use cross-band correlation to reject residual noise → DONE
```

---

## Files to modify

| Phase | Files | Changes |
|-------|-------|---------|
| 1 | `src/dreamsync/live.py` | Extend `_hybrid_onset()` to accept + fall back to whitened_flux; add onset normalization in `update()` |
| 2 | `src/dreamsync/live.py` | New `NoiseFloorEstimator` class; integrate spectral subtraction into frame loop before `_spectral_features()` or as post-processing on `sf.mag` |
| 3a | `src/dreamsync/live.py` or `src/dreamsync/dsp/features.py` | New `PercussiveOnsetTracker` class with rolling mag buffer + median filtering |
| 3b | `src/dreamsync/live.py` | Multi-band flux computation in `_spectral_features()`, fusion logic in `LiveBpmEstimator.update()` |
| All | `tests/test_live_bpm.py` | Integration tests per phase |
| All | `scripts/inspect_telemetry.py` | Update to show new fields |

---

## Implementation Status

### Phase 1: Quick wins — DONE

- [x] **Option A — Hybrid whitened-flux fallback**
  - `_hybrid_onset()` now accepts `whitened_flux` parameter
  - Tracks `_wf_activity` EMA alongside bass/kick
  - Falls back to whitened_flux when both bass and kick activity < `dead_threshold` (1e-4)
  - New hybrid source: `"whitened"`
  - 5 new tests in `TestHybridWhitenedFluxFallback`

- [x] **Option B — Onset envelope normalization**
  - After smoothing, normalizes onset_arr to zero-mean unit-variance when std > 1e-8
  - Makes beat detection amplitude-invariant
  - 2 new tests in `TestOnsetNormalization`

### Phase 2: Proper noise reduction — DONE

- [x] **Options D + C — Minimum-statistics noise estimation + spectral subtraction**
  - New `NoiseFloorEstimator` class: per-bin rolling minimum over 200-frame window
  - Readiness gate: waits 86 frames (~1s) before activating subtraction
  - `_spectral_features()` accepts optional `noise_floor` parameter
  - `SpectralFeatures` has new `raw_mag` field (pre-subtraction mag)
  - Integrated into `run_live_to_govee()` frame loop
  - Noise estimator resets on song boundary
  - Whitened flux uses raw mag (preserves existing normalization behavior)
  - 4 new tests in `TestNoiseFloorEstimator`, 4 in `TestSpectralSubtraction`

### Phase 3a: HPSS percussive onset — DONE

- [x] **Option E — HPSS percussive onset**
  - New `PercussiveOnsetTracker` class: rolling buffer of active-frame magnitude spectra
  - Time-direction median per frequency bin = harmonic (sustained noise) estimate
  - Percussive component = max(0, current_mag - median) captures transients
  - Energy gate: only active frames buffered and scored (silent frames return 0)
  - `_MIN_READY_FRAMES = 15` warmup (capped at kernel_size)
  - `kernel_size = 43` (~3.3s of active frames at 13 active fps)
  - Integrated into `run_live_to_govee()` frame loop with raw_mag input
  - `_hybrid_onset()` prefers percussive over whitened in noisy fallback
  - Resets on song boundary
  - Logged as `percussive_onset` in telemetry
  - 6 new tests in `TestPercussiveOnsetTracker`, 4 in `TestHybridPercussiveFallback`

### Phase 3b: Advanced — NOT YET NEEDED

- [ ] Option F — Band-specific onset fusion (implement only if Phase 3a insufficient)

### Post-live-test fixes (v2)

Telemetry analysis of `song-002.jsonl` (bar session, 82% of frames RMS=0) revealed:

1. **Whitened fallback never triggered** — `_bass_activity ≈ 0.009` stayed above `dead_threshold=1e-4` due to noise fluctuations in the bass band. **Fixed:** replaced absolute dead_threshold with ratio-based trigger: switch when `wf_activity > 10 * (bass_activity + kick_activity)` and `wf_activity > 0.01`.

2. **Onset normalization created negative thresholds** — zero-mean normalization on 82%-zero signal pushed all zeros below zero, making adaptive threshold negative (-0.36), so everything triggered as a "beat." **Fixed:** changed to scale-only normalization (divide by std, no mean subtraction) to preserve the zero baseline.

3. **Log compression added for whitened flux** — raw whitened flux ranges 100–4200+; `log1p()` compresses to 0–8.3, preventing extreme outliers from dominating autocorrelation.

**Simulation on recorded telemetry shows median IOI → 156.6 BPM** (target was 155 BPM), a massive improvement over the previous 163.5±11.9 BPM from random drift.

### Post-live-test fixes (v3) — energy-gated whitened flux

v2 testing revealed whitened flux was firing on ALL active frames equally (p25=5.12, p75=5.53 after log — no amplitude selectivity). Root cause: `spectral_mean` was dominated by 85% silence frames, making ANY audio produce huge whitened flux.

**Fix:** Added `energy_gate` parameter to `_compute_whitened_flux()`:
- Only update `spectral_mean` from frames with `mag.sum() > energy_gate` (skip silence)
- Compute flux only between consecutive ACTIVE frames (skip silence→active transitions)
- Silent frames preserve the last active whitened_mag unchanged
- `energy_gate=1.0` in `run_live_to_govee()`, `energy_gate=0` (disabled) for backward compat in tests

**Effect:** Whitened flux now measures spectral change between audio captures, not the trivial silence→audio transition. Beat transients produce different spectra than ambient captures → amplitude selectivity restored.

### Post-live-test analysis (v3) — whitened flux still lacks selectivity

v3 energy-gated WF still fires uniformly on all active frames (p25=5.19, p75=5.52 after log1p). Even comparing only consecutive active frames, all mic captures in a noisy environment produce similar spectral content. Autocorrelation dominated by capture-rate harmonics (lag 7, 14, 21) with only weak beat-related peaks (lag 35~148 BPM, lag 43~120 BPM). This is the fundamental limitation of frame-to-frame flux in an 85%-dropout environment.

**Root cause:** Whitened flux measures spectral *change* between frames. When all active captures see similar ambient noise, all changes are similar magnitude — no beat/non-beat selectivity. HPSS (Phase 3a) addresses this by measuring per-frame *deviation from the ambient model* instead.

### Post-live-test fixes (v5–v8.2) — Noise-lock feedback loop

Bar testing revealed the BPM estimator locks onto noise and stays confidently wrong. The failure chain: quiet sections → phase accumulator fires on noise → template learns noise shape → similarity gate passes everything → autocorrelation finds spurious peaks → inertia holds wrong BPM.

**Three core fixes break the loop at different points:**

1. **Autocorrelation confidence gate** (`src/dreamsync/dsp/features.py`):
   - `_estimate_bpm()` now returns `(bpm, confidence)` tuple
   - Confidence = `peak_val / mean(abs(corr))` — periodic signals score ~25, noise scores ~2
   - Returns `(0.0, confidence)` when confidence < 3.0
   - Tracked as `autocorr_confidence` in telemetry

2. **Energy-gated template bootstrap** (`SpectralBeatTemplate`):
   - `update()` accepts `frame_energy` parameter
   - Frames below `energy_threshold` (0.005) skip bootstrap and scoring
   - Returns `_prev_similarity` for quiet frames (no-op)
   - Prevents template from training on noise-dominated frames

3. **Template selectivity monitor** (`SpectralBeatTemplate`):
   - `_sim_buffer` (deque, maxlen=200) tracks recent similarity scores
   - `has_selectivity` property: True if `std(sim_buffer) > 0.05`
   - After sustained no-selectivity (350 frames ≈ 4s), auto-resets template
   - `LiveBpmEstimator` skips gating when template has no selectivity

**Additional fixes from iterative bar testing:**

4. **Zero-estimate BPM decay** (`LiveBpmEstimator`):
   - `_zero_estimate_count` increments when both estimation methods return 0
   - After 6 consecutive zeros (~3s), `last_bpm` decays to 0.0
   - Prevents holding stale wrong BPM indefinitely

5. **Bass-frequency percussive onset filtering** (`PercussiveOnsetTracker`):
   - `freq_mask` parameter restricts percussive energy to bass frequencies (≤300 Hz)
   - Filters out speech (200–4000 Hz) and glass/plate noise (1–5 kHz)
   - In bar testing, 97% of percussive energy was non-bass noise — filtering eliminated it

6. **Subharmonic-aware BPM snap** (`LiveBpmEstimator._snap_to_last()`):
   - Extended with subharmonic ratio candidates: 1.5, 2/3, 4/3, 0.75, 1.25, 0.8
   - **Directional**: only applies for downward drift (`bpm < last_bpm * 0.9`)
   - Prevents BPM drifting to 2/3, 3/4, 4/5 of true BPM while allowing upward escape from wrong state

**Bar test results (v8.2, true BPM 164):** 77% locked at 80–85 BPM (correct half-time). Eliminated previous wild drift between 80–172 BPM. Quiet sections correctly show BPM = 0.

9 new tests across 3 test classes:
- `TestAutocorrelationConfidenceGate` (3 tests): rejects noise, accepts periodic, decays after sustained zeros
- `TestEnergyGatedTemplateBootstrap` (3 tests): skips low energy, bootstraps high energy, returns prev similarity for quiet
- `TestTemplateSelectivityMonitor` (3 tests): detects no selectivity, resets on prolonged, maintains with real beats

### Test results

All 548 tests pass (65 in test_live_bpm.py, 6 in test_spectral_template.py, 21 in test_dsp_features.py).

---

## Live Testing — Phase 3a (HPSS Percussive Onset)

### Test command

```bash
python -m dreamsync govee-live --device 10.126.166.180:7:primary:ptreal \
    --duration 600 --debug-mood --telemetry-dir out/bar-noise-v4
```

### Inspect results

```bash
python scripts/inspect_telemetry.py out/bar-noise-v4
python scripts/analyze_noise_onset.py out/bar-noise-v4/session-*/song-*.jsonl
```

### What to check

1. **`hybrid_source` should be `"percussive"`** — confirms HPSS activated (not stuck on `"whitened"` or `"bass"`). If still `"whitened"`, the percussive tracker hasn't warmed up (needs 15 active frames) or is returning 0.

2. **`percussive_onset` distribution should be bimodal** — low values for non-beat active frames, high values for beat-frame active captures. Check:
   - Ratio of p75/p25 should be >> 1 (was ~1.06 for whitened flux; need >> 2)
   - If still uniform, the ambient noise is too transient-like and median filtering can't separate beats from noise fluctuations

3. **BPM stability** — v3 had std ~21 BPM. Target: std < 10 BPM. If BPM still drifts wildly, the percussive onset may have selectivity but the autocorrelation is still dominated by capture-rate harmonics (the 85% zeros in the onset envelope).

4. **Beat rate vs BPM** — beats/second should approximate BPM/60. If beats >> expected, threshold is too low. If beats << expected, threshold is too high or percussive onset has no peaks above threshold.

### If it doesn't work — next steps

**If percussive onset is uniform (no selectivity):**
The ambient noise has transient spectral variation that looks like beats. Options:
- Increase `kernel_size` from 43 to ~86 (longer median window = more stable ambient model)
- Add frequency-direction median (full HPSS Wiener masking) to better isolate percussive vs harmonic
- Phase 3b: Band-specific onset fusion (cross-band correlation rejects single-band noise)

**If percussive has selectivity but BPM still drifts:**
The 85% zeros in the onset envelope swamp the autocorrelation. Options:
- Fill-forward: hold last nonzero percussive value during silent frames instead of appending 0
- Sparse-signal mode: run autocorrelation only on nonzero onset values with their timestamps
- Reduce reliance on autocorrelation (increase beat-spacing weight from 0.7 to 0.9)

**If percussive tracker never becomes ready:**
Fewer than 15 active frames in the session. Check RMS zero % — if > 95%, the mic is barely capturing audio. Consider: lower `energy_gate` from 1.0, or lower `_MIN_READY_FRAMES` from 15 to 8.
