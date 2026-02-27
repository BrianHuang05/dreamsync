from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class FeatureFrame:
    t: float
    rms: float
    zcr: float
    centroid: float
    bass: float
    beat: bool
    bpm: float


def _frame_signal(signal: np.ndarray, frame_size: int, hop_size: int) -> np.ndarray:
    if len(signal) < frame_size:
        pad = frame_size - len(signal)
        signal = np.pad(signal, (0, pad), mode="constant")
    n_frames = 1 + (len(signal) - frame_size) // hop_size
    strides = (signal.strides[0] * hop_size, signal.strides[0])
    return np.lib.stride_tricks.as_strided(
        signal, shape=(n_frames, frame_size), strides=strides, writeable=False
    )


def _estimate_bpm(onset_env: np.ndarray, hop_size: int, sr: int) -> tuple[float, float]:
    """Estimate BPM from onset envelope via autocorrelation.

    Returns:
        (bpm, confidence) where confidence is the peak-to-median ratio
        of the autocorrelation in the valid lag range.  When the ratio
        is below 1.5 (no clear periodicity), returns (0.0, ratio).
    """
    if onset_env.size < 2:
        return 0.0, 0.0
    if hop_size <= 0 or sr <= 0:
        return 0.0, 0.0
    onset_env = onset_env - onset_env.mean()
    corr = np.correlate(onset_env, onset_env, mode="full")
    corr = corr[corr.size // 2 :]
    corr[:1] = 0.0

    min_bpm = 60.0
    max_bpm = 180.0
    min_lag = int(sr * 60.0 / max_bpm / hop_size)
    max_lag = int(sr * 60.0 / min_bpm / hop_size)
    if max_lag <= min_lag + 1 or max_lag >= corr.size:
        return 0.0, 0.0

    lag = min_lag + int(np.argmax(corr[min_lag:max_lag]))
    if lag <= 0:
        return 0.0, 0.0

    peak_val = float(corr[lag])
    # Confidence: how much the peak stands out from the typical correlation.
    # Mean-subtracted signals can have negative autocorrelation, so use mean
    # of absolute values as baseline (robust to sign).
    mean_abs = float(np.mean(np.abs(corr[min_lag:max_lag]))) + 1e-12
    confidence = peak_val / mean_abs
    if confidence < 3.0:
        return 0.0, confidence

    return 60.0 * sr / (lag * hop_size), confidence


def _smooth_signal(values: np.ndarray, width: int = 5) -> np.ndarray:
    if values.size == 0 or width <= 1:
        return values
    kernel = np.ones(width, dtype=np.float32) / float(width)
    return np.convolve(values, kernel, mode="same")


def _detect_beats(onset_env: np.ndarray, hop_size: int, sr: int) -> np.ndarray:
    if onset_env.size < 3:
        return np.array([], dtype=int)
    smooth = _smooth_signal(onset_env, width=5)
    thresh = np.percentile(smooth, 75) + 0.25 * smooth.std()
    peaks = (smooth[1:-1] > smooth[:-2]) & (smooth[1:-1] >= smooth[2:])
    peaks &= smooth[1:-1] > thresh

    peak_idx = (np.where(peaks)[0] + 1).tolist()
    if not peak_idx:
        return np.array([], dtype=int)

    # Suppress near-duplicates so one onset creates one beat marker.
    min_gap_frames = max(1, int((sr * 0.15) / hop_size))
    deduped: list[int] = [peak_idx[0]]
    for idx in peak_idx[1:]:
        if idx - deduped[-1] >= min_gap_frames:
            deduped.append(idx)
    return np.array(deduped, dtype=int)


def _estimate_bpm_from_beats(beat_idx: np.ndarray, hop_size: int, sr: int) -> float:
    if beat_idx.size < 2:
        return 0.0
    intervals = np.diff(beat_idx).astype(np.float32)
    if intervals.size == 0:
        return 0.0

    med = float(np.median(intervals))
    mad = float(np.median(np.abs(intervals - med)))
    if mad > 0:
        good = np.abs(intervals - med) <= (2.5 * mad)
        if np.any(good):
            intervals = intervals[good]
    if intervals.size == 0:
        return 0.0

    sec_per_beat = float(np.median(intervals)) * hop_size / sr
    if sec_per_beat <= 0:
        return 0.0
    bpm = 60.0 / sec_per_beat
    if bpm < 60.0:
        bpm *= 2.0
    if bpm > 180.0:
        bpm *= 0.5
    return bpm


def extract_feature_frames(
    signal: np.ndarray,
    sr: int,
    frame_size: int = 2048,
    hop_size: int = 512,
) -> list[FeatureFrame]:
    if hop_size <= 0:
        raise ValueError(f"hop_size must be > 0, got {hop_size}")
    if frame_size <= 0:
        raise ValueError(f"frame_size must be > 0, got {frame_size}")
    if sr <= 0:
        raise ValueError(f"sr must be > 0, got {sr}")
    frames = _frame_signal(signal, frame_size, hop_size)
    window = np.hanning(frame_size).astype(np.float32)
    spectrum = np.fft.rfft(frames * window[None, :], axis=1)
    mag = np.abs(spectrum)
    freqs = np.fft.rfftfreq(frame_size, d=1.0 / sr)

    rms = np.sqrt(np.mean(frames**2, axis=1))
    zcr = np.mean(np.abs(np.diff(np.sign(frames), axis=1)) > 0, axis=1)
    centroid = (mag * freqs[None, :]).sum(axis=1) / (mag.sum(axis=1) + 1e-8)
    bass = mag[:, freqs <= 200].sum(axis=1) / (mag.sum(axis=1) + 1e-8)

    onset_env = np.maximum(0.0, np.diff(rms, prepend=rms[0]))
    onset_env = _smooth_signal(onset_env, width=5)
    beat_idx = _detect_beats(onset_env, hop_size, sr)
    bpm_from_beats = _estimate_bpm_from_beats(beat_idx, hop_size, sr)
    bpm_from_corr, _corr_conf = _estimate_bpm(onset_env, hop_size, sr)
    if bpm_from_beats > 0 and bpm_from_corr > 0:
        bpm = 0.7 * bpm_from_beats + 0.3 * bpm_from_corr
    else:
        bpm = bpm_from_beats if bpm_from_beats > 0 else bpm_from_corr
    beat_set = set(beat_idx.tolist())

    frames_out: list[FeatureFrame] = []
    for i in range(frames.shape[0]):
        t = (i * hop_size) / sr
        frames_out.append(
            FeatureFrame(
                t=t,
                rms=float(rms[i]),
                zcr=float(zcr[i]),
                centroid=float(centroid[i]),
                bass=float(bass[i]),
                beat=i in beat_set,
                bpm=float(bpm),
            )
        )
    return frames_out
