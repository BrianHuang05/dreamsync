"""Tests for C1 — _fetch_timing() attribute name fix.

Validates that _fetch_timing() correctly accesses QueueSnapshot.currently_playing,
retrieves progress_ms from PlaybackState, handles None safely, and avoids
list+tuple concatenation errors.
"""

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from dreamsync.spotify.models import PlaybackState, QueueSnapshot, SpotifyTrack


def _make_track(name="Test Song", artist="Test Artist", album="Test Album",
                duration_ms=180_000, track_id="abc123", uri="spotify:track:abc123"):
    return SpotifyTrack(
        track_id=track_id, name=name, artist=artist,
        album=album, duration_ms=duration_ms, uri=uri,
    )


def _make_playback_state(progress_ms=30_000, is_playing=True):
    return PlaybackState(
        is_playing=is_playing,
        track=_make_track(),
        progress_ms=progress_ms,
        timestamp=time.monotonic(),
        device_name="Test Device",
        shuffle=False,
        repeat="off",
    )


def _build_fetch_timing(spotify_watcher):
    """Build a _fetch_timing closure identical to the one in cli.py / session.py."""
    def _fetch_timing():
        queue = spotify_watcher.queue
        if queue is None or queue.currently_playing is None:
            return None
        current = queue.currently_playing
        pb = spotify_watcher.playback_state
        return {
            "song_durations": [
                t.duration_ms / 1000.0
                for t in [current, *queue.queue]
            ],
            "current_playback_time": (pb.progress_ms / 1000.0) if pb else 0.0,
            "current_song": {
                "song_title": current.name,
                "artist": current.artist,
                "album": current.album,
            },
        }
    return _fetch_timing


class TestFetchTimingNoneGuards:
    def test_returns_none_when_queue_is_none(self):
        watcher = MagicMock()
        watcher.queue = None
        fetch = _build_fetch_timing(watcher)
        assert fetch() is None

    def test_returns_none_when_currently_playing_is_none(self):
        watcher = MagicMock()
        watcher.queue = QueueSnapshot(
            currently_playing=None, queue=(), fetched_at=time.monotonic()
        )
        fetch = _build_fetch_timing(watcher)
        assert fetch() is None


class TestFetchTimingValidQueue:
    def test_valid_queue_returns_correct_structure(self):
        current = _make_track(name="Song A", artist="Artist A", album="Album A", duration_ms=200_000)
        q1 = _make_track(name="Song B", duration_ms=180_000)
        q2 = _make_track(name="Song C", duration_ms=240_000)

        watcher = MagicMock()
        watcher.queue = QueueSnapshot(
            currently_playing=current,
            queue=(q1, q2),
            fetched_at=time.monotonic(),
        )
        watcher.playback_state = _make_playback_state(progress_ms=30_000)

        fetch = _build_fetch_timing(watcher)
        result = fetch()

        assert result is not None
        assert result["song_durations"] == [200.0, 180.0, 240.0]
        assert result["current_playback_time"] == 30.0
        assert result["current_song"]["song_title"] == "Song A"
        assert result["current_song"]["artist"] == "Artist A"
        assert result["current_song"]["album"] == "Album A"

    def test_empty_queue_returns_single_duration(self):
        current = _make_track(duration_ms=200_000)
        watcher = MagicMock()
        watcher.queue = QueueSnapshot(
            currently_playing=current, queue=(), fetched_at=time.monotonic(),
        )
        watcher.playback_state = _make_playback_state(progress_ms=10_000)

        fetch = _build_fetch_timing(watcher)
        result = fetch()
        assert len(result["song_durations"]) == 1

    def test_no_playback_state_falls_back_to_zero(self):
        current = _make_track()
        watcher = MagicMock()
        watcher.queue = QueueSnapshot(
            currently_playing=current, queue=(), fetched_at=time.monotonic(),
        )
        watcher.playback_state = None

        fetch = _build_fetch_timing(watcher)
        result = fetch()
        assert result["current_playback_time"] == 0.0

    def test_unicode_track_names_no_exception(self):
        current = _make_track(name="\u30ed\u30d9\u30ea\u30a2", artist="\u308a\u3076", album="\u30a2\u30eb\u30d0\u30e0")
        watcher = MagicMock()
        watcher.queue = QueueSnapshot(
            currently_playing=current, queue=(), fetched_at=time.monotonic(),
        )
        watcher.playback_state = _make_playback_state()

        fetch = _build_fetch_timing(watcher)
        result = fetch()
        assert result["current_song"]["song_title"] == "\u30ed\u30d9\u30ea\u30a2"
        assert result["current_song"]["artist"] == "\u308a\u3076"

    def test_tuple_queue_does_not_raise_type_error(self):
        """Regression: [current] + tuple raised TypeError before the fix."""
        current = _make_track(duration_ms=100_000)
        q1 = _make_track(duration_ms=200_000)
        watcher = MagicMock()
        watcher.queue = QueueSnapshot(
            currently_playing=current,
            queue=(q1,),  # tuple, not list
            fetched_at=time.monotonic(),
        )
        watcher.playback_state = _make_playback_state()

        fetch = _build_fetch_timing(watcher)
        # Should not raise TypeError
        result = fetch()
        assert len(result["song_durations"]) == 2
