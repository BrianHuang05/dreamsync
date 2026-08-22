from unittest.mock import patch

from dreamsync.capture.ffmpeg_device import PulseSource
from dreamsync.gui.services.audio_device_service import AudioDeviceService


def test_linux_input_options_include_named_pipewire_sources():
    with (
        patch(
            "dreamsync.gui.services.audio_device_service.list_input_devices",
            return_value=[
                {
                    "id": 2,
                    "name": "pulse",
                    "hostapi": "ALSA",
                    "max_input_channels": 2,
                    "default_samplerate": 48_000,
                }
            ],
        ),
        patch(
            "dreamsync.gui.services.audio_device_service.list_pulse_sources",
            return_value=[
                PulseSource(
                    index=42,
                    name="alsa_input.surface_mic",
                    channels=2,
                    sample_rate=48_000,
                ),
                PulseSource(
                    index=43,
                    name="dreamsync_queue_capture.monitor",
                    channels=2,
                    sample_rate=48_000,
                ),
            ],
        ),
        patch("dreamsync.gui.services.audio_device_service.sys.platform", "linux"),
    ):
        service = AudioDeviceService()
        options = service.list_input_options()
        capture_sources = service.list_capture_source_names()

    source = options[-1]
    assert source.id == "pulse-source:dreamsync_queue_capture.monitor"
    assert source.hostapi == "PipeWire/Pulse"
    assert source.channel_count == 2
    assert capture_sources == ("dreamsync_queue_capture.monitor",)
