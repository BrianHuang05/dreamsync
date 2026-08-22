"""Tests for dreamsync.capture.ffmpeg_device — FFmpeg device discovery."""

from unittest.mock import patch, MagicMock
import subprocess

import pytest

from dreamsync.capture.ffmpeg_device import (
    CaptureBackend, CaptureDiscoveryError, discover_audio_device,
    discover_capture_device, parse_pulse_sources, select_pulse_source, _parse_device_list,
)


# ---------------------------------------------------------------------------
# Sample FFmpeg stderr outputs for testing
# ---------------------------------------------------------------------------

STDERR_SINGLE_DEVICE = """\
[dshow @ 000001] DirectShow video devices (some may be both video and audio devices)
[dshow @ 000001]  "Integrated Webcam"
[dshow @ 000001] DirectShow audio devices
[dshow @ 000001]  "CABLE Output (VB-Audio Virtual Cable)"
dummy: Immediate exit requested
"""

STDERR_MULTIPLE_DEVICES = """\
[dshow @ 000001] DirectShow video devices (some may be both video and audio devices)
[dshow @ 000001]  "Integrated Webcam"
[dshow @ 000001] DirectShow audio devices
[dshow @ 000001]  "Microphone (Realtek(R) Audio)"
[dshow @ 000001]  "CABLE Output (VB-Audio Virtual Cable)"
[dshow @ 000001]  "Stereo Mix (Realtek(R) Audio)"
dummy: Immediate exit requested
"""

STDERR_NO_CABLE = """\
[dshow @ 000001] DirectShow video devices (some may be both video and audio devices)
[dshow @ 000001]  "Integrated Webcam"
[dshow @ 000001] DirectShow audio devices
[dshow @ 000001]  "Microphone (Realtek(R) Audio)"
[dshow @ 000001]  "Stereo Mix (Realtek(R) Audio)"
dummy: Immediate exit requested
"""

STDERR_EMPTY = """\
[dshow @ 000001] DirectShow video devices (some may be both video and audio devices)
[dshow @ 000001] DirectShow audio devices
dummy: Immediate exit requested
"""

STDERR_FFMPEG_8_FLAT = """\
[in#0 @ 000001] "Integrated Webcam" (video)
[in#0 @ 000001]   Alternative name "@device_pnp_camera"
[in#0 @ 000001] "CABLE Output (VB-Audio Virtual Cable)" (audio)
[in#0 @ 000001]   Alternative name "@device_cm_cable"
[in#0 @ 000001] "Microphone Array (Intel Audio)" (audio)
Error opening input file dummy.
"""


# ---------------------------------------------------------------------------
# _parse_device_list unit tests
# ---------------------------------------------------------------------------

class TestParseDeviceList:
    def test_finds_cable_output_single(self):
        result = _parse_device_list(STDERR_SINGLE_DEVICE, "CABLE Output")
        assert result == "CABLE Output (VB-Audio Virtual Cable)"

    def test_finds_cable_output_among_multiple(self):
        result = _parse_device_list(STDERR_MULTIPLE_DEVICES, "CABLE Output")
        assert result == "CABLE Output (VB-Audio Virtual Cable)"

    def test_returns_none_when_no_match(self):
        result = _parse_device_list(STDERR_NO_CABLE, "CABLE Output")
        assert result is None

    def test_returns_none_for_empty_audio_section(self):
        result = _parse_device_list(STDERR_EMPTY, "CABLE Output")
        assert result is None

    def test_case_insensitive_pattern(self):
        result = _parse_device_list(STDERR_SINGLE_DEVICE, "cable output")
        assert result == "CABLE Output (VB-Audio Virtual Cable)"

    def test_partial_pattern_match(self):
        result = _parse_device_list(STDERR_MULTIPLE_DEVICES, "VB-Audio")
        assert result == "CABLE Output (VB-Audio Virtual Cable)"

    def test_alternative_pattern(self):
        result = _parse_device_list(STDERR_MULTIPLE_DEVICES, "Realtek")
        assert result == "Microphone (Realtek(R) Audio)"

    def test_returns_none_for_empty_stderr(self):
        result = _parse_device_list("", "CABLE Output")
        assert result is None

    def test_finds_cable_output_in_ffmpeg_8_flat_listing(self):
        result = _parse_device_list(STDERR_FFMPEG_8_FLAT, "CABLE Output")
        assert result == "CABLE Output (VB-Audio Virtual Cable)"


# ---------------------------------------------------------------------------
# discover_audio_device integration tests (mocked subprocess)
# ---------------------------------------------------------------------------

class TestDiscoverAudioDevice:
    @patch("dreamsync.capture.ffmpeg_device.resolve_ffmpeg", return_value="ffmpeg")
    @patch("dreamsync.capture.ffmpeg_device.subprocess.run")
    def test_returns_device_name(self, mock_run, _mock_resolve):
        mock_run.return_value = MagicMock(
            stderr=STDERR_MULTIPLE_DEVICES,
            returncode=1,  # ffmpeg exits non-zero with -i dummy
        )
        result = discover_audio_device("CABLE Output")
        assert result == "CABLE Output (VB-Audio Virtual Cable)"
        mock_run.assert_called_once()

    @patch("dreamsync.capture.ffmpeg_device.resolve_ffmpeg", return_value="ffmpeg")
    @patch("dreamsync.capture.ffmpeg_device.subprocess.run")
    def test_returns_none_when_not_found(self, mock_run, _mock_resolve):
        mock_run.return_value = MagicMock(
            stderr=STDERR_NO_CABLE, returncode=1,
        )
        result = discover_audio_device("CABLE Output")
        assert result is None

    @patch("dreamsync.capture.ffmpeg_device.resolve_ffmpeg", return_value=None)
    @patch("dreamsync.capture.ffmpeg_device.subprocess.run")
    def test_handles_ffmpeg_not_found(self, mock_run, _mock_resolve):
        result = discover_audio_device()
        assert result is None
        mock_run.assert_not_called()

    @patch("dreamsync.capture.ffmpeg_device.resolve_ffmpeg", return_value="ffmpeg")
    @patch("dreamsync.capture.ffmpeg_device.subprocess.run")
    def test_handles_timeout(self, mock_run, _mock_resolve):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ffmpeg", timeout=10)
        result = discover_audio_device()
        assert result is None

    @patch("dreamsync.capture.ffmpeg_device.resolve_ffmpeg", return_value="ffmpeg")
    @patch("dreamsync.capture.ffmpeg_device.subprocess.run")
    def test_custom_pattern(self, mock_run, _mock_resolve):
        mock_run.return_value = MagicMock(
            stderr=STDERR_MULTIPLE_DEVICES, returncode=1,
        )
        result = discover_audio_device("Stereo Mix")
        assert result == "Stereo Mix (Realtek(R) Audio)"


PULSE_SOURCES = """\
42\tdreamsync_capture.monitor\tPipeWire\ts16le 2ch 48000Hz\tIDLE
43\talsa_input.pci-0000_00_1f.3.analog-stereo\tPipeWire\ts16le 2ch 48000Hz\tRUNNING
"""


class TestPulseSources:
    def test_parser_preserves_source_details(self):
        sources = parse_pulse_sources(PULSE_SOURCES)
        assert sources[0].name == "dreamsync_capture.monitor"
        assert sources[0].sample_rate == 48000
        assert sources[0].channels == 2
        assert sources[0].state == "IDLE"
        assert sources[0].is_monitor

    def test_parser_ignores_malformed_rows(self):
        assert len(parse_pulse_sources("bad row\nnot-an-index\tname\n" + PULSE_SOURCES)) == 2

    def test_exact_match_wins_over_substring(self):
        source, rule = select_pulse_source(parse_pulse_sources(PULSE_SOURCES), "dreamsync_capture.monitor")
        assert source.name == "dreamsync_capture.monitor"
        assert rule == "exact"

    def test_ambiguous_match_is_descriptive(self):
        sources = parse_pulse_sources(PULSE_SOURCES + "44\tdreamsync_capture.other\tPipeWire\ts16le 2ch 48000Hz\tIDLE\n")
        with pytest.raises(CaptureDiscoveryError, match="ambiguous"):
            select_pulse_source(sources, "dreamsync_capture")

    @patch("dreamsync.capture.ffmpeg_device.list_pulse_sources")
    def test_linux_resolution_records_pulse_backend(self, mock_list):
        mock_list.return_value = parse_pulse_sources(PULSE_SOURCES)
        device = discover_capture_device("dreamsync_capture.monitor", platform="linux")
        assert device.backend is CaptureBackend.PULSE
        assert device.sample_rate == 48000
