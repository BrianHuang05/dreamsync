# Next Steps

## Active — BPM Stability (2/27)

### Current problem

The BPM estimator has high within-song variance (stdev ~28, target <10). Harmonic-lock Layers 2+3 (classifier + resistant lock) are implemented and prevent out-of-range values, but the underlying raw estimates from the autocorrelation + beat-spacing fusion are too noisy for post-processing alone to fix. **Layer 1 (IOI Histogram)** is needed to replace/augment the raw estimation pipeline.

See `plans/harmonic-lock.md` for the full plan. Layers 2+3 are done; Layer 1 is next.

### Test results (harmonic-lock v3, 15min run)

- Overall stdev: 28.4 (target: <10)
- Range: 80.8–199.7 (good — stays in 80–200 corridor)
- 1x ratio: 43.7% (target: >95%)
- 2/3x ratio: 21.0% (target: <5%)
- HIGH-VARIANCE 30s windows: 16 (target: 0)
- Outliers >220: 0 (fixed by normalize)

### Resume here

Implement **Layer 1: IOI Histogram** from `plans/harmonic-lock.md`. This replaces the autocorrelation + beat-spacing BPM fusion with an onset-interval histogram that finds the dominant beat period in the time domain, avoiding octave ambiguity.

### Validation progress

- [x] 1–6. Unit tests, scan, connectivity, auto-detect, profiles, hot-swap
- [ ] 7–9. Profile rotation, health monitor, offline/online — **requires hardware**
- [~] 10. BPM stability — harmonic-lock v3 done, needs Layer 1 for target stdev
- [ ] 11–13. Song boundaries, mood cycling, resource stability — **no hardware needed**

### After BPM stability

- Finish validation tests 11–13 (boundary, mood, resource)
- Hardware tests 7–9 when device available
- Tuning pass (mood thresholds, effect weights)

---

## Mk I — Completed Features

| Feature | Date | Tests |
|---|---|---|
| Song boundary detection (silence-gap) | 2/25 (live validated) | 14 |
| Per-song telemetry | 2/25 | 11 |
| Crossfade-aware boundaries | 2/25 | 13 |
| Config file watcher (YAML edit → re-probe) | 2/26 | 21 |
| Noise-robust onset detection (Phase 1+2) | 2/26 | 15 |
| HPSS percussive onset (Phase 3a) | 2/26 | 10 |

## Mk II — Completed Features

| Feature | Date | Tests |
|---|---|---|
| Color profile system (YAML loader, validator, 8 built-ins) | 2/26 | 63 |
| Profile-aware EffectCycler (palette/effect/param overrides) | 2/26 | 8 |
| Profile CLI (`--profile`, `--auto-profile`, `profiles`, `profile-validate`) | 2/26 | — |
| Profile hot-reload (ProfileWatcher → `set_profile()`) | 2/26 | 7 |
| Profile rotation (`--profile-rotation`, timed swap) | 2/26 | 4 |
| Device health monitor (probe loop, offline/online, role reclass, discovery) | 2/26 | 31 |

See `plans/color-profiles.md` and `plans/device-health-monitor.md` for architecture details.

---

## Future — Pre-programmed Light Shows (separate project)

A different project entirely: instead of reacting to live audio, **compile** a deterministic light show from a known playlist ahead of time.

### How it differs from DreamSync

| | DreamSync (Mk I/II) | Show Compiler |
|---|---|---|
| Input | Live audio stream | Spotify playlist + offline audio files |
| Analysis | Real-time (~5ms budget) | Offline (unlimited time, full song context) |
| Decisions | Director makes mood/effect choices at runtime | All decisions made at compile time, optionally hand-tweaked |
| Output | Direct device control | Timeline file (JSON/binary) → player runtime |
| Runtime | Detection + rendering + output | Playback only (seek to timestamp, send frame) |

### Rough architecture

1. **Playlist import** — Spotify API to get track list, BPM, sections, audio features; or local audio analysis via librosa
2. **Offline analysis** — Run beat detection, mood classification, section segmentation on full tracks with lookahead (knows what's coming)
3. **Show compiler** — Map sections → effects + palettes (using Mk II profiles), generate a frame-by-frame timeline
4. **Editor** — Optional manual tweaking: move boundaries, override colors, add cues
5. **Player runtime** — Sync to Spotify playback position (or local audio), read timeline, send frames to devices

### What it reuses from DreamSync

- Effect renderers (`render.py`) — same SCROLL, PULSE, BREATHE, etc.
- Device output layer (`govee_lan.py`, `govee_ble.py`, `MultiGoveeLanAdapter`)
- Device config and auto-detect (`auto_detect.py`, `config_watcher.py`)
- Mood color profiles (Mk II)

### What it does NOT reuse

- Real-time audio capture and feature extraction
- Live BPM estimator (would use offline analysis instead)
- Director's runtime decision-making (replaced by the compiled timeline)
- Song boundary detection (known from playlist metadata)
