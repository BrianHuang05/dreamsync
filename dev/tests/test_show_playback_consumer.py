"""Tests for ShowPlaybackConsumer (dedicated playback thread)."""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dreamsync.output.null_adapter import NullMultiAdapter
from dreamsync.show_playback_consumer import ShowPlaybackConsumer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_timeline(duration: float = 1.0):
    t = MagicMock()
    t.duration = duration
    t.cues = [MagicMock()] * 3
    return t


def _make_consumer(ready_queue=None, adapter=None, purge=False, debug=False):
    return ShowPlaybackConsumer(
        ready_queue=ready_queue or queue.Queue(),
        multi_adapter=adapter or NullMultiAdapter(),
        sample_rate=44100,
        audio_device=None,
        purge=purge,
        debug=debug,
    )


def _mock_player():
    """Create a mock AudioPlayer that 'finishes' immediately."""
    player = MagicMock()
    player.finished = True
    player.position_seconds = 0.0
    return player


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestShowPlaybackConsumer:

    @patch("dreamsync.show.runtime.ShowPlaybackRuntime")
    @patch("dreamsync.show.player.AudioPlayer")
    def test_plays_track_from_queue(self, MockPlayer, MockRuntime):
        MockPlayer.return_value = _mock_player()
        MockRuntime.return_value = MagicMock()

        q = queue.Queue()
        stop = threading.Event()
        consumer = _make_consumer(ready_queue=q)

        timeline = _fake_timeline()
        q.put((Path("/tmp/song.mp3"), timeline))

        # Stop after processing one item
        def _stop_after_delay():
            time.sleep(0.5)
            stop.set()

        threading.Thread(target=_stop_after_delay, daemon=True).start()
        consumer.run(stop)

        assert consumer.tracks_played == 1
        MockPlayer.assert_called_once()

    @patch("dreamsync.show.runtime.ShowPlaybackRuntime")
    @patch("dreamsync.show.player.AudioPlayer")
    def test_blocks_until_item_available(self, MockPlayer, MockRuntime):
        MockPlayer.return_value = _mock_player()
        MockRuntime.return_value = MagicMock()

        q = queue.Queue()
        stop = threading.Event()
        consumer = _make_consumer(ready_queue=q)

        result = [None]

        def run_consumer():
            result[0] = consumer.run(stop)

        t = threading.Thread(target=run_consumer, daemon=True)
        t.start()

        # Consumer should be blocked waiting
        time.sleep(0.3)
        assert consumer.tracks_played == 0

        # Now enqueue and let it process
        q.put((Path("/tmp/song.mp3"), _fake_timeline()))
        time.sleep(0.5)
        stop.set()
        t.join(timeout=3)

        assert consumer.tracks_played == 1

    @patch("dreamsync.show.runtime.ShowPlaybackRuntime")
    @patch("dreamsync.show.player.AudioPlayer")
    def test_stop_event_exits_loop(self, MockPlayer, MockRuntime):
        q = queue.Queue()
        stop = threading.Event()
        consumer = _make_consumer(ready_queue=q)

        # Set stop immediately
        stop.set()
        summary = consumer.run(stop)

        assert summary["tracks_played"] == 0

    @patch("dreamsync.show.runtime.ShowPlaybackRuntime")
    @patch("dreamsync.show.player.AudioPlayer")
    def test_multiple_tracks_played_in_order(self, MockPlayer, MockRuntime):
        MockPlayer.return_value = _mock_player()
        MockRuntime.return_value = MagicMock()

        q = queue.Queue()
        stop = threading.Event()
        consumer = _make_consumer(ready_queue=q)

        played_paths = []
        orig_play_one = consumer._play_one

        def tracking_play_one(mp3_path, timeline, stop_event):
            played_paths.append(str(mp3_path))
            orig_play_one(mp3_path, timeline, stop_event)

        consumer._play_one = tracking_play_one

        for i in range(3):
            q.put((Path(f"/tmp/song{i}.mp3"), _fake_timeline()))

        def _stop_after_delay():
            time.sleep(1)
            stop.set()

        threading.Thread(target=_stop_after_delay, daemon=True).start()
        consumer.run(stop)

        assert consumer.tracks_played == 3
        assert played_paths == [
            str(Path("/tmp/song0.mp3")),
            str(Path("/tmp/song1.mp3")),
            str(Path("/tmp/song2.mp3")),
        ]

    @patch("dreamsync.show.runtime.ShowPlaybackRuntime")
    @patch("dreamsync.show.player.AudioPlayer")
    def test_purge_deletes_files(self, MockPlayer, MockRuntime, tmp_path):
        MockPlayer.return_value = _mock_player()
        MockRuntime.return_value = MagicMock()

        mp3 = tmp_path / "song.mp3"
        json_f = tmp_path / "song.json"
        analysis = tmp_path / "song.analysis.json"
        for f in [mp3, json_f, analysis]:
            f.write_text("test")

        q = queue.Queue()
        stop = threading.Event()
        consumer = _make_consumer(ready_queue=q, purge=True)

        q.put((mp3, _fake_timeline()))

        def _stop_after_delay():
            time.sleep(0.5)
            stop.set()

        threading.Thread(target=_stop_after_delay, daemon=True).start()
        consumer.run(stop)

        assert not mp3.exists()
        assert not json_f.exists()
        assert not analysis.exists()
        assert consumer.tracks_purged == 1

    @patch("dreamsync.show.runtime.ShowPlaybackRuntime")
    @patch("dreamsync.show.player.AudioPlayer")
    def test_purge_false_retains_files(self, MockPlayer, MockRuntime, tmp_path):
        MockPlayer.return_value = _mock_player()
        MockRuntime.return_value = MagicMock()

        mp3 = tmp_path / "song.mp3"
        mp3.write_text("test")

        q = queue.Queue()
        stop = threading.Event()
        consumer = _make_consumer(ready_queue=q, purge=False)

        q.put((mp3, _fake_timeline()))

        def _stop_after_delay():
            time.sleep(0.5)
            stop.set()

        threading.Thread(target=_stop_after_delay, daemon=True).start()
        consumer.run(stop)

        assert mp3.exists()
        assert consumer.tracks_purged == 0

    @patch("dreamsync.show.runtime.ShowPlaybackRuntime")
    @patch("dreamsync.show.player.AudioPlayer")
    def test_purge_missing_files_no_error(self, MockPlayer, MockRuntime):
        MockPlayer.return_value = _mock_player()
        MockRuntime.return_value = MagicMock()

        q = queue.Queue()
        stop = threading.Event()
        consumer = _make_consumer(ready_queue=q, purge=True)

        # Path that doesn't exist
        q.put((Path("/tmp/nonexistent_xyz.mp3"), _fake_timeline()))

        def _stop_after_delay():
            time.sleep(0.5)
            stop.set()

        threading.Thread(target=_stop_after_delay, daemon=True).start()
        consumer.run(stop)

        assert consumer.tracks_purged == 1

    @patch("dreamsync.show.runtime.ShowPlaybackRuntime")
    @patch("dreamsync.show.player.AudioPlayer")
    def test_summary_counts(self, MockPlayer, MockRuntime, tmp_path):
        MockPlayer.return_value = _mock_player()
        MockRuntime.return_value = MagicMock()

        q = queue.Queue()
        stop = threading.Event()
        consumer = _make_consumer(ready_queue=q, purge=True)

        for i in range(3):
            mp3 = tmp_path / f"song{i}.mp3"
            mp3.write_text("test")
            q.put((mp3, _fake_timeline()))

        def _stop_after_delay():
            time.sleep(1)
            stop.set()

        threading.Thread(target=_stop_after_delay, daemon=True).start()
        summary = consumer.run(stop)

        assert summary["tracks_played"] == 3
        assert summary["tracks_purged"] == 3

    @patch("dreamsync.show.runtime.ShowPlaybackRuntime")
    @patch("dreamsync.show.player.AudioPlayer")
    def test_activates_and_deactivates_adapter(self, MockPlayer, MockRuntime):
        MockPlayer.return_value = _mock_player()
        MockRuntime.return_value = MagicMock()

        adapter = MagicMock()
        q = queue.Queue()
        stop = threading.Event()
        consumer = _make_consumer(ready_queue=q, adapter=adapter)

        q.put((Path("/tmp/song.mp3"), _fake_timeline()))

        def _stop_after_delay():
            time.sleep(0.5)
            stop.set()

        threading.Thread(target=_stop_after_delay, daemon=True).start()
        consumer.run(stop)

        adapter.activate.assert_called_once_with(brightness=100)
        adapter.deactivate.assert_called_once()

    @patch("dreamsync.show.runtime.ShowPlaybackRuntime")
    @patch("dreamsync.show.player.AudioPlayer")
    def test_works_with_null_adapter(self, MockPlayer, MockRuntime):
        MockPlayer.return_value = _mock_player()
        MockRuntime.return_value = MagicMock()

        adapter = NullMultiAdapter()
        q = queue.Queue()
        stop = threading.Event()
        consumer = _make_consumer(ready_queue=q, adapter=adapter)

        q.put((Path("/tmp/song.mp3"), _fake_timeline()))

        def _stop_after_delay():
            time.sleep(0.5)
            stop.set()

        threading.Thread(target=_stop_after_delay, daemon=True).start()
        summary = consumer.run(stop)

        assert summary["tracks_played"] == 1
