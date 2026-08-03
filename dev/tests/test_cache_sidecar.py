"""Tests for sidecar_track_id and track_id_for_capture — cache key functions."""

from pathlib import Path

import pytest

from dreamsync.cache import sidecar_track_id, spotify_track_cache_id, track_id_for_capture
from dreamsync.capture.scanner import CaptureTrack


class TestSidecarTrackId:

    def test_deterministic(self):
        id1 = sidecar_track_id("Hello", "Adele")
        id2 = sidecar_track_id("Hello", "Adele")
        assert id1 == id2
        assert id1.startswith("sidecar_")

    def test_case_insensitive(self):
        id1 = sidecar_track_id("Hello", "Adele")
        id2 = sidecar_track_id("hello", "adele")
        id3 = sidecar_track_id("HELLO", "ADELE")
        assert id1 == id2 == id3

    def test_strips_whitespace(self):
        id1 = sidecar_track_id("  Hello  ", "  Adele  ")
        id2 = sidecar_track_id("Hello", "Adele")
        assert id1 == id2


class TestTrackIdForCapture:
    def test_spotify_identity_takes_precedence(self):
        track = type("Track", (), {
            "spotify_track_id": "edition-a", "song_title": "Hello",
            "artist": "Adele", "mp3_path": "/tmp/hello.mp3",
        })()
        assert track_id_for_capture(track) == spotify_track_cache_id("edition-a")

    def test_identical_metadata_with_distinct_spotify_ids_do_not_collide(self):
        assert spotify_track_cache_id("edition-a") != spotify_track_cache_id("edition-b")

    def test_with_metadata(self, tmp_path):
        mp3 = tmp_path / "song.mp3"
        mp3.write_bytes(b"\x00")
        track = CaptureTrack(
            mp3_path=mp3,
            sidecar_path=None,
            segment_index=0,
            song_title="Hello",
            artist="Adele",
            album="25",
            duration_seconds=295.0,
            metadata={},
        )
        tid = track_id_for_capture(track)
        assert tid.startswith("sidecar_")
        assert tid == sidecar_track_id("Hello", "Adele")

    def test_without_metadata(self, tmp_path):
        mp3 = tmp_path / "song.mp3"
        mp3.write_bytes(b"\x00")
        track = CaptureTrack(
            mp3_path=mp3,
            sidecar_path=None,
            segment_index=0,
            song_title=None,
            artist=None,
            album=None,
            duration_seconds=0.0,
            metadata={},
        )
        tid = track_id_for_capture(track)
        assert not tid.startswith("sidecar_")
        assert "song" in tid.lower()
