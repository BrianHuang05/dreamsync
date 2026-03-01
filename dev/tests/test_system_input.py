import unittest
from unittest.mock import patch

import numpy as np

from dreamsync.audio.system_input import capture_mono_audio


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
