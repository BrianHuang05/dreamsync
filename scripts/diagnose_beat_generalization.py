"""Cross-track generalization diagnostics for DreamSync beat detection.

This script targets the failure mode where development AUC is above chance but
held-out-song AUC is random. It performs:

* per-track z-score normalization;
* causal onset strength and spectral-flux features;
* lightweight tempogram/autocorrelation features at plausible beat periods;
* chroma summaries when chroma columns exist in the cache;
* leave-one-development-track-out Ridge validation;
* raw AUC/average-precision per track;
* simple onset/flux/periodicity baselines;
* a final held-out evaluation on the designated test tracks.

It deliberately does not tune a large model grid. The objective is to decide
whether the representation generalizes before adding nonlinear models.
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
import seaborn as sns
from scipy.stats import pearsonr
from sklearn.metrics import average_precision_score, roc_auc_score

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from beat_model_analysis import build_causal_window_dataset  # noqa: E402
from tune_beat_ridge import (  # noqa: E402
    RidgeConfig,
    feature_columns,
    fit_ridge_pair,
    load_dataset,
    parse_float_list,
    predict_pair,
    resolve_path,
    target_arrays,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", type=Path, default=Path("out/beat-ml-analysis/frame_features.csv.gz"))
    parser.add_argument("--manifest", type=Path, default=Path("out/beat-ml-analysis/verified_dataset_manifest.csv"))
    parser.add_argument("--splits", type=Path, default=Path("out/beat-ml-analysis/track_split.csv"))
    parser.add_argument("--model-dir", type=Path, default=Path("out/beat-ridge-tuning-full"))
    parser.add_argument("--output-dir", type=Path, default=Path("out/beat-generalization-diagnostics"))
    parser.add_argument("--window-seconds", type=float, default=2.0)
    parser.add_argument("--random-state", type=int, default=73)
    parser.add_argument("--early-stopping-patience", type=int, default=5)
    parser.add_argument("--early-stopping-tolerance", type=float, default=1e-6)
    parser.add_argument("--periods-seconds", type=parse_float_list, default=(0.40, 0.50, 0.60, 0.70))
    parser.add_argument("--max-track-seconds", type=float, default=None)
    return parser.parse_args(argv)


def ascii_label(value: object) -> str:
    text = str(value).encode("ascii", errors="ignore").decode("ascii").strip()
    return text or "track"


def load_config(model_dir: Path, window_seconds: float) -> RidgeConfig:
    path = model_dir / "best_ridge_config.json"
    if path.is_file():
        values = json.loads(path.read_text(encoding="utf-8"))
        return RidgeConfig(window_seconds=window_seconds, **{key: value for key, value in values.items() if key != "window_seconds"})
    return RidgeConfig(window_seconds=window_seconds, alpha=0.01, learning_rate=0.01, batch_size=1024, epochs=35, target_sigma=0.04, beat_weight=3.0, downbeat_weight=12.0)


def safe_auc(labels: np.ndarray, scores: np.ndarray) -> tuple[float, float]:
    labels = np.asarray(labels, dtype=np.int8)
    scores = np.asarray(scores, dtype=np.float64)
    if np.unique(labels).size < 2:
        return math.nan, math.nan
    return float(roc_auc_score(labels, scores)), float(average_precision_score(labels, scores))


def add_generalization_features(frame_df: pd.DataFrame, base_features: Sequence[str], periods: Sequence[float], hop_seconds: float) -> tuple[pd.DataFrame, list[str], dict[str, Any]]:
    """Add per-track normalization and causal onset/periodicity descriptors."""
    result = frame_df.copy()
    new_features: list[str] = []
    numeric_base = [name for name in base_features if pd.api.types.is_numeric_dtype(result[name])]
    grouped = result.groupby("track_id", sort=False)

    # Per-track normalization removes song-level loudness/timbre shortcuts.
    for name in numeric_base:
        mean = grouped[name].transform("mean")
        std = grouped[name].transform("std").replace(0.0, 1.0).fillna(1.0)
        normalized_name = f"norm__{name}"
        result[normalized_name] = ((result[name] - mean) / std).astype(np.float32)
        new_features.append(normalized_name)

    spectral_names = [
        name for name in numeric_base
        if any(token in name.casefold() for token in ("mel", "spect", "mfcc", "chroma", "rms", "energy", "loud"))
    ]
    source_names = spectral_names or numeric_base
    source_values = result[source_names].to_numpy(dtype=np.float64)
    source_values = np.nan_to_num(source_values, nan=0.0, posinf=0.0, neginf=0.0)
    frame_delta = np.diff(source_values, axis=0, prepend=source_values[:1])
    flux = np.sqrt(np.mean(np.square(np.maximum(frame_delta, 0.0)), axis=1))
    result["feature__spectral_flux"] = flux.astype(np.float32)
    result["feature__onset_strength"] = grouped["feature__spectral_flux"].transform(lambda series: series.rolling(3, min_periods=1).mean()).astype(np.float32)
    new_features.extend(["feature__spectral_flux", "feature__onset_strength"])

    # Causal autocorrelation at plausible beat periods acts as a compact
    # tempogram/local-periodicity representation.
    onset = result["feature__onset_strength"]
    for period in periods:
        lag = max(1, int(round(float(period) / hop_seconds)))
        name = f"feature__tempogram_{period:.3f}s"
        result[name] = grouped["feature__onset_strength"].transform(
            lambda series, lag=lag: (series * series.shift(lag)).rolling(lag + 1, min_periods=lag + 1).mean()
        ).fillna(0.0).astype(np.float32)
        new_features.append(name)
    periodicity_names = [f"feature__tempogram_{period:.3f}s" for period in periods]
    result["feature__local_periodicity"] = result[periodicity_names].max(axis=1).astype(np.float32)
    new_features.append("feature__local_periodicity")

    chroma_names = [name for name in numeric_base if "chroma" in name.casefold()]
    if chroma_names:
        result["feature__chroma_mean"] = result[chroma_names].mean(axis=1).astype(np.float32)
        result["feature__chroma_std"] = result[chroma_names].std(axis=1).fillna(0.0).astype(np.float32)
        new_features.extend(["feature__chroma_mean", "feature__chroma_std"])

    metadata = {
        "base_feature_count": len(base_features),
        "normalized_feature_count": len(numeric_base),
        "spectral_source_count": len(source_names),
        "chroma_source_count": len(chroma_names),
        "generated_feature_count": len(new_features),
        "periods_seconds": list(periods),
    }
    return result, list(base_features) + new_features, metadata


def track_report(rows: pd.DataFrame, beat_scores: np.ndarray, downbeat_scores: np.ndarray) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for track_id, track_rows in rows.groupby("track_id", sort=False):
        positions = track_rows.index.to_numpy()
        beat_auc, beat_ap = safe_auc(track_rows["is_beat"].to_numpy(), beat_scores[positions])
        downbeat_auc, downbeat_ap = safe_auc(track_rows["is_downbeat"].to_numpy(), downbeat_scores[positions])
        report[str(track_id)] = {
            "frames": int(len(positions)),
            "beat_auc": beat_auc,
            "beat_average_precision": beat_ap,
            "downbeat_auc": downbeat_auc,
            "downbeat_average_precision": downbeat_ap,
            "beat_score_std": float(np.std(beat_scores[positions])),
            "downbeat_score_std": float(np.std(downbeat_scores[positions])),
        }
    return report


def baseline_report(rows: pd.DataFrame, score_columns: Sequence[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for column in score_columns:
        beat_auc, beat_ap = safe_auc(rows["is_beat"].to_numpy(), rows[column].to_numpy())
        downbeat_auc, downbeat_ap = safe_auc(rows["is_downbeat"].to_numpy(), rows[column].to_numpy())
        result[column] = {
            "beat_auc": beat_auc,
            "beat_average_precision": beat_ap,
            "downbeat_auc": downbeat_auc,
            "downbeat_average_precision": downbeat_ap,
        }
    return result


def fit_predict(train_rows: pd.DataFrame, test_rows: pd.DataFrame, features: Sequence[str], config: RidgeConfig, args: argparse.Namespace) -> tuple[np.ndarray, np.ndarray]:
    X_train = train_rows.loc[:, features].to_numpy(dtype=np.float32)
    X_test = test_rows.loc[:, features].to_numpy(dtype=np.float32)
    beat_target, downbeat_target = target_arrays(train_rows, config.target_sigma)
    models = fit_ridge_pair(X_train, beat_target, downbeat_target, config, args)
    return predict_pair(models, X_test)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.features = resolve_path(args.features, REPO_ROOT)
    args.manifest = resolve_path(args.manifest, REPO_ROOT)
    args.splits = resolve_path(args.splits, REPO_ROOT)
    args.model_dir = resolve_path(args.model_dir, REPO_ROOT)
    args.output_dir = resolve_path(args.output_dir, REPO_ROOT)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    frame_df, _timelines, development_tracks, test_tracks = load_dataset(args)
    base_features = feature_columns(frame_df)
    hop_seconds = float(np.median(np.diff(frame_df["t"].to_numpy(dtype=np.float64))))
    enriched_df, enriched_features, feature_metadata = add_generalization_features(
        frame_df, base_features, args.periods_seconds, hop_seconds
    )
    window_df, window_features = build_causal_window_dataset(
        enriched_df, enriched_features, args.window_seconds, hop_seconds
    )
    config = load_config(args.model_dir, args.window_seconds)
    development_rows = window_df[window_df["track_id"].isin(development_tracks)].reset_index(drop=True)
    test_rows = window_df[window_df["track_id"].isin(test_tracks)].reset_index(drop=True)

    print(f"loaded {len(frame_df):,} frames; enriched features={len(enriched_features)}; window features={len(window_features)}", flush=True)
    print(f"development tracks={len(development_tracks)}; held-out tracks={len(test_tracks)}", flush=True)
    print(f"feature metadata: {feature_metadata}", flush=True)

    # Baselines are computed on the frame-level enriched data, before windowing.
    baseline_columns = ["feature__spectral_flux", "feature__onset_strength", "feature__local_periodicity"]
    baseline_rows = enriched_df[enriched_df["track_id"].isin(test_tracks)].reset_index(drop=True)
    baseline_metrics = baseline_report(baseline_rows, baseline_columns)

    # Leave one development song out at a time.
    looto_rows: list[dict[str, Any]] = []
    for heldout_track in sorted(development_tracks):
        train_rows = development_rows[development_rows["track_id"] != heldout_track].reset_index(drop=True)
        validation_rows = development_rows[development_rows["track_id"] == heldout_track].reset_index(drop=True)
        beat_scores, downbeat_scores = fit_predict(train_rows, validation_rows, window_features, config, args)
        metrics = track_report(validation_rows, beat_scores, downbeat_scores)[heldout_track]
        looto_rows.append({"track_id": heldout_track, "split": "leave_one_development_track_out", **metrics})
        print(
            f"LOOTO {ascii_label(heldout_track)} beat_auc={metrics['beat_auc']:.4f} "
            f"downbeat_auc={metrics['downbeat_auc']:.4f}", flush=True
        )

    # Final fit on every development song, evaluated on the designated test songs.
    beat_test, downbeat_test = fit_predict(development_rows, test_rows, window_features, config, args)
    final_track_metrics = track_report(test_rows, beat_test, downbeat_test)
    for track_id, metrics in final_track_metrics.items():
        print(
            f"held-out {ascii_label(track_id)} beat_auc={metrics['beat_auc']:.4f} "
            f"downbeat_auc={metrics['downbeat_auc']:.4f}", flush=True
        )

    heldout_predictions = test_rows[["track_id", "t", "is_beat", "is_downbeat"]].copy()
    heldout_predictions["beat_activation"] = beat_test
    heldout_predictions["downbeat_activation"] = downbeat_test
    heldout_predictions.to_csv(args.output_dir / "heldout_enriched_predictions.csv.gz", index=False, compression="gzip")

    all_track_rows = looto_rows + [
        {"track_id": track_id, "split": "held_out_test", **metrics}
        for track_id, metrics in final_track_metrics.items()
    ]
    track_metrics_df = pd.DataFrame(all_track_rows)
    track_metrics_df.to_csv(args.output_dir / "per_track_auc.csv", index=False)
    plot_df = track_metrics_df.melt(
        id_vars=["track_id", "split"],
        value_vars=["beat_auc", "downbeat_auc"],
        var_name="event",
        value_name="auc",
    )
    plot_df["track_label"] = plot_df["track_id"].map(ascii_label)
    figure, axis = plt.subplots(figsize=(14, 6))
    sns.barplot(data=plot_df, x="track_label", y="auc", hue="event", ax=axis)
    axis.axhline(0.5, color="0.4", linestyle="--", label="chance")
    axis.set_ylim(0.0, 1.0)
    axis.set_title("Per-track raw activation AUC")
    axis.set_xlabel("Track")
    axis.set_ylabel("ROC-AUC")
    axis.tick_params(axis="x", rotation=70)
    axis.legend()
    figure.tight_layout()
    figure.savefig(args.output_dir / "per_track_auc.png", dpi=170)
    plt.close(figure)

    report = {
        "config": asdict(config),
        "feature_metadata": feature_metadata,
        "baseline_metrics_heldout": baseline_metrics,
        "leave_one_development_track_out": looto_rows,
        "heldout_track_metrics": final_track_metrics,
        "notes": [
            "AUC and average precision are computed on raw activations before event decoding.",
            "Feature normalization is performed within each track before causal windows are built.",
            "LOOTO estimates cross-song generalization; the final held-out result is the designated test split.",
        ],
    }
    (args.output_dir / "generalization_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=True), encoding="utf-8"
    )
    print(f"artifacts written to {args.output_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
