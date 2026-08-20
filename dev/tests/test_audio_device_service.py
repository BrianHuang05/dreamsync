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
                )
            ],
        ),
        patch("dreamsync.gui.services.audio_device_service.sys.platform", "linux"),
    ):
        options = AudioDeviceService().list_input_options()

    source = options[-1]
    assert source.id == "pulse-source:alsa_input.surface_mic"
    assert source.hostapi == "PipeWire/Pulse"
    assert source.channel_count == 2
