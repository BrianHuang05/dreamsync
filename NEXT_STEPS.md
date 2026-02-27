# Next Steps

## Active — Hardware Validation Tests (2/27)

All Mk I+II features are implemented and unit tested (496 tests). Now running live hardware validation. See `VALIDATION_TESTS.md` for full test plan.

### Progress

- [x] 1. Unit tests (all systems)
- [x] 2. Network scan (`govee-scan`)
- [x] 3. Device connectivity (`govee-test`)
- [x] 4. Auto-detect + role classification (session mode)
- [x] 5. Profile basic (aurora + neon_city live on device)
- [x] 6. Profile hot-swap (live YAML edit → reload, error recovery)
- [ ] 7. Profile rotation (3 profiles, timed swap)
- [ ] 8. Health monitor (probe lifecycle + telemetry)
- [ ] 9. Offline/online detection (power cycle during session)

### Resume here

Next test is **7. Profile rotation**:

```bash
python -m dreamsync govee-live --device 10.126.166.180:7:primary:ptreal --duration 120 --profile-rotation aurora,neon_city,midnight_rave --rotation-interval 30 --debug-mood
```

Then **8. Health monitor** and **9. Offline/online** — see `VALIDATION_TESTS.md` Phase 3.

### After validation

- Noisy-environment beat detection (bar/coffee shop) — see `plans/noise-robust-onset.md`
- Crossfade boundary detection tuning
- Infinite session stability (15-30+ min)
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
