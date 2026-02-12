from __future__ import annotations

import time
from pathlib import Path

from dreamsync.director import Director
from dreamsync.output.ledfx import LedFxOutputAdapter
from dreamsync.output.fake import FakeOutputAdapter
from dreamsync.pipeline import extract_wav_features_to_stream


def replay_wav_to_fake_output(
    wav_path: Path,
    frame_size: int = 2048,
    hop_size: int = 512,
) -> list[dict[str, float | str]]:
    features = extract_wav_features_to_stream(wav_path, frame_size=frame_size, hop_size=hop_size)
    director = Director()
    out = FakeOutputAdapter()

    for row in features:
        intent = director.update(row)
        out.emit(float(row.get("t", 0.0)), intent)

    logs: list[dict[str, float | str]] = []
    for rec in out.records:
        logs.append(
            {
                "t": rec.t,
                "mode": rec.mode,
                "intensity": rec.intensity,
                "speed": rec.speed,
                "bpm": rec.bpm,
            }
        )
    return logs


def replay_wav_to_ledfx_output(
    wav_path: Path,
    adapter: LedFxOutputAdapter,
    frame_size: int = 2048,
    hop_size: int = 512,
    realtime: bool = False,
    max_events: int | None = None,
    sleep_fn=time.sleep,
) -> list[dict[str, float | str | bool]]:
    features = extract_wav_features_to_stream(
        wav_path, frame_size=frame_size, hop_size=hop_size
    )
    director = Director()
    logs: list[dict[str, float | str | bool]] = []
    prev_t: float | None = None

    for i, row in enumerate(features):
        if max_events is not None and i >= max_events:
            break
        t = float(row.get("t", 0.0))
        if realtime and prev_t is not None:
            dt = t - prev_t
            if dt > 0:
                sleep_fn(dt)
        intent = director.update(row)
        sent = adapter.emit(t, intent)
        logs.append(
            {
                "t": t,
                "mode": intent.mode.value,
                "intensity": intent.intensity,
                "speed": intent.speed,
                "bpm": intent.bpm,
                "sent": sent,
            }
        )
        prev_t = t

    return logs
