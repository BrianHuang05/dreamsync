# DreamSync

DreamSync is a Windows-first Python project for local audio-reactive lighting with Govee LAN devices. It listens to system audio, detects beats and energy in real time, and streams per-segment RGB frames directly over LAN UDP without requiring cloud control or LedFx.

This page is a lightweight project entry point. For the full command reference, architecture notes, troubleshooting, and extended workflow examples, see [README.md](README.md).

## What it does

- Discovers compatible Govee devices on your local network
- Runs real-time music sync from system audio
- Supports single-device and multi-device lighting setups
- Captures MP3s from system audio for later analysis and playback
- Builds synchronized light shows with an analyze -> compile -> play pipeline
- Falls back to dry-run and dummy-device workflows for testing without hardware

## Quick start

Create a virtual environment and install the project:

```bash
python -m venv .venv
. .venv/Scripts/activate
pip install -e .
```

## Common workflows

### Discover devices

```bash
python -m dreamsync govee-scan
```

### Run live music sync

```bash
python -m dreamsync govee-live \
    --device-ip 10.0.0.123 \
    --segments 15 \
    --duration 120 \
    --render-mode scroll \
    --brightness 0.8
```

### Run a multi-device session from YAML config

```bash
python -m dreamsync session --config devices.yaml --debug-mood
```

Example config:

```yaml
devices:
  - name: "Desk Strip"
    address: "192.168.1.100"
    segments: 15
    role: primary
    brightness_scale: 0.4
  - name: "Ceiling Bulb"
    address: "192.168.1.101"
    segments: 1
    role: primary
    brightness_scale: 1.0
```

### Test without lights

```bash
python -m dreamsync play path/to/song.mp3 --dry-run --debug
python -m dreamsync session --config dev/devices-dummy.yaml --pipeline --capture --debug-mood
```

## Show pipeline

DreamSync can turn captured or existing MP3 files into synchronized light shows:

```text
MP3 -> analyze -> .analysis.json -> compile -> .show.json -> play
```

Typical commands:

```bash
python -m dreamsync analyze path/to/song.mp3 --summary
python -m dreamsync compile path/to/song.analysis.json --summary
python -m dreamsync play path/to/song.mp3 --config devices.yaml --debug
```

For a one-command workflow:

```bash
python -m dreamsync compile-and-play path/to/song.mp3 --config devices.yaml --debug
```

## Project layout

- `src/dreamsync/`: main package and CLI implementation
- `dev/`: development configs and broader test coverage
- `scripts/`: utility scripts
- `out/`: generated captures, analysis, shows, and session output

## Where to go next

- Full setup and usage: [README.md](README.md)
- Device config template: [devices-template.yaml](devices-template.yaml)
- Package metadata: [pyproject.toml](pyproject.toml)
