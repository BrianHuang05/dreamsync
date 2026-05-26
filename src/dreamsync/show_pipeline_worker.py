"""Background worker: analyze + compile MP3s as they're captured.

Ready tracks are pushed to a queue.Queue for the playback consumer.
"""

from __future__ import annotations

import queue
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path


class ShowPipelineWorker:
    """Analyze + compile MP3s in a thread pool as they arrive from capture.

    Plug ``on_segment_saved`` into ``CaptureOrchestrator`` as the callback.
    Compiled (mp3_path, timeline) pairs are pushed to *ready_queue* for
    the ``ShowPlaybackConsumer``.
    """

    def __init__(
        self,
        cache,
        profile=None,
        sample_rate: int = 44100,
        max_workers: int = 2,
        ready_queue: queue.Queue | None = None,
        debug: bool = False,
        state_callback=None,
    ) -> None:
        self._cache = cache
        self._profile = profile
        self._sample_rate = sample_rate
        self._debug = debug
        self._ready_queue = ready_queue or queue.Queue()
        self._state_callback = state_callback

        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="show-pipeline"
        )
        self._lock = threading.Lock()
        self._futures: list[Future] = []
        self._processed: int = 0
        self._errors: list[tuple[Path, str]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on_segment_saved(self, mp3_path: str, metadata: dict) -> None:
        """CaptureOrchestrator callback — submits analyze+compile to pool."""
        future = self._executor.submit(self._process, Path(mp3_path), metadata)
        with self._lock:
            self._futures.append(future)

    @property
    def ready_queue(self) -> queue.Queue:
        return self._ready_queue

    def pending_count(self) -> int:
        with self._lock:
            return sum(1 for f in self._futures if not f.done())

    def stats(self) -> dict:
        with self._lock:
            return {
                "processed": self._processed,
                "errors": len(self._errors),
                "pending": sum(1 for f in self._futures if not f.done()),
            }

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _process(self, mp3_path: Path, metadata: dict) -> None:
        from .analyzer.analyze import analyze_song
        from .cache import ShowCache, cached_compile_show, path_based_track_id

        try:
            self._emit_state(mp3_path, "analyzing")
            structure = analyze_song(mp3_path, sample_rate=self._sample_rate)
            self._emit_state(mp3_path, "compiling")
            track_id = path_based_track_id(mp3_path)
            timeline, _from_cache = cached_compile_show(
                structure, self._profile, cache=self._cache, track_id=track_id
            )
            self._ready_queue.put((mp3_path, timeline))
            self._emit_state(mp3_path, "ready", timeline=timeline)
            with self._lock:
                self._processed += 1
            if self._debug:
                print(
                    f"[pipeline] Ready: {mp3_path.name} "
                    f"({len(timeline.cues)} cues)"
                )
        except Exception as exc:
            with self._lock:
                self._errors.append((mp3_path, str(exc)))
            self._emit_state(mp3_path, "failed", error=exc)
            if self._debug:
                print(f"[pipeline] Failed: {mp3_path.name}: {exc}")

    def _emit_state(self, mp3_path: Path, state: str, *, timeline=None, error: Exception | None = None) -> None:
        if self._state_callback is None:
            return
        self._state_callback(mp3_path, state, timeline=timeline, error=error)
