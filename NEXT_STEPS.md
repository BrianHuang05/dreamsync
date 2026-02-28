# Next Steps

## Remaining — Hardware Validation Tests 7–9

These require Govee LAN devices powered on and connected. See `VALIDATION_TESTS.md` for full details.

- [ ] **7. Profile rotation** — 3 profiles rotating every 30s, verify visible color changes
- [ ] **8. Health monitor** — probe lifecycle, telemetry snapshots, clean shutdown
- [ ] **9. Offline/online** — power-cycle a device during test 8, verify auto-pause/resume

### Commands

```bash
# 7. Profile rotation (2 min)
python -m dreamsync govee-live --device 10.126.166.180:7:primary:ptreal --duration 120 --profile-rotation aurora,neon_city,midnight_rave --rotation-interval 30 --debug-mood

# 8. Health monitor (run until satisfied, Ctrl+C)
python -m dreamsync session --config devices.yaml --health-monitor --health-interval 15 --debug-mood --telemetry-dir out/health-test

# 9. Offline/online — power-cycle a device while test 8 is running
```

### After hardware tests

- Tuning pass (mood thresholds, effect weights) — see README.md "Tuning reference" section

---

## Validation Progress

- [x] 1–6. Unit tests, scan, connectivity, auto-detect, profiles, hot-swap
- [ ] 7–9. Profile rotation, health monitor, offline/online — **requires hardware**
- [x] 10. BPM stability — **PASSED** (stdev 18.0, 0 unstable songs, 0% outliers)
- [x] 11. Song boundary detection — **PASSED** (13 boundaries / ~14 songs, all `[silence]`, 0 false positives)
- [x] 12. Mood & effect cycling — **PASSED** (4 moods: CHILL 51%, DROP 20%, GROOVE 16%, HYPE 14%; 9 effects)
- [x] 13. Resource stability — **PASSED** (0 errors, 0 dropped blocks, clean exit after 30 min)

---

## Completed Features

### Mk I

| Feature | Date | Tests |
|---|---|---|
| Song boundary detection (silence-gap) | 2/25 | 14 |
| Per-song telemetry | 2/25 | 11 |
| Crossfade-aware boundaries | 2/25 | 13 |
| Config file watcher (YAML edit → re-probe) | 2/26 | 21 |
| Noise-robust onset detection (Phase 1+2) | 2/26 | 15 |
| HPSS percussive onset (Phase 3a) | 2/26 | 10 |

### Mk II

| Feature | Date | Tests |
|---|---|---|
| Color profile system (YAML loader, validator, 8 built-ins) | 2/26 | 63 |
| Profile-aware EffectCycler (palette/effect/param overrides) | 2/26 | 8 |
| Profile CLI (`--profile`, `--auto-profile`, `profiles`, `profile-validate`) | 2/26 | — |
| Profile hot-reload (ProfileWatcher → `set_profile()`) | 2/26 | 7 |
| Profile rotation (`--profile-rotation`, timed swap) | 2/26 | 4 |
| Device health monitor (probe loop, offline/online, role reclass, discovery) | 2/26 | 31 |
| BPM harmonic-lock stabilization (classifier + resistant lock + pipeline fixes) | 2/27 | 95 |

### Plans (retained)

- `plans/harmonic-lock.md` — BPM stabilization design + live test results
- `plans/color-profiles.md` — Color profile system architecture
- `plans/device-health-monitor.md` — Health monitor design
- `plans/smart-profile-swap.md` — Future: auto-swap profiles at song boundaries

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
