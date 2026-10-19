"""Cancellable CPU jobs kept outside the capture/playback Python interpreter."""
from __future__ import annotations

from concurrent.futures import CancelledError
import multiprocessing
import threading
import traceback


def _execute(connection, function, args):
    try:
        # Spawn avoids inheriting Qt, PortAudio, and locks from running threads.
        # Small DSP matrix operations do not benefit from a BLAS pool per job.
        # Load both native runtimes before discovering their thread controllers;
        # imports performed inside the job would otherwise escape the limit.
        import numpy  # noqa: F401
        import scipy.linalg  # noqa: F401
        from threadpoolctl import threadpool_limits

        with threadpool_limits(limits=1):
            result = function(*args)
        connection.send((True, result))
    except BaseException:
        connection.send((False, traceback.format_exc()))
    finally:
        connection.close()


def analyze_track(path, sample_rate):
    from dreamsync.analyzer.analyze import analyze_song

    return analyze_song(path, sample_rate=sample_rate)


def compile_track(structure, profile, cache_dir, track_id):
    from dreamsync.cache import ShowCache, cached_compile_show

    return cached_compile_show(
        structure, profile, cache=ShowCache(cache_dir), track_id=track_id,
    )


class PipelineProcesses:
    """Run at most one child per calling coordinator thread.

    Each operation owns its child. Stop cancels active jobs, and children are
    reaped before coordinator threads finish. A crashed job cannot poison the
    next job's process or leave a shared pool unusable.
    """

    def __init__(self):
        self._context = multiprocessing.get_context("spawn")
        self._lock = threading.Lock()
        self._stopped = threading.Event()

    def run(self, function, *args):
        reader, writer = self._context.Pipe(duplex=False)
        child = self._context.Process(target=_execute, args=(writer, function, args))
        started = False
        try:
            with self._lock:
                if self._stopped.is_set():
                    raise CancelledError()
                child.start()
                started = True
            writer.close()
            while not reader.poll(0.05):
                if self._stopped.is_set():
                    raise CancelledError()
                if not child.is_alive():
                    raise RuntimeError(f"Pipeline process exited with code {child.exitcode}")
            if self._stopped.is_set():
                raise CancelledError()
            try:
                success, result = reader.recv()
            except EOFError as exc:
                child.join(timeout=0.2)
                raise RuntimeError(
                    f"Pipeline process exited without a result (code {child.exitcode})"
                ) from exc
            if not success:
                raise RuntimeError(result)
            return result
        finally:
            reader.close()
            writer.close()
            if started:
                child.join(timeout=0.2)
                if child.is_alive():
                    child.terminate()
                    child.join(timeout=2)
                if child.is_alive():
                    child.kill()
                    child.join()
                child.close()

    def shutdown(self):
        with self._lock:
            self._stopped.set()
