import unittest

import numpy as np

from dreamsync.dsp.features import _estimate_bpm, extract_feature_frames


class DspFeaturesEdgeCaseTests(unittest.TestCase):
    def test_estimate_bpm_with_zero_hop_size_returns_zero(self) -> None:
        onset = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        bpm, confidence = _estimate_bpm(onset, hop_size=0, sr=44100)
        self.assertEqual(bpm, 0.0)

    def test_estimate_bpm_with_zero_sample_rate_returns_zero(self) -> None:
        onset = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        bpm, confidence = _estimate_bpm(onset, hop_size=512, sr=0)
        self.assertEqual(bpm, 0.0)

    def test_extract_feature_frames_rejects_zero_hop_size(self) -> None:
        signal = np.ones(1000, dtype=np.float32)
        with self.assertRaises(ValueError) as ctx:
            extract_feature_frames(signal, sr=44100, hop_size=0)
        self.assertIn("hop_size", str(ctx.exception).lower())

    def test_extract_feature_frames_rejects_zero_frame_size(self) -> None:
        signal = np.ones(1000, dtype=np.float32)
        with self.assertRaises(ValueError) as ctx:
            extract_feature_frames(signal, sr=44100, frame_size=0)
        self.assertIn("frame_size", str(ctx.exception).lower())

    def test_extract_feature_frames_rejects_negative_sample_rate(self) -> None:
        signal = np.ones(1000, dtype=np.float32)
        with self.assertRaises(ValueError) as ctx:
            extract_feature_frames(signal, sr=-44100)
        self.assertIn("sr", str(ctx.exception).lower())
