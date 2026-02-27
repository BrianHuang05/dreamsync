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

See `IMPLEMENTATION.md` for device config format and auto-detect details.

## List audio devices

```bash
python -m dreamsync devices
```

## Run tests

```bash
python -m pytest tests/ -v
```

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

### Transport protocols

| Protocol | Packet type | Use case |
|----------|-------------|----------|
| `razer` | DreamView per-LED binary | Strips with many segments (e.g. H808A, 25 LEDs) |
| `ptreal` | BLE-over-LAN per-segment | Strips with IC segments (e.g. H612F, 7 segments) |
| `colorwc` | Whole-strip single color | Fallback for unsupported devices |
