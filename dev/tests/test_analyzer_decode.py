"""Tests for the Mp3Decoder (D4.1)."""

from __future__ import annotations

import struct
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from dreamsync.analyzer.decode import AudioData, DecodeError, decode_mp3, _probe_file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_pcm(
    n_samples: int = 4410,
    sample_rate: int = 44100,
    *,
    channels: int = 1,
) -> bytes:
    """Generate raw float32 PCM bytes for a short sine wave."""
    t = np.linspace(0, n_samples / sample_rate, n_samples, dtype=np.float32)
    left = 0.5 * np.sin(2 * np.pi * 440 * t)
    if channels <= 1:
        return left.astype(np.float32).tobytes()
    right = 0.35 * np.sin(2 * np.pi * 660 * t)
    signal = np.stack((left, right), axis=1).astype(np.float32)
    return signal.tobytes()


def _mock_ffprobe_result(sr: int = 44100, channels: int = 2, duration: float = 3.0):
    """Return a mock ffprobe JSON result."""
    import json
    data = {
        "streams": [{
            "codec_type": "audio",
            "sample_rate": str(sr),
            "channels": channels,
            "duration": str(duration),
        }]
    }
    return json.dumps(data)


# ---------------------------------------------------------------------------
# AudioData dataclass
# ---------------------------------------------------------------------------

class TestAudioData:
    def test_frozen(self):
        signal = np.zeros(100, dtype=np.float32)
        ad = AudioData(signal=signal, sample_rate=44100, duration=0.1, channels=1)
        with pytest.raises(AttributeError):
            ad.sample_rate = 48000  # type: ignore[misc]

    def test_fields(self):
        signal = np.ones(44100, dtype=np.float32)
        ad = AudioData(signal=signal, sample_rate=44100, duration=1.0, channels=2)
        assert ad.sample_rate == 44100
        assert ad.duration == 1.0
        assert ad.channels == 2
        assert len(ad.signal) == 44100
        assert ad.stereo_signal is None


# ---------------------------------------------------------------------------
# decode_mp3 — error cases
# ---------------------------------------------------------------------------

class TestDecodeMp3Errors:
    def test_missing_file(self, tmp_path: Path):
        with pytest.raises(DecodeError, match="File not found"):
            decode_mp3(tmp_path / "nonexistent.mp3")

    def test_empty_file(self, tmp_path: Path):
        empty = tmp_path / "empty.mp3"
        empty.write_bytes(b"")
        with pytest.raises(DecodeError, match="File is empty"):
            decode_mp3(empty)

    def test_no_ffmpeg(self, tmp_path: Path):
        fake = tmp_path / "test.mp3"
        fake.write_bytes(b"\xff\xfb\x90\x00" * 100)  # fake mp3 header bytes
        with patch("dreamsync.analyzer.decode.check_ffmpeg", return_value=False):
            with pytest.raises(DecodeError, match="ffmpeg not found"):
                decode_mp3(fake)

    def test_corrupt_file(self, tmp_path: Path):
        corrupt = tmp_path / "corrupt.mp3"
        corrupt.write_bytes(b"this is not audio data at all")
        # ffprobe or ffmpeg should fail on this
        with pytest.raises(DecodeError):
            decode_mp3(corrupt)

    def test_ffmpeg_produces_no_output(self, tmp_path: Path):
        fake = tmp_path / "test.mp3"
        fake.write_bytes(b"\xff\xfb\x90\x00" * 100)
        mock_probe = MagicMock()
        mock_probe.stdout = '{"streams": [{"codec_type": "audio", "channels": 2}]}'
        mock_run = MagicMock(
            return_value=MagicMock(stdout=b"", stderr=b"", returncode=0)
        )
        with patch("dreamsync.analyzer.decode.check_ffmpeg", return_value=True), \
             patch("dreamsync.analyzer.decode._probe_file", return_value={"channels": 2}), \
             patch("subprocess.run", mock_run):
            with pytest.raises(DecodeError, match="no output"):
                decode_mp3(fake)


# ---------------------------------------------------------------------------
# decode_mp3 — happy path (mocked ffmpeg)
# ---------------------------------------------------------------------------

class TestDecodeMp3Mocked:
    def test_basic_decode(self, tmp_path: Path):
        """Mocked decode returns correct AudioData."""
        fake = tmp_path / "song.mp3"
        fake.write_bytes(b"\xff\xfb" * 50)

        n_samples = 44100  # 1 second
        pcm_bytes = _make_fake_pcm(n_samples, 44100, channels=2)

        with patch("dreamsync.analyzer.decode.check_ffmpeg", return_value=True), \
             patch("dreamsync.analyzer.decode._probe_file", return_value={"channels": 2}), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=pcm_bytes, stderr=b"", returncode=0)
            result = decode_mp3(fake, target_sr=44100)

        assert isinstance(result, AudioData)
        assert result.sample_rate == 44100
        assert result.channels == 2
        assert len(result.signal) == n_samples
        assert abs(result.duration - 1.0) < 0.01
        assert result.signal.dtype == np.float32
        assert result.stereo_signal is not None
        assert result.stereo_signal.shape == (n_samples, 2)

    def test_resample(self, tmp_path: Path):
        """Target sample rate is passed to ffmpeg."""
        fake = tmp_path / "song.mp3"
        fake.write_bytes(b"\xff\xfb" * 50)

        n_samples = 22050  # 0.5 seconds at 44100
        pcm_bytes = _make_fake_pcm(n_samples, 44100, channels=2)

        with patch("dreamsync.analyzer.decode.check_ffmpeg", return_value=True), \
             patch("dreamsync.analyzer.decode._probe_file", return_value={"channels": 1}), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=pcm_bytes, stderr=b"", returncode=0)
            result = decode_mp3(fake, target_sr=22050)

        # Verify ffmpeg was called with correct -ar
        call_args = mock_run.call_args[0][0]
        ar_idx = call_args.index("-ar")
        assert call_args[ar_idx + 1] == "22050"
        assert result.sample_rate == 22050

    def test_stereo_retention_keeps_mono_compatibility(self, tmp_path: Path):
        """Decode retains stereo PCM but still exposes a mono primary signal."""
        fake = tmp_path / "stereo.mp3"
        fake.write_bytes(b"\xff\xfb" * 50)

        n_samples = 4410
        pcm_bytes = _make_fake_pcm(n_samples, 44100, channels=2)

        with patch("dreamsync.analyzer.decode.check_ffmpeg", return_value=True), \
             patch("dreamsync.analyzer.decode._probe_file", return_value={"channels": 2}), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=pcm_bytes, stderr=b"", returncode=0)
            result = decode_mp3(fake)

        assert result.signal.ndim == 1
        assert result.stereo_signal is not None
        assert result.stereo_signal.shape == (n_samples, 2)
        call_args = mock_run.call_args[0][0]
        ac_idx = call_args.index("-ac")
        assert call_args[ac_idx + 1] == "2"

    def test_disable_stereo_retention(self, tmp_path: Path):
        fake = tmp_path / "mono-only.mp3"
        fake.write_bytes(b"\xff\xfb" * 50)

        n_samples = 4410
        pcm_bytes = _make_fake_pcm(n_samples, 44100, channels=1)

        with patch("dreamsync.analyzer.decode.check_ffmpeg", return_value=True), \
             patch("dreamsync.analyzer.decode._probe_file", return_value={"channels": 1}), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=pcm_bytes, stderr=b"", returncode=0)
            result = decode_mp3(fake, preserve_stereo=False)

        assert result.signal.ndim == 1
        assert result.stereo_signal is None
        call_args = mock_run.call_args[0][0]
        ac_idx = call_args.index("-ac")
        assert call_args[ac_idx + 1] == "1"

    def test_path_coercion(self, tmp_path: Path):
        """String paths are coerced to Path."""
        fake = tmp_path / "song.mp3"
        fake.write_bytes(b"\xff\xfb" * 50)

        pcm_bytes = _make_fake_pcm(4410, 44100, channels=2)
        with patch("dreamsync.analyzer.decode.check_ffmpeg", return_value=True), \
             patch("dreamsync.analyzer.decode._probe_file", return_value={"channels": 2}), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=pcm_bytes, stderr=b"", returncode=0)
            result = decode_mp3(str(fake))

        assert isinstance(result, AudioData)


# ---------------------------------------------------------------------------
# _probe_file
# ---------------------------------------------------------------------------

class TestProbeFile:
    def test_no_audio_stream(self, tmp_path: Path):
        """Probe raises if file has no audio stream."""
        fake = tmp_path / "video.mp4"
        fake.write_bytes(b"\x00" * 100)

        probe_json = '{"streams": [{"codec_type": "video"}]}'
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=probe_json, stderr="", returncode=0)
            with pytest.raises(DecodeError, match="No audio stream"):
                _probe_file(fake)

    def test_ffprobe_not_found(self, tmp_path: Path):
        fake = tmp_path / "test.mp3"
        fake.write_bytes(b"\xff" * 10)
        with patch("subprocess.run", side_effect=FileNotFoundError):
            with pytest.raises(DecodeError, match="ffprobe not found"):
                _probe_file(fake)
