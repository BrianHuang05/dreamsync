"""Unit tests for SpectralBeatTemplate."""

import unittest

import numpy as np

from dreamsync.live import SpectralBeatTemplate


class TestSpectralBeatTemplate(unittest.TestCase):

    def _make_beat_spectrum(self, n_bins: int = 1025, seed: int = 42) -> np.ndarray:
        """Create a consistent 'beat-like' spectrum for testing."""
        rng = np.random.default_rng(seed)
        # Strong low-frequency content (typical beat)
        mag = np.zeros(n_bins, dtype=np.float32)
        mag[:50] = rng.uniform(5.0, 10.0, 50).astype(np.float32)
        mag[50:200] = rng.uniform(1.0, 3.0, 150).astype(np.float32)
        return mag

    # All tests pass frame_energy=1.0 to satisfy the energy gate.

    def test_bootstrap_returns_zero(self) -> None:
        """update() returns 0.0 until min_beats spectra collected."""
        tmpl = SpectralBeatTemplate(min_beats_for_template=4)
        mag = self._make_beat_spectrum()
        # Feed fewer than min_beats beat frames
        for _ in range(3):
            sim = tmpl.update(mag, is_beat=True, frame_energy=1.0)
            self.assertEqual(sim, 0.0)
        # Non-beat frames also return 0 during bootstrap
        sim = tmpl.update(mag, is_beat=False, frame_energy=1.0)
        self.assertEqual(sim, 0.0)

    def test_template_builds_after_min_beats(self) -> None:
        """.ready becomes True after feeding min_beats beat frames."""
        tmpl = SpectralBeatTemplate(min_beats_for_template=4)
        mag = self._make_beat_spectrum()
        self.assertFalse(tmpl.ready)
        for _ in range(4):
            tmpl.update(mag, is_beat=True, frame_energy=1.0)
        self.assertTrue(tmpl.ready)

    def test_identical_spectrum_high_similarity(self) -> None:
        """Feeding the same spectrum as training gives similarity > 0.9."""
        tmpl = SpectralBeatTemplate(min_beats_for_template=4)
        mag = self._make_beat_spectrum()
        # Bootstrap
        for _ in range(4):
            tmpl.update(mag, is_beat=True, frame_energy=1.0)
        # Now score the same spectrum
        sim = tmpl.update(mag, is_beat=False, frame_energy=1.0)
        self.assertGreater(sim, 0.9)

    def test_random_noise_low_similarity(self) -> None:
        """Random noise spectrum gives similarity < 0.5."""
        tmpl = SpectralBeatTemplate(min_beats_for_template=4)
        mag = self._make_beat_spectrum()
        # Bootstrap with beat spectrum
        for _ in range(4):
            tmpl.update(mag, is_beat=True, frame_energy=1.0)
        # Score random noise
        rng = np.random.default_rng(99)
        noise = rng.uniform(0, 1, mag.shape[0]).astype(np.float32)
        sim = tmpl.update(noise, is_beat=False, frame_energy=1.0)
        self.assertLess(sim, 0.5)

    def test_template_adapts(self) -> None:
        """After many adapted beats with shifted spectrum, similarity stays high."""
        tmpl = SpectralBeatTemplate(min_beats_for_template=4, ema_alpha=0.15)
        mag = self._make_beat_spectrum()
        # Bootstrap
        for _ in range(4):
            tmpl.update(mag, is_beat=True, frame_energy=1.0)

        # Gradually shift spectrum (simulate timbral change)
        shifted = mag.copy()
        shifted[:50] *= 0.5
        shifted[200:400] = 3.0

        # Feed many adapted beat frames with the shifted spectrum
        for _ in range(50):
            sim = tmpl.update(shifted, is_beat=True, frame_energy=1.0)

        # After adaptation, the shifted spectrum should score high
        final_sim = tmpl.update(shifted, is_beat=False, frame_energy=1.0)
        self.assertGreater(final_sim, 0.7)

    def test_reset_clears_template(self) -> None:
        """After .reset(), .ready is False and similarity returns 0.0."""
        tmpl = SpectralBeatTemplate(min_beats_for_template=4)
        mag = self._make_beat_spectrum()
        # Bootstrap and confirm ready
        for _ in range(4):
            tmpl.update(mag, is_beat=True, frame_energy=1.0)
        self.assertTrue(tmpl.ready)

        tmpl.reset()
        self.assertFalse(tmpl.ready)
        sim = tmpl.update(mag, is_beat=False, frame_energy=1.0)
        self.assertEqual(sim, 0.0)


if __name__ == "__main__":
    unittest.main()
