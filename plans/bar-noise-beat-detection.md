# Beat Detection in Noisy Bar Environments

## Problem

The bar has loud broadband noise (glasses, crowd, shouting) across bass, mid, and treble. The current beat detector uses frame-to-frame energy deltas (bass_diff, kick_flux) which can't distinguish rhythmic musical energy from random bar noise. A spectral filter alone won't work because bar noise occupies the same frequency bands as music.

The key insight: **music is periodic, bar noise is stochastic**. We need detection approaches that exploit periodicity, not just spectral content.

## Current Architecture

- `LiveBpmEstimator.update()` receives per-frame features: `energy`, `spectral_flux`, `kick_spectral_flux`, `whitened_flux`
- Hybrid onset mode picks between `bass_diff` (energy delta) and `kick_flux` (kick-band spectral flux)
- Onset envelope feeds into autocorrelation + beat peak detection to estimate BPM
- Synthetic beat phase accumulator fires beats at the estimated BPM rate
- All onset/feature extraction lives in `src/dreamsync/dsp/features.py`; BPM estimation in `src/dreamsync/live.py`

---

## Fix 2 — Spectral Template Matching (Priority 1)

### Concept

Instead of detecting beats from raw energy deltas, learn what a "beat frame" looks like spectrally and match against it. A kick drum at 120 BPM has a consistent spectral shape every ~0.5s; a glass clinking has a random spectral shape at random times.

### Approach

1. **Build a rolling beat template** — after the first few detected beats (from autocorrelation lock), average the magnitude spectra of frames at beat positions to create a spectral template of "what a beat looks like in this song."

2. **Cosine similarity scoring** — for each new frame, compute cosine similarity between its magnitude spectrum and the beat template. High similarity = likely beat frame; low similarity = noise or non-beat musical content.

3. **Use similarity as an onset signal** — replace or augment the raw energy-delta onset with the template similarity signal. This signal will be high only at frames that look like previous beats, naturally rejecting bar noise that doesn't match the beat's spectral signature.

4. **Template adaptation** — slowly update the template with confirmed beat frames (EMA blend) so it tracks gradual changes in the song. Reset on song boundary.

### Where it fits

- New class `SpectralBeatTemplate` in `dsp/features.py` or `live.py`
- `LiveBpmEstimator.update()` receives the current frame's magnitude spectrum (already computed as `sf.mag`)
- Template builds during first ~5s using autocorrelation-detected beats
- Once template is established, similarity score becomes the primary onset signal
- Falls back to current hybrid onset when template confidence is low (e.g. first few seconds or after boundary reset)

### Risks

- Template could lock onto a non-beat spectral pattern if initial beat detection is wrong
- Needs a confidence/quality metric to know when the template is trustworthy
- Songs with varied instrumentation (verse kick vs chorus kick) may need template adaptation rate tuning

---

## Fix 3 — Harmonic/Percussive Separation (Priority 2)

### Concept

Decompose each spectrogram frame into harmonic (tonal, sustained) and percussive (transient, sharp) components. Music beats are percussive with consistent periodicity; bar noise is percussive but aperiodic. By isolating the percussive component first, then running periodicity analysis on it, we get a cleaner beat signal.

### Approach

1. **Median filtering HPSS** — classic technique: apply median filter along time axis (captures harmonic = sustained tones) and along frequency axis (captures percussive = broadband transients). The percussive residual is `P = S - H` where S is the spectrogram and H is the time-median-filtered version.

2. **Percussive onset envelope** — compute the onset envelope from the percussive-only spectrogram instead of the full spectrogram. This removes sustained bar noise (crowd hum, music harmonics) and isolates transient events.

3. **Periodicity gate on percussive onsets** — run the existing autocorrelation on the percussive onset envelope. Since bar transients (clinks, shouts) are aperiodic, the autocorrelation of the percussive stream will show strong peaks only at the musical beat period.

4. **Integration** — the percussive onset replaces or supplements the current `spectral_flux` / `bass_diff` / `kick_flux` signals fed into `LiveBpmEstimator`.

### Where it fits

- New function `_hpss_percussive_onset(mag_history, ...)` in `dsp/features.py`
- Needs a short rolling buffer of magnitude spectra (last ~0.5-1s, ~40-80 frames) for the median filter
- Returns a percussive onset strength per frame
- Add as a new `onset_mode` option (e.g. `"percussive"`) or integrate into hybrid mode

### Implementation detail

The median filter approach needs a time-axis buffer of magnitude spectra. With hop_size=512 at 44100 Hz (~86 frames/sec), a 0.5s buffer is ~43 frames. Each frame is FFT_size/2+1 bins. At frame_size=2048, that's 1025 floats per frame, so ~43 * 1025 * 4 bytes ≈ 175 KB — trivial memory cost.

```python
# Pseudocode
class PercussiveOnsetTracker:
    def __init__(self, buffer_frames=43, fft_bins=1025):
        self.mag_buffer = deque(maxlen=buffer_frames)

    def update(self, mag: np.ndarray) -> float:
        self.mag_buffer.append(mag)
        if len(self.mag_buffer) < 5:
            return 0.0
        S = np.column_stack(self.mag_buffer)          # (bins, frames)
        H = median_filter(S, size=(1, kernel_t))       # harmonic: smooth along time
        P = np.maximum(S - H, 0)                       # percussive residual
        # Onset = flux of percussive component (last frame vs previous)
        if P.shape[1] >= 2:
            return float(np.sum(np.maximum(P[:, -1] - P[:, -2], 0)))
        return 0.0
```

### Risks

- Median filter adds a small latency (~half the kernel width, ~5-10 frames = 50-100ms)
- CPU cost of per-frame median filter on a 43x1025 matrix — should be fine at 86 fps but worth profiling
- HPSS quality depends on kernel size tuning (too small = leaky, too large = laggy)

---

## Fix 1 — Autocorrelation Confidence Gate (Priority 3 — Last Resort)

### Concept

Use the strength of the autocorrelation peak as a quality metric. When there's no clear periodic signal (pure bar noise), the autocorrelation is flat — no peak stands out. When music is playing, the peak at the beat period is strong. Gate BPM updates on this confidence to prevent noise from corrupting the estimate.

### Approach

1. **Peak-to-median ratio** — after computing autocorrelation, measure the ratio of the strongest peak to the median autocorrelation value. A strong beat signal gives ratio >> 2; pure noise gives ratio ≈ 1.

2. **Confidence threshold** — only accept BPM estimates when peak-to-median ratio exceeds a threshold (e.g. 1.5). Below that, hold the previous BPM estimate (or 0 if no prior lock).

3. **Confidence-weighted inertia** — instead of a hard gate, scale the inertia by confidence. High confidence = accept new BPM readily. Low confidence = hold previous estimate strongly.

### Where it fits

- Modify `_estimate_bpm()` in `dsp/features.py` to return `(bpm, confidence)` tuple
- `LiveBpmEstimator._apply_inertia()` uses confidence to modulate acceptance

### Why last resort

This approach **suppresses** bad estimates rather than **improving** the onset signal. It makes the system more conservative (won't lock onto noise) but doesn't help it detect beats that are masked by noise. Fixes 2 and 3 are better because they improve the signal itself.

---

## Implementation Order

1. **Fix 2 (spectral template)** — highest potential payoff, directly exploits the "beats repeat" pattern that bar noise lacks. Implement and test at the bar.
2. **Fix 3 (HPSS)** — if template matching isn't enough on its own, add percussive separation as a preprocessing step that feeds cleaner onsets into both the template matcher and autocorrelation.
3. **Fix 1 (confidence gate)** — if the signal is still too noisy after fixes 2+3, add the confidence gate as a safety net to prevent noise-corrupted estimates from destabilizing BPM.

Fixes 2 and 3 are complementary and can stack: HPSS cleans the signal → template matching identifies beat frames → autocorrelation confirms periodicity.
