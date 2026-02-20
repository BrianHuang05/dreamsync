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
| `--audio-device` | system default | PortAudio input device ID |

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

## List audio devices

```bash
python -m dreamsync devices
```

## Run tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
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
