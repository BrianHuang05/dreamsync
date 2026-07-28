"""Diagnose Ridge beat/downbeat activations separately from temporal decoding.

This script is deliberately diagnostic rather than a training grid. It answers
four questions on the held-out tracks:

1. Do the raw Ridge activations contain beat/downbeat signal? (PR-AUC and
   Pearson/Spearman correlation.)
2. Do those activations peak near verified events? (activation plots.)
3. How sensitive are decoded events to timing tolerance? (F1 curves.)
4. Is the causal decoder the bottleneck? (simple peak-picker sweep.)

The best Ridge configuration is read from ``--model-dir`` when available;
otherwise the command-line/default configuration is used. The model is fit
only on development tracks and all diagnostic metrics are computed on held-out
tracks. The best peak-picker result on held-out data is labelled optimistic
and must not be used as a production score.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.signal import find_peaks
from scipy.stats import pearsonr, spearmanr
from sklearn.metrics import average_precision_score, roc_auc_score, roc_curve
import seaborn as sns

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from beat_model_analysis import (  # noqa: E402
    TimelineRecord,
    console_safe,
    load_verified_timelines,
    match_event_times,
)
from tune_beat_ridge import (  # noqa: E402
    DEFAULT_DECODER,
    DecoderConfig,
    RidgeConfig,
    decode_track,
    feature_columns,
    fit_ridge_pair,
    load_dataset,
    predict_pair,
    resolve_path,
    target_arrays,
)


def parse_float_list(value: str) -> tuple[float, ...]:
    values = tuple(float(item.strip()) for item in value.split(",") if item.strip())
    if not values:
        raise argparse.ArgumentTypeError("expected a comma-separated list")
    return values


def ascii_label(value: object) -> str:
    text = str(value).encode("ascii", errors="ignore").decode("ascii").strip()
    return text or "track"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", type=Path, default=Path("out/beat-ml-analysis/frame_features.csv.gz"))
    parser.add_argument("--manifest", type=Path, default=Path("out/beat-ml-analysis/verified_dataset_manifest.csv"))
    parser.add_argument("--splits", type=Path, default=Path("out/beat-ml-analysis/track_split.csv"))
    parser.add_argument("--model-dir", type=Path, default=Path("out/beat-ridge-tuning-full"))
    parser.add_argument("--output-dir", type=Path, default=Path("out/beat-ridge-diagnostics"))
    parser.add_argument("--random-state", type=int, default=73)
    parser.add_argument("--early-stopping-patience", type=int, default=5)
    parser.add_argument("--early-stopping-tolerance", type=float, default=1e-6)
    parser.add_argument("--match-tolerances", type=parse_float_list, default=(0.020, 0.040, 0.070, 0.100))
    parser.add_argument("--peak-thresholds", type=parse_float_list, default=(0.15, 0.25, 0.35, 0.45, 0.55, 0.65))
    parser.add_argument("--peak-min-interval", type=float, default=0.24)
    parser.add_argument("--downbeat-peak-min-interval", type=float, default=0.70)
    parser.add_argument("--max-track-seconds", type=float, default=None)
    parser.add_argument("--sample-duration", type=float, default=30.0)
    return parser.parse_args(argv)


def load_ridge_config(model_dir: Path) -> RidgeConfig:
    path = model_dir / "best_ridge_config.json"
    if path.is_file():
        return RidgeConfig(**json.loads(path.read_text(encoding="utf-8")))
    return RidgeConfig(
        window_seconds=2.0,
        alpha=0.01,
        learning_rate=0.01,
        batch_size=1024,
        epochs=35,
        target_sigma=0.04,
        beat_weight=3.0,
        downbeat_weight=12.0,
    )


def correlation_metrics(y_true: np.ndarray, scores: np.ndarray) -> dict[str, float]:
    y_true = np.asarray(y_true, dtype=np.float64)
    scores = np.asarray(scores, dtype=np.float64)
    result: dict[str, float] = {}
    for name, function in (("pearson", pearsonr), ("spearman", spearmanr)):
        try:
            value = float(function(y_true, scores).statistic)
        except (ValueError, FloatingPointError):
            value = math.nan
        result[name] = value
    for name, function in (("average_precision", average_precision_score), ("roc_auc", roc_auc_score)):
        try:
            value = float(function(y_true, scores))
        except ValueError:
            value = math.nan
        result[name] = value
    return result


def raw_activation_report(rows: pd.DataFrame, beat_scores: np.ndarray, downbeat_scores: np.ndarray) -> dict[str, Any]:
    report = {
        "beat": correlation_metrics(rows["is_beat"].to_numpy(), beat_scores),
        "downbeat": correlation_metrics(rows["is_downbeat"].to_numpy(), downbeat_scores),
    }
    per_track: dict[str, Any] = {}
    for track_id, track_rows in rows.groupby("track_id", sort=False):
        positions = track_rows.index.to_numpy()
        per_track[str(track_id)] = {
            "beat": correlation_metrics(track_rows["is_beat"].to_numpy(), beat_scores[positions]),
            "downbeat": correlation_metrics(track_rows["is_downbeat"].to_numpy(), downbeat_scores[positions]),
            "frames": int(len(positions)),
        }
    report["per_track"] = per_track
    return report


def plot_roc_curves(
    rows: pd.DataFrame,
    beat_scores: np.ndarray,
    downbeat_scores: np.ndarray,
    output_dir: Path,
) -> dict[str, Any]:
    """Plot pooled held-out ROC curves and export every threshold point."""
    curves: list[dict[str, Any]] = []
    figure, axis = plt.subplots(figsize=(9, 7))
    for name, truth_column, scores in (
        ("Beat", "is_beat", beat_scores),
        ("Downbeat", "is_downbeat", downbeat_scores),
    ):
        truth = rows[truth_column].to_numpy(dtype=np.int8)
        false_positive_rate, true_positive_rate, thresholds = roc_curve(truth, scores)
        auc_value = float(roc_auc_score(truth, scores))
        frame = pd.DataFrame(
            {
                "event": name.lower(),
                "threshold": thresholds,
                "false_positive_rate": false_positive_rate,
                "true_positive_rate": true_positive_rate,
                "auc": auc_value,
            }
        )
        curves.extend(frame.to_dict(orient="records"))
        sns.lineplot(
            data=frame,
            x="false_positive_rate",
            y="true_positive_rate",
            label=f"{name} (AUC={auc_value:.3f})",
            ax=axis,
        )
    sns.lineplot(x=[0.0, 1.0], y=[0.0, 1.0], linestyle="--", color="0.5", label="chance", ax=axis)
    axis.set_title("Held-out activation ROC by threshold")
    axis.set_xlabel("False-positive rate")
    axis.set_ylabel("True-positive rate")
    axis.set_xlim(0.0, 1.0)
    axis.set_ylim(0.0, 1.0)
    axis.grid(alpha=0.2)
    figure.tight_layout()
    figure.savefig(output_dir / "activation_roc_curves.png", dpi=170)
    plt.close(figure)
    pd.DataFrame(curves).to_csv(output_dir / "activation_roc_points.csv", index=False)
    return {
        "beat_auc": float(roc_auc_score(rows["is_beat"].to_numpy(), beat_scores)),
        "downbeat_auc": float(roc_auc_score(rows["is_downbeat"].to_numpy(), downbeat_scores)),
        "point_count": len(curves),
    }


def simple_peak_decode(times: np.ndarray, scores: np.ndarray, threshold: float, min_interval: float) -> np.ndarray:
    times = np.asarray(times, dtype=np.float64)
    scores = np.asarray(scores, dtype=np.float64)
    if times.size == 0:
        return np.empty(0, dtype=np.float64)
    hop = float(np.median(np.diff(times))) if times.size > 1 else 0.02
    distance = max(1, int(round(min_interval / max(hop, 1e-6))))
    indices, properties = find_peaks(scores, height=float(threshold), distance=distance)
    if indices.size == 0:
        return np.empty(0, dtype=np.float64)
    # find_peaks already performs local-max suppression; retain its times.
    return times[indices]


def actual_events(timeline: TimelineRecord, times: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    beats = timeline.beat_times[(timeline.beat_times >= times[0]) & (timeline.beat_times <= times[-1])]
    downbeats = timeline.downbeat_times[(timeline.downbeat_times >= times[0]) & (timeline.downbeat_times <= times[-1])]
    return beats, downbeats


def decoded_metrics(
    rows: pd.DataFrame,
    decoded: dict[str, tuple[np.ndarray, np.ndarray]],
    timelines: dict[str, TimelineRecord],
    tolerances: Sequence[float],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for tolerance in tolerances:
        beat_items: list[Any] = []
        downbeat_items: list[Any] = []
        for track_id, track_rows in rows.groupby("track_id", sort=False):
            times = track_rows["t"].to_numpy(dtype=np.float64)
            actual_beats, actual_downbeats = actual_events(timelines[str(track_id)], times)
            predicted_beats, predicted_downbeats = decoded[str(track_id)]
            beat_items.append(match_event_times(predicted_beats, actual_beats, tolerance=tolerance, miss_penalty=0.5))
            downbeat_items.append(match_event_times(predicted_downbeats, actual_downbeats, tolerance=tolerance, miss_penalty=0.5))

        def average(items: Sequence[Any], field: str) -> float:
            values = [float(getattr(item, field)) for item in items if math.isfinite(float(getattr(item, field)))]
            return float(np.mean(values)) if values else math.nan

        result[f"{float(tolerance):.3f}"] = {
            "beat_precision": average(beat_items, "precision"),
            "beat_recall": average(beat_items, "recall"),
            "beat_f1": average(beat_items, "f1"),
            "downbeat_precision": average(downbeat_items, "precision"),
            "downbeat_recall": average(downbeat_items, "recall"),
            "downbeat_f1": average(downbeat_items, "f1"),
            "beat_matched_mae_ms": average(beat_items, "matched_mae_ms"),
            "downbeat_matched_mae_ms": average(downbeat_items, "matched_mae_ms"),
        }
    return result


def plot_activations(
    rows: pd.DataFrame,
    beat_scores: np.ndarray,
    downbeat_scores: np.ndarray,
    timelines: dict[str, TimelineRecord],
    output_dir: Path,
    duration: float,
) -> None:
    for track_id, track_rows in rows.groupby("track_id", sort=False):
        positions = track_rows.index.to_numpy()
        times = track_rows["t"].to_numpy(dtype=np.float64)
        start = float(times[0])
        mask = times <= start + duration
        timeline = timelines[str(track_id)]
        figure, axes = plt.subplots(2, 1, figsize=(14, 7), sharex=True)
        axes[0].plot(times[mask], beat_scores[positions][mask], label="Ridge beat activation")
        axes[1].plot(times[mask], downbeat_scores[positions][mask], color="#d62728", label="Ridge downbeat activation")
        for value in timeline.beat_times[(timeline.beat_times >= start) & (timeline.beat_times <= start + duration)]:
            axes[0].axvline(value, color="#333333", alpha=0.25)
        for value in timeline.downbeat_times[(timeline.downbeat_times >= start) & (timeline.downbeat_times <= start + duration)]:
            axes[1].axvline(value, color="#333333", alpha=0.35)
        axes[0].set_title(f"Raw beat activation - {ascii_label(track_id)}")
        axes[1].set_title("Raw downbeat activation; vertical lines are verified events")
        for axis in axes:
            axis.set_ylim(-0.05, 1.05)
            axis.grid(alpha=0.2)
            axis.legend()
        axes[1].set_xlabel("Time (seconds)")
        figure.tight_layout()
        figure.savefig(output_dir / f"activation_{ascii_label(track_id)[:80]}.png", dpi=160)
        plt.close(figure)


def pipeline_sanity_checks(
    frame_df: pd.DataFrame,
    window_df: pd.DataFrame,
    development_rows: pd.DataFrame,
    test_rows: pd.DataFrame,
    development_scores: tuple[np.ndarray, np.ndarray],
    test_scores: tuple[np.ndarray, np.ndarray],
) -> dict[str, Any]:
    """Check label alignment, row ordering, generalization, and score variance."""
    checks: dict[str, Any] = {}

    # 1. Verified event columns must agree with the distance-derived labels.
    distance_checks: dict[str, Any] = {}
    for event_name, distance_column, label_column in (
        ("beat", "nearest_beat_distance", "is_beat"),
        ("downbeat", "nearest_downbeat_distance", "is_downbeat"),
    ):
        expected = window_df[distance_column].to_numpy(dtype=np.float64) <= 0.040
        observed = window_df[label_column].to_numpy(dtype=bool)
        agreement = float(np.mean(expected == observed))
        distance_checks[event_name] = {
            "agreement": agreement,
            "mismatches": int(np.count_nonzero(expected != observed)),
            "status": "PASS" if agreement >= 0.999 else "WARN",
        }
    checks["label_distance_alignment"] = distance_checks

    # 2. Window features must preserve one row per original track/time sample.
    original_keys = frame_df[["track_id", "t"]].reset_index(drop=True)
    window_keys = window_df[["track_id", "t"]].reset_index(drop=True)
    same_length = len(original_keys) == len(window_keys)
    same_order = bool(same_length and original_keys.equals(window_keys))
    monotonic = bool(
        all(group["t"].is_monotonic_increasing for _, group in window_df.groupby("track_id", sort=False))
    )
    checks["feature_label_ordering"] = {
        "same_row_count": same_length,
        "same_track_time_order": same_order,
        "track_times_monotonic": monotonic,
        "duplicate_track_time_rows": int(window_df.duplicated(["track_id", "t"]).sum()),
        "status": "PASS" if same_order and monotonic else "WARN",
    }

    # 3. Training AUC should exceed chance if the feature mapping is learnable.
    development_report = raw_activation_report(
        development_rows, development_scores[0], development_scores[1]
    )
    test_report = raw_activation_report(test_rows, test_scores[0], test_scores[1])
    train_beat_auc = development_report["beat"]["roc_auc"]
    train_downbeat_auc = development_report["downbeat"]["roc_auc"]
    checks["development_vs_heldout_auc"] = {
        "development_beat_auc": train_beat_auc,
        "development_downbeat_auc": train_downbeat_auc,
        "heldout_beat_auc": test_report["beat"]["roc_auc"],
        "heldout_downbeat_auc": test_report["downbeat"]["roc_auc"],
        "status": "PASS" if train_beat_auc > 0.55 or train_downbeat_auc > 0.55 else "WARN",
    }

    # 4. Constant or nearly constant activations cannot support thresholding.
    variance: dict[str, Any] = {}
    for name, dev_values, test_values in (
        ("beat", development_scores[0], test_scores[0]),
        ("downbeat", development_scores[1], test_scores[1]),
    ):
        dev_std = float(np.std(dev_values))
        test_std = float(np.std(test_values))
        variance[name] = {
            "development_std": dev_std,
            "heldout_std": test_std,
            "development_range": [float(np.min(dev_values)), float(np.max(dev_values))],
            "heldout_range": [float(np.min(test_values)), float(np.max(test_values))],
            "status": "PASS" if max(dev_std, test_std) >= 1e-4 else "WARN",
        }
    checks["activation_variance"] = variance
    return checks


def print_sanity_checks(checks: dict[str, Any]) -> None:
    print("\nPipeline sanity checks", flush=True)
    alignment = checks["label_distance_alignment"]
    for event_name, values in alignment.items():
        print(
            console_safe(
                f"  [{values['status']}] {event_name} distance/label agreement="
                f"{values['agreement']:.5f} mismatches={values['mismatches']}"
            ),
            flush=True,
        )
    ordering = checks["feature_label_ordering"]
    print(
        console_safe(
            f"  [{ordering['status']}] feature/label ordering rows={ordering['same_row_count']} "
            f"track_time_order={ordering['same_track_time_order']} "
            f"monotonic={ordering['track_times_monotonic']} "
            f"duplicates={ordering['duplicate_track_time_rows']}"
        ),
        flush=True,
    )
    auc = checks["development_vs_heldout_auc"]
    print(
        console_safe(
            f"  [{auc['status']}] AUC dev beat/downbeat={auc['development_beat_auc']:.4f}/"
            f"{auc['development_downbeat_auc']:.4f}; held-out="
            f"{auc['heldout_beat_auc']:.4f}/{auc['heldout_downbeat_auc']:.4f}"
        ),
        flush=True,
    )
    for event_name, values in checks["activation_variance"].items():
        print(
            console_safe(
                f"  [{values['status']}] {event_name} activation std dev/held-out="
                f"{values['development_std']:.6g}/{values['heldout_std']:.6g} "
                f"range={values['heldout_range']}"
            ),
            flush=True,
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.features = resolve_path(args.features, REPO_ROOT)
    args.manifest = resolve_path(args.manifest, REPO_ROOT)
    args.splits = resolve_path(args.splits, REPO_ROOT)
    args.model_dir = resolve_path(args.model_dir, REPO_ROOT)
    args.output_dir = resolve_path(args.output_dir, REPO_ROOT)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    frame_df, timelines, development_tracks, test_tracks = load_dataset(args)
    base_features = feature_columns(frame_df)
    ridge_config = load_ridge_config(args.model_dir)
    timeline_by_track = {timeline.track_id: timeline for timeline in timelines}
    hop_seconds = float(np.median(np.diff(frame_df["t"].to_numpy(dtype=np.float64))))

    from beat_model_analysis import build_causal_window_dataset

    window_df, window_features = build_causal_window_dataset(
        frame_df, base_features, ridge_config.window_seconds, hop_seconds
    )
    development_rows = window_df[window_df["track_id"].isin(development_tracks)].reset_index(drop=True)
    test_rows = window_df[window_df["track_id"].isin(test_tracks)].reset_index(drop=True)
    X_development = development_rows.loc[:, window_features].to_numpy(dtype=np.float32)
    X_test = test_rows.loc[:, window_features].to_numpy(dtype=np.float32)
    beat_target, downbeat_target = target_arrays(development_rows, ridge_config.target_sigma)
    models = fit_ridge_pair(X_development, beat_target, downbeat_target, ridge_config, args)
    development_scores = predict_pair(models, X_development)
    beat_scores, downbeat_scores = predict_pair(models, X_test)

    print(console_safe(f"diagnosing {len(test_rows):,} held-out frames with Ridge {asdict(ridge_config)}"), flush=True)
    raw_report = raw_activation_report(test_rows, beat_scores, downbeat_scores)
    sanity_checks = pipeline_sanity_checks(
        frame_df,
        window_df,
        development_rows,
        test_rows,
        development_scores,
        (beat_scores, downbeat_scores),
    )
    print_sanity_checks(sanity_checks)
    roc_report = plot_roc_curves(test_rows, beat_scores, downbeat_scores, args.output_dir)

    causal_decoded: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    peak_decoded: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for track_id, track_rows in test_rows.groupby("track_id", sort=False):
        positions = track_rows.index.to_numpy()
        times = track_rows["t"].to_numpy(dtype=np.float64)
        causal_decoded[str(track_id)] = decode_track(
            track_rows.reset_index(drop=True), beat_scores[positions], downbeat_scores[positions], DEFAULT_DECODER
        )
        peak_decoded[str(track_id)] = (
            simple_peak_decode(times, beat_scores[positions], 0.30, args.peak_min_interval),
            simple_peak_decode(times, downbeat_scores[positions], 0.25, args.downbeat_peak_min_interval),
        )

    causal_report = decoded_metrics(test_rows, causal_decoded, timeline_by_track, args.match_tolerances)
    simple_peak_default_report = decoded_metrics(
        test_rows, peak_decoded, timeline_by_track, args.match_tolerances
    )
    peak_sweep: list[dict[str, Any]] = []
    for threshold in args.peak_thresholds:
        decoded: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for track_id, track_rows in test_rows.groupby("track_id", sort=False):
            positions = track_rows.index.to_numpy()
            times = track_rows["t"].to_numpy(dtype=np.float64)
            decoded[str(track_id)] = (
                simple_peak_decode(times, beat_scores[positions], threshold, args.peak_min_interval),
                simple_peak_decode(times, downbeat_scores[positions], threshold, args.downbeat_peak_min_interval),
            )
        metrics = decoded_metrics(test_rows, decoded, timeline_by_track, args.match_tolerances)
        peak_sweep.append({"threshold": float(threshold), "metrics": metrics})

    plot_activations(test_rows, beat_scores, downbeat_scores, timeline_by_track, args.output_dir, args.sample_duration)
    predictions = test_rows[["track_id", "t", "is_beat", "is_downbeat"]].copy()
    predictions["beat_activation"] = beat_scores
    predictions["downbeat_activation"] = downbeat_scores
    predictions.to_csv(args.output_dir / "heldout_activations.csv.gz", index=False, compression="gzip")
    report = {
        "ridge_config": asdict(ridge_config),
        "development_tracks": sorted(development_tracks),
        "test_tracks": sorted(test_tracks),
        "frame_count": int(len(test_rows)),
        "raw_activation_metrics": raw_report,
        "pipeline_sanity_checks": sanity_checks,
        "roc_metrics": roc_report,
        "causal_decoder_metrics": causal_report,
        "simple_peak_default_metrics": simple_peak_default_report,
        "simple_peak_picker_sweep": peak_sweep,
        "notes": [
            "Peak-picker scores are an optimistic diagnostic sweep, not a production estimate, because thresholds are inspected on held-out data.",
            "Matched MAE is conditional on an event match; it does not measure missed events.",
        ],
    }
    (args.output_dir / "diagnostic_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=True), encoding="utf-8"
    )
    print(console_safe(f"raw activations: {raw_report}"), flush=True)
    print(console_safe(f"causal tolerance metrics: {causal_report}"), flush=True)
    print(console_safe(f"artifacts written to {args.output_dir}"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
