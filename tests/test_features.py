import unittest

import numpy as np

from dreamsync.live import _feature_row_from_frame


class FeatureRowFromFrameTests(unittest.TestCase):
    """Tests for _feature_row_from_frame (Phase 1 live feature extraction)."""

    def _make_frame(self, freq: float = 440.0, sr: int = 44100, size: int = 2048,
                    amplitude: float = 0.5) -> np.ndarray:
        """Generate a sine-wave frame for testing."""
        t = np.arange(size, dtype=np.float32) / sr
        return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)

    def test_returns_all_expected_keys(self) -> None:
        frame = self._make_frame()
        features = _feature_row_from_frame(frame, rms=0.1, t=1.0, bpm=120.0, beat=True)
        expected_keys = {"t", "rms", "zcr", "centroid", "bass", "beat", "bpm"}
        self.assertEqual(set(features.keys()), expected_keys)

    def test_rms_passthrough(self) -> None:
        frame = self._make_frame()
        features = _feature_row_from_frame(frame, rms=0.42, t=0.0, bpm=100.0, beat=False)
        self.assertAlmostEqual(features["rms"], 0.42)

    def test_rms_non_negative(self) -> None:
        frame = self._make_frame()
        for rms_val in [0.0, 0.01, 0.5, 1.0]:
            features = _feature_row_from_frame(frame, rms=rms_val, t=0.0, bpm=120.0, beat=False)
            self.assertGreaterEqual(features["rms"], 0.0)

    def test_zcr_in_unit_range(self) -> None:
        frame = self._make_frame()
        features = _feature_row_from_frame(frame, rms=0.1, t=0.0, bpm=120.0, beat=False)
        self.assertGreaterEqual(features["zcr"], 0.0)
        self.assertLessEqual(features["zcr"], 1.0)

    def test_zcr_higher_for_noisy_signal(self) -> None:
        """White noise should have higher ZCR than a low-frequency sine."""
        rng = np.random.RandomState(42)
        noise = rng.randn(2048).astype(np.float32)
        sine = self._make_frame(freq=100.0)
        f_noise = _feature_row_from_frame(noise, rms=0.5, t=0.0, bpm=120.0, beat=False)
        f_sine = _feature_row_from_frame(sine, rms=0.5, t=0.0, bpm=120.0, beat=False)
        self.assertGreater(f_noise["zcr"], f_sine["zcr"])

    def test_bass_defaults_to_zero(self) -> None:
        frame = self._make_frame()
        features = _feature_row_from_frame(frame, rms=0.1, t=0.0, bpm=120.0, beat=False)
        self.assertEqual(features["bass"], 0.0)

    def test_bass_passed_through(self) -> None:
        frame = self._make_frame()
        features = _feature_row_from_frame(frame, rms=0.1, t=0.0, bpm=120.0, beat=False, bass=3.14)
        self.assertAlmostEqual(features["bass"], 3.14)

    def test_beat_is_bool(self) -> None:
        frame = self._make_frame()
        features_beat = _feature_row_from_frame(frame, rms=0.1, t=0.0, bpm=120.0, beat=True)
        features_no_beat = _feature_row_from_frame(frame, rms=0.1, t=0.0, bpm=120.0, beat=False)
        self.assertIs(features_beat["beat"], True)
        self.assertIs(features_no_beat["beat"], False)

    def test_bpm_is_float(self) -> None:
        frame = self._make_frame()
        features = _feature_row_from_frame(frame, rms=0.1, t=0.0, bpm=128, beat=False)
        self.assertIsInstance(features["bpm"], float)
        self.assertEqual(features["bpm"], 128.0)

    def test_t_passthrough(self) -> None:
        frame = self._make_frame()
        features = _feature_row_from_frame(frame, rms=0.1, t=5.5, bpm=120.0, beat=False)
        self.assertEqual(features["t"], 5.5)

    def test_silent_frame_has_zero_zcr(self) -> None:
        """A completely silent frame should have ZCR of 0."""
        frame = np.zeros(2048, dtype=np.float32)
        features = _feature_row_from_frame(frame, rms=0.0, t=0.0, bpm=0.0, beat=False)
        self.assertEqual(features["zcr"], 0.0)


if __name__ == "__main__":
    unittest.main()
