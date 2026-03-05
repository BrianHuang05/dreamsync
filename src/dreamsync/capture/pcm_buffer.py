"""Thread-safe PCM buffer with global frame counter for the capture pipeline."""

from __future__ import annotations

import queue
import threading

from dreamsync.capture.pcm_reader import BYTES_PER_FRAME, SAMPLE_RATE


class AudioBuffer:
    """Decouple capture rate from downstream consumption.

    Sits between the PCM reader and the split/encoding logic.  Provides a
    thread-safe put/get interface and maintains an authoritative frame
    counter that serves as the pipeline's master clock.

    Parameters
    ----------
    max_chunks:
        Maximum number of chunks the buffer will hold before ``put``
        blocks (backpressure).  Default is 10 (~1 s at 100 ms chunks).
    sample_rate:
        Sample rate for elapsed-time calculation.
    """

    def __init__(
        self,
        max_chunks: int = 10,
        sample_rate: int = SAMPLE_RATE,
    ) -> None:
        self._queue: queue.Queue[bytes | None] = queue.Queue(maxsize=max_chunks)
        self._sample_rate = sample_rate
        self._frames_processed: int = 0
        self._bytes_processed: int = 0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Producer API
    # ------------------------------------------------------------------

    def put(self, chunk: bytes, timeout: float | None = None) -> None:
        """Enqueue a PCM chunk.  Blocks if the buffer is full.

        The frame counter is updated atomically on each put so that
        ``frames_processed`` always reflects the total data *accepted*
        by the buffer, regardless of consumer progress.
        """
        self._queue.put(chunk, timeout=timeout)
        n_bytes = len(chunk)
        with self._lock:
            self._bytes_processed += n_bytes
            self._frames_processed += n_bytes // BYTES_PER_FRAME

    def signal_eof(self) -> None:
        """Signal end-of-stream so that a blocking ``get`` returns ``None``."""
        self._queue.put(None)

    # ------------------------------------------------------------------
    # Consumer API
    # ------------------------------------------------------------------

    def get(self, timeout: float | None = None) -> bytes | None:
        """Dequeue the next PCM chunk.

        Returns ``None`` when the producer has signalled EOF via
        :meth:`signal_eof`.  Blocks until a chunk is available.
        """
        return self._queue.get(timeout=timeout)

    # ------------------------------------------------------------------
    # Counters
    # ------------------------------------------------------------------

    @property
    def frames_processed(self) -> int:
        """Total audio frames accepted into the buffer since creation."""
        with self._lock:
            return self._frames_processed

    @property
    def bytes_processed(self) -> int:
        """Total bytes accepted into the buffer since creation."""
        with self._lock:
            return self._bytes_processed

    def elapsed_seconds(self) -> float:
        """Elapsed audio time based on the frame counter."""
        return self.frames_processed / self._sample_rate

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def pending(self) -> int:
        """Approximate number of chunks waiting in the buffer."""
        return self._queue.qsize()

    @property
    def full(self) -> bool:
        return self._queue.full()

    @property
    def empty(self) -> bool:
        return self._queue.empty()
