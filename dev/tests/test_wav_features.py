import unittest

import numpy as np

from dreamsync.dsp.features import extract_feature_frames


def _pulse_train(sr: int, seconds: float, bpm: float) -> np.ndarray:
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    period = 60.0 / bpm
    phase = (t % period) / period
    pulse = (phase < 0.05).astype(np.float32)
    return (pulse * 0.8).astype(np.float32)


def _mixed_pulse_train(sr: int, bpm_a: float, bpm_b: float, split_s: float, total_s: float) -> np.ndarray:
    a = _pulse_train(sr, split_s, bpm_a)
    b = _pulse_train(sr, total_s - split_s, bpm_b)
    return np.concatenate([a, b]).astype(np.float32)


class WavFeatureTests(unittest.TestCase):
    def test_bpm_estimate_from_pulse_train(self) -> None:
        sr = 44100
        bpm = 120.0
        signal = _pulse_train(sr, seconds=6.0, bpm=bpm)

        frames = extract_feature_frames(signal, sr, frame_size=2048, hop_size=512)
        self.assertTrue(frames)
        est = frames[0].bpm
        self.assertGreaterEqual(est, 110.0)
        self.assertLessEqual(est, 130.0)
        beat_count = sum(1 for f in frames if f.beat)
        self.assertGreaterEqual(beat_count, 6)

    def test_bpm_estimate_with_noise_is_stable(self) -> None:
        sr = 44100
        bpm = 128.0
        rng = np.random.default_rng(0)
        signal = _pulse_train(sr, seconds=8.0, bpm=bpm)
        noisy = (signal + 0.08 * rng.standard_normal(signal.shape)).astype(np.float32)

        frames = extract_feature_frames(noisy, sr, frame_size=2048, hop_size=512)
        self.assertTrue(frames)
        est = frames[0].bpm
        self.assertGreaterEqual(est, 118.0)
        self.assertLessEqual(est, 138.0)
        beat_count = sum(1 for f in frames if f.beat)
        self.assertGreaterEqual(beat_count, 10)

    def test_bpm_estimate_tracks_dominant_tempo_when_section_changes(self) -> None:
        sr = 44100
        signal = _mixed_pulse_train(sr, bpm_a=100.0, bpm_b=140.0, split_s=2.0, total_s=8.0)
        frames = extract_feature_frames(signal, sr, frame_size=2048, hop_size=512)
        self.assertTrue(frames)
        est = frames[0].bpm
        # The second section is longer, so the global estimate should lean higher.
        self.assertGreaterEqual(est, 115.0)
        self.assertLessEqual(est, 150.0)
