"""Tests for the OfflineFeaturePipeline (D4.2)."""

from __future__ import annotations

import numpy as np
import pytest

from dreamsync.analyzer.features import FeatureRow, OfflineFeaturePipeline
from dreamsync.live import EQ_BAND_NAMES, _prepare_bass_window, _spectral_features


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sine_signal(freq: float = 440.0, duration: float = 2.0, sr: int = 44100) -> np.ndarray:
    """Generate a mono sine wave at *freq* Hz."""
    t = np.linspace(0, duration, int(sr * duration), dtype=np.float32)
    return 0.5 * np.sin(2 * np.pi * freq * t).astype(np.float32)


def _silence(duration: float = 1.0, sr: int = 44100) -> np.ndarray:
    """Generate silence."""
    return np.zeros(int(sr * duration), dtype=np.float32)


def _noise(duration: float = 2.0, sr: int = 44100, amplitude: float = 0.3) -> np.ndarray:
    """Generate white noise."""
    rng = np.random.default_rng(42)
    return (amplitude * rng.standard_normal(int(sr * duration))).astype(np.float32)


def _stereo_signal(
    left_scale: float = 0.8,
    right_scale: float = 0.2,
    duration: float = 2.0,
    sr: int = 44100,
) -> np.ndarray:
    t = np.linspace(0, duration, int(sr * duration), dtype=np.float32)
    left = left_scale * np.sin(2 * np.pi * 220 * t)
    right = right_scale * np.sin(2 * np.pi * 880 * t)
    return np.stack((left, right), axis=1).astype(np.float32)


def _click_track(bpm: float = 120.0, duration: float = 10.0, sr: int = 44100) -> np.ndarray:
    """Generate a click track at the given BPM."""
    n_samples = int(sr * duration)
    signal = np.zeros(n_samples, dtype=np.float32)
    beat_interval = 60.0 / bpm
    click_len = int(sr * 0.01)  # 10ms click
    click = np.ones(click_len, dtype=np.float32) * 0.8

    t = 0.0
    while t < duration:
        idx = int(t * sr)
        end = min(idx + click_len, n_samples)
        signal[idx:end] = click[:end - idx]
        t += beat_interval
    return signal


def _frame_sine(freq: float, frame_size: int = 2048, sr: int = 44100) -> np.ndarray:
    """Generate one FFT-sized sine frame."""
    t = np.arange(frame_size, dtype=np.float32) / sr
    return 0.5 * np.sin(2 * np.pi * freq * t).astype(np.float32)


# ---------------------------------------------------------------------------
# FeatureRow dataclass
# ---------------------------------------------------------------------------

class TestFeatureRow:
    def test_frozen(self):
        row = FeatureRow(
            t=0.0, rms=0.1, zcr=0.05, centroid=2000.0,
            bass_ratio=0.3, spectral_flux=0.5, kick_spectral_flux=0.1,
            onset_strength=0.2, energy=0.4, bpm=120.0, beat=True, mood="groove",
        )
        with pytest.raises(AttributeError):
            row.bpm = 130.0  # type: ignore[misc]

    def test_fields(self):
        row = FeatureRow(
            t=1.5, rms=0.2, zcr=0.1, centroid=3000.0,
            bass_ratio=0.4, spectral_flux=0.6, kick_spectral_flux=0.2,
            onset_strength=0.3, energy=0.5, bpm=128.0, beat=False, mood="chill",
        )
        assert row.t == 1.5
        assert row.mood == "chill"
        assert row.beat is False
        assert row.pan_center == 0.0
        assert row.pan_width == 0.0
        assert len(row.band_energies) == len(EQ_BAND_NAMES)
        assert len(row.band_ratios) == len(EQ_BAND_NAMES)
        assert len(row.band_fluxes) == len(EQ_BAND_NAMES)
        assert len(row.band_pan_centers) == len(EQ_BAND_NAMES)


# ---------------------------------------------------------------------------
# OfflineFeaturePipeline — basic operation
# ---------------------------------------------------------------------------

class TestOfflineFeaturePipeline:
    def test_extract_returns_feature_rows(self):
        signal = _sine_signal(freq=440, duration=1.0)
        pipeline = OfflineFeaturePipeline()
        rows = pipeline.extract(signal)
        assert len(rows) > 0
        assert all(isinstance(r, FeatureRow) for r in rows)

    def test_timestamps_are_monotonic(self):
        signal = _sine_signal(duration=2.0)
        pipeline = OfflineFeaturePipeline()
        rows = pipeline.extract(signal)
        times = [r.t for r in rows]
        for i in range(1, len(times)):
            assert times[i] > times[i - 1]

    def test_timestamp_accuracy(self):
        """Frame timestamps should be frame_index * hop_size / sample_rate."""
        sr = 44100
        hop = 512
        signal = _sine_signal(duration=1.0, sr=sr)
        pipeline = OfflineFeaturePipeline(sample_rate=sr, hop_size=hop)
        rows = pipeline.extract(signal)
        for i, row in enumerate(rows):
            expected_t = i * hop / sr
            assert abs(row.t - expected_t) < 1e-6, f"Frame {i}: {row.t} != {expected_t}"

    def test_frame_count(self):
        """Number of frames should match expected value."""
        sr = 44100
        frame_size = 2048
        hop = 512
        duration = 2.0
        signal = _sine_signal(duration=duration, sr=sr)
        pipeline = OfflineFeaturePipeline(sample_rate=sr, frame_size=frame_size, hop_size=hop)
        rows = pipeline.extract(signal)
        n_samples = len(signal)
        expected_frames = (n_samples - frame_size) // hop + 1
        assert len(rows) == expected_frames

    def test_rms_nonzero_for_signal(self):
        signal = _sine_signal(freq=440, duration=1.0)
        pipeline = OfflineFeaturePipeline()
        rows = pipeline.extract(signal)
        rms_values = [r.rms for r in rows]
        assert max(rms_values) > 0.1

    def test_rms_near_zero_for_silence(self):
        signal = _silence(duration=1.0)
        pipeline = OfflineFeaturePipeline()
        rows = pipeline.extract(signal)
        rms_values = [r.rms for r in rows]
        assert max(rms_values) < 0.001

    def test_energy_populated(self):
        signal = _sine_signal(freq=440, duration=2.0)
        pipeline = OfflineFeaturePipeline()
        rows = pipeline.extract(signal)
        # Energy should be > 0 for at least some frames after warmup
        energies = [r.energy for r in rows[10:]]  # skip first few warmup frames
        assert max(energies) > 0

    def test_mood_populated(self):
        signal = _sine_signal(freq=440, duration=2.0)
        pipeline = OfflineFeaturePipeline()
        rows = pipeline.extract(signal)
        moods = {r.mood for r in rows}
        # Should have at least one mood
        assert len(moods) >= 1
        # All moods should be valid
        valid_moods = {"chill", "groove", "hype", "drop"}
        assert moods.issubset(valid_moods)

    def test_spectral_features_populated(self):
        signal = _sine_signal(freq=440, duration=2.0)
        pipeline = OfflineFeaturePipeline()
        rows = pipeline.extract(signal)
        # Centroid should be non-zero for a non-silent signal
        mid_rows = rows[len(rows) // 3 :]
        centroids = [r.centroid for r in mid_rows]
        avg_centroid = sum(centroids) / len(centroids)
        assert avg_centroid > 0, "centroid should be non-zero for audio"
        # Bass ratio should be less than 1 for a 440Hz sine (above bass range)
        bass_ratios = [r.bass_ratio for r in mid_rows]
        avg_bass = sum(bass_ratios) / len(bass_ratios)
        assert avg_bass < 0.5, f"440Hz sine should have low bass ratio, got {avg_bass}"

    def test_custom_parameters(self):
        signal = _sine_signal(duration=1.0)
        pipeline = OfflineFeaturePipeline(
            sample_rate=44100, frame_size=1024, hop_size=256,
        )
        rows = pipeline.extract(signal)
        assert len(rows) > 0
        # More frames with smaller frame/hop
        pipeline2 = OfflineFeaturePipeline(sample_rate=44100, frame_size=2048, hop_size=512)
        rows2 = pipeline2.extract(signal)
        assert len(rows) > len(rows2)

    def test_noise_features(self):
        """White noise should produce different features than a sine."""
        sine = _sine_signal(freq=440, duration=2.0)
        noise = _noise(duration=2.0)
        pipeline = OfflineFeaturePipeline()
        sine_rows = pipeline.extract(sine)
        noise_rows = pipeline.extract(noise)
        # Noise should have higher ZCR than a low-frequency sine
        sine_zcr = sum(r.zcr for r in sine_rows) / len(sine_rows)
        noise_zcr = sum(r.zcr for r in noise_rows) / len(noise_rows)
        assert noise_zcr > sine_zcr

    def test_stereo_signal_populates_pan_fields(self):
        signal = _stereo_signal(left_scale=0.9, right_scale=0.15, duration=2.0)
        pipeline = OfflineFeaturePipeline()
        rows = pipeline.extract(signal)
        assert len(rows) > 0
        mid_rows = rows[5:]
        avg_pan = sum(r.pan_center for r in mid_rows) / len(mid_rows)
        avg_width = sum(r.pan_width for r in mid_rows) / len(mid_rows)
        assert avg_pan < -0.2
        assert avg_width > 0.1
        assert any(abs(value) > 0.05 for value in mid_rows[0].band_pan_centers)

    def test_very_short_signal(self):
        """Signal shorter than one frame should produce empty list."""
        signal = np.zeros(100, dtype=np.float32)
        pipeline = OfflineFeaturePipeline(frame_size=2048)
        rows = pipeline.extract(signal)
        assert rows == []

    def test_click_track_detects_beats(self):
        """A click track should eventually detect beats."""
        signal = _click_track(bpm=120.0, duration=15.0)
        pipeline = OfflineFeaturePipeline()
        rows = pipeline.extract(signal)
        beats = [r for r in rows if r.beat]
        # After warmup, should detect at least some beats
        assert len(beats) > 0, "No beats detected on click track"

    def test_eq_bands_track_low_frequency_content(self):
        frame_size = 2048
        sr = 44100
        window, bass_mask, kick_mask, freqs = _prepare_bass_window(frame_size, sr)
        frame = _frame_sine(60.0, frame_size=frame_size, sr=sr)
        sf = _spectral_features(
            frame, window, bass_mask, None, kick_mask=kick_mask, freqs=freqs,
        )
        band_energy = dict(zip(EQ_BAND_NAMES, sf.band_energies))
        assert band_energy["sub"] > band_energy["presence"]
        assert band_energy["bass"] > band_energy["mid"]

    def test_eq_bands_track_presence_content(self):
        frame_size = 2048
        sr = 44100
        window, bass_mask, kick_mask, freqs = _prepare_bass_window(frame_size, sr)
        frame = _frame_sine(4000.0, frame_size=frame_size, sr=sr)
        sf = _spectral_features(
            frame, window, bass_mask, None, kick_mask=kick_mask, freqs=freqs,
        )
        band_energy = dict(zip(EQ_BAND_NAMES, sf.band_energies))
        assert band_energy["presence"] > band_energy["bass"]
        assert band_energy["presence"] > band_energy["low_mid"]

    def test_eq_band_flux_follows_band_onset(self):
        frame_size = 2048
        sr = 44100
        window, bass_mask, kick_mask, freqs = _prepare_bass_window(frame_size, sr)
        silence = np.zeros(frame_size, dtype=np.float32)
        bass_frame = _frame_sine(60.0, frame_size=frame_size, sr=sr)
        sf_silence = _spectral_features(
            silence, window, bass_mask, None, kick_mask=kick_mask, freqs=freqs,
        )
        sf_bass = _spectral_features(
            bass_frame, window, bass_mask, sf_silence.mag, kick_mask=kick_mask, freqs=freqs,
        )
        band_flux = dict(zip(EQ_BAND_NAMES, sf_bass.band_fluxes))
        assert band_flux["sub"] > band_flux["presence"]
        assert band_flux["bass"] > band_flux["mid"]
