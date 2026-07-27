"""Bounded structured logging for prediction diagnostics and replay."""

from __future__ import annotations

import dataclasses
import json
from collections import deque
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


class PredictionEventLog:
    """Keep a bounded RAM log; filesystem persistence is an explicit flush."""

    def __init__(self, max_events: int = 4096) -> None:
        if max_events < 1:
            raise ValueError("max_events must be positive")
        self._events: deque[dict[str, Any]] = deque(maxlen=int(max_events))

    def append(self, kind: str, *, t: float, payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict):
            serializable = payload
        elif dataclasses.is_dataclass(payload):
            serializable = dataclasses.asdict(payload)
        else:
            raise TypeError("payload must be a dict or dataclass")
        row = {
            "schema_version": SCHEMA_VERSION,
            "kind": str(kind),
            "t": float(t),
            "payload": serializable,
        }
        self._events.append(row)
        return row

    def snapshot(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(row) for row in self._events)

    def clear(self) -> None:
        self._events.clear()

    def flush_jsonl(self, path: Path) -> int:
        """Persist the current bounded snapshot outside the analysis loop."""

        rows = self.snapshot()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")))
                handle.write("\n")
        return len(rows)
