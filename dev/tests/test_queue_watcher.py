"""Tests for dreamsync.spotify.queue_watcher — mock client, event dispatch."""

import threading
import time
from unittest.mock import MagicMock

import pytest

from dreamsync.spotify.client import SpotifyAuthError
from dreamsync.spotify.models import PlaybackState, QueueSnapshot, SpotifyTrack
from dreamsync.spotify.queue_watcher import SpotifyQueueWatcher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _track(track_id: str, name: str = "Song") -> SpotifyTrack:
    return SpotifyTrack(
        track_id=track_id, name=name, artist="Artist",
        album="Album", duration_ms=200000, uri=f"spotify:track:{track_id}",
    )


def _playback(track: SpotifyTrack | None, playing: bool = True) -> PlaybackState:
    return PlaybackState(
        is_playing=playing, track=track, progress_ms=0,
        timestamp=time.monotonic(), device_name="test", shuffle=False, repeat="off",
    )


def _queue_snap(current: SpotifyTrack | None = None) -> QueueSnapshot:
    return QueueSnapshot(currently_playing=current, queue=(), fetched_at=time.monotonic())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTrackChangeDetection:
    def test_detects_track_change(self):
        """Track A → Track B fires on_track_changed(B, A)."""
        track_a = _track("a", "Song A")
        track_b = _track("b", "Song B")

        client = MagicMock()
        client.get_playback_state = MagicMock(
            side_effect=[_playback(track_a), _playback(track_b)]
        )
        client.get_queue = MagicMock(return_value=_queue_snap())

        changes: list[tuple] = []
        watcher = SpotifyQueueWatcher(
            client, poll_interval=0.05,
            on_track_changed=lambda new, old: changes.append((new, old)),
        )
        watcher.start()
        time.sleep(0.3)
        watcher.stop()

        assert len(changes) >= 1
        # First change: None → track_a, then track_a → track_b
        # Find the A→B transition
        ab_changes = [(n, o) for n, o in changes if n.track_id == "b"]
        assert len(ab_changes) == 1
        assert ab_changes[0][1].track_id == "a"

    def test_no_callback_when_same_track(self):
        """Same track twice → on_track_changed called only once (initial detection)."""
        track_a = _track("a")

        client = MagicMock()
        client.get_playback_state = MagicMock(return_value=_playback(track_a))
        client.get_queue = MagicMock(return_value=_queue_snap())

        changes: list[tuple] = []
        watcher = SpotifyQueueWatcher(
            client, poll_interval=0.05,
            on_track_changed=lambda new, old: changes.append((new, old)),
        )
        watcher.start()
        time.sleep(0.3)
        watcher.stop()

        # Only the initial None→A change
        assert len(changes) == 1


class TestQueuePolling:
    def test_queue_polled_on_track_change(self):
        """get_queue() is called immediately after a track change."""
        track_a = _track("a")
        track_b = _track("b")

        client = MagicMock()
        client.get_playback_state = MagicMock(
            side_effect=[_playback(track_a), _playback(track_b)]
            + [_playback(track_b)] * 20
        )
        client.get_queue = MagicMock(return_value=_queue_snap())

        watcher = SpotifyQueueWatcher(
            client, poll_interval=0.05, queue_poll_interval=100.0,
        )
        watcher.start()
        time.sleep(0.3)
        watcher.stop()

        # Queue should have been polled (at least on startup + track changes)
        assert client.get_queue.call_count >= 1


class TestNothingPlaying:
    def test_handles_nothing_playing(self):
        """None playback state → no crash, no track change callback."""
        client = MagicMock()
        client.get_playback_state = MagicMock(return_value=None)
        client.get_queue = MagicMock(return_value=_queue_snap())

        changes: list[tuple] = []
        watcher = SpotifyQueueWatcher(
            client, poll_interval=0.05,
            on_track_changed=lambda new, old: changes.append((new, old)),
        )
        watcher.start()
        time.sleep(0.2)
        watcher.stop()

        assert len(changes) == 0


class TestErrorBackoff:
    def test_error_backoff(self):
        """Consecutive errors increase the effective poll interval."""
        client = MagicMock()
        client.get_playback_state = MagicMock(side_effect=Exception("Network error"))
        client.get_queue = MagicMock(return_value=_queue_snap())

        watcher = SpotifyQueueWatcher(client, poll_interval=0.05)
        watcher.start()
        time.sleep(0.5)

        # After several errors, consecutive_errors should be > 0
        assert watcher._consecutive_errors >= 3
        watcher.stop()


class TestLifecycle:
    def test_start_stop_idempotent(self):
        """start() twice + stop() twice — no errors, only one thread."""
        client = MagicMock()
        client.get_playback_state = MagicMock(return_value=None)
        client.get_queue = MagicMock(return_value=_queue_snap())

        watcher = SpotifyQueueWatcher(client, poll_interval=0.1)
        watcher.start()
        watcher.start()  # second start is no-op

        # Count daemon threads with our name
        watcher_threads = [
            t for t in threading.enumerate() if t.name == "spotify-queue-watcher"
        ]
        assert len(watcher_threads) == 1

        watcher.stop()
        watcher.stop()  # second stop is no-op

    def test_properties_thread_safe(self):
        """Read properties from main thread while watcher runs."""
        track = _track("a")
        client = MagicMock()
        client.get_playback_state = MagicMock(return_value=_playback(track))
        client.get_queue = MagicMock(return_value=_queue_snap(track))

        watcher = SpotifyQueueWatcher(client, poll_interval=0.05)
        watcher.start()
        time.sleep(0.2)

        # Should be readable without errors
        _ = watcher.current_track
        _ = watcher.playback_state
        _ = watcher.queue
        _ = watcher.auth_failed

        watcher.stop()
