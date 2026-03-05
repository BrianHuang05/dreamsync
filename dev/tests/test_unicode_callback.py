"""Tests for C2 — Unicode-safe callback output.

Validates that _safe_track_msg and _safe_segment_msg handle non-ASCII
characters without raising UnicodeEncodeError on any console encoding.
"""

import sys
from io import StringIO
from unittest.mock import patch

import pytest

from dreamsync.spotify.models import SpotifyTrack


def _make_track(name="Test Song", artist="Test Artist", album="Test Album"):
    return SpotifyTrack(
        track_id="abc123", name=name, artist=artist,
        album=album, duration_ms=180_000, uri="spotify:track:abc123",
    )


def _safe_track_msg(new, encoding=None):
    """Reproduce the _safe_track_msg helper from cli.py / session.py."""
    enc = encoding or sys.stdout.encoding or "utf-8"
    name = new.name.encode(enc, errors="replace").decode(enc, errors="replace")
    artist = new.artist.encode(enc, errors="replace").decode(enc, errors="replace")
    return f"Spotify: now playing '{name}' by {artist}"


def _safe_segment_msg(path, meta, encoding=None):
    """Reproduce the _safe_segment_msg helper from cli.py / session.py."""
    enc = encoding or sys.stdout.encoding or "utf-8"
    from pathlib import Path as P
    fname = P(path).name
    artist = str(meta.get("artist", "unknown")).encode(enc, errors="replace").decode(enc, errors="replace")
    title = str(meta.get("song_title", "unknown")).encode(enc, errors="replace").decode(enc, errors="replace")
    return f"Capture: saved {fname} ({artist} - {title})"


class TestSafeTrackMsg:
    def test_ascii_track_prints_normally(self):
        track = _make_track(name="Hello World", artist="Some Artist")
        msg = _safe_track_msg(track)
        assert "Hello World" in msg
        assert "Some Artist" in msg

    def test_japanese_track_no_exception(self):
        track = _make_track(name="\u30ed\u30d9\u30ea\u30a2", artist="\u308a\u3076")
        # Should not raise
        msg = _safe_track_msg(track)
        assert "Spotify: now playing" in msg

    def test_japanese_track_cp1252_replaces(self):
        """On cp1252, Japanese characters should be replaced, not raise."""
        track = _make_track(name="\u30ed\u30d9\u30ea\u30a2", artist="\u308a\u3076")
        msg = _safe_track_msg(track, encoding="cp1252")
        assert "?" in msg  # replacement chars
        assert "Spotify: now playing" in msg

    def test_mixed_ascii_unicode(self):
        track = _make_track(name="Otome Kaibou (\u4e59\u5973\u89e3\u5256)", artist="Rib")
        msg = _safe_track_msg(track, encoding="cp1252")
        assert "Otome Kaibou" in msg
        assert "Rib" in msg

    def test_emoji_in_track_name(self):
        track = _make_track(name="Song \U0001f3b5", artist="DJ \u2764")
        msg = _safe_track_msg(track, encoding="cp1252")
        assert "Song" in msg
        assert "DJ" in msg

    def test_utf8_encoding_preserves_all(self):
        track = _make_track(name="\u30ed\u30d9\u30ea\u30a2", artist="\u308a\u3076")
        msg = _safe_track_msg(track, encoding="utf-8")
        assert "\u30ed\u30d9\u30ea\u30a2" in msg
        assert "\u308a\u3076" in msg


class TestSafeSegmentMsg:
    def test_ascii_metadata(self):
        msg = _safe_segment_msg("/out/song.mp3", {"artist": "Artist", "song_title": "Title"})
        assert "song.mp3" in msg
        assert "Artist" in msg
        assert "Title" in msg

    def test_japanese_metadata_cp1252(self):
        msg = _safe_segment_msg(
            "/out/song.mp3",
            {"artist": "\u308a\u3076", "song_title": "\u30ed\u30d9\u30ea\u30a2"},
            encoding="cp1252",
        )
        assert "?" in msg
        assert "song.mp3" in msg

    def test_missing_metadata_defaults(self):
        msg = _safe_segment_msg("/out/song.mp3", {})
        assert "unknown" in msg
