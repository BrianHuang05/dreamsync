# DreamSync — Audio-Reactive Govee LAN Control

Windows-first Python project for local audio-reactive lighting. Listens to system audio, detects beats and energy, and streams per-segment RGB frames directly to Govee devices over LAN UDP — no cloud, no LedFx dependency.

## Quick start

```bash
python -m venv .venv
. .venv/Scripts/activate
pip install -e .
```

## Device discovery

Find Govee devices on your network:

```bash
python -m dreamsync govee-scan
```

## Live music sync

Play music on your PC, then run `govee-live` to drive the lights in real time.

### Single device

```bash
python -m dreamsync govee-live \
    --device-ip 10.0.0.123 \
    --segments 15 \
    --duration 120 \
    --render-mode scroll \
    --brightness 0.8
```

### Multiple devices (different hardware)

Use `--device IP:SEGMENTS:ROLE:TRANSPORT` to target multiple strips. Each device can have its own segment count and transport protocol.

```bash
python -m dreamsync govee-live \
    --device 10.0.0.1:7:primary:ptreal \
    --device 10.0.0.2:25:primary:razer \
    --duration 120 \
    --render-mode scroll
```

### Key options

| Flag | Default | Description |
|------|---------|-------------|
| `--device-ip` | | Single device IP (use with `--segments`) |
| `--device` | | Repeatable device spec: `IP:SEGMENTS[:ROLE[:TRANSPORT]]` |
| `--segments` | 15 | Segment count (with `--device-ip`) |
| `--duration` | | Capture duration in seconds (required) |
| `--render-mode` | scroll | `solid`, `pulse`, `scroll`, or `breathe` |
| `--transport` | ptreal | `razer` (DreamView per-LED), `ptreal` (BLE-over-LAN per-segment), `colorwc` (whole-strip) |
| `--fps` | 30 | Frame rate |
| `--brightness` | 1.0 | Global brightness (0-1) |
| `--colors` | auto | Comma-separated hex colors to cycle on beats |
| `--mirror` / `--no-mirror` | mirror | Scroll from center outward vs left-to-right |
| `--half-time` | off | Halve detected BPM (fixes octave-doubled detection) |
| `--max-brightness` | off | Force all frames to full intensity |
| `--auto-cycle` / `--no-auto-cycle` | on | Mood-driven effect cycling |
| `--cycle-interval` | 16 | Seconds between effect changes within same mood |
| `--debug-mood` | off | Print mood, effect, BPM, and song boundary events to stdout |
| `--audio-device` | system default | PortAudio input device ID |

## Debug / dry-run testing (no lights needed)

Use a fake IP to test the audio analysis pipeline without any hardware connected. UDP sends are fire-and-forget, so they silently fail on unreachable IPs while the full BPM, mood, effect, and song boundary pipeline runs normally.

```bash
# Watch mood/effect/BPM transitions live (60 seconds)
python -m dreamsync govee-live \
    --device 192.168.0.99:7:primary:ptreal \
    --duration 60 \
    --debug-mood

# Longer run to test song boundary detection across a playlist
python -m dreamsync govee-live \
    --device 192.168.0.99:7:primary:ptreal \
    --duration 600 \
    --debug-mood
```

With `--debug-mood` you'll see output like:

```
mood=chill effect=slow_breathe palette=cool mode=breathe energy=0.0812 stability=0.0340 bpm=127.3
mood=groove effect=beat_pulse palette=vivid mode=pulse energy=0.3812 stability=0.0540 bpm=128.1
*** Song boundary detected (#1) — state reset ***
mood=chill effect=wave_drift palette=warm mode=wave energy=0.0023 stability=0.0000 bpm=0.0
```

## Smoke tests

Send a solid color to verify connectivity:

```bash
python -m dreamsync govee-test --device-ip 10.0.0.123 --segments 15 --color '#ff0000' --duration 5
```

Test patterns for segment diagnostics:

```bash
python -m dreamsync govee-test --device-ip 10.0.0.123 --segments 15 --pattern rainbow
python -m dreamsync govee-test --device-ip 10.0.0.123 --segments 15 --pattern walk
```

## Infinite session mode (YAML config)

For production use with a device config file. Runs until Ctrl+C, auto-detects device roles, and handles song boundaries automatically.

```bash
python -m dreamsync session --config devices.yaml --debug-mood
```

### Color profiles

8 built-in color profiles control palette selection and effect pools per mood. Use `--profile` to load one, or `--profile-rotation` to cycle through several:

```bash
# List available profiles
python -m dreamsync profiles --verbose

# Run with a specific profile
python -m dreamsync govee-live --device 10.0.0.1:7:primary:ptreal --duration 120 --profile aurora --debug-mood

# Rotate through profiles every 60 seconds
python -m dreamsync govee-live --device 10.0.0.1:7:primary:ptreal --duration 300 \
    --profile-rotation aurora,neon_city,midnight_rave --rotation-interval 60 --debug-mood

# Validate a profile's YAML structure
python -m dreamsync profile-validate aurora
```

Editing a profile YAML under `src/dreamsync/profiles/` during a live session triggers a hot-reload within ~2 seconds.

| Flag | Default | Description |
|------|---------|-------------|
| `--profile` | none | Load a named color profile |
| `--profile-rotation` | none | Comma-separated profile names to rotate through |
| `--rotation-interval` | 60 | Seconds between profile rotations |

### Device health monitoring

Enable periodic probing to detect offline/online transitions and auto-pause/resume devices:

```bash
python -m dreamsync session --config devices.yaml --health-monitor --health-interval 30 --debug-mood
```

| Flag | Default | Description |
|------|---------|-------------|
| `--health-monitor` | off | Enable periodic device health probing |
| `--health-interval` | 30 | Seconds between health probes |
| `--health-discovery` | off | Scan for new devices on the network |

When a device goes offline (3 consecutive failed probes), its adapter is paused. When it comes back (2 consecutive successes), it resumes automatically. The audio pipeline is never blocked.

## List audio devices

```bash
python -m dreamsync devices
```

## Run tests

```bash
python -m pytest tests/ -v
```

527 tests covering all subsystems:

| Test file | Tests | Scope |
|---|---|---|
| `test_song_boundary.py` | 14 | Boundary detector, reset methods for BPM/Director/Mood/Effects |
| `test_crossfade_boundary.py` | 13 | Crossfade detector: signal voting, thresholds, cooldown, confirm frames |
| `test_govee_ble.py` | 54 | BLE adapter, packet builders, threading, keep-alive |
| `test_telemetry.py` | 11 | JSONL writing, file rotation, song summaries, session summary |
| `test_config_watcher.py` | 21 | ConfigWatcher mtime detection/reload/add/remove, thread safety |
| `test_auto_detect.py` | 24 | Auto-detect role classification, latency probing |
| `test_profile.py` | 63 | Profile loader/validator, EffectCycler integration, ProfileWatcher, rotation, all 8 built-ins |
| `test_device_health.py` | 29 | Health monitor probe loop, offline/online thresholds, anti-flap, role reclass, discovery |

## Architecture

```
System Audio → LiveBpmEstimator → beat events + BPM
                                      │
                                      ▼
                               BeatRippleController
                                      │
                                      ▼
                              LightingIntent { mode, intensity, color, bpm }
                                      │
                                      ▼
                              SegmentRenderer → RGB frame buffer (N segments)
                                      │
                                      ▼
                              GoveeLanAdapter → UDP packet to device:4003
```

### Module map

| Component | Location |
|---|---|
| Audio capture (WASAPI loopback) | `src/dreamsync/live.py` |
| Beat detection (hybrid onset, adaptive threshold, kick isolation) | `src/dreamsync/bpm.py` |
| Composite energy metric (RMS + spectral flux + bass + onset) | `src/dreamsync/director.py` |
| Mood classification (CHILL / GROOVE / HYPE / DROP) | `src/dreamsync/mood.py` |
| Effect cycling + color profiles | `src/dreamsync/effects.py`, `profile.py` |
| Segment rendering (SOLID, PULSE, SCROLL, BREATHE, STROBE, WAVE, GRADIENT) | `src/dreamsync/render.py` |
| LAN output (ptreal, razer, colorwc over UDP) | `src/dreamsync/output/govee_lan.py` |
| BLE output (bleak GATT, mood-follower mode) | `src/dreamsync/output/govee_ble.py` |
| Auto-detect device roles (latency-based classification) | `src/dreamsync/output/auto_detect.py` |
| Device health monitor (probe loop, offline/online, role reclass) | `src/dreamsync/device_health.py` |
| Song boundary detection (silence-gap + crossfade voting) | `src/dreamsync/live.py` |
| Per-song telemetry (JSONL per song, session summary) | `src/dreamsync/telemetry.py` |
| Hot-reload device config (mtime polling, diff, atomic swap) | `src/dreamsync/config_watcher.py` |
| Infinite session runner (YAML config + Ctrl+C shutdown) | `src/dreamsync/session.py` |

### Transport protocols

| Protocol | Packet type | Use case |
|----------|-------------|----------|
| `razer` | DreamView per-LED binary | Strips with many segments (e.g. H808A, 25 LEDs) |
| `ptreal` | BLE-over-LAN per-segment | Strips with IC segments (e.g. H612F, 7 segments) |
| `colorwc` | Whole-strip single color | Fallback for unsupported devices |

### BLE protocol reference

GATT identifiers (no authentication or pairing required):

| Identifier | UUID |
|---|---|
| Service | `00010203-0405-0607-0809-0a0b0c0d1910` |
| Write Characteristic | `00010203-0405-0607-0809-0a0b0c0d2b11` |
| Notify/Read Characteristic | `00010203-0405-0607-0809-0a0b0c0d2b10` |

All commands use a fixed 20-byte structure:

```
Byte:  [0]    [1]     [2]      [3..18]        [19]
       IDENT  CMD     SUB      PAYLOAD+PAD    XOR_CHECKSUM
```

| Command | Header | Devices | Description |
|---|---|---|---|
| Power on | `33 01 01` | All | Power on |
| Power off | `33 01 00` | All | Power off |
| Brightness | `33 04 [0x00-0xFF]` | All | 0=off, 255=max |
| Manual color | `33 05 02 RR GG BB` | H6001, H6127, H6159 | Whole-device single color |
| Bulb color | `33 05 0D RR GG BB` | H6006, H615B | Bulb-specific direct color |
| Segment color | `33 05 15 01 RR GG BB [pad] [bitmask]` | H617A, H612F, H6199 | Per-segment with 7-byte bitmask |
| Keep-alive | `AA 01` | All | Send every ~2s to prevent disconnect |

The `ptreal` LAN transport wraps these same 20-byte BLE packets in a JSON/base64 envelope for UDP. Packets are byte-identical; packet builders from `govee_lan.py` are reused by the BLE adapter.

Measured BLE latency — single device (H617A, 100 writes): median 4.1ms, P95 9.0ms, max 17.0ms. Multi-device (7 BLE + 2 LAN, 30 rounds at 5 Hz): median 4-5ms per device, P95 15-17ms.

---

## Tuning reference

### Mood thresholds (`MoodConfig` in `src/dreamsync/mood.py`)

| Parameter | Default | Range | Controls |
|---|---|---|---|
| `chill_energy_ceiling` | 0.20 | 0.10-0.35 | Max energy to enter CHILL |
| `chill_energy_exit` | 0.28 | ceiling+0.05-0.10 | Energy to leave CHILL |
| `groove_energy_ceiling` | 0.50 | 0.35-0.65 | Energy above this → HYPE |
| `groove_energy_exit_low` | 0.15 | 0.08-0.25 | Below this from GROOVE → CHILL |
| `groove_energy_exit_high` | 0.58 | ceiling+0.05-0.10 | Above this from GROOVE → HYPE |
| `hype_energy_exit` | 0.40 | 0.30-0.50 | Below this from HYPE → GROOVE |
| `stability_threshold` | 0.07 | 0.03-0.12 | Max stability for "stable beat" |
| `stability_exit` | 0.09 | threshold+0.01-0.04 | Above this = unstable beat |
| `min_bpm_for_groove` | 70.0 | 50.0-90.0 | BPM floor for GROOVE/HYPE |
| `drop_energy_spike` | 0.25 | 0.15-0.40 | Required energy jump for DROP |
| `drop_energy_dip` | 0.15 | 0.08-0.25 | Energy must dip below this before DROP |
| `drop_window` | 0.5s | 0.3-1.5 | Spike must occur within this time after dip |
| `drop_cooldown` | 10.0s | 5.0-20.0 | Min seconds between DROPs |
| `drop_duration` | 3.0s | 1.5-5.0 | How long DROP lasts |
| `min_dwell_seconds` | 4.0s | 2.0-10.0 | Min time in any mood before switching |

### Composite energy weights (`DirectorConfig` in `src/dreamsync/director.py`)

| Weight | Default | Description |
|---|---|---|
| `w_rms` | 0.25 | Relative volume (auto-calibrated) |
| `w_spectral_flux` | 0.30 | Frame-to-frame spectral change (punchiness) |
| `w_bass_ratio` | 0.20 | Energy below 200Hz (genre sensitivity) |
| `w_onset_strength` | 0.25 | Percussive transient strength |

Weights must sum to 1.0. Self-calibration takes ~10-15 seconds.

### Effect pools

```
CHILL:   warm_glow (2.0), slow_breathe (3.0), color_breathe (1.0), wave_drift (2.0), gradient_flow (2.0)
GROOVE:  color_breathe (1.0), beat_pulse (3.0), color_scroll (2.0), wave_drift (1.0)
HYPE:    fast_scroll (2.0), beat_pulse (1.0)
DROP:    drop_blast (1.0)
```

### Common tuning issues

| Problem | Fix |
|---|---|
| Energy stuck low, always CHILL | Lower `chill_energy_ceiling` to 0.12-0.15, check mic placement |
| Energy always high, never CHILL | Raise `chill_energy_ceiling` to 0.30-0.35 |
| Thrashing GROOVE ↔ HYPE | Widen hysteresis: lower `hype_energy_exit`, raise `groove_energy_exit_high` |
| Thrashing CHILL ↔ GROOVE | Widen gap: lower `groove_energy_exit_low`, raise `chill_energy_exit` |
| DROP never fires | Lower `drop_energy_spike` to 0.18-0.20, widen `drop_window` to 1.0s |
| DROP fires on random loud moments | Raise `drop_energy_spike` to 0.35, increase `drop_cooldown` |
| Moods change too quickly | Increase `min_dwell_seconds` to 6.0-10.0 |
| Moods change too slowly | Decrease `min_dwell_seconds` to 2.0-3.0 (not below 2.0) |

---

## Troubleshooting

### BLE

| Issue | Cause | Fix |
|---|---|---|
| Device doesn't respond | Wrong GATT characteristic or needs different protocol variant | Run GATT enumeration, try segment vs bulb protocol |
| Colors are wrong | BGR byte order or HSV mode on some models | Note requested vs observed, adjust packet builder |
| Connection drops | Low RSSI or Wi-Fi interference | Move closer, check RSSI > -75, disable 2.4GHz Wi-Fi |
| bleak won't import | Missing WinRT backend | `pip install bleak[winrt]`, need Python 3.11+, Windows 10 1709+ |
| Govee app blocks connection | BLE is single-connection | Close/force-quit Govee Home app before running |
| Latency spikes after idle | Connection went stale | Keep-alive packets every 2s (already implemented) |

---

## Dependencies

### Required
- `sounddevice`, `numpy`, `scipy` — audio capture and DSP
- `pyyaml` — device config files and color profiles

### Optional
- `bleak>=0.21` — BLE support (`pip install dreamsync-music-sync[ble]`)
