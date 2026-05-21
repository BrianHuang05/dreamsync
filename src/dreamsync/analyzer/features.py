"""Offline Feature Pipeline — run the full live-quality feature chain on a decoded PCM signal."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from dreamsync.director import Director
from dreamsync.live import (
    EQ_BAND_NAMES,
    LiveBpmEstimator,
    NoiseFloorEstimator,
    PercussiveOnsetTracker,
    _build_eq_band_masks,
    _compute_eq_band_features,
    _compute_whitened_flux,
    _feature_row_from_frame,
    _prepare_bass_window,
    _spectral_features,
)
from dreamsync.mood import MoodClassifier

_ZERO_EQ_BANDS: tuple[float, ...] = (0.0,) * len(EQ_BAND_NAMES)


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
    mfcc: tuple[float, ...] = (0.0,) * 13    # 13 MFCC coefficients
    chroma: tuple[float, ...] = (1/12,) * 12  # 12 chroma pitch-class bins
    pan_center: float = 0.0
    pan_width: float = 0.0
    left_energy: float = 0.0
    right_energy: float = 0.0
    band_energies: tuple[float, ...] = _ZERO_EQ_BANDS
    band_ratios: tuple[float, ...] = _ZERO_EQ_BANDS
    band_fluxes: tuple[float, ...] = _ZERO_EQ_BANDS
    band_pan_centers: tuple[float, ...] = _ZERO_EQ_BANDS


# ---------------------------------------------------------------------------
# DSP helpers for MFCC and chroma (numpy-only, no librosa)
# ---------------------------------------------------------------------------

def _hz_to_mel(f: float) -> float:
    return 2595.0 * np.log10(1.0 + f / 700.0)


def _mel_to_hz(m: float) -> float:
    return 700.0 * (10.0 ** (m / 2595.0) - 1.0)


def _mel_filterbank(
    n_mels: int = 40,
    n_fft: int = 2048,
    sr: int = 44100,
    fmin: float = 20.0,
    fmax: float | None = None,
) -> np.ndarray:
    """Build a mel-scale triangular filterbank matrix: (n_mels, n_fft//2+1)."""
    if fmax is None:
        fmax = sr / 2.0
    n_bins = n_fft // 2 + 1

    mel_min = _hz_to_mel(fmin)
    mel_max = _hz_to_mel(fmax)
    mel_points = np.linspace(mel_min, mel_max, n_mels + 2)
    hz_points = np.array([_mel_to_hz(m) for m in mel_points])
    bin_indices = np.floor((n_fft + 1) * hz_points / sr).astype(int)

    filterbank = np.zeros((n_mels, n_bins), dtype=np.float64)
    for i in range(n_mels):
        left = bin_indices[i]
        center = bin_indices[i + 1]
        right = bin_indices[i + 2]
        # Rising slope
        for j in range(left, center):
            if center != left:
                filterbank[i, j] = (j - left) / (center - left)
        # Falling slope
        for j in range(center, right):
            if right != center:
                filterbank[i, j] = (right - j) / (right - center)

    return filterbank


def _dct_matrix(n_mfcc: int, n_mels: int) -> np.ndarray:
    """Precompute DCT-II matrix for MFCC: (n_mfcc, n_mels)."""
    basis = np.zeros((n_mfcc, n_mels), dtype=np.float64)
    for k in range(n_mfcc):
        for n in range(n_mels):
            basis[k, n] = np.cos(np.pi * k * (2 * n + 1) / (2 * n_mels))
    return basis


def _compute_mfcc(
    mag: np.ndarray,
    mel_fb: np.ndarray,
    dct_mat: np.ndarray,
) -> tuple[float, ...]:
    """Compute 13 MFCC coefficients from a magnitude spectrum."""
    mel_power = mel_fb @ (mag ** 2)
    log_mel = np.log(mel_power + 1e-10)
    mfcc = dct_mat @ log_mel
    return tuple(float(c) for c in mfcc)


def _compute_chroma(
    mag: np.ndarray,
    freqs: np.ndarray,
) -> tuple[float, ...]:
    """Compute 12 chroma bins from a magnitude spectrum."""
    chroma_bins = np.zeros(12, dtype=np.float64)
    for i in range(len(freqs)):
        f = freqs[i]
        if f < 20.0:
            continue
        pitch_class = int(round(12.0 * np.log2(f / 440.0))) % 12
        chroma_bins[pitch_class] += mag[i]

    mx = chroma_bins.max()
    if mx > 1e-10:
        chroma_bins /= mx
    return tuple(float(c) for c in chroma_bins)


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
        """Run the full analysis pipeline on a mono or stereo float32 PCM signal.

        Returns a list of FeatureRow, one per frame.
        """
        signal_array = np.asarray(signal, dtype=np.float32)
        if signal_array.ndim == 1:
            mono_signal = signal_array
            stereo_signal: np.ndarray | None = None
        elif signal_array.ndim == 2:
            if signal_array.shape[1] >= 2:
                stereo_signal = signal_array[:, :2]
                mono_signal = stereo_signal.mean(axis=1, dtype=np.float32)
            elif signal_array.shape[1] == 1:
                stereo_signal = None
                mono_signal = signal_array[:, 0]
            else:
                return []
        else:
            raise ValueError("signal must be 1-D mono or 2-D channel-major PCM")

        sr = self.sample_rate
        frame_size = self.frame_size
        hop_size = self.hop_size

        # Pre-compute DSP helpers
        window, bass_mask, kick_mask, freqs = _prepare_bass_window(frame_size, sr)
        n_bins = frame_size // 2 + 1
        perc_freq_mask = freqs <= 300.0
        band_masks = _build_eq_band_masks(freqs)

        # Pre-compute mel filterbank and DCT matrix for MFCC/chroma
        mel_fb = _mel_filterbank(n_mels=40, n_fft=frame_size, sr=sr)
        dct_mat = _dct_matrix(n_mfcc=13, n_mels=40)

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
        n_samples = len(mono_signal)
        pos = 0
        while pos + frame_size <= n_samples:
            frame = mono_signal[pos : pos + frame_size]
            rms = float(np.sqrt(np.mean(frame ** 2)))
            stereo_frame = stereo_signal[pos : pos + frame_size] if stereo_signal is not None else None
            pan_center, pan_width, left_energy, right_energy, band_pan_centers = _stereo_pan_features(
                stereo_frame,
                window,
                band_masks,
            )

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

            # MFCC and chroma from magnitude spectrum
            mfcc = _compute_mfcc(sf.mag, mel_fb, dct_mat)
            chroma = _compute_chroma(sf.mag, freqs)

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
                mfcc=mfcc,
                chroma=chroma,
                pan_center=pan_center,
                pan_width=pan_width,
                left_energy=left_energy,
                right_energy=right_energy,
                band_energies=sf.band_energies,
                band_ratios=sf.band_ratios,
                band_fluxes=sf.band_fluxes,
                band_pan_centers=band_pan_centers,
            ))

            stream_t += hop_size / sr
            pos += hop_size

        return rows


def _stereo_pan_features(
    stereo_frame: np.ndarray | None,
    window: np.ndarray,
    band_masks: tuple[np.ndarray, ...],
) -> tuple[float, float, float, float, tuple[float, ...]]:
    if stereo_frame is None or stereo_frame.ndim != 2 or stereo_frame.shape[1] < 2:
        return 0.0, 0.0, 0.0, 0.0, _ZERO_EQ_BANDS

    left = np.asarray(stereo_frame[:, 0], dtype=np.float32)
    right = np.asarray(stereo_frame[:, 1], dtype=np.float32)
    left_energy = float(np.mean(left ** 2))
    right_energy = float(np.mean(right ** 2))
    total_energy = left_energy + right_energy + 1e-8
    pan_center = float((right_energy - left_energy) / total_energy)
    width_num = float(np.mean(np.abs(left - right)))
    width_den = float(np.mean(np.abs(left) + np.abs(right))) + 1e-8
    pan_width = max(0.0, min(1.0, width_num / width_den))

    left_mag = np.abs(np.fft.rfft(left * window))
    right_mag = np.abs(np.fft.rfft(right * window))
    left_band_energies, _left_ratios, _left_fluxes = _compute_eq_band_features(
        left_mag,
        None,
        band_masks,
    )
    right_band_energies, _right_ratios, _right_fluxes = _compute_eq_band_features(
        right_mag,
        None,
        band_masks,
    )
    band_pan_centers = tuple(
        float((right_band - left_band) / (right_band + left_band + 1e-8))
        for left_band, right_band in zip(left_band_energies, right_band_energies)
    )
    return (
        pan_center,
        pan_width,
        left_energy,
        right_energy,
        band_pan_centers,
    )
