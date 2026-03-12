# Interactive Audio Output Device Picker

## Context

Today, choosing an audio output device for show playback requires a two-step workflow:

1. Run `python -m dreamsync devices` to see a JSON dump of all audio devices with their numeric IDs
2. Manually pass the ID via `--playback-device 4` or `--audio-device 5` to the actual command

This is clunky for several reasons:

- **IDs are opaque** — `4` vs `5` vs `7` means nothing without the listing
- **IDs can change** — plugging in AirPods, an aux cable, or a USB DAC shifts device IDs between sessions
- **No feedback-safety warning** — nothing prevents the user from selecting CABLE Input (the capture device) as the playback output, which would create a feedback loop in the streaming pipeline

### Audio routing constraint

The system must always route application audio through VB-Cable for capture:

```
Spotify → CABLE Input (system default) → CABLE Output → FFmpeg capture
Show player → [user-selected device] → physical speakers/headphones
```

The picker only affects the `sounddevice.OutputStream` device used by the show player (`AudioPlayer` in `show/player.py`). It does not change the Windows system default output (CABLE Input stays as default for capture).

### Current code path

- `list_output_devices()` in `audio/system_input.py` already enumerates all output devices via `sounddevice.query_devices()`
- `AudioPlayer.__init__(device=...)` in `show/player.py` passes the device ID to `sounddevice.OutputStream`
- The `devices` subcommand in `cli.py` prints raw JSON — functional but not user-friendly

## Prerequisites

- `sounddevice` installed (already a required dependency)
- No new dependencies needed — uses only stdlib (`input()`, ANSI escapes for formatting)

## Goals

1. Add a `pick_output_device()` interactive function that presents a numbered menu of output devices and returns the selected device ID
2. Wire the picker into the CLI so `--playback-device pick` (or omitting the flag with `--pick-device`) triggers it before the session/play loop starts
3. Warn (but don't block) when the user selects a device that looks like a virtual cable / capture device
4. Improve the `devices` subcommand output from raw JSON to a formatted table
5. Keep full backward compatibility — existing `--playback-device <int>` and `--audio-device <int>` work unchanged

## Files to Create

| File | Purpose |
|------|---------|
| `dev/tests/test_device_picker.py` | Unit tests for `pick_output_device()` and helpers |

## Files to Modify

| File | Change |
|------|--------|
| `src/dreamsync/audio/system_input.py` | Add `pick_output_device()`, `format_device_table()`, `is_capture_device()` |
| `src/dreamsync/cli.py` | Wire picker into `session`, `play`, `govee-live` subcommands; improve `devices` subcommand output |

## Design

### `pick_output_device()` (new function in `audio/system_input.py`)

Interactive numbered menu that lists output devices and prompts for a selection. Returns the selected device ID (int).

```python
def pick_output_device(
    *,
    warn_capture: bool = True,
    capture_keywords: tuple[str, ...] = ("cable input", "virtual cable", "vb-audio"),
) -> int:
    """Show an interactive menu of audio output devices and return the selected ID.

    Raises RuntimeError if no output devices are found.
    Raises KeyboardInterrupt if the user cancels (Ctrl+C).
    """
```

Example output:

```
  Audio output devices:
  ─────────────────────────────────────────────────────────────
   #  ID   Device                                     Host API
  ─────────────────────────────────────────────────────────────
   1   3   Speakers (Realtek High Definition Audio)    WASAPI
   2   5   Headphones (Realtek High Definition Audio)  WASAPI
   3   7   AirPods Pro (Brian)                         WASAPI
   4   9   CABLE Input (VB-Audio Virtual Cable)        WASAPI    ⚠ capture
  ─────────────────────────────────────────────────────────────

  Select device [1-4]: 3
  → Using: AirPods Pro (Brian) (device ID 7)
```

If the user selects a capture device (detected via `is_capture_device()`):

```
  Select device [1-4]: 4
  ⚠ Warning: "CABLE Input (VB-Audio Virtual Cable)" looks like a capture device.
    Using this for playback may cause audio feedback in the streaming pipeline.
    Continue? [y/N]: y
  → Using: CABLE Input (VB-Audio Virtual Cable) (device ID 9)
```

### `is_capture_device()` (new function)

```python
def is_capture_device(
    name: str,
    keywords: tuple[str, ...] = ("cable input", "virtual cable", "vb-audio"),
) -> bool:
    """Return True if the device name matches known capture/virtual-cable patterns."""
    lower = name.lower()
    return any(kw in lower for kw in keywords)
```

### `format_device_table()` (new function)

```python
def format_device_table(
    devices: list[dict],
    *,
    kind: str = "output",
    mark_capture: bool = False,
) -> str:
    """Format a list of device dicts as an aligned text table."""
```

Shared by both the picker and the improved `devices` subcommand.

### CLI Wiring

**Option A (recommended): `--playback-device pick`**

Allow the existing `--playback-device` argument to accept either an int or the literal string `"pick"`:

```python
# In argument parser
session_cmd.add_argument(
    "--playback-device",
    default=None,
    help="Output audio device ID, or 'pick' for interactive selection.",
)
```

At resolution time (before starting the session/player):

```python
playback_device = args.playback_device
if playback_device == "pick":
    from dreamsync.audio.system_input import pick_output_device
    playback_device = pick_output_device()
elif playback_device is not None:
    playback_device = int(playback_device)
```

This approach adds zero new flags and keeps the existing `--playback-device 4` path unchanged. The same pattern applies to `--audio-device` on the `play` command.

**Option B (additive): `--pick-device` boolean flag**

```python
session_cmd.add_argument("--pick-device", action="store_true",
    help="Interactively choose audio output device before starting.")
```

This is simpler but adds yet another flag. Option A is preferred.

### Improved `devices` subcommand

Replace the raw JSON dump with a formatted table:

```
=== Audio Input Devices (for --audio-device) ===

  ID   Device                                     Host API       Channels  Sample Rate
  ───  ─────────────────────────────────────────   ──────────────  ────────  ───────────
   1   Stereo Mix (Realtek)                        WASAPI          2         48000
   4   CABLE Output (VB-Audio Virtual Cable)       WASAPI          2         44100

=== Audio Output Devices (for --playback-device) ===

  ID   Device                                     Host API       Channels  Sample Rate
  ───  ─────────────────────────────────────────   ──────────────  ────────  ───────────
   3   Speakers (Realtek High Definition Audio)    WASAPI          2         48000
   5   Headphones (Realtek High Definition Audio)  WASAPI          2         48000
   7   AirPods Pro (Brian)                         WASAPI          2         44100
   9   CABLE Input (VB-Audio Virtual Cable)        WASAPI          2         44100     ⚠ capture
```

Keep `--json` flag for machine-readable output (backward compat for scripts):

```python
devices_cmd.add_argument("--json", action="store_true", help="Output raw JSON (machine-readable).")
```

---

## Steps

### Step 1: Add `is_capture_device()` and `format_device_table()` helpers

**Prerequisites:** None — pure functions, no I/O.

**Deliverables:**
- `is_capture_device()` in `src/dreamsync/audio/system_input.py`
- `format_device_table()` in `src/dreamsync/audio/system_input.py`
- Tests in `dev/tests/test_device_picker.py`

**Implementation details:**

`is_capture_device(name, keywords)`:
- Case-insensitive substring match against a tuple of keywords
- Default keywords: `("cable input", "virtual cable", "vb-audio")`
- Returns `bool`

`format_device_table(devices, kind, mark_capture)`:
- Takes a list of device dicts (from `list_output_devices()` or `list_input_devices()`)
- Computes column widths dynamically from the data
- Appends `"⚠ capture"` marker if `mark_capture=True` and `is_capture_device(name)` matches
- Returns a multi-line string (no trailing newline)

**Tests (8):**
1. `test_is_capture_device_cable_input` — `"CABLE Input (VB-Audio Virtual Cable)"` → `True`
2. `test_is_capture_device_speakers` — `"Speakers (Realtek)"` → `False`
3. `test_is_capture_device_case_insensitive` — `"cable INPUT"` → `True`
4. `test_is_capture_device_custom_keywords` — custom keywords tuple respected
5. `test_format_table_basic` — 2 devices → table with header + 2 rows, all aligned
6. `test_format_table_empty` — empty list → header only (or informative message)
7. `test_format_table_marks_capture_devices` — `mark_capture=True` → capture device row has `"⚠"` marker
8. `test_format_table_no_mark_when_disabled` — `mark_capture=False` → no markers

**Completion criteria:**
- All 8 tests pass
- No changes to existing behavior

**Verify:**
```bash
python -m pytest dev/tests/test_device_picker.py -v -k "is_capture or format_table"
```

---

### Step 2: Add `pick_output_device()` interactive function

**Prerequisites:** Step 1 (helpers exist).

**Deliverables:**
- `pick_output_device()` in `src/dreamsync/audio/system_input.py`
- Tests in `dev/tests/test_device_picker.py`

**Implementation details:**

```python
def pick_output_device(
    *,
    warn_capture: bool = True,
    capture_keywords: tuple[str, ...] = ("cable input", "virtual cable", "vb-audio"),
) -> int:
    devices = list_output_devices()
    if not devices:
        raise RuntimeError("No audio output devices found.")

    print()
    print(format_device_table(devices, kind="output", mark_capture=warn_capture))
    print()

    while True:
        try:
            raw = input(f"  Select device [1-{len(devices)}]: ").strip()
        except EOFError:
            raise KeyboardInterrupt

        if not raw.isdigit():
            print(f"  Please enter a number between 1 and {len(devices)}.")
            continue

        choice = int(raw)
        if choice < 1 or choice > len(devices):
            print(f"  Please enter a number between 1 and {len(devices)}.")
            continue

        selected = devices[choice - 1]
        name = str(selected["name"])
        dev_id = int(selected["id"])

        # Capture device warning
        if warn_capture and is_capture_device(name, capture_keywords):
            confirm = input(
                f'  ⚠ Warning: "{name}" looks like a capture device.\n'
                f"    Using this for playback may cause audio feedback.\n"
                f"    Continue? [y/N]: "
            ).strip().lower()
            if confirm not in ("y", "yes"):
                continue  # re-prompt

        print(f"  → Using: {name} (device ID {dev_id})")
        return dev_id
```

**Tests (7):**

Tests mock `list_output_devices()` and `builtins.input` to avoid real hardware and stdin:

1. `test_pick_returns_device_id` — mock 3 devices, input="1" → returns first device's ID
2. `test_pick_last_device` — input="3" → returns third device's ID
3. `test_pick_invalid_then_valid` — input sequence ["abc", "0", "2"] → retries, returns second device
4. `test_pick_no_devices_raises` — empty device list → `RuntimeError`
5. `test_pick_capture_device_warned_then_confirmed` — select capture device, input=["4", "y"] → returns ID with warning shown
6. `test_pick_capture_device_warned_then_rejected` — select capture device, input=["4", "n", "1"] → re-prompts, returns non-capture device
7. `test_pick_eof_raises_keyboard_interrupt` — `input()` raises `EOFError` → `KeyboardInterrupt`

**Completion criteria:**
- All 7 tests pass
- Interactive picker works when called directly:
  ```python
  from dreamsync.audio.system_input import pick_output_device
  dev_id = pick_output_device()
  ```

**Verify:**
```bash
python -m pytest dev/tests/test_device_picker.py -v -k "test_pick"
```

---

### Step 3: Wire picker into CLI subcommands

**Prerequisites:** Steps 1 and 2.

**Deliverables:**
- `--playback-device` on `session` accepts `"pick"` or an integer (currently `type=int`)
- `--audio-device` on `play` accepts `"pick"` or an integer
- Picker runs before the main loop starts (blocking interactive prompt)

**Implementation details:**

Change the argument type from `int` to `str` (with downstream `int()` conversion):

```python
# session subcommand — change existing --playback-device
session_cmd.add_argument(
    "--playback-device",
    default=None,
    help="Output audio device ID, or 'pick' for interactive selection.",
)

# play subcommand — change existing --audio-device
play_cmd.add_argument(
    "--audio-device",
    default=None,
    help="Output audio device ID, or 'pick' for interactive selection (None = system default).",
)
```

Add a resolver helper in `cli.py`:

```python
def _resolve_output_device(raw_value: str | None) -> int | None:
    """Resolve --playback-device / --audio-device value to an int or None."""
    if raw_value is None:
        return None
    if raw_value == "pick":
        from dreamsync.audio.system_input import pick_output_device
        return pick_output_device()
    try:
        return int(raw_value)
    except ValueError:
        raise SystemExit(f"Error: invalid device value '{raw_value}'. Use an integer ID or 'pick'.")
```

Call `_resolve_output_device()` early in each subcommand handler, before starting any threads or audio pipelines.

**Tests:**
- No new automated tests — CLI argument wiring tested manually
- Existing tests must still pass (default `None` path unchanged)

**Completion criteria:**
- `python -m dreamsync session --config devices.yaml --pipeline --playback-device pick` shows the picker
- `python -m dreamsync play song.mp3 --audio-device pick` shows the picker
- `python -m dreamsync session --config devices.yaml --playback-device 4` still works (int path)
- Omitting the flag entirely still defaults to `None` (system default)

**Verify:**
```bash
python -m dreamsync session --help | grep playback-device
python -m dreamsync play --help | grep audio-device
# Interactive test:
python -m dreamsync play path/to/song.mp3 --audio-device pick --dry-run --debug
```

---

### Step 4: Improve `devices` subcommand output

**Prerequisites:** Step 1 (`format_device_table()` exists).

**Deliverables:**
- `devices` subcommand prints formatted tables instead of raw JSON
- `--json` flag on `devices` subcommand preserves the old raw JSON output for scripting

**Implementation details:**

Add `--json` flag to `devices` parser:

```python
devices_cmd = sub.add_parser("devices", help="List audio input and output devices.")
devices_cmd.add_argument("--json", action="store_true", dest="json_output",
    help="Output raw JSON (machine-readable).")
```

In the `devices` handler:

```python
if args.command == "devices":
    from dreamsync.audio.system_input import (
        list_input_devices, list_output_devices, format_device_table,
    )
    inputs = list_input_devices()
    outputs = list_output_devices()

    if getattr(args, "json_output", False):
        # Backward-compatible raw JSON
        print("=== Audio Input Devices ===")
        for dev in inputs:
            print(json.dumps(dev, separators=(",", ":")))
        print()
        print("=== Audio Output Devices ===")
        for dev in outputs:
            print(json.dumps(dev, separators=(",", ":")))
    else:
        print()
        print("  Audio Input Devices (for --audio-device)")
        print(format_device_table(inputs, kind="input"))
        print()
        print("  Audio Output Devices (for --playback-device / --audio-device)")
        print(format_device_table(outputs, kind="output", mark_capture=True))
        print()
    return 0
```

**Tests:**
- No automated tests — output formatting verified manually

**Completion criteria:**
- `python -m dreamsync devices` prints a clean, aligned table
- `python -m dreamsync devices --json` prints the old JSON format
- Capture devices are marked with `⚠ capture` in the table

**Verify:**
```bash
python -m dreamsync devices
python -m dreamsync devices --json
```

---

## Tests Summary

| Step | Deliverable | Tests | Dependencies |
|------|-------------|-------|-------------|
| 1 | `is_capture_device()` + `format_device_table()` | 8 | None |
| 2 | `pick_output_device()` | 7 | Step 1 |
| 3 | CLI wiring (`--playback-device pick`) | 0 (manual) | Steps 1-2 |
| 4 | Improved `devices` subcommand | 0 (manual) | Step 1 |
| **Total** | | **15** | |

## Completion Criteria

- [ ] `python -m dreamsync devices` prints a formatted table with capture device warnings
- [ ] `python -m dreamsync devices --json` prints old-style JSON (backward compat)
- [ ] `--playback-device pick` and `--audio-device pick` launch an interactive device picker
- [ ] `--playback-device 4` (integer) still works unchanged
- [ ] Omitting `--playback-device` still defaults to system default
- [ ] Selecting a capture device shows a warning and asks for confirmation
- [ ] All 15 new tests pass
- [ ] All existing tests pass (no regressions)

## Final Verification

```bash
# Run new tests
python -m pytest dev/tests/test_device_picker.py -v

# Run full test suites (no regressions)
python -m pytest tests/ -v
python -m pytest dev/tests/ -v

# Manual: interactive picker
python -m dreamsync play path/to/song.mp3 --audio-device pick --dry-run --debug

# Manual: formatted device listing
python -m dreamsync devices

# Manual: backward compat
python -m dreamsync devices --json
python -m dreamsync play path/to/song.mp3 --audio-device 4 --dry-run --debug
```

## Future Work (out of scope)

- **Persistent default** — save the last-picked device ID to a config file so it's auto-selected next time
- **Device hot-plug detection** — detect when AirPods connect/disconnect mid-session and offer to switch
- **Windows audio policy integration** — use PowerShell/COM to set the system default output device (for routing Spotify audio directly, not just show playback)
- **Input device picker** — same interactive menu for `--audio-device` on `govee-live` (input/capture device selection)
