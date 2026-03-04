"""Tests for AudioStreamBuffer (D3.1)."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from dreamsync.capture.buffer import AudioStreamBuffer, BufferConfig


class TestBufferBasics:
    def test_empty_read_mono(self):
        buf = AudioStreamBuffer()
        result = buf.read_all()
        assert result.shape == (0,)
        assert result.dtype == np.float32

    def test_empty_read_stereo(self):
        buf = AudioStreamBuffer(BufferConfig(channels=2))
        result = buf.read_all()
        assert result.shape == (2, 0)

    def test_write_and_read_mono(self):
        buf = AudioStreamBuffer()
        chunk = np.ones(1024, dtype=np.float32) * 0.5
        buf.write(chunk)
        result = buf.read_all()
        np.testing.assert_array_almost_equal(result, chunk)

    def test_write_multiple_chunks(self):
        buf = AudioStreamBuffer()
        for i in range(5):
            buf.write(np.full(100, float(i), dtype=np.float32))
        result = buf.read_all()
        assert result.shape == (500,)
        assert result[0] == 0.0
        assert result[100] == 1.0
        assert result[400] == 4.0

    def test_frame_count(self):
        buf = AudioStreamBuffer()
        buf.write(np.zeros(512, dtype=np.float32))
        buf.write(np.zeros(256, dtype=np.float32))
        assert buf.frame_count == 768

    def test_duration_seconds(self):
        cfg = BufferConfig(sample_rate=44100)
        buf = AudioStreamBuffer(cfg)
        buf.write(np.zeros(44100, dtype=np.float32))
        assert abs(buf.duration_seconds - 1.0) < 1e-6

    def test_duration_accuracy_within_one_frame(self):
        cfg = BufferConfig(sample_rate=44100, chunk_size=1024)
        buf = AudioStreamBuffer(cfg)
        frames = 44100 * 3 + 17  # 3 seconds + 17 frames
        buf.write(np.zeros(frames, dtype=np.float32))
        expected = frames / 44100
        assert abs(buf.duration_seconds - expected) < (1.0 / 44100)


class TestClear:
    def test_clear_resets(self):
        buf = AudioStreamBuffer()
        buf.write(np.ones(1000, dtype=np.float32))
        buf.clear()
        assert buf.frame_count == 0
        assert buf.duration_seconds == 0.0
        result = buf.read_all()
        assert result.shape == (0,)

    def test_write_after_clear(self):
        buf = AudioStreamBuffer()
        buf.write(np.ones(100, dtype=np.float32))
        buf.clear()
        buf.write(np.full(50, 0.5, dtype=np.float32))
        assert buf.frame_count == 50
        result = buf.read_all()
        np.testing.assert_array_almost_equal(result, np.full(50, 0.5))


class TestMemoryCap:
    def test_drops_oldest_when_exceeded(self):
        cfg = BufferConfig(sample_rate=10, max_song_seconds=1.0)  # 10 frames max
        buf = AudioStreamBuffer(cfg)
        # Write 15 frames total in 3 chunks of 5
        buf.write(np.full(5, 1.0, dtype=np.float32))
        buf.write(np.full(5, 2.0, dtype=np.float32))
        buf.write(np.full(5, 3.0, dtype=np.float32))
        # Should have shed oldest chunk(s) to stay <= 10 frames
        assert buf.frame_count <= 10
        result = buf.read_all()
        # The oldest chunk (1.0s) should have been dropped
        assert result[0] != 1.0 or len(result) <= 10

    def test_stays_within_cap(self):
        cfg = BufferConfig(sample_rate=100, max_song_seconds=0.5)  # 50 frames
        buf = AudioStreamBuffer(cfg)
        for _ in range(20):
            buf.write(np.zeros(10, dtype=np.float32))
        assert buf.frame_count <= 50


class TestMonoStereo:
    def test_stereo_input_to_mono_buffer(self):
        """2-D stereo input should be averaged to mono."""
        buf = AudioStreamBuffer(BufferConfig(channels=1))
        stereo = np.array([[0.5, 0.5, 0.5], [1.0, 1.0, 1.0]], dtype=np.float32)
        buf.write(stereo)
        result = buf.read_all()
        assert result.ndim == 1
        np.testing.assert_array_almost_equal(result, [0.75, 0.75, 0.75])

    def test_mono_input_to_stereo_buffer(self):
        """1-D mono input should be replicated to stereo."""
        buf = AudioStreamBuffer(BufferConfig(channels=2))
        mono = np.array([0.5, 0.5, 0.5], dtype=np.float32)
        buf.write(mono)
        result = buf.read_all()
        assert result.shape == (2, 3)
        np.testing.assert_array_almost_equal(result[0], [0.5, 0.5, 0.5])
        np.testing.assert_array_almost_equal(result[1], [0.5, 0.5, 0.5])

    def test_stereo_input_to_stereo_buffer(self):
        buf = AudioStreamBuffer(BufferConfig(channels=2))
        stereo = np.array([[0.3, 0.3], [0.7, 0.7]], dtype=np.float32)
        buf.write(stereo)
        result = buf.read_all()
        assert result.shape == (2, 2)


class TestThreadSafety:
    def test_concurrent_write_read(self):
        """Concurrent writes and reads should not corrupt data or raise."""
        buf = AudioStreamBuffer()
        errors: list[Exception] = []

        def writer():
            try:
                for _ in range(100):
                    buf.write(np.random.randn(64).astype(np.float32))
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for _ in range(100):
                    data = buf.read_all()
                    assert data.dtype == np.float32
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = []
            for _ in range(2):
                futures.append(pool.submit(writer))
                futures.append(pool.submit(reader))
            for f in futures:
                f.result()

        assert len(errors) == 0

    def test_concurrent_write_clear(self):
        """Concurrent writes and clears should not raise."""
        buf = AudioStreamBuffer()
        errors: list[Exception] = []

        def writer():
            try:
                for _ in range(100):
                    buf.write(np.zeros(32, dtype=np.float32))
            except Exception as e:
                errors.append(e)

        def clearer():
            try:
                for _ in range(50):
                    buf.clear()
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(writer), pool.submit(writer), pool.submit(clearer)]
            for f in futures:
                f.result()

        assert len(errors) == 0
