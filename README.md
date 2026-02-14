# Govee DreamView LAN Music Sync

Windows-first Python project for local audio-reactive lighting control. See project scope document for goals and phases.

## Quick start

Create a virtual environment and install deps:

```bash
python -m venv .venv
. .venv/Scripts/activate
pip install -e .
pip install ledfx
```

### Fixing C-extension / version-mismatch errors

If you see `ImportError` messages about NumPy, SciPy, or other compiled
packages being incompatible with your Python version (e.g. built for
`cp313` but running `cpython-312`), the venv has stale binaries from a
different Python install. Rebuild it from scratch:

```bash
deactivate 2>/dev/null
python -m venv .venv --clear   # wipes and recreates the venv
. .venv/Scripts/activate
pip install -e .
pip install ledfx
```

Run the CLI stub:

```bash
python -m dreamsync --help
```

## Device discovery

LedFx needs the Govee device's LAN IP address. To find it, run the multicast ping sweeper:

```bash
python scripts/pingsweeper.py
```

This sends a UDP scan to the Govee multicast group and prints any responding device IPs. Use the discovered IP when adding the device in the LedFx UI.

## Live usage (today's workflow)

You need two terminals.

**Terminal 1** — start the LedFx server (installs if needed, waits for API, lists virtuals):

```powershell
powershell -ExecutionPolicy Bypass -File .\start_ledfx.ps1
```

**Terminal 2** — run dreamsync commands:

```bash
. .venv/Scripts/activate
```

1) Make sure your virtual(s) exist in LedFx (the startup script will list them).
2) Use the beat tester to verify timing.
3) Use the full director mode for the actual lightshow behavior.

Beat-only tester (prints beat timing, flashes on beat). Clears any existing effect first:

```bash
python -m dreamsync ledfx-beat --duration 60 --base-url http://127.0.0.1:8888 --virtual-id vcouch
```

Beat ripple (slow color-wave that changes color on each beat). Clears any existing effect first:

```bash
python -m dreamsync ledfx-ripple --duration 60 --base-url http://127.0.0.1:8888 --virtual-id vdown --effect-type scroll
```

Customize brightness, colors, or try a different LedFx effect type:

```bash
python -m dreamsync ledfx-ripple --duration 60 --base-url http://127.0.0.1:8888 --virtual-id vcouch --brightness 0.9 --effect-type wavelength --colors '#ff0000,#00ff00,#0000ff'
```

Full director (smoother, concert-style behavior). Clears any existing effect first:

```bash
python -m dreamsync ledfx-live --duration 60 --base-url http://127.0.0.1:8888 --virtual-id vcouch
```

### Multi-device support

You can target multiple light strips with `--virtual-id` repeated. Each ID can have an optional role suffix (`:primary` or `:accent`). Without a suffix the device defaults to `primary` (full reactive). The `accent` role locks the strip to ambient mode with reduced intensity.

```bash
# Two devices: couch strip gets full reactive, desk strip gets subdued ambient
python -m dreamsync ledfx-live --duration 60 --base-url http://127.0.0.1:8888 --virtual-id vcouch --virtual-id vdown
```

```bash
# Beat flash across two strips
python -m dreamsync ledfx-beat --duration 60 --base-url http://127.0.0.1:8888 --virtual-id vcouch --virtual-id vdown
```

If you want to leave LedFx untouched, add `--no-force-stop`.

Debug the payloads sent to LedFx:

```bash
python -m dreamsync ledfx-live --duration 20 --base-url http://127.0.0.1:8888 --virtual-id vcouch --debug-ledfx
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

Run unit tests (no external services required):

```bash
PYTHONPATH=src python3 -m unittest tests.test_output_ledfx tests.test_output_roles tests.test_basic_controller tests.test_director tests.test_cli_plot -v
```

Run the full test suite (requires LedFx — start it first with `powershell -ExecutionPolicy Bypass -File .\start_ledfx.ps1`):

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Director state machine (D2.1) lives in:

```text
src/dreamsync/director.py
```
