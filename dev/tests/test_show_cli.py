"""Tests for CLI `play` subcommand (D5.4)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dreamsync.cli import build_parser, main
from dreamsync.show.models import ShowCue, ShowTimeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_show_file(tmp_path: Path) -> Path:
    """Write a minimal show timeline JSON to tmp_path and return its path."""
    cues = (
        ShowCue(0.0, "breathe", ("#ff0000",), 0.3, 0.2, {}, "cut", 0),
        ShowCue(5.0, "scroll", ("#00ff00",), 0.6, 0.5, {}, "fade", 2),
    )
    tl = ShowTimeline(
        song_path="test.mp3",
        duration=10.0,
        bpm=120.0,
        time_signature=4,
        beat_times=tuple(i * 0.5 for i in range(20)),
        downbeat_times=tuple(i * 2.0 for i in range(5)),
        cues=cues,
        metadata={"track_name": "Test"},
    )
    show_path = tmp_path / "show.json"
    tl.to_json(show_path)
    return show_path


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

class TestPlayArgParsing:
    def test_play_show_is_optional(self):
        parser = build_parser()
        # --show is now optional (D7.3); should NOT raise
        args = parser.parse_args(["play", "song.mp3", "--config", "dev.yaml"])
        assert args.show is None

    def test_play_requires_config(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["play", "song.mp3", "--show", "show.json"])

    def test_play_parses_all_args(self):
        parser = build_parser()
        args = parser.parse_args([
            "play", "song.mp3",
            "--show", "show.json",
            "--config", "devices.yaml",
            "--sample-rate", "48000",
            "--audio-device", "3",
            "--fps", "60",
            "--brightness", "0.8",
            "--no-mirror",
            "--debug",
        ])
        assert args.command == "play"
        assert args.audio_path == Path("song.mp3")
        assert args.show == Path("show.json")
        assert args.config == Path("devices.yaml")
        assert args.sample_rate == 48000
        assert args.audio_device == 3
        assert args.fps == 60
        assert args.brightness == 0.8
        assert args.mirror is False
        assert args.debug is True

    def test_play_defaults(self):
        parser = build_parser()
        args = parser.parse_args([
            "play", "song.mp3", "--show", "show.json", "--config", "dev.yaml",
        ])
        assert args.sample_rate == 44100
        assert args.audio_device is None
        assert args.fps == 30
        assert args.brightness == 1.0
        assert args.mirror is True
        assert args.debug is False


# ---------------------------------------------------------------------------
# Command handler
# ---------------------------------------------------------------------------

class TestPlayCommand:
    def test_missing_mp3_returns_error(self, tmp_path: Path, capsys):
        show = _make_show_file(tmp_path)
        config = tmp_path / "devices.yaml"
        config.write_text("devices: []")
        ret = main(["play", str(tmp_path / "nope.mp3"), "--show", str(show), "--config", str(config)])
        assert ret == 1
        assert "not found" in capsys.readouterr().out

    def test_missing_show_returns_error(self, tmp_path: Path, capsys):
        mp3 = tmp_path / "test.mp3"
        mp3.write_bytes(b"\x00")
        config = tmp_path / "devices.yaml"
        config.write_text("devices: []")
        ret = main(["play", str(mp3), "--show", str(tmp_path / "nope.json"), "--config", str(config)])
        assert ret == 1
        assert "not found" in capsys.readouterr().out

    @patch("dreamsync.show.runtime.run_show_playback")
    @patch("dreamsync.output.auto_detect.build_multi_adapter")
    @patch("dreamsync.output.auto_detect.detect_all_devices")
    @patch("dreamsync.output.auto_detect.load_device_config")
    def test_play_calls_run_show_playback(
        self, mock_load, mock_detect, mock_build, mock_run, tmp_path: Path,
    ):
        mp3 = tmp_path / "test.mp3"
        mp3.write_bytes(b"\x00")
        show = _make_show_file(tmp_path)
        config = tmp_path / "devices.yaml"
        config.write_text("devices: []")

        mock_load.return_value = []
        mock_detect.return_value = []
        mock_adapter = MagicMock()
        mock_build.return_value = mock_adapter
        mock_run.return_value = {"duration": 10.0, "frames_sent": 100}

        ret = main(["play", str(mp3), "--show", str(show), "--config", str(config)])
        assert ret == 0
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args
        assert call_kwargs[0][0] == mp3  # mp3_path
        assert call_kwargs[0][1] == show  # show_path
