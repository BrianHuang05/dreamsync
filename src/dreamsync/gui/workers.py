"""Background worker wrappers for non-UI tasks."""

from __future__ import annotations

import queue
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable

from .events import WorkerFailure, WorkerSuccess


@dataclass(frozen=True)
class WorkerResult:
    name: str
    ok: bool
    value: Any


class WorkerPool:
    """Run background tasks and expose completions through a thread-safe queue."""

    def __init__(self, max_workers: int = 4) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="gui")
        self._events: queue.Queue[WorkerSuccess | WorkerFailure] = queue.Queue()

    def submit(self, name: str, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Future:
        future = self._executor.submit(func, *args, **kwargs)

        def _callback(done: Future) -> None:
            try:
                result = done.result()
            except Exception as exc:  # pragma: no cover - exercised via consumers
                self._events.put(WorkerFailure(name=name, payload=None, error=exc))
            else:
                self._events.put(WorkerSuccess(name=name, payload=result, result=result))

        future.add_done_callback(_callback)
        return future

    def drain_events(self) -> list[WorkerSuccess | WorkerFailure]:
        items: list[WorkerSuccess | WorkerFailure] = []
        while True:
            try:
                items.append(self._events.get_nowait())
            except queue.Empty:
                return items

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False)
