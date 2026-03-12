"""Tests for interactive audio device picker and helpers."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from dreamsync.audio.system_input import (
    format_device_table,
    is_capture_device,
    pick_output_device,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FAKE_DEVICES = [
    {
        "id": 3,
        "name": "Speakers (Realtek High Definition Audio)",
        "hostapi": "WASAPI",
        "max_output_channels": 2,
        "default_samplerate": 48000.0,
    },
    {
        "id": 5,
        "name": "Headphones (Realtek High Definition Audio)",
        "hostapi": "WASAPI",
        "max_output_channels": 2,
        "default_samplerate": 48000.0,
    },
    {
        "id": 7,
        "name": "AirPods Pro (Brian)",
        "hostapi": "WASAPI",
        "max_output_channels": 2,
        "default_samplerate": 44100.0,
    },
    {
        "id": 9,
        "name": "CABLE Input (VB-Audio Virtual Cable)",
        "hostapi": "WASAPI",
        "max_output_channels": 2,
        "default_samplerate": 44100.0,
    },
]


# ===========================================================================
# Step 1: is_capture_device tests
# ===========================================================================


class TestIsCaptureDevice:
    def test_cable_input(self):
        assert is_capture_device("CABLE Input (VB-Audio Virtual Cable)") is True

    def test_speakers(self):
        assert is_capture_device("Speakers (Realtek)") is False

    def test_case_insensitive(self):
        assert is_capture_device("cable INPUT") is True

    def test_custom_keywords(self):
        assert is_capture_device("My Loopback", keywords=("loopback",)) is True
        assert is_capture_device("My Loopback", keywords=("cable input",)) is False


# ===========================================================================
# Step 1: format_device_table tests
# ===========================================================================


class TestFormatDeviceTable:
    def test_basic(self):
        table = format_device_table(FAKE_DEVICES[:2], kind="output")
        lines = table.split("\n")
        # header separator + header + separator + 2 data rows + trailing separator = 6
        assert len(lines) == 6
        assert "Speakers" in table
        assert "Headphones" in table

    def test_empty(self):
        table = format_device_table([], kind="output")
        assert "No output devices found" in table

    def test_marks_capture_devices(self):
        table = format_device_table(FAKE_DEVICES, kind="output", mark_capture=True)
        assert "\u26a0 capture" in table
        # Only CABLE Input should be marked
        lines_with_marker = [l for l in table.split("\n") if "\u26a0" in l]
        assert len(lines_with_marker) == 1
        assert "CABLE Input" in lines_with_marker[0]

    def test_no_mark_when_disabled(self):
        table = format_device_table(FAKE_DEVICES, kind="output", mark_capture=False)
        assert "\u26a0" not in table


# ===========================================================================
# Step 2: pick_output_device tests
# ===========================================================================


class TestPickOutputDevice:
    @patch("dreamsync.audio.system_input.list_output_devices", return_value=FAKE_DEVICES)
    @patch("builtins.input", return_value="1")
    def test_returns_device_id(self, mock_input, mock_devices):
        assert pick_output_device() == 3  # first device ID

    @patch("dreamsync.audio.system_input.list_output_devices", return_value=FAKE_DEVICES)
    @patch("builtins.input", return_value="3")
    def test_last_device(self, mock_input, mock_devices):
        assert pick_output_device() == 7  # third device ID

    @patch("dreamsync.audio.system_input.list_output_devices", return_value=FAKE_DEVICES)
    @patch("builtins.input", side_effect=["abc", "0", "2"])
    def test_invalid_then_valid(self, mock_input, mock_devices):
        assert pick_output_device() == 5  # second device ID

    @patch("dreamsync.audio.system_input.list_output_devices", return_value=[])
    def test_no_devices_raises(self, mock_devices):
        with pytest.raises(RuntimeError, match="No audio output devices"):
            pick_output_device()

    @patch("dreamsync.audio.system_input.list_output_devices", return_value=FAKE_DEVICES)
    @patch("builtins.input", side_effect=["4", "y"])
    def test_capture_device_warned_then_confirmed(self, mock_input, mock_devices, capsys):
        result = pick_output_device()
        assert result == 9
        captured = capsys.readouterr().out
        assert "capture device" in captured or "\u26a0" in mock_input.call_args_list[1][0][0]

    @patch("dreamsync.audio.system_input.list_output_devices", return_value=FAKE_DEVICES)
    @patch("builtins.input", side_effect=["4", "n", "1"])
    def test_capture_device_warned_then_rejected(self, mock_input, mock_devices):
        result = pick_output_device()
        assert result == 3  # fell back to first device

    @patch("dreamsync.audio.system_input.list_output_devices", return_value=FAKE_DEVICES)
    @patch("builtins.input", side_effect=EOFError)
    def test_eof_raises_keyboard_interrupt(self, mock_input, mock_devices):
        with pytest.raises(KeyboardInterrupt):
            pick_output_device()
