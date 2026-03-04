"""Thread-safe growable PCM buffer for accumulating audio per song."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BufferConfig:
    sample_rate: int = 44100
    channels: int = 1
    max_song_seconds: float = 900.0  # 15 min cap
    chunk_size: int = 1024           # frames per chunk


class AudioStreamBuffer:
    """Accumulates PCM audio chunks for the current song.

    Thread-safe: ``write()`` and ``read_all()`` can be called from
    different threads without data corruption.  A configurable max
    duration prevents unbounded memory growth — oldest chunks are
    dropped (FIFO shed) when the cap is exceeded.
    """

    def __init__(self, config: BufferConfig | None = None) -> None:
        self._config = config or BufferConfig()
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._total_frames: int = 0

    @property
    def config(self) -> BufferConfig:
        return self._config

    def write(self, pcm: np.ndarray) -> None:
        """Append a PCM chunk.  Thread-safe.

        Accepts both 1-D (mono) and 2-D (channels, frames) arrays.
        Data is normalised to the configured channel count.
        """
        pcm = self._normalise(pcm)
        frames = pcm.shape[0] if pcm.ndim == 1 else pcm.shape[-1]
        max_frames = int(self._config.max_song_seconds * self._config.sample_rate)

        with self._lock:
            self._chunks.append(pcm)
            self._total_frames += frames

            # FIFO shed: drop oldest chunks until within cap
            while self._total_frames > max_frames and self._chunks:
                oldest = self._chunks.pop(0)
                dropped = oldest.shape[0] if oldest.ndim == 1 else oldest.shape[-1]
                self._total_frames -= dropped
                logger.warning(
                    "Buffer cap exceeded — dropped oldest chunk (%d frames)", dropped,
                )

    def read_all(self) -> np.ndarray:
        """Return all accumulated PCM as a single contiguous array."""
        with self._lock:
            if not self._chunks:
                if self._config.channels == 1:
                    return np.empty(0, dtype=np.float32)
                return np.empty((self._config.channels, 0), dtype=np.float32)
            return np.concatenate(self._chunks)

    def clear(self) -> None:
        """Discard all accumulated data (called after song boundary)."""
        with self._lock:
            self._chunks.clear()
            self._total_frames = 0

    @property
    def duration_seconds(self) -> float:
        """Current accumulated duration."""
        return self._total_frames / self._config.sample_rate

    @property
    def frame_count(self) -> int:
        """Total frames accumulated."""
        return self._total_frames

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _normalise(self, pcm: np.ndarray) -> np.ndarray:
        """Ensure *pcm* matches the configured channel layout."""
        pcm = np.asarray(pcm, dtype=np.float32)

        if self._config.channels == 1:
            # Collapse to 1-D mono
            if pcm.ndim == 2:
                pcm = pcm.mean(axis=0) if pcm.shape[0] <= pcm.shape[1] else pcm.mean(axis=1)
            return pcm

        # Multi-channel: ensure shape (channels, frames)
        if pcm.ndim == 1:
            pcm = np.stack([pcm] * self._config.channels)
        elif pcm.shape[0] != self._config.channels:
            # Transpose if necessary (frames, channels) → (channels, frames)
            if pcm.shape[1] == self._config.channels:
                pcm = pcm.T
            else:
                # Best-effort: average down to mono then replicate
                mono = pcm.mean(axis=0)
                pcm = np.stack([mono] * self._config.channels)
        return pcm
