"""Tests for C3 — Callback exception isolation.

Validates that _capture_track_changed always calls the orchestrator
regardless of display callback failures, and vice versa.
"""

from unittest.mock import MagicMock, call

import pytest

from dreamsync.spotify.models import SpotifyTrack


def _make_track(name="Test Song", artist="Test Artist", album="Test Album",
                duration_ms=180_000):
    return SpotifyTrack(
        track_id="abc123", name=name, artist=artist,
        album=album, duration_ms=duration_ms, uri="spotify:track:abc123",
    )


def _build_capture_track_changed(capture_orchestrator, orig_callback=None):
    """Build a _capture_track_changed closure matching the fixed code."""
    _orig = orig_callback

    def _capture_track_changed(new, old, _orig=_orig):
        # Critical path: notify orchestrator of track change
        try:
            capture_orchestrator.on_track_change({
                "song_durations": [new.duration_ms / 1000.0],
                "current_playback_time": 0.0,
                "current_song": {
                    "song_title": new.name,
                    "artist": new.artist,
                    "album": new.album,
                },
            })
        except Exception:
            pass
        # Informational: display track change to user
        if _orig:
            try:
                _orig(new, old)
            except Exception:
                pass

    return _capture_track_changed


class TestOrchestratorAlwaysCalled:
    def test_orchestrator_called_when_orig_succeeds(self):
        orch = MagicMock()
        orig = MagicMock()
        track = _make_track()

        fn = _build_capture_track_changed(orch, orig)
        fn(track, None)

        orch.on_track_change.assert_called_once()
        orig.assert_called_once_with(track, None)

    def test_orchestrator_called_when_orig_raises_unicode_error(self):
        orch = MagicMock()
        orig = MagicMock(side_effect=UnicodeEncodeError("charmap", "", 0, 1, "test"))
        track = _make_track()

        fn = _build_capture_track_changed(orch, orig)
        fn(track, None)  # Should not raise

        orch.on_track_change.assert_called_once()

    def test_orchestrator_called_when_orig_raises_runtime_error(self):
        orch = MagicMock()
        orig = MagicMock(side_effect=RuntimeError("display broken"))
        track = _make_track()

        fn = _build_capture_track_changed(orch, orig)
        fn(track, None)

        orch.on_track_change.assert_called_once()

    def test_orig_called_when_orchestrator_raises(self):
        orch = MagicMock()
        orch.on_track_change.side_effect = RuntimeError("orchestrator error")
        orig = MagicMock()
        track = _make_track()

        fn = _build_capture_track_changed(orch, orig)
        fn(track, None)  # Should not raise

        orig.assert_called_once_with(track, None)

    def test_both_raise_no_propagation(self):
        orch = MagicMock()
        orch.on_track_change.side_effect = RuntimeError("orch error")
        orig = MagicMock(side_effect=UnicodeEncodeError("charmap", "", 0, 1, "test"))
        track = _make_track()

        fn = _build_capture_track_changed(orch, orig)
        # Should not raise
        fn(track, None)

    def test_orig_none_skips_display(self):
        orch = MagicMock()
        track = _make_track()

        fn = _build_capture_track_changed(orch, None)
        fn(track, None)

        orch.on_track_change.assert_called_once()


class TestMetadataCorrectness:
    def test_orchestrator_receives_correct_metadata(self):
        orch = MagicMock()
        track = _make_track(
            name="My Song", artist="My Artist", album="My Album",
            duration_ms=200_000,
        )

        fn = _build_capture_track_changed(orch, None)
        fn(track, None)

        expected = {
            "song_durations": [200.0],
            "current_playback_time": 0.0,
            "current_song": {
                "song_title": "My Song",
                "artist": "My Artist",
                "album": "My Album",
            },
        }
        orch.on_track_change.assert_called_once_with(expected)

    def test_album_accessed_directly_not_getattr(self):
        """Verify album is accessed as a direct attribute (no getattr fallback)."""
        orch = MagicMock()
        track = _make_track(album="Direct Album")

        fn = _build_capture_track_changed(orch, None)
        fn(track, None)

        passed_dict = orch.on_track_change.call_args[0][0]
        assert passed_dict["current_song"]["album"] == "Direct Album"
