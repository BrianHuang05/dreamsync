from __future__ import annotations

import time
from collections import Counter, deque
from typing import Any

import numpy as np

from dreamsync.audio.system_input import _require_sounddevice
from dreamsync.director import Director
from dreamsync.output.ledfx import LedFxOutputAdapter


def _feature_row_from_frame(frame: np.ndarray, sample_rate: int, t: float) -> dict[str, float | bool]:
    rms = float(np.sqrt(np.mean(frame**2)))
    signs = np.sign(frame)
    zcr = float(np.mean(np.abs(np.diff(signs)) > 0))
    return {
        "t": t,
        "rms": rms,
        "zcr": zcr,
        "centroid": 0.0,
        "bass": 0.0,
        "beat": False,
        "bpm": 120.0,
    }


def run_live_input_to_ledfx(
    adapter: LedFxOutputAdapter,
    duration_seconds: float,
    sample_rate: int = 44100,
    channels: int = 1,
    device: int | None = None,
    frame_size: int = 2048,
    hop_size: int = 512,
    telemetry_interval_seconds: float = 1.0,
    blocksize: int = 1024,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be > 0")
    if sample_rate <= 0:
        raise ValueError("sample_rate must be > 0")
    if channels <= 0:
        raise ValueError("channels must be > 0")
    if frame_size <= 0:
        raise ValueError("frame_size must be > 0")
    if hop_size <= 0:
        raise ValueError("hop_size must be > 0")

    sd = _require_sounddevice()
    audio_queue: deque[np.ndarray] = deque()
    logs: list[dict[str, Any]] = []
    director = Director()
    mode_counts: Counter[str] = Counter()
    transitions = 0
    last_mode: str | None = None
    sent_count = 0
    dropped_blocks = 0
    captured_samples = 0

    def _callback(indata, frames, time_info, status) -> None:
        del frames, time_info
        nonlocal dropped_blocks, captured_samples
        if status and getattr(status, "input_overflow", False):
            dropped_blocks += 1
        mono = indata.mean(axis=1) if indata.ndim == 2 else indata.reshape(-1)
        arr = np.asarray(mono, dtype=np.float32).copy()
        captured_samples += int(arr.shape[0])
        audio_queue.append(arr)

    stream_t = 0.0
    buffer = np.zeros(0, dtype=np.float32)
    started_at = time.monotonic()
    next_telemetry = started_at + max(0.1, telemetry_interval_seconds)

    with sd.InputStream(
        samplerate=sample_rate,
        channels=channels,
        device=device,
        dtype="float32",
        blocksize=blocksize,
        callback=_callback,
    ):
        while True:
            now = time.monotonic()
            elapsed = now - started_at
            if elapsed >= duration_seconds:
                break

            while audio_queue:
                chunk = audio_queue.popleft()
                buffer = np.concatenate([buffer, chunk])

            while buffer.shape[0] >= frame_size:
                frame = buffer[:frame_size]
                buffer = buffer[hop_size:]
                features = _feature_row_from_frame(frame, sample_rate, stream_t)
                intent = director.update(features)
                sent = adapter.emit(stream_t, intent)
                if sent:
                    sent_count += 1
                mode = intent.mode.value
                mode_counts[mode] += 1
                if last_mode is not None and mode != last_mode:
                    transitions += 1
                last_mode = mode
                logs.append(
                    {
                        "kind": "frame",
                        "t": round(stream_t, 4),
                        "elapsed_wall": round(elapsed, 4),
                        "rms": round(float(features["rms"]), 6),
                        "zcr": round(float(features["zcr"]), 6),
                        "mode": mode,
                        "intensity": round(float(intent.intensity), 4),
                        "speed": round(float(intent.speed), 4),
                        "bpm": round(float(intent.bpm), 2),
                        "sent": bool(sent),
                    }
                )
                stream_t += float(hop_size) / float(sample_rate)

            if now >= next_telemetry:
                logs.append(
                    {
                        "kind": "telemetry",
                        "elapsed_wall": round(elapsed, 3),
                        "samples_captured": int(captured_samples),
                        "dropped_blocks": int(dropped_blocks),
                        "queue_chunks": int(len(audio_queue)),
                    }
                )
                next_telemetry += max(0.1, telemetry_interval_seconds)
            time.sleep(0.01)

    summary = {
        "duration_seconds": float(duration_seconds),
        "sample_rate": int(sample_rate),
        "channels": int(channels),
        "device": device,
        "frame_size": int(frame_size),
        "hop_size": int(hop_size),
        "samples_captured": int(captured_samples),
        "dropped_blocks": int(dropped_blocks),
        "rows": int(sum(1 for row in logs if row["kind"] == "frame")),
        "sent": int(sent_count),
        "transitions": int(transitions),
        "mode_counts": dict(mode_counts),
    }
    return logs, summary
