from __future__ import annotations

from pathlib import Path

from dreamsync.director import Director
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


