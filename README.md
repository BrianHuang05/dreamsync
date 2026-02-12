# Govee DreamView LAN Music Sync

Windows-first Python project for local audio-reactive lighting control. See project scope document for goals and phases.

## Quick start

Create a virtual environment and install deps:

```bash
python -m venv .venv
. .venv/Scripts/activate
pip install -e .
```

Run the CLI stub:

```bash
python -m dreamsync --help
```

Run the offline WAV feature extractor (JSON Lines output):

```bash
python -m dreamsync wav path/to/audio.wav --jsonl out/features.jsonl
```

Replay WAV through Director + FakeOutput and write intent logs:

```bash
python -m dreamsync replay path/to/audio.wav --jsonl out/replay_intents.jsonl
```

Send a single test intent to LedFx (D3.1 smoke test):

```bash
python -m dreamsync ledfx-test --base-url http://127.0.0.1:8888 --virtual-id my_virtual --mode pulse
```

Run the D3.2 end-to-end demo (WAV -> Director -> LedFx) and save run logs:

```bash
python -m dreamsync ledfx-replay path/to/audio.wav --base-url http://127.0.0.1:8888 --virtual-id my_virtual --realtime --jsonl out/ledfx_replay.jsonl
```

List available system audio input devices:

```bash
python -m dreamsync devices
```

Run timed real-time capture and write feature stream:

```bash
python -m dreamsync capture --duration 60 --device 1 --jsonl out/live_features.jsonl
```

Plot the extracted features to inspect sync behavior quickly:

```bash
python -m dreamsync plot out/features.jsonl out/features.png
```

Run tests (no external test runner required):

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Director state machine (D2.1) lives in:

```text
src/dreamsync/director.py
```
