# DreamSync — Implementation Reference

## Current State

### What's Implemented

| Component | Status | Location |
|---|---|---|
| Audio capture (WASAPI loopback) | Done | `src/dreamsync/live.py` |
| Beat detection (hybrid onset, adaptive threshold, kick isolation) | Done | `src/dreamsync/bpm.py` |
| Composite energy metric (RMS + spectral flux + bass ratio + onset strength) | Done | `src/dreamsync/director.py` |
| Mood classification (CHILL / GROOVE / HYPE / DROP) | Done | `src/dreamsync/mood.py` |
| Effect cycling (warm_glow, slow_breathe, beat_pulse, fast_scroll, etc.) | Done | `src/dreamsync/effects.py` |
| Segment rendering (SOLID, PULSE, SCROLL, BREATHE, STROBE, WAVE, GRADIENT) | Done | `src/dreamsync/render.py` |
| LAN output (ptreal, razer, colorwc transports over UDP) | Done | `src/dreamsync/output/govee_lan.py` |
| BLE output (bleak GATT, mood-follower mode) | Done | `src/dreamsync/output/govee_ble.py` |
| BLE CLI (govee-ble-scan, govee-ble-test, --ble-device) | Done | `src/dreamsync/cli.py` |
| Multi-device orchestration (LAN 30 Hz + BLE 5 Hz) | Done | `src/dreamsync/output/govee_lan.py` |
| Song boundary detection (silence-gap detector + full state reset) | Done (unit tested, hardware untested) | `src/dreamsync/live.py` |
| Auto-detect device roles (latency-based classification) | Done (unit tested, hardware untested) | `src/dreamsync/output/auto_detect.py` |
| Infinite session runner (YAML config + Ctrl+C shutdown) | Done (unit tested, hardware untested) | `src/dreamsync/session.py` |
| CLI `session` subcommand | Done (untested) | `src/dreamsync/cli.py` |

### Test Coverage

- `tests/test_song_boundary.py` — 14 tests (boundary detector, reset methods for BPM/Director/Mood/Effects)
- `tests/test_govee_ble.py` — 54 tests (BLE adapter, packet builders, threading, keep-alive)
- 352 total tests passing on current branch

---

## Device Fleet

### LAN Devices

| Device | Model | IP | Segments | Transport |
|---|---|---|---|---|
| LED strip (small) | H612F | `10.126.166.180` | 7 | `ptreal` |
| LED strip (large) | H808A | `10.126.166.156` | 25 | `razer` |

### BLE Devices

| Device | Model | Address | Protocol |
|---|---|---|---|
| LED strip (RGBIC) | H617A | `C7:90:80:C6:44:74` | segment |
| LED strip (RGBIC) | H612F | `DD:6E:05:86:6A:53` | segment |
| DreamView | H808A | `DA:B9:84:C6:35:57` | segment |
| LED strip | H6199 | `D6:36:34:39:35:3C` | segment |
| Lamp | H6097 | `D7:01:86:46:44:59` | segment |
| Smart bulb x6 | H6006 | Various (`ihoment_` prefix) | bulb |

### CLI Quick Reference

```bash
# LAN devices
--device 10.126.166.180:7:primary:ptreal --device 10.126.166.156:25:primary:razer

# BLE devices
--ble-device C7:90:80:C6:44:74 --ble-device D0:C9:07:C5:14:45:bulb

# Mixed session
python -m dreamsync govee-live \
    --device 10.126.166.180:7:primary:ptreal \
    --device 10.126.166.156:25:primary:razer \
    --ble-device C7:90:80:C6:44:74 \
    --ble-device D0:C9:07:C5:14:45:bulb \
    --duration 120 --debug-mood
```

---

## BLE Protocol Reference

### GATT Identifiers

| Identifier | UUID |
|---|---|
| Service | `00010203-0405-0607-0809-0a0b0c0d1910` |
| Write Characteristic | `00010203-0405-0607-0809-0a0b0c0d2b11` |
| Notify/Read Characteristic | `00010203-0405-0607-0809-0a0b0c0d2b10` |

No authentication or pairing required.

### Packet Format

All commands use a fixed 20-byte structure:

```
Byte:  [0]    [1]     [2]      [3..18]        [19]
       IDENT  CMD     SUB      PAYLOAD+PAD    XOR_CHECKSUM
```

| Field | Size | Description |
|---|---|---|
| Identifier | 1 byte | `0x33` (commands), `0xAA` (keep-alive) |
| Command | 1 byte | `0x01`=power, `0x04`=brightness, `0x05`=color/mode |
| Sub-command | 1 byte | Varies by command type |
| Payload | 16 bytes | Command-specific data, zero-padded |
| Checksum | 1 byte | XOR of bytes 0-18 |

### Command Reference

| Command | Header | Devices | Description |
|---|---|---|---|
| Power on | `33 01 01` | All | Power on |
| Power off | `33 01 00` | All | Power off |
| Brightness | `33 04 [0x00-0xFF]` | All | 0=off, 255=max |
| Manual color | `33 05 02 RR GG BB` | H6001, H6127, H6159 | Whole-device single color |
| Bulb color | `33 05 0D RR GG BB` | H6006, H615B | Bulb-specific direct color |
| Segment color | `33 05 15 01 RR GG BB [pad] [bitmask]` | H617A, H612F, H6199 | Per-segment with 7-byte bitmask |
| Keep-alive | `AA 01` | All | Send every ~2s to prevent disconnect |

### LAN vs BLE Relationship

The ptreal transport wraps the same 20-byte BLE packets in a JSON/base64 envelope for UDP:

```python
# LAN: JSON-wrapped
{"msg":{"cmd":"ptReal","data":{"command":["MwUVAf8AAAAAAAAAAAB/AA=="]}}}
# BLE: raw 20-byte packet to GATT characteristic
b'\x33\x05\x15\x01\xff\x00\x00\x00\x00\x00\x00\x00\x00\x7f\x00...'
```

The packets are byte-identical. Packet builders from `govee_lan.py` are reused by the BLE adapter.

### Measured BLE Latency

Single device (H617A, 100 writes): median 4.1ms, P95 9.0ms, max 17.0ms.

Multi-device (7 BLE + 2 LAN, 30 rounds at 5 Hz): median 4-5ms per device, P95 15-17ms.

BLE latency is ~20x higher than LAN UDP but well under the 200ms threshold for mood following at 5 Hz.

---

## Next Steps

### Hardware Testing

All recent work (song boundary detection, auto-detect, session runner) has been unit tested but **not yet validated on hardware**. The following need live testing:

1. **Song boundary detection** — Play a multi-song playlist with `--debug-mood` and verify:
   - "Song boundary detected" messages appear at track transitions
   - BPM/mood adapt quickly to the new song after reset
   - No false triggers during quiet musical passages or mid-song breakdowns
   - Tuning parameters (`silence_threshold_rms`, `min_silence_seconds`, `min_song_seconds`) may need adjustment based on real playback gaps

2. **Auto-detect role classification** — Run `dreamsync session --config devices.yaml` and verify:
   - LAN devices are always assigned `realtime` (no latency classification)
   - BLE devices are classified by measured latency (`scripts/ble_latency_bench.py` protocol): `realtime` (<20ms), `follower` (20-200ms), or `slow` (>200ms)
   - Unreachable devices are logged and skipped
   - BLE latency numbers match expectations from earlier manual testing (median 4-5ms per device)

3. **Infinite session mode** — Run `dreamsync session --config devices.yaml --debug-mood` for 10+ minutes and verify:
   - Ctrl+C cleanly shuts down all devices
   - No memory growth or degraded performance over time
   - Song boundaries fire and state resets work across multiple songs

### Future Work

- **Crossfade-aware boundaries**: Some players crossfade tracks (audio never hits silence). Could detect BPM discontinuities or spectral centroid jumps as an alternative trigger.
- **Per-song telemetry**: Log BPM/mood/energy stats per song (between boundaries) for post-session analysis.
- **Device config hot-reload**: Watch `devices.yaml` for changes and add/remove devices without restarting the session.

---

## Tuning Reference

### Mood Thresholds (MoodConfig in `src/dreamsync/mood.py`)

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

### Composite Energy Weights (DirectorConfig in `src/dreamsync/director.py`)

| Weight | Default | Description |
|---|---|---|
| `w_rms` | 0.25 | Relative volume (auto-calibrated) |
| `w_spectral_flux` | 0.30 | Frame-to-frame spectral change (punchiness) |
| `w_bass_ratio` | 0.20 | Energy below 200Hz (genre sensitivity) |
| `w_onset_strength` | 0.25 | Percussive transient strength |

Weights must sum to 1.0. Self-calibration takes ~10-15 seconds.

### EMA Smoothing

| Parameter | Default | Range |
|---|---|---|
| `ema_alpha_rms` | 0.20 | 0.05-0.40 |
| `ema_alpha_spectral_flux` | 0.15 | 0.05-0.30 |
| `ema_alpha_bass_ratio` | 0.15 | 0.05-0.30 |
| `ema_alpha_onset_strength` | 0.15 | 0.05-0.30 |

### Effect Pools

```
CHILL:   warm_glow (2.0), slow_breathe (3.0), color_breathe (1.0), wave_drift (2.0), gradient_flow (2.0)
GROOVE:  color_breathe (1.0), beat_pulse (3.0), color_scroll (2.0), wave_drift (1.0)
HYPE:    fast_scroll (2.0), beat_pulse (1.0)
DROP:    drop_blast (1.0)
```

### Common Tuning Issues

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

## BLE Troubleshooting

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
- `pyzmq` — not currently used in live path but in pyproject.toml

### Optional
- `bleak>=0.21` — BLE support (`pip install dreamsync-music-sync[ble]`)
- `pyyaml` — device config file (needed for Step 1)
