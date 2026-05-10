"""Integration tests: ShowPipelineWorker ↔ ShowPlaybackConsumer handoff.

These tests wire Worker + Consumer through a shared queue.Queue, verifying
the handoff under normal and error conditions. All analysis/compile/playback
is mocked — no real audio, FFmpeg, or device I/O.
"""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dreamsync.output.null_adapter import NullMultiAdapter
from dreamsync.show_pipeline_worker import ShowPipelineWorker
from dreamsync.show_playback_consumer import ShowPlaybackConsumer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_structure():
    s = MagicMock()
    s.bpm = 120.0
    s.duration = 180.0
    return s


def _fake_timeline(duration: float = 1.0):
    t = MagicMock()
    t.cues = [MagicMock()] * 3
    t.duration = duration
    return t


def _make_worker(ready_queue, max_workers=1, debug=False):
    cache = MagicMock()
    return ShowPipelineWorker(
        cache=cache,
        profile=None,
        sample_rate=44100,
        max_workers=max_workers,
        ready_queue=ready_queue,
        debug=debug,
    )


def _make_consumer(ready_queue, adapter=None, purge=False, debug=False):
    return ShowPlaybackConsumer(
        ready_queue=ready_queue,
        multi_adapter=adapter or NullMultiAdapter(),
        sample_rate=44100,
        audio_device=None,
        purge=purge,
        debug=debug,
    )


def _mock_player():
    player = MagicMock()
    player.finished = True
    player.position_seconds = 0.0
    return player


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPipelineIntegration:

    @patch("dreamsync.show.runtime.ShowPlaybackRuntime")
    @patch("dreamsync.show.player.AudioPlayer")
    @patch("dreamsync.cache.cached_compile_show")
    @patch("dreamsync.cache.path_based_track_id", return_value="test_id")
    @patch("dreamsync.analyzer.analyze.analyze_song")
    def test_worker_to_consumer_handoff(
        self, mock_analyze, mock_track_id, mock_compile,
        MockPlayer, MockRuntime,
    ):
        """Worker processes a segment → consumer picks it up and plays it."""
        mock_analyze.return_value = _fake_structure()
        mock_compile.return_value = (_fake_timeline(), False)
        MockPlayer.return_value = _mock_player()
        MockRuntime.return_value = MagicMock()

        q = queue.Queue()
        stop = threading.Event()
        worker = _make_worker(ready_queue=q)
        consumer = _make_consumer(ready_queue=q)

        # Run consumer in a thread
        consumer_thread = threading.Thread(
            target=consumer.run, args=(stop,), daemon=True,
        )
        consumer_thread.start()

        # Fire one segment through the worker
        worker.on_segment_saved("/tmp/song.mp3", {"title": "Test"})

        # Wait for consumer to play it
        deadline = time.monotonic() + 5
        while consumer.tracks_played < 1 and time.monotonic() < deadline:
            time.sleep(0.05)

        stop.set()
        consumer_thread.join(timeout=3)
        worker.shutdown()

        assert consumer.tracks_played == 1
        assert worker.stats()["processed"] == 1
        assert q.empty()

    @patch("dreamsync.show.runtime.ShowPlaybackRuntime")
    @patch("dreamsync.show.player.AudioPlayer")
    @patch("dreamsync.cache.cached_compile_show")
    @patch("dreamsync.cache.path_based_track_id", return_value="id")
    @patch("dreamsync.analyzer.analyze.analyze_song")
    def test_multiple_segments_flow_through_pipeline(
        self, mock_analyze, mock_track_id, mock_compile,
        MockPlayer, MockRuntime,
    ):
        """3 segments submitted → all 3 reach consumer in order."""
        mock_analyze.return_value = _fake_structure()
        mock_compile.return_value = (_fake_timeline(), False)
        MockPlayer.return_value = _mock_player()
        MockRuntime.return_value = MagicMock()

        q = queue.Queue()
        stop = threading.Event()
        worker = _make_worker(ready_queue=q, max_workers=1)
        consumer = _make_consumer(ready_queue=q)

        played_paths = []
        orig_play_one = consumer._play_one

        def tracking_play_one(mp3_path, timeline, stop_event):
            played_paths.append(str(mp3_path))
            orig_play_one(mp3_path, timeline, stop_event)

        consumer._play_one = tracking_play_one

        consumer_thread = threading.Thread(
            target=consumer.run, args=(stop,), daemon=True,
        )
        consumer_thread.start()

        for i in range(3):
            worker.on_segment_saved(f"/tmp/song{i}.mp3", {})

        deadline = time.monotonic() + 10
        while consumer.tracks_played < 3 and time.monotonic() < deadline:
            time.sleep(0.05)

        stop.set()
        consumer_thread.join(timeout=3)
        worker.shutdown()

        assert consumer.tracks_played == 3
        assert worker.stats()["processed"] == 3
        assert played_paths == [
            str(Path(f"/tmp/song{i}.mp3")) for i in range(3)
        ]

    @patch("dreamsync.show.runtime.ShowPlaybackRuntime")
    @patch("dreamsync.show.player.AudioPlayer")
    @patch("dreamsync.cache.cached_compile_show")
    @patch("dreamsync.cache.path_based_track_id", return_value="id")
    @patch("dreamsync.analyzer.analyze.analyze_song")
    def test_worker_failure_doesnt_block_consumer(
        self, mock_analyze, mock_track_id, mock_compile,
        MockPlayer, MockRuntime,
    ):
        """Worker fails on segment 2 of 3; consumer still plays 1 and 3."""
        call_count = [0]

        def _analyze_side(*args, **kwargs):
            idx = call_count[0]
            call_count[0] += 1
            if idx == 1:  # fail on second call
                raise RuntimeError("decode failed")
            return _fake_structure()

        mock_analyze.side_effect = _analyze_side
        mock_compile.return_value = (_fake_timeline(), False)
        MockPlayer.return_value = _mock_player()
        MockRuntime.return_value = MagicMock()

        q = queue.Queue()
        stop = threading.Event()
        worker = _make_worker(ready_queue=q, max_workers=1)
        consumer = _make_consumer(ready_queue=q)

        consumer_thread = threading.Thread(
            target=consumer.run, args=(stop,), daemon=True,
        )
        consumer_thread.start()

        for i in range(3):
            worker.on_segment_saved(f"/tmp/song{i}.mp3", {})

        deadline = time.monotonic() + 10
        while consumer.tracks_played < 2 and time.monotonic() < deadline:
            time.sleep(0.05)

        # Give a bit more time to confirm no extra items appear
        time.sleep(0.3)

        stop.set()
        consumer_thread.join(timeout=3)
        worker.shutdown()

        assert consumer.tracks_played == 2
        stats = worker.stats()
        assert stats["errors"] == 1
        assert stats["processed"] == 2

    @patch("dreamsync.show.runtime.ShowPlaybackRuntime")
    @patch("dreamsync.show.player.AudioPlayer")
    @patch("dreamsync.cache.cached_compile_show")
    @patch("dreamsync.cache.path_based_track_id", return_value="id")
    @patch("dreamsync.analyzer.analyze.analyze_song")
    def test_consumer_survives_empty_queue_between_tracks(
        self, mock_analyze, mock_track_id, mock_compile,
        MockPlayer, MockRuntime,
    ):
        """Consumer blocks patiently during gap, then plays segment 2."""
        mock_analyze.return_value = _fake_structure()
        mock_compile.return_value = (_fake_timeline(), False)
        MockPlayer.return_value = _mock_player()
        MockRuntime.return_value = MagicMock()

        q = queue.Queue()
        stop = threading.Event()
        worker = _make_worker(ready_queue=q)
        consumer = _make_consumer(ready_queue=q)

        consumer_thread = threading.Thread(
            target=consumer.run, args=(stop,), daemon=True,
        )
        consumer_thread.start()

        # Submit first segment
        worker.on_segment_saved("/tmp/song0.mp3", {})
        deadline = time.monotonic() + 5
        while consumer.tracks_played < 1 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert consumer.tracks_played == 1

        # Wait 2 seconds with nothing — consumer should stay alive
        time.sleep(2)

        # Submit second segment
        worker.on_segment_saved("/tmp/song1.mp3", {})
        deadline = time.monotonic() + 5
        while consumer.tracks_played < 2 and time.monotonic() < deadline:
            time.sleep(0.05)

        stop.set()
        consumer_thread.join(timeout=3)
        worker.shutdown()

        assert consumer.tracks_played == 2

    @patch("dreamsync.show.runtime.ShowPlaybackRuntime")
    @patch("dreamsync.show.player.AudioPlayer")
    @patch("dreamsync.cache.cached_compile_show")
    @patch("dreamsync.cache.path_based_track_id", return_value="id")
    @patch("dreamsync.analyzer.analyze.analyze_song")
    def test_purge_during_active_pipeline(
        self, mock_analyze, mock_track_id, mock_compile,
        MockPlayer, MockRuntime, tmp_path,
    ):
        """Consumer purges segment 1 files while worker processes segment 2."""
        # Gate so we can control segment 2's processing
        gate = threading.Event()
        call_count = [0]

        def _gated_analyze(*args, **kwargs):
            idx = call_count[0]
            call_count[0] += 1
            if idx == 1:
                gate.wait(timeout=5)
            return _fake_structure()

        mock_analyze.side_effect = _gated_analyze
        mock_compile.return_value = (_fake_timeline(), False)
        MockPlayer.return_value = _mock_player()
        MockRuntime.return_value = MagicMock()

        # Create real temp files for 2 segments
        files = []
        for i in range(2):
            mp3 = tmp_path / f"song{i}.mp3"
            json_f = tmp_path / f"song{i}.json"
            analysis = tmp_path / f"song{i}.analysis.json"
            for f in [mp3, json_f, analysis]:
                f.write_text("test")
            files.append((mp3, json_f, analysis))

        q = queue.Queue()
        stop = threading.Event()
        worker = _make_worker(ready_queue=q, max_workers=1)
        consumer = _make_consumer(ready_queue=q, purge=True)

        consumer_thread = threading.Thread(
            target=consumer.run, args=(stop,), daemon=True,
        )
        consumer_thread.start()

        # Submit both segments — segment 2 will block in analyze
        worker.on_segment_saved(str(files[0][0]), {})
        worker.on_segment_saved(str(files[1][0]), {})

        # Wait for segment 1 to be played & purged
        deadline = time.monotonic() + 5
        while consumer.tracks_played < 1 and time.monotonic() < deadline:
            time.sleep(0.05)

        # Segment 1 files should be purged
        assert not files[0][0].exists()
        assert not files[0][1].exists()
        assert not files[0][2].exists()

        # Release segment 2
        gate.set()

        deadline = time.monotonic() + 5
        while consumer.tracks_played < 2 and time.monotonic() < deadline:
            time.sleep(0.05)

        stop.set()
        consumer_thread.join(timeout=3)
        worker.shutdown()

        assert not files[1][0].exists()
        assert not files[1][1].exists()
        assert not files[1][2].exists()
        assert consumer.tracks_purged == 2

    @patch("dreamsync.show.runtime.ShowPlaybackRuntime")
    @patch("dreamsync.show.player.AudioPlayer")
    @patch("dreamsync.cache.cached_compile_show")
    @patch("dreamsync.cache.path_based_track_id", return_value="id")
    @patch("dreamsync.analyzer.analyze.analyze_song")
    def test_stop_event_drains_gracefully(
        self, mock_analyze, mock_track_id, mock_compile,
        MockPlayer, MockRuntime,
    ):
        """Stop event mid-flight — consumer exits, no deadlock."""
        gate = threading.Event()

        def slow_analyze(*args, **kwargs):
            gate.wait(timeout=10)
            return _fake_structure()

        mock_analyze.side_effect = slow_analyze
        mock_compile.return_value = (_fake_timeline(), False)

        # Player that takes a bit to finish
        player = MagicMock()
        player.finished = False
        player.position_seconds = 0.0
        MockPlayer.return_value = player
        MockRuntime.return_value = MagicMock()

        adapter = MagicMock()
        q = queue.Queue()
        stop = threading.Event()
        worker = _make_worker(ready_queue=q, max_workers=1)
        consumer = _make_consumer(ready_queue=q, adapter=adapter)

        # Put one item directly on queue so consumer starts playing
        q.put((Path("/tmp/playing.mp3"), _fake_timeline()))

        # Also submit one to the worker (will block in slow_analyze)
        worker.on_segment_saved("/tmp/pending.mp3", {})

        consumer_thread = threading.Thread(
            target=consumer.run, args=(stop,), daemon=True,
        )
        consumer_thread.start()

        # Let consumer start its play loop
        time.sleep(0.3)

        # Fire stop
        stop.set()
        gate.set()  # unblock worker too

        consumer_thread.join(timeout=5)
        worker.shutdown()

        assert not consumer_thread.is_alive(), "Consumer thread should have exited"
        adapter.deactivate.assert_called_once()

    @patch("dreamsync.show.runtime.ShowPlaybackRuntime")
    @patch("dreamsync.show.player.AudioPlayer")
    @patch("dreamsync.cache.cached_compile_show")
    @patch("dreamsync.cache.path_based_track_id", return_value="id")
    @patch("dreamsync.analyzer.analyze.analyze_song")
    def test_backpressure_queue_does_not_overflow(
        self, mock_analyze, mock_track_id, mock_compile,
        MockPlayer, MockRuntime,
    ):
        """Worker produces 5 items faster than consumer plays them; nothing lost."""
        mock_analyze.return_value = _fake_structure()
        mock_compile.return_value = (_fake_timeline(), False)
        MockRuntime.return_value = MagicMock()

        # Player that takes a bit (simulates playback time)
        def _slow_player(*args, **kwargs):
            p = MagicMock()
            p.finished = False
            p.position_seconds = 0.0

            def _make_finished():
                time.sleep(0.2)
                p.finished = True

            threading.Thread(target=_make_finished, daemon=True).start()
            return p

        MockPlayer.side_effect = _slow_player

        q = queue.Queue()
        stop = threading.Event()
        worker = _make_worker(ready_queue=q, max_workers=2)
        consumer = _make_consumer(ready_queue=q)

        max_queue_size = [0]
        orig_play_one = consumer._play_one

        def tracking_play_one(mp3_path, timeline, stop_event):
            size = q.qsize()
            if size > max_queue_size[0]:
                max_queue_size[0] = size
            orig_play_one(mp3_path, timeline, stop_event)

        consumer._play_one = tracking_play_one

        consumer_thread = threading.Thread(
            target=consumer.run, args=(stop,), daemon=True,
        )
        consumer_thread.start()

        # Submit 5 segments rapidly
        for i in range(5):
            worker.on_segment_saved(f"/tmp/song{i}.mp3", {})

        # Wait for all to be played
        deadline = time.monotonic() + 15
        while consumer.tracks_played < 5 and time.monotonic() < deadline:
            time.sleep(0.05)

        stop.set()
        consumer_thread.join(timeout=3)
        worker.shutdown()

        assert consumer.tracks_played == 5
        # Backpressure should have happened (queue was non-empty at some point)
        assert max_queue_size[0] > 0, "Expected queue backpressure"
