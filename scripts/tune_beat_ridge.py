"""Tune DreamSync's causal Ridge beat model and temporal decoders.

This script consumes the cached frame-level data produced by
``beat_model_analysis.py``.  It intentionally does not decode MP3 files for
each trial.  The training portion is a pair of mini-batch Ridge regressors:
one for beat activation and one for downbeat activation.  A causal beat-grid
decoder then filters isolated activations, and a four-state downbeat phase
decoder selects bar starts from the decoded beat sequence.

Stages:

    python scripts/tune_beat_ridge.py --stage ridge
    python scripts/tune_beat_ridge.py --stage decoder
    python scripts/tune_beat_ridge.py --stage all

``--stage decoder`` reuses ``best_ridge_config.json`` from the output folder.
``--stage all`` runs both stages and then fits the selected configuration on
all development tracks before evaluating the held-out tracks.

The Ridge optimizer minimizes a differentiable weighted activation objective.
The decoder is selected with event timing/F1 metrics because peak-to-annotation
matching is discrete and cannot be differentiated through directly.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
    import seaborn as sns
    from sklearn.model_selection import GroupKFold
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
except ModuleNotFoundError as exc:  # pragma: no cover - dependency guard
    raise SystemExit(
        f"Missing dependency {exc.name!r}. Install pandas, matplotlib, seaborn, "
        "scipy, and scikit-learn before running this tuner."
    ) from exc

from beat_model_analysis import (  # noqa: E402
    AnalysisConfig,
    MiniBatchRidgeRegressor,
    TimelineRecord,
    build_causal_window_dataset,
    console_safe,
    json_ready,
    load_verified_timelines,
    match_event_times,
)


def plot_safe(value: object) -> str:
    """Return a portable ASCII label for matplotlib titles on Windows."""
    text = str(value).encode("ascii", errors="ignore").decode("ascii").strip()
    return text or "track"


TARGET_COLUMNS = ("beat_target", "downbeat_target")
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


@dataclass(frozen=True)
class RidgeConfig:
    window_seconds: float
    alpha: float
    learning_rate: float
    batch_size: int
    epochs: int
    target_sigma: float
    beat_weight: float
    downbeat_weight: float


@dataclass(frozen=True)
class DecoderConfig:
    beat_threshold: float
    downbeat_threshold: float
    min_beat_interval: float
    min_bpm: float
    max_bpm: float
    period_tolerance: float
    history_beats: int
    meter: int
    phase_margin: float
    phase_penalty: float


@dataclass(frozen=True)
class EventSummary:
    precision: float
    recall: float
    f1: float
    matched_mae_ms: float
    timing_cost_seconds: float
    predicted_events: int
    actual_events: int
    matched_events: int


DEFAULT_DECODER = DecoderConfig(
    beat_threshold=0.30,
    downbeat_threshold=0.25,
    min_beat_interval=0.24,
    min_bpm=80.0,
    max_bpm=200.0,
    period_tolerance=0.28,
    history_beats=8,
    meter=4,
    phase_margin=0.05,
    phase_penalty=0.15,
)


def parse_float_list(value: str) -> tuple[float, ...]:
    result = tuple(float(part.strip()) for part in value.split(",") if part.strip())
    if not result:
        raise argparse.ArgumentTypeError("expected a comma-separated float list")
    return result


def parse_int_list(value: str) -> tuple[int, ...]:
    result = tuple(int(part.strip()) for part in value.split(",") if part.strip())
    if not result:
        raise argparse.ArgumentTypeError("expected a comma-separated integer list")
    return result


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("ridge", "decoder", "all"), default="all")
    parser.add_argument(
        "--features",
        type=Path,
        default=Path("out/beat-ml-analysis/frame_features.csv.gz"),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("out/beat-ml-analysis/verified_dataset_manifest.csv"),
    )
    parser.add_argument(
        "--splits",
        type=Path,
        default=Path("out/beat-ml-analysis/track_split.csv"),
    )
    parser.add_argument("--output-dir", type=Path, default=Path("out/beat-ridge-tuning"))
    # Evaluate bar-scale context first; the short 0.5 s window remains as a
    # baseline but is intentionally last because it cannot reliably encode
    # downbeat phase.
    parser.add_argument("--window-seconds", type=parse_float_list, default=(2.0, 5.0, 10.0, 1.0, 0.5))
    parser.add_argument("--alphas", type=parse_float_list, default=(1e-3, 1e-2, 1e-1))
    parser.add_argument("--learning-rates", type=parse_float_list, default=(0.005, 0.01))
    parser.add_argument("--batch-sizes", type=parse_int_list, default=(1024, 2048))
    parser.add_argument("--epochs", type=parse_int_list, default=(35,))
    parser.add_argument("--early-stopping-patience", type=int, default=5)
    parser.add_argument("--early-stopping-tolerance", type=float, default=1e-6)
    parser.add_argument("--target-sigmas", type=parse_float_list, default=(0.04,))
    parser.add_argument("--beat-weights", type=parse_float_list, default=(3.0,))
    parser.add_argument("--downbeat-weights", type=parse_float_list, default=(8.0, 12.0))
    parser.add_argument("--cv-folds", type=int, default=3)
    parser.add_argument("--random-state", type=int, default=73)
    parser.add_argument("--sample-track", default="")
    parser.add_argument("--sample-start", type=float, default=30.0)
    parser.add_argument("--sample-duration", type=float, default=12.0)
    parser.add_argument("--match-tolerance", type=float, default=0.070)
    parser.add_argument(
        "--evaluation-tolerances",
        type=parse_float_list,
        default=(0.020, 0.040, 0.070, 0.100),
        help="Comma-separated event matching tolerances used for quality curves.",
    )
    parser.add_argument("--miss-penalty", type=float, default=0.500)
    parser.add_argument("--decoder-beat-thresholds", type=parse_float_list, default=(0.20, 0.30, 0.40, 0.50))
    parser.add_argument("--decoder-downbeat-thresholds", type=parse_float_list, default=(0.15, 0.25, 0.35))
    parser.add_argument("--decoder-min-intervals", type=parse_float_list, default=(0.24, 0.30))
    parser.add_argument("--decoder-period-tolerances", type=parse_float_list, default=(0.20, 0.28, 0.36))
    parser.add_argument("--decoder-phase-margins", type=parse_float_list, default=(0.02, 0.05, 0.10))
    parser.add_argument("--max-track-seconds", type=float, default=None)
    parser.add_argument("--quick", action="store_true")
    return parser.parse_args(argv)


def resolve_path(path: Path, repo_root: Path) -> Path:
    return path.resolve() if path.is_absolute() else (repo_root / path).resolve()


def load_dataset(args: argparse.Namespace) -> tuple[pd.DataFrame, list[TimelineRecord], set[str], set[str]]:
    repo_root = REPO_ROOT.resolve()
    feature_path = resolve_path(args.features, repo_root)
    manifest_path = resolve_path(args.manifest, repo_root)
    split_path = resolve_path(args.splits, repo_root)
    if not feature_path.is_file():
        raise FileNotFoundError(f"Feature cache not found: {feature_path}")
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Dataset manifest not found: {manifest_path}")
    if not split_path.is_file():
        raise FileNotFoundError(f"Track split not found: {split_path}")

    frame_df = pd.read_csv(feature_path)
    if args.max_track_seconds is not None:
        frame_df = frame_df.loc[frame_df["t"] <= float(args.max_track_seconds)].copy()
        if frame_df.empty:
            raise ValueError("--max-track-seconds removed every feature row")
    manifest = pd.read_csv(manifest_path)
    split_df = pd.read_csv(split_path)
    if "track_id" not in frame_df or "track_id" not in manifest or "track_id" not in split_df:
        raise ValueError("Feature, manifest, and split files must contain track_id")

    show_paths = [Path(path) for path in manifest["show_path"].dropna().astype(str)]
    timeline_args = SimpleNamespace(repo_root=repo_root, show_paths=show_paths)
    timelines = load_verified_timelines(timeline_args)
    timeline_ids = {timeline.track_id for timeline in timelines}
    frame_ids = set(frame_df["track_id"].astype(str).unique())
    if frame_ids != timeline_ids:
        raise ValueError(
            "Feature cache and verified manifest disagree: "
            f"missing_features={sorted(timeline_ids - frame_ids)}, "
            f"unlabeled_features={sorted(frame_ids - timeline_ids)}"
        )

    development_tracks = set(
        split_df.loc[split_df["split"].eq("development"), "track_id"].astype(str)
    )
    test_tracks = set(split_df.loc[split_df["split"].eq("test"), "track_id"].astype(str))
    if not development_tracks or not test_tracks:
        raise ValueError("Track split must contain both development and test tracks")
    return frame_df, timelines, development_tracks, test_tracks


def feature_columns(frame_df: pd.DataFrame) -> list[str]:
    columns = [
        column
        for column in frame_df.columns
        if column not in NON_FEATURE_COLUMNS and "__" not in column
    ]
    if not columns:
        raise ValueError("No base feature columns found in frame cache")
    return columns


def target_arrays(
    rows: pd.DataFrame,
    target_sigma: float,
) -> tuple[np.ndarray, np.ndarray]:
    if target_sigma <= 0:
        raise ValueError("target_sigma must be positive")
    beat_distance = rows["nearest_beat_distance"].to_numpy(dtype=np.float32)
    downbeat_distance = rows["nearest_downbeat_distance"].to_numpy(dtype=np.float32)
    beat_target = np.exp(-0.5 * np.square(beat_distance / target_sigma)).astype(np.float32)
    downbeat_target = np.exp(-0.5 * np.square(downbeat_distance / target_sigma)).astype(np.float32)
    return beat_target, downbeat_target


def fit_ridge_pair(
    X: np.ndarray,
    beat_target: np.ndarray,
    downbeat_target: np.ndarray,
    config: RidgeConfig,
    args: argparse.Namespace,
) -> tuple[Pipeline, Pipeline]:
    common = {
        "alpha": config.alpha,
        "learning_rate": config.learning_rate,
        "epochs": config.epochs,
        "batch_size": config.batch_size,
        "patience": max(1, int(args.early_stopping_patience)),
        "tolerance": max(0.0, float(args.early_stopping_tolerance)),
        "random_state": args.random_state,
    }
    beat_model = Pipeline(
        [("scale", StandardScaler()), ("model", MiniBatchRidgeRegressor(**common))]
    )
    downbeat_model = Pipeline(
        [("scale", StandardScaler()), ("model", MiniBatchRidgeRegressor(**common))]
    )
    beat_weights = 1.0 + config.beat_weight * beat_target
    downbeat_weights = 1.0 + config.downbeat_weight * downbeat_target
    beat_model.fit(X, beat_target, model__sample_weight=beat_weights)
    downbeat_model.fit(X, downbeat_target, model__sample_weight=downbeat_weights)
    return beat_model, downbeat_model


def predict_pair(
    models: tuple[Pipeline, Pipeline],
    X: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    beat, downbeat = models[0].predict(X), models[1].predict(X)
    return np.clip(np.asarray(beat, dtype=np.float64), 0.0, 1.0), np.clip(
        np.asarray(downbeat, dtype=np.float64), 0.0, 1.0
    )


def frame_nearest_index(frame_times: np.ndarray, event_time: float) -> int:
    index = int(np.searchsorted(frame_times, event_time, side="left"))
    if index <= 0:
        return 0
    if index >= frame_times.size:
        return frame_times.size - 1
    left = index - 1
    return left if abs(frame_times[left] - event_time) <= abs(frame_times[index] - event_time) else index


class CausalBeatGridDecoder:
    """Causal refractory/tempo decoder for frame-level beat activations."""

    def __init__(self, config: DecoderConfig) -> None:
        self.config = config

    def _causal_candidates(self, times: np.ndarray, scores: np.ndarray) -> list[tuple[float, float]]:
        candidate_index: int | None = None
        emitted: list[tuple[float, float]] = []
        for index, (time_value, score) in enumerate(zip(times, scores)):
            if score >= self.config.beat_threshold:
                if candidate_index is None or score >= scores[candidate_index]:
                    candidate_index = index
            if candidate_index is not None:
                candidate_age = time_value - times[candidate_index]
                if candidate_age >= self.config.min_beat_interval:
                    emitted.append((float(times[candidate_index]), float(scores[candidate_index])))
                    candidate_index = None
            # A quiet gap commits the best candidate using only past samples.
            if candidate_index is not None and score < self.config.beat_threshold:
                if index + 1 < times.size and times[index + 1] - times[candidate_index] >= self.config.min_beat_interval:
                    emitted.append((float(times[candidate_index]), float(scores[candidate_index])))
                    candidate_index = None
        if candidate_index is not None:
            emitted.append((float(times[candidate_index]), float(scores[candidate_index])))
        return emitted

    def _normalized_period(self, intervals: np.ndarray) -> float | None:
        if intervals.size == 0:
            return None
        raw_period = float(np.median(intervals[-self.config.history_beats :]))
        if raw_period <= 0:
            return None
        bpm = 60.0 / raw_period
        while bpm < self.config.min_bpm:
            bpm *= 2.0
        while bpm > self.config.max_bpm:
            bpm *= 0.5
        return 60.0 / bpm

    def decode(self, times: np.ndarray, scores: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        times = np.asarray(times, dtype=np.float64)
        scores = np.clip(np.asarray(scores, dtype=np.float64), 0.0, 1.0)
        candidates = self._causal_candidates(times, scores)
        accepted_times: list[float] = []
        accepted_scores: list[float] = []
        for candidate_time, candidate_score in candidates:
            if not accepted_times:
                accepted_times.append(candidate_time)
                accepted_scores.append(candidate_score)
                continue
            interval = candidate_time - accepted_times[-1]
            if interval < self.config.min_beat_interval:
                if candidate_score > accepted_scores[-1]:
                    accepted_times[-1] = candidate_time
                    accepted_scores[-1] = candidate_score
                continue
            period = self._normalized_period(np.diff(accepted_times))
            if period is None:
                accepted_times.append(candidate_time)
                accepted_scores.append(candidate_score)
                continue
            multiples = max(1, int(round(interval / period)))
            expected = multiples * period
            error = abs(interval - expected) / period
            if error <= self.config.period_tolerance or interval >= 1.5 * period:
                accepted_times.append(candidate_time)
                accepted_scores.append(candidate_score)
        return np.asarray(accepted_times, dtype=np.float64), np.asarray(accepted_scores, dtype=np.float64)


class DownbeatPhaseDecoder:
    """Select bar phase from downbeat evidence at decoded beat locations."""

    def __init__(self, config: DecoderConfig) -> None:
        self.config = config

    def decode(
        self,
        beat_times: np.ndarray,
        frame_times: np.ndarray,
        downbeat_scores: np.ndarray,
    ) -> np.ndarray:
        if beat_times.size == 0:
            return np.empty(0, dtype=np.float64)
        evidence = np.asarray(
            [downbeat_scores[frame_nearest_index(frame_times, value)] for value in beat_times],
            dtype=np.float64,
        )
        phase_scores = np.zeros(self.config.meter, dtype=np.float64)
        locked_phase: int | None = None
        downbeats: list[float] = []
        for beat_index, (beat_time, score) in enumerate(zip(beat_times, evidence)):
            for phase in range(self.config.meter):
                phase_scores[phase] += score if beat_index % self.config.meter == phase else -self.config.phase_penalty * score
            ranking = np.argsort(phase_scores)[::-1]
            best_phase = int(ranking[0])
            margin = phase_scores[ranking[0]] - phase_scores[ranking[1]]
            total_evidence = float(np.sum(evidence[: beat_index + 1]))
            # Do not lock on the first bar.  A spurious first beat otherwise
            # shifts every subsequent downbeat by one or more phases.  The
            # relative-margin check also prevents a tiny absolute advantage
            # from becoming a permanent phase decision.
            required_margin = max(self.config.phase_margin, 0.10 * total_evidence)
            if (
                locked_phase is None
                and beat_index >= (2 * self.config.meter) - 1
                and margin >= required_margin
            ):
                locked_phase = best_phase
            if locked_phase is not None and beat_index % self.config.meter == locked_phase and score >= self.config.downbeat_threshold:
                downbeats.append(float(beat_time))

        # If no phase became confident, choose the best observed phase at the
        # end.  This is a controlled fallback for short/quiet excerpts.
        if locked_phase is None:
            locked_phase = int(np.argmax(phase_scores))
            downbeats = [
                float(beat_time)
                for beat_index, (beat_time, score) in enumerate(zip(beat_times, evidence))
                if beat_index % self.config.meter == locked_phase
                and score >= self.config.downbeat_threshold
            ]
        return np.asarray(downbeats, dtype=np.float64)


def decode_track(
    rows: pd.DataFrame,
    beat_scores: np.ndarray,
    downbeat_scores: np.ndarray,
    config: DecoderConfig,
) -> tuple[np.ndarray, np.ndarray]:
    times = rows["t"].to_numpy(dtype=np.float64)
    beat_times, _beat_strengths = CausalBeatGridDecoder(config).decode(times, beat_scores)
    downbeat_times = DownbeatPhaseDecoder(config).decode(
        beat_times, times, downbeat_scores
    )
    return beat_times, downbeat_times


def mean_event_summary(summaries: Sequence[Any]) -> EventSummary:
    if not summaries:
        return EventSummary(0.0, 0.0, 0.0, math.nan, 0.0, 0, 0, 0)

    def finite_mean(values: Iterable[float]) -> float:
        finite_values = [float(value) for value in values if math.isfinite(float(value))]
        return float(np.mean(finite_values)) if finite_values else math.nan

    return EventSummary(
        precision=finite_mean(item.precision for item in summaries),
        recall=finite_mean(item.recall for item in summaries),
        f1=finite_mean(item.f1 for item in summaries),
        matched_mae_ms=finite_mean(item.matched_mae_ms for item in summaries),
        timing_cost_seconds=finite_mean(item.timing_cost_seconds for item in summaries),
        predicted_events=int(sum(item.predicted_events for item in summaries)),
        actual_events=int(sum(item.actual_events for item in summaries)),
        matched_events=int(sum(item.matched_events for item in summaries)),
    )


def score_decoded(
    rows: pd.DataFrame,
    decoded_by_track: dict[str, tuple[np.ndarray, np.ndarray]],
    timeline_by_track: dict[str, TimelineRecord],
    args: argparse.Namespace,
) -> dict[str, Any]:
    beat_summaries: list[Any] = []
    downbeat_summaries: list[Any] = []
    per_track: dict[str, Any] = {}
    for track_id, track_rows in rows.groupby("track_id", sort=False):
        frame_times = track_rows["t"].to_numpy(dtype=np.float64)
        timeline = timeline_by_track[str(track_id)]
        predicted_beats, predicted_downbeats = decoded_by_track[str(track_id)]
        actual_beats = timeline.beat_times[
            (timeline.beat_times >= frame_times[0]) & (timeline.beat_times <= frame_times[-1])
        ]
        actual_downbeats = timeline.downbeat_times[
            (timeline.downbeat_times >= frame_times[0]) & (timeline.downbeat_times <= frame_times[-1])
        ]
        beat = match_event_times(
            predicted_beats,
            actual_beats,
            tolerance=args.match_tolerance,
            miss_penalty=args.miss_penalty,
        )
        downbeat = match_event_times(
            predicted_downbeats,
            actual_downbeats,
            tolerance=args.match_tolerance,
            miss_penalty=args.miss_penalty,
        )
        beat_summaries.append(beat)
        downbeat_summaries.append(downbeat)
        per_track[str(track_id)] = {"beat": asdict(beat), "downbeat": asdict(downbeat)}
    beat = mean_event_summary(beat_summaries)
    downbeat = mean_event_summary(downbeat_summaries)
    tolerance_metrics: dict[str, Any] = {}
    for tolerance in args.evaluation_tolerances:
        tolerance_beats: list[Any] = []
        tolerance_downbeats: list[Any] = []
        for track_id, track_rows in rows.groupby("track_id", sort=False):
            frame_times = track_rows["t"].to_numpy(dtype=np.float64)
            timeline = timeline_by_track[str(track_id)]
            predicted_beats, predicted_downbeats = decoded_by_track[str(track_id)]
            actual_beats = timeline.beat_times[
                (timeline.beat_times >= frame_times[0]) & (timeline.beat_times <= frame_times[-1])
            ]
            actual_downbeats = timeline.downbeat_times[
                (timeline.downbeat_times >= frame_times[0]) & (timeline.downbeat_times <= frame_times[-1])
            ]
            tolerance_beats.append(
                match_event_times(predicted_beats, actual_beats, tolerance=float(tolerance), miss_penalty=args.miss_penalty)
            )
            tolerance_downbeats.append(
                match_event_times(predicted_downbeats, actual_downbeats, tolerance=float(tolerance), miss_penalty=args.miss_penalty)
            )
        beat_at_tolerance = mean_event_summary(tolerance_beats)
        downbeat_at_tolerance = mean_event_summary(tolerance_downbeats)
        tolerance_metrics[f"{float(tolerance):.3f}"] = {
            "beat_f1": beat_at_tolerance.f1,
            "downbeat_f1": downbeat_at_tolerance.f1,
            "beat_matched_mae_ms": beat_at_tolerance.matched_mae_ms,
            "downbeat_matched_mae_ms": downbeat_at_tolerance.matched_mae_ms,
        }
    return {
        "event_cost_seconds": 0.5 * (beat.timing_cost_seconds + downbeat.timing_cost_seconds),
        "beat": asdict(beat),
        "downbeat": asdict(downbeat),
        "tolerance_metrics": tolerance_metrics,
        "per_track": per_track,
    }


def decode_frame_predictions(
    rows: pd.DataFrame,
    beat_scores: np.ndarray,
    downbeat_scores: np.ndarray,
    decoder_config: DecoderConfig,
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    decoded: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for track_id, track_rows in rows.groupby("track_id", sort=False):
        positions = track_rows.index.to_numpy()
        local_rows = track_rows.reset_index(drop=True)
        decoded[str(track_id)] = decode_track(
            local_rows,
            beat_scores[positions],
            downbeat_scores[positions],
            decoder_config,
        )
    return decoded


def grid_values(args: argparse.Namespace) -> dict[str, list[Any]]:
    if args.quick:
        return {
            "window_seconds": [2.0],
            "alpha": [0.001, 0.01],
            "learning_rate": [0.01],
            "batch_size": [1024],
            "epochs": [25],
            "target_sigma": [0.04],
            "beat_weight": [3.0],
            "downbeat_weight": [8.0],
        }
    return {
        "window_seconds": list(args.window_seconds),
        "alpha": list(args.alphas),
        "learning_rate": list(args.learning_rates),
        "batch_size": list(args.batch_sizes),
        "epochs": list(args.epochs),
        "target_sigma": list(args.target_sigmas),
        "beat_weight": list(args.beat_weights),
        "downbeat_weight": list(args.downbeat_weights),
    }


def group_folds(rows: pd.DataFrame, args: argparse.Namespace) -> Iterable[tuple[np.ndarray, np.ndarray]]:
    groups = rows["track_id"].to_numpy()
    n_splits = min(args.cv_folds, np.unique(groups).size)
    if n_splits < 2:
        raise ValueError("At least two development tracks are required for grouped CV")
    dummy = np.zeros(len(rows), dtype=np.float32)
    return GroupKFold(n_splits=n_splits).split(dummy, groups=groups)


def make_analysis_config(args: argparse.Namespace, hop_seconds: float) -> AnalysisConfig:
    return AnalysisConfig(
        sample_rate=44_100,
        frame_size=2_048,
        hop_seconds=hop_seconds,
        n_mels=32,
        n_mfcc=13,
        label_tolerance=0.040,
        target_sigma=0.040,
        match_tolerance=args.match_tolerance,
        miss_penalty=args.miss_penalty,
        beat_peak_threshold=DEFAULT_DECODER.beat_threshold,
        downbeat_peak_threshold=DEFAULT_DECODER.downbeat_threshold,
        min_beat_interval=DEFAULT_DECODER.min_beat_interval,
        min_downbeat_interval=0.70,
        window_seconds=(0.5,),
        random_state=args.random_state,
    )


def tune_ridge(
    frame_df: pd.DataFrame,
    base_features: Sequence[str],
    timelines: Sequence[TimelineRecord],
    development_tracks: set[str],
    args: argparse.Namespace,
) -> tuple[RidgeConfig, pd.DataFrame]:
    timeline_by_track = {timeline.track_id: timeline for timeline in timelines}
    development_source = frame_df[frame_df["track_id"].isin(development_tracks)].reset_index(drop=True)
    hop_seconds = float(np.median(np.diff(frame_df["t"].to_numpy(dtype=np.float64))))
    analysis_config = make_analysis_config(args, hop_seconds)
    all_results: list[dict[str, Any]] = []
    best_config: RidgeConfig | None = None
    best_score = math.inf
    values = grid_values(args)

    windows = values["window_seconds"]
    window_cache: dict[float, tuple[pd.DataFrame, list[str]]] = {}
    for window_seconds in windows:
        started_window = time.perf_counter()
        print(console_safe(f"building Ridge window features: {window_seconds:.3f}s"), flush=True)
        window_df, window_feature_names = build_causal_window_dataset(
            frame_df, base_features, window_seconds, hop_seconds
        )
        window_cache[float(window_seconds)] = (
            window_df[window_df["track_id"].isin(development_tracks)].reset_index(drop=True),
            window_feature_names,
        )
        print(
            console_safe(
                f"    {len(window_df):,} rows ready in {time.perf_counter() - started_window:.1f}s"
            ),
            flush=True,
        )

    import itertools

    for combination in itertools.product(*values.values()):
        params = dict(zip(values.keys(), combination))
        ridge_config = RidgeConfig(**params)
        fold_scores: list[dict[str, Any]] = []
        started = time.perf_counter()
        window_rows, window_feature_names = window_cache[ridge_config.window_seconds]
        X = window_rows.loc[:, window_feature_names].to_numpy(dtype=np.float32)
        beat_target, downbeat_target = target_arrays(window_rows, ridge_config.target_sigma)
        for train_indices, validation_indices in group_folds(window_rows, args):
            models = fit_ridge_pair(
                X[train_indices],
                beat_target[train_indices],
                downbeat_target[train_indices],
                ridge_config,
                args,
            )
            beat_scores, downbeat_scores = predict_pair(models, X[validation_indices])
            validation_rows = window_rows.iloc[validation_indices].reset_index(drop=True)
            decoded = decode_frame_predictions(
                validation_rows,
                beat_scores,
                downbeat_scores,
                DEFAULT_DECODER,
            )
            score = score_decoded(validation_rows, decoded, timeline_by_track, args)
            fold_scores.append(score)

        mean_cost = float(np.mean([score["event_cost_seconds"] for score in fold_scores]))
        mean_beat_f1 = float(np.mean([score["beat"]["f1"] for score in fold_scores]))
        mean_downbeat_f1 = float(np.mean([score["downbeat"]["f1"] for score in fold_scores]))
        result = {
            **asdict(ridge_config),
            "validation_event_cost": mean_cost,
            "validation_beat_f1": mean_beat_f1,
            "validation_downbeat_f1": mean_downbeat_f1,
            "elapsed_seconds": time.perf_counter() - started,
        }
        all_results.append(result)
        if mean_cost < best_score:
            best_score = mean_cost
            best_config = ridge_config
        print(
            console_safe(
                f"  Ridge {asdict(ridge_config)} cost={mean_cost:.4f}s "
                f"beat_f1={mean_beat_f1:.3f} downbeat_f1={mean_downbeat_f1:.3f} "
                f"({result['elapsed_seconds']:.1f}s)"
            ),
            flush=True,
        )

    if best_config is None:
        raise RuntimeError("Ridge grid produced no candidates")
    results_df = pd.DataFrame(all_results).sort_values("validation_event_cost")
    return best_config, results_df


def fit_oof_predictions(
    frame_df: pd.DataFrame,
    base_features: Sequence[str],
    timelines: Sequence[TimelineRecord],
    development_tracks: set[str],
    ridge_config: RidgeConfig,
    args: argparse.Namespace,
) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    hop_seconds = float(np.median(np.diff(frame_df["t"].to_numpy(dtype=np.float64))))
    window_df, window_features = build_causal_window_dataset(
        frame_df, base_features, ridge_config.window_seconds, hop_seconds
    )
    rows = window_df[window_df["track_id"].isin(development_tracks)].reset_index(drop=True)
    X = rows.loc[:, window_features].to_numpy(dtype=np.float32)
    beat_target, downbeat_target = target_arrays(rows, ridge_config.target_sigma)
    beat_oof = np.zeros(len(rows), dtype=np.float64)
    downbeat_oof = np.zeros(len(rows), dtype=np.float64)
    for train_indices, validation_indices in group_folds(rows, args):
        models = fit_ridge_pair(
            X[train_indices],
            beat_target[train_indices],
            downbeat_target[train_indices],
            ridge_config,
            args,
        )
        beat_oof[validation_indices], downbeat_oof[validation_indices] = predict_pair(
            models, X[validation_indices]
        )
    return rows, beat_oof, downbeat_oof


def decoder_grid(args: argparse.Namespace) -> list[DecoderConfig]:
    if args.quick:
        beat_thresholds = (0.30,)
        downbeat_thresholds = (0.25,)
        min_intervals = (0.24,)
        tolerances = (0.28,)
        phase_margins = (0.05,)
    else:
        beat_thresholds = args.decoder_beat_thresholds
        downbeat_thresholds = args.decoder_downbeat_thresholds
        min_intervals = args.decoder_min_intervals
        tolerances = args.decoder_period_tolerances
        phase_margins = args.decoder_phase_margins
    import itertools

    return [
        DecoderConfig(
            beat_threshold=float(beat_threshold),
            downbeat_threshold=float(downbeat_threshold),
            min_beat_interval=float(min_interval),
            min_bpm=DEFAULT_DECODER.min_bpm,
            max_bpm=DEFAULT_DECODER.max_bpm,
            period_tolerance=float(period_tolerance),
            history_beats=DEFAULT_DECODER.history_beats,
            meter=DEFAULT_DECODER.meter,
            phase_margin=float(phase_margin),
            phase_penalty=DEFAULT_DECODER.phase_penalty,
        )
        for beat_threshold, downbeat_threshold, min_interval, period_tolerance, phase_margin in itertools.product(
            beat_thresholds,
            downbeat_thresholds,
            min_intervals,
            tolerances,
            phase_margins,
        )
    ]


def tune_decoder(
    rows: pd.DataFrame,
    beat_oof: np.ndarray,
    downbeat_oof: np.ndarray,
    timelines: Sequence[TimelineRecord],
    args: argparse.Namespace,
) -> tuple[DecoderConfig, pd.DataFrame]:
    timeline_by_track = {timeline.track_id: timeline for timeline in timelines}
    results: list[dict[str, Any]] = []
    best: DecoderConfig | None = None
    best_cost = math.inf
    for config in decoder_grid(args):
        decoded = decode_frame_predictions(rows, beat_oof, downbeat_oof, config)
        metrics = score_decoded(rows, decoded, timeline_by_track, args)
        row = {
            **asdict(config),
            "validation_event_cost": metrics["event_cost_seconds"],
            "validation_beat_f1": metrics["beat"]["f1"],
            "validation_downbeat_f1": metrics["downbeat"]["f1"],
        }
        results.append(row)
        if row["validation_event_cost"] < best_cost:
            best_cost = row["validation_event_cost"]
            best = config
    if best is None:
        raise RuntimeError("Decoder grid produced no candidates")
    results_df = pd.DataFrame(results).sort_values("validation_event_cost")
    return best, results_df


def fit_final_models(
    frame_df: pd.DataFrame,
    base_features: Sequence[str],
    development_tracks: set[str],
    test_tracks: set[str],
    ridge_config: RidgeConfig,
    args: argparse.Namespace,
) -> tuple[pd.DataFrame, np.ndarray, np.ndarray, tuple[Pipeline, Pipeline]]:
    hop_seconds = float(np.median(np.diff(frame_df["t"].to_numpy(dtype=np.float64))))
    window_df, window_features = build_causal_window_dataset(
        frame_df, base_features, ridge_config.window_seconds, hop_seconds
    )
    development_rows = window_df[window_df["track_id"].isin(development_tracks)].reset_index(drop=True)
    test_rows = window_df[window_df["track_id"].isin(test_tracks)].reset_index(drop=True)
    X_development = development_rows.loc[:, window_features].to_numpy(dtype=np.float32)
    X_test = test_rows.loc[:, window_features].to_numpy(dtype=np.float32)
    beat_target, downbeat_target = target_arrays(development_rows, ridge_config.target_sigma)
    models = fit_ridge_pair(
        X_development,
        beat_target,
        downbeat_target,
        ridge_config,
        args,
    )
    beat_test, downbeat_test = predict_pair(models, X_test)
    return test_rows, beat_test, downbeat_test, models


def save_model_weights(models: tuple[Pipeline, Pipeline], path: Path) -> None:
    beat_scale = models[0].named_steps["scale"]
    beat_ridge = models[0].named_steps["model"]
    downbeat_scale = models[1].named_steps["scale"]
    downbeat_ridge = models[1].named_steps["model"]
    np.savez_compressed(
        path,
        beat_mean=beat_scale.mean_,
        beat_scale=beat_scale.scale_,
        beat_coef=beat_ridge.coef_,
        beat_intercept=beat_ridge.intercept_,
        downbeat_mean=downbeat_scale.mean_,
        downbeat_scale=downbeat_scale.scale_,
        downbeat_coef=downbeat_ridge.coef_,
        downbeat_intercept=downbeat_ridge.intercept_,
    )


def save_training_curves(models: tuple[Pipeline, Pipeline], path: Path) -> None:
    beat_history = models[0].named_steps["model"].loss_history_
    downbeat_history = models[1].named_steps["model"].loss_history_
    figure, axis = plt.subplots(figsize=(9, 5))
    axis.plot(beat_history, label="beat Ridge")
    axis.plot(downbeat_history, label="downbeat Ridge")
    axis.set_xlabel("Epoch")
    axis.set_ylabel("Weighted activation loss + L2")
    axis.set_title("Final Ridge mini-batch training curves")
    axis.legend()
    axis.grid(alpha=0.2)
    figure.tight_layout()
    figure.savefig(path, dpi=170)
    plt.close(figure)


def save_decoder_heatmap(results: pd.DataFrame, path: Path) -> None:
    pivot = results.pivot_table(
        index="beat_threshold",
        columns="downbeat_threshold",
        values="validation_event_cost",
        aggfunc="min",
    )
    figure, axis = plt.subplots(figsize=(8, 6))
    sns.heatmap(pivot, annot=True, fmt=".3f", cmap="viridis_r", ax=axis)
    axis.set_title("Decoder event-time cost by activation thresholds")
    axis.set_xlabel("Downbeat threshold")
    axis.set_ylabel("Beat threshold")
    figure.tight_layout()
    figure.savefig(path, dpi=170)
    plt.close(figure)


def save_prediction_plot(
    rows: pd.DataFrame,
    beat_scores: np.ndarray,
    downbeat_scores: np.ndarray,
    decoded: dict[str, tuple[np.ndarray, np.ndarray]],
    timelines: Sequence[TimelineRecord],
    args: argparse.Namespace,
    path: Path,
) -> None:
    test_tracks = list(rows["track_id"].drop_duplicates())
    selected = next(
        (track for track in test_tracks if args.sample_track.casefold() in track.casefold()),
        test_tracks[0],
    )
    mask = rows["track_id"].eq(selected).to_numpy()
    track_rows = rows.iloc[np.flatnonzero(mask)]
    track_positions = np.flatnonzero(mask)
    start = float(args.sample_start)
    end = start + float(args.sample_duration)
    local_mask = (track_rows["t"].to_numpy() >= start) & (track_rows["t"].to_numpy() <= end)
    if not np.any(local_mask):
        start = float(track_rows["t"].min())
        end = min(float(track_rows["t"].max()), start + float(args.sample_duration))
        local_mask = (track_rows["t"].to_numpy() >= start) & (track_rows["t"].to_numpy() <= end)
    positions = track_positions[local_mask]
    times = rows.iloc[positions]["t"].to_numpy(dtype=np.float64)
    timeline = next(item for item in timelines if item.track_id == selected)
    beat_events, downbeat_events = decoded[selected]
    figure, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    axes[0].plot(times, beat_scores[positions], label="beat activation")
    axes[1].plot(times, downbeat_scores[positions], label="downbeat activation", color="#d62728")
    axes[0].plot(beat_events, np.full(beat_events.shape, 0.9), "|", markersize=14, label="decoded beats")
    axes[1].plot(downbeat_events, np.full(downbeat_events.shape, 0.9), "|", markersize=14, label="decoded downbeats")
    actual_beats = timeline.beat_times[(timeline.beat_times >= start) & (timeline.beat_times <= end)]
    actual_downbeats = timeline.downbeat_times[(timeline.downbeat_times >= start) & (timeline.downbeat_times <= end)]
    for value in actual_beats:
        axes[0].axvline(value, color="#333333", alpha=0.25)
    for value in actual_downbeats:
        axes[1].axvline(value, color="#333333", alpha=0.35)
    axes[0].set_title(plot_safe(f"Tuned beat decoder - {selected}"))
    axes[1].set_title("Tuned downbeat phase decoder")
    for axis in axes:
        axis.set_ylim(-0.05, 1.05)
        axis.grid(alpha=0.2)
        axis.legend()
    axes[1].set_xlabel("Time (seconds)")
    figure.tight_layout()
    figure.savefig(path, dpi=170)
    plt.close(figure)


def write_decoded_events(
    rows: pd.DataFrame,
    beat_scores: np.ndarray,
    downbeat_scores: np.ndarray,
    decoder_config: DecoderConfig,
    timelines: Sequence[TimelineRecord],
    args: argparse.Namespace,
    output_path: Path,
) -> dict[str, Any]:
    decoded = decode_frame_predictions(rows, beat_scores, downbeat_scores, decoder_config)
    timeline_by_track = {timeline.track_id: timeline for timeline in timelines}
    metrics = score_decoded(rows, decoded, timeline_by_track, args)
    data = {
        "decoder_config": asdict(decoder_config),
        "metrics": metrics,
        "tracks": {
            track_id: {
                "beat_times": beats.tolist(),
                "downbeat_times": downbeats.tolist(),
            }
            for track_id, (beats, downbeats) in decoded.items()
        },
    }
    output_path.write_text(json.dumps(json_ready(data), indent=2, ensure_ascii=False), encoding="utf-8")
    return data


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.features = resolve_path(args.features, REPO_ROOT)
    args.manifest = resolve_path(args.manifest, REPO_ROOT)
    args.splits = resolve_path(args.splits, REPO_ROOT)
    args.output_dir = resolve_path(args.output_dir, REPO_ROOT)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    sns.set_theme(style="whitegrid", context="notebook")

    frame_df, timelines, development_tracks, test_tracks = load_dataset(args)
    base_features = feature_columns(frame_df)
    timeline_by_track = {timeline.track_id: timeline for timeline in timelines}
    print(
        console_safe(
            f"loaded {len(frame_df):,} frames, {len(base_features)} base features, "
            f"{len(development_tracks)} development tracks, {len(test_tracks)} test tracks"
        ),
        flush=True,
    )

    best_ridge_path = args.output_dir / "best_ridge_config.json"
    if args.stage in {"ridge", "all"}:
        best_ridge, ridge_results = tune_ridge(
            frame_df,
            base_features,
            timelines,
            development_tracks,
            args,
        )
        ridge_results.to_csv(args.output_dir / "ridge_trial_results.csv", index=False)
        best_ridge_path.write_text(
            json.dumps(json_ready(asdict(best_ridge)), indent=2), encoding="utf-8"
        )
    else:
        if not best_ridge_path.is_file():
            raise FileNotFoundError(
                f"{best_ridge_path} is required for --stage decoder. Run --stage ridge first."
            )
        best_ridge = RidgeConfig(**json.loads(best_ridge_path.read_text(encoding="utf-8")))

    if args.stage == "ridge":
        print(console_safe(f"best Ridge configuration: {asdict(best_ridge)}"), flush=True)
        return 0

    oof_rows, beat_oof, downbeat_oof = fit_oof_predictions(
        frame_df,
        base_features,
        timelines,
        development_tracks,
        best_ridge,
        args,
    )
    oof_frame = oof_rows.loc[:, ["track_id", "t", "is_beat", "is_downbeat"]].copy()
    oof_frame["beat_activation"] = beat_oof
    oof_frame["downbeat_activation"] = downbeat_oof
    oof_frame.to_csv(args.output_dir / "ridge_oof_predictions.csv.gz", index=False, compression="gzip")

    best_decoder_path = args.output_dir / "best_decoder_config.json"
    if args.stage in {"decoder", "all"}:
        best_decoder, decoder_results = tune_decoder(
            oof_rows,
            beat_oof,
            downbeat_oof,
            timelines,
            args,
        )
        decoder_results.to_csv(args.output_dir / "decoder_trial_results.csv", index=False)
        best_decoder_path.write_text(
            json.dumps(json_ready(asdict(best_decoder)), indent=2), encoding="utf-8"
        )
        save_decoder_heatmap(decoder_results, args.output_dir / "decoder_parameter_heatmap.png")
    else:
        if not best_decoder_path.is_file():
            raise FileNotFoundError(
                f"{best_decoder_path} is required for the final stage. Run --stage all first."
            )
        best_decoder = DecoderConfig(**json.loads(best_decoder_path.read_text(encoding="utf-8")))

    test_rows, beat_test, downbeat_test, models = fit_final_models(
        frame_df,
        base_features,
        development_tracks,
        test_tracks,
        best_ridge,
        args,
    )
    decoded_data = write_decoded_events(
        test_rows,
        beat_test,
        downbeat_test,
        best_decoder,
        timelines,
        args,
        args.output_dir / "best_decoded_events.json",
    )
    save_model_weights(models, args.output_dir / "best_model_weights.npz")
    save_training_curves(models, args.output_dir / "ridge_training_curves.png")

    predictions = test_rows.loc[:, ["track_id", "t", "is_beat", "is_downbeat"]].copy()
    predictions["beat_activation"] = beat_test
    predictions["downbeat_activation"] = downbeat_test
    predictions.to_csv(
        args.output_dir / "best_test_frame_predictions.csv.gz",
        index=False,
        compression="gzip",
    )
    decoder_by_track = {
        track_id: (
            np.asarray(values["beat_times"], dtype=np.float64),
            np.asarray(values["downbeat_times"], dtype=np.float64),
        )
        for track_id, values in decoded_data["tracks"].items()
    }
    save_prediction_plot(
        test_rows,
        beat_test,
        downbeat_test,
        decoder_by_track,
        timelines,
        args,
        args.output_dir / "beat_downbeat_prediction_plot.png",
    )
    summary = {
        "ridge_config": asdict(best_ridge),
        "decoder_config": asdict(best_decoder),
        "development_tracks": sorted(development_tracks),
        "test_tracks": sorted(test_tracks),
        "base_feature_count": len(base_features),
        "frame_count": len(frame_df),
        "test_metrics": decoded_data["metrics"],
    }
    (args.output_dir / "tuning_summary.json").write_text(
        json.dumps(json_ready(summary), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(console_safe(f"best Ridge: {asdict(best_ridge)}"), flush=True)
    print(console_safe(f"best decoder: {asdict(best_decoder)}"), flush=True)
    print(console_safe(f"held-out metrics: {decoded_data['metrics']}"), flush=True)
    print(console_safe(f"artifacts written to {args.output_dir}"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
