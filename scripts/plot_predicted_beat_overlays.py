"""Render tiled verified overlays for raw candidates or decoded beat grids.

In grid mode, verified beats are black, matched decoder beats green, unmatched
decoder beats red, suppressed subdivision candidates purple, snapped beats blue
markers, and low-confidence grid beats gray dashed lines.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from beat_model_analysis import TimelineRecord, console_safe  # noqa: E402
from diagnose_beat_bands import add_derived_signals, parse_float_list  # noqa: E402
from tune_beat_ridge import load_dataset, resolve_path  # noqa: E402


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", type=Path, default=Path("out/beat-ml-analysis/frame_features.csv.gz"))
    parser.add_argument("--manifest", type=Path, default=Path("out/beat-ml-analysis/verified_dataset_manifest.csv"))
    parser.add_argument("--splits", type=Path, default=Path("out/beat-ml-analysis/track_split.csv"))
    parser.add_argument("--rankings", type=Path, default=Path("out/beat-band-diagnostics/development_signal_auc.csv"))
    parser.add_argument("--candidates", type=Path, default=Path("out/beat-band-diagnostics/large-scale-validation/event-candidate-analysis/event_candidates.csv"))
    parser.add_argument("--decoded-beats", type=Path, default=Path("out/beat-band-diagnostics/grid-decoder/decoded_beats.csv"))
    parser.add_argument("--output-dir", type=Path, default=Path("out/beat-band-diagnostics/large-scale-validation/predicted-beat-overlays"))
    parser.add_argument("--mode", choices=("raw-candidates", "grid-decoder"), default="raw-candidates")
    parser.add_argument("--track-scope", choices=("development", "heldout", "all"), default="development")
    parser.add_argument("--track-substring", default="", help="Render only tracks whose identifier contains this text.")
    parser.add_argument("--allow-existing", action="store_true", help="Resume an explicitly selected overlay directory.")
    parser.add_argument("--tile-seconds", type=float, default=30.0)
    parser.add_argument("--match-tolerance", type=float, default=0.25)
    parser.add_argument("--top-signals", type=int, default=6)
    parser.add_argument("--periods-seconds", type=parse_float_list, default=(0.40, 0.50, 0.60, 0.70, 0.80, 1.00))
    parser.add_argument("--derived-windows", type=parse_float_list, default=(0.50, 2.0, 5.0, 10.0))
    parser.add_argument("--max-track-seconds", type=float, default=None)
    return parser.parse_args(argv)


def filename_label(value: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .") or "track"


def manifest_path(path: Path) -> str:
    """Prefer repository-relative paths but support external experiment roots."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def matched_predictions(predicted: np.ndarray, actual: np.ndarray, tolerance: float) -> np.ndarray:
    """Return a mask for greedy one-to-one predicted/verified matches."""
    predicted = np.asarray(predicted, dtype=np.float64)
    actual = np.asarray(actual, dtype=np.float64)
    matched = np.zeros(len(predicted), dtype=bool)
    pred_index = actual_index = 0
    while pred_index < len(predicted) and actual_index < len(actual):
        difference = predicted[pred_index] - actual[actual_index]
        if abs(difference) <= tolerance:
            matched[pred_index] = True
            pred_index += 1
            actual_index += 1
        elif difference < 0:
            pred_index += 1
        else:
            actual_index += 1
    return matched


def plot_tile(
    rows: pd.DataFrame,
    top: pd.DataFrame,
    timeline: TimelineRecord,
    predicted: np.ndarray,
    predicted_match: np.ndarray,
    suppressed: np.ndarray,
    snapped: np.ndarray,
    low_confidence: np.ndarray,
    output_path: Path,
    start: float,
    end: float,
    mode: str,
) -> None:
    times = rows["t"].to_numpy(dtype=np.float64)
    mask = (times >= start) & (times <= end)
    verified = timeline.beat_times[(timeline.beat_times >= start) & (timeline.beat_times <= end)]
    predicted_mask = (predicted >= start) & (predicted <= end)
    tile_predicted = predicted[predicted_mask]
    tile_matches = predicted_match[predicted_mask]
    tile_suppressed = suppressed[(suppressed >= start) & (suppressed <= end)]
    tile_snapped = snapped[(snapped >= start) & (snapped <= end)]
    tile_low_confidence = low_confidence[(low_confidence >= start) & (low_confidence <= end)]
    figure, axes = plt.subplots(len(top), 1, figsize=(14, 2.3 * len(top)), sharex=True)
    axes = np.atleast_1d(axes)
    for axis, (_, record) in zip(axes, top.iterrows()):
        feature = str(record["feature"])
        values = rows[feature].to_numpy(dtype=np.float64)
        values = (values - np.nanmedian(values)) / (np.nanstd(values) + 1e-9)
        axis.plot(times[mask], values[mask], color="#1f77b4", label=feature)
        for event in verified:
            axis.axvline(event, color="#202020", alpha=0.42, linewidth=1.0)
        for event in tile_predicted[tile_matches]:
            axis.axvline(event, color="#1b9e77", alpha=0.85, linewidth=1.25, linestyle="--")
        for event in tile_predicted[~tile_matches]:
            axis.axvline(event, color="#d95f02", alpha=0.78, linewidth=1.1, linestyle="--")
        for event in tile_suppressed:
            axis.axvline(event, color="#7b3294", alpha=0.65, linewidth=0.8, linestyle=":")
        for event in tile_low_confidence:
            axis.axvline(event, color="#777777", alpha=0.72, linewidth=0.9, linestyle="--")
        if len(tile_snapped):
            axis.plot(tile_snapped, np.full(len(tile_snapped), axis.get_ylim()[1]), "o", color="#377eb8", markersize=3.2, label="candidate snapped")
        axis.legend(loc="upper right")
        axis.grid(alpha=0.2)
    axes[0].legend(handles=[
        Line2D([0], [0], color="#1f77b4", label="signal"),
        Line2D([0], [0], color="#202020", label="verified beat"),
        Line2D([0], [0], color="#1b9e77", linestyle="--", label="matched detection"),
        Line2D([0], [0], color="#d95f02", linestyle="--", label="unmatched detection"),
        Line2D([0], [0], color="#7b3294", linestyle=":", label="suppressed subdivision"),
        Line2D([0], [0], color="#377eb8", marker="o", linestyle="", label="candidate snapped"),
        Line2D([0], [0], color="#777777", linestyle="--", label="low-confidence grid"),
    ], loc="upper right")
    axes[-1].set_xlabel("Time (seconds)")
    label = "stable grid decoder" if mode == "grid-decoder" else "adaptive raw candidates"
    figure.suptitle(f"Verified versus {label}: {timeline.track_id} [{start:.1f}-{end:.1f}s]")
    figure.tight_layout()
    figure.savefig(output_path, dpi=160)
    plt.close(figure)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.tile_seconds <= 0 or args.match_tolerance <= 0 or args.top_signals <= 0:
        raise ValueError("tile duration, match tolerance, and top-signal count must be positive")
    for name in ("features", "manifest", "splits", "rankings", "candidates", "decoded_beats", "output_dir"):
        setattr(args, name, resolve_path(getattr(args, name), REPO_ROOT))
    if not args.rankings.is_file():
        raise FileNotFoundError("--rankings must exist")
    if args.mode == "raw-candidates" and not args.candidates.is_file():
        raise FileNotFoundError("--candidates must exist for --mode raw-candidates")
    if args.mode == "grid-decoder" and (not args.candidates.is_file() or not args.decoded_beats.is_file()):
        raise FileNotFoundError("--candidates and --decoded-beats must exist for --mode grid-decoder")
    if args.output_dir.exists() and any(args.output_dir.iterdir()) and not args.allow_existing:
        raise FileExistsError(f"Refusing to overwrite existing overlay directory: {args.output_dir}")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    frame_df, timelines, development_tracks, test_tracks = load_dataset(args)
    rankings = pd.read_csv(args.rankings)
    top = rankings.sort_values("beat_auc_oriented", ascending=False).head(args.top_signals).rename(columns={"beat_auc_oriented": "beat_auc_oriented_development"})
    sources = sorted({str(feature).split("__", 1)[0] for feature in top["feature"]})
    hop = float(np.median(np.diff(frame_df["t"].to_numpy(dtype=np.float64))))
    enriched, _ = add_derived_signals(frame_df, sources, args.periods_seconds, args.derived_windows, hop)
    candidate_df = pd.read_csv(args.candidates) if args.candidates.is_file() else pd.DataFrame()
    if args.mode == "raw-candidates":
        required_columns = {"track_id", "t", "selected_adaptive"}
        if not required_columns.issubset(candidate_df.columns):
            raise ValueError(f"Candidate file must contain {sorted(required_columns)}")
        selected_values = candidate_df["selected_adaptive"]
        if selected_values.dtype != bool:
            selected_values = selected_values.astype(str).str.casefold().eq("true")
        candidate_df["selected_adaptive"] = selected_values
        decoded_df = pd.DataFrame()
    else:
        decoded_df = pd.read_csv(args.decoded_beats)
        required_columns = {"track_id", "time_seconds", "candidate_snapped", "grid_confidence"}
        if not required_columns.issubset(decoded_df.columns):
            raise ValueError(f"Decoded beat file must contain {sorted(required_columns)}")
    timeline_by_track = {timeline.track_id: timeline for timeline in timelines}
    manifest_rows: list[dict[str, object]] = []
    if args.track_scope == "development":
        all_tracks = sorted(development_tracks)
    elif args.track_scope == "heldout":
        all_tracks = sorted(test_tracks)
    else:
        all_tracks = sorted(development_tracks | test_tracks)
    if args.track_substring:
        needle = args.track_substring.casefold()
        all_tracks = [track_id for track_id in all_tracks if needle in track_id.casefold()]
        if not all_tracks:
            raise ValueError(f"No tracks match --track-substring {args.track_substring!r}")
    for track_index, track_id in enumerate(all_tracks, start=1):
        rows = enriched.loc[enriched["track_id"].eq(track_id)].reset_index(drop=True)
        timeline = timeline_by_track[track_id]
        if args.mode == "raw-candidates":
            predicted = np.sort(candidate_df.loc[candidate_df["track_id"].eq(track_id) & candidate_df["selected_adaptive"], "t"].to_numpy(dtype=np.float64))
            suppressed = snapped = low_confidence = np.empty(0, dtype=np.float64)
        else:
            decoded_track = decoded_df.loc[decoded_df["track_id"].eq(track_id)]
            predicted = np.sort(decoded_track["time_seconds"].to_numpy(dtype=np.float64))
            suppressed = candidate_df.loc[
                candidate_df["track_id"].eq(track_id)
                & candidate_df.get("subdivision_class", pd.Series("", index=candidate_df.index)).isin(["eighth", "triplet", "sixteenth"]),
                "t",
            ].to_numpy(dtype=np.float64)
            snapped = decoded_track.loc[decoded_track["candidate_snapped"].astype(str).str.casefold().eq("true"), "time_seconds"].to_numpy(dtype=np.float64)
            low_confidence = decoded_track.loc[decoded_track["grid_confidence"] < 0.40, "time_seconds"].to_numpy(dtype=np.float64)
        actual = timeline.beat_times[(timeline.beat_times >= rows["t"].iloc[0]) & (timeline.beat_times <= rows["t"].iloc[-1])]
        matches = matched_predictions(predicted, actual, args.match_tolerance)
        track_dir = args.output_dir / f"{track_index:02d}_{filename_label(track_id)}"
        track_dir.mkdir(parents=True, exist_ok=True)
        start, last, tile_index = float(rows["t"].iloc[0]), float(rows["t"].iloc[-1]), 0
        while start <= last:
            end = min(last, start + args.tile_seconds)
            output_path = track_dir / f"tile_{tile_index:03d}_{start:07.2f}s_to_{end:07.2f}s.png"
            plot_tile(rows, top, timeline, predicted, matches, suppressed, snapped, low_confidence, output_path, start, end, args.mode)
            manifest_rows.append({"track_id": track_id, "tile_index": tile_index, "start_seconds": start, "end_seconds": end, "image_path": manifest_path(output_path), "mode": args.mode, "detected_count": int(((predicted >= start) & (predicted <= end)).sum())})
            tile_index += 1
            start = end + hop
        print(console_safe(f"[{track_index}/{len(all_tracks)}] {track_id}: {tile_index} overlays"), flush=True)
    pd.DataFrame(manifest_rows).to_csv(args.output_dir / "overlay_manifest.csv", index=False)
    print(f"wrote {len(manifest_rows)} overlays to {args.output_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
