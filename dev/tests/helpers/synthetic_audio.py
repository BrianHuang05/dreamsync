"""Synthetic PCM audio generation and mock pipeline components for integration tests."""

from __future__ import annotations

import math
import struct
from io import BytesIO
from pathlib import Path


# ---------------------------------------------------------------------------
# PCM generation
# ---------------------------------------------------------------------------

def generate_sine_pcm(
    frequency: float,
    duration_seconds: float,
    sample_rate: int = 48000,
    channels: int = 2,
) -> bytes:
    """Generate raw PCM s16le bytes of a sine wave."""
    n_frames = int(duration_seconds * sample_rate)
    samples = []
    for i in range(n_frames):
        val = int(32767 * math.sin(2 * math.pi * frequency * i / sample_rate))
        for _ in range(channels):
            samples.append(val)
    return struct.pack(f"<{len(samples)}h", *samples)


def generate_multi_song_pcm(
    song_durations: list[float],
    frequencies: list[float] | None = None,
    sample_rate: int = 48000,
    channels: int = 2,
) -> bytes:
    """Generate PCM with distinct frequencies per song for split verification."""
    if frequencies is None:
        frequencies = [220 + i * 110 for i in range(len(song_durations))]
    parts = []
    for dur, freq in zip(song_durations, frequencies):
        parts.append(generate_sine_pcm(freq, dur, sample_rate, channels))
    return b"".join(parts)


# ---------------------------------------------------------------------------
# Mock capture process
# ---------------------------------------------------------------------------

class MockCaptureProcess:
    """Drop-in replacement for CaptureProcessManager — reads from BytesIO."""

    def __init__(self, pcm_data: bytes) -> None:
        self._data = pcm_data
        self._stream: BytesIO | None = None

    @property
    def stdout(self):
        return self._stream

    def start(self, device_name=None):
        self._stream = BytesIO(self._data)

    def stop(self):
        if self._stream:
            self._stream.close()
        return 0

    def is_alive(self):
        return self._stream is not None and not self._stream.closed

    def restart(self, device_name=None):
        self.stop()
        self.start(device_name)


# ---------------------------------------------------------------------------
# Mock encoder process
# ---------------------------------------------------------------------------

class MockEncoderProcess:
    """Drop-in replacement for EncoderProcess — writes raw bytes to disk."""

    def __init__(self, output_path: str, **kwargs) -> None:
        self._output_path = output_path
        self._file = None
        self._bytes_written = 0

    @property
    def output_path(self):
        return self._output_path

    @property
    def stderr_output(self):
        return []

    def start(self):
        Path(self._output_path).parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self._output_path, "wb")

    def write(self, data: bytes):
        if self._file:
            self._file.write(data)
            self._bytes_written += len(data)

    def close(self):
        if self._file and not self._file.closed:
            self._file.close()

    def wait(self, timeout=30.0):
        return 0  # success

    def is_alive(self):
        return self._file is not None and not self._file.closed


def make_mock_encoder_factory(output_dir: str, extension: str = ".mp3"):
    """Return a factory function that creates MockEncoderProcess instances."""
    counter = [0]

    def factory(segment_index: int) -> MockEncoderProcess:
        path = str(Path(output_dir) / f"segment_{counter[0]:06d}{extension}")
        counter[0] += 1
        enc = MockEncoderProcess(output_path=path)
        enc.start()
        return enc

    return factory
