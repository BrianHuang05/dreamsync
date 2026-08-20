from __future__ import annotations

import os
from contextlib import contextmanager
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np


_PULSE_SOURCE_DEVICE_PREFIX = "pulse-source:"


def pulse_source_device_id(source_name: str) -> str:
    """Return the persisted device ID for a named PipeWire/Pulse source."""
    return f"{_PULSE_SOURCE_DEVICE_PREFIX}{source_name}"


def _pulse_source_name(device: int | str | None) -> str | None:
    if isinstance(device, str) and device.startswith(_PULSE_SOURCE_DEVICE_PREFIX):
        return device.removeprefix(_PULSE_SOURCE_DEVICE_PREFIX)
    return None


def _pulse_alsa_device_id(sd) -> int:
    """Find PortAudio's PulseAudio ALSA endpoint used to open Pulse sources."""
    devices = sd.query_devices()
    hostapis = sd.query_hostapis()
    for index, info in enumerate(devices):
        if int(info.get("max_input_channels", 0)) <= 0:
            continue
        hostapi_index = int(info.get("hostapi", -1))
        hostapi = (
            str(hostapis[hostapi_index].get("name", ""))
            if 0 <= hostapi_index < len(hostapis)
            else ""
        )
        name = str(info.get("name", "")).strip().lower()
        if hostapi.lower() == "alsa" and name in {"pulse", "pulse alsa"}:
            return index
    raise RuntimeError(
        "This PipeWire/Pulse source needs PortAudio's Pulse ALSA input endpoint, "
        "but it is not available. Install the PulseAudio ALSA plugin and restart the app."
    )


@contextmanager
def open_input_stream(sd, *, device: int | str | None = None, **kwargs):
    """Open an input stream, including a specifically selected Pulse source.

    PortAudio exposes PipeWire's Pulse server as one ALSA device (usually
    ``pulse``), which hides its individual microphones.  ``PULSE_SOURCE`` is
    read when that stream is opened, allowing the GUI to present the actual
    PipeWire sources without changing the user's system default source.
    """
    source_name = _pulse_source_name(device)
    old_source = None
    if source_name is not None:
        if not source_name:
            raise ValueError("The selected PipeWire/Pulse source is empty.")
        device = _pulse_alsa_device_id(sd)
        old_source = os.environ.get("PULSE_SOURCE")
        os.environ["PULSE_SOURCE"] = source_name
    try:
        with sd.InputStream(device=device, **kwargs) as stream:
            yield stream
    finally:
        if source_name is not None:
            if old_source is None:
                os.environ.pop("PULSE_SOURCE", None)
            else:
                os.environ["PULSE_SOURCE"] = old_source


@dataclass(frozen=True)
class CaptureStats:
    sample_rate: int
    channels: int
    duration_seconds: float
    dropped_blocks: int


@dataclass(frozen=True)
class CaptureProgress:
    elapsed_seconds: float
    samples_captured: int
    dropped_blocks: int


def _require_sounddevice():
    try:
        import sounddevice as sd
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("sounddevice is required for real-time capture") from exc
    return sd


def list_input_devices() -> list[dict[str, int | float | str]]:
    sd = _require_sounddevice()
    devices = sd.query_devices()
    hostapis = sd.query_hostapis()
    rows: list[dict[str, int | float | str]] = []
    for idx, dev in enumerate(devices):
        max_in = int(dev.get("max_input_channels", 0))
        if max_in <= 0:
            continue
        hostapi_idx = int(dev.get("hostapi", 0))
        hostapi_name = str(hostapis[hostapi_idx].get("name", ""))
        rows.append(
            {
                "id": idx,
                "name": str(dev.get("name", "")),
                "hostapi": hostapi_name,
                "max_input_channels": max_in,
                "default_samplerate": float(dev.get("default_samplerate", 0.0)),
            }
        )
    return rows


def list_output_devices() -> list[dict[str, int | float | str]]:
    """List audio output devices (speakers, headphones, virtual cables)."""
    sd = _require_sounddevice()
    devices = sd.query_devices()
    hostapis = sd.query_hostapis()
    rows: list[dict[str, int | float | str]] = []
    for idx, dev in enumerate(devices):
        max_out = int(dev.get("max_output_channels", 0))
        if max_out <= 0:
            continue
        hostapi_idx = int(dev.get("hostapi", 0))
        hostapi_name = str(hostapis[hostapi_idx].get("name", ""))
        rows.append(
            {
                "id": idx,
                "name": str(dev.get("name", "")),
                "hostapi": hostapi_name,
                "max_output_channels": max_out,
                "default_samplerate": float(dev.get("default_samplerate", 0.0)),
            }
        )
    return rows


def is_capture_device(
    name: str,
    keywords: tuple[str, ...] = ("cable input", "virtual cable", "vb-audio"),
) -> bool:
    """Return True if the device name matches known capture/virtual-cable patterns."""
    lower = name.lower()
    return any(kw in lower for kw in keywords)


def format_device_table(
    devices: list[dict],
    *,
    kind: str = "output",
    mark_capture: bool = False,
) -> str:
    """Format a list of device dicts as an aligned text table."""
    if not devices:
        return f"  No {kind} devices found."

    # Determine channel key
    chan_key = "max_output_channels" if kind == "output" else "max_input_channels"

    # Compute column widths
    headers = ("#", "ID", "Device", "Host API", "Ch", "Rate")
    rows: list[tuple[str, ...]] = []
    for i, dev in enumerate(devices, 1):
        name = str(dev.get("name", ""))
        marker = ""
        if mark_capture and is_capture_device(name):
            marker = "  \u26a0 capture"
        rows.append((
            str(i),
            str(dev.get("id", "")),
            name,
            str(dev.get("hostapi", "")),
            str(dev.get(chan_key, "")),
            str(int(dev.get("default_samplerate", 0))),
        ))
        if marker:
            rows[-1] = (*rows[-1][:5], rows[-1][5] + marker)

    widths = [len(h) for h in headers]
    for row in rows:
        for j, cell in enumerate(row):
            widths[j] = max(widths[j], len(cell))

    def fmt_row(cells: tuple[str, ...]) -> str:
        parts = []
        for j, cell in enumerate(cells):
            if j in (0, 1, 4):  # right-align numeric columns
                parts.append(cell.rjust(widths[j]))
            else:
                parts.append(cell.ljust(widths[j]))
        return "  " + "   ".join(parts)

    sep = "  " + "\u2500" * (sum(widths) + 3 * (len(widths) - 1))
    lines = [sep, fmt_row(headers), sep]
    for row in rows:
        lines.append(fmt_row(row))
    lines.append(sep)
    return "\n".join(lines)


def pick_output_device(
    *,
    warn_capture: bool = True,
    capture_keywords: tuple[str, ...] = ("cable input", "virtual cable", "vb-audio"),
) -> int:
    """Show an interactive menu of audio output devices and return the selected ID.

    Raises RuntimeError if no output devices are found.
    Raises KeyboardInterrupt if the user cancels (Ctrl+C).
    """
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

        if warn_capture and is_capture_device(name, capture_keywords):
            confirm = input(
                f'  \u26a0 Warning: "{name}" looks like a capture device.\n'
                f"    Using this for playback may cause audio feedback.\n"
                f"    Continue? [y/N]: "
            ).strip().lower()
            if confirm not in ("y", "yes"):
                continue

        print(f"  \u2192 Using: {name} (device ID {dev_id})")
        return dev_id


def capture_mono_audio(
    duration_seconds: float,
    sample_rate: int = 44100,
    channels: int = 1,
    device: int | str | None = None,
    blocksize: int = 1024,
    progress_interval_seconds: float | None = None,
    progress_callback: Callable[[CaptureProgress], None] | None = None,
) -> tuple[np.ndarray, CaptureStats]:
    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be > 0")
    if channels <= 0:
        raise ValueError("channels must be > 0")
    if sample_rate <= 0:
        raise ValueError("sample_rate must be > 0")

    sd = _require_sounddevice()
    captured: list[np.ndarray] = []
    captured_samples = 0
    dropped_blocks = 0

    def _callback(indata, frames, time_info, status) -> None:
        del frames, time_info
        nonlocal dropped_blocks, captured_samples
        # `status` reports PortAudio callback issues (including input overflow).
        if status and getattr(status, "input_overflow", False):
            dropped_blocks += 1
        captured.append(indata.copy())
        captured_samples += int(indata.shape[0])

    with open_input_stream(
        sd,
        samplerate=sample_rate,
        channels=channels,
        device=device,
        dtype="float32",
        blocksize=blocksize,
        callback=_callback,
    ):
        started_at = time.monotonic()
        next_progress = started_at + (
            progress_interval_seconds if progress_interval_seconds and progress_interval_seconds > 0 else 0.0
        )
        while True:
            now = time.monotonic()
            elapsed = now - started_at
            if elapsed >= duration_seconds:
                break
            if (
                progress_callback
                and progress_interval_seconds
                and progress_interval_seconds > 0
                and now >= next_progress
            ):
                progress_callback(
                    CaptureProgress(
                        elapsed_seconds=min(elapsed, duration_seconds),
                        samples_captured=captured_samples,
                        dropped_blocks=dropped_blocks,
                    )
                )
                next_progress += progress_interval_seconds
            time.sleep(0.05)

    if progress_callback and progress_interval_seconds and progress_interval_seconds > 0:
        progress_callback(
            CaptureProgress(
                elapsed_seconds=duration_seconds,
                samples_captured=captured_samples,
                dropped_blocks=dropped_blocks,
            )
        )

    if not captured:
        signal = np.zeros(0, dtype=np.float32)
    else:
        signal_2d = np.concatenate(captured, axis=0)
        if signal_2d.ndim == 2 and signal_2d.shape[1] > 1:
            signal = signal_2d.mean(axis=1).astype(np.float32)
        else:
            signal = signal_2d.reshape(-1).astype(np.float32)

    stats = CaptureStats(
        sample_rate=sample_rate,
        channels=channels,
        duration_seconds=duration_seconds,
        dropped_blocks=dropped_blocks,
    )
    return signal, stats
