"""Shared numpy-only spectral helpers used by live and offline analysis."""

from __future__ import annotations

from functools import lru_cache

import numpy as np


def hz_to_mel(frequency: float) -> float:
    return float(2595.0 * np.log10(1.0 + float(frequency) / 700.0))


def mel_to_hz(value: float) -> float:
    return float(700.0 * (10.0 ** (float(value) / 2595.0) - 1.0))


@lru_cache(maxsize=16)
def mel_filterbank(
    *,
    n_mels: int = 40,
    n_fft: int = 2048,
    sample_rate: int = 44100,
    fmin: float = 20.0,
    fmax: float | None = None,
) -> np.ndarray:
    """Return a cached triangular mel filterbank."""

    if n_mels < 1 or n_fft < 2 or sample_rate < 1:
        raise ValueError("mel filterbank dimensions must be positive")
    upper = float(sample_rate) / 2.0 if fmax is None else float(fmax)
    if not 0.0 <= fmin < upper <= float(sample_rate) / 2.0:
        raise ValueError("mel filterbank frequencies are invalid")
    mel_points = np.linspace(hz_to_mel(fmin), hz_to_mel(upper), n_mels + 2)
    hz_points = np.array([mel_to_hz(value) for value in mel_points])
    bins = np.floor((n_fft + 1) * hz_points / sample_rate).astype(int)
    bins = np.clip(bins, 0, n_fft // 2)
    bank = np.zeros((n_mels, n_fft // 2 + 1), dtype=np.float64)
    for index in range(n_mels):
        left, center, right = bins[index : index + 3]
        if center > left:
            bank[index, left:center] = (
                np.arange(left, center, dtype=np.float64) - left
            ) / (center - left)
        if right > center:
            bank[index, center:right] = (
                right - np.arange(center, right, dtype=np.float64)
            ) / (right - center)
    bank.setflags(write=False)
    return bank


@lru_cache(maxsize=8)
def dct_matrix(*, n_mfcc: int = 13, n_mels: int = 40) -> np.ndarray:
    """Return a cached orthonormal DCT-II basis."""

    if n_mfcc < 1 or n_mels < 1:
        raise ValueError("DCT dimensions must be positive")
    coefficient = np.arange(n_mfcc, dtype=np.float64)[:, None]
    sample = np.arange(n_mels, dtype=np.float64)[None, :]
    basis = np.cos(np.pi * coefficient * (2.0 * sample + 1.0) / (2.0 * n_mels))
    basis[0] *= np.sqrt(1.0 / n_mels)
    if n_mfcc > 1:
        basis[1:] *= np.sqrt(2.0 / n_mels)
    basis.setflags(write=False)
    return basis


def mfcc_from_magnitude(
    magnitude: np.ndarray,
    *,
    sample_rate: int,
    n_fft: int,
    n_mfcc: int = 13,
    n_mels: int = 40,
) -> tuple[float, ...]:
    """Compute MFCCs from an existing magnitude spectrum without another FFT."""

    mag = np.asarray(magnitude, dtype=np.float64).reshape(-1)
    expected = n_fft // 2 + 1
    if mag.size != expected:
        raise ValueError(f"magnitude must contain {expected} FFT bins")
    safe = np.nan_to_num(mag, nan=0.0, posinf=0.0, neginf=0.0)
    safe = np.maximum(0.0, safe)
    mel_power = mel_filterbank(
        n_mels=n_mels,
        n_fft=n_fft,
        sample_rate=sample_rate,
    ) @ np.square(safe)
    coefficients = dct_matrix(n_mfcc=n_mfcc, n_mels=n_mels) @ np.log(
        mel_power + 1e-10
    )
    return tuple(float(value) for value in coefficients)
