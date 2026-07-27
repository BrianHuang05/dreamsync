"""Evaluate a JSON observation fixture with the frozen baseline predictor."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dreamsync.prediction.baseline import BaselinePredictionAdapter
from dreamsync.prediction.models import LiveMusicalObservation
from dreamsync.prediction.replay import DeterministicPredictionReplay


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("fixture", type=Path)
    args = parser.parse_args()
    rows = json.loads(args.fixture.read_text(encoding="utf-8"))
    observations = tuple(LiveMusicalObservation(**row) for row in rows)
    result = DeterministicPredictionReplay(BaselinePredictionAdapter()).run(
        observations
    )
    print(json.dumps({"observations": result.observations, "predictions": len(result.predictions)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
