# Next Steps

## Mk I+II — Final Validation (2/27)

All core features including color profiles are implemented and unit tested (496 tests). What remains is live validation.

### Noisy-environment beat detection (coffee shop / bar)

1. **HPSS percussive onset (Phase 3a)** — Primary validation target:
   ```bash
   python -m dreamsync govee-live --device 10.126.166.180:7:primary:ptreal \
       --duration 600 --debug-mood --telemetry-dir out/bar-noise-v4
   ```
   Inspect:
   ```bash
   python scripts/inspect_telemetry.py out/bar-noise-v4
   python scripts/analyze_noise_onset.py out/bar-noise-v4/session-*/song-*.jsonl
   ```
   **Pass criteria:**
   - `hybrid_source` = `"percussive"` (not `"whitened"` or `"bass"`)
   - `percussive_onset` distribution is bimodal (p75/p25 ratio >> 2)
   - BPM std < 10 (was ~21 in v3)
   - Beat rate ≈ BPM/60

   **If it fails** — see `plans/noise-robust-onset.md` "If it doesn't work" section for decision tree (kernel tuning, fill-forward, Phase 3b band fusion).

2. **Crossfade boundary detection** — Play a crossfaded playlist (Spotify crossfade 5-12s):
   ```bash
   python -m dreamsync govee-live --device 10.126.166.180:7:primary:ptreal \
       --duration 600 --crossfade-detect --debug-mood --telemetry-dir out/crossfade-test
   ```
   - "Song boundary detected [crossfade]" at track transitions
   - No false triggers during verse→chorus or breakdowns
   - Telemetry shows `"boundary_type": "crossfade"` in song summaries
   - May need to tune `CrossfadeConfig` thresholds

### With lights (home)

3. **Auto-detect + session** — First end-to-end YAML config test:
   ```bash
   python -m dreamsync session --config devices.yaml --debug-mood --telemetry-dir out/
   ```
   - LAN devices → `realtime`, BLE → classified by latency
   - Unreachable devices logged and skipped
   - All reachable devices respond to music

4. **Infinite session stability** — 15-30+ minutes:
   - Ctrl+C cleanly shuts down all devices
   - No memory growth or degraded performance
   - Song boundaries fire across multiple songs
   - Mood/effect transitions feel right

5. **Tuning pass** — Review telemetry, adjust mood thresholds and effect weights if needed.

### Color profiles (live validation)

7. **Profile basic** — Run with a built-in profile and verify palette selection:
   ```bash
   python -m dreamsync govee-live --device 10.126.166.180:7:primary:ptreal \
       --duration 120 --profile aurora --debug-mood
   ```
   - Debug output shows aurora palette names
   - Mood transitions pick from profile-defined palettes/effects
   - Effects cycle from profile's effect pools

8. **Profile hot-swap** — Edit profile YAML during a live session:
   - Change a palette color → observe change within ~2s
   - Introduce a YAML syntax error → watcher logs warning, old profile stays active
   - Fix syntax error → watcher picks up the fixed version

9. **Profile rotation** — Verify timed rotation through multiple profiles:
   ```bash
   python -m dreamsync govee-live --device 10.126.166.180:7:primary:ptreal \
       --duration 300 --profile-rotation aurora,neon_city,midnight_rave --rotation-interval 60 --debug-mood
   ```
   - Profile switches every 60s
   - Debug output shows new palette names after each switch

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

## Mk II — In Progress

| Feature | Date | Tests |
|---|---|---|
| Color profile system (YAML loader, validator, 8 built-ins) | 2/26 | 63 |
| Profile-aware EffectCycler (palette/effect/param overrides) | 2/26 | 8 |
| Profile CLI (`--profile`, `--auto-profile`, `profiles`, `profile-validate`) | 2/26 | — |
| Profile hot-reload (ProfileWatcher → `set_profile()`) | 2/26 | 7 |
| Profile rotation (`--profile-rotation`, timed swap) | 2/26 | 4 |
| Device health monitor (probe loop, offline/online, role reclass, discovery) | 2/26 | 31 |

See `plans/color-profiles.md` and `plans/device-health-monitor.md` for architecture details.

### Device health monitor (live validation)

10. **Health monitor cold test** — No music, just verify probe lifecycle:
    ```bash
    python -m dreamsync session --config devices.yaml --health-monitor --health-interval 15 --debug-mood
    ```
    - Health monitor logs appear every ~15s (device status + latency)
    - Ctrl+C cleanly shuts down health thread + all devices
    - No errors or thread leaks

11. **Offline detection** — Start session with health monitor, then unplug/power off a device:
    ```bash
    python -m dreamsync session --config devices.yaml --health-monitor --health-interval 15 --debug-mood
    ```
    - After 3 failed probes (~45s): `WARNING: Device X went offline`
    - Adapter is paused (no UDP errors spamming logs)
    - Other devices continue receiving frames normally

12. **Online recovery** — Power the offline device back on:
    - After 2 successful probes (~30s): `INFO: Device X back online`
    - Device resumes receiving frames immediately
    - No manual intervention required

13. **Health + telemetry** — Verify health snapshots in JSONL:
    ```bash
    python -m dreamsync session --config devices.yaml --health-monitor --health-interval 15 \
        --telemetry-dir out/health-test
    ```
    - Check `out/health-test/session-*/song-*.jsonl` for `"kind":"device_health"` rows
    - Rows contain per-device status, role, latency, failure counts

14. **Discovery scan** — Verify new device detection (optional):
    ```bash
    python -m dreamsync session --config devices.yaml --health-monitor --health-discovery
    ```
    - `INFO: New device discovered: X.X.X.X (HXXXX)` if a new Govee device is on the network

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
