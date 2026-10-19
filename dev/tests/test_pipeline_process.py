"""Real spawn-based regression coverage (no audio hardware required)."""
from concurrent.futures import CancelledError, ThreadPoolExecutor
import multiprocessing
import os
from pathlib import Path
import time
import wave

import numpy as np
import pytest

from dreamsync.cache import ShowCache
from dreamsync.pipeline_process import PipelineProcesses
from dreamsync.show_pipeline_worker import ShowPipelineWorker


def process_info():
    from threadpoolctl import threadpool_info
    np.ones((32, 32)) @ np.ones((32, 32))
    return os.getpid(), [entry["num_threads"] for entry in threadpool_info()]


def fail():
    raise ValueError("intentional worker failure")


def crash():
    os._exit(9)


def wait_forever(marker):
    Path(marker).write_text(str(os.getpid()))
    time.sleep(120)


def test_runs_outside_parent_and_bounds_native_threads():
    runner = PipelineProcesses()
    try:
        pid, thread_counts = runner.run(process_info)
        assert pid != os.getpid()
        assert thread_counts and all(count == 1 for count in thread_counts)
        assert not any(child.pid == pid for child in multiprocessing.active_children())
    finally:
        runner.shutdown()


@pytest.mark.parametrize("function, message", [(fail, "intentional worker failure"),
                                               (crash, "exited")])
def test_failure_does_not_break_next_job(function, message):
    runner = PipelineProcesses()
    try:
        with pytest.raises(RuntimeError, match=message):
            runner.run(function)
        assert runner.run(process_info)[0] != os.getpid()
    finally:
        runner.shutdown()


def test_stop_terminates_active_job_and_rejects_new_jobs(tmp_path):
    marker = tmp_path / "started"
    runner = PipelineProcesses()
    with ThreadPoolExecutor(max_workers=1) as pool:
        job = pool.submit(runner.run, wait_forever, marker)
        try:
            deadline = time.monotonic() + 20
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert marker.exists(), "child did not start"
            pid = int(marker.read_text())
            runner.shutdown()
            with pytest.raises(CancelledError):
                job.result(timeout=5)
            assert not any(child.pid == pid for child in multiprocessing.active_children())
            with pytest.raises(CancelledError):
                runner.run(process_info)
        finally:
            runner.shutdown()


def test_real_worker_preserves_analysis_and_publishes_valid_cache(tmp_path):
    from dreamsync.analyzer.analyze import analyze_song
    from dreamsync.pipeline_process import analyze_track
    from dreamsync.ffmpeg import resolve_ffmpeg, resolve_ffprobe
    from dreamsync.profile import load_profile, BUILTIN_PROFILES_DIR
    if not resolve_ffmpeg() or not resolve_ffprobe():
        pytest.skip("FFmpeg required for end-to-end pipeline check")
    sr = 44100
    t = np.arange(sr * 3) / sr
    signal = 0.2 * np.sin(2 * np.pi * 220 * t)
    samples = np.column_stack((signal, signal * 0.7))
    path = tmp_path / "audio with spaces.wav"
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(sr)
        output.writeframes((samples * 32767).astype("<i2").tobytes())
    runner = PipelineProcesses()
    try:
        assert runner.run(analyze_track, path, sr).to_dict() == analyze_song(path).to_dict()
    finally:
        runner.shutdown()
    for isolated in (False, True):
        states = []
        cache = ShowCache(tmp_path / str(isolated))
        profile = load_profile(BUILTIN_PROFILES_DIR / "aurora.yaml")
        worker = ShowPipelineWorker(
            cache, profile=profile, isolate_processing=isolated,
            state_callback=lambda _, state, **kw: states.append(state),
        )
        try:
            worker.on_segment_saved(str(path), {})
            _, timeline = worker.ready_queue.get(timeout=60)
            deadline = time.monotonic() + 5
            while worker.pending_count() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert states == ["analyzing", "compiling", "ready"]
            assert worker.stats()["processed"] == 1
            assert worker.stats()["last_timing"]["analysis_seconds"] > 0
            assert timeline.duration == pytest.approx(3.0)
            assert timeline.cues
            from dreamsync.cache import path_based_track_id
            assert cache.get(path_based_track_id(path), profile) is not None
        finally:
            worker.shutdown()
