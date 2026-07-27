"""Bounded, callback-safe audio handoff primitives for reactive live mode."""

from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class AudioBlock:
    """One consumer-owned copy of a captured input block."""

    data: np.ndarray
    frames: int
    channels: int
    capture_sample_index: int
    adc_time: float | None
    callback_monotonic: float
    sequence: int


@dataclass(frozen=True)
class AudioRingStats:
    capacity: int
    depth: int
    writes: int
    reads: int
    overwritten_blocks: int
    contention_drops: int
    invalid_blocks: int

    @property
    def dropped_blocks(self) -> int:
        return self.overwritten_blocks + self.contention_drops + self.invalid_blocks


class AudioBlockRing:
    """Preallocated single-producer/single-consumer block ring.

    ``write_from_callback()`` never waits for the consumer. If the bookkeeping
    lock is momentarily unavailable, the incoming block is dropped. If the
    ring is full, the oldest unconsumed block is discarded so reactive mode
    stays close to current audio.
    """

    def __init__(
        self,
        *,
        capacity: int,
        blocksize: int,
        channels: int,
        dtype: np.dtype | str = np.float32,
    ) -> None:
        if capacity < 2:
            raise ValueError("capacity must be at least 2")
        if blocksize <= 0:
            raise ValueError("blocksize must be > 0")
        if channels <= 0:
            raise ValueError("channels must be > 0")

        self.capacity = int(capacity)
        self.blocksize = int(blocksize)
        self.channels = int(channels)
        self._storage = np.empty(
            (self.capacity, self.blocksize, self.channels),
            dtype=dtype,
        )
        self._frames = np.zeros(self.capacity, dtype=np.int32)
        self._channel_counts = np.zeros(self.capacity, dtype=np.int16)
        self._capture_sample_indices = np.zeros(self.capacity, dtype=np.int64)
        self._adc_times = np.full(self.capacity, np.nan, dtype=np.float64)
        self._callback_times = np.zeros(self.capacity, dtype=np.float64)
        self._sequences = np.zeros(self.capacity, dtype=np.int64)
        self._lock = threading.Lock()
        self._read_index = 0
        self._write_index = 0
        self._depth = 0
        self._writes = 0
        self._reads = 0
        self._overwritten_blocks = 0
        self._contention_drops = 0
        self._invalid_blocks = 0
        self._next_sequence = 0

    def write_from_callback(
        self,
        indata: np.ndarray,
        *,
        frames: int,
        capture_sample_index: int,
        adc_time: float | None,
        callback_monotonic: float,
    ) -> bool:
        """Copy one input block without ever waiting for the consumer."""

        if not self._lock.acquire(blocking=False):
            self._contention_drops += 1
            return False
        try:
            arr = np.asarray(indata)
            frame_count = int(frames)
            if (
                arr.ndim != 2
                or frame_count <= 0
                or frame_count > self.blocksize
                or arr.shape[0] < frame_count
                or arr.shape[1] <= 0
                or arr.shape[1] > self.channels
            ):
                self._invalid_blocks += 1
                return False

            if self._depth == self.capacity:
                self._read_index = (self._read_index + 1) % self.capacity
                self._depth -= 1
                self._overwritten_blocks += 1

            slot = self._write_index
            channel_count = int(arr.shape[1])
            np.copyto(
                self._storage[slot, :frame_count, :channel_count],
                arr[:frame_count, :channel_count],
                casting="unsafe",
            )
            self._frames[slot] = frame_count
            self._channel_counts[slot] = channel_count
            self._capture_sample_indices[slot] = int(capture_sample_index)
            self._adc_times[slot] = np.nan if adc_time is None else float(adc_time)
            self._callback_times[slot] = float(callback_monotonic)
            self._sequences[slot] = self._next_sequence

            self._next_sequence += 1
            self._write_index = (slot + 1) % self.capacity
            self._depth += 1
            self._writes += 1
            return True
        finally:
            self._lock.release()

    def read(self) -> AudioBlock | None:
        """Return the oldest block as a consumer-owned array."""

        with self._lock:
            if self._depth == 0:
                return None
            slot = self._read_index
            frames = int(self._frames[slot])
            channels = int(self._channel_counts[slot])
            data = self._storage[slot, :frames, :channels].copy()
            adc_value = float(self._adc_times[slot])
            block = AudioBlock(
                data=data,
                frames=frames,
                channels=channels,
                capture_sample_index=int(self._capture_sample_indices[slot]),
                adc_time=None if np.isnan(adc_value) else adc_value,
                callback_monotonic=float(self._callback_times[slot]),
                sequence=int(self._sequences[slot]),
            )
            self._read_index = (slot + 1) % self.capacity
            self._depth -= 1
            self._reads += 1
            return block

    def snapshot(self) -> AudioRingStats:
        with self._lock:
            return AudioRingStats(
                capacity=self.capacity,
                depth=self._depth,
                writes=self._writes,
                reads=self._reads,
                overwritten_blocks=self._overwritten_blocks,
                contention_drops=self._contention_drops,
                invalid_blocks=self._invalid_blocks,
            )


class PcmFrameBuffer:
    """Fixed-capacity circular sample buffer with source-sample timestamps."""

    def __init__(self, *, capacity_samples: int, channels: int) -> None:
        if capacity_samples <= 0:
            raise ValueError("capacity_samples must be > 0")
        if channels <= 0:
            raise ValueError("channels must be > 0")
        self.capacity_samples = int(capacity_samples)
        self.channels = int(channels)
        self._storage = np.empty(
            (self.capacity_samples, self.channels),
            dtype=np.float32,
        )
        self._start = 0
        self._size = 0
        self._start_sample_index: int | None = None
        self._next_sample_index: int | None = None
        self.discontinuities = 0
        self.overrun_samples = 0

    @property
    def available_samples(self) -> int:
        return self._size

    @property
    def start_sample_index(self) -> int | None:
        return self._start_sample_index

    def clear(self) -> None:
        self._start = 0
        self._size = 0
        self._start_sample_index = None
        self._next_sample_index = None

    def append(self, samples: np.ndarray, *, capture_sample_index: int) -> bool:
        """Append samples and return True when a source discontinuity occurred."""

        arr = np.asarray(samples, dtype=np.float32)
        if arr.ndim != 2 or arr.shape[1] != self.channels:
            raise ValueError(
                f"samples must have shape (n, {self.channels}); got {arr.shape}"
            )
        count = int(arr.shape[0])
        if count == 0:
            return False

        sample_index = int(capture_sample_index)
        discontinuity = (
            self._next_sample_index is not None
            and sample_index != self._next_sample_index
        )
        if discontinuity:
            self.discontinuities += 1
            self.clear()

        if count >= self.capacity_samples:
            dropped = count - self.capacity_samples
            self.overrun_samples += max(0, dropped)
            arr = arr[-self.capacity_samples :]
            sample_index += dropped
            count = self.capacity_samples
            self.clear()

        overflow = max(0, self._size + count - self.capacity_samples)
        if overflow:
            self._advance(overflow)
            self.overrun_samples += overflow
            discontinuity = True

        if self._size == 0:
            self._start_sample_index = sample_index

        write_at = (self._start + self._size) % self.capacity_samples
        first = min(count, self.capacity_samples - write_at)
        self._storage[write_at : write_at + first] = arr[:first]
        remaining = count - first
        if remaining:
            self._storage[:remaining] = arr[first:]
        self._size += count
        self._next_sample_index = sample_index + count
        return discontinuity

    def read_frame(self, *, frame_size: int, hop_size: int) -> tuple[np.ndarray, int]:
        if frame_size <= 0 or hop_size <= 0:
            raise ValueError("frame_size and hop_size must be > 0")
        if self._size < frame_size or self._start_sample_index is None:
            raise RuntimeError("not enough samples for a frame")

        frame = np.empty((frame_size, self.channels), dtype=np.float32)
        first = min(frame_size, self.capacity_samples - self._start)
        frame[:first] = self._storage[self._start : self._start + first]
        remaining = frame_size - first
        if remaining:
            frame[first:] = self._storage[:remaining]
        source_index = self._start_sample_index
        self._advance(min(hop_size, self._size))
        return frame, source_index

    def _advance(self, count: int) -> None:
        amount = max(0, min(int(count), self._size))
        if amount == 0:
            return
        self._start = (self._start + amount) % self.capacity_samples
        self._size -= amount
        if self._start_sample_index is not None:
            self._start_sample_index += amount
        if self._size == 0:
            self._start = 0
            self._start_sample_index = None
            self._next_sample_index = None
