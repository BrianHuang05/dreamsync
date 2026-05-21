"""Mp3 Decoder — decode audio files to float32 PCM via ffmpeg."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from dreamsync.capture.writer import check_ffmpeg
from dreamsync.ffmpeg import resolve_ffmpeg, resolve_ffprobe


class DecodeError(Exception):
    """Raised when ffmpeg decoding fails."""


@dataclass(frozen=True)
class AudioData:
    signal: np.ndarray    # mono float32, [-1.0, 1.0]
    sample_rate: int
    duration: float       # seconds
    channels: int         # original channel count (before downmix)
    stereo_signal: np.ndarray | None = None  # optional Nx2 float32 PCM retained for pan analysis


def _probe_file(path: Path) -> dict:
    """Probe an audio file with ffprobe and return stream info."""
    ffprobe = resolve_ffprobe()
    if ffprobe is None:
        raise DecodeError("ffprobe not found on PATH — install ffmpeg")
    try:
        result = subprocess.run(
            [
                ffprobe, "-v", "quiet",
                "-print_format", "json",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError:
        raise DecodeError("ffprobe not found on PATH — install ffmpeg")
    except subprocess.CalledProcessError as exc:
        raise DecodeError(f"ffprobe failed on {path}: {exc.stderr.strip()}")

    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise DecodeError(f"ffprobe returned invalid JSON for {path}")

    streams = info.get("streams", [])
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    if not audio_streams:
        raise DecodeError(f"No audio stream found in {path}")

    return audio_streams[0]


def decode_mp3(
    path: Path,
    target_sr: int = 44100,
    *,
    preserve_stereo: bool = True,
) -> AudioData:
    """Decode an audio file to float32 PCM via ffmpeg.

    Resamples to *target_sr*. By default this retains a stereo PCM view for
    later pan analysis while also returning a mono downmix in ``signal`` for
    existing mono consumers.
    Works with mp3, wav, flac, ogg, and any ffmpeg-supported format.
    Raises DecodeError on failure.
    """
    path = Path(path)
    if not path.exists():
        raise DecodeError(f"File not found: {path}")
    if path.stat().st_size == 0:
        raise DecodeError(f"File is empty: {path}")

    if not check_ffmpeg():
        raise DecodeError("ffmpeg not found on PATH — install ffmpeg")
    ffmpeg = resolve_ffmpeg()
    if ffmpeg is None:
        raise DecodeError("ffmpeg not found on PATH — install ffmpeg")

    # Probe for original metadata
    stream_info = _probe_file(path)
    original_channels = int(stream_info.get("channels", 2))

    output_channels = 2 if preserve_stereo else 1

    # Decode to raw float32 PCM via ffmpeg pipe
    try:
        proc = subprocess.run(
            [
                ffmpeg, "-i", str(path),
                "-f", "f32le",
                "-acodec", "pcm_f32le",
                "-ar", str(target_sr),
                "-ac", str(output_channels),
                "pipe:1",
            ],
            capture_output=True,
            check=True,
        )
    except FileNotFoundError:
        raise DecodeError("ffmpeg not found on PATH — install ffmpeg")
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace").strip()
        raise DecodeError(f"ffmpeg decode failed for {path}: {stderr}")

    raw_bytes = proc.stdout
    if len(raw_bytes) == 0:
        raise DecodeError(f"ffmpeg produced no output for {path}")
    if len(raw_bytes) % 4 != 0:
        raise DecodeError(
            f"ffmpeg output length ({len(raw_bytes)} bytes) is not a multiple of 4"
        )

    pcm = np.frombuffer(raw_bytes, dtype=np.float32).copy()
    if output_channels > 1:
        if len(pcm) % output_channels != 0:
            raise DecodeError(
                f"ffmpeg output sample count ({len(pcm)}) is not divisible by {output_channels}"
            )
        stereo_signal = pcm.reshape(-1, output_channels)[:, :2].copy()
        signal = stereo_signal.mean(axis=1, dtype=np.float32)
    else:
        stereo_signal = None
        signal = pcm
    duration = len(signal) / target_sr

    return AudioData(
        signal=signal.astype(np.float32, copy=False),
        sample_rate=target_sr,
        duration=duration,
        channels=original_channels,
        stereo_signal=stereo_signal,
    )
