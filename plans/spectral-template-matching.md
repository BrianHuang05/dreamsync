# Implementation Plan: Spectral Template Matching (Fix 2)

## Goal

Replace/augment the raw energy-delta onset signal with a spectral template similarity signal that rejects bar noise. Music beats repeat a consistent spectral shape; bar noise doesn't. We exploit this by learning a "beat fingerprint" from early autocorrelation-detected beats, then scoring every new frame against it.

---

## Architecture Overview

```
                    ┌─────────────────────────┐
                    │  _spectral_features()    │
                    │  returns sf.mag (1025 bins)
                    └────────────┬────────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              ▼                  ▼                   ▼
     existing onset       SpectralBeatTemplate      (future HPSS)
     (hybrid/bass/kick)   .update(mag, is_beat)
              │                  │
              │                  ▼
              │            similarity score
              │            (0.0 – 1.0)
              │                  │
              └──────┬───────────┘
                     ▼
           _blend_onset(raw_onset, similarity)
                     │
                     ▼
              onset_env.append()
              (existing BPM pipeline unchanged)
```

**Key design decision:** The template matcher produces a *similarity score* that modulates the existing onset signal, rather than replacing it entirely. This lets the system fall back gracefully when the template isn't established yet.

---

## Step 1: Add `SpectralBeatTemplate` class

**File:** `src/dreamsync/live.py` (near the other signal processing classes, after line ~593)

```python
class SpectralBeatTemplate:
    """Learn and match against the spectral shape of beat frames.

    During the bootstrap phase (first ~5s), collects magnitude spectra at
    detected beat positions.  Once enough beats are collected, builds a
    template via averaging and scores each new frame by cosine similarity
    against the template.
    """

    def __init__(
        self,
        min_beats_for_template: int = 8,
        ema_alpha: float = 0.08,
        similarity_floor: float = 0.3,
    ):
        self.min_beats = min_beats_for_template
        self.ema_alpha = ema_alpha
        self.similarity_floor = similarity_floor

        # Bootstrap collection
        self._beat_mags: list[np.ndarray] = []   # magnitude spectra at beat positions
        self._template: np.ndarray | None = None  # L2-normalized mean beat spectrum
        self._template_ready = False

        # Frame state
        self._prev_similarity = 0.0

    # --- public API ---

    def update(self, mag: np.ndarray, is_beat: bool) -> float:
        """Score current frame against the beat template.

        Args:
            mag: magnitude spectrum from rfft (shape: n_bins,)
            is_beat: whether the current frame is a detected beat
                     (from _advance_beat_phase or onset peak)

        Returns:
            similarity: 0.0–1.0, how much this frame looks like a beat.
                        Returns 0.0 during bootstrap.
        """
        if not self._template_ready:
            # Bootstrap: collect beat spectra
            if is_beat:
                self._beat_mags.append(mag.copy())
                if len(self._beat_mags) >= self.min_beats:
                    self._build_template()
            return 0.0

        # Compute cosine similarity
        similarity = self._cosine_similarity(mag)

        # Adapt template with confirmed beat frames (high-similarity beats)
        if is_beat and similarity > 0.5:
            norm_mag = mag / (np.linalg.norm(mag) + 1e-10)
            self._template = (
                self.ema_alpha * norm_mag
                + (1.0 - self.ema_alpha) * self._template
            )
            # Re-normalize after blend
            self._template /= np.linalg.norm(self._template) + 1e-10

        self._prev_similarity = similarity
        return similarity

    @property
    def ready(self) -> bool:
        return self._template_ready

    def reset(self) -> None:
        """Clear template on song boundary."""
        self._beat_mags.clear()
        self._template = None
        self._template_ready = False
        self._prev_similarity = 0.0

    # --- internals ---

    def _build_template(self) -> None:
        """Average collected beat spectra into a template."""
        stack = np.stack(self._beat_mags)          # (n_beats, n_bins)
        mean_mag = stack.mean(axis=0)
        self._template = mean_mag / (np.linalg.norm(mean_mag) + 1e-10)
        self._template_ready = True
        self._beat_mags.clear()                    # free memory

    def _cosine_similarity(self, mag: np.ndarray) -> float:
        """Cosine similarity between frame spectrum and template."""
        norm_mag = np.linalg.norm(mag)
        if norm_mag < 1e-10:
            return 0.0
        sim = float(np.dot(mag, self._template) / (norm_mag + 1e-10))
        return max(0.0, sim)  # clamp negative to 0
```

### Design notes

- **`min_beats_for_template = 8`**: ~4 seconds at 120 BPM. Enough to average out noise in individual beat frames, short enough for fast lock-on.
- **`ema_alpha = 0.08`**: Template adapts slowly. At ~2 beats/sec, the half-life is ~4 seconds — tracks gradual timbral shifts without drifting to noise.
- **`similarity_floor = 0.3`**: Used in Step 3 blending to set a minimum "pass-through" for the raw onset signal.
- **Bootstrap phase**: Returns 0.0 so the raw onset drives BPM estimation unmodified until the template is ready.
- **Template adaptation gate (`similarity > 0.5`)**: Only updates the template from frames that already resemble it, preventing noise corruption.

---

## Step 2: Integrate into `LiveBpmEstimator`

### 2a. Add template as a member

**File:** `src/dreamsync/live.py`, `LiveBpmEstimator.__init__()` (after line ~76)

```python
# Spectral template matching
self._beat_template = SpectralBeatTemplate()
```

### 2b. Add `mag` parameter to `update()`

**File:** `src/dreamsync/live.py`, `LiveBpmEstimator.update()` (line 97)

Change signature from:
```python
def update(self, energy, t, spectral_flux=0.0, kick_spectral_flux=0.0, whitened_flux=0.0)
```
to:
```python
def update(self, energy, t, spectral_flux=0.0, kick_spectral_flux=0.0,
           whitened_flux=0.0, mag: np.ndarray | None = None)
```

### 2c. Feed template and blend onset

After the onset mode selection (line ~117, after `self.last_onset = onset`), add:

```python
# Spectral template scoring
if mag is not None:
    beat_from_phase = self._beat_phase > 0.9  # about to fire
    similarity = self._beat_template.update(mag, beat_from_phase)
    if self._beat_template.ready:
        # Blend: similarity modulates the onset signal.
        # High similarity → pass onset through amplified.
        # Low similarity → attenuate onset (likely noise).
        gate = self._similarity_gate(similarity)
        onset = onset * gate
        self.last_onset = onset
```

### 2d. Add `_similarity_gate()` method

```python
def _similarity_gate(self, similarity: float) -> float:
    """Convert similarity [0,1] into an onset multiplier.

    Maps similarity through a soft gate:
    - similarity >= 0.7  → multiplier = 1.0 (full pass)
    - similarity ~= 0.5  → multiplier ~= 0.7
    - similarity <= 0.3  → multiplier = 0.3 (floor, don't fully mute)
    """
    floor = 0.3
    if similarity >= 0.7:
        return 1.0
    if similarity <= floor:
        return floor
    # Linear ramp between floor and 1.0
    return floor + (similarity - floor) / (0.7 - floor) * (1.0 - floor)
```

**Why a gate (multiplier) instead of replacement:**
- During template bootstrap, multiplier is implicitly 1.0 (similarity returns 0.0, gate not applied).
- After template lock, noisy frames get attenuated but not silenced (floor=0.3 prevents starving the autocorrelation).
- Actual beat frames pass through at full strength, improving the onset envelope's SNR.

### 2e. Reset template on song boundary

In `LiveBpmEstimator.reset()` (line 78), add:

```python
self._beat_template.reset()
```

---

## Step 3: Pass `sf.mag` from the main loop

**File:** `src/dreamsync/live.py`, `run_live_to_govee()` (lines 845–850)

Change:
```python
bpm, beat = bpm_estimator.update(
    sf.bass, stream_t,
    spectral_flux=sf.spectral_flux,
    kick_spectral_flux=sf.kick_spectral_flux,
    whitened_flux=wf,
)
```
to:
```python
bpm, beat = bpm_estimator.update(
    sf.bass, stream_t,
    spectral_flux=sf.spectral_flux,
    kick_spectral_flux=sf.kick_spectral_flux,
    whitened_flux=wf,
    mag=sf.mag,
)
```

This is the only call-site change needed. `sf.mag` is already computed by `_spectral_features()`.

---

## Step 4: Add telemetry for template state

Add to the telemetry frame dict (in `_feature_row_from_frame` or the telemetry JSONL output):

```python
"template_similarity": bpm_estimator._beat_template._prev_similarity,
"template_ready": bpm_estimator._beat_template.ready,
```

This lets us analyze template behavior from bar recordings without code changes.

---

## Step 5: Tests

### 5a. Unit test `SpectralBeatTemplate` directly

**File:** `tests/test_spectral_template.py`

| Test | What it verifies |
|------|-----------------|
| `test_bootstrap_returns_zero` | `.update()` returns 0.0 until `min_beats` spectra collected |
| `test_template_builds_after_min_beats` | `.ready` becomes True after feeding `min_beats` beat frames |
| `test_identical_spectrum_high_similarity` | Feeding the same spectrum as training → similarity > 0.9 |
| `test_random_noise_low_similarity` | Random noise spectrum → similarity < 0.4 |
| `test_template_adapts` | After many adapted beats with shifted spectrum, similarity stays high |
| `test_reset_clears_template` | After `.reset()`, `.ready` is False and similarity returns 0.0 |

### 5b. Integration test with `LiveBpmEstimator`

**File:** `tests/test_live_bpm.py` (extend existing)

| Test | What it verifies |
|------|-----------------|
| `test_template_onset_gating` | With `mag=` supplied, onset values are attenuated for non-beat-like frames |
| `test_backward_compat_no_mag` | Calling `update()` without `mag=` works identically to before (no regression) |

---

## Step 6: Tuning knobs (for bar testing)

These parameters may need field tuning. Expose them as `LiveBpmEstimator.__init__()` kwargs with sensible defaults:

| Parameter | Default | Effect |
|-----------|---------|--------|
| `template_min_beats` | 8 | Beats needed before template activates |
| `template_ema_alpha` | 0.08 | Template adaptation rate (lower = more stable) |
| `similarity_gate_floor` | 0.3 | Minimum onset passthrough when similarity is low |
| `similarity_gate_ceiling` | 0.7 | Similarity above which onset passes at full strength |

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Template locks onto non-beat pattern (wrong initial beats) | Gate floor of 0.3 means raw onset still gets through; autocorrelation self-corrects over 12s window |
| Songs with very different verse/chorus timbres | EMA adaptation (alpha=0.08) tracks gradual shifts; hard transitions handled by gate floor |
| Computational cost of per-frame cosine similarity | Single dot product + norm on ~1025 floats — negligible (<1μs) |
| Template not ready fast enough | 8 beats at 120 BPM = 4 seconds; system runs unmodified until then |
| Template corrupted by bar noise during bootstrap | Bootstrap uses synthetic beat phase (`beat_phase > 0.9`), which is already filtered through autocorrelation — resistant to random noise |

---

## Implementation Order

1. ~~Add `SpectralBeatTemplate` class (Step 1)~~ ✅
2. ~~Write unit tests, verify they pass (Step 5a)~~ ✅ 6/6 pass
3. ~~Integrate into `LiveBpmEstimator` (Steps 2a–2e)~~ ✅
4. ~~Update call site (Step 3)~~ ✅
5. ~~Add telemetry fields (Step 4)~~ ✅ `template_similarity`, `template_ready`
6. ~~Write integration tests (Step 5b)~~ ✅ 2/2 pass
7. ~~Run full test suite, verify no regressions~~ ✅ 405/405 pass
8. Field test at bar with telemetry recording

---

## Live Testing (No Lights)

Use a dummy/unreachable IP so the Govee adapter runs but no lights are needed.
Play music through your default audio device (speakers/headphones) so the mic picks it up.

### Basic smoke test (30s, debug output)

```bash
python -m dreamsync govee-live \
  --device-ip 10.255.255.1 --segments 7 \
  --duration 30 --debug-mood \
  --telemetry-dir telemetry-test
```

### Longer session with telemetry (5 min, captures template behavior)

```bash
python -m dreamsync govee-live \
  --device-ip 10.255.255.1 --segments 7 \
  --duration 300 --debug-mood \
  --telemetry-dir telemetry-spectral-test
```

### Inspect telemetry output

After a run, check the JSONL files for `template_similarity` and `template_ready` fields:

```bash
# See when template locks on
grep template_ready telemetry-spectral-test/session-*/song-001.jsonl | head -20

# Check similarity scores over time
python -c "
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1]) if 'template_similarity' in l]
for r in rows[::50]:
    print(f't={r[\"t\"]:.1f}  sim={r[\"template_similarity\"]:.3f}  ready={r[\"template_ready\"]}  bpm={r[\"bpm\"]:.1f}')
" telemetry-spectral-test/session-*/song-001.jsonl
```

### What to look for

1. **Template lock-on**: `template_ready` should flip to `true` within ~4-5s of music starting
2. **Beat frames**: `template_similarity` > 0.7 on actual beat frames
3. **Non-beat frames**: `template_similarity` < 0.5 during quiet/between beats
4. **Bar noise test**: Play music at a bar — noise frames should get attenuated (similarity < 0.4), real beats should pass through (similarity > 0.6)
5. **Song boundary**: After a detected boundary, `template_ready` should reset to `false` and re-bootstrap

---

## Files Modified

| File | Changes |
|------|---------|
| `src/dreamsync/live.py` | Add `SpectralBeatTemplate` class; modify `LiveBpmEstimator.__init__`, `.update()`, `.reset()`; add `_similarity_gate()`; update `run_live_to_govee()` call site; add telemetry fields |
| `tests/test_spectral_template.py` | New file — 6 unit tests for `SpectralBeatTemplate` |
| `tests/test_live_bpm.py` | Add 2 integration tests for template gating + backward compat |
