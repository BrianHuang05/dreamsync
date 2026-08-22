import os
import unittest
from unittest.mock import patch

import numpy as np

from dreamsync.audio.system_input import (
    capture_mono_audio,
    open_input_stream,
    pulse_source_device_id,
)


class _FakeStatus:
    pass


class _FakeInputStream:
    def __init__(self, *_, **kwargs) -> None:
        self._callback = kwargs["callback"]

    def __enter__(self):
        data = np.zeros((8, 1), dtype=np.float32)
        self._callback(data, data.shape[0], None, _FakeStatus())
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class _FakeSoundDevice:
    InputStream = _FakeInputStream


class _PulseFakeSoundDevice:
    InputStream = _FakeInputStream

    @staticmethod
    def query_devices():
        return [
            {"name": "default", "hostapi": 0, "max_input_channels": 2},
            {"name": "pulse", "hostapi": 0, "max_input_channels": 2},
        ]

    @staticmethod
    def query_hostapis():
        return [{"name": "ALSA"}]


class SystemInputTests(unittest.TestCase):
    def test_missing_input_overflow_does_not_count_as_drop(self) -> None:
        def _fake_monotonic() -> float:
            _fake_monotonic.t += 0.2
            return _fake_monotonic.t

        _fake_monotonic.t = 0.0

        with (
            patch("dreamsync.audio.system_input._require_sounddevice", return_value=_FakeSoundDevice()),
            patch("dreamsync.audio.system_input.time.monotonic", side_effect=_fake_monotonic),
            patch("dreamsync.audio.system_input.time.sleep"),
        ):
            _, stats = capture_mono_audio(duration_seconds=0.1, sample_rate=44100, channels=1)

        self.assertEqual(stats.dropped_blocks, 0)

    def test_named_pulse_source_uses_pulse_alsa_endpoint(self) -> None:
        source = pulse_source_device_id("alsa_input.surface_mic")
        original = os.environ.pop("PULSE_SOURCE", None)
        try:
            with open_input_stream(
                _PulseFakeSoundDevice(),
                device=source,
                samplerate=48_000,
                channels=1,
                callback=lambda *_: None,
            ):
                self.assertEqual(os.environ["PULSE_SOURCE"], "alsa_input.surface_mic")
            self.assertNotIn("PULSE_SOURCE", os.environ)
        finally:
            if original is not None:
                os.environ["PULSE_SOURCE"] = original
