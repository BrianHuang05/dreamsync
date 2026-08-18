"""Tests for dreamsync.capture.capture_process — FFmpeg process manager."""

from unittest.mock import patch, MagicMock, PropertyMock
import io
import subprocess

import pytest

from dreamsync.capture.capture_process import CaptureProcessManager, CaptureConfig
from dreamsync.capture.ffmpeg_device import CaptureBackend, CaptureDevice


@pytest.fixture(autouse=True)
def windows_capture_test_environment(monkeypatch):
    """Keep legacy lifecycle tests explicitly on the DirectShow backend."""
    monkeypatch.setattr("dreamsync.capture.capture_process.sys.platform", "win32")
    monkeypatch.setattr("dreamsync.capture.ffmpeg_device.sys.platform", "win32")
    monkeypatch.setattr("dreamsync.capture.capture_process.resolve_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr("dreamsync.capture.capture_process.subprocess.CREATE_NEW_PROCESS_GROUP", 0, raising=False)


@pytest.fixture
def manager():
    return CaptureProcessManager(CaptureConfig())


class TestCaptureConfig:
    def test_defaults(self):
        cfg = CaptureConfig()
        assert cfg.sample_rate == 44100
        assert cfg.channels == 2
        assert cfg.thread_queue_size == 1024
        assert cfg.device_pattern == "CABLE Output"

    def test_custom(self):
        cfg = CaptureConfig(sample_rate=44100, channels=1)
        assert cfg.sample_rate == 44100
        assert cfg.channels == 1


class TestBuildCommand:
    def test_command_structure(self, manager):
        cmd = manager._build_command("CABLE Output (VB-Audio Virtual Cable)")
        assert cmd[0].lower().endswith(("ffmpeg", "ffmpeg.exe"))
        assert "-hide_banner" in cmd
        assert "pipe:1" in cmd
        assert "audio=CABLE Output (VB-Audio Virtual Cable)" in cmd
        assert "-f" in cmd
        # Check s16le format
        idx = cmd.index("-acodec")
        assert cmd[idx + 1] == "pcm_s16le"

    def test_sample_rate_in_command(self):
        mgr = CaptureProcessManager(CaptureConfig(sample_rate=44100))
        cmd = mgr._build_command("Test Device")
        idx = cmd.index("-ar")
        assert cmd[idx + 1] == "44100"

    def test_pulse_command_preserves_source_and_pcm_contract(self, manager):
        cmd = manager._build_command(CaptureDevice(CaptureBackend.PULSE, "dreamsync_capture.monitor"))
        assert cmd[cmd.index("-f", 4) + 1] == "pulse"
        assert cmd[cmd.index("-i") + 1] == "dreamsync_capture.monitor"
        assert cmd[cmd.index("-acodec") + 1] == "pcm_s16le"
        assert cmd[cmd.index("-ar") + 1] == "44100"


class TestLifecycle:
    @patch("dreamsync.capture.capture_process.subprocess.Popen")
    @patch("dreamsync.capture.capture_process.discover_capture_device")
    def test_linux_start_uses_resolved_pulse_source(self, mock_discover, mock_popen, manager, monkeypatch):
        monkeypatch.setattr("dreamsync.capture.capture_process.sys.platform", "linux")
        mock_discover.return_value = CaptureDevice(
            CaptureBackend.PULSE, "dreamsync_capture.monitor", sample_rate=48000,
        )
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stderr = io.BytesIO(b"")
        mock_popen.return_value = mock_proc

        manager.start()

        mock_discover.assert_called_once_with("CABLE Output")
        command = mock_popen.call_args.args[0]
        assert command[command.index("-f", 4) + 1] == "pulse"
        assert command[command.index("-i") + 1] == "dreamsync_capture.monitor"

    @patch("dreamsync.capture.capture_process.subprocess.Popen")
    @patch("dreamsync.capture.capture_process.discover_audio_device")
    def test_start_with_explicit_device(self, mock_discover, mock_popen, manager):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stderr = io.BytesIO(b"")
        mock_popen.return_value = mock_proc

        manager.start(device_name="Test Device")

        mock_popen.assert_called_once()
        mock_discover.assert_not_called()
        assert manager.is_alive()

    @patch("dreamsync.capture.capture_process.subprocess.Popen")
    @patch("dreamsync.capture.capture_process.discover_audio_device")
    def test_start_with_discovery(self, mock_discover, mock_popen, manager):
        mock_discover.return_value = "CABLE Output (VB-Audio Virtual Cable)"
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stderr = io.BytesIO(b"")
        mock_popen.return_value = mock_proc

        manager.start()

        mock_discover.assert_called_once_with("CABLE Output")

    @patch("dreamsync.capture.capture_process.discover_audio_device")
    def test_start_raises_when_no_device(self, mock_discover, manager):
        mock_discover.return_value = None
        with pytest.raises(RuntimeError, match="No audio device"):
            manager.start()

    @patch("dreamsync.capture.capture_process.subprocess.Popen")
    @patch("dreamsync.capture.capture_process.discover_audio_device")
    def test_start_raises_when_already_running(self, mock_discover, mock_popen, manager):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stderr = io.BytesIO(b"")
        mock_popen.return_value = mock_proc

        manager.start(device_name="Dev")
        with pytest.raises(RuntimeError, match="already running"):
            manager.start(device_name="Dev")

    @patch("dreamsync.capture.capture_process.subprocess.Popen")
    def test_stop_terminates_cleanly(self, mock_popen, manager):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stderr = io.BytesIO(b"")
        mock_proc.returncode = 0
        mock_proc.wait.return_value = 0
        mock_popen.return_value = mock_proc

        manager.start(device_name="Dev")
        rc = manager.stop()

        mock_proc.terminate.assert_called_once()
        assert rc == 0
        assert not manager.is_alive()

    def test_stop_when_not_started(self, manager):
        assert manager.stop() is None

    @patch("dreamsync.capture.capture_process.subprocess.Popen")
    def test_is_alive_false_after_exit(self, mock_popen, manager):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1  # already exited
        mock_proc.stderr = io.BytesIO(b"")
        mock_popen.return_value = mock_proc

        manager.start(device_name="Dev")
        assert not manager.is_alive()

    @patch("dreamsync.capture.capture_process.subprocess.Popen")
    def test_restart(self, mock_popen, manager):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stderr = io.BytesIO(b"")
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        manager.start(device_name="Dev")
        manager.restart(device_name="Dev")

        assert mock_popen.call_count == 2
        mock_proc.terminate.assert_called_once()

    @patch("dreamsync.capture.capture_process.subprocess.Popen")
    def test_stdout_accessible(self, mock_popen, manager):
        mock_stdout = io.BytesIO(b"\x00" * 100)
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stdout = mock_stdout
        mock_proc.stderr = io.BytesIO(b"")
        mock_popen.return_value = mock_proc

        manager.start(device_name="Dev")
        assert manager.stdout is mock_stdout

    def test_stdout_raises_before_start(self, manager):
        with pytest.raises(RuntimeError, match="not started"):
            _ = manager.stdout

    @patch("dreamsync.capture.capture_process.subprocess.Popen")
    def test_force_kill_on_timeout(self, mock_popen, manager):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stderr = io.BytesIO(b"")
        mock_proc.returncode = -9
        mock_proc.wait.side_effect = [
            subprocess.TimeoutExpired(cmd="ffmpeg", timeout=5),
            None,
        ]
        mock_popen.return_value = mock_proc

        manager.start(device_name="Dev")
        rc = manager.stop()

        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_called_once()
