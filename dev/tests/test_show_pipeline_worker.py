"""Tests for ShowPipelineWorker (background analyze + compile)."""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dreamsync.show_pipeline_worker import ShowPipelineWorker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_structure():
    """Return a minimal mock SongStructure."""
    s = MagicMock()
    s.bpm = 120.0
    s.duration = 180.0
    return s


def _fake_timeline():
    """Return a minimal mock ShowTimeline."""
    t = MagicMock()
    t.cues = [MagicMock()] * 5
    t.duration = 180.0
    return t


def _make_worker(ready_queue=None, max_workers=2, debug=False):
    cache = MagicMock()
    return ShowPipelineWorker(
        cache=cache,
        profile=None,
        sample_rate=44100,
        max_workers=max_workers,
        ready_queue=ready_queue,
        debug=debug,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestShowPipelineWorker:

    @patch("dreamsync.cache.cached_compile_show")
    @patch("dreamsync.cache.path_based_track_id", return_value="test_id")
    @patch("dreamsync.analyzer.analyze.analyze_song")
    def test_on_segment_saved_triggers_processing(
        self, mock_analyze, mock_track_id, mock_compile
    ):
        structure = _fake_structure()
        timeline = _fake_timeline()
        mock_analyze.return_value = structure
        mock_compile.return_value = (timeline, False)

        q = queue.Queue()
        worker = _make_worker(ready_queue=q)
        worker.on_segment_saved("/tmp/song.mp3", {"title": "Test"})

        # Wait for processing
        result = q.get(timeout=5)
        worker.shutdown()

        assert result[0] == Path("/tmp/song.mp3")
        assert result[1] is timeline
        mock_analyze.assert_called_once()

    def test_ready_queue_empty_initially(self):
        worker = _make_worker()
        assert worker.ready_queue.empty()
        worker.shutdown()

    @patch("dreamsync.cache.cached_compile_show")
    @patch("dreamsync.cache.path_based_track_id", return_value="id")
    @patch("dreamsync.analyzer.analyze.analyze_song")
    def test_ready_queue_receives_items_in_order(
        self, mock_analyze, mock_track_id, mock_compile
    ):
        mock_analyze.return_value = _fake_structure()
        timelines = [_fake_timeline() for _ in range(3)]
        call_count = [0]

        def _compile_side_effect(*args, **kwargs):
            idx = call_count[0]
            call_count[0] += 1
            return timelines[idx], False

        mock_compile.side_effect = _compile_side_effect

        q = queue.Queue()
        worker = _make_worker(ready_queue=q, max_workers=1)

        for i in range(3):
            worker.on_segment_saved(f"/tmp/song{i}.mp3", {})

        results = []
        for _ in range(3):
            results.append(q.get(timeout=5))
        worker.shutdown()

        assert len(results) == 3
        for i, (path, tl) in enumerate(results):
            assert path == Path(f"/tmp/song{i}.mp3")

    @patch("dreamsync.cache.cached_compile_show")
    @patch("dreamsync.cache.path_based_track_id", return_value="id")
    @patch("dreamsync.analyzer.analyze.analyze_song")
    def test_ready_queue_unblocks_consumer(
        self, mock_analyze, mock_track_id, mock_compile
    ):
        mock_analyze.return_value = _fake_structure()
        mock_compile.return_value = (_fake_timeline(), False)

        q = queue.Queue()
        worker = _make_worker(ready_queue=q)
        received = []
        event = threading.Event()

        def consumer():
            item = q.get(timeout=5)
            received.append(item)
            event.set()

        t = threading.Thread(target=consumer, daemon=True)
        t.start()

        worker.on_segment_saved("/tmp/song.mp3", {})
        event.wait(timeout=5)
        worker.shutdown()

        assert len(received) == 1

    @patch("dreamsync.analyzer.analyze.analyze_song")
    def test_analysis_failure_logged_not_fatal(self, mock_analyze):
        mock_analyze.side_effect = RuntimeError("decode failed")

        q = queue.Queue()
        worker = _make_worker(ready_queue=q, debug=True)
        worker.on_segment_saved("/tmp/bad.mp3", {})

        # Give the worker time to process
        time.sleep(1)
        worker.shutdown()

        assert q.empty()
        stats = worker.stats()
        assert stats["errors"] == 1
        assert stats["processed"] == 0

    @patch("dreamsync.cache.cached_compile_show")
    @patch("dreamsync.cache.path_based_track_id", return_value="id")
    @patch("dreamsync.analyzer.analyze.analyze_song")
    def test_compile_failure_logged_not_fatal(
        self, mock_analyze, mock_track_id, mock_compile
    ):
        mock_analyze.return_value = _fake_structure()
        mock_compile.side_effect = RuntimeError("compile failed")

        q = queue.Queue()
        worker = _make_worker(ready_queue=q, debug=True)
        worker.on_segment_saved("/tmp/song.mp3", {})

        time.sleep(1)
        worker.shutdown()

        assert q.empty()
        stats = worker.stats()
        assert stats["errors"] == 1

    @patch("dreamsync.cache.cached_compile_show")
    @patch("dreamsync.cache.path_based_track_id", return_value="id")
    @patch("dreamsync.analyzer.analyze.analyze_song")
    def test_pending_count_tracks_in_flight(
        self, mock_analyze, mock_track_id, mock_compile
    ):
        gate = threading.Event()

        def slow_analyze(*args, **kwargs):
            gate.wait(timeout=5)
            return _fake_structure()

        mock_analyze.side_effect = slow_analyze
        mock_compile.return_value = (_fake_timeline(), False)

        worker = _make_worker(max_workers=1)
        worker.on_segment_saved("/tmp/song1.mp3", {})
        worker.on_segment_saved("/tmp/song2.mp3", {})

        time.sleep(0.2)
        assert worker.pending_count() > 0

        gate.set()
        time.sleep(1)
        worker.shutdown()

    @patch("dreamsync.cache.cached_compile_show")
    @patch("dreamsync.cache.path_based_track_id", return_value="id")
    @patch("dreamsync.analyzer.analyze.analyze_song")
    def test_stats_reflects_processed_and_errors(
        self, mock_analyze, mock_track_id, mock_compile
    ):
        call_count = [0]

        def _analyze_side(*args, **kwargs):
            idx = call_count[0]
            call_count[0] += 1
            if idx == 2:
                raise RuntimeError("bad file")
            return _fake_structure()

        mock_analyze.side_effect = _analyze_side
        mock_compile.return_value = (_fake_timeline(), False)

        q = queue.Queue()
        worker = _make_worker(ready_queue=q, max_workers=1)

        for i in range(3):
            worker.on_segment_saved(f"/tmp/song{i}.mp3", {})

        # Wait for all to complete
        time.sleep(2)
        worker.shutdown()

        stats = worker.stats()
        assert stats["processed"] == 2
        assert stats["errors"] == 1

    @patch("dreamsync.cache.cached_compile_show")
    @patch("dreamsync.cache.path_based_track_id", return_value="id")
    @patch("dreamsync.analyzer.analyze.analyze_song")
    def test_cache_hit_skips_recompile(
        self, mock_analyze, mock_track_id, mock_compile
    ):
        mock_analyze.return_value = _fake_structure()
        mock_compile.return_value = (_fake_timeline(), True)  # cache hit

        q = queue.Queue()
        worker = _make_worker(ready_queue=q)
        worker.on_segment_saved("/tmp/cached.mp3", {})

        result = q.get(timeout=5)
        worker.shutdown()

        assert result is not None
        mock_compile.assert_called_once()

    @patch("dreamsync.cache.cached_compile_show")
    @patch("dreamsync.cache.path_based_track_id", return_value="id")
    @patch("dreamsync.analyzer.analyze.analyze_song")
    def test_multiple_concurrent_submissions(
        self, mock_analyze, mock_track_id, mock_compile
    ):
        mock_analyze.return_value = _fake_structure()
        mock_compile.return_value = (_fake_timeline(), False)

        q = queue.Queue()
        worker = _make_worker(ready_queue=q, max_workers=2)

        for i in range(5):
            worker.on_segment_saved(f"/tmp/song{i}.mp3", {})

        results = []
        for _ in range(5):
            results.append(q.get(timeout=10))
        worker.shutdown()

        assert len(results) == 5

    def test_shutdown_idempotent(self):
        worker = _make_worker()
        worker.shutdown()
        worker.shutdown()  # should not raise

    @patch("dreamsync.cache.cached_compile_show")
    @patch("dreamsync.cache.path_based_track_id", return_value="id")
    @patch("dreamsync.analyzer.analyze.analyze_song")
    def test_custom_ready_queue_used(
        self, mock_analyze, mock_track_id, mock_compile
    ):
        mock_analyze.return_value = _fake_structure()
        mock_compile.return_value = (_fake_timeline(), False)

        my_queue = queue.Queue()
        worker = _make_worker(ready_queue=my_queue)
        worker.on_segment_saved("/tmp/song.mp3", {})

        result = my_queue.get(timeout=5)
        worker.shutdown()

        assert result is not None
        assert worker.ready_queue is my_queue
