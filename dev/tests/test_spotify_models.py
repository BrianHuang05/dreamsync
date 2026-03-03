"""Tests for dreamsync.spotify.models — frozen dataclasses and parse_track."""

import pytest

from dreamsync.spotify.models import PlaybackState, QueueSnapshot, SpotifyTrack, parse_track


# ---------------------------------------------------------------------------
# Sample API JSON blobs
# ---------------------------------------------------------------------------

SAMPLE_TRACK_JSON = {
    "id": "6rqhFgbbKwnb9MLmUQDhG6",
    "name": "Bohemian Rhapsody",
    "artists": [{"name": "Queen", "id": "1dfeR4HaWDbWqFHLkxsg1d"}],
    "album": {"name": "A Night at the Opera", "id": "abc123"},
    "duration_ms": 354947,
    "uri": "spotify:track:6rqhFgbbKwnb9MLmUQDhG6",
}


class TestParseTrack:
    def test_parse_track_from_api_json(self):
        """Full API JSON blob parses into correct SpotifyTrack fields."""
        track = parse_track(SAMPLE_TRACK_JSON)
        assert track.track_id == "6rqhFgbbKwnb9MLmUQDhG6"
        assert track.name == "Bohemian Rhapsody"
        assert track.artist == "Queen"
        assert track.album == "A Night at the Opera"
        assert track.duration_ms == 354947
        assert track.uri == "spotify:track:6rqhFgbbKwnb9MLmUQDhG6"

    def test_parse_track_missing_fields(self):
        """Incomplete JSON uses graceful defaults."""
        track = parse_track({"id": "abc"})
        assert track.track_id == "abc"
        assert track.name == ""
        assert track.artist == ""
        assert track.album == ""
        assert track.duration_ms == 0
        assert track.uri == ""

    def test_parse_track_null_artists(self):
        """Null artists list produces empty artist string."""
        track = parse_track({"id": "x", "artists": None})
        assert track.artist == ""

    def test_parse_track_empty_artists(self):
        """Empty artists list produces empty artist string."""
        track = parse_track({"id": "x", "artists": []})
        assert track.artist == ""


class TestPlaybackState:
    def test_playback_state_immutable(self):
        """PlaybackState is frozen — attribute assignment raises."""
        state = PlaybackState(
            is_playing=True, track=None, progress_ms=0,
            timestamp=0.0, device_name="test", shuffle=False, repeat="off",
        )
        with pytest.raises(AttributeError):
            state.is_playing = False  # type: ignore[misc]


class TestQueueSnapshot:
    def test_queue_snapshot_tuple(self):
        """QueueSnapshot.queue is a tuple, not a mutable list."""
        snap = QueueSnapshot(currently_playing=None, queue=(), fetched_at=0.0)
        assert isinstance(snap.queue, tuple)

    def test_queue_snapshot_with_tracks(self):
        """QueueSnapshot holds multiple tracks."""
        t1 = parse_track({"id": "a", "name": "Song A", "artists": [{"name": "X"}]})
        t2 = parse_track({"id": "b", "name": "Song B", "artists": [{"name": "Y"}]})
        snap = QueueSnapshot(currently_playing=t1, queue=(t1, t2), fetched_at=1.0)
        assert len(snap.queue) == 2
        assert snap.currently_playing.name == "Song A"
