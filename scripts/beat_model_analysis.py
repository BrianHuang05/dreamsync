"""DreamSync beat/downbeat feature analysis and model comparison.

This is deliberately a single-file, inspectable research pipeline.  It:

1. loads the ten manually verified ``.show.json`` timelines listed below;
2. decodes each paired MP3 to floating-point PCM with DreamSync's decoder;
3. creates causal FFT, log-mel, MFCC, chroma, stereo, and onset features;
4. encodes verified beat/downbeat indicators and smooth activation targets;
5. builds causal sliding-window summaries for configurable window lengths;
6. tunes and compares mini-batch Ridge, linear SVM, and Decision Tree models;
7. trains a heterogeneous stack whose meta-model is a Random Forest;
8. evaluates event timing on entirely held-out tracks and writes plots/metrics.

The hard operation "extract peaks, then match them to annotated events" is not
differentiable.  Mini-batch Ridge therefore minimizes a regularized smooth
activation loss, while hyperparameter/model selection uses the requested event
time-difference cost.  This preserves a true linear Ridge model and makes the
gradient mathematically meaningful.

Run from the repository root, for example:

    python scripts/beat_model_analysis.py --quick
    python scripts/beat_model_analysis.py --window-seconds 0.5,1.0,2.0

Required third-party packages:

    numpy pandas matplotlib seaborn scipy scikit-learn

FFmpeg/ffprobe must also be available through DreamSync's existing resolver.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
    import seaborn as sns
    from scipy import fft as scipy_fft
    from scipy.signal import find_peaks
    from scipy.stats import pointbiserialr
    from sklearn.base import BaseEstimator, RegressorMixin, clone
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.model_selection import GroupKFold, GroupShuffleSplit, ParameterGrid
    from sklearn.multioutput import MultiOutputRegressor
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.svm import LinearSVR
    from sklearn.tree import DecisionTreeRegressor
except ModuleNotFoundError as exc:  # pragma: no cover - dependency guard
    raise SystemExit(
        "Missing analysis dependency "
        f"{exc.name!r}. Install numpy, pandas, matplotlib, seaborn, scipy, "
        "and scikit-learn in the Python environment used to run this script."
    ) from exc

from dreamsync.analyzer.decode import decode_mp3


VERIFIED_SHOW_FILES: tuple[str, ...] = (
    "2026-03-06_00-07-58_Wasia Project_-_Is This What Love Is_.show.json",
    "2026-03-06_00-11-14_Vacation Manor_-_What Could Be.show.json",
    "2026-03-06_00-15-02_Vacation Manor_-_If Only for Tonight - Midnight Version.show.json",
    "2026-03-06_00-19-45_Pat Metheny Group_-_Last Train Home.show.json",
    "2026-03-06_22-48-18_加藤達也_-_MILLIONS KNIVES - Vocal Ver.show.json",
    "2026-03-06_22-51-49_Pretty Patterns_-_Cloudy Hollow.show.json",
    "2026-03-06_22-56-14_ILLENIUM_-_Feels Like You.show.json",
    "2026-03-06_22-59-47_Ghostly Kisses_-_Keep It Real.show.json",
    "2026-03-06_23-02-41_Maggie Lindemann_-_break me.show.json",
    "2026-03-06_23-05-17_Maggie Lindemann_-_i feel everything.show.json",
)

TARGET_COLUMNS: tuple[str, str] = ("beat_target", "downbeat_target")
INDICATOR_COLUMNS: tuple[str, str] = ("is_beat", "is_downbeat")
NON_FEATURE_COLUMNS = {
    "track_id",
    "show_path",
    "audio_path",
    "t",
    "is_beat",
    "is_downbeat",
    "beat_target",
    "downbeat_target",
    "nearest_beat_distance",
    "nearest_downbeat_distance",
    "seconds_to_next_beat",
    "seconds_to_next_downbeat",
}


def console_safe(value: Any) -> str:
    """Render user/path text without failing on a legacy Windows console.

    The verified Japanese track name is valid Unicode and remains unchanged in
    labels, CSV, and JSON.  Only human-facing stdout/stderr text is replaced
    when the active PowerShell stream advertises a legacy code page such as
    cp1252.  Unrepresentable characters are omitted from display text rather
    than replaced with ``?`` so downstream console/log consumers only receive
    safe filename characters.
    """
    text = str(value)
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    return text.encode(encoding, errors="ignore").decode(encoding, errors="ignore")


@dataclass(frozen=True)
class TimelineRecord:
    track_id: str
    show_path: Path
    audio_path: Path
    duration: float
    beat_times: np.ndarray
    downbeat_times: np.ndarray
    time_signature: int


@dataclass(frozen=True)
class AnalysisConfig:
    sample_rate: int
    frame_size: int
    hop_seconds: float
    n_mels: int
    n_mfcc: int
    label_tolerance: float
    target_sigma: float
    match_tolerance: float
    miss_penalty: float
    beat_peak_threshold: float
    downbeat_peak_threshold: float
    min_beat_interval: float
    min_downbeat_interval: float
    window_seconds: tuple[float, ...]
    random_state: int

    @property
    def hop_size(self) -> int:
        return max(1, int(round(self.sample_rate * self.hop_seconds)))


@dataclass(frozen=True)
class EventMetrics:
    precision: float
    recall: float
    f1: float
    matched_mae_ms: float
    timing_cost_seconds: float
    predicted_events: int
    actual_events: int
    matched_events: int


@dataclass
class CandidateResult:
    family: str
    window_seconds: float
    params: dict[str, Any]
    validation_event_cost: float
    validation_beat_f1: float
    validation_downbeat_f1: float
    frame_mse: float


def _csv_floats(value: str) -> tuple[float, ...]:
    parsed = tuple(float(part.strip()) for part in value.split(",") if part.strip())
    if not parsed:
        raise argparse.ArgumentTypeError("expected a comma-separated list of numbers")
    return parsed


def _csv_ints(value: str) -> tuple[int, ...]:
    parsed = tuple(int(part.strip()) for part in value.split(",") if part.strip())
    if not parsed:
        raise argparse.ArgumentTypeError("expected a comma-separated list of integers")
    return parsed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--show",
        dest="show_paths",
        action="append",
        type=Path,
        help="Override verified defaults; may be repeated.",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("out/beat-ml-analysis"))
    parser.add_argument("--sample-rate", type=int, default=44_100)
    parser.add_argument("--frame-size", type=int, default=2_048)
    parser.add_argument("--hop-seconds", type=float, default=0.020)
    parser.add_argument("--n-mels", type=int, default=32)
    parser.add_argument("--n-mfcc", type=int, default=13)
    parser.add_argument("--label-tolerance", type=float, default=0.040)
    parser.add_argument("--target-sigma", type=float, default=0.040)
    parser.add_argument("--match-tolerance", type=float, default=0.070)
    parser.add_argument("--miss-penalty", type=float, default=0.500)
    parser.add_argument("--beat-peak-threshold", type=float, default=0.30)
    parser.add_argument("--downbeat-peak-threshold", type=float, default=0.25)
    parser.add_argument("--min-beat-interval", type=float, default=0.240)
    parser.add_argument("--min-downbeat-interval", type=float, default=0.700)
    parser.add_argument(
        "--window-seconds",
        type=_csv_floats,
        default=(0.5, 1.0, 2.0, 5.0, 10.0),
        help="Causal context windows; longer windows preserve repeating beat patterns.",
    )
    parser.add_argument("--ridge-alphas", type=_csv_floats, default=(1e-4, 1e-3, 1e-2))
    parser.add_argument("--ridge-learning-rates", type=_csv_floats, default=(0.01, 0.03))
    parser.add_argument("--ridge-epochs", type=int, default=35)
    parser.add_argument("--batch-size", type=int, default=1_024)
    parser.add_argument("--svm-c-values", type=_csv_floats, default=(0.1, 1.0))
    parser.add_argument("--max-svm-training-rows", type=int, default=60_000)
    parser.add_argument("--tree-depths", type=_csv_ints, default=(8, 16))
    parser.add_argument("--tree-min-leaf", type=_csv_ints, default=(10, 30))
    parser.add_argument("--cv-folds", type=int, default=3)
    parser.add_argument("--test-track-fraction", type=float, default=0.20)
    parser.add_argument("--forest-estimators", type=int, default=200)
    parser.add_argument("--forest-max-depth", type=int, default=10)
    parser.add_argument("--random-state", type=int, default=73)
    parser.add_argument("--sample-track", default="", help="Substring for the diagnostic plot track.")
    parser.add_argument("--sample-start", type=float, default=30.0)
    parser.add_argument("--sample-duration", type=float, default=12.0)
    parser.add_argument(
        "--max-track-seconds",
        type=float,
        default=None,
        help="Debug/smoke-test cap; omitted for the full verified recordings.",
    )
    parser.add_argument(
        "--no-save-features",
        action="store_true",
        help="Do not write the generated frame-level DataFrame as compressed CSV.",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Small hyperparameter grid and fewer epochs/trees for a first diagnostic run.",
    )
    return parser.parse_args(argv)


def resolve_path(path: Path, repo_root: Path) -> Path:
    return path.resolve() if path.is_absolute() else (repo_root / path).resolve()


def load_verified_timelines(args: argparse.Namespace) -> list[TimelineRecord]:
    repo_root = args.repo_root.resolve()
    if args.show_paths:
        show_paths = [resolve_path(path, repo_root) for path in args.show_paths]
    else:
        show_paths = [repo_root / "out" / "shows" / name for name in VERIFIED_SHOW_FILES]

    timelines: list[TimelineRecord] = []
    for show_path in show_paths:
        if not show_path.is_file():
            raise FileNotFoundError(f"Verified show file is missing: {show_path}")
        data = json.loads(show_path.read_text(encoding="utf-8"))
        required = {"song_path", "duration", "beat_times", "downbeat_times", "time_signature"}
        missing = sorted(required.difference(data))
        if missing:
            raise ValueError(f"{show_path} is not a flat ShowTimeline; missing {missing}")

        audio_path = resolve_path(Path(data["song_path"]), repo_root)
        if not audio_path.is_file():
            raise FileNotFoundError(f"Paired MP3 is missing for {show_path.name}: {audio_path}")

        beat_times = np.asarray(data["beat_times"], dtype=np.float64)
        downbeat_times = np.asarray(data["downbeat_times"], dtype=np.float64)
        if beat_times.size == 0 or downbeat_times.size == 0:
            raise ValueError(f"Verified labels must contain beats and downbeats: {show_path}")
        if np.any(np.diff(beat_times) <= 0) or np.any(np.diff(downbeat_times) <= 0):
            raise ValueError(f"Verified labels must be strictly increasing: {show_path}")

        timelines.append(
            TimelineRecord(
                track_id=show_path.name.removesuffix(".show.json"),
                show_path=show_path.resolve(),
                audio_path=audio_path,
                duration=float(data["duration"]),
                beat_times=beat_times,
                downbeat_times=downbeat_times,
                time_signature=int(data["time_signature"]),
            )
        )
    if len({timeline.audio_path for timeline in timelines}) != len(timelines):
        raise ValueError("Verified show list contains duplicate audio paths")
    return timelines


def hz_to_mel(frequency_hz: np.ndarray | float) -> np.ndarray | float:
    return 2_595.0 * np.log10(1.0 + np.asarray(frequency_hz) / 700.0)


def mel_to_hz(mel: np.ndarray | float) -> np.ndarray | float:
    return 700.0 * (10.0 ** (np.asarray(mel) / 2_595.0) - 1.0)


def make_mel_filterbank(
    sample_rate: int,
    frame_size: int,
    n_mels: int,
    fmin: float = 20.0,
    fmax: float | None = None,
) -> np.ndarray:
    fmax = float(fmax if fmax is not None else sample_rate / 2.0)
    mel_edges = np.linspace(float(hz_to_mel(fmin)), float(hz_to_mel(fmax)), n_mels + 2)
    hz_edges = np.asarray(mel_to_hz(mel_edges), dtype=np.float64)
    frequencies = scipy_fft.rfftfreq(frame_size, d=1.0 / sample_rate)
    bank = np.zeros((n_mels, frequencies.size), dtype=np.float32)
    for index in range(n_mels):
        left, center, right = hz_edges[index : index + 3]
        rising = (frequencies - left) / max(center - left, 1e-12)
        falling = (right - frequencies) / max(right - center, 1e-12)
        bank[index] = np.maximum(0.0, np.minimum(rising, falling))
    normalization = bank.sum(axis=1, keepdims=True)
    bank /= np.maximum(normalization, 1e-12)
    return bank


def make_chroma_projection(frequencies: np.ndarray) -> np.ndarray:
    projection = np.zeros((12, frequencies.size), dtype=np.float32)
    valid_indices = np.flatnonzero(frequencies >= 20.0)
    if valid_indices.size:
        pitch_classes = (
            np.rint(12.0 * np.log2(frequencies[valid_indices] / 440.0)).astype(int) % 12
        )
        projection[pitch_classes, valid_indices] = 1.0
    return projection


def nearest_event_distances(times: np.ndarray, event_times: np.ndarray) -> np.ndarray:
    if event_times.size == 0:
        return np.full(times.shape, np.inf, dtype=np.float64)
    right = np.searchsorted(event_times, times, side="left")
    left = np.clip(right - 1, 0, event_times.size - 1)
    right = np.clip(right, 0, event_times.size - 1)
    return np.minimum(np.abs(times - event_times[left]), np.abs(times - event_times[right]))


def seconds_to_next_event(times: np.ndarray, event_times: np.ndarray) -> np.ndarray:
    right = np.searchsorted(event_times, times, side="left")
    output = np.full(times.shape, np.nan, dtype=np.float64)
    valid = right < event_times.size
    output[valid] = event_times[right[valid]] - times[valid]
    return output


def _spectral_band_ratio(
    magnitude: np.ndarray,
    frequencies: np.ndarray,
    low_hz: float,
    high_hz: float,
    total: np.ndarray,
) -> np.ndarray:
    mask = (frequencies >= low_hz) & (frequencies < high_hz)
    if not np.any(mask):
        return np.zeros(magnitude.shape[0], dtype=np.float32)
    return magnitude[:, mask].sum(axis=1) / total


def extract_track_dataframe(
    timeline: TimelineRecord,
    config: AnalysisConfig,
    *,
    max_track_seconds: float | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """Decode one MP3 and return one causal feature row per analysis hop."""
    audio = decode_mp3(timeline.audio_path, target_sr=config.sample_rate, preserve_stereo=True)
    mono = np.asarray(audio.signal, dtype=np.float32)
    stereo = None if audio.stereo_signal is None else np.asarray(audio.stereo_signal, dtype=np.float32)
    if max_track_seconds is not None:
        sample_limit = min(mono.size, int(round(max_track_seconds * config.sample_rate)))
        mono = mono[:sample_limit]
        if stereo is not None:
            stereo = stereo[:sample_limit]

    frame_size = config.frame_size
    hop_size = config.hop_size
    if mono.size < frame_size:
        raise ValueError(f"Audio is shorter than one frame: {timeline.audio_path}")
    n_frames = 1 + (mono.size - frame_size) // hop_size
    starts = np.arange(n_frames, dtype=np.int64) * hop_size
    # A causal frame becomes available at its right edge, not its center.
    frame_times = (starts + frame_size) / float(config.sample_rate)

    frequencies = scipy_fft.rfftfreq(frame_size, d=1.0 / config.sample_rate)
    window = np.hanning(frame_size).astype(np.float32)
    mel_bank = make_mel_filterbank(config.sample_rate, frame_size, config.n_mels)
    chroma_projection = make_chroma_projection(frequencies)
    rolloff_threshold = 0.85

    scalar_feature_names = [
        "rms",
        "zcr",
        "spectral_centroid",
        "spectral_rolloff_85",
        "spectral_flatness",
        "spectral_entropy",
        "spectral_flux",
        "dominant_frequency_hz",
        "dominant_magnitude_ratio",
        "sub_ratio",
        "kick_ratio",
        "bass_ratio",
        "low_mid_ratio",
        "mid_ratio",
        "presence_ratio",
        "brilliance_ratio",
        "pan_center",
        "stereo_width",
        "left_rms",
        "right_rms",
    ]
    mel_feature_names = [f"log_mel_{index:02d}" for index in range(config.n_mels)]
    mfcc_feature_names = [f"mfcc_{index:02d}" for index in range(config.n_mfcc)]
    chroma_feature_names = [f"chroma_{index:02d}" for index in range(12)]
    feature_names = scalar_feature_names + mel_feature_names + mfcc_feature_names + chroma_feature_names

    feature_arrays: dict[str, list[np.ndarray]] = {name: [] for name in feature_names}
    previous_magnitude: np.ndarray | None = None
    batch_frames = 2_048
    sample_offsets = np.arange(frame_size, dtype=np.int64)

    for block_start in range(0, n_frames, batch_frames):
        block_starts = starts[block_start : block_start + batch_frames]
        indices = block_starts[:, None] + sample_offsets[None, :]
        frames = mono[indices]
        windowed = frames * window[None, :]
        magnitude = np.abs(scipy_fft.rfft(windowed, axis=1, workers=-1)).astype(np.float32)
        power = magnitude * magnitude
        total_magnitude = magnitude.sum(axis=1) + 1e-12
        total_power = power.sum(axis=1) + 1e-12

        rms = np.sqrt(np.mean(frames * frames, axis=1))
        zcr = np.mean(np.signbit(frames[:, 1:]) != np.signbit(frames[:, :-1]), axis=1)
        centroid = (magnitude * frequencies[None, :]).sum(axis=1) / total_magnitude
        cumulative_power = np.cumsum(power, axis=1)
        rolloff_indices = np.argmax(
            cumulative_power >= (rolloff_threshold * total_power[:, None]), axis=1
        )
        rolloff = frequencies[rolloff_indices]
        geometric_mean = np.exp(np.mean(np.log(magnitude + 1e-12), axis=1))
        flatness = geometric_mean / (np.mean(magnitude, axis=1) + 1e-12)
        probability = power / total_power[:, None]
        entropy = -(probability * np.log2(probability + 1e-12)).sum(axis=1)
        entropy /= math.log2(max(2, probability.shape[1]))

        flux_source = magnitude
        if previous_magnitude is None:
            previous = np.vstack([magnitude[0:1], magnitude[:-1]])
        else:
            previous = np.vstack([previous_magnitude[None, :], magnitude[:-1]])
        positive_difference = np.maximum(0.0, flux_source - previous)
        spectral_flux = positive_difference.sum(axis=1) / (previous.sum(axis=1) + 1e-12)
        previous_magnitude = magnitude[-1].copy()

        dominant_indices = np.argmax(magnitude[:, 1:], axis=1) + 1
        dominant_frequency = frequencies[dominant_indices]
        dominant_ratio = magnitude[np.arange(magnitude.shape[0]), dominant_indices] / total_magnitude

        log_mel = np.log1p(power @ mel_bank.T).astype(np.float32)
        mfcc = scipy_fft.dct(log_mel, type=2, axis=1, norm="ortho")[:, : config.n_mfcc]
        chroma = magnitude @ chroma_projection.T
        chroma /= np.maximum(chroma.sum(axis=1, keepdims=True), 1e-12)

        if stereo is not None:
            left = stereo[indices, 0]
            right = stereo[indices, 1]
            left_energy = np.mean(left * left, axis=1)
            right_energy = np.mean(right * right, axis=1)
            pan_center = (right_energy - left_energy) / (right_energy + left_energy + 1e-12)
            stereo_width = np.mean(np.abs(left - right), axis=1) / (
                np.mean(np.abs(left) + np.abs(right), axis=1) + 1e-12
            )
            left_rms = np.sqrt(left_energy)
            right_rms = np.sqrt(right_energy)
        else:
            zeros = np.zeros(frames.shape[0], dtype=np.float32)
            pan_center = stereo_width = left_rms = right_rms = zeros

        scalar_values = {
            "rms": rms,
            "zcr": zcr,
            "spectral_centroid": centroid,
            "spectral_rolloff_85": rolloff,
            "spectral_flatness": flatness,
            "spectral_entropy": entropy,
            "spectral_flux": spectral_flux,
            "dominant_frequency_hz": dominant_frequency,
            "dominant_magnitude_ratio": dominant_ratio,
            "sub_ratio": _spectral_band_ratio(magnitude, frequencies, 20, 60, total_magnitude),
            "kick_ratio": _spectral_band_ratio(magnitude, frequencies, 50, 130, total_magnitude),
            "bass_ratio": _spectral_band_ratio(magnitude, frequencies, 60, 200, total_magnitude),
            "low_mid_ratio": _spectral_band_ratio(magnitude, frequencies, 200, 600, total_magnitude),
            "mid_ratio": _spectral_band_ratio(magnitude, frequencies, 600, 2_000, total_magnitude),
            "presence_ratio": _spectral_band_ratio(magnitude, frequencies, 2_000, 5_000, total_magnitude),
            "brilliance_ratio": _spectral_band_ratio(magnitude, frequencies, 5_000, 20_000, total_magnitude),
            "pan_center": pan_center,
            "stereo_width": stereo_width,
            "left_rms": left_rms,
            "right_rms": right_rms,
        }
        for name, values in scalar_values.items():
            feature_arrays[name].append(np.asarray(values, dtype=np.float32))
        for index, name in enumerate(mel_feature_names):
            feature_arrays[name].append(log_mel[:, index])
        for index, name in enumerate(mfcc_feature_names):
            feature_arrays[name].append(np.asarray(mfcc[:, index], dtype=np.float32))
        for index, name in enumerate(chroma_feature_names):
            feature_arrays[name].append(np.asarray(chroma[:, index], dtype=np.float32))

    frame_data: dict[str, Any] = {
        "track_id": timeline.track_id,
        "show_path": str(timeline.show_path),
        "audio_path": str(timeline.audio_path),
        "t": frame_times,
    }
    for name in feature_names:
        frame_data[name] = np.concatenate(feature_arrays[name]).astype(np.float32, copy=False)

    beat_distance = nearest_event_distances(frame_times, timeline.beat_times)
    downbeat_distance = nearest_event_distances(frame_times, timeline.downbeat_times)
    frame_data["nearest_beat_distance"] = beat_distance
    frame_data["nearest_downbeat_distance"] = downbeat_distance
    frame_data["seconds_to_next_beat"] = seconds_to_next_event(frame_times, timeline.beat_times)
    frame_data["seconds_to_next_downbeat"] = seconds_to_next_event(
        frame_times, timeline.downbeat_times
    )
    frame_data["is_beat"] = (beat_distance <= config.label_tolerance).astype(np.int8)
    frame_data["is_downbeat"] = (downbeat_distance <= config.label_tolerance).astype(np.int8)
    frame_data["beat_target"] = np.exp(
        -0.5 * np.square(beat_distance / config.target_sigma)
    ).astype(np.float32)
    frame_data["downbeat_target"] = np.exp(
        -0.5 * np.square(downbeat_distance / config.target_sigma)
    ).astype(np.float32)

    return pd.DataFrame(frame_data), feature_names


def prepare_frame_dataset(
    timelines: Sequence[TimelineRecord],
    config: AnalysisConfig,
    *,
    max_track_seconds: float | None,
) -> tuple[pd.DataFrame, list[str]]:
    frames: list[pd.DataFrame] = []
    feature_names: list[str] | None = None
    for index, timeline in enumerate(timelines, start=1):
        started = time.perf_counter()
        print(
            console_safe(f"[{index}/{len(timelines)}] extracting {timeline.audio_path.name}"),
            flush=True,
        )
        track_frame, current_names = extract_track_dataframe(
            timeline, config, max_track_seconds=max_track_seconds
        )
        if feature_names is None:
            feature_names = current_names
        elif feature_names != current_names:
            raise RuntimeError("Feature schema changed between tracks")
        frames.append(track_frame)
        print(
            f"    {len(track_frame):,} frames in {time.perf_counter() - started:.1f}s",
            flush=True,
        )
    return pd.concat(frames, ignore_index=True), feature_names or []


def build_causal_window_dataset(
    frame_df: pd.DataFrame,
    base_feature_names: Sequence[str],
    window_seconds: float,
    hop_seconds: float,
) -> tuple[pd.DataFrame, list[str]]:
    """Add bounded-size summaries of a causal sliding feature window.

    Flattening every 20 ms frame in a two-second window would create thousands
    of highly collinear columns.  The model instead sees the current frame,
    past-window mean/std/max, the immediate delta, and three causal lag taps.
    Window length remains a real hyperparameter while memory remains practical.
    """
    window_frames = max(2, int(round(window_seconds / hop_seconds)))
    lag_frames = sorted({1, max(1, window_frames // 4), max(1, window_frames // 2), window_frames - 1})
    feature_names = list(base_feature_names)
    output_groups: list[pd.DataFrame] = []

    summary_names = [
        *(f"{name}__mean_w" for name in base_feature_names),
        *(f"{name}__std_w" for name in base_feature_names),
        *(f"{name}__max_w" for name in base_feature_names),
        *(f"{name}__delta_1" for name in base_feature_names),
    ]
    for lag in lag_frames:
        summary_names.extend(f"{name}__lag_{lag}" for name in base_feature_names)
    feature_names.extend(summary_names)

    # Preserve band-wise temporal evidence that aggregate statistics can erase:
    # causal running normalization, positive onset deltas, peak excess, and
    # autocorrelation at plausible beat periods.
    periodicity_sources = [
        name
        for name in (
            "rms", "spectral_flux", "sub_ratio", "kick_ratio", "bass_ratio",
            "low_mid_ratio", "mid_ratio", "presence_ratio", "brilliance_ratio",
        )
        if name in base_feature_names
    ]
    periodicity_lags = sorted({
        max(1, int(round(period / hop_seconds)))
        for period in (0.40, 0.50, 0.60, 0.70, 0.80, 1.00)
    })
    normalized_names = [f"{name}__causal_z" for name in base_feature_names]
    positive_delta_names = [f"{name}__positive_delta" for name in periodicity_sources]
    peak_excess_names = [f"{name}__peak_excess" for name in periodicity_sources]
    periodicity_names = [
        f"{name}__autocorr_{lag}"
        for name in periodicity_sources
        for lag in periodicity_lags
    ]
    feature_names.extend(normalized_names + positive_delta_names + peak_excess_names + periodicity_names)

    for _track_id, track_df in frame_df.groupby("track_id", sort=False):
        track_df = track_df.reset_index(drop=True)
        base = track_df.loc[:, base_feature_names].astype(np.float32)
        rolling = base.rolling(window=window_frames, min_periods=1)
        mean = rolling.mean().astype(np.float32)
        std = rolling.std(ddof=0).fillna(0.0).astype(np.float32)
        maximum = rolling.max().astype(np.float32)
        delta = base.diff().fillna(0.0).astype(np.float32)

        mean.columns = [f"{name}__mean_w" for name in base_feature_names]
        std.columns = [f"{name}__std_w" for name in base_feature_names]
        maximum.columns = [f"{name}__max_w" for name in base_feature_names]
        delta.columns = [f"{name}__delta_1" for name in base_feature_names]

        prior_mean = base.expanding(min_periods=1).mean().shift(1)
        prior_std = base.expanding(min_periods=1).std(ddof=0).shift(1)
        causal_z = ((base - prior_mean) / prior_std.replace(0.0, np.nan)).fillna(0.0)
        causal_z = causal_z.clip(-8.0, 8.0).astype(np.float32)
        causal_z.columns = normalized_names

        periodicity_base = base.loc[:, periodicity_sources]
        positive_delta = periodicity_base.diff().clip(lower=0.0).fillna(0.0).astype(np.float32)
        positive_delta.columns = positive_delta_names
        peak_excess = (
            periodicity_base.rolling(window=window_frames, min_periods=1).max()
            - periodicity_base.rolling(window=window_frames, min_periods=1).mean()
        ).fillna(0.0).astype(np.float32)
        peak_excess.columns = peak_excess_names
        autocorr_blocks: list[pd.DataFrame] = []
        for source in periodicity_sources:
            onset = periodicity_base[source].diff().clip(lower=0.0).fillna(0.0)
            for lag in periodicity_lags:
                autocorrelation = (
                    onset * onset.shift(lag)
                ).rolling(lag + 1, min_periods=lag + 1).mean().fillna(0.0)
                autocorr_blocks.append(
                    pd.DataFrame({f"{source}__autocorr_{lag}": autocorrelation.astype(np.float32)})
                )
        autocorr = pd.concat(autocorr_blocks, axis=1) if autocorr_blocks else pd.DataFrame(index=base.index)

        blocks = [track_df, mean, std, maximum, delta, causal_z, positive_delta, peak_excess, autocorr]

        values = base.to_numpy(dtype=np.float32, copy=False)
        row_indices = np.arange(len(base))
        for lag in lag_frames:
            source_indices = np.maximum(0, row_indices - lag)
            lagged = pd.DataFrame(
                values[source_indices],
                columns=[f"{name}__lag_{lag}" for name in base_feature_names],
            )
            blocks.append(lagged)
        output_groups.append(pd.concat(blocks, axis=1))

    output = pd.concat(output_groups, ignore_index=True)
    if output.loc[:, feature_names].isna().any().any():
        raise RuntimeError("Causal feature generation produced NaN values")
    return output, feature_names


class MiniBatchRidgeRegressor(RegressorMixin, BaseEstimator):
    """Multi-output linear Ridge regression optimized by mini-batch GD."""

    def __init__(
        self,
        *,
        alpha: float = 1e-3,
        learning_rate: float = 0.01,
        epochs: int = 35,
        batch_size: int = 1_024,
        gradient_clip: float = 10.0,
        tolerance: float = 1e-6,
        patience: int = 5,
        random_state: int = 73,
        verbose: bool = False,
    ) -> None:
        self.alpha = alpha
        self.learning_rate = learning_rate
        self.epochs = epochs
        self.batch_size = batch_size
        self.gradient_clip = gradient_clip
        self.tolerance = tolerance
        self.patience = patience
        self.random_state = random_state
        self.verbose = verbose

    def fit(
        self,
        X: np.ndarray,
        y: np.ndarray,
        sample_weight: np.ndarray | None = None,
    ) -> "MiniBatchRidgeRegressor":
        X = np.asarray(X, dtype=np.float32)
        y = np.asarray(y, dtype=np.float32)
        if y.ndim == 1:
            y = y[:, None]
        if X.ndim != 2 or y.ndim != 2 or X.shape[0] != y.shape[0]:
            raise ValueError("X and y must be aligned 2-D arrays")
        if X.shape[0] == 0:
            raise ValueError("Cannot fit an empty dataset")

        weights = (
            np.ones(X.shape[0], dtype=np.float32)
            if sample_weight is None
            else np.asarray(sample_weight, dtype=np.float32)
        )
        if weights.shape != (X.shape[0],):
            raise ValueError("sample_weight must contain one value per row")

        generator = np.random.default_rng(self.random_state)
        self.coef_ = np.zeros((X.shape[1], y.shape[1]), dtype=np.float32)
        self.intercept_ = np.average(y, axis=0, weights=weights).astype(np.float32)
        self.loss_history_: list[float] = []
        best_loss = math.inf
        stale_epochs = 0
        best_coef = self.coef_.copy()
        best_intercept = self.intercept_.copy()

        for epoch in range(max(1, self.epochs)):
            order = generator.permutation(X.shape[0])
            epoch_learning_rate = self.learning_rate / math.sqrt(1.0 + 0.15 * epoch)
            for batch_start in range(0, X.shape[0], max(1, self.batch_size)):
                batch_indices = order[batch_start : batch_start + self.batch_size]
                batch_X = X[batch_indices]
                batch_y = y[batch_indices]
                batch_weights = weights[batch_indices, None]
                weight_sum = float(batch_weights.sum()) + 1e-12
                error = batch_X @ self.coef_ + self.intercept_ - batch_y
                weighted_error = error * batch_weights
                gradient_coef = (2.0 / weight_sum) * (batch_X.T @ weighted_error)
                gradient_coef += 2.0 * self.alpha * self.coef_
                gradient_intercept = (2.0 / weight_sum) * weighted_error.sum(axis=0)

                gradient_norm = float(
                    np.sqrt(np.sum(gradient_coef * gradient_coef) + np.sum(gradient_intercept**2))
                )
                if gradient_norm > self.gradient_clip:
                    scale = self.gradient_clip / (gradient_norm + 1e-12)
                    gradient_coef *= scale
                    gradient_intercept *= scale
                self.coef_ -= epoch_learning_rate * gradient_coef
                self.intercept_ -= epoch_learning_rate * gradient_intercept

            prediction = X @ self.coef_ + self.intercept_
            squared_error = np.square(prediction - y)
            data_loss = float(np.sum(squared_error * weights[:, None]) / (weights.sum() * y.shape[1]))
            ridge_loss = float(self.alpha * np.sum(self.coef_ * self.coef_))
            loss = data_loss + ridge_loss
            self.loss_history_.append(loss)
            if self.verbose:
                print(f"      ridge epoch={epoch + 1:03d} loss={loss:.7f}", flush=True)
            if best_loss - loss > self.tolerance:
                best_loss = loss
                stale_epochs = 0
                best_coef = self.coef_.copy()
                best_intercept = self.intercept_.copy()
            else:
                stale_epochs += 1
                if stale_epochs >= self.patience:
                    break

        # Early stopping is useful only if the best iterate is retained.  The
        # final mini-batch update can otherwise move away from the lowest-loss
        # solution immediately before patience expires.
        self.coef_ = best_coef
        self.intercept_ = best_intercept
        self.effective_epochs_ = len(self.loss_history_)

        self.n_features_in_ = X.shape[1]
        self.n_outputs_ = y.shape[1]
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        if not hasattr(self, "coef_"):
            raise RuntimeError("MiniBatchRidgeRegressor must be fit before predict")
        prediction = np.asarray(X, dtype=np.float32) @ self.coef_ + self.intercept_
        return prediction[:, 0] if self.n_outputs_ == 1 else prediction


def make_model(
    family: str,
    params: dict[str, Any],
    args: argparse.Namespace,
) -> BaseEstimator:
    if family == "ridge":
        regressor = MiniBatchRidgeRegressor(
            alpha=float(params["alpha"]),
            learning_rate=float(params["learning_rate"]),
            epochs=int(args.ridge_epochs),
            batch_size=int(args.batch_size),
            random_state=int(args.random_state),
        )
        return Pipeline([("scale", StandardScaler()), ("model", regressor)])
    if family == "svm":
        regressor = MultiOutputRegressor(
            LinearSVR(
                C=float(params["C"]),
                epsilon=float(params.get("epsilon", 0.02)),
                loss="squared_epsilon_insensitive",
                dual="auto",
                max_iter=5_000,
                random_state=int(args.random_state),
            )
        )
        return Pipeline([("scale", StandardScaler()), ("model", regressor)])
    if family == "decision_tree":
        return DecisionTreeRegressor(
            max_depth=int(params["max_depth"]),
            min_samples_leaf=int(params["min_samples_leaf"]),
            random_state=int(args.random_state),
        )
    raise ValueError(f"Unknown model family: {family}")


def event_times_from_activation(
    frame_times: np.ndarray,
    activation: np.ndarray,
    *,
    threshold: float,
    minimum_interval: float,
) -> np.ndarray:
    if frame_times.size < 3:
        return np.empty(0, dtype=np.float64)
    activation = np.clip(np.asarray(activation, dtype=np.float64), 0.0, 1.0)
    hop = float(np.median(np.diff(frame_times)))
    distance_frames = max(1, int(round(minimum_interval / max(hop, 1e-12))))
    peak_indices, _properties = find_peaks(
        activation,
        height=threshold,
        distance=distance_frames,
        prominence=max(0.02, threshold * 0.10),
    )
    return frame_times[peak_indices]


def match_event_times(
    predicted: np.ndarray,
    actual: np.ndarray,
    *,
    tolerance: float,
    miss_penalty: float,
) -> EventMetrics:
    predicted = np.sort(np.asarray(predicted, dtype=np.float64))
    actual = np.sort(np.asarray(actual, dtype=np.float64))
    pred_index = actual_index = 0
    matched_errors: list[float] = []
    while pred_index < predicted.size and actual_index < actual.size:
        difference = predicted[pred_index] - actual[actual_index]
        if abs(difference) <= tolerance:
            matched_errors.append(abs(difference))
            pred_index += 1
            actual_index += 1
        elif difference < 0:
            pred_index += 1
        else:
            actual_index += 1

    matched = len(matched_errors)
    precision = matched / predicted.size if predicted.size else 0.0
    recall = matched / actual.size if actual.size else 0.0
    f1 = 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0

    if predicted.size and actual.size:
        actual_distance = np.min(np.abs(actual[:, None] - predicted[None, :]), axis=1)
        predicted_distance = np.min(np.abs(predicted[:, None] - actual[None, :]), axis=1)
        timing_cost = 0.5 * (
            float(np.mean(np.minimum(actual_distance, miss_penalty)))
            + float(np.mean(np.minimum(predicted_distance, miss_penalty)))
        )
    elif predicted.size or actual.size:
        timing_cost = miss_penalty
    else:
        timing_cost = 0.0

    return EventMetrics(
        precision=precision,
        recall=recall,
        f1=f1,
        matched_mae_ms=(1_000.0 * float(np.mean(matched_errors)) if matched_errors else math.nan),
        timing_cost_seconds=timing_cost,
        predicted_events=int(predicted.size),
        actual_events=int(actual.size),
        matched_events=matched,
    )


def _events_in_frame_range(events: np.ndarray, frame_times: np.ndarray) -> np.ndarray:
    if frame_times.size == 0:
        return np.empty(0, dtype=np.float64)
    return events[(events >= frame_times[0]) & (events <= frame_times[-1])]


def evaluate_activation_predictions(
    rows: pd.DataFrame,
    predictions: np.ndarray,
    timeline_by_track: dict[str, TimelineRecord],
    config: AnalysisConfig,
) -> dict[str, Any]:
    predictions = np.asarray(predictions, dtype=np.float64)
    if predictions.shape != (len(rows), 2):
        raise ValueError(f"Expected prediction shape {(len(rows), 2)}, got {predictions.shape}")

    beat_metrics: list[EventMetrics] = []
    downbeat_metrics: list[EventMetrics] = []
    per_track: dict[str, Any] = {}
    for track_id, track_rows in rows.groupby("track_id", sort=False):
        indices = track_rows.index.to_numpy()
        # rows passed here always have a RangeIndex in their own prediction order.
        local_positions = rows.index.get_indexer(indices)
        frame_times = track_rows["t"].to_numpy(dtype=np.float64)
        timeline = timeline_by_track[str(track_id)]
        actual_beats = _events_in_frame_range(timeline.beat_times, frame_times)
        actual_downbeats = _events_in_frame_range(timeline.downbeat_times, frame_times)
        predicted_beats = event_times_from_activation(
            frame_times,
            predictions[local_positions, 0],
            threshold=config.beat_peak_threshold,
            minimum_interval=config.min_beat_interval,
        )
        predicted_downbeats = event_times_from_activation(
            frame_times,
            predictions[local_positions, 1],
            threshold=config.downbeat_peak_threshold,
            minimum_interval=config.min_downbeat_interval,
        )
        beat = match_event_times(
            predicted_beats,
            actual_beats,
            tolerance=config.match_tolerance,
            miss_penalty=config.miss_penalty,
        )
        downbeat = match_event_times(
            predicted_downbeats,
            actual_downbeats,
            tolerance=config.match_tolerance,
            miss_penalty=config.miss_penalty,
        )
        beat_metrics.append(beat)
        downbeat_metrics.append(downbeat)
        per_track[str(track_id)] = {"beat": asdict(beat), "downbeat": asdict(downbeat)}

    def average(metrics: Sequence[EventMetrics]) -> dict[str, float]:
        keys = ("precision", "recall", "f1", "matched_mae_ms", "timing_cost_seconds")
        return {
            key: float(np.nanmean([getattr(metric, key) for metric in metrics]))
            for key in keys
        }

    beat_average = average(beat_metrics)
    downbeat_average = average(downbeat_metrics)
    event_cost = 0.5 * (
        beat_average["timing_cost_seconds"] + downbeat_average["timing_cost_seconds"]
    )
    return {
        "event_cost_seconds": event_cost,
        "beat": beat_average,
        "downbeat": downbeat_average,
        "per_track": per_track,
    }


def target_sample_weights(targets: np.ndarray) -> np.ndarray:
    """Counter sparse downbeats while preserving ordinary non-event frames."""
    targets = np.asarray(targets, dtype=np.float32)
    return (1.0 + 3.0 * targets[:, 0] + 8.0 * targets[:, 1]).astype(np.float32)


def _bounded_training_rows(
    X: np.ndarray,
    y: np.ndarray,
    weights: np.ndarray,
    max_rows: int | None,
    random_state: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if not max_rows or X.shape[0] <= max_rows:
        return X, y, weights
    generator = np.random.default_rng(random_state)
    important = np.flatnonzero(np.max(y, axis=1) >= 0.10)
    ordinary = np.flatnonzero(np.max(y, axis=1) < 0.10)
    ordinary_slots = max(0, max_rows - min(max_rows, important.size))
    if important.size > max_rows:
        selected = generator.choice(important, size=max_rows, replace=False)
    else:
        ordinary_selected = generator.choice(
            ordinary, size=min(ordinary_slots, ordinary.size), replace=False
        )
        selected = np.concatenate([important, ordinary_selected])
    selected.sort()
    return X[selected], y[selected], weights[selected]


def fit_model(
    model: BaseEstimator,
    family: str,
    X: np.ndarray,
    y: np.ndarray,
    args: argparse.Namespace,
) -> BaseEstimator:
    weights = target_sample_weights(y)
    max_rows = args.max_svm_training_rows if family == "svm" else None
    X_fit, y_fit, weights_fit = _bounded_training_rows(
        X, y, weights, max_rows, args.random_state
    )
    if family in {"ridge", "svm"}:
        model.fit(X_fit, y_fit, model__sample_weight=weights_fit)
    elif family == "decision_tree":
        model.fit(X_fit, y_fit, sample_weight=weights_fit)
    else:
        model.fit(X_fit, y_fit)
    return model


def model_parameter_grids(args: argparse.Namespace) -> dict[str, list[dict[str, Any]]]:
    if args.quick:
        ridge_alphas = args.ridge_alphas[:2]
        ridge_learning_rates = args.ridge_learning_rates[:1]
        svm_c_values = args.svm_c_values[:1]
        tree_depths = args.tree_depths[:1]
        tree_min_leaf = args.tree_min_leaf[:1]
    else:
        ridge_alphas = args.ridge_alphas
        ridge_learning_rates = args.ridge_learning_rates
        svm_c_values = args.svm_c_values
        tree_depths = args.tree_depths
        tree_min_leaf = args.tree_min_leaf
    return {
        "ridge": list(
            ParameterGrid(
                {"alpha": list(ridge_alphas), "learning_rate": list(ridge_learning_rates)}
            )
        ),
        "svm": list(ParameterGrid({"C": list(svm_c_values), "epsilon": [0.02]})),
        "decision_tree": list(
            ParameterGrid(
                {
                    "max_depth": list(tree_depths),
                    "min_samples_leaf": list(tree_min_leaf),
                }
            )
        ),
    }


def split_development_and_test_tracks(
    frame_df: pd.DataFrame,
    args: argparse.Namespace,
) -> tuple[set[str], set[str]]:
    track_ids = frame_df["track_id"].drop_duplicates().to_numpy()
    if track_ids.size < 4:
        raise ValueError("At least four independent tracks are required for grouped tuning/testing")
    splitter = GroupShuffleSplit(
        n_splits=1,
        test_size=float(args.test_track_fraction),
        random_state=int(args.random_state),
    )
    dummy = np.zeros(track_ids.size)
    development_indices, test_indices = next(splitter.split(dummy, groups=track_ids))
    return set(track_ids[development_indices]), set(track_ids[test_indices])


def tune_base_models(
    frame_df: pd.DataFrame,
    base_feature_names: Sequence[str],
    timelines: Sequence[TimelineRecord],
    development_tracks: set[str],
    config: AnalysisConfig,
    args: argparse.Namespace,
) -> tuple[dict[str, CandidateResult], pd.DataFrame]:
    timeline_by_track = {timeline.track_id: timeline for timeline in timelines}
    grids = model_parameter_grids(args)
    best: dict[str, CandidateResult] = {}
    all_results: list[CandidateResult] = []
    windows = config.window_seconds[:1] if args.quick else config.window_seconds

    for window_seconds in windows:
        print(f"building causal window features: {window_seconds:.3f}s", flush=True)
        window_df, window_features = build_causal_window_dataset(
            frame_df, base_feature_names, window_seconds, config.hop_seconds
        )
        development = window_df[window_df["track_id"].isin(development_tracks)].reset_index(drop=True)
        groups = development["track_id"].to_numpy()
        unique_group_count = np.unique(groups).size
        n_splits = min(int(args.cv_folds), unique_group_count)
        if n_splits < 2:
            raise ValueError("Grouped cross-validation requires at least two development tracks")
        splitter = GroupKFold(n_splits=n_splits)
        X = development.loc[:, window_features].to_numpy(dtype=np.float32)
        y = development.loc[:, TARGET_COLUMNS].to_numpy(dtype=np.float32)

        for family, candidates in grids.items():
            for params in candidates:
                fold_costs: list[float] = []
                fold_beat_f1: list[float] = []
                fold_downbeat_f1: list[float] = []
                fold_mse: list[float] = []
                started = time.perf_counter()
                for train_indices, validation_indices in splitter.split(X, y, groups):
                    model = make_model(family, params, args)
                    fit_model(model, family, X[train_indices], y[train_indices], args)
                    prediction = np.clip(model.predict(X[validation_indices]), 0.0, 1.0)
                    validation_rows = development.iloc[validation_indices].reset_index(drop=True)
                    metrics = evaluate_activation_predictions(
                        validation_rows,
                        prediction,
                        timeline_by_track,
                        config,
                    )
                    fold_costs.append(float(metrics["event_cost_seconds"]))
                    fold_beat_f1.append(float(metrics["beat"]["f1"]))
                    fold_downbeat_f1.append(float(metrics["downbeat"]["f1"]))
                    fold_mse.append(float(np.mean(np.square(prediction - y[validation_indices]))))

                result = CandidateResult(
                    family=family,
                    window_seconds=float(window_seconds),
                    params=dict(params),
                    validation_event_cost=float(np.mean(fold_costs)),
                    validation_beat_f1=float(np.mean(fold_beat_f1)),
                    validation_downbeat_f1=float(np.mean(fold_downbeat_f1)),
                    frame_mse=float(np.mean(fold_mse)),
                )
                all_results.append(result)
                incumbent = best.get(family)
                if incumbent is None or result.validation_event_cost < incumbent.validation_event_cost:
                    best[family] = result
                print(
                    f"  {family:13s} params={params} cost={result.validation_event_cost:.4f}s "
                    f"beat_f1={result.validation_beat_f1:.3f} "
                    f"downbeat_f1={result.validation_downbeat_f1:.3f} "
                    f"({time.perf_counter() - started:.1f}s)",
                    flush=True,
                )

    result_rows = []
    for result in all_results:
        row = asdict(result)
        row["params"] = json.dumps(row["params"], sort_keys=True)
        result_rows.append(row)
    return best, pd.DataFrame(result_rows).sort_values("validation_event_cost")


def fit_oof_and_final_base_models(
    frame_df: pd.DataFrame,
    base_feature_names: Sequence[str],
    best_candidates: dict[str, CandidateResult],
    development_tracks: set[str],
    test_tracks: set[str],
    args: argparse.Namespace,
) -> tuple[
    dict[str, BaseEstimator],
    dict[str, np.ndarray],
    dict[str, np.ndarray],
    pd.DataFrame,
    pd.DataFrame,
    np.ndarray,
]:
    final_models: dict[str, BaseEstimator] = {}
    development_predictions: dict[str, np.ndarray] = {}
    test_predictions: dict[str, np.ndarray] = {}
    development_rows: pd.DataFrame | None = None
    test_rows: pd.DataFrame | None = None
    development_targets: np.ndarray | None = None

    for family, candidate in best_candidates.items():
        window_df, window_features = build_causal_window_dataset(
            frame_df,
            base_feature_names,
            candidate.window_seconds,
            args.hop_seconds,
        )
        current_development = window_df[
            window_df["track_id"].isin(development_tracks)
        ].reset_index(drop=True)
        current_test = window_df[window_df["track_id"].isin(test_tracks)].reset_index(drop=True)
        if development_rows is None:
            metadata_columns = [
                column
                for column in current_development.columns
                if column in NON_FEATURE_COLUMNS
            ]
            development_rows = current_development.loc[:, metadata_columns].copy()
            test_rows = current_test.loc[:, metadata_columns].copy()
            development_targets = current_development.loc[:, TARGET_COLUMNS].to_numpy(
                dtype=np.float32
            )
        else:
            expected_development = development_rows[["track_id", "t"]].reset_index(drop=True)
            actual_development = current_development[["track_id", "t"]].reset_index(drop=True)
            expected_test = test_rows[["track_id", "t"]].reset_index(drop=True)
            actual_test = current_test[["track_id", "t"]].reset_index(drop=True)
            if not expected_development.equals(actual_development) or not expected_test.equals(actual_test):
                raise RuntimeError("Window datasets do not preserve frame ordering")

        X_development = current_development.loc[:, window_features].to_numpy(dtype=np.float32)
        y_development = current_development.loc[:, TARGET_COLUMNS].to_numpy(dtype=np.float32)
        X_test = current_test.loc[:, window_features].to_numpy(dtype=np.float32)
        groups = current_development["track_id"].to_numpy()
        n_splits = min(int(args.cv_folds), np.unique(groups).size)
        splitter = GroupKFold(n_splits=n_splits)
        oof = np.zeros_like(y_development, dtype=np.float32)
        for train_indices, validation_indices in splitter.split(X_development, y_development, groups):
            fold_model = make_model(family, candidate.params, args)
            fit_model(
                fold_model,
                family,
                X_development[train_indices],
                y_development[train_indices],
                args,
            )
            oof[validation_indices] = np.clip(
                fold_model.predict(X_development[validation_indices]), 0.0, 1.0
            )
        development_predictions[family] = oof

        final_model = make_model(family, candidate.params, args)
        fit_model(final_model, family, X_development, y_development, args)
        final_models[family] = final_model
        test_predictions[family] = np.clip(final_model.predict(X_test), 0.0, 1.0)

    if development_rows is None or test_rows is None or development_targets is None:
        raise RuntimeError("No base models were fit")
    return (
        final_models,
        development_predictions,
        test_predictions,
        development_rows,
        test_rows,
        development_targets,
    )


def fit_stacked_random_forest(
    development_predictions: dict[str, np.ndarray],
    test_predictions: dict[str, np.ndarray],
    development_targets: np.ndarray,
    args: argparse.Namespace,
) -> tuple[RandomForestRegressor, np.ndarray, list[str]]:
    families = sorted(development_predictions)
    meta_feature_names = [
        f"{family}_{target.removesuffix('_target')}_activation"
        for family in families
        for target in TARGET_COLUMNS
    ]
    meta_development = np.column_stack([development_predictions[family] for family in families])
    meta_test = np.column_stack([test_predictions[family] for family in families])
    forest = RandomForestRegressor(
        n_estimators=(50 if args.quick else int(args.forest_estimators)),
        max_depth=int(args.forest_max_depth),
        min_samples_leaf=10,
        max_features="sqrt",
        n_jobs=-1,
        random_state=int(args.random_state),
    )
    forest.fit(
        meta_development,
        development_targets,
        sample_weight=target_sample_weights(development_targets),
    )
    return forest, np.clip(forest.predict(meta_test), 0.0, 1.0), meta_feature_names


def compute_feature_correlations(
    frame_df: pd.DataFrame,
    feature_names: Sequence[str],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for feature in feature_names:
        values = frame_df[feature].to_numpy(dtype=np.float64)
        finite = np.isfinite(values)
        if finite.sum() < 3 or float(np.nanstd(values[finite])) <= 1e-12:
            beat_correlation = downbeat_correlation = 0.0
            beat_pvalue = downbeat_pvalue = 1.0
        else:
            beat_result = pointbiserialr(
                frame_df.loc[finite, "is_beat"].to_numpy(), values[finite]
            )
            downbeat_result = pointbiserialr(
                frame_df.loc[finite, "is_downbeat"].to_numpy(), values[finite]
            )
            beat_correlation = float(np.nan_to_num(beat_result.statistic))
            downbeat_correlation = float(np.nan_to_num(downbeat_result.statistic))
            beat_pvalue = float(np.nan_to_num(beat_result.pvalue, nan=1.0))
            downbeat_pvalue = float(np.nan_to_num(downbeat_result.pvalue, nan=1.0))
        rows.append(
            {
                "feature": feature,
                "beat_correlation": beat_correlation,
                "downbeat_correlation": downbeat_correlation,
                "beat_pvalue": beat_pvalue,
                "downbeat_pvalue": downbeat_pvalue,
                "max_absolute_correlation": max(
                    abs(beat_correlation), abs(downbeat_correlation)
                ),
            }
        )
    return pd.DataFrame(rows).sort_values("max_absolute_correlation", ascending=False)


def plot_feature_correlation_heatmap(
    correlations: pd.DataFrame,
    output_path: Path,
    *,
    top_n: int = 24,
) -> None:
    selected = correlations.head(top_n).set_index("feature")
    plot_data = selected[["beat_correlation", "downbeat_correlation"]]
    figure_height = max(6.0, 0.30 * len(plot_data))
    figure, axis = plt.subplots(figsize=(9, figure_height))
    sns.heatmap(
        plot_data,
        annot=True,
        fmt=".3f",
        center=0.0,
        cmap="vlag",
        linewidths=0.3,
        cbar_kws={"label": "point-biserial correlation"},
        ax=axis,
    )
    axis.set_title("Strongest frame-feature correlations with verified events")
    axis.set_xlabel("Verified target")
    axis.set_ylabel("Feature")
    figure.tight_layout()
    figure.savefig(output_path, dpi=170)
    plt.close(figure)


def choose_sample_track(
    available_tracks: Iterable[str],
    requested_substring: str,
) -> str:
    tracks = list(available_tracks)
    if not tracks:
        raise ValueError("No tracks are available for sample plotting")
    if requested_substring:
        lowered = requested_substring.casefold()
        matches = [track for track in tracks if lowered in track.casefold()]
        if not matches:
            raise ValueError(f"--sample-track did not match a held-out track: {requested_substring}")
        return matches[0]
    return tracks[0]


def plot_correlated_features_for_sample(
    frame_df: pd.DataFrame,
    correlations: pd.DataFrame,
    timeline: TimelineRecord,
    output_path: Path,
    *,
    sample_start: float,
    sample_duration: float,
    top_n: int = 8,
) -> None:
    sample_end = sample_start + sample_duration
    sample = frame_df[
        (frame_df["track_id"] == timeline.track_id)
        & (frame_df["t"] >= sample_start)
        & (frame_df["t"] <= sample_end)
    ].copy()
    if sample.empty:
        track_rows = frame_df[frame_df["track_id"] == timeline.track_id]
        if track_rows.empty:
            raise ValueError(f"No feature rows for sample track {timeline.track_id}")
        sample_start = float(track_rows["t"].min())
        sample_end = min(float(track_rows["t"].max()), sample_start + sample_duration)
        sample = track_rows[
            (track_rows["t"] >= sample_start) & (track_rows["t"] <= sample_end)
        ].copy()

    selected_features = correlations.head(top_n)["feature"].tolist()
    figure, axis = plt.subplots(figsize=(15, 8))
    for feature in selected_features:
        values = sample[feature].to_numpy(dtype=np.float64)
        centered = values - float(np.mean(values))
        scale = float(np.std(centered))
        normalized = centered / scale if scale > 1e-12 else centered
        axis.plot(sample["t"], normalized, linewidth=1.0, alpha=0.80, label=feature)

    beats = timeline.beat_times[
        (timeline.beat_times >= sample_start) & (timeline.beat_times <= sample_end)
    ]
    downbeats = timeline.downbeat_times[
        (timeline.downbeat_times >= sample_start) & (timeline.downbeat_times <= sample_end)
    ]
    for event_time in beats:
        axis.axvline(event_time, color="#555555", alpha=0.20, linewidth=0.8)
    for event_time in downbeats:
        axis.axvline(event_time, color="#d62728", alpha=0.70, linewidth=1.4)
    axis.set_title(
        console_safe(f"Top correlated features around verified events\n{timeline.track_id}")
    )
    axis.set_xlabel("Time (seconds)")
    axis.set_ylabel("Within-sample standardized feature value")
    axis.legend(loc="upper right", ncol=2, fontsize=8)
    axis.grid(alpha=0.15)
    figure.tight_layout()
    figure.savefig(output_path, dpi=170)
    plt.close(figure)


def flatten_model_metrics(model_metrics: dict[str, dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for model_name, metrics in model_metrics.items():
        rows.append(
            {
                "model": model_name,
                "event_cost_seconds": metrics["event_cost_seconds"],
                "beat_f1": metrics["beat"]["f1"],
                "downbeat_f1": metrics["downbeat"]["f1"],
                "beat_timing_mae_ms": metrics["beat"]["matched_mae_ms"],
                "downbeat_timing_mae_ms": metrics["downbeat"]["matched_mae_ms"],
            }
        )
    return pd.DataFrame(rows).sort_values("event_cost_seconds")


def plot_model_comparison(metrics_df: pd.DataFrame, output_path: Path) -> None:
    figure, axes = plt.subplots(1, 2, figsize=(14, 5))
    f1_long = metrics_df.melt(
        id_vars="model",
        value_vars=["beat_f1", "downbeat_f1"],
        var_name="target",
        value_name="F1",
    )
    sns.barplot(data=f1_long, x="model", y="F1", hue="target", ax=axes[0])
    axes[0].set_ylim(0.0, 1.0)
    axes[0].set_title("Held-out event F1 (± timing tolerance)")
    axes[0].tick_params(axis="x", rotation=20)
    sns.barplot(
        data=metrics_df,
        x="model",
        y="event_cost_seconds",
        color="#4c72b0",
        ax=axes[1],
    )
    axes[1].set_title("Held-out symmetric event-time cost (lower is better)")
    axes[1].set_ylabel("Seconds")
    axes[1].tick_params(axis="x", rotation=20)
    figure.tight_layout()
    figure.savefig(output_path, dpi=170)
    plt.close(figure)


def plot_sample_predictions(
    test_rows: pd.DataFrame,
    predictions_by_model: dict[str, np.ndarray],
    timeline: TimelineRecord,
    output_path: Path,
    *,
    sample_start: float,
    sample_duration: float,
) -> None:
    track_mask = test_rows["track_id"].eq(timeline.track_id).to_numpy()
    track_positions = np.flatnonzero(track_mask)
    track_rows = test_rows.iloc[track_positions]
    sample_end = sample_start + sample_duration
    interval_mask = (
        (track_rows["t"].to_numpy() >= sample_start)
        & (track_rows["t"].to_numpy() <= sample_end)
    )
    if not np.any(interval_mask):
        sample_start = float(track_rows["t"].min())
        sample_end = min(float(track_rows["t"].max()), sample_start + sample_duration)
        interval_mask = (
            (track_rows["t"].to_numpy() >= sample_start)
            & (track_rows["t"].to_numpy() <= sample_end)
        )
    sample_positions = track_positions[interval_mask]
    sample_times = test_rows.iloc[sample_positions]["t"].to_numpy(dtype=np.float64)

    figure, axes = plt.subplots(2, 1, figsize=(15, 9), sharex=True)
    for model_name, predictions in predictions_by_model.items():
        axes[0].plot(sample_times, predictions[sample_positions, 0], label=model_name, alpha=0.85)
        axes[1].plot(sample_times, predictions[sample_positions, 1], label=model_name, alpha=0.85)
    beats = timeline.beat_times[
        (timeline.beat_times >= sample_start) & (timeline.beat_times <= sample_end)
    ]
    downbeats = timeline.downbeat_times[
        (timeline.downbeat_times >= sample_start) & (timeline.downbeat_times <= sample_end)
    ]
    for event_time in beats:
        axes[0].axvline(event_time, color="#222222", alpha=0.35, linewidth=0.9)
    for event_time in downbeats:
        axes[1].axvline(event_time, color="#d62728", alpha=0.65, linewidth=1.2)
    axes[0].set_title(console_safe(f"Predicted beat activation — {timeline.track_id}"))
    axes[1].set_title("Predicted downbeat activation")
    axes[0].set_ylabel("Activation")
    axes[1].set_ylabel("Activation")
    axes[1].set_xlabel("Time (seconds)")
    for axis in axes:
        axis.set_ylim(-0.05, 1.05)
        axis.grid(alpha=0.15)
        axis.legend(loc="upper right")
    figure.tight_layout()
    figure.savefig(output_path, dpi=170)
    plt.close(figure)


def plot_ridge_loss(model: BaseEstimator, output_path: Path) -> None:
    if not isinstance(model, Pipeline):
        return
    ridge = model.named_steps.get("model")
    history = getattr(ridge, "loss_history_", None)
    if not history:
        return
    figure, axis = plt.subplots(figsize=(8, 4))
    axis.plot(np.arange(1, len(history) + 1), history, marker="o", markersize=3)
    axis.set_title("Final mini-batch Ridge training objective")
    axis.set_xlabel("Epoch")
    axis.set_ylabel("Weighted MSE + L2 penalty")
    axis.grid(alpha=0.20)
    figure.tight_layout()
    figure.savefig(output_path, dpi=170)
    plt.close(figure)


def plot_stacking_importance(
    forest: RandomForestRegressor,
    feature_names: Sequence[str],
    output_path: Path,
) -> None:
    importances = pd.DataFrame(
        {"meta_feature": list(feature_names), "importance": forest.feature_importances_}
    ).sort_values("importance", ascending=False)
    figure, axis = plt.subplots(figsize=(10, 5))
    sns.barplot(data=importances, x="importance", y="meta_feature", color="#55a868", ax=axis)
    axis.set_title("Random-Forest stack: base-model feedback importance")
    figure.tight_layout()
    figure.savefig(output_path, dpi=170)
    plt.close(figure)


def json_ready(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_ready(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.repo_root = args.repo_root.resolve()
    args.output_dir = resolve_path(args.output_dir, args.repo_root)
    if args.quick:
        args.ridge_epochs = min(args.ridge_epochs, 12)
        args.cv_folds = min(args.cv_folds, 2)
        args.forest_estimators = min(args.forest_estimators, 50)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    sns.set_theme(style="whitegrid", context="notebook")

    config = AnalysisConfig(
        sample_rate=int(args.sample_rate),
        frame_size=int(args.frame_size),
        hop_seconds=float(args.hop_seconds),
        n_mels=int(args.n_mels),
        n_mfcc=int(args.n_mfcc),
        label_tolerance=float(args.label_tolerance),
        target_sigma=float(args.target_sigma),
        match_tolerance=float(args.match_tolerance),
        miss_penalty=float(args.miss_penalty),
        beat_peak_threshold=float(args.beat_peak_threshold),
        downbeat_peak_threshold=float(args.downbeat_peak_threshold),
        min_beat_interval=float(args.min_beat_interval),
        min_downbeat_interval=float(args.min_downbeat_interval),
        window_seconds=tuple(float(value) for value in args.window_seconds),
        random_state=int(args.random_state),
    )
    if config.frame_size <= 0 or config.sample_rate <= 0 or config.hop_seconds <= 0:
        raise ValueError("sample rate, frame size, and hop duration must be positive")
    if config.target_sigma <= 0 or config.label_tolerance <= 0:
        raise ValueError("target sigma and label tolerance must be positive")

    timelines = load_verified_timelines(args)
    timeline_by_track = {timeline.track_id: timeline for timeline in timelines}
    manifest = pd.DataFrame(
        [
            {
                "track_id": timeline.track_id,
                "show_path": str(timeline.show_path),
                "audio_path": str(timeline.audio_path),
                "duration": timeline.duration,
                "beats": timeline.beat_times.size,
                "downbeats": timeline.downbeat_times.size,
                "time_signature": timeline.time_signature,
                "verified": True,
            }
            for timeline in timelines
        ]
    )
    manifest.to_csv(args.output_dir / "verified_dataset_manifest.csv", index=False)
    print(
        f"verified dataset: {len(timelines)} tracks, {manifest['duration'].sum() / 60.0:.1f} min, "
        f"{manifest['beats'].sum():,} beats, {manifest['downbeats'].sum():,} downbeats",
        flush=True,
    )

    frame_df, base_feature_names = prepare_frame_dataset(
        timelines,
        config,
        max_track_seconds=args.max_track_seconds,
    )
    if not args.no_save_features:
        feature_path = args.output_dir / "frame_features.csv.gz"
        print(f"writing {feature_path}", flush=True)
        frame_df.to_csv(feature_path, index=False, compression="gzip")

    correlations = compute_feature_correlations(frame_df, base_feature_names)
    correlations.to_csv(args.output_dir / "feature_correlations.csv", index=False)
    plot_feature_correlation_heatmap(
        correlations, args.output_dir / "feature_correlation_heatmap.png"
    )

    development_tracks, test_tracks = split_development_and_test_tracks(frame_df, args)
    split_rows = [
        {"track_id": track_id, "split": "development"}
        for track_id in sorted(development_tracks)
    ] + [{"track_id": track_id, "split": "test"} for track_id in sorted(test_tracks)]
    pd.DataFrame(split_rows).to_csv(args.output_dir / "track_split.csv", index=False)
    print(
        console_safe(f"development tracks ({len(development_tracks)}): {sorted(development_tracks)}"),
        flush=True,
    )
    print(
        console_safe(f"held-out test tracks ({len(test_tracks)}): {sorted(test_tracks)}"),
        flush=True,
    )

    best_candidates, grid_results = tune_base_models(
        frame_df,
        base_feature_names,
        timelines,
        development_tracks,
        config,
        args,
    )
    grid_results.to_csv(args.output_dir / "grid_search_results.csv", index=False)
    (args.output_dir / "best_hyperparameters.json").write_text(
        json.dumps(
            json_ready({family: asdict(result) for family, result in best_candidates.items()}),
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    (
        final_models,
        development_predictions,
        test_predictions,
        _development_rows,
        test_rows,
        development_targets,
    ) = fit_oof_and_final_base_models(
        frame_df,
        base_feature_names,
        best_candidates,
        development_tracks,
        test_tracks,
        args,
    )
    forest, stacked_test_prediction, meta_feature_names = fit_stacked_random_forest(
        development_predictions,
        test_predictions,
        development_targets,
        args,
    )
    predictions_by_model = {
        "Ridge (mini-batch GD)": test_predictions["ridge"],
        "Linear SVM": test_predictions["svm"],
        "Decision Tree": test_predictions["decision_tree"],
        "Stacked Random Forest": stacked_test_prediction,
    }
    model_metrics = {
        model_name: evaluate_activation_predictions(
            test_rows.reset_index(drop=True),
            prediction,
            timeline_by_track,
            config,
        )
        for model_name, prediction in predictions_by_model.items()
    }
    prediction_frame = test_rows.loc[
        :, ["track_id", "show_path", "audio_path", "t", *INDICATOR_COLUMNS, *TARGET_COLUMNS]
    ].copy()
    prediction_prefixes = {
        "Ridge (mini-batch GD)": "ridge",
        "Linear SVM": "svm",
        "Decision Tree": "decision_tree",
        "Stacked Random Forest": "stacked_random_forest",
    }
    for model_name, prediction in predictions_by_model.items():
        prefix = prediction_prefixes[model_name]
        prediction_frame[f"{prefix}_beat_activation"] = prediction[:, 0]
        prediction_frame[f"{prefix}_downbeat_activation"] = prediction[:, 1]
    prediction_frame.to_csv(
        args.output_dir / "held_out_frame_predictions.csv.gz",
        index=False,
        compression="gzip",
    )
    metrics_df = flatten_model_metrics(model_metrics)
    metrics_df.to_csv(args.output_dir / "held_out_model_metrics.csv", index=False)
    (args.output_dir / "held_out_model_metrics.json").write_text(
        json.dumps(json_ready(model_metrics), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    plot_model_comparison(metrics_df, args.output_dir / "model_comparison.png")
    plot_ridge_loss(final_models["ridge"], args.output_dir / "ridge_training_loss.png")
    plot_stacking_importance(
        forest,
        meta_feature_names,
        args.output_dir / "stacking_model_importance.png",
    )

    sample_track_id = choose_sample_track(sorted(test_tracks), args.sample_track)
    sample_timeline = timeline_by_track[sample_track_id]
    plot_correlated_features_for_sample(
        frame_df,
        correlations,
        sample_timeline,
        args.output_dir / "sample_correlated_features.png",
        sample_start=float(args.sample_start),
        sample_duration=float(args.sample_duration),
    )
    plot_sample_predictions(
        test_rows.reset_index(drop=True),
        predictions_by_model,
        sample_timeline,
        args.output_dir / "sample_model_predictions.png",
        sample_start=float(args.sample_start),
        sample_duration=float(args.sample_duration),
    )

    run_summary = {
        "arguments": vars(args),
        "analysis_config": asdict(config),
        "verified_show_files": [str(timeline.show_path) for timeline in timelines],
        "development_tracks": sorted(development_tracks),
        "test_tracks": sorted(test_tracks),
        "base_feature_count": len(base_feature_names),
        "frame_count": len(frame_df),
        "best_hyperparameters": {
            family: asdict(result) for family, result in best_candidates.items()
        },
        "held_out_metrics": model_metrics,
    }
    (args.output_dir / "run_summary.json").write_text(
        json.dumps(json_ready(run_summary), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("\nHeld-out results:", flush=True)
    print(metrics_df.to_string(index=False), flush=True)
    print(f"\nArtifacts written to {args.output_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
