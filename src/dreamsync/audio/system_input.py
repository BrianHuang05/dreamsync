from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

import numpy as np


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


def capture_mono_audio(
    duration_seconds: float,
    sample_rate: int = 44100,
    channels: int = 1,
    device: int | None = None,
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

    with sd.InputStream(
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
