"""Offline Feature Pipeline — run the full live-quality feature chain on a decoded PCM signal."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from dreamsync.director import Director
from dreamsync.live import (
    LiveBpmEstimator,
    NoiseFloorEstimator,
    PercussiveOnsetTracker,
    _compute_whitened_flux,
    _feature_row_from_frame,
    _prepare_bass_window,
    _spectral_features,
)
from dreamsync.mood import MoodClassifier


@dataclass(frozen=True)
class FeatureRow:
    t: float
    rms: float
    zcr: float
    centroid: float
    bass_ratio: float
    spectral_flux: float
    kick_spectral_flux: float
    onset_strength: float
    energy: float           # Director's composite energy
    bpm: float              # frame-level BPM estimate
    beat: bool
    mood: str               # "chill" | "groove" | "hype" | "drop"


class OfflineFeaturePipeline:
    """Run the full live-quality feature extraction chain offline.

    Instantiates the same analysis classes used in run_live_to_govee()
    and feeds an entire PCM signal frame-by-frame.
    """

    def __init__(
        self,
        sample_rate: int = 44100,
        frame_size: int = 2048,
        hop_size: int = 512,
    ) -> None:
        self.sample_rate = sample_rate
        self.frame_size = frame_size
        self.hop_size = hop_size

    def extract(self, signal: np.ndarray) -> list[FeatureRow]:
        """Run the full analysis pipeline on a mono float32 PCM signal.

        Returns a list of FeatureRow, one per frame.
        """
        sr = self.sample_rate
        frame_size = self.frame_size
        hop_size = self.hop_size

        # Pre-compute DSP helpers
        window, bass_mask, kick_mask, freqs = _prepare_bass_window(frame_size, sr)
        n_bins = frame_size // 2 + 1
        perc_freq_mask = freqs <= 300.0

        # Instantiate fresh analysis objects
        bpm_estimator = LiveBpmEstimator(sample_rate=sr, hop_size=hop_size)
        noise_estimator = NoiseFloorEstimator(n_bins=n_bins)
        percussive_tracker = PercussiveOnsetTracker(
            n_bins=n_bins, energy_gate=1.0, freq_mask=perc_freq_mask,
        )
        director = Director()
        mood_classifier = MoodClassifier()

        # Seed director with a silent frame
        director.update({
            "t": 0.0, "rms": 0.0, "zcr": 0.0, "bpm": 120.0,
            "beat": False, "bass": 0.0,
        })

        # State
        prev_mag: np.ndarray | None = None
        spectral_mean: np.ndarray | None = None
        prev_whitened_mag: np.ndarray | None = None
        stream_t = 0.0
        rows: list[FeatureRow] = []

        # Frame the signal and process
        n_samples = len(signal)
        pos = 0
        while pos + frame_size <= n_samples:
            frame = signal[pos : pos + frame_size]
            rms = float(np.sqrt(np.mean(frame ** 2)))

            # Noise floor
            nf = noise_estimator.noise_floor if noise_estimator.ready else None
            sf = _spectral_features(
                frame, window, bass_mask, prev_mag,
                kick_mask=kick_mask, freqs=freqs, noise_floor=nf,
            )
            raw_mag = sf.raw_mag if sf.raw_mag is not None else sf.mag
            noise_estimator.update(raw_mag)
            prev_mag = sf.mag

            # Whitened flux
            wf, spectral_mean, prev_whitened_mag = _compute_whitened_flux(
                raw_mag, spectral_mean, prev_whitened_mag, energy_gate=1.0,
            )

            # Percussive onset
            perc = percussive_tracker.update(raw_mag)

            # BPM and beat
            bpm, beat = bpm_estimator.update(
                sf.bass, stream_t,
                spectral_flux=sf.spectral_flux,
                kick_spectral_flux=sf.kick_spectral_flux,
                whitened_flux=wf,
                percussive_onset=perc,
                mag=sf.mag,
            )

            # Feature dict for Director
            features = _feature_row_from_frame(
                frame, rms, stream_t, bpm, beat,
                bass=sf.bass,
                bass_ratio=sf.bass_ratio,
                spectral_flux=sf.spectral_flux,
                onset_strength=bpm_estimator.last_onset,
                centroid=sf.centroid,
            )

            # Director update → energy
            director.update(features)

            # Mood update
            mood = mood_classifier.update(
                director.energy, director.stability,
                director.effective_bpm, stream_t,
            )

            rows.append(FeatureRow(
                t=stream_t,
                rms=rms,
                zcr=features["zcr"],
                centroid=sf.centroid,
                bass_ratio=sf.bass_ratio,
                spectral_flux=sf.spectral_flux,
                kick_spectral_flux=sf.kick_spectral_flux,
                onset_strength=bpm_estimator.last_onset,
                energy=director.energy,
                bpm=bpm,
                beat=beat,
                mood=mood.value,
            ))

            stream_t += hop_size / sr
            pos += hop_size

        return rows
