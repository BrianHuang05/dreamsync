"""Tests for dreamsync.capture.pcm_buffer — AudioBuffer with frame counter."""

import threading
import time

import pytest

from dreamsync.capture.pcm_buffer import AudioBuffer
from dreamsync.capture.pcm_reader import BYTES_PER_FRAME, SAMPLE_RATE


class TestBasicOperations:
    def test_put_get_roundtrip(self):
        buf = AudioBuffer()
        data = b"\x01\x02\x03\x04" * 100  # 100 frames
        buf.put(data)
        assert buf.get(timeout=1) == data

    def test_multiple_put_get(self):
        buf = AudioBuffer()
        chunks = [b"\x00" * (BYTES_PER_FRAME * 10) for _ in range(5)]
        for c in chunks:
            buf.put(c)
        for c in chunks:
            assert buf.get(timeout=1) == c

    def test_fifo_order(self):
        buf = AudioBuffer()
        buf.put(b"\x01" * 4)
        buf.put(b"\x02" * 4)
        buf.put(b"\x03" * 4)
        assert buf.get(timeout=1) == b"\x01" * 4
        assert buf.get(timeout=1) == b"\x02" * 4
        assert buf.get(timeout=1) == b"\x03" * 4


class TestFrameCounter:
    def test_starts_at_zero(self):
        buf = AudioBuffer()
        assert buf.frames_processed == 0
        assert buf.bytes_processed == 0

    def test_counts_frames_after_put(self):
        buf = AudioBuffer()
        # 4800 frames = 19200 bytes (100ms at 48kHz stereo)
        chunk = b"\x00" * 19200
        buf.put(chunk)
        assert buf.frames_processed == 4800
        assert buf.bytes_processed == 19200

    def test_cumulative_counting(self):
        buf = AudioBuffer()
        chunk = b"\x00" * (BYTES_PER_FRAME * 100)  # 100 frames
        for _ in range(10):
            buf.put(chunk)
        assert buf.frames_processed == 1000
        assert buf.bytes_processed == 4000

    def test_10_seconds_equals_441000_frames(self):
        """Verify the integration test from the phase plan."""
        buf = AudioBuffer(max_chunks=200)
        # 10 seconds at 44.1kHz = 441,000 frames = 1,764,000 bytes
        # Deliver as 100 chunks of 100ms each
        chunk = b"\x00" * 17640  # 100ms = 4410 frames
        for _ in range(100):
            buf.put(chunk)
        assert buf.frames_processed == 441_000
        assert buf.bytes_processed == 1_764_000


class TestElapsedSeconds:
    def test_zero_initially(self):
        buf = AudioBuffer()
        assert buf.elapsed_seconds() == 0.0

    def test_1_second(self):
        buf = AudioBuffer()
        # 1 second = 44100 frames = 176400 bytes
        chunk = b"\x00" * 17640  # 100ms
        for _ in range(10):
            buf.put(chunk)
        assert buf.elapsed_seconds() == pytest.approx(1.0)

    def test_fractional_seconds(self):
        buf = AudioBuffer()
        chunk = b"\x00" * 17640  # 100ms
        buf.put(chunk)
        assert buf.elapsed_seconds() == pytest.approx(0.1)


class TestEOFSignal:
    def test_signal_eof_returns_none(self):
        buf = AudioBuffer()
        buf.put(b"\x00" * 4)
        buf.signal_eof()

        assert buf.get(timeout=1) == b"\x00" * 4
        assert buf.get(timeout=1) is None

    def test_eof_after_empty_buffer(self):
        buf = AudioBuffer()
        buf.signal_eof()
        assert buf.get(timeout=1) is None


class TestBackpressure:
    def test_max_chunks_respected(self):
        buf = AudioBuffer(max_chunks=3)
        for _ in range(3):
            buf.put(b"\x00" * 4)
        assert buf.full

    def test_put_blocks_when_full(self):
        buf = AudioBuffer(max_chunks=2)
        buf.put(b"\x00" * 4)
        buf.put(b"\x00" * 4)

        # put should block; use timeout to verify
        with pytest.raises(Exception):
            buf.put(b"\x00" * 4, timeout=0.05)


class TestThreadSafety:
    def test_concurrent_put_get(self):
        buf = AudioBuffer(max_chunks=100)
        n_chunks = 50
        chunk = b"\x00" * (BYTES_PER_FRAME * 100)
        results = []

        def producer():
            for _ in range(n_chunks):
                buf.put(chunk)
            buf.signal_eof()

        def consumer():
            while True:
                c = buf.get(timeout=5)
                if c is None:
                    break
                results.append(c)

        t1 = threading.Thread(target=producer)
        t2 = threading.Thread(target=consumer)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert len(results) == n_chunks
        assert buf.frames_processed == n_chunks * 100

    def test_frame_counter_thread_safe(self):
        """Multiple producers writing concurrently."""
        buf = AudioBuffer(max_chunks=1000)
        chunk = b"\x00" * (BYTES_PER_FRAME * 10)  # 10 frames
        n_per_thread = 100
        n_threads = 4

        def writer():
            for _ in range(n_per_thread):
                buf.put(chunk)

        threads = [threading.Thread(target=writer) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert buf.frames_processed == n_threads * n_per_thread * 10


class TestIntrospection:
    def test_empty_initially(self):
        buf = AudioBuffer()
        assert buf.empty
        assert not buf.full
        assert buf.pending == 0

    def test_pending_tracks_items(self):
        buf = AudioBuffer()
        buf.put(b"\x00" * 4)
        buf.put(b"\x00" * 4)
        assert buf.pending == 2
        buf.get(timeout=1)
        assert buf.pending == 1
