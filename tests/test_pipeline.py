import unittest
from unittest.mock import patch

import numpy as np

from dreamsync import pipeline
from dreamsync.audio.system_input import CaptureProgress, CaptureStats
from dreamsync.pipeline import extract_signal_features_to_stream


def _pulse_train(sr: int, seconds: float, bpm: float) -> np.ndarray:
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    period = 60.0 / bpm
    phase = (t % period) / period
    pulse = (phase < 0.05).astype(np.float32)
    return (pulse * 0.8).astype(np.float32)


class PipelineTests(unittest.TestCase):
    def test_extract_signal_features_to_stream(self) -> None:
        sr = 44100
        signal = _pulse_train(sr, seconds=3.0, bpm=120.0)
        stream = extract_signal_features_to_stream(signal, sample_rate=sr)
        self.assertTrue(stream)
        first = stream[0]
        for key in ("t", "rms", "zcr", "centroid", "bass", "beat", "bpm"):
            self.assertIn(key, first)

    def test_capture_pipeline_emits_telemetry_rows(self) -> None:
        signal = _pulse_train(48000, seconds=2.0, bpm=120.0)

        def _fake_capture(*args, **kwargs):
            callback = kwargs.get("progress_callback")
            if callback:
                callback(CaptureProgress(elapsed_seconds=1.0, samples_captured=48000, dropped_blocks=0))
                callback(CaptureProgress(elapsed_seconds=2.0, samples_captured=96000, dropped_blocks=0))
            return (
                signal,
                CaptureStats(sample_rate=48000, channels=1, duration_seconds=2.0, dropped_blocks=0),
            )

        with patch.object(pipeline, "capture_mono_audio", side_effect=_fake_capture):
            stream, meta, telemetry = pipeline.capture_system_input_features_to_stream(
                duration_seconds=2.0,
                sample_rate=48000,
                channels=1,
                telemetry_interval_seconds=1.0,
            )

        self.assertTrue(stream)
        self.assertEqual(meta["sample_rate"], 48000)
        self.assertEqual(meta["dropped_blocks"], 0)
        self.assertEqual(len(telemetry), 2)
        self.assertEqual(telemetry[0]["kind"], "telemetry")
