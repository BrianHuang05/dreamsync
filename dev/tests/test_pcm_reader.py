"""Tests for dreamsync.capture.pcm_reader — PCM stream chunking."""

import io

import pytest

from dreamsync.capture.pcm_reader import (
    BYTES_PER_FRAME,
    BYTES_PER_SECOND,
    SAMPLE_RATE,
    chunk_bytes_for_ms,
    read_chunks,
)


class TestConstants:
    def test_bytes_per_frame(self):
        # 2 channels * 2 bytes/sample = 4
        assert BYTES_PER_FRAME == 4

    def test_bytes_per_second(self):
        # 48000 * 4 = 192000
        assert BYTES_PER_SECOND == 192000

    def test_sample_rate(self):
        assert SAMPLE_RATE == 48000


class TestChunkBytesForMs:
    def test_100ms(self):
        assert chunk_bytes_for_ms(100) == 19200  # 4800 frames * 4

    def test_50ms(self):
        assert chunk_bytes_for_ms(50) == 9600  # 2400 frames * 4

    def test_20ms(self):
        assert chunk_bytes_for_ms(20) == 3840  # 960 frames * 4

    def test_always_frame_aligned(self):
        for ms in range(1, 200):
            size = chunk_bytes_for_ms(ms)
            assert size % BYTES_PER_FRAME == 0, f"{ms}ms -> {size} not aligned"

    def test_1ms_nonzero(self):
        # 1ms = 192 bytes = 48 frames
        assert chunk_bytes_for_ms(1) == 192


class TestReadChunks:
    def test_exact_chunks(self):
        """Stream that is an exact multiple of chunk size."""
        chunk_size = chunk_bytes_for_ms(100)  # 19200
        data = b"\x01\x02\x03\x04" * (chunk_size // 4) * 3  # 3 chunks
        stream = io.BytesIO(data)

        chunks = list(read_chunks(stream, chunk_ms=100))
        assert len(chunks) == 3
        assert all(len(c) == chunk_size for c in chunks)

    def test_partial_final_chunk(self):
        """Stream with trailing bytes that don't fill a full chunk."""
        chunk_size = chunk_bytes_for_ms(100)
        extra_frames = 100  # 400 bytes
        data = b"\x00" * (chunk_size * 2 + extra_frames * BYTES_PER_FRAME)
        stream = io.BytesIO(data)

        chunks = list(read_chunks(stream, chunk_ms=100))
        assert len(chunks) == 3
        assert len(chunks[0]) == chunk_size
        assert len(chunks[1]) == chunk_size
        assert len(chunks[2]) == extra_frames * BYTES_PER_FRAME

    def test_partial_final_frame_discarded(self):
        """Trailing bytes that don't form a complete frame are discarded."""
        chunk_size = chunk_bytes_for_ms(100)
        # 2 full chunks + 3 extra bytes (not a full frame)
        data = b"\x00" * (chunk_size * 2 + 3)
        stream = io.BytesIO(data)

        chunks = list(read_chunks(stream, chunk_ms=100))
        assert len(chunks) == 2  # trailing 3 bytes dropped

    def test_empty_stream(self):
        stream = io.BytesIO(b"")
        chunks = list(read_chunks(stream, chunk_ms=100))
        assert chunks == []

    def test_less_than_one_chunk(self):
        """Data smaller than a single chunk but frame-aligned."""
        data = b"\x00" * (BYTES_PER_FRAME * 10)  # 40 bytes
        stream = io.BytesIO(data)

        chunks = list(read_chunks(stream, chunk_ms=100))
        assert len(chunks) == 1
        assert len(chunks[0]) == 40

    def test_all_chunks_frame_aligned(self):
        """Every chunk from any stream must be frame-aligned."""
        data = b"\xAB" * 50000  # arbitrary size
        stream = io.BytesIO(data)

        for chunk in read_chunks(stream, chunk_ms=50):
            assert len(chunk) % BYTES_PER_FRAME == 0

    def test_chunk_ms_20(self):
        chunk_size = chunk_bytes_for_ms(20)
        data = b"\x00" * (chunk_size * 5)
        stream = io.BytesIO(data)

        chunks = list(read_chunks(stream, chunk_ms=20))
        assert len(chunks) == 5
        assert all(len(c) == chunk_size for c in chunks)

    def test_total_bytes_preserved(self):
        """Total bytes across all chunks equals input (minus partial frame)."""
        input_bytes = 19200 * 3 + 400  # 3 full chunks + 100 frames
        data = b"\x00" * input_bytes
        stream = io.BytesIO(data)

        chunks = list(read_chunks(stream, chunk_ms=100))
        total = sum(len(c) for c in chunks)
        assert total == input_bytes

    def test_invalid_chunk_ms_zero(self):
        with pytest.raises(ValueError, match="0-byte"):
            list(read_chunks(io.BytesIO(b""), chunk_ms=0))

    def test_simulated_slow_reads(self):
        """Simulate a stream that returns fewer bytes than requested."""
        chunk_size = chunk_bytes_for_ms(100)  # 19200
        data = b"\x00" * (chunk_size * 2)

        class SlowStream:
            """Returns data in small increments to simulate slow I/O."""
            def __init__(self, data: bytes, step: int = 1000):
                self._data = data
                self._pos = 0
                self._step = step

            def read(self, n: int) -> bytes:
                actual = min(n, self._step, len(self._data) - self._pos)
                chunk = self._data[self._pos:self._pos + actual]
                self._pos += actual
                return chunk

        stream = SlowStream(data, step=1000)
        chunks = list(read_chunks(stream, chunk_ms=100))
        assert len(chunks) == 2
        assert all(len(c) == chunk_size for c in chunks)
