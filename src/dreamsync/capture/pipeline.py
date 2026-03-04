"""StreamCapturePipeline — wires buffer → detector → writer into a single feed() call."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from .boundary import BoundaryEvent, CompositeBoundaryDetector
from .buffer import AudioStreamBuffer, BufferConfig
from .writer import SongFileWriter, WriterConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CaptureConfig:
    buffer: BufferConfig = field(default_factory=BufferConfig)
    writer: WriterConfig = field(default_factory=WriterConfig)
    hop_size: int = 512
    sample_rate: int = 44100
    min_song_seconds: float = 30.0


class StreamCapturePipeline:
    """Orchestrator that accumulates PCM audio and splits it into per-song mp3 files.

    Call ``feed()`` once per audio hop frame.  When a song boundary is
    detected the accumulated PCM is encoded and written to disk via
    ``SongFileWriter``, and the optional ``on_song_saved`` callback fires.
    """

    def __init__(
        self,
        config: CaptureConfig | None = None,
        on_song_saved: Callable[[Path, dict], None] | None = None,
    ) -> None:
        cfg = config or CaptureConfig()
        self._buffer = AudioStreamBuffer(cfg.buffer)
        self._detector = CompositeBoundaryDetector(
            hop_size=cfg.hop_size,
            sample_rate=cfg.sample_rate,
            min_song_seconds=cfg.min_song_seconds,
            on_boundary=self._handle_boundary,
        )
        self._writer = SongFileWriter(cfg.writer)
        self._on_song_saved = on_song_saved
        self._songs_saved: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def feed(self, pcm: np.ndarray, features: dict, t: float) -> None:
        """Called per audio frame.  Buffers audio and checks for boundaries."""
        self._buffer.write(pcm)
        self._detector.update(features, t)

    def notify_track_changed(self, new_track, old_track) -> None:
        """Forward Spotify track change to boundary detector."""
        self._detector.notify_track_changed(new_track, old_track)

    def flush(self) -> Path | None:
        """Finalise whatever is in the buffer (e.g. on session end)."""
        pcm = self._buffer.read_all()
        self._buffer.clear()
        if pcm.size == 0:
            return None
        path = self._writer.finalize(pcm, metadata=None)
        if path and self._on_song_saved:
            self._on_song_saved(path, {"source": "flush"})
        if path:
            self._songs_saved += 1
        return path

    def reset(self) -> None:
        """Clear all state."""
        self._buffer.clear()
        self._detector.reset()
        self._songs_saved = 0

    @property
    def songs_saved(self) -> int:
        return self._songs_saved

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _handle_boundary(self, event: BoundaryEvent) -> None:
        """Finalise current song, start new accumulation."""
        pcm = self._buffer.read_all()
        self._buffer.clear()
        metadata = {
            "track_name": event.track_name,
            "artist": event.artist,
            "source": event.source,
            "boundary_time": event.timestamp,
        }
        path = self._writer.finalize(pcm, metadata)
        if path:
            self._songs_saved += 1
            if self._on_song_saved:
                self._on_song_saved(path, metadata)
