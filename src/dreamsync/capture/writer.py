"""Encode PCM audio to mp3 via ffmpeg and write one file per song."""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


class EncodeError(Exception):
    """Raised when ffmpeg encoding fails."""


@dataclass(frozen=True)
class WriterConfig:
    output_dir: str = "captured_songs"
    bitrate: str = "192k"
    sample_rate: int = 44100
    channels: int = 1
    min_duration_seconds: float = 15.0  # discard fragments shorter than this
    naming: str = "timestamp"           # "timestamp" | "metadata"


def check_ffmpeg() -> bool:
    """Return True if ffmpeg is available on PATH."""
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


# Characters illegal in filenames on Windows / most filesystems
_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _sanitise_filename(name: str) -> str:
    """Remove characters that are invalid in file paths."""
    name = _ILLEGAL_CHARS.sub("_", name)
    name = name.strip(". ")
    return name or "untitled"


class SongFileWriter:
    """Encodes PCM data to mp3 and writes one file per song."""

    def __init__(self, config: WriterConfig | None = None) -> None:
        self._config = config or WriterConfig()
        self._output_dir = Path(self._config.output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)

    @property
    def config(self) -> WriterConfig:
        return self._config

    def finalize(
        self,
        pcm: np.ndarray,
        metadata: dict | None = None,
    ) -> Path | None:
        """Encode *pcm* to mp3 and write to disk.

        Returns the output ``Path``, or ``None`` if the audio is too
        short (below ``min_duration_seconds``).
        """
        frames = pcm.shape[0] if pcm.ndim == 1 else pcm.shape[-1]
        duration = frames / self._config.sample_rate
        if duration < self._config.min_duration_seconds:
            logger.info(
                "Skipping short fragment (%.1fs < %.1fs min)",
                duration,
                self._config.min_duration_seconds,
            )
            return None

        filename = self._build_filename(metadata)
        output_path = self._output_dir / filename
        self._encode_mp3(pcm, output_path)

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise EncodeError(f"Output file missing or empty: {output_path}")

        logger.info("Saved song: %s (%.1fs)", output_path, duration)
        return output_path

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_filename(self, metadata: dict | None) -> str:
        """Generate filename from metadata or timestamp."""
        if (
            self._config.naming == "metadata"
            and metadata
            and metadata.get("track_name")
            and metadata.get("artist")
        ):
            base = f"{_sanitise_filename(metadata['artist'])} - {_sanitise_filename(metadata['track_name'])}"
        else:
            base = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Collision avoidance
        candidate = f"{base}.mp3"
        if not (self._output_dir / candidate).exists():
            return candidate
        for i in range(2, 10000):
            candidate = f"{base}_{i}.mp3"
            if not (self._output_dir / candidate).exists():
                return candidate
        return candidate  # pragma: no cover

    def _encode_mp3(self, pcm: np.ndarray, output_path: Path) -> None:
        """Pipe raw PCM to ffmpeg, produce mp3 file."""
        # Flatten to 1-D for mono, interleave for multi-channel
        if pcm.ndim == 2:
            # (channels, frames) → interleaved (frames * channels,)
            pcm = pcm.T.reshape(-1)

        # float32 → int16
        pcm_int16 = (np.clip(pcm, -1.0, 1.0) * 32767).astype(np.int16)

        cmd = [
            "ffmpeg",
            "-f", "s16le",
            "-ar", str(self._config.sample_rate),
            "-ac", str(self._config.channels),
            "-i", "pipe:0",
            "-b:a", self._config.bitrate,
            "-y",
            str(output_path),
        ]

        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        _, stderr = proc.communicate(input=pcm_int16.tobytes())

        if proc.returncode != 0:
            raise EncodeError(
                f"ffmpeg exited with code {proc.returncode}: {stderr.decode(errors='replace')}"
            )
