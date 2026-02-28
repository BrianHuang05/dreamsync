# Next Steps

## Active — Validation Tests (2/27)

### BPM stability — PASSED (test 10)

Harmonic-lock layers + pipeline fixes validated on 15-min live test with 8 songs:

- **Layer 1: Pipeline fixes** — Closeness threshold (15%), last_bpm on rejection, EMA smoothing (alpha=0.3)
- **Layer 2: Harmonic Classifier** — `_classify_harmonic()` with 15% closeness gate
- **Layer 3: Harmonic-Resistant Lock** — `_apply_inertia()` requires 12 confirmations, returns last_bpm on rejection

**Live test results (histogram run, 8 songs, ~86K samples):**

| Metric | Baseline (Layers 2+3 only) | With pipeline fixes | Improvement |
|---|---|---|---|
| Stdev | 27.8 | **18.0** | -35% |
| Range | 80.0–199.7 | **85.4–168.1** | Tightened 37% |
| 1x ratio | 44.5% | **69.0%** | +24.5pp |
| 2/3x ratio | 18.3% | **6.1%** | -12.2pp |
| Unstable songs | 6/7 | **0/8** | All stable |
| HIGH-VARIANCE windows | 27/30 | **3/30** | At transitions only |
| Outliers (<40 or >220) | 0% | 0% | — |

One song (song-003) flagged at 21% off-center — confirmed as a song transition zone, not a detector issue. All 3 HIGH-VARIANCE windows occur at song boundaries where tempo re-acquisition is expected.

See `plans/harmonic-lock.md` for the full plan and `out/longrun/bpm-analysis-histogram.txt` for raw data.

### Resume here

- [ ] 7–9. Profile rotation, health monitor, offline/online — **requires hardware**
- [ ] 11–13. Song boundaries, mood cycling, resource stability — **no hardware needed, 30-min run**
- Tuning pass (mood thresholds, effect weights)

### Validation progress

- [x] 1–6. Unit tests, scan, connectivity, auto-detect, profiles, hot-swap
- [ ] 7–9. Profile rotation, health monitor, offline/online — **requires hardware**
- [x] 10. BPM stability — **PASSED** (stdev 18.0, 0 unstable songs, 0% outliers)
- [ ] 11–13. Song boundaries, mood cycling, resource stability — **no hardware needed**

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
