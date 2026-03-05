"""Read frame-aligned PCM byte chunks from an FFmpeg capture stream."""

from __future__ import annotations

import logging
from typing import BinaryIO, Iterator

logger = logging.getLogger(__name__)

# Audio format constants (s16le stereo @ 44.1 kHz — matches VB-Cable default)
SAMPLE_RATE: int = 44100
CHANNELS: int = 2
BYTES_PER_SAMPLE: int = 2  # 16-bit
BYTES_PER_FRAME: int = CHANNELS * BYTES_PER_SAMPLE  # 4
BYTES_PER_SECOND: int = SAMPLE_RATE * BYTES_PER_FRAME  # 176400


def chunk_bytes_for_ms(chunk_ms: int) -> int:
    """Return the frame-aligned byte count for *chunk_ms* milliseconds."""
    raw = int(chunk_ms / 1000.0 * BYTES_PER_SECOND)
    return raw - (raw % BYTES_PER_FRAME)


def read_chunks(stream: BinaryIO, chunk_ms: int = 100) -> Iterator[bytes]:
    """Yield frame-aligned PCM byte chunks from *stream* until EOF.

    Each yielded chunk is exactly ``chunk_bytes_for_ms(chunk_ms)`` bytes,
    except possibly the very last chunk on EOF which may be shorter (but
    still frame-aligned).

    Parameters
    ----------
    stream:
        Binary readable stream (typically ``subprocess.Popen.stdout``).
    chunk_ms:
        Duration of each chunk in milliseconds (default 100 ms).
    """
    target = chunk_bytes_for_ms(chunk_ms)
    if target <= 0:
        raise ValueError(f"chunk_ms={chunk_ms} produces 0-byte chunks")

    buf = bytearray()

    while True:
        needed = target - len(buf)
        data = stream.read(needed)

        if not data:
            # EOF — yield whatever remains, aligned to frame boundary.
            if buf:
                remainder = len(buf) - (len(buf) % BYTES_PER_FRAME)
                if remainder > 0:
                    yield bytes(buf[:remainder])
            return

        buf.extend(data)

        if len(buf) >= target:
            yield bytes(buf[:target])
            buf = buf[target:]
