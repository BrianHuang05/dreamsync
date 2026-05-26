"""Tests for SongFileWriter (D3.3)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from dreamsync.capture.writer import (
    EncodeError,
    SongFileWriter,
    WriterConfig,
    _sanitise_filename,
    check_ffmpeg,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sine_pcm(duration: float = 20.0, sr: int = 44100, freq: float = 440.0) -> np.ndarray:
    """Generate a simple sine wave as float32 PCM."""
    t = np.linspace(0, duration, int(sr * duration), endpoint=False, dtype=np.float32)
    return 0.5 * np.sin(2.0 * np.pi * freq * t)


def _mock_ffmpeg_success(cmd, stdin, stdout, stderr):
    """Mock Popen that simulates successful ffmpeg encoding."""
    mock_proc = MagicMock()
    mock_proc.communicate.return_value = (b"", b"")
    mock_proc.returncode = 0
    # Create the output file to simulate ffmpeg writing it
    # Find the output path (last argument)
    output_path = Path(cmd[-1])
    output_path.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 100)  # fake mp3 header
    return mock_proc


# ---------------------------------------------------------------------------
# check_ffmpeg tests
# ---------------------------------------------------------------------------


class TestCheckFfmpeg:
    def test_ffmpeg_available(self):
        with patch("dreamsync.capture.writer.resolve_ffmpeg", return_value="ffmpeg"), \
             patch("dreamsync.capture.writer.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            assert check_ffmpeg() is True

    def test_ffmpeg_not_found(self):
        with patch("dreamsync.capture.writer.resolve_ffmpeg", return_value="ffmpeg"), \
             patch("dreamsync.capture.writer.subprocess.run", side_effect=FileNotFoundError):
            assert check_ffmpeg() is False

    def test_ffmpeg_error(self):
        with patch("dreamsync.capture.writer.resolve_ffmpeg", return_value="ffmpeg"), \
             patch("dreamsync.capture.writer.subprocess.run",
                   side_effect=subprocess.CalledProcessError(1, "ffmpeg")):
            assert check_ffmpeg() is False

    def test_ffmpeg_missing_before_subprocess(self):
        with patch("dreamsync.capture.writer.resolve_ffmpeg", return_value=None), \
             patch("dreamsync.capture.writer.subprocess.run") as mock_run:
            assert check_ffmpeg() is False
            mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# Filename sanitisation tests
# ---------------------------------------------------------------------------


class TestSanitiseFilename:
    def test_removes_illegal_chars(self):
        assert _sanitise_filename('foo:bar/baz<>"|?*') == "foo_bar_baz______"

    def test_strips_dots_and_spaces(self):
        assert _sanitise_filename("  .hello. ") == "hello"

    def test_empty_becomes_untitled(self):
        assert _sanitise_filename("") == "untitled"
        assert _sanitise_filename("...") == "untitled"

    def test_normal_name_unchanged(self):
        assert _sanitise_filename("Daft Punk") == "Daft Punk"


# ---------------------------------------------------------------------------
# WriterConfig defaults
# ---------------------------------------------------------------------------


class TestWriterConfig:
    def test_defaults(self):
        cfg = WriterConfig()
        assert cfg.output_dir == "captured_songs"
        assert cfg.bitrate == "192k"
        assert cfg.sample_rate == 44100
        assert cfg.channels == 1
        assert cfg.min_duration_seconds == 15.0
        assert cfg.naming == "timestamp"


# ---------------------------------------------------------------------------
# SongFileWriter.finalize tests
# ---------------------------------------------------------------------------


class TestFinalize:
    def test_skip_short_fragment(self, tmp_path):
        cfg = WriterConfig(output_dir=str(tmp_path), min_duration_seconds=15.0)
        writer = SongFileWriter(cfg)
        # 5 seconds of audio — below threshold
        pcm = _sine_pcm(duration=5.0)
        result = writer.finalize(pcm)
        assert result is None

    def test_finalize_calls_encode(self, tmp_path):
        cfg = WriterConfig(output_dir=str(tmp_path), min_duration_seconds=1.0)
        writer = SongFileWriter(cfg)
        pcm = _sine_pcm(duration=2.0)

        with patch("dreamsync.capture.writer.resolve_ffmpeg", return_value="ffmpeg"), \
             patch("dreamsync.capture.writer.subprocess.Popen", side_effect=_mock_ffmpeg_success):
            result = writer.finalize(pcm)

        assert result is not None
        assert result.suffix == ".mp3"
        assert result.exists()

    def test_finalize_returns_path(self, tmp_path):
        cfg = WriterConfig(output_dir=str(tmp_path), min_duration_seconds=1.0)
        writer = SongFileWriter(cfg)
        pcm = _sine_pcm(duration=2.0)

        with patch("dreamsync.capture.writer.resolve_ffmpeg", return_value="ffmpeg"), \
             patch("dreamsync.capture.writer.subprocess.Popen", side_effect=_mock_ffmpeg_success):
            result = writer.finalize(pcm)
            assert isinstance(result, Path)


# ---------------------------------------------------------------------------
# Naming modes tests
# ---------------------------------------------------------------------------


class TestNaming:
    def test_timestamp_naming(self, tmp_path):
        cfg = WriterConfig(output_dir=str(tmp_path), naming="timestamp", min_duration_seconds=1.0)
        writer = SongFileWriter(cfg)
        pcm = _sine_pcm(duration=2.0)

        with patch("dreamsync.capture.writer.resolve_ffmpeg", return_value="ffmpeg"), \
             patch("dreamsync.capture.writer.subprocess.Popen", side_effect=_mock_ffmpeg_success):
            result = writer.finalize(pcm)

        # Should be like 20260303_142015.mp3
        assert result is not None
        assert result.stem[0:8].isdigit()

    def test_metadata_naming(self, tmp_path):
        cfg = WriterConfig(output_dir=str(tmp_path), naming="metadata", min_duration_seconds=1.0)
        writer = SongFileWriter(cfg)
        pcm = _sine_pcm(duration=2.0)
        meta = {"track_name": "Around the World", "artist": "Daft Punk"}

        with patch("dreamsync.capture.writer.resolve_ffmpeg", return_value="ffmpeg"), \
             patch("dreamsync.capture.writer.subprocess.Popen", side_effect=_mock_ffmpeg_success):
            result = writer.finalize(pcm, metadata=meta)

        assert result is not None
        assert "Daft Punk" in result.stem
        assert "Around the World" in result.stem

    def test_metadata_fallback_to_timestamp(self, tmp_path):
        cfg = WriterConfig(output_dir=str(tmp_path), naming="metadata", min_duration_seconds=1.0)
        writer = SongFileWriter(cfg)
        pcm = _sine_pcm(duration=2.0)

        with patch("dreamsync.capture.writer.resolve_ffmpeg", return_value="ffmpeg"), \
             patch("dreamsync.capture.writer.subprocess.Popen", side_effect=_mock_ffmpeg_success):
            result = writer.finalize(pcm, metadata=None)

        assert result is not None
        # Should fall back to timestamp format
        assert result.stem[0:8].isdigit()

    def test_collision_avoidance(self, tmp_path):
        cfg = WriterConfig(output_dir=str(tmp_path), naming="metadata", min_duration_seconds=1.0)
        writer = SongFileWriter(cfg)
        pcm = _sine_pcm(duration=2.0)
        meta = {"track_name": "Test", "artist": "Artist"}

        with patch("dreamsync.capture.writer.resolve_ffmpeg", return_value="ffmpeg"), \
             patch("dreamsync.capture.writer.subprocess.Popen", side_effect=_mock_ffmpeg_success):
            p1 = writer.finalize(pcm, metadata=meta)
            p2 = writer.finalize(pcm, metadata=meta)

        assert p1 != p2
        assert p2 is not None
        assert "_2" in p2.stem


# ---------------------------------------------------------------------------
# Encoding error tests
# ---------------------------------------------------------------------------


class TestEncodeErrors:
    def test_ffmpeg_failure_raises_encode_error(self, tmp_path):
        cfg = WriterConfig(output_dir=str(tmp_path), min_duration_seconds=1.0)
        writer = SongFileWriter(cfg)
        pcm = _sine_pcm(duration=2.0)

        def _mock_ffmpeg_fail(cmd, stdin, stdout, stderr):
            mock_proc = MagicMock()
            mock_proc.communicate.return_value = (b"", b"Error: bad encoding")
            mock_proc.returncode = 1
            return mock_proc

        with patch("dreamsync.capture.writer.resolve_ffmpeg", return_value="ffmpeg"), \
             patch("dreamsync.capture.writer.subprocess.Popen", side_effect=_mock_ffmpeg_fail):
            with pytest.raises(EncodeError, match="ffmpeg exited"):
                writer.finalize(pcm)

    def test_missing_output_raises_encode_error(self, tmp_path):
        cfg = WriterConfig(output_dir=str(tmp_path), min_duration_seconds=1.0)
        writer = SongFileWriter(cfg)
        pcm = _sine_pcm(duration=2.0)

        def _mock_ffmpeg_no_output(cmd, stdin, stdout, stderr):
            mock_proc = MagicMock()
            mock_proc.communicate.return_value = (b"", b"")
            mock_proc.returncode = 0
            # Don't create the output file
            return mock_proc

        with patch("dreamsync.capture.writer.resolve_ffmpeg", return_value="ffmpeg"), \
             patch("dreamsync.capture.writer.subprocess.Popen", side_effect=_mock_ffmpeg_no_output):
            with pytest.raises(EncodeError, match="missing or empty"):
                writer.finalize(pcm)


# ---------------------------------------------------------------------------
# PCM conversion tests
# ---------------------------------------------------------------------------


class TestPcmConversion:
    def test_float32_to_int16_clipping(self, tmp_path):
        """Values outside [-1, 1] should be clipped."""
        cfg = WriterConfig(output_dir=str(tmp_path), min_duration_seconds=0.1)
        writer = SongFileWriter(cfg)
        pcm = np.array([2.0, -2.0, 0.5] * 5000, dtype=np.float32)

        captured_bytes = []

        def _capture_popen(cmd, stdin, stdout, stderr):
            mock_proc = MagicMock()
            def _comm(input=None):
                captured_bytes.append(input)
                Path(cmd[-1]).write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 100)
                return (b"", b"")
            mock_proc.communicate = _comm
            mock_proc.returncode = 0
            return mock_proc

        with patch("dreamsync.capture.writer.resolve_ffmpeg", return_value="ffmpeg"), \
             patch("dreamsync.capture.writer.subprocess.Popen", side_effect=_capture_popen):
            writer.finalize(pcm)

        # Verify the int16 data was clipped
        raw = np.frombuffer(captured_bytes[0], dtype=np.int16)
        assert raw.max() == 32767
        assert raw.min() == -32767

    def test_output_dir_created(self, tmp_path):
        out_dir = tmp_path / "nested" / "output"
        cfg = WriterConfig(output_dir=str(out_dir))
        SongFileWriter(cfg)
        assert out_dir.exists()


# ---------------------------------------------------------------------------
# Real ffmpeg integration test (skipped if ffmpeg not available)
# ---------------------------------------------------------------------------


class TestRealFfmpeg:
    @pytest.mark.slow
    def test_real_encode(self, tmp_path):
        """Actually encode with ffmpeg — verifies end-to-end pipeline."""
        if not check_ffmpeg():
            pytest.skip("ffmpeg not available")

        cfg = WriterConfig(output_dir=str(tmp_path), min_duration_seconds=1.0)
        writer = SongFileWriter(cfg)
        pcm = _sine_pcm(duration=3.0)
        result = writer.finalize(pcm)

        assert result is not None
        assert result.exists()
        assert result.stat().st_size > 0
