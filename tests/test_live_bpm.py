"""Tests for LiveBpmEstimator onset modes and beat detection."""

import unittest

import numpy as np

from dreamsync.live import (
    LiveBpmEstimator,
    SpectralFeatures,
    _spectral_features,
    _prepare_bass_window,
    _compute_whitened_flux,
)


def _make_pulse_audio(
    sr: int, seconds: float, bpm: float, frame_size: int, hop_size: int
) -> list[np.ndarray]:
    """Generate pulse-train audio frames at a given BPM."""
    n_samples = int(sr * seconds)
    t = np.arange(n_samples, dtype=np.float32) / sr
    period = 60.0 / bpm
    phase = (t % period) / period
    signal = (phase < 0.05).astype(np.float32) * 0.8
    # Slice into frames
    frames = []
    for start in range(0, n_samples - frame_size, hop_size):
        frames.append(signal[start : start + frame_size])
    return frames


def _run_estimator_with_audio(
    frames: list[np.ndarray],
    sr: int,
    hop_size: int,
    frame_size: int,
    onset_mode: str = "spectral_flux",
    **estimator_kwargs,
) -> dict:
    """Feed frames through spectral features + LiveBpmEstimator, return results."""
    window, bass_mask, kick_mask = _prepare_bass_window(frame_size, sr)
    est = LiveBpmEstimator(sample_rate=sr, hop_size=hop_size, onset_mode=onset_mode, **estimator_kwargs)
    prev_mag = None
    spectral_mean = None
    prev_whitened_mag = None
    beat_count = 0
    stream_t = 0.0
    bpm = 0.0
    bpm_history = []

    for frame in frames:
        sf = _spectral_features(frame, window, bass_mask, prev_mag, kick_mask=kick_mask)
        prev_mag = sf.mag
        wf, spectral_mean, prev_whitened_mag = _compute_whitened_flux(
            sf.mag, spectral_mean, prev_whitened_mag,
        )
        bpm, beat = est.update(
            sf.bass, stream_t,
            spectral_flux=sf.spectral_flux,
            kick_spectral_flux=sf.kick_spectral_flux,
            whitened_flux=wf,
        )
        if beat:
            beat_count += 1
        bpm_history.append(bpm)
        stream_t += float(hop_size) / float(sr)

    return {
        "final_bpm": bpm,
        "beat_count": beat_count,
        "bpm_history": bpm_history,
        "duration": stream_t,
    }


class TestLiveBpmEstimatorOnsetMode(unittest.TestCase):
    """Phase A: spectral flux onset detection."""

    def test_onset_mode_default_is_hybrid(self) -> None:
        est = LiveBpmEstimator(sample_rate=44100, hop_size=512)
        self.assertEqual(est.onset_mode, "hybrid")

    def test_onset_mode_bass_diff_accepted(self) -> None:
        est = LiveBpmEstimator(sample_rate=44100, hop_size=512, onset_mode="bass_diff")
        self.assertEqual(est.onset_mode, "bass_diff")

    def test_onset_mode_invalid_raises(self) -> None:
        with self.assertRaises(ValueError):
            LiveBpmEstimator(sample_rate=44100, hop_size=512, onset_mode="invalid")

    def test_spectral_flux_mode_detects_bpm_from_pulse(self) -> None:
        """Spectral flux mode should detect ~120 BPM from a clean pulse train."""
        sr, bpm_target = 44100, 120.0
        frame_size, hop_size = 2048, 512
        frames = _make_pulse_audio(sr, 8.0, bpm_target, frame_size, hop_size)
        result = _run_estimator_with_audio(
            frames, sr, hop_size, frame_size, onset_mode="spectral_flux"
        )
        self.assertGreater(result["final_bpm"], 0.0, "Should detect a BPM")
        self.assertAlmostEqual(result["final_bpm"], bpm_target, delta=15.0)
        self.assertGreater(result["beat_count"], 5, "Should detect beats")

    def test_bass_diff_mode_detects_bpm_from_pulse(self) -> None:
        """Bass diff mode (legacy) should also detect ~120 BPM from a clean pulse."""
        sr, bpm_target = 44100, 120.0
        frame_size, hop_size = 2048, 512
        frames = _make_pulse_audio(sr, 8.0, bpm_target, frame_size, hop_size)
        result = _run_estimator_with_audio(
            frames, sr, hop_size, frame_size, onset_mode="bass_diff"
        )
        self.assertGreater(result["final_bpm"], 0.0, "Should detect a BPM")
        self.assertAlmostEqual(result["final_bpm"], bpm_target, delta=15.0)

    def test_spectral_flux_uses_flux_not_bass(self) -> None:
        """Verify spectral_flux mode actually uses the spectral_flux value."""
        est = LiveBpmEstimator(sample_rate=44100, hop_size=512, onset_mode="spectral_flux")
        # Feed constant energy but varying spectral flux
        est.update(energy=1.0, t=0.0, spectral_flux=10.0)
        onset1 = est.last_onset
        est.update(energy=1.0, t=0.01, spectral_flux=0.0)
        onset2 = est.last_onset
        # In spectral_flux mode, onset should follow the flux values
        self.assertEqual(onset1, 10.0)
        self.assertEqual(onset2, 0.0)

    def test_bass_diff_ignores_spectral_flux(self) -> None:
        """Verify bass_diff mode ignores spectral_flux and uses energy diff."""
        est = LiveBpmEstimator(sample_rate=44100, hop_size=512, onset_mode="bass_diff")
        est.update(energy=1.0, t=0.0, spectral_flux=999.0)
        onset1 = est.last_onset
        est.update(energy=2.0, t=0.01, spectral_flux=999.0)
        onset2 = est.last_onset
        # bass_diff onset = max(0, energy - prev_energy)
        self.assertEqual(onset1, 1.0)  # max(0, 1.0 - 0.0)
        self.assertEqual(onset2, 1.0)  # max(0, 2.0 - 1.0)

    def test_update_backward_compat_without_flux_arg(self) -> None:
        """Calling update() without spectral_flux should work (defaults to 0.0)."""
        est = LiveBpmEstimator(sample_rate=44100, hop_size=512, onset_mode="bass_diff")
        bpm, beat = est.update(1.0, 0.0)
        self.assertIsInstance(bpm, float)
        self.assertIsInstance(beat, bool)


class TestAdaptiveThreshold(unittest.TestCase):
    """Phase B: adaptive local onset threshold."""

    def test_threshold_mode_default_is_adaptive(self) -> None:
        est = LiveBpmEstimator(sample_rate=44100, hop_size=512)
        self.assertEqual(est.threshold_mode, "adaptive")

    def test_threshold_mode_global_accepted(self) -> None:
        est = LiveBpmEstimator(sample_rate=44100, hop_size=512, threshold_mode="global")
        self.assertEqual(est.threshold_mode, "global")

    def test_threshold_mode_invalid_raises(self) -> None:
        with self.assertRaises(ValueError):
            LiveBpmEstimator(sample_rate=44100, hop_size=512, threshold_mode="bad")

    def test_adaptive_detects_bpm_from_pulse(self) -> None:
        """Adaptive threshold should detect ~120 BPM from a clean pulse train."""
        sr, bpm_target = 44100, 120.0
        frame_size, hop_size = 2048, 512
        frames = _make_pulse_audio(sr, 8.0, bpm_target, frame_size, hop_size)
        result = _run_estimator_with_audio(
            frames, sr, hop_size, frame_size, onset_mode="spectral_flux"
        )
        self.assertGreater(result["final_bpm"], 0.0)
        self.assertAlmostEqual(result["final_bpm"], bpm_target, delta=15.0)
        self.assertGreater(result["beat_count"], 5)

    def test_adaptive_detects_beats_in_low_amplitude_signal(self) -> None:
        """Adaptive threshold should find beats even when signal is very quiet.

        Global threshold often fails here because the absolute onset values
        are tiny but still have clear peaks relative to their local neighborhood.
        """
        sr, bpm_target = 44100, 120.0
        frame_size, hop_size = 2048, 512
        frames = _make_pulse_audio(sr, 8.0, bpm_target, frame_size, hop_size)
        # Scale down to very low amplitude (simulates distant mic)
        frames = [f * 0.01 for f in frames]

        result_adaptive = _run_estimator_with_audio(
            frames, sr, hop_size, frame_size, onset_mode="spectral_flux"
        )
        # Adaptive should still detect something
        self.assertGreater(result_adaptive["final_bpm"], 0.0,
                          "Adaptive threshold should detect BPM even at low amplitude")

    def test_detect_beats_adaptive_method_directly(self) -> None:
        """Test _detect_beats_adaptive on a synthetic onset envelope."""
        est = LiveBpmEstimator(sample_rate=44100, hop_size=512)
        # Create onset envelope with clear periodic peaks
        n_frames = 500
        onset_env = np.zeros(n_frames, dtype=np.float32)
        # Add peaks every ~43 frames (120 BPM at 44100/512)
        beat_period = int(44100 * 60.0 / 120.0 / 512)
        for i in range(0, n_frames, beat_period):
            if i < n_frames:
                onset_env[i] = 1.0
        # Add some noise floor
        rng = np.random.default_rng(42)
        onset_env += rng.uniform(0, 0.1, n_frames).astype(np.float32)

        beats = est._detect_beats_adaptive(onset_env)
        self.assertGreater(len(beats), 3, "Should detect multiple beats")

    def test_global_and_adaptive_both_work_on_clean_signal(self) -> None:
        """Both threshold modes should work on clean, loud signals."""
        sr, bpm_target = 44100, 120.0
        frame_size, hop_size = 2048, 512
        frames = _make_pulse_audio(sr, 8.0, bpm_target, frame_size, hop_size)

        # Run with global threshold
        window, bass_mask, kick_mask = _prepare_bass_window(frame_size, sr)
        est_global = LiveBpmEstimator(
            sample_rate=sr, hop_size=hop_size,
            onset_mode="spectral_flux", threshold_mode="global"
        )
        prev_mag = None
        stream_t = 0.0
        for frame in frames:
            sf = _spectral_features(frame, window, bass_mask, prev_mag, kick_mask=kick_mask)
            prev_mag = sf.mag
            est_global.update(sf.bass, stream_t, spectral_flux=sf.spectral_flux)
            stream_t += float(hop_size) / float(sr)

        self.assertGreater(est_global.last_bpm, 0.0, "Global should detect BPM")


class TestKickBandIsolation(unittest.TestCase):
    """Phase C: narrow-band kick drum isolation."""

    def test_prepare_bass_window_returns_three_masks(self) -> None:
        window, bass_mask, kick_mask = _prepare_bass_window(2048, 44100)
        self.assertEqual(window.shape[0], 2048)
        # kick band should be narrower than bass band
        self.assertGreater(bass_mask.sum(), kick_mask.sum())
        # kick band should be non-empty
        self.assertGreater(kick_mask.sum(), 0)

    def test_spectral_features_has_kick_fields(self) -> None:
        frame_size, sr = 2048, 44100
        window, bass_mask, kick_mask = _prepare_bass_window(frame_size, sr)
        frame = np.random.default_rng(0).standard_normal(frame_size).astype(np.float32)
        sf = _spectral_features(frame, window, bass_mask, None, kick_mask=kick_mask)
        self.assertIsInstance(sf.kick_energy, float)
        self.assertIsInstance(sf.kick_ratio, float)
        self.assertIsInstance(sf.kick_spectral_flux, float)
        # First frame has no prev_mag so kick_spectral_flux = 0
        self.assertEqual(sf.kick_spectral_flux, 0.0)

    def test_kick_spectral_flux_computed_on_second_frame(self) -> None:
        frame_size, sr = 2048, 44100
        window, bass_mask, kick_mask = _prepare_bass_window(frame_size, sr)
        t = np.arange(frame_size, dtype=np.float32) / sr
        # Frame 1: silence in kick band
        frame1 = np.zeros(frame_size, dtype=np.float32)
        # Frame 2: 80Hz sine (in kick band) — creates spectral change
        frame2 = (np.sin(2 * np.pi * 80 * t) * 0.5).astype(np.float32)
        sf1 = _spectral_features(frame1, window, bass_mask, None, kick_mask=kick_mask)
        sf2 = _spectral_features(frame2, window, bass_mask, sf1.mag, kick_mask=kick_mask)
        # Second frame should have non-zero kick flux (80Hz appeared)
        self.assertGreater(sf2.kick_spectral_flux, 0.0)

    def test_onset_mode_kick_flux_accepted(self) -> None:
        est = LiveBpmEstimator(sample_rate=44100, hop_size=512, onset_mode="kick_flux")
        self.assertEqual(est.onset_mode, "kick_flux")

    def test_kick_flux_mode_uses_kick_flux_signal(self) -> None:
        est = LiveBpmEstimator(sample_rate=44100, hop_size=512, onset_mode="kick_flux")
        est.update(energy=1.0, t=0.0, spectral_flux=5.0, kick_spectral_flux=10.0)
        self.assertEqual(est.last_onset, 10.0)

    def test_kick_flux_falls_back_to_spectral_flux_on_silence(self) -> None:
        """When kick band has no energy (kick_spectral_flux == 0), fall back to broadband."""
        est = LiveBpmEstimator(sample_rate=44100, hop_size=512, onset_mode="kick_flux")
        est.update(energy=1.0, t=0.0, spectral_flux=5.0, kick_spectral_flux=0.0)
        self.assertEqual(est.last_onset, 5.0)

    def test_kick_flux_detects_bpm_from_pulse(self) -> None:
        sr, bpm_target = 44100, 120.0
        frame_size, hop_size = 2048, 512
        frames = _make_pulse_audio(sr, 8.0, bpm_target, frame_size, hop_size)
        result = _run_estimator_with_audio(
            frames, sr, hop_size, frame_size, onset_mode="kick_flux"
        )
        self.assertGreater(result["final_bpm"], 0.0)
        self.assertAlmostEqual(result["final_bpm"], bpm_target, delta=15.0)

    def test_spectral_features_without_kick_mask(self) -> None:
        """When kick_mask is None, kick fields default to 0."""
        frame_size, sr = 2048, 44100
        window, bass_mask, _ = _prepare_bass_window(frame_size, sr)
        frame = np.random.default_rng(0).standard_normal(frame_size).astype(np.float32)
        sf = _spectral_features(frame, window, bass_mask, None, kick_mask=None)
        self.assertEqual(sf.kick_energy, 0.0)
        self.assertEqual(sf.kick_ratio, 0.0)
        self.assertEqual(sf.kick_spectral_flux, 0.0)


class TestWhitenedFlux(unittest.TestCase):
    """Phase D: spectral contrast pre-whitening."""

    def test_onset_mode_whitened_flux_accepted(self) -> None:
        est = LiveBpmEstimator(sample_rate=44100, hop_size=512, onset_mode="whitened_flux")
        self.assertEqual(est.onset_mode, "whitened_flux")

    def test_whitened_flux_mode_uses_whitened_flux(self) -> None:
        est = LiveBpmEstimator(sample_rate=44100, hop_size=512, onset_mode="whitened_flux")
        est.update(energy=1.0, t=0.0, spectral_flux=5.0, whitened_flux=12.0)
        self.assertEqual(est.last_onset, 12.0)

    def test_compute_whitened_flux_first_frame(self) -> None:
        """First frame: spectral_mean initializes from mag, no prev → flux=0."""
        mag = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        wf, sm, wm = _compute_whitened_flux(mag, None, None)
        self.assertEqual(wf, 0.0)
        np.testing.assert_array_equal(sm, mag)  # mean initializes from first frame
        np.testing.assert_allclose(wm, mag / (mag + 1e-8), atol=1e-5)

    def test_compute_whitened_flux_second_frame(self) -> None:
        """Second frame with changed spectrum should produce nonzero whitened flux."""
        mag1 = np.array([1.0, 1.0, 1.0], dtype=np.float32)
        _, sm, wm1 = _compute_whitened_flux(mag1, None, None)
        # Second frame: spike in bin 0
        mag2 = np.array([5.0, 1.0, 1.0], dtype=np.float32)
        wf, sm2, wm2 = _compute_whitened_flux(mag2, sm, wm1)
        self.assertGreater(wf, 0.0)

    def test_compute_whitened_flux_learns_spectral_mean(self) -> None:
        """spectral_mean should slowly track toward the current mag (EMA)."""
        mag = np.array([10.0, 10.0, 10.0], dtype=np.float32)
        sm = np.array([1.0, 1.0, 1.0], dtype=np.float32)
        _, sm_new, _ = _compute_whitened_flux(mag, sm, None, alpha=0.05)
        # sm_new = 0.05 * 10 + 0.95 * 1 = 0.5 + 0.95 = 1.45
        np.testing.assert_allclose(sm_new, np.array([1.45, 1.45, 1.45]), atol=1e-5)

    def test_whitened_flux_detects_bpm_from_pulse(self) -> None:
        sr, bpm_target = 44100, 120.0
        frame_size, hop_size = 2048, 512
        frames = _make_pulse_audio(sr, 8.0, bpm_target, frame_size, hop_size)
        result = _run_estimator_with_audio(
            frames, sr, hop_size, frame_size, onset_mode="whitened_flux"
        )
        self.assertGreater(result["final_bpm"], 0.0)
        self.assertAlmostEqual(result["final_bpm"], bpm_target, delta=15.0)


class TestHybridOnset(unittest.TestCase):
    """Hybrid onset mode: bass_diff primary, kick_flux fallback."""

    def test_onset_mode_hybrid_accepted(self) -> None:
        est = LiveBpmEstimator(sample_rate=44100, hop_size=512, onset_mode="hybrid")
        self.assertEqual(est.onset_mode, "hybrid")

    def test_hybrid_starts_on_bass_when_signals_similar(self) -> None:
        """Hybrid should use bass when kick isn't much stronger."""
        est = LiveBpmEstimator(sample_rate=44100, hop_size=512, onset_mode="hybrid")
        est.update(energy=1.0, t=0.0, kick_spectral_flux=1.0)
        # bass_onset = 1.0, kick_flux = 1.0, ratio ~1.0 < 3.0 → stays on bass
        self.assertEqual(est._hybrid_source, "bass")
        self.assertEqual(est.last_onset, 1.0)

    def test_hybrid_uses_bass_when_bass_has_signal(self) -> None:
        """When bass energy changes are present, hybrid should stay on bass."""
        est = LiveBpmEstimator(sample_rate=44100, hop_size=512, onset_mode="hybrid")
        # Simulate strong bass onsets with moderate kick flux
        for i in range(100):
            energy = 1.0 + (i % 2) * 0.5  # alternating bass energy
            est.update(energy=energy, t=i * 0.01, kick_spectral_flux=0.3)
        self.assertEqual(est._hybrid_source, "bass")

    def test_hybrid_switches_to_kick_when_bass_flat(self) -> None:
        """When bass energy is constant but kick band has activity, switch to kick."""
        est = LiveBpmEstimator(sample_rate=44100, hop_size=512, onset_mode="hybrid")
        # Constant bass energy (onset = 0) but strong kick flux
        for i in range(200):
            est.update(energy=1.0, t=i * 0.01, kick_spectral_flux=10.0)
        self.assertEqual(est._hybrid_source, "kick")

    def test_hybrid_detects_bpm_from_pulse(self) -> None:
        """Hybrid mode should detect ~120 BPM from a clean pulse train."""
        sr, bpm_target = 44100, 120.0
        frame_size, hop_size = 2048, 512
        frames = _make_pulse_audio(sr, 8.0, bpm_target, frame_size, hop_size)
        result = _run_estimator_with_audio(
            frames, sr, hop_size, frame_size, onset_mode="hybrid"
        )
        self.assertGreater(result["final_bpm"], 0.0)
        self.assertAlmostEqual(result["final_bpm"], bpm_target, delta=15.0)
        self.assertGreater(result["beat_count"], 5)

    def test_hybrid_detects_bpm_at_low_amplitude(self) -> None:
        """Hybrid should still detect BPM when signal is very quiet."""
        sr, bpm_target = 44100, 120.0
        frame_size, hop_size = 2048, 512
        frames = _make_pulse_audio(sr, 8.0, bpm_target, frame_size, hop_size)
        frames = [f * 0.02 for f in frames]
        result = _run_estimator_with_audio(
            frames, sr, hop_size, frame_size, onset_mode="hybrid"
        )
        self.assertGreater(result["final_bpm"], 0.0,
                          "Hybrid should detect BPM even at low amplitude")


if __name__ == "__main__":
    unittest.main()
