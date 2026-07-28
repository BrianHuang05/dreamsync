"""Visual band-level diagnostics for beat/downbeat transfer.

The script evaluates each cached frequency-oriented feature independently,
then evaluates causal positive-delta, peak-excess, and autocorrelation variants.
Feature selection is performed on development tracks only. The selected feature
is finally passed through a simple peak picker on held-out tracks.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import warnings
from pathlib import Path
from typing import Any, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from scipy.signal import find_peaks
from sklearn.metrics import average_precision_score, roc_auc_score

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from beat_model_analysis import TimelineRecord, console_safe, match_event_times  # noqa: E402
from tune_beat_ridge import feature_columns, load_dataset, resolve_path  # noqa: E402

warnings.filterwarnings("ignore", category=pd.errors.PerformanceWarning)


def parse_float_list(value: str) -> tuple[float, ...]:
    result = tuple(float(item.strip()) for item in value.split(",") if item.strip())
    if not result:
        raise argparse.ArgumentTypeError("expected a comma-separated list")
    return result


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", type=Path, default=Path("out/beat-ml-analysis/frame_features.csv.gz"))
    parser.add_argument("--manifest", type=Path, default=Path("out/beat-ml-analysis/verified_dataset_manifest.csv"))
    parser.add_argument("--splits", type=Path, default=Path("out/beat-ml-analysis/track_split.csv"))
    parser.add_argument("--output-dir", type=Path, default=Path("out/beat-band-diagnostics"))
    parser.add_argument("--periods-seconds", type=parse_float_list, default=(0.40, 0.50, 0.60, 0.70, 0.80, 1.00))
    parser.add_argument("--derived-windows", type=parse_float_list, default=(0.50, 2.0, 5.0, 10.0))
    parser.add_argument("--match-tolerance", type=float, default=0.070)
    parser.add_argument("--max-track-seconds", type=float, default=None)
    parser.add_argument("--plot-seconds", type=float, default=30.0)
    return parser.parse_args(argv)


def safe_auc(labels: np.ndarray, values: np.ndarray) -> tuple[float, float, float]:
    labels = np.asarray(labels, dtype=np.int8)
    values = np.asarray(values, dtype=np.float64)
    if np.unique(labels).size < 2:
        return math.nan, math.nan, 1.0
    auc = float(roc_auc_score(labels, values))
    ap = float(average_precision_score(labels, values))
    orientation = -1.0 if auc < 0.5 else 1.0
    return auc, ap, orientation


def signal_features(base_features: Sequence[str]) -> list[str]:
    selected = []
    for name in base_features:
        folded = name.casefold()
        if (
            "ratio" in folded
            or folded.startswith("log_mel_")
            or folded.startswith("chroma_")
            or name in {"rms", "spectral_flux", "dominant_frequency_hz", "dominant_magnitude_ratio"}
        ):
            selected.append(name)
    return selected or list(base_features)


def add_derived_signals(frame_df: pd.DataFrame, sources: Sequence[str], periods: Sequence[float], windows: Sequence[float], hop: float) -> tuple[pd.DataFrame, list[str]]:
    result = frame_df.copy()
    names: list[str] = []
    period_lags = sorted({max(1, int(round(period / hop))) for period in periods})
    window_frames = {window: max(2, int(round(window / hop))) for window in windows}
    for source in sources:
        grouped = result.groupby("track_id", sort=False)[source]
        positive_name = f"{source}__positive_delta"
        result[positive_name] = grouped.diff().clip(lower=0.0).fillna(0.0).astype(np.float32)
        names.append(positive_name)
        for window, frames in window_frames.items():
            rolling_max = result.groupby("track_id", sort=False)[source].transform(
                lambda series, frames=frames: series.rolling(frames, min_periods=1).max()
            )
            rolling_mean = result.groupby("track_id", sort=False)[source].transform(
                lambda series, frames=frames: series.rolling(frames, min_periods=1).mean()
            )
            name = f"{source}__peak_excess_{window:g}s"
            result[name] = (rolling_max - rolling_mean).fillna(0.0).astype(np.float32)
            names.append(name)
        onset = result[positive_name]
        for lag in period_lags:
            name = f"{source}__autocorr_{lag}"
            result[name] = result.groupby("track_id", sort=False)[positive_name].transform(
                lambda series, lag=lag: (series * series.shift(lag)).rolling(lag + 1, min_periods=lag + 1).mean()
            ).fillna(0.0).astype(np.float32)
            names.append(name)
    return result, names


def evaluate_feature(rows: pd.DataFrame, feature: str, split_tracks: set[str]) -> dict[str, Any]:
    subset = rows[rows["track_id"].isin(split_tracks)]
    values = subset[feature].to_numpy(dtype=np.float64)
    beat_auc, beat_ap, beat_orientation = safe_auc(subset["is_beat"].to_numpy(), values)
    down_auc, down_ap, down_orientation = safe_auc(subset["is_downbeat"].to_numpy(), values)
    return {
        "feature": feature,
        "beat_auc": beat_auc,
        "beat_auc_oriented": max(beat_auc, 1.0 - beat_auc) if math.isfinite(beat_auc) else math.nan,
        "beat_average_precision": beat_ap,
        "downbeat_auc": down_auc,
        "downbeat_auc_oriented": max(down_auc, 1.0 - down_auc) if math.isfinite(down_auc) else math.nan,
        "downbeat_average_precision": down_ap,
        "beat_orientation": beat_orientation,
        "downbeat_orientation": down_orientation,
    }


def simple_peaks(times: np.ndarray, values: np.ndarray, threshold_quantile: float, minimum_interval: float) -> np.ndarray:
    centered = np.asarray(values, dtype=np.float64)
    centered = (centered - np.nanmedian(centered)) / (np.nanstd(centered) + 1e-9)
    threshold = float(np.nanquantile(centered, threshold_quantile))
    hop = float(np.median(np.diff(times))) if len(times) > 1 else 0.02
    indices, _ = find_peaks(centered, height=threshold, distance=max(1, int(round(minimum_interval / hop))))
    return times[indices]


def peak_metrics(rows: pd.DataFrame, feature: str, orientation: float, timelines: dict[str, TimelineRecord], tolerance: float) -> dict[str, Any]:
    beat_items: list[Any] = []
    downbeat_items: list[Any] = []
    for track_id, track_rows in rows.groupby("track_id", sort=False):
        times = track_rows["t"].to_numpy(dtype=np.float64)
        values = track_rows[feature].to_numpy(dtype=np.float64) * orientation
        beats = simple_peaks(times, values, 0.85, 0.24)
        downbeats = simple_peaks(times, values, 0.90, 0.70)
        timeline = timelines[str(track_id)]
        actual_beats = timeline.beat_times[(timeline.beat_times >= times[0]) & (timeline.beat_times <= times[-1])]
        actual_downbeats = timeline.downbeat_times[(timeline.downbeat_times >= times[0]) & (timeline.downbeat_times <= times[-1])]
        beat_items.append(match_event_times(beats, actual_beats, tolerance=tolerance, miss_penalty=0.5))
        downbeat_items.append(match_event_times(downbeats, actual_downbeats, tolerance=tolerance, miss_penalty=0.5))

    def mean(items: Sequence[Any], field: str) -> float:
        values = [float(getattr(item, field)) for item in items if math.isfinite(float(getattr(item, field)))]
        return float(np.mean(values)) if values else math.nan

    return {
        "beat_precision": mean(beat_items, "precision"),
        "beat_recall": mean(beat_items, "recall"),
        "beat_f1": mean(beat_items, "f1"),
        "downbeat_precision": mean(downbeat_items, "precision"),
        "downbeat_recall": mean(downbeat_items, "recall"),
        "downbeat_f1": mean(downbeat_items, "f1"),
    }


def plot_top_signals(rows: pd.DataFrame, results: pd.DataFrame, split_tracks: set[str], timelines: dict[str, TimelineRecord], output_dir: Path, duration: float) -> None:
    top = results.sort_values("beat_auc_oriented_development", ascending=False).head(6)
    heatmap = results.set_index("feature")[["beat_auc_oriented_heldout", "downbeat_auc_oriented_heldout"]].sort_values("beat_auc_oriented_heldout", ascending=False).head(35)
    heatmap.columns = ["beat_auc", "downbeat_auc"]
    figure, axis = plt.subplots(figsize=(10, 12))
    sns.heatmap(heatmap, vmin=0.0, vmax=1.0, cmap="viridis", annot=False, ax=axis)
    axis.set_title("Band and derived-signal held-out AUC")
    figure.tight_layout()
    figure.savefig(output_dir / "band_auc_heatmap.png", dpi=170)
    plt.close(figure)

    bar_data = top.melt(id_vars=["feature"], value_vars=["beat_auc_oriented_development", "downbeat_auc_oriented_development"], var_name="event", value_name="auc")
    figure, axis = plt.subplots(figsize=(12, 6))
    sns.barplot(data=bar_data, x="auc", y="feature", hue="event", ax=axis)
    axis.axvline(0.5, color="0.4", linestyle="--")
    axis.set_xlim(0.0, 1.0)
    axis.set_title("Top signals selected by development AUC")
    figure.tight_layout()
    figure.savefig(output_dir / "top_band_auc.png", dpi=170)
    plt.close(figure)

    if not split_tracks:
        return
    track_id = sorted(split_tracks)[0]
    track_rows = rows[rows["track_id"] == track_id].reset_index(drop=True)
    times = track_rows["t"].to_numpy(dtype=np.float64)
    mask = times <= times[0] + duration
    figure, axes = plt.subplots(len(top), 1, figsize=(14, 2.3 * len(top)), sharex=True)
    axes = np.atleast_1d(axes)
    timeline = timelines[track_id]
    for axis, (_, record) in zip(axes, top.iterrows()):
        feature = record["feature"]
        values = track_rows[feature].to_numpy(dtype=np.float64)
        values = (values - np.nanmedian(values)) / (np.nanstd(values) + 1e-9)
        axis.plot(times[mask], values[mask], label=f"{feature} (dev beat AUC={record['beat_auc_oriented_development']:.3f})")
        for event in timeline.beat_times[(timeline.beat_times >= times[0]) & (timeline.beat_times <= times[0] + duration)]:
            axis.axvline(event, color="#333333", alpha=0.22)
        axis.legend(loc="upper right")
        axis.grid(alpha=0.2)
    axes[-1].set_xlabel("Time (seconds)")
    figure.suptitle(f"Top signals versus verified beats: {ascii_label(track_id)}")
    figure.tight_layout()
    figure.savefig(output_dir / "top_signal_waveforms.png", dpi=160)
    plt.close(figure)


def ascii_label(value: object) -> str:
    return str(value).encode("ascii", errors="ignore").decode("ascii").strip() or "track"


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.features = resolve_path(args.features, REPO_ROOT)
    args.manifest = resolve_path(args.manifest, REPO_ROOT)
    args.splits = resolve_path(args.splits, REPO_ROOT)
    args.output_dir = resolve_path(args.output_dir, REPO_ROOT)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    frame_df, timelines, development_tracks, test_tracks = load_dataset(args)
    base_features = feature_columns(frame_df)
    sources = signal_features(base_features)
    hop = float(np.median(np.diff(frame_df["t"].to_numpy(dtype=np.float64))))
    enriched, derived = add_derived_signals(frame_df, sources, args.periods_seconds, args.derived_windows, hop)
    candidate_features = sources + derived
    timeline_by_track = {timeline.track_id: timeline for timeline in timelines}
    print(f"evaluating {len(candidate_features)} signals ({len(sources)} base, {len(derived)} derived)", flush=True)

    dev_results = pd.DataFrame([evaluate_feature(enriched, feature, development_tracks) for feature in candidate_features])
    test_results = pd.DataFrame([evaluate_feature(enriched, feature, test_tracks) for feature in candidate_features])
    dev_results.to_csv(args.output_dir / "development_signal_auc.csv", index=False)
    test_results.to_csv(args.output_dir / "heldout_signal_auc.csv", index=False)

    merged = dev_results.merge(test_results, on="feature", suffixes=("_development", "_heldout"))
    merged["beat_generalization_gap"] = merged["beat_auc_oriented_development"] - merged["beat_auc_oriented_heldout"]
    merged["downbeat_generalization_gap"] = merged["downbeat_auc_oriented_development"] - merged["downbeat_auc_oriented_heldout"]
    plot_top_signals(enriched[enriched["track_id"].isin(test_tracks)], merged, test_tracks, timeline_by_track, args.output_dir, args.plot_seconds)

    top_beat = merged.sort_values("beat_auc_oriented_development", ascending=False).iloc[0]
    top_downbeat = merged.sort_values("downbeat_auc_oriented_development", ascending=False).iloc[0]
    peak_results = {
        "beat_selected_feature": top_beat["feature"],
        "beat_selected_development_auc": float(top_beat["beat_auc_oriented_development"]),
        "beat_heldout_peak_metrics": peak_metrics(enriched[enriched["track_id"].isin(test_tracks)], top_beat["feature"], float(top_beat["beat_orientation_development"]), timeline_by_track, args.match_tolerance),
        "downbeat_selected_feature": top_downbeat["feature"],
        "downbeat_selected_development_auc": float(top_downbeat["downbeat_auc_oriented_development"]),
        "downbeat_heldout_peak_metrics": peak_metrics(enriched[enriched["track_id"].isin(test_tracks)], top_downbeat["feature"], float(top_downbeat["downbeat_orientation_development"]), timeline_by_track, args.match_tolerance),
    }
    report = {
        "base_signal_count": len(sources),
        "derived_signal_count": len(derived),
        "development_tracks": sorted(development_tracks),
        "test_tracks": sorted(test_tracks),
        "top_development_signals": merged.sort_values("beat_auc_oriented_development", ascending=False).head(20).to_dict(orient="records"),
        "peak_picker_diagnostic": peak_results,
        "notes": [
            "AUC orientation is chosen on development tracks; held-out metrics are not used for feature selection.",
            "Peak-picker metrics are diagnostic and use a fixed 85th/90th percentile threshold.",
        ],
    }
    (args.output_dir / "band_diagnostic_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=True), encoding="utf-8")
    print(console_safe(f"top development beat signal: {top_beat['feature']} AUC={top_beat['beat_auc_oriented_development']:.3f} held-out={top_beat['beat_auc_oriented_heldout']:.3f}"), flush=True)
    print(console_safe(f"top development downbeat signal: {top_downbeat['feature']} AUC={top_downbeat['downbeat_auc_oriented_development']:.3f} held-out={top_downbeat['downbeat_auc_oriented_heldout']:.3f}"), flush=True)
    print(f"artifacts written to {args.output_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
