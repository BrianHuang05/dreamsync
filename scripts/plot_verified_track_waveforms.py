"""Create one top-signal waveform diagnostic for every verified track.

This is the batch counterpart to ``diagnose_beat_bands.py``'s
``top_signal_waveforms.png``.  The top signals are selected once using the
development split, then the same signals and scaling are plotted for every
verified track so the images can be compared directly.
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

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from beat_model_analysis import TimelineRecord, console_safe  # noqa: E402
from diagnose_beat_bands import (  # noqa: E402
    add_derived_signals,
    parse_float_list,
)
from tune_beat_ridge import load_dataset, resolve_path  # noqa: E402


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
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
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("out/beat-band-diagnostics/verified-track-waveforms"),
    )
    parser.add_argument(
        "--rankings",
        type=Path,
        default=Path("out/beat-band-diagnostics/development_signal_auc.csv"),
        help="Development rankings written by diagnose_beat_bands.py.",
    )
    parser.add_argument(
        "--periods-seconds",
        type=parse_float_list,
        default=(0.40, 0.50, 0.60, 0.70, 0.80, 1.00),
    )
    parser.add_argument(
        "--derived-windows",
        type=parse_float_list,
        default=(0.50, 2.0, 5.0, 10.0),
    )
    parser.add_argument("--plot-seconds", type=float, default=30.0)
    parser.add_argument("--top-signals", type=int, default=6)
    parser.add_argument("--max-track-seconds", type=float, default=None)
    return parser.parse_args(argv)


def filename_label(track_id: str) -> str:
    """Return a Windows-safe filename while keeping the track recognizable."""
    label = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(track_id)).strip(" .")
    return label or "track"


def plot_track_waveforms(
    track_rows: pd.DataFrame,
    top: pd.DataFrame,
    timeline: TimelineRecord,
    output_path: Path,
    duration: float,
) -> None:
    track_rows = track_rows.reset_index(drop=True)
    times = track_rows["t"].to_numpy(dtype=np.float64)
    plot_end = min(float(times[-1]), float(times[0]) + duration)
    mask = times <= plot_end

    figure, axes = plt.subplots(len(top), 1, figsize=(14, 2.3 * len(top)), sharex=True)
    axes = np.atleast_1d(axes)
    for axis, (_, record) in zip(axes, top.iterrows()):
        feature = str(record["feature"])
        values = track_rows[feature].to_numpy(dtype=np.float64)
        values = (values - np.nanmedian(values)) / (np.nanstd(values) + 1e-9)
        axis.plot(
            times[mask],
            values[mask],
            label=(
                f"{feature} "
                f"(dev beat AUC={record['beat_auc_oriented_development']:.3f})"
            ),
        )
        for event in timeline.beat_times[
            (timeline.beat_times >= times[0]) & (timeline.beat_times <= plot_end)
        ]:
            axis.axvline(event, color="#333333", alpha=0.22)
        axis.legend(loc="upper right")
        axis.grid(alpha=0.2)

    axes[-1].set_xlabel("Time (seconds)")
    figure.suptitle(f"Top signals versus verified beats: {timeline.track_id}")
    figure.tight_layout()
    figure.savefig(output_path, dpi=160)
    plt.close(figure)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.plot_seconds <= 0:
        raise ValueError("--plot-seconds must be positive")
    if args.top_signals <= 0:
        raise ValueError("--top-signals must be positive")

    args.features = resolve_path(args.features, REPO_ROOT)
    args.manifest = resolve_path(args.manifest, REPO_ROOT)
    args.splits = resolve_path(args.splits, REPO_ROOT)
    args.rankings = resolve_path(args.rankings, REPO_ROOT)
    args.output_dir = resolve_path(args.output_dir, REPO_ROOT)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    frame_df, timelines, development_tracks, test_tracks = load_dataset(args)
    verified_tracks = development_tracks | test_tracks
    if not args.rankings.is_file():
        raise FileNotFoundError(
            f"Development signal rankings not found: {args.rankings}. "
            "Run diagnose_beat_bands.py first."
        )
    rankings = pd.read_csv(args.rankings)
    required_ranking_columns = {"feature", "beat_auc_oriented"}
    if not required_ranking_columns.issubset(rankings.columns):
        raise ValueError(
            f"Ranking file must contain {sorted(required_ranking_columns)}: {args.rankings}"
        )
    top = (
        rankings.sort_values("beat_auc_oriented", ascending=False)
        .head(args.top_signals)
        .rename(columns={"beat_auc_oriented": "beat_auc_oriented_development"})
    )
    sources = sorted({str(feature).split("__", 1)[0] for feature in top["feature"]})
    missing_sources = sorted(set(sources) - set(frame_df.columns))
    if missing_sources:
        raise ValueError(f"Ranked source signals are missing from the feature cache: {missing_sources}")

    hop = float(np.median(np.diff(frame_df["t"].to_numpy(dtype=np.float64))))
    enriched, _ = add_derived_signals(
        frame_df,
        sources,
        args.periods_seconds,
        args.derived_windows,
        hop,
    )
    missing_features = sorted(set(top["feature"].astype(str)) - set(enriched.columns))
    if missing_features:
        raise ValueError(
            "Ranked derived signals could not be reproduced with the requested periods/windows: "
            f"{missing_features}"
        )

    timeline_by_track = {timeline.track_id: timeline for timeline in timelines}
    written = 0
    for index, track_id in enumerate(sorted(verified_tracks), start=1):
        track_rows = enriched.loc[enriched["track_id"].eq(track_id)]
        if track_rows.empty:
            raise ValueError(f"No feature rows found for verified track: {track_id}")
        output_path = args.output_dir / (
            f"{index:02d}_{filename_label(track_id)}__top_signal_waveforms.png"
        )
        plot_track_waveforms(
            track_rows,
            top,
            timeline_by_track[track_id],
            output_path,
            args.plot_seconds,
        )
        written += 1
        print(console_safe(f"[{written}/{len(verified_tracks)}] {output_path.name}"), flush=True)

    print(f"wrote {written} waveform graphs to {args.output_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
