"""Core split loop — route PCM chunks to per-segment encoders at exact boundaries."""

from __future__ import annotations

import logging
from typing import Callable, Iterator

from dreamsync.capture.pcm_reader import BYTES_PER_FRAME

logger = logging.getLogger(__name__)


class SplitProcessor:
    """Process PCM chunks and route them to segment encoders.

    The processor maintains a global frame counter and splits chunks at
    exact frame boundaries.  When a boundary is reached, the current
    encoder is closed and the ``on_segment_complete`` / ``start_encoder``
    callbacks are invoked.

    Parameters
    ----------
    boundaries:
        Cumulative frame boundaries (from :func:`compute_boundaries`).
    start_encoder:
        Callable that receives ``(segment_index,)`` and returns an object
        with ``write(bytes)`` and ``close()`` methods.
    on_segment_complete:
        Optional callback ``(segment_index, start_frame, end_frame)``
        invoked when a segment is finalized.
    """

    def __init__(
        self,
        boundaries: list[int],
        start_encoder: Callable[[int], object],
        on_segment_complete: Callable[[int, int, int], None] | None = None,
    ) -> None:
        self._boundaries = list(boundaries)
        self._start_encoder = start_encoder
        self._on_segment_complete = on_segment_complete

        self._current_frame: int = 0
        self._segment_index: int = 0
        self._segment_start_frame: int = 0
        self._encoder = None
        self._finished = False
        self._total_bytes_written: int = 0

    @property
    def current_frame(self) -> int:
        return self._current_frame

    @property
    def segment_index(self) -> int:
        return self._segment_index

    @property
    def total_bytes_written(self) -> int:
        return self._total_bytes_written

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Initialize the first encoder."""
        self._encoder = self._start_encoder(self._segment_index)

    def process_chunk(self, chunk: bytes) -> None:
        """Route *chunk* to the correct encoder(s), splitting at boundaries."""
        if self._encoder is None:
            raise RuntimeError("Call start() before processing chunks")

        chunk_frames = len(chunk) // BYTES_PER_FRAME
        offset = 0

        while chunk_frames > 0:
            if self._segment_index < len(self._boundaries):
                frames_remaining = self._boundaries[self._segment_index] - self._current_frame
            else:
                # Past the last boundary — write everything to the last encoder.
                frames_remaining = chunk_frames

            if frames_remaining <= 0:
                # Boundary at exactly this position — rotate.
                self._rotate_encoder()
                continue

            if chunk_frames <= frames_remaining:
                # Entire remainder fits in current segment.
                data = chunk[offset:]
                self._encoder.write(data)  # type: ignore[union-attr]
                self._current_frame += chunk_frames
                self._total_bytes_written += len(data)
                chunk_frames = 0
            else:
                # Chunk spans a boundary — split.
                split_bytes = frames_remaining * BYTES_PER_FRAME
                data = chunk[offset: offset + split_bytes]
                self._encoder.write(data)  # type: ignore[union-attr]
                self._current_frame += frames_remaining
                self._total_bytes_written += len(data)
                offset += split_bytes
                chunk_frames -= frames_remaining
                self._rotate_encoder()

    def finish(self) -> None:
        """Close the final encoder."""
        if self._encoder is not None and not self._finished:
            self._encoder.close()  # type: ignore[union-attr]
            if self._on_segment_complete:
                self._on_segment_complete(
                    self._segment_index,
                    self._segment_start_frame,
                    self._current_frame,
                )
            self._finished = True

    def run(self, chunks: Iterator[bytes]) -> None:
        """Convenience: start, process all chunks, finish."""
        self.start()
        for chunk in chunks:
            self.process_chunk(chunk)
        self.finish()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _rotate_encoder(self) -> None:
        """Close current encoder and start the next one."""
        if self._encoder is not None:
            self._encoder.close()  # type: ignore[union-attr]
            if self._on_segment_complete:
                self._on_segment_complete(
                    self._segment_index,
                    self._segment_start_frame,
                    self._current_frame,
                )

        self._segment_index += 1
        self._segment_start_frame = self._current_frame
        self._encoder = self._start_encoder(self._segment_index)
