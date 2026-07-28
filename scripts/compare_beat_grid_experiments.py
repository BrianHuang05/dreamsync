"""Compare grid-decoder experiments only when cache and split provenance match."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

import pandas as pd


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiments", type=Path, nargs="+", help="Experiment directories to compare.")
    parser.add_argument("--output", type=Path, default=None, help="Optional CSV output path.")
    return parser.parse_args(argv)


def _config(path: Path) -> dict[str, object]:
    config_path = path / "experiment_config.json"
    if not config_path.is_file():
        raise FileNotFoundError(f"Missing experiment configuration: {config_path}")
    return json.loads(config_path.read_text(encoding="utf-8"))


def _compatibility_key(config: dict[str, object]) -> tuple[str, str, str, str]:
    hashes = config.get("source_hashes", {})
    return (
        str(hashes.get("features", "")),
        str(hashes.get("manifest", "")),
        str(hashes.get("splits", "")),
        json.dumps(config.get("split_identifiers", {}), sort_keys=True),
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    directories = [path.resolve() for path in args.experiments]
    configs = [_config(path) for path in directories]
    keys = {_compatibility_key(config) for config in configs}
    if len(keys) != 1:
        raise ValueError("Refusing to compare experiments with incompatible source-cache or split versions")
    frames: list[pd.DataFrame] = []
    for directory in directories:
        summary_path = directory / "summary.json"
        if not summary_path.is_file():
            raise FileNotFoundError(f"Missing summary: {summary_path}")
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        frame = pd.DataFrame(summary.get("cohort_metrics", []))
        frame.insert(0, "experiment", directory.name)
        frames.append(frame)
    result = pd.concat(frames, ignore_index=True)
    if args.output is not None:
        args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(args.output, index=False)
    else:
        print(result.to_csv(index=False), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
