"""Background worker events for the desktop GUI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkerEvent:
    name: str
    payload: Any = None


@dataclass(frozen=True)
class WorkerSuccess(WorkerEvent):
    result: Any = None


@dataclass(frozen=True)
class WorkerFailure(WorkerEvent):
    error: Exception | None = None
