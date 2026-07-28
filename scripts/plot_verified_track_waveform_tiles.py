"""Create consecutive waveform tiles for every verified track.

The existing first-window images remain untouched.  This script writes a new
directory tree containing 30-second (configurable) views across each full
track, using the same development-selected signals and verified-beat overlays.
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
from diagnose_beat_bands import add_derived_signals, parse_float_list  # noqa: E402
from tune_beat_ridge import load_dataset, resolve_path  # noqa: E402


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", type=Path, default=Path("out/beat-ml-analysis/frame_features.csv.gz"))
    parser.add_argument("--manifest", type=Path, default=Path("out/beat-ml-analysis/verified_dataset_manifest.csv"))
    parser.add_argument("--splits", type=Path, default=Path("out/beat-ml-analysis/track_split.csv"))
    parser.add_argument("--rankings", type=Path, default=Path("out/beat-band-diagnostics/development_signal_auc.csv"))
    parser.add_argument("--output-dir", type=Path, default=Path("out/beat-band-diagnostics/verified-track-waveform-tiles"))
    parser.add_argument("--tile-seconds", type=float, default=30.0)
    parser.add_argument("--top-signals", type=int, default=6)
    parser.add_argument("--periods-seconds", type=parse_float_list, default=(0.40, 0.50, 0.60, 0.70, 0.80, 1.00))
    parser.add_argument("--derived-windows", type=parse_float_list, default=(0.50, 2.0, 5.0, 10.0))
    parser.add_argument("--max-track-seconds", type=float, default=None)
    return parser.parse_args(argv)


def filename_label(value: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .") or "track"


def plot_tile(
    track_rows: pd.DataFrame,
    top: pd.DataFrame,
    timeline: TimelineRecord,
    output_path: Path,
    start: float,
    end: float,
) -> None:
    times = track_rows["t"].to_numpy(dtype=np.float64)
    mask = (times >= start) & (times <= end)
    if not np.any(mask):
        return
    figure, axes = plt.subplots(len(top), 1, figsize=(14, 2.3 * len(top)), sharex=True)
    axes = np.atleast_1d(axes)
    for axis, (_, record) in zip(axes, top.iterrows()):
        feature = str(record["feature"])
        values = track_rows[feature].to_numpy(dtype=np.float64)
        values = (values - np.nanmedian(values)) / (np.nanstd(values) + 1e-9)
        axis.plot(times[mask], values[mask], label=f"{feature} (dev beat AUC={record['beat_auc_oriented_development']:.3f})")
        for event in timeline.beat_times[(timeline.beat_times >= start) & (timeline.beat_times <= end)]:
            axis.axvline(event, color="#333333", alpha=0.22)
        axis.legend(loc="upper right")
        axis.grid(alpha=0.2)
    axes[-1].set_xlabel("Time (seconds)")
    figure.suptitle(f"Top signals versus verified beats: {timeline.track_id} [{start:.1f}-{end:.1f}s]")
    figure.tight_layout()
    figure.savefig(output_path, dpi=160)
    plt.close(figure)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.tile_seconds <= 0 or args.top_signals <= 0:
        raise ValueError("--tile-seconds and --top-signals must be positive")
    for name in ("features", "manifest", "splits", "rankings", "output_dir"):
        setattr(args, name, resolve_path(getattr(args, name), REPO_ROOT))
    if not args.rankings.is_file():
        raise FileNotFoundError(f"Development signal rankings not found: {args.rankings}")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    frame_df, timelines, development_tracks, test_tracks = load_dataset(args)
    rankings = pd.read_csv(args.rankings)
    if not {"feature", "beat_auc_oriented"}.issubset(rankings.columns):
        raise ValueError("Ranking file must contain feature and beat_auc_oriented")
    top = rankings.sort_values("beat_auc_oriented", ascending=False).head(args.top_signals).rename(
        columns={"beat_auc_oriented": "beat_auc_oriented_development"}
    )
    sources = sorted({str(feature).split("__", 1)[0] for feature in top["feature"]})
    missing_sources = sorted(set(sources) - set(frame_df.columns))
    if missing_sources:
        raise ValueError(f"Ranked source signals are missing: {missing_sources}")
    hop = float(np.median(np.diff(frame_df["t"].to_numpy(dtype=np.float64))))
    enriched, _ = add_derived_signals(frame_df, sources, args.periods_seconds, args.derived_windows, hop)
    missing_features = sorted(set(top["feature"].astype(str)) - set(enriched.columns))
    if missing_features:
        raise ValueError(f"Ranked features could not be reproduced: {missing_features}")

    timeline_by_track = {timeline.track_id: timeline for timeline in timelines}
    manifest_rows: list[dict[str, object]] = []
    all_tracks = sorted(development_tracks | test_tracks)
    for track_index, track_id in enumerate(all_tracks, start=1):
        rows = enriched.loc[enriched["track_id"].eq(track_id)].reset_index(drop=True)
        first, last = float(rows["t"].iloc[0]), float(rows["t"].iloc[-1])
        track_dir = args.output_dir / f"{track_index:02d}_{filename_label(track_id)}"
        track_dir.mkdir(parents=True, exist_ok=True)
        tile_index = 0
        start = first
        while start <= last:
            end = min(last, start + args.tile_seconds)
            output_path = track_dir / f"tile_{tile_index:03d}_{start:07.2f}s_to_{end:07.2f}s.png"
            plot_tile(rows, top, timeline_by_track[track_id], output_path, start, end)
            manifest_rows.append({"track_id": track_id, "tile_index": tile_index, "start_seconds": start, "end_seconds": end, "image_path": str(output_path.relative_to(REPO_ROOT))})
            tile_index += 1
            start = end + hop
        print(console_safe(f"[{track_index}/{len(all_tracks)}] {track_id}: {tile_index} tiles"), flush=True)
    pd.DataFrame(manifest_rows).to_csv(args.output_dir / "tile_manifest.csv", index=False)
    print(f"wrote {len(manifest_rows)} tiles to {args.output_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
