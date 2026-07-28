"""Turn stable-grid decoder CSV output into playable comparison show timelines.

This intentionally reuses the visual cues from a reference timeline, but
replaces *all* beat and downbeat timestamps.  It therefore isolates how the
decoder's predicted pulse feels in the existing show runtime.  It is an
offline replay tool, not a replacement for the future live decoder adapter.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dreamsync.show.models import ShowTimeline
from dreamsync.show.predicted_timeline import build_predicted_beat_timeline


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--decoded-beats",
        type=Path,
        required=True,
        help="Grid-decoder decoded_beats.csv input.",
    )
    parser.add_argument(
        "--reference-show-dir",
        type=Path,
        default=REPO_ROOT / "out" / "shows",
        help="Directory containing reference .show.json files with visual cues.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for decoder-driven .show.json files and a manifest.",
    )
    parser.add_argument(
        "--track-id",
        action="append",
        default=None,
        help="Build one matching decoder track ID; repeat to select several. Defaults to every CSV track.",
    )
    parser.add_argument(
        "--allow-existing",
        action="store_true",
        help="Permit an existing output directory and reuse compatible existing predicted show files.",
    )
    return parser.parse_args(argv)


def _read_decoder_beats(path: Path) -> dict[str, list[float]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "track_id" not in reader.fieldnames:
            raise ValueError("decoded beat CSV must contain a track_id column")
        time_column = "time_seconds" if "time_seconds" in reader.fieldnames else "time"
        if time_column not in reader.fieldnames:
            raise ValueError("decoded beat CSV must contain time_seconds or time")
        grouped: dict[str, list[float]] = {}
        for line_number, row in enumerate(reader, start=2):
            track_id = str(row.get("track_id", "")).strip()
            if not track_id:
                raise ValueError(f"decoded beat CSV row {line_number} has an empty track_id")
            try:
                beat_time = float(row[time_column])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"decoded beat CSV row {line_number} has an invalid {time_column}") from exc
            grouped.setdefault(track_id, []).append(beat_time)
    return grouped


def _reference_show_by_track_id(show_dir: Path) -> dict[str, Path]:
    references: dict[str, Path] = {}
    for path in sorted(show_dir.glob("*.show.json")):
        track_id = path.name.removesuffix(".show.json")
        references.setdefault(track_id, path)
    return references


def _console_safe(value: object) -> str:
    """Keep Unicode track names from failing on legacy Windows consoles."""

    encoding = sys.stdout.encoding or "utf-8"
    return str(value).encode(encoding, errors="replace").decode(encoding)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    decoded_path = args.decoded_beats.resolve()
    reference_show_dir = args.reference_show_dir.resolve()
    output_dir = args.output_dir.resolve()
    if not decoded_path.is_file():
        raise FileNotFoundError(f"Decoded beat CSV not found: {decoded_path}")
    if not reference_show_dir.is_dir():
        raise FileNotFoundError(f"Reference show directory not found: {reference_show_dir}")
    if output_dir.exists() and any(output_dir.iterdir()) and not args.allow_existing:
        raise FileExistsError(f"Refusing to write into nonempty output directory: {output_dir}")

    decoder_beats = _read_decoder_beats(decoded_path)
    requested_ids = set(args.track_id or decoder_beats)
    unknown_ids = requested_ids.difference(decoder_beats)
    if unknown_ids:
        raise ValueError(f"Requested track IDs are absent from decoded CSV: {sorted(unknown_ids)}")
    reference_paths = _reference_show_by_track_id(reference_show_dir)
    missing_references = requested_ids.difference(reference_paths)
    if missing_references:
        raise ValueError(
            "No exactly matching reference .show.json for decoder track IDs: "
            f"{sorted(missing_references)}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, object]] = []
    for track_id in sorted(requested_ids):
        reference_path = reference_paths[track_id]
        output_path = output_dir / f"{track_id}.grid-decoder.show.json"
        if output_path.exists():
            if not args.allow_existing:
                raise FileExistsError(f"Refusing to overwrite predicted show: {output_path}")
            timeline = ShowTimeline.from_json(output_path)
            if timeline.metadata.get("decoder_track_id") != track_id:
                raise ValueError(
                    f"Existing predicted show does not belong to {track_id}: {output_path}"
                )
            status = "Reused"
        else:
            reference = ShowTimeline.from_json(reference_path)
            timeline = build_predicted_beat_timeline(
                reference,
                decoder_beats[track_id],
                decoder_metadata={
                    "decoder_track_id": track_id,
                    "decoder_csv": str(decoded_path),
                    "reference_cue_timeline": str(reference_path),
                },
            )
            timeline.to_json(output_path)
            status = "Built"
        manifest.append(
            {
                "track_id": track_id,
                "show_path": str(output_path),
                "reference_show_path": str(reference_path),
                "decoder_csv": str(decoded_path),
                "beat_count": len(timeline.beat_times),
                "synthetic_downbeat_count": len(timeline.downbeat_times),
                "median_predicted_bpm": round(timeline.bpm, 4),
            }
        )
        print(_console_safe(
            f"{status} {track_id}: {len(timeline.beat_times)} decoder beats -> {output_path}"
        ))

    manifest_path = output_dir / "predicted_show_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(_console_safe(f"Wrote manifest: {manifest_path}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
