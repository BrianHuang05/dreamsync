from __future__ import annotations

from pathlib import Path

from .audio.system_input import CaptureProgress, capture_mono_audio
from .audio.wav_source import read_wav_mono
from .dsp.features import extract_feature_frames


def extract_wav_features_to_stream(
    path: Path,
    frame_size: int = 2048,
    hop_size: int = 512,
) -> list[dict[str, float | bool]]:
    sr, signal = read_wav_mono(path)
    frames = extract_feature_frames(signal, sr, frame_size=frame_size, hop_size=hop_size)

    stream: list[dict[str, float | bool]] = []
    for f in frames:
        stream.append(
            {
                "t": f.t,
                "rms": f.rms,
                "zcr": f.zcr,
                "centroid": f.centroid,
                "bass": f.bass,
                "beat": f.beat,
                "bpm": f.bpm,
            }
        )
    return stream


def extract_signal_features_to_stream(
    signal,
    sample_rate: int,
    frame_size: int = 2048,
    hop_size: int = 512,
) -> list[dict[str, float | bool]]:
    frames = extract_feature_frames(signal, sample_rate, frame_size=frame_size, hop_size=hop_size)
    stream: list[dict[str, float | bool]] = []
    for f in frames:
        stream.append(
            {
                "t": f.t,
                "rms": f.rms,
                "zcr": f.zcr,
                "centroid": f.centroid,
                "bass": f.bass,
                "beat": f.beat,
                "bpm": f.bpm,
            }
        )
    return stream


def capture_system_input_features_to_stream(
    duration_seconds: float,
    sample_rate: int = 44100,
    channels: int = 1,
    device: int | None = None,
    frame_size: int = 2048,
    hop_size: int = 512,
    telemetry_interval_seconds: float | None = None,
) -> tuple[list[dict[str, float | bool]], dict[str, float | int], list[dict[str, float | int | str]]]:
    telemetry_rows: list[dict[str, float | int | str]] = []

    def _on_progress(progress: CaptureProgress) -> None:
        telemetry_rows.append(
            {
                "kind": "telemetry",
                "elapsed_seconds": round(progress.elapsed_seconds, 3),
                "samples_captured": progress.samples_captured,
                "dropped_blocks": progress.dropped_blocks,
            }
        )

    signal, stats = capture_mono_audio(
        duration_seconds=duration_seconds,
        sample_rate=sample_rate,
        channels=channels,
        device=device,
        progress_interval_seconds=telemetry_interval_seconds,
        progress_callback=_on_progress if telemetry_interval_seconds else None,
    )
    stream = extract_signal_features_to_stream(
        signal,
        sample_rate=sample_rate,
        frame_size=frame_size,
        hop_size=hop_size,
    )
    meta = {
        "sample_rate": stats.sample_rate,
        "channels": stats.channels,
        "duration_seconds": stats.duration_seconds,
        "dropped_blocks": stats.dropped_blocks,
        "samples_captured": int(signal.size),
    }
    return stream, meta, telemetry_rows
