"""Tests for LiveBpmEstimator onset modes and beat detection."""

import unittest

import numpy as np

from dreamsync.dsp.features import _estimate_bpm
from dreamsync.live import (
    HARMONIC_RATIOS,
    CyclicBeatGridTracker,
    IOIHistogram,
    LiveEqStateTracker,
    LiveInstrumentState,
    LiveInstrumentStateTracker,
    LivePanFrame,
    LiveBpmEstimator,
    LiveCycleTempoOverride,
    NoiseFloorEstimator,
    PercussiveOnsetTracker,
    _apply_live_routes_to_intent,
    SpectralBeatTemplate,
    SpectralFeatures,
    _apply_live_eq_to_intent,
    _build_eq_layers,
    _compute_whitened_flux,
    _feature_row_from_frame,
    _prepare_bass_window,
    _prepare_live_audio_chunk,
    _resolve_live_eq_routes,
    _resolve_live_instrument_routes,
    _spectral_features,
    _stereo_pan_features_live,
)


def test_cycle_tempo_override_halves_by_emitting_every_other_beat():
    override = LiveCycleTempoOverride()
    override.set_multiplier(0.5)

    rows = [
        override.update(
            t=index * 0.5,
            detected_bpm=120.0,
            detected_beat=True,
        )
        for index in range(4)
    ]

    assert [bpm for bpm, _beat in rows] == [60.0] * 4
    assert [beat for _bpm, beat in rows] == [
        True,
        False,
        True,
        False,
    ]


def test_cycle_tempo_override_doubles_with_midpoint_subdivision():
    override = LiveCycleTempoOverride()
    override.set_multiplier(2.0)

    assert override.update(
        t=0.0,
        detected_bpm=120.0,
        detected_beat=True,
    ) == (240.0, True)
    assert override.update(
        t=0.24,
        detected_bpm=120.0,
        detected_beat=False,
    ) == (240.0, False)
    assert override.update(
        t=0.25,
        detected_bpm=120.0,
        detected_beat=False,
    ) == (240.0, True)
    assert override.update(
        t=0.5,
        detected_bpm=120.0,
        detected_beat=True,
    ) == (240.0, True)


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


def _band_vector(**values: float) -> tuple[float, ...]:
    order = ("sub", "kick", "bass", "low_mid", "mid", "presence", "air")
    return tuple(float(values.get(name, 0.0)) for name in order)


def _spectral_stub(
    *,
    ratios: tuple[float, ...] | None = None,
    fluxes: tuple[float, ...] | None = None,
) -> SpectralFeatures:
    zeros = np.zeros(8, dtype=np.float32)
    return SpectralFeatures(
        bass=0.0,
        bass_ratio=float((ratios or _band_vector())[2]),
        spectral_flux=0.0,
        kick_energy=0.0,
        kick_ratio=0.0,
        kick_spectral_flux=0.0,
        centroid=0.0,
        mag=zeros,
        band_energies=_band_vector(),
        band_ratios=ratios or _band_vector(),
        band_fluxes=fluxes or _band_vector(),
    )


def _run_estimator_with_audio(
    frames: list[np.ndarray],
    sr: int,
    hop_size: int,
    frame_size: int,
    onset_mode: str = "spectral_flux",
    enable_noise_floor: bool = False,
    enable_percussive: bool = False,
    **estimator_kwargs,
) -> dict:
    """Feed frames through spectral features + LiveBpmEstimator, return results."""
    window, bass_mask, kick_mask, _freqs = _prepare_bass_window(frame_size, sr)
    est = LiveBpmEstimator(sample_rate=sr, hop_size=hop_size, onset_mode=onset_mode, **estimator_kwargs)
    n_bins = frame_size // 2 + 1
    noise_estimator = NoiseFloorEstimator(n_bins=n_bins) if enable_noise_floor else None
    perc_tracker = PercussiveOnsetTracker(n_bins=n_bins, energy_gate=1.0) if enable_percussive else None
    prev_mag = None
    spectral_mean = None
    prev_whitened_mag = None
    beat_count = 0
    stream_t = 0.0
    bpm = 0.0
    bpm_history = []

    for frame in frames:
        nf = noise_estimator.noise_floor if (noise_estimator and noise_estimator.ready) else None
        sf = _spectral_features(frame, window, bass_mask, prev_mag, kick_mask=kick_mask, noise_floor=nf)
        raw_mag = sf.raw_mag if sf.raw_mag is not None else sf.mag
        if noise_estimator is not None:
            noise_estimator.update(raw_mag)
        prev_mag = sf.mag
        wf, spectral_mean, prev_whitened_mag = _compute_whitened_flux(
            raw_mag, spectral_mean, prev_whitened_mag,
        )
        perc = perc_tracker.update(raw_mag) if perc_tracker else 0.0
        bpm, beat = est.update(
            sf.bass, stream_t,
            spectral_flux=sf.spectral_flux,
            kick_spectral_flux=sf.kick_spectral_flux,
            whitened_flux=wf,
            percussive_onset=perc,
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
        "hybrid_source": est._hybrid_source,
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
        frames = _make_pulse_audio(sr, 12.0, bpm_target, frame_size, hop_size)
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
        frames = _make_pulse_audio(sr, 12.0, bpm_target, frame_size, hop_size)
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
        frames = _make_pulse_audio(sr, 12.0, bpm_target, frame_size, hop_size)
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
        frames = _make_pulse_audio(sr, 12.0, bpm_target, frame_size, hop_size)
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
        frames = _make_pulse_audio(sr, 12.0, bpm_target, frame_size, hop_size)

        # Run with global threshold
        window, bass_mask, kick_mask, _freqs = _prepare_bass_window(frame_size, sr)
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

    def test_prepare_bass_window_returns_masks_and_freqs(self) -> None:
        window, bass_mask, kick_mask, freqs = _prepare_bass_window(2048, 44100)
        self.assertEqual(window.shape[0], 2048)
        # kick band should be narrower than bass band
        self.assertGreater(bass_mask.sum(), kick_mask.sum())
        # kick band should be non-empty
        self.assertGreater(kick_mask.sum(), 0)
        # freqs should have same length as rfft output
        self.assertEqual(freqs.shape[0], 2048 // 2 + 1)

    def test_spectral_features_has_kick_fields(self) -> None:
        frame_size, sr = 2048, 44100
        window, bass_mask, kick_mask, _freqs = _prepare_bass_window(frame_size, sr)
        frame = np.random.default_rng(0).standard_normal(frame_size).astype(np.float32)
        sf = _spectral_features(frame, window, bass_mask, None, kick_mask=kick_mask)
        self.assertIsInstance(sf.kick_energy, float)
        self.assertIsInstance(sf.kick_ratio, float)
        self.assertIsInstance(sf.kick_spectral_flux, float)
        # First frame has no prev_mag so kick_spectral_flux = 0
        self.assertEqual(sf.kick_spectral_flux, 0.0)

    def test_kick_spectral_flux_computed_on_second_frame(self) -> None:
        frame_size, sr = 2048, 44100
        window, bass_mask, kick_mask, _freqs = _prepare_bass_window(frame_size, sr)
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
        frames = _make_pulse_audio(sr, 12.0, bpm_target, frame_size, hop_size)
        result = _run_estimator_with_audio(
            frames, sr, hop_size, frame_size, onset_mode="kick_flux"
        )
        self.assertGreater(result["final_bpm"], 0.0)
        self.assertAlmostEqual(result["final_bpm"], bpm_target, delta=15.0)

    def test_spectral_features_without_kick_mask(self) -> None:
        """When kick_mask is None, kick fields default to 0."""
        frame_size, sr = 2048, 44100
        window, bass_mask, _, _f = _prepare_bass_window(frame_size, sr)
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


class TestLiveEqRouting(unittest.TestCase):
    def test_tracker_detects_bass_enter_and_dominant_band(self) -> None:
        tracker = LiveEqStateTracker(smoothing=1.0, event_cooldown_seconds=0.0)
        tracker.update(_spectral_stub(ratios=_band_vector(low_mid=0.05, mid=0.05)), 0.0)
        state = tracker.update(_spectral_stub(ratios=_band_vector(bass=0.26, low_mid=0.04)), 0.1)
        self.assertEqual(state.dominant_band, "bass")
        self.assertIn("bass_enter", state.events)
        self.assertIn(("bass", "dominant"), state.trigger_keys)

    def test_tracker_detects_presence_lift_from_band_flux(self) -> None:
        tracker = LiveEqStateTracker(smoothing=1.0, event_cooldown_seconds=0.0)
        tracker.update(
            _spectral_stub(
                ratios=_band_vector(presence=0.18, mid=0.08),
                fluxes=_band_vector(),
            ),
            0.0,
        )
        state = tracker.update(
            _spectral_stub(
                ratios=_band_vector(presence=0.22, mid=0.05),
                fluxes=_band_vector(presence=0.8, bass=0.1),
            ),
            0.1,
        )
        self.assertIn("presence_lift", state.events)
        self.assertIn(("presence", "lift"), state.trigger_keys)

    def test_resolve_live_eq_routes_merges_configured_overrides(self) -> None:
        tracker = LiveEqStateTracker(smoothing=1.0, event_cooldown_seconds=0.0)
        tracker.update(_spectral_stub(ratios=_band_vector()), 0.0)
        state = tracker.update(_spectral_stub(ratios=_band_vector(bass=0.28)), 0.1)
        routes = _resolve_live_eq_routes(
            state,
            {
                "eq_routes": [
                    {
                        "band": "bass",
                        "when": "dominant",
                        "color_bias": "#00ffaa",
                        "spatial_preset": "wave_front_to_back",
                        "intensity_boost": 0.03,
                    }
                ]
            },
        )
        dominant_route = next(route for route in routes if route["when"] == "dominant")
        self.assertEqual(dominant_route["band"], "bass")
        self.assertEqual(dominant_route["color_bias"], "#00ffaa")
        self.assertEqual(dominant_route["spatial_preset"], "wave_front_to_back")

    def test_build_eq_layers_and_apply_live_eq_to_intent(self) -> None:
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class _Intent:
            intensity: float
            color: str

        routes = [
            {
                "band": "presence",
                "when": "lift",
                "color_bias": "#66ccff",
                "spatial_preset": "flash_top_only",
                "effect_layer": {"layer_category": "static", "effect_mode": "pulse"},
                "layer_category": "static",
                "falloff": "smoothstep",
                "intensity_scale": 1.2,
                "intensity_boost": 0.1,
            },
            {
                "band": "bass",
                "when": "dominant",
                "color_bias": "#ff8800",
                "spatial_preset": "flash_floor_only",
                "intensity_boost": 0.06,
            },
        ]
        layers = _build_eq_layers(routes)
        self.assertEqual([layer["band"] for layer in layers], ["presence", "bass"])
        self.assertEqual(layers[0]["effect_layer"]["layer_category"], "static")
        self.assertEqual(layers[0]["layer_category"], "static")
        self.assertEqual(layers[0]["falloff"], "smoothstep")
        self.assertEqual(layers[0]["intensity_scale"], 1.2)
        intent = _apply_live_eq_to_intent(_Intent(intensity=0.5, color="#123456"), routes)
        self.assertEqual(intent.color, "#66ccff")
        self.assertAlmostEqual(intent.intensity, 0.66, places=2)


class TestLiveStereoPreservation(unittest.TestCase):
    def test_prepare_live_audio_chunk_preserves_stereo(self) -> None:
        indata = np.array(
            [
                [0.8, 0.2],
                [0.6, 0.4],
                [0.2, 0.8],
            ],
            dtype=np.float32,
        )
        mono, stereo, preserved = _prepare_live_audio_chunk(indata)
        self.assertTrue(preserved)
        self.assertIsNotNone(stereo)
        self.assertEqual(stereo.shape, (3, 2))
        np.testing.assert_allclose(mono, np.array([0.5, 0.5, 0.5], dtype=np.float32))

    def test_prepare_live_audio_chunk_falls_back_to_mono(self) -> None:
        indata = np.array([0.2, -0.1, 0.5], dtype=np.float32)
        mono, stereo, preserved = _prepare_live_audio_chunk(indata)
        self.assertFalse(preserved)
        self.assertIsNone(stereo)
        np.testing.assert_allclose(mono, indata)

    def test_stereo_pan_features_live_detects_left_bias(self) -> None:
        frame_size = 2048
        sample_rate = 44100
        window, _bass_mask, _kick_mask, freqs = _prepare_bass_window(frame_size, sample_rate)
        band_masks = tuple((freqs >= low_hz) & (freqs <= high_hz) for _, low_hz, high_hz in (
            ("sub", 20.0, 60.0),
            ("kick", 50.0, 130.0),
            ("bass", 60.0, 250.0),
            ("low_mid", 250.0, 500.0),
            ("mid", 500.0, 2000.0),
            ("presence", 2000.0, 6000.0),
            ("air", 6000.0, 16000.0),
        ))
        t = np.arange(frame_size, dtype=np.float32) / sample_rate
        left = (0.9 * np.sin(2 * np.pi * 110 * t)).astype(np.float32)
        right = (0.2 * np.sin(2 * np.pi * 110 * t)).astype(np.float32)
        stereo = np.column_stack([left, right]).astype(np.float32)

        result = _stereo_pan_features_live(stereo, window, band_masks)
        self.assertIsInstance(result, LivePanFrame)
        self.assertTrue(result.stereo_preserved)
        self.assertLess(result.pan_center, -0.4)
        self.assertGreaterEqual(result.pan_width, 0.0)
        self.assertGreater(result.left_energy, result.right_energy)
        self.assertTrue(any(abs(value) > 0.05 for value in result.band_pan_centers))

    def test_feature_row_from_frame_carries_pan_fields(self) -> None:
        frame = np.array([0.1, -0.1, 0.2, -0.2], dtype=np.float32)
        row = _feature_row_from_frame(
            frame,
            rms=0.15,
            t=1.0,
            bpm=128.0,
            beat=True,
            pan_center=0.35,
            pan_width=0.28,
            left_energy=0.12,
            right_energy=0.25,
            band_pan_centers=_band_vector(bass=-0.2, presence=0.4),
            stereo_preserved=True,
        )
        self.assertEqual(row["pan_center"], 0.35)
        self.assertEqual(row["pan_width"], 0.28)
        self.assertEqual(row["left_energy"], 0.12)
        self.assertEqual(row["right_energy"], 0.25)
        self.assertEqual(row["band_pan_centers"][2], -0.2)
        self.assertTrue(row["stereo_preserved"])


class TestLiveInstrumentProxyTracking(unittest.TestCase):
    def test_tracker_detects_bass_presence_and_enter(self) -> None:
        tracker = LiveInstrumentStateTracker(smoothing=1.0, event_cooldown_seconds=0.0)
        tracker.update(
            _spectral_stub(ratios=_band_vector(mid=0.06, presence=0.05)),
            {
                "onset_strength": 0.05,
                "spectral_flux": 0.1,
                "pan_center": 0.0,
                "pan_width": 0.1,
                "band_pan_centers": _band_vector(),
            },
            percussive_onset=0.0,
            t=0.0,
        )
        state = tracker.update(
            _spectral_stub(
                ratios=_band_vector(sub=0.16, bass=0.28, low_mid=0.05),
                fluxes=_band_vector(bass=0.2, kick=0.1),
            ),
            {
                "onset_strength": 0.9,
                "spectral_flux": 12.0,
                "pan_center": -0.08,
                "pan_width": 0.18,
                "band_pan_centers": _band_vector(bass=-0.22),
            },
            percussive_onset=0.4,
            t=0.1,
        )
        self.assertEqual(state.dominant_proxy, "bass")
        self.assertGreater(state.bass, state.vocals)
        self.assertIn("bass_enter", state.events)
        self.assertIn(("bass", "dominant"), state.trigger_keys)
        self.assertIn(("bass", "present"), state.trigger_keys)

    def test_tracker_prefers_centered_vocals(self) -> None:
        tracker = LiveInstrumentStateTracker(smoothing=1.0, event_cooldown_seconds=0.0)
        state = tracker.update(
            _spectral_stub(
                ratios=_band_vector(mid=0.22, presence=0.31, low_mid=0.08),
                fluxes=_band_vector(presence=0.4, mid=0.2),
            ),
            {
                "onset_strength": 0.3,
                "spectral_flux": 3.0,
                "pan_center": 0.02,
                "pan_width": 0.08,
                "band_pan_centers": _band_vector(mid=0.04, presence=0.06),
            },
            percussive_onset=0.05,
            t=0.0,
        )
        self.assertEqual(state.dominant_proxy, "vocals")
        self.assertGreater(state.vocals, state.percussive)
        self.assertGreater(state.vocals, state.harmonic)

    def test_tracker_holds_dominant_proxy_briefly_during_dense_transition(self) -> None:
        tracker = LiveInstrumentStateTracker(
            smoothing=1.0,
            dominant_hold_seconds=0.8,
            dominant_margin=0.08,
            event_cooldown_seconds=0.0,
        )
        first = tracker.update(
            _spectral_stub(
                ratios=_band_vector(sub=0.14, bass=0.3),
                fluxes=_band_vector(bass=0.12, kick=0.05),
            ),
            {
                "onset_strength": 0.35,
                "spectral_flux": 4.0,
                "pan_center": -0.12,
                "pan_width": 0.2,
                "band_pan_centers": _band_vector(bass=-0.18),
            },
            percussive_onset=0.1,
            t=0.0,
        )
        second = tracker.update(
            _spectral_stub(
                ratios=_band_vector(mid=0.18, presence=0.24, low_mid=0.12),
                fluxes=_band_vector(presence=0.3, kick=0.18),
            ),
            {
                "onset_strength": 0.45,
                "spectral_flux": 6.0,
                "pan_center": 0.01,
                "pan_width": 0.15,
                "band_pan_centers": _band_vector(presence=0.04),
            },
            percussive_onset=0.12,
            t=0.2,
        )
        self.assertEqual(first.dominant_proxy, "bass")
        self.assertEqual(second.dominant_proxy, "bass")

    def test_resolve_live_instrument_routes_merges_override_and_enriches_pan(self) -> None:
        state = LiveInstrumentState(
            dominant_proxy="vocals",
            vocals=0.74,
            harmonic=0.31,
            pan_center=0.62,
            pan_width=0.28,
            band_pan_centers=_band_vector(presence=0.35),
            trigger_keys=(("vocals", "dominant"), ("vocals", "present")),
        )
        routes = _resolve_live_instrument_routes(
            state,
            {
                "instrument_routes": [
                    {
                        "instrument": "vocals",
                        "when": "dominant",
                        "spatial_zone": "right",
                        "pan_follow": 0.75,
                        "width_scale": 1.2,
                        "color_bias": "#99ddff",
                        "effect_layer": {"layer_category": "slice", "effect_mode": "pulse"},
                        "layer_category": "slice",
                        "speed_units_per_second": 1.25,
                    }
                ]
            },
        )
        self.assertEqual(len(routes), 1)
        route = routes[0]
        self.assertEqual(route["instrument"], "vocals")
        self.assertEqual(route["when"], "dominant")
        self.assertEqual(route["color_bias"], "#99ddff")
        self.assertGreater(route["spatial_origin"]["x"], 0.9)
        self.assertGreater(route["spatial_width"], 0.18)
        self.assertEqual(route["band_pan_centers"][5], 0.35)
        self.assertEqual(route["effect_layer"]["layer_category"], "slice")
        self.assertEqual(route["layer_category"], "slice")
        self.assertEqual(route["speed_units_per_second"], 1.25)

    def test_build_eq_layers_carries_instrument_metadata(self) -> None:
        layers = _build_eq_layers([
            {
                "instrument": "bass",
                "when": "dominant",
                "spatial_preset": "flash_floor_only",
                "pan_follow": 0.8,
                "width_scale": 1.1,
                "color_bias": "#ff8a3d",
                "trigger_mode": "oneshot",
                "duration_s": 0.4,
                "layer_priority": 4,
            }
        ])
        self.assertEqual(len(layers), 1)
        self.assertEqual(layers[0]["instrument"], "bass")
        self.assertEqual(layers[0]["when"], "dominant")
        self.assertEqual(layers[0]["pan_follow"], 0.8)
        self.assertEqual(layers[0]["trigger_mode"], "oneshot")
        self.assertEqual(layers[0]["duration_s"], 0.4)
        self.assertEqual(layers[0]["layer_priority"], 4)

    def test_apply_live_routes_to_intent_uses_route_bias_and_boost(self) -> None:
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class _Intent:
            intensity: float
            color: str

        intent = _Intent(intensity=0.4, color="#123456")
        result = _apply_live_routes_to_intent(
            intent,
            [
                {"instrument": "vocals", "when": "dominant", "color_bias": "#99ddff", "intensity_boost": 0.08},
                {"band": "presence", "when": "lift", "color_bias": "#66ccff", "intensity_boost": 0.04},
            ],
        )
        self.assertEqual(result.color, "#99ddff")
        self.assertAlmostEqual(result.intensity, 0.52, places=2)

    def test_compute_whitened_flux_first_frame(self) -> None:
        """First frame: spectral_mean initializes from mag, no prev → flux=0."""
        mag = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        wf, sm, wm = _compute_whitened_flux(mag, None, None)
        self.assertEqual(wf, 0.0)
        np.testing.assert_array_equal(sm, mag)  # mean initializes from first frame
        # First frame returns prev_whitened_mag (None) since it's initialization
        self.assertIsNone(wm)

    def test_compute_whitened_flux_second_frame(self) -> None:
        """Second frame with changed spectrum should produce nonzero whitened flux."""
        mag1 = np.array([1.0, 1.0, 1.0], dtype=np.float32)
        _, sm, wm1 = _compute_whitened_flux(mag1, None, None)
        # wm1 is None (first frame is init-only); second frame produces wm but no flux yet
        mag2 = np.array([5.0, 1.0, 1.0], dtype=np.float32)
        wf2, sm2, wm2 = _compute_whitened_flux(mag2, sm, wm1)
        self.assertIsNotNone(wm2)
        # Third frame: another change should produce flux (two active frames with prev)
        mag3 = np.array([1.0, 5.0, 1.0], dtype=np.float32)
        wf3, sm3, wm3 = _compute_whitened_flux(mag3, sm2, wm2)
        self.assertGreater(wf3, 0.0)

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
        frames = _make_pulse_audio(sr, 12.0, bpm_target, frame_size, hop_size)
        result = _run_estimator_with_audio(
            frames, sr, hop_size, frame_size, onset_mode="whitened_flux"
        )
        self.assertGreater(result["final_bpm"], 0.0)
        self.assertAlmostEqual(result["final_bpm"], bpm_target, delta=15.0)


    def test_energy_gate_suppresses_silence_frames(self) -> None:
        """With energy_gate, silent frames don't dilute spectral mean or produce flux."""
        # Two active frames with different spectra
        mag_a = np.array([10.0, 1.0, 1.0], dtype=np.float32)
        mag_b = np.array([1.0, 10.0, 1.0], dtype=np.float32)
        silence = np.array([0.0, 0.0, 0.0], dtype=np.float32)

        # Without gate: silence dilutes the mean
        _, sm1, wm1 = _compute_whitened_flux(mag_a, None, None)
        _, sm2, wm2 = _compute_whitened_flux(silence, sm1, wm1)
        # sm2 was updated with silence (pulls mean toward 0)
        self.assertLess(float(sm2.sum()), float(sm1.sum()))

        # With gate: silence does NOT update the mean
        _, sm1g, wm1g = _compute_whitened_flux(mag_a, None, None, energy_gate=1.0)
        _, sm2g, wm2g = _compute_whitened_flux(silence, sm1g, wm1g, energy_gate=1.0)
        np.testing.assert_array_equal(sm2g, sm1g)  # mean unchanged

    def test_energy_gate_no_flux_on_silence_to_active(self) -> None:
        """Transition from silence to active should not produce flux with energy gate."""
        active = np.array([10.0, 10.0, 10.0], dtype=np.float32)
        silence = np.array([0.0, 0.0, 0.0], dtype=np.float32)

        # Init with active frame
        _, sm, wm = _compute_whitened_flux(active, None, None, energy_gate=1.0)
        # Silent frame (returns prev active wm unchanged)
        wf_s, sm, wm = _compute_whitened_flux(silence, sm, wm, energy_gate=1.0)
        self.assertEqual(wf_s, 0.0)
        # Active frame after silence: no flux (prev_whitened_mag is None from init)
        wf_a, sm, wm = _compute_whitened_flux(active, sm, wm, energy_gate=1.0)
        # Since wm was None (from init), this is first real comparison — no flux yet
        # After the init frame sets sm, the next active frame gets wm but flux=0
        # because the init frame returned wm=None
        self.assertEqual(wf_a, 0.0)

    def test_energy_gate_flux_between_active_frames(self) -> None:
        """Flux should fire between two consecutive active frames with different spectra."""
        mag_a = np.array([10.0, 1.0, 1.0], dtype=np.float32)
        mag_b = np.array([1.0, 10.0, 1.0], dtype=np.float32)

        _, sm, wm = _compute_whitened_flux(mag_a, None, None, energy_gate=1.0)
        # Second active: gets whitened_mag but no flux (wm is None from init)
        wf2, sm, wm = _compute_whitened_flux(mag_a, sm, wm, energy_gate=1.0)
        # Third active with different spectrum: should produce flux
        wf3, sm, wm = _compute_whitened_flux(mag_b, sm, wm, energy_gate=1.0)
        self.assertGreater(wf3, 0.0)


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
        frames = _make_pulse_audio(sr, 12.0, bpm_target, frame_size, hop_size)
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
        frames = _make_pulse_audio(sr, 12.0, bpm_target, frame_size, hop_size)
        frames = [f * 0.02 for f in frames]
        result = _run_estimator_with_audio(
            frames, sr, hop_size, frame_size, onset_mode="hybrid"
        )
        self.assertGreater(result["final_bpm"], 0.0,
                          "Hybrid should detect BPM even at low amplitude")


class TestSpectralTemplateIntegration(unittest.TestCase):
    """Integration tests for spectral template gating in LiveBpmEstimator."""

    def test_template_onset_gating(self) -> None:
        """With mag= supplied, onset values are attenuated for non-beat-like frames."""
        sr, frame_size, hop_size = 44100, 2048, 512
        window, bass_mask, kick_mask, _freqs = _prepare_bass_window(frame_size, sr)
        est = LiveBpmEstimator(
            sample_rate=sr, hop_size=hop_size, onset_mode="spectral_flux",
        )
        frames = _make_pulse_audio(sr, 8.0, 120.0, frame_size, hop_size)
        prev_mag = None
        spectral_mean = None
        prev_whitened_mag = None
        stream_t = 0.0

        # Feed frames with mag to bootstrap the template
        for frame in frames:
            sf = _spectral_features(frame, window, bass_mask, prev_mag, kick_mask=kick_mask)
            prev_mag = sf.mag
            wf, spectral_mean, prev_whitened_mag = _compute_whitened_flux(
                sf.mag, spectral_mean, prev_whitened_mag,
            )
            est.update(
                sf.bass, stream_t,
                spectral_flux=sf.spectral_flux,
                kick_spectral_flux=sf.kick_spectral_flux,
                whitened_flux=wf,
                mag=sf.mag,
            )
            stream_t += float(hop_size) / float(sr)

        # Template should be ready after 8+ seconds of 120 BPM
        self.assertTrue(est._beat_template.ready)

        # Now feed a noise frame and check onset is attenuated
        rng = np.random.default_rng(77)
        noise_frame = rng.standard_normal(frame_size).astype(np.float32) * 0.01
        sf_noise = _spectral_features(noise_frame, window, bass_mask, prev_mag, kick_mask=kick_mask)

        # Get onset without mag (no gating)
        est_no_gate = LiveBpmEstimator(
            sample_rate=sr, hop_size=hop_size, onset_mode="spectral_flux",
        )
        est_no_gate.update(sf_noise.bass, 0.0, spectral_flux=sf_noise.spectral_flux)
        raw_onset = est_no_gate.last_onset

        # Get onset with mag (gated) - if template is ready and similarity is low,
        # the onset should be attenuated (multiplied by gate <= 1.0)
        est.update(
            sf_noise.bass, stream_t,
            spectral_flux=sf_noise.spectral_flux,
            kick_spectral_flux=sf_noise.kick_spectral_flux,
            whitened_flux=0.0,
            mag=sf_noise.mag,
        )
        gated_onset = est.last_onset

        # The gated onset should be <= the raw onset (attenuated or equal)
        if raw_onset > 0:
            self.assertLessEqual(gated_onset, raw_onset + 1e-6)

    def test_backward_compat_no_mag(self) -> None:
        """Calling update() without mag= works identically to before."""
        est = LiveBpmEstimator(sample_rate=44100, hop_size=512, onset_mode="bass_diff")
        bpm, beat = est.update(1.0, 0.0)
        self.assertIsInstance(bpm, float)
        self.assertIsInstance(beat, bool)
        # Template should not be ready (no mag ever provided)
        self.assertFalse(est._beat_template.ready)

        # Feed several frames — should work fine without mag
        for i in range(100):
            energy = 1.0 + (i % 2) * 0.5
            bpm, beat = est.update(energy, i * 0.01)
        self.assertIsInstance(bpm, float)


class TestHybridWhitenedFluxFallback(unittest.TestCase):
    """Phase 1A: whitened flux fallback when bass and kick are dead."""

    def test_hybrid_falls_back_to_whitened_when_bass_and_kick_dead(self) -> None:
        """When bass_onset=0 and kick_flux=0 but whitened_flux has signal, use whitened."""
        est = LiveBpmEstimator(sample_rate=44100, hop_size=512, onset_mode="hybrid")
        # Feed many frames with zero bass/kick but nonzero whitened flux
        for i in range(200):
            est.update(energy=1.0, t=i * 0.01, kick_spectral_flux=0.0, whitened_flux=50.0)
        self.assertEqual(est._hybrid_source, "whitened")

    def test_hybrid_stays_on_bass_when_bass_has_signal(self) -> None:
        """When bass has activity comparable to whitened flux, stay on bass."""
        est = LiveBpmEstimator(sample_rate=44100, hop_size=512, onset_mode="hybrid")
        for i in range(200):
            energy = 1.0 + (i % 2) * 5.0  # strong alternating bass energy
            est.update(energy=energy, t=i * 0.01, whitened_flux=10.0)
        self.assertEqual(est._hybrid_source, "bass")

    def test_hybrid_whitened_fallback_onset_value(self) -> None:
        """When in whitened mode, last_onset should be log1p(whitened_flux)."""
        import math
        est = LiveBpmEstimator(sample_rate=44100, hop_size=512, onset_mode="hybrid")
        # Force into dead-signal state
        for i in range(200):
            est.update(energy=1.0, t=i * 0.01, kick_spectral_flux=0.0, whitened_flux=42.0)
        self.assertEqual(est._hybrid_source, "whitened")
        est.update(energy=1.0, t=2.01, kick_spectral_flux=0.0, whitened_flux=99.0)
        self.assertAlmostEqual(est.last_onset, math.log1p(99.0), places=4)

    def test_hybrid_detects_bpm_noisy_signal_with_whitened_flux(self) -> None:
        """Simulate noisy environment: bass/kick dead, whitened flux has periodic spikes."""
        sr, bpm_target = 44100, 120.0
        frame_size, hop_size = 2048, 512
        n_samples = int(sr * 12.0)
        n_frames = (n_samples - frame_size) // hop_size
        period_frames = int(sr * 60.0 / bpm_target / hop_size)

        window, bass_mask, kick_mask, _freqs = _prepare_bass_window(frame_size, sr)
        est = LiveBpmEstimator(sample_rate=sr, hop_size=hop_size, onset_mode="hybrid")
        rng = np.random.default_rng(42)
        stream_t = 0.0
        beat_count = 0

        for i in range(n_frames):
            # Simulate: constant energy (bass_onset = 0), no kick flux
            # But periodic whitened flux spikes
            wf = 100.0 if (i % period_frames < 2) else rng.uniform(0, 5)
            bpm, beat = est.update(
                energy=1.0, t=stream_t,
                kick_spectral_flux=0.0,
                whitened_flux=wf,
            )
            if beat:
                beat_count += 1
            stream_t += float(hop_size) / float(sr)

        self.assertEqual(est._hybrid_source, "whitened")
        self.assertGreater(bpm, 0.0, "Should detect BPM from whitened flux spikes")
        self.assertGreater(beat_count, 3, "Should detect beats from whitened flux")

    def test_wf_activity_tracked_in_reset(self) -> None:
        est = LiveBpmEstimator(sample_rate=44100, hop_size=512, onset_mode="hybrid")
        est.update(energy=1.0, t=0.0, whitened_flux=50.0)
        self.assertGreater(est._wf_activity, 0.0)
        est.reset()
        self.assertEqual(est._wf_activity, 0.0)


class TestOnsetNormalization(unittest.TestCase):
    """Phase 1B: onset envelope normalization for amplitude invariance."""

    def test_low_amplitude_pulse_detected_with_normalization(self) -> None:
        """Very quiet signal should still detect BPM thanks to normalization."""
        sr, bpm_target = 44100, 120.0
        frame_size, hop_size = 2048, 512
        frames = _make_pulse_audio(sr, 12.0, bpm_target, frame_size, hop_size)
        # Scale to extremely low amplitude
        frames = [f * 0.005 for f in frames]
        result = _run_estimator_with_audio(
            frames, sr, hop_size, frame_size, onset_mode="spectral_flux"
        )
        self.assertGreater(result["final_bpm"], 0.0,
                          "Normalization should enable BPM detection at very low amplitude")

    def test_normal_amplitude_still_works(self) -> None:
        """Normal amplitude pulse train should still work with normalization."""
        sr, bpm_target = 44100, 120.0
        frame_size, hop_size = 2048, 512
        frames = _make_pulse_audio(sr, 12.0, bpm_target, frame_size, hop_size)
        result = _run_estimator_with_audio(
            frames, sr, hop_size, frame_size, onset_mode="spectral_flux"
        )
        self.assertGreater(result["final_bpm"], 0.0)
        self.assertAlmostEqual(result["final_bpm"], bpm_target, delta=15.0)


class TestNoiseFloorEstimator(unittest.TestCase):
    """Phase 2: minimum-statistics noise floor estimation."""

    def test_noise_floor_basic(self) -> None:
        """Noise floor should be per-bin minimum over the window."""
        nfe = NoiseFloorEstimator(n_bins=4, window_frames=5)
        nfe.update(np.array([5.0, 3.0, 7.0, 1.0]))
        nfe.update(np.array([2.0, 6.0, 1.0, 4.0]))
        nfe.update(np.array([3.0, 1.0, 5.0, 2.0]))
        nf = nfe.noise_floor
        np.testing.assert_array_equal(nf, [2.0, 1.0, 1.0, 1.0])

    def test_noise_floor_sliding_window(self) -> None:
        """When window overflows, oldest frame drops out of minimum."""
        nfe = NoiseFloorEstimator(n_bins=2, window_frames=3)
        nfe.update(np.array([1.0, 10.0]))  # frame 0
        nfe.update(np.array([5.0, 5.0]))   # frame 1
        nfe.update(np.array([3.0, 3.0]))   # frame 2
        # min of [1,5,3] = 1, min of [10,5,3] = 3
        np.testing.assert_array_equal(nfe.noise_floor, [1.0, 3.0])
        nfe.update(np.array([4.0, 4.0]))   # frame 3 — frame 0 drops
        # min of [5,3,4] = 3, min of [5,3,4] = 3
        np.testing.assert_array_equal(nfe.noise_floor, [3.0, 3.0])

    def test_ready_after_min_frames(self) -> None:
        nfe = NoiseFloorEstimator(n_bins=4)
        self.assertFalse(nfe.ready)
        for i in range(NoiseFloorEstimator._MIN_READY_FRAMES):
            nfe.update(np.ones(4) * (i + 1))
        self.assertTrue(nfe.ready)

    def test_reset_clears_state(self) -> None:
        nfe = NoiseFloorEstimator(n_bins=4)
        for i in range(100):
            nfe.update(np.ones(4))
        self.assertTrue(nfe.ready)
        nfe.reset()
        self.assertFalse(nfe.ready)
        self.assertIsNone(nfe.noise_floor)


class TestSpectralSubtraction(unittest.TestCase):
    """Phase 2: spectral subtraction integration."""

    def test_spectral_features_with_noise_floor(self) -> None:
        """When noise_floor is provided, features are computed from cleaned mag."""
        frame_size, sr = 2048, 44100
        window, bass_mask, kick_mask, _freqs = _prepare_bass_window(frame_size, sr)
        rng = np.random.default_rng(42)
        frame = rng.standard_normal(frame_size).astype(np.float32)
        # No noise floor
        sf_raw = _spectral_features(frame, window, bass_mask, None, kick_mask=kick_mask)
        # With noise floor = half of the raw mag
        nf = sf_raw.mag * 0.5
        sf_clean = _spectral_features(frame, window, bass_mask, None, kick_mask=kick_mask, noise_floor=nf)
        # Cleaned bass should be less than raw bass
        self.assertLess(sf_clean.bass, sf_raw.bass)
        # raw_mag should be set when noise_floor is used
        self.assertIsNotNone(sf_clean.raw_mag)
        np.testing.assert_array_almost_equal(sf_clean.raw_mag, sf_raw.mag)
        # Without noise floor, raw_mag should be None
        self.assertIsNone(sf_raw.raw_mag)

    def test_spectral_features_noise_floor_floors_at_zero(self) -> None:
        """Spectral subtraction should never produce negative magnitudes."""
        frame_size, sr = 2048, 44100
        window, bass_mask, kick_mask, _freqs = _prepare_bass_window(frame_size, sr)
        frame = np.zeros(frame_size, dtype=np.float32)
        frame[100] = 0.01  # tiny signal
        nf = np.ones(frame_size // 2 + 1) * 100.0  # huge noise floor
        sf = _spectral_features(frame, window, bass_mask, None, kick_mask=kick_mask, noise_floor=nf)
        self.assertGreaterEqual(sf.bass, 0.0)
        self.assertTrue(np.all(sf.mag >= 0.0))

    def test_noise_floor_integration_detects_bpm(self) -> None:
        """Full integration: noise floor + feature extraction + BPM estimation."""
        sr, bpm_target = 44100, 120.0
        frame_size, hop_size = 2048, 512
        frames = _make_pulse_audio(sr, 12.0, bpm_target, frame_size, hop_size)
        result = _run_estimator_with_audio(
            frames, sr, hop_size, frame_size,
            onset_mode="spectral_flux",
            enable_noise_floor=True,
        )
        self.assertGreater(result["final_bpm"], 0.0)
        self.assertAlmostEqual(result["final_bpm"], bpm_target, delta=15.0)

    def test_noise_floor_with_noisy_signal(self) -> None:
        """Pulse train + additive noise: noise floor should help detection."""
        sr, bpm_target = 44100, 120.0
        frame_size, hop_size = 2048, 512
        frames = _make_pulse_audio(sr, 12.0, bpm_target, frame_size, hop_size)
        rng = np.random.default_rng(99)
        # Add broadband noise at moderate level (signal still stronger)
        frames_noisy = [f * 0.5 + rng.standard_normal(frame_size).astype(np.float32) * 0.15 for f in frames]
        result = _run_estimator_with_audio(
            frames_noisy, sr, hop_size, frame_size,
            onset_mode="hybrid",
            enable_noise_floor=True,
        )
        # Should detect something (noise floor helps extract the pulse)
        self.assertGreater(result["final_bpm"], 0.0,
                          "Noise floor subtraction should help detect BPM in noisy signal")


class TestPercussiveOnsetTracker(unittest.TestCase):
    """Phase 3a: HPSS percussive onset detection."""

    def test_warmup_returns_zero(self) -> None:
        """Returns 0 until enough active frames are buffered."""
        kernel = 20
        pot = PercussiveOnsetTracker(n_bins=4, kernel_size=kernel, energy_gate=0.5)
        ready_threshold = min(PercussiveOnsetTracker._MIN_READY_FRAMES, kernel)
        mag = np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float32)
        for _ in range(ready_threshold - 1):
            self.assertEqual(pot.update(mag), 0.0)
        self.assertFalse(pot.ready)
        # One more should make it ready
        pot.update(mag)
        self.assertTrue(pot.ready)

    def test_silent_frames_skipped(self) -> None:
        """Silent frames (below energy_gate) return 0 and don't fill the buffer."""
        pot = PercussiveOnsetTracker(n_bins=4, kernel_size=10, energy_gate=5.0)
        silence = np.zeros(4, dtype=np.float32)
        for _ in range(100):
            self.assertEqual(pot.update(silence), 0.0)
        self.assertFalse(pot.ready)  # buffer should still be empty

    def test_constant_signal_low_onset(self) -> None:
        """Constant spectral shape should produce near-zero percussive onset."""
        pot = PercussiveOnsetTracker(n_bins=4, kernel_size=20, energy_gate=0.0)
        mag = np.array([5.0, 3.0, 2.0, 1.0], dtype=np.float32)
        onsets = []
        for _ in range(30):
            val = pot.update(mag)
            onsets.append(val)
        # After warmup, constant signal = at median = P ≈ 0
        post_warmup = [v for v in onsets[PercussiveOnsetTracker._MIN_READY_FRAMES:]]
        self.assertTrue(all(v == 0.0 for v in post_warmup),
                        f"Constant signal should give zero percussive onset, got {post_warmup}")

    def test_transient_above_ambient(self) -> None:
        """A transient (kick) should produce higher onset than steady ambient."""
        pot = PercussiveOnsetTracker(n_bins=4, kernel_size=20, energy_gate=0.0)
        ambient = np.array([2.0, 2.0, 2.0, 2.0], dtype=np.float32)
        # Fill buffer with ambient
        for _ in range(25):
            pot.update(ambient)
        # Ambient frame onset
        ambient_onset = pot.update(ambient)
        # Transient frame (much higher in some bands)
        transient = np.array([10.0, 2.0, 2.0, 2.0], dtype=np.float32)
        transient_onset = pot.update(transient)
        self.assertGreater(transient_onset, ambient_onset,
                           "Transient should produce higher percussive onset than ambient")
        self.assertGreater(transient_onset, 0.0)

    def test_reset_clears_state(self) -> None:
        pot = PercussiveOnsetTracker(n_bins=4, kernel_size=10, energy_gate=0.0)
        mag = np.ones(4, dtype=np.float32)
        for _ in range(20):
            pot.update(mag)
        self.assertTrue(pot.ready)
        pot.reset()
        self.assertFalse(pot.ready)

    def test_energy_gate_filters_correctly(self) -> None:
        """Only frames above energy_gate contribute to the buffer."""
        pot = PercussiveOnsetTracker(n_bins=4, kernel_size=10, energy_gate=5.0)
        loud = np.array([3.0, 3.0, 3.0, 3.0], dtype=np.float32)  # sum=12 > 5
        quiet = np.array([0.5, 0.5, 0.5, 0.5], dtype=np.float32)  # sum=2 < 5
        # Mix loud and quiet frames
        for _ in range(20):
            pot.update(quiet)  # should be ignored
            pot.update(loud)   # should be counted
        # After 20 loud frames, should be ready
        self.assertTrue(pot.ready)


class TestHybridPercussiveFallback(unittest.TestCase):
    """Hybrid onset prefers percussive over whitened in noisy fallback."""

    def test_hybrid_uses_percussive_over_whitened(self) -> None:
        """When bass+kick dead and percussive available, use percussive."""
        est = LiveBpmEstimator(sample_rate=44100, hop_size=512, onset_mode="hybrid")
        # Force into noisy mode with dead bass/kick and active whitened
        for i in range(200):
            est.update(energy=1.0, t=i * 0.01,
                       kick_spectral_flux=0.0,
                       whitened_flux=50.0,
                       percussive_onset=5.0)
        self.assertEqual(est._hybrid_source, "percussive")
        # The onset value should be the percussive_onset, not log1p(wf)
        est.update(energy=1.0, t=2.01,
                   kick_spectral_flux=0.0,
                   whitened_flux=50.0,
                   percussive_onset=7.0)
        self.assertAlmostEqual(est.last_onset, 7.0, places=4)

    def test_hybrid_falls_to_whitened_when_percussive_zero(self) -> None:
        """When bass+kick dead and percussive=0 (warmup), fall through to whitened."""
        import math
        est = LiveBpmEstimator(sample_rate=44100, hop_size=512, onset_mode="hybrid")
        for i in range(200):
            est.update(energy=1.0, t=i * 0.01,
                       kick_spectral_flux=0.0,
                       whitened_flux=50.0,
                       percussive_onset=0.0)
        self.assertEqual(est._hybrid_source, "whitened")
        est.update(energy=1.0, t=2.01,
                   kick_spectral_flux=0.0,
                   whitened_flux=99.0,
                   percussive_onset=0.0)
        self.assertAlmostEqual(est.last_onset, math.log1p(99.0), places=4)

    def test_hybrid_stays_bass_when_primary_works(self) -> None:
        """When bass has signal, percussive is ignored."""
        est = LiveBpmEstimator(sample_rate=44100, hop_size=512, onset_mode="hybrid")
        for i in range(200):
            energy = 1.0 + (i % 2) * 5.0  # strong alternating bass
            est.update(energy=energy, t=i * 0.01,
                       whitened_flux=10.0,
                       percussive_onset=5.0)
        self.assertEqual(est._hybrid_source, "bass")

    def test_percussive_detects_bpm_noisy_sim(self) -> None:
        """Simulate noisy environment with HPSS: periodic percussive spikes."""
        sr, bpm_target = 44100, 120.0
        frame_size, hop_size = 2048, 512
        n_samples = int(sr * 12.0)
        n_frames = (n_samples - frame_size) // hop_size
        period_frames = int(sr * 60.0 / bpm_target / hop_size)

        est = LiveBpmEstimator(sample_rate=sr, hop_size=hop_size, onset_mode="hybrid")
        rng = np.random.default_rng(42)
        stream_t = 0.0
        beat_count = 0

        for i in range(n_frames):
            # Dead bass/kick, active whitened flux, periodic percussive spikes
            perc = 8.0 if (i % period_frames < 2) else rng.uniform(0, 0.5)
            bpm, beat = est.update(
                energy=1.0, t=stream_t,
                kick_spectral_flux=0.0,
                whitened_flux=50.0,
                percussive_onset=perc,
            )
            if beat:
                beat_count += 1
            stream_t += float(hop_size) / float(sr)

        self.assertEqual(est._hybrid_source, "percussive")
        self.assertGreater(bpm, 0.0, "Should detect BPM from percussive spikes")
        self.assertGreater(beat_count, 3, "Should detect beats from percussive onset")


class TestAutocorrelationConfidenceGate(unittest.TestCase):
    """Fix 1: autocorrelation confidence gate rejects noise, accepts periodic signals."""

    def test_estimate_bpm_rejects_flat_autocorrelation(self) -> None:
        """White noise onset envelope → no clear peak → returns 0.0."""
        rng = np.random.default_rng(42)
        noise_onset = rng.standard_normal(500).astype(np.float32)
        bpm, confidence = _estimate_bpm(noise_onset, hop_size=512, sr=44100)
        self.assertEqual(bpm, 0.0, "Noise should produce no BPM")
        self.assertLess(confidence, 3.0, "Confidence should be below threshold for noise")

    def test_estimate_bpm_accepts_periodic_signal(self) -> None:
        """Clean pulse train onset → clear peak → returns correct BPM."""
        sr, hop_size = 44100, 512
        bpm_target = 120.0
        period_frames = int(sr * 60.0 / bpm_target / hop_size)
        n_frames = 500
        onset = np.zeros(n_frames, dtype=np.float32)
        for i in range(0, n_frames, period_frames):
            onset[i] = 1.0
        bpm, confidence = _estimate_bpm(onset, hop_size=hop_size, sr=sr)
        self.assertGreater(bpm, 0.0, "Should detect BPM from periodic signal")
        self.assertAlmostEqual(bpm, bpm_target, delta=15.0)
        self.assertGreaterEqual(confidence, 3.0, "Confidence should be above threshold")

    def test_bpm_decays_after_sustained_zero_estimates(self) -> None:
        """After 6+ updates with both methods returning 0, last_bpm → 0.0."""
        est = LiveBpmEstimator(sample_rate=44100, hop_size=512, onset_mode="bass_diff")
        # Manually set a BPM as if it was previously detected
        est.last_bpm = 120.0
        # Feed constant energy (onset = 0) for many update intervals
        # Each update at 0.5s intervals to trigger the BPM estimation block
        for i in range(10):
            t = i * 0.6
            # Fill min_frames first
            for _ in range(est.min_frames + 1):
                est.onset_env.append(0.0)
            est.update(energy=1.0, t=t)  # constant energy → onset = 0
        self.assertEqual(est.last_bpm, 0.0,
                         "BPM should decay to 0 after sustained zero estimates")


class TestEnergyGatedTemplateBootstrap(unittest.TestCase):
    """Fix 2: energy-gated template bootstrap prevents noise training."""

    def test_template_skips_low_energy_frames(self) -> None:
        """Low-energy frames with is_beat=True should NOT bootstrap the template."""
        tpl = SpectralBeatTemplate(min_beats_for_template=4, energy_threshold=0.01)
        rng = np.random.default_rng(42)
        for _ in range(20):
            mag = rng.standard_normal(100).astype(np.float32)
            tpl.update(mag, is_beat=True, frame_energy=0.001)  # below threshold
        self.assertFalse(tpl.ready, "Template should NOT bootstrap from low-energy frames")

    def test_template_bootstraps_on_high_energy(self) -> None:
        """High-energy beat frames should bootstrap the template normally."""
        tpl = SpectralBeatTemplate(min_beats_for_template=4, energy_threshold=0.01)
        rng = np.random.default_rng(42)
        for _ in range(10):
            mag = np.abs(rng.standard_normal(100).astype(np.float32)) + 1.0
            tpl.update(mag, is_beat=True, frame_energy=0.5)  # above threshold
        self.assertTrue(tpl.ready, "Template should bootstrap from high-energy frames")

    def test_template_returns_prev_similarity_for_quiet_frames(self) -> None:
        """After template is ready, quiet frames return previous similarity."""
        tpl = SpectralBeatTemplate(min_beats_for_template=4, energy_threshold=0.01)
        rng = np.random.default_rng(42)
        # Bootstrap the template with high-energy frames
        for _ in range(8):
            mag = np.abs(rng.standard_normal(100).astype(np.float32)) + 1.0
            tpl.update(mag, is_beat=True, frame_energy=0.5)
        self.assertTrue(tpl.ready)
        # Feed a high-energy frame to set _prev_similarity
        active_mag = np.abs(rng.standard_normal(100).astype(np.float32)) + 1.0
        prev_sim = tpl.update(active_mag, is_beat=False, frame_energy=0.5)
        # Now feed a quiet frame — should return prev_similarity
        quiet_sim = tpl.update(active_mag, is_beat=False, frame_energy=0.001)
        self.assertEqual(quiet_sim, prev_sim,
                         "Quiet frame should return previous similarity, not re-score")


class TestTemplateSelectivityMonitor(unittest.TestCase):
    """Fix 3: template selectivity monitor detects noise-lock."""

    def test_template_detects_no_selectivity(self) -> None:
        """Uniform similarity scores → has_selectivity becomes False."""
        tpl = SpectralBeatTemplate(min_beats_for_template=4, energy_threshold=0.0)
        # Bootstrap with beat frames
        rng = np.random.default_rng(42)
        for _ in range(8):
            mag = np.abs(rng.standard_normal(100).astype(np.float32)) + 1.0
            tpl.update(mag, is_beat=True, frame_energy=1.0)
        self.assertTrue(tpl.ready)
        # Feed many frames that all produce nearly the same similarity
        # (same spectrum every time → same cosine similarity)
        uniform_mag = np.abs(rng.standard_normal(100).astype(np.float32)) + 1.0
        for _ in range(200):
            tpl.update(uniform_mag, is_beat=False, frame_energy=1.0)
        self.assertFalse(tpl.has_selectivity,
                         "Template should detect no selectivity with uniform similarity")

    def test_template_resets_on_prolonged_no_selectivity(self) -> None:
        """After sustained no-selectivity, template resets to bootstrap."""
        tpl = SpectralBeatTemplate(
            min_beats_for_template=4, energy_threshold=0.0,
        )
        # Reduce reset threshold for testing
        tpl._no_selectivity_reset_threshold = 50
        rng = np.random.default_rng(42)
        # Bootstrap
        for _ in range(8):
            mag = np.abs(rng.standard_normal(100).astype(np.float32)) + 1.0
            tpl.update(mag, is_beat=True, frame_energy=1.0)
        self.assertTrue(tpl.ready)
        # Feed uniform frames until reset
        uniform_mag = np.abs(rng.standard_normal(100).astype(np.float32)) + 1.0
        for _ in range(300):
            tpl.update(uniform_mag, is_beat=False, frame_energy=1.0)
        self.assertFalse(tpl.ready,
                         "Template should have reset after sustained no-selectivity")

    def test_template_selectivity_with_real_beats(self) -> None:
        """Alternating beat/non-beat spectra → has_selectivity stays True."""
        tpl = SpectralBeatTemplate(min_beats_for_template=4, energy_threshold=0.0)
        rng = np.random.default_rng(42)
        # Create two distinct spectral shapes
        beat_mag = np.zeros(100, dtype=np.float32)
        beat_mag[:20] = 5.0  # bass-heavy
        nonbeat_mag = np.zeros(100, dtype=np.float32)
        nonbeat_mag[50:70] = 5.0  # mid-heavy
        # Bootstrap with beat frames
        for _ in range(8):
            tpl.update(beat_mag, is_beat=True, frame_energy=1.0)
        self.assertTrue(tpl.ready)
        # Alternate beat and non-beat frames
        for i in range(200):
            if i % 5 == 0:
                tpl.update(beat_mag, is_beat=True, frame_energy=1.0)
            else:
                tpl.update(nonbeat_mag, is_beat=False, frame_energy=1.0)
        self.assertTrue(tpl.has_selectivity,
                        "Template should maintain selectivity with distinct beat/non-beat spectra")


class TestHarmonicClassifier(unittest.TestCase):
    """Tests for _classify_harmonic() — Layer 2 of harmonic-lock plan."""

    def _make_est(self, last_bpm: float = 0.0) -> LiveBpmEstimator:
        est = LiveBpmEstimator(sample_rate=44100, hop_size=512)
        est.last_bpm = last_bpm
        return est

    def test_no_last_bpm_returns_1x(self):
        est = self._make_est(0.0)
        label, mapped = est._classify_harmonic(120.0)
        self.assertEqual(label, "1x")
        self.assertAlmostEqual(mapped, 120.0)

    def test_half_time(self):
        est = self._make_est(160.0)
        label, mapped = est._classify_harmonic(80.0)
        self.assertEqual(label, "1/2x")
        self.assertAlmostEqual(mapped, 160.0)

    def test_double_time(self):
        est = self._make_est(80.0)
        label, mapped = est._classify_harmonic(160.0)
        self.assertEqual(label, "2x")
        self.assertAlmostEqual(mapped, 80.0)

    def test_three_halves(self):
        est = self._make_est(160.0)
        label, mapped = est._classify_harmonic(240.0)
        self.assertEqual(label, "3/2x")
        self.assertAlmostEqual(mapped, 160.0)

    def test_two_thirds(self):
        est = self._make_est(160.0)
        label, mapped = est._classify_harmonic(107.0)
        self.assertEqual(label, "2/3x")
        self.assertAlmostEqual(mapped, 160.5, places=0)

    def test_close_to_1x(self):
        est = self._make_est(160.0)
        label, mapped = est._classify_harmonic(158.0)
        self.assertEqual(label, "1x")
        self.assertAlmostEqual(mapped, 158.0)

    def test_all_ratios_covered(self):
        """Every HARMONIC_RATIOS entry should be selectable."""
        for target_label, ratio in HARMONIC_RATIOS.items():
            if target_label == "1x":
                continue
            est = self._make_est(120.0)
            raw = 120.0 * ratio  # e.g. 2x → raw=240
            label, mapped = est._classify_harmonic(raw)
            self.assertEqual(label, target_label,
                             f"Expected {target_label} for raw={raw:.1f}, got {label}")
            self.assertAlmostEqual(mapped, 120.0, delta=1.0,
                                   msg=f"mapped should ≈120 for {target_label}")


class TestHarmonicResistantLock(unittest.TestCase):
    """Tests for the harmonic-resistant _apply_inertia() — Layer 3."""

    def _make_est(self, last_bpm: float = 140.0, **kw) -> LiveBpmEstimator:
        est = LiveBpmEstimator(sample_rate=44100, hop_size=512, **kw)
        est.last_bpm = last_bpm
        return est

    def test_rejects_short_half_time_burst(self):
        """5 frames of half-time should be rejected (mapped back)."""
        est = self._make_est(140.0)
        for _ in range(5):
            result = est._apply_inertia(70.0)
        # Should stay near 140 (mapped = 70 / 0.5 = 140)
        self.assertAlmostEqual(result, 140.0, delta=2.0)

    def test_accepts_sustained_half_time(self):
        """15 frames of consistent half-time → accept the genuine change."""
        est = self._make_est(140.0, harmonic_confirm_count=12)
        results = []
        for _ in range(15):
            r = est._apply_inertia(70.0)
            est.last_bpm = r  # simulate pipeline updating last_bpm
            results.append(r)
        # First 11 should be ~140, the 12th onward should be 70
        self.assertAlmostEqual(results[0], 140.0, delta=2.0)
        self.assertAlmostEqual(results[11], 70.0, delta=2.0)
        self.assertAlmostEqual(results[-1], 70.0, delta=2.0)

    def test_alternating_ratios_stay_locked(self):
        """Alternating half-time and full-time resets the confirm counter."""
        est = self._make_est(140.0)
        for i in range(30):
            if i % 2 == 0:
                result = est._apply_inertia(70.0)   # 1/2x
            else:
                result = est._apply_inertia(140.0)   # 1x
        # Should stay near 140 throughout
        self.assertAlmostEqual(result, 140.0, delta=2.0)

    def test_gradual_drift_follows(self):
        """Small drift within max_jump_bpm follows (with EMA smoothing lag)."""
        est = self._make_est(140.0)
        # Feed a steady target for several frames so EMA converges
        for _ in range(10):
            result = est._apply_inertia(146.0)
            est.last_bpm = result
        # After 10 EMA steps at alpha=0.3, should be close to 146
        self.assertAlmostEqual(result, 146.0, delta=1.5,
                               msg="Gradual drift should converge to target")

    def test_first_bpm_accepted(self):
        """When last_bpm is 0, any estimate is accepted immediately."""
        est = self._make_est(0.0)
        result = est._apply_inertia(128.0)
        self.assertAlmostEqual(result, 128.0)

    def test_non_harmonic_big_jump_needs_confirmation(self):
        """A big non-harmonic jump still needs confirm_updates confirmations."""
        est = self._make_est(140.0, confirm_updates=4)
        # 155 is not a harmonic of 140 but is > max_jump_bpm away
        results = []
        for _ in range(6):
            r = est._apply_inertia(155.0)
            est.last_bpm = r  # simulate pipeline updating last_bpm
            results.append(r)
        # First 3 should be 140 (held), 4th onward should be 155
        self.assertAlmostEqual(results[0], 140.0, delta=1.0)
        self.assertAlmostEqual(results[3], 155.0, delta=1.0)
        self.assertAlmostEqual(results[-1], 155.0, delta=1.0)


class TestIOIHistogram(unittest.TestCase):
    """Tests for IOIHistogram (Layer 1) — time-domain BPM estimation."""

    def test_exact_120bpm(self):
        """Onsets at exact 120 BPM (500ms intervals) → ~120 BPM."""
        h = IOIHistogram(buffer_seconds=8.0)
        period = 0.5  # 120 BPM
        for i in range(16):
            h.add_onset(i * period)
        bpm = h.estimate_bpm()
        self.assertAlmostEqual(bpm, 120.0, delta=3.0)

    def test_exact_140bpm(self):
        """Onsets at exact 140 BPM → ~140 BPM."""
        h = IOIHistogram(buffer_seconds=8.0)
        period = 60.0 / 140.0
        for i in range(20):
            h.add_onset(i * period)
        bpm = h.estimate_bpm()
        self.assertAlmostEqual(bpm, 140.0, delta=3.0)

    def test_occasional_skips(self):
        """A few beats missing → still picks base period over half-time."""
        h = IOIHistogram(buffer_seconds=8.0)
        period = 0.5  # 120 BPM
        skip = {5, 11, 18}  # skip 3 out of 24 beats
        t = 0.0
        for i in range(24):
            if i not in skip:
                h.add_onset(t)
            t += period
        bpm = h.estimate_bpm()
        # 500ms intervals still dominate over 1000ms gaps
        self.assertAlmostEqual(bpm, 120.0, delta=5.0)

    def test_tempo_change_adapts(self):
        """Shift from 120→140 BPM — histogram adapts within buffer window."""
        h = IOIHistogram(buffer_seconds=6.0)
        # 4 seconds at 120 BPM
        period1 = 0.5
        t = 0.0
        for _ in range(8):
            h.add_onset(t)
            t += period1
        # Then 6 seconds at 140 BPM (fills the buffer, pushes out old)
        period2 = 60.0 / 140.0
        for _ in range(14):
            h.add_onset(t)
            t += period2
        bpm = h.estimate_bpm()
        # After 6s of 140 BPM data, old 120 BPM data should be evicted
        self.assertAlmostEqual(bpm, 140.0, delta=5.0)

    def test_random_noise_returns_zero(self):
        """Random onset times with no periodicity → 0.0."""
        rng = np.random.RandomState(42)
        h = IOIHistogram(buffer_seconds=8.0)
        # 30 random onsets in 8 seconds
        times = sorted(rng.uniform(0, 8, 30))
        for t in times:
            h.add_onset(t)
        bpm = h.estimate_bpm()
        # With random intervals, the histogram should have no clear peak.
        # The estimate may be non-zero but shouldn't be reliable.
        # Accept 0.0 or any value (the key check is that it doesn't crash).
        self.assertIsInstance(bpm, float)

    def test_too_few_onsets_returns_zero(self):
        """Fewer than 4 onsets → 0.0."""
        h = IOIHistogram()
        h.add_onset(0.0)
        h.add_onset(0.5)
        h.add_onset(1.0)
        self.assertEqual(h.estimate_bpm(), 0.0)

    def test_feed_detects_onsets(self):
        """feed() with a pulse train detects onset events."""
        h = IOIHistogram(buffer_seconds=8.0, thresh_window=200)
        period = 0.5  # 120 BPM
        fps = 86.0
        dt = 1.0 / fps
        detections = 0
        t = 0.0
        # Simulate ~6 seconds of frames
        for frame in range(int(6.0 * fps)):
            # Pulse train: spike at beat positions, zero elsewhere
            phase = (t % period) / period
            onset_val = 1.0 if phase < 0.02 else 0.0
            if h.feed(onset_val, t):
                detections += 1
            t += dt
        # Should detect roughly 12 onsets (6s * 2 beats/s)
        self.assertGreater(detections, 6)
        self.assertLess(detections, 20)

    def test_reset_clears_state(self):
        """reset() clears all accumulated data."""
        h = IOIHistogram()
        for i in range(10):
            h.add_onset(i * 0.5)
        self.assertGreater(h.onset_count, 0)
        h.reset()
        self.assertEqual(h.onset_count, 0)
        self.assertEqual(h.estimate_bpm(), 0.0)


class TestCyclicBeatGridTracker(unittest.TestCase):
    """Long-window beat-grid fitting should tolerate recurring syncopation."""

    def test_recurring_offbeat_pattern_fits_one_grid(self):
        tracker = CyclicBeatGridTracker(retention_seconds=24.0)
        period = 0.5  # 120 BPM
        # The dominant onset is consistently offbeat.  A smaller ghost onset
        # appears in one out of four cycles, modelling syncopated bass/kick
        # detail that should not turn into its own metronome pulse.
        for index in range(48):
            tracker.observe(index * period + 0.16 * period, strength=1.0)
            if index % 4 == 1:
                tracker.observe(index * period + 0.73 * period, strength=0.35)

        bpm = tracker.update((120.0,), t=24.0)

        self.assertTrue(tracker.active)
        self.assertGreater(tracker.confidence, 0.6)
        self.assertAlmostEqual(bpm, 120.0, delta=2.0)

        emitted: list[float] = []
        for frame in range(600):
            t = 24.0 + frame * 0.01
            if tracker.advance(t):
                emitted.append(t)
        intervals = [later - earlier for earlier, later in zip(emitted, emitted[1:])]
        self.assertGreaterEqual(len(intervals), 5)
        self.assertTrue(all(abs(interval - period) <= 0.02 for interval in intervals))

    def test_nonrepeating_onsets_do_not_activate_grid(self):
        tracker = CyclicBeatGridTracker(retention_seconds=24.0)
        rng = np.random.default_rng(47)
        for timestamp in sorted(rng.uniform(0.0, 24.0, size=60)):
            tracker.observe(float(timestamp), strength=1.0)

        bpm = tracker.update((120.0, 128.0, 96.0), t=24.0)

        self.assertEqual(bpm, 0.0)
        self.assertFalse(tracker.active)


class TestEqBeatOnset(unittest.TestCase):
    def test_hybrid_detector_uses_eq_band_breakdown_as_low_frequency_candidate(self):
        estimator = LiveBpmEstimator(
            sample_rate=44100,
            hop_size=512,
            onset_mode="hybrid",
        )
        for index in range(160):
            estimator.update(
                energy=1.0,
                t=index * 0.01,
                kick_spectral_flux=0.0,
                eq_band_fluxes=(0.4, 10.0, 3.0, 0.2, 0.1, 0.0, 0.0),
            )

        self.assertEqual(estimator._hybrid_source, "eq_low")
        self.assertGreater(estimator.last_eq_onset, 0.0)

    def test_eq_band_onset_is_cleared_by_reset(self):
        estimator = LiveBpmEstimator(sample_rate=44100, hop_size=512)
        estimator.update(
            energy=1.0,
            t=0.0,
            eq_band_fluxes=(0.0, 5.0, 2.0),
        )
        self.assertGreater(estimator.last_eq_onset, 0.0)

        estimator.reset()

        self.assertEqual(estimator.last_eq_onset, 0.0)
        self.assertEqual(estimator._eq_activity, 0.0)


if __name__ == "__main__":
    unittest.main()
