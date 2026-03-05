"""Tests for dreamsync.capture.encoder_process — per-segment FFmpeg encoder."""

from unittest.mock import patch, MagicMock
import io

import pytest

from dreamsync.capture.encoder_process import EncoderProcess


@pytest.fixture
def encoder():
    return EncoderProcess("/tmp/test_segment.mp3")


class TestInit:
    def test_defaults(self, encoder):
        assert encoder.output_path == "/tmp/test_segment.mp3"
        assert encoder._sample_rate == 48000
        assert encoder._channels == 2
        assert encoder._bitrate == "192k"

    def test_custom_params(self):
        enc = EncoderProcess("/tmp/out.mp3", sample_rate=44100, channels=1, bitrate="320k")
        assert enc._sample_rate == 44100
        assert enc._channels == 1
        assert enc._bitrate == "320k"


class TestLifecycle:
    @patch("dreamsync.capture.encoder_process.subprocess.Popen")
    def test_start(self, mock_popen, encoder):
        mock_proc = MagicMock()
        mock_proc.stderr = io.BytesIO(b"")
        mock_popen.return_value = mock_proc

        encoder.start()

        mock_popen.assert_called_once()
        call_args = mock_popen.call_args
        cmd = call_args[0][0]
        assert cmd[0] == "ffmpeg"
        assert "pipe:0" in cmd
        assert "libmp3lame" in cmd

    @patch("dreamsync.capture.encoder_process.subprocess.Popen")
    def test_start_raises_if_already_started(self, mock_popen, encoder):
        mock_proc = MagicMock()
        mock_proc.stderr = io.BytesIO(b"")
        mock_popen.return_value = mock_proc

        encoder.start()
        with pytest.raises(RuntimeError, match="already started"):
            encoder.start()

    @patch("dreamsync.capture.encoder_process.subprocess.Popen")
    def test_write(self, mock_popen, encoder):
        mock_proc = MagicMock()
        mock_proc.stderr = io.BytesIO(b"")
        mock_popen.return_value = mock_proc

        encoder.start()
        encoder.write(b"\x00" * 1000)

        mock_proc.stdin.write.assert_called_once_with(b"\x00" * 1000)

    def test_write_before_start_raises(self, encoder):
        with pytest.raises(RuntimeError, match="not started"):
            encoder.write(b"\x00")

    @patch("dreamsync.capture.encoder_process.subprocess.Popen")
    def test_close(self, mock_popen, encoder):
        mock_proc = MagicMock()
        mock_proc.stderr = io.BytesIO(b"")
        mock_popen.return_value = mock_proc

        encoder.start()
        encoder.close()

        mock_proc.stdin.close.assert_called_once()

    @patch("dreamsync.capture.encoder_process.subprocess.Popen")
    def test_wait(self, mock_popen, encoder):
        mock_proc = MagicMock()
        mock_proc.stderr = io.BytesIO(b"")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        encoder.start()
        encoder.close()
        rc = encoder.wait()

        assert rc == 0

    @patch("dreamsync.capture.encoder_process.subprocess.Popen")
    def test_is_alive(self, mock_popen, encoder):
        mock_proc = MagicMock()
        mock_proc.stderr = io.BytesIO(b"")
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        encoder.start()
        assert encoder.is_alive()

        mock_proc.poll.return_value = 0
        assert not encoder.is_alive()

    def test_is_alive_before_start(self, encoder):
        assert not encoder.is_alive()
