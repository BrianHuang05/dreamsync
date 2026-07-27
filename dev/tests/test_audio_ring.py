"""Tests for bounded reactive-live audio handoff primitives."""

from __future__ import annotations

import numpy as np

from dreamsync.audio.ring import AudioBlockRing, PcmFrameBuffer


def _block(value: float, frames: int = 4, channels: int = 1) -> np.ndarray:
    return np.full((frames, channels), value, dtype=np.float32)


def test_audio_block_ring_preserves_order_and_metadata() -> None:
    ring = AudioBlockRing(capacity=3, blocksize=4, channels=2)

    assert ring.write_from_callback(
        _block(1.0, channels=2),
        frames=4,
        capture_sample_index=8,
        adc_time=12.5,
        callback_monotonic=20.0,
    )
    result = ring.read()

    assert result is not None
    assert result.frames == 4
    assert result.channels == 2
    assert result.capture_sample_index == 8
    assert result.adc_time == 12.5
    assert result.callback_monotonic == 20.0
    assert result.sequence == 0
    np.testing.assert_array_equal(result.data, _block(1.0, channels=2))


def test_audio_block_ring_drops_oldest_when_full() -> None:
    ring = AudioBlockRing(capacity=2, blocksize=4, channels=1)
    for index in range(3):
        assert ring.write_from_callback(
            _block(float(index)),
            frames=4,
            capture_sample_index=index * 4,
            adc_time=None,
            callback_monotonic=float(index),
        )

    first = ring.read()
    second = ring.read()
    stats = ring.snapshot()

    assert first is not None and second is not None
    assert first.sequence == 1
    assert second.sequence == 2
    assert stats.overwritten_blocks == 1
    assert stats.dropped_blocks == 1
    assert stats.depth == 0


def test_audio_block_ring_rejects_oversize_without_waiting() -> None:
    ring = AudioBlockRing(capacity=2, blocksize=4, channels=1)

    assert not ring.write_from_callback(
        _block(1.0, frames=5),
        frames=5,
        capture_sample_index=0,
        adc_time=None,
        callback_monotonic=0.0,
    )
    assert ring.snapshot().invalid_blocks == 1


def test_pcm_frame_buffer_reads_overlapping_frames_across_wrap() -> None:
    pcm = PcmFrameBuffer(capacity_samples=8, channels=1)
    pcm.append(np.arange(6, dtype=np.float32).reshape(-1, 1), capture_sample_index=10)

    frame1, index1 = pcm.read_frame(frame_size=4, hop_size=2)
    pcm.append(
        np.arange(6, 10, dtype=np.float32).reshape(-1, 1),
        capture_sample_index=16,
    )
    frame2, index2 = pcm.read_frame(frame_size=4, hop_size=2)
    frame3, index3 = pcm.read_frame(frame_size=4, hop_size=2)

    assert index1 == 10
    assert index2 == 12
    assert index3 == 14
    np.testing.assert_array_equal(frame1[:, 0], [0, 1, 2, 3])
    np.testing.assert_array_equal(frame2[:, 0], [2, 3, 4, 5])
    np.testing.assert_array_equal(frame3[:, 0], [4, 5, 6, 7])


def test_pcm_frame_buffer_resets_on_source_gap() -> None:
    pcm = PcmFrameBuffer(capacity_samples=16, channels=1)
    assert not pcm.append(_block(1.0), capture_sample_index=0)
    assert pcm.append(_block(2.0), capture_sample_index=12)

    assert pcm.discontinuities == 1
    assert pcm.available_samples == 4
    assert pcm.start_sample_index == 12
    frame, source_index = pcm.read_frame(frame_size=4, hop_size=4)
    assert source_index == 12
    np.testing.assert_array_equal(frame[:, 0], [2.0] * 4)
