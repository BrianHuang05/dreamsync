"""Tests for CompositeBoundaryDetector (D3.2)."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from dreamsync.capture.boundary import BoundaryEvent, CompositeBoundaryDetector


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FakeTrack:
    name: str = "Test Song"
    artist: str = "Test Artist"


def _make(
    min_song_seconds: float = 0.0,
    debounce_seconds: float = 3.0,
    hop_size: int = 512,
    sample_rate: int = 44100,
    on_boundary=None,
) -> CompositeBoundaryDetector:
    return CompositeBoundaryDetector(
        hop_size=hop_size,
        sample_rate=sample_rate,
        min_song_seconds=min_song_seconds,
        debounce_seconds=debounce_seconds,
        on_boundary=on_boundary,
    )


def _features(rms=0.1, bpm=120.0, centroid=2000.0, bass_ratio=0.3,
              energy=0.5, onset_strength=0.03, beat=False):
    return {
        "rms": rms, "bpm": bpm, "centroid": centroid,
        "bass_ratio": bass_ratio, "energy": energy,
        "onset_strength": onset_strength, "beat": beat,
    }


# ---------------------------------------------------------------------------
# Spotify signal tests
# ---------------------------------------------------------------------------


class TestSpotifySignal:
    def test_spotify_fires_immediately(self):
        det = _make()
        det.notify_track_changed(FakeTrack(), None)
        event = det.update(_features(), t=100.0)
        assert event is not None
        assert event.source == "spotify"
        assert event.track_name == "Test Song"
        assert event.artist == "Test Artist"
        assert event.confidence == 1.0

    def test_spotify_metadata_propagated(self):
        det = _make()
        det.notify_track_changed(FakeTrack(name="My Song", artist="DJ"), None)
        event = det.update(_features(), t=50.0)
        assert event.track_name == "My Song"
        assert event.artist == "DJ"

    def test_spotify_clears_pending_after_fire(self):
        det = _make()
        det.notify_track_changed(FakeTrack(), None)
        det.update(_features(), t=10.0)
        # Second update without new notification should not fire
        event = det.update(_features(), t=11.0)
        assert event is None

    def test_spotify_with_none_old_track(self):
        det = _make()
        det.notify_track_changed(FakeTrack(), None)
        event = det.update(_features(), t=1.0)
        assert event is not None
        assert event.source == "spotify"


# ---------------------------------------------------------------------------
# Silence signal tests
# ---------------------------------------------------------------------------


class TestSilenceSignal:
    def _feed_silence_boundary(self, det: CompositeBoundaryDetector, start_t: float = 0.0):
        """Feed enough frames to trigger a silence-based boundary."""
        spf = det._hop_size / det._sample_rate
        t = start_t
        # Enough silence frames (using defaults: 2s silence / (512/44100) ≈ 172 frames)
        silence_frames = int(2.5 * det._sample_rate / det._hop_size)
        for _ in range(silence_frames):
            det.update(_features(rms=0.0), t=t)
            t += spf
        # Resume with audio (3 confirm frames)
        result = None
        for _ in range(5):
            result = det.update(_features(rms=0.1), t=t)
            t += spf
            if result is not None:
                return result, t
        return result, t

    def test_silence_boundary_fires(self):
        det = _make(min_song_seconds=0.0)
        event, _ = self._feed_silence_boundary(det)
        assert event is not None
        assert event.source == "silence"
        assert event.confidence == 0.8

    def test_silence_no_metadata(self):
        det = _make(min_song_seconds=0.0)
        event, _ = self._feed_silence_boundary(det)
        assert event is not None
        assert event.track_name is None
        assert event.artist is None


# ---------------------------------------------------------------------------
# Crossfade signal tests
# ---------------------------------------------------------------------------


class TestCrossfadeSignal:
    def test_crossfade_event_has_correct_source(self):
        """When crossfade detector fires, event source is 'crossfade'."""
        events: list[BoundaryEvent] = []
        det = _make(min_song_seconds=0.0, on_boundary=events.append)

        # Mock the crossfade detector to fire immediately
        with patch.object(det._crossfade_det, "update", return_value=True):
            det.update(_features(), t=100.0)

        assert len(events) == 1
        assert events[0].source == "crossfade"
        assert events[0].confidence == 0.6


# ---------------------------------------------------------------------------
# Debounce tests
# ---------------------------------------------------------------------------


class TestDebounce:
    def test_audio_suppressed_near_spotify(self):
        """Audio boundary within debounce window of Spotify should be suppressed."""
        det = _make(min_song_seconds=0.0, debounce_seconds=3.0)

        # Fire a Spotify boundary at t=10.0
        det.notify_track_changed(FakeTrack(), None)
        event = det.update(_features(), t=10.0)
        assert event is not None
        assert event.source == "spotify"

        # Silence boundary at t=11.5 (within 3s debounce) should be suppressed
        with patch.object(det._silence_det, "update", return_value=True):
            event = det.update(_features(), t=11.5)
        assert event is None

    def test_audio_fires_outside_debounce(self):
        """Audio boundary outside debounce window should fire."""
        det = _make(min_song_seconds=0.0, debounce_seconds=3.0)

        # Fire Spotify at t=10
        det.notify_track_changed(FakeTrack(), None)
        det.update(_features(), t=10.0)

        # Audio boundary at t=20 (well outside debounce)
        with patch.object(det._silence_det, "update", return_value=True):
            event = det.update(_features(), t=20.0)
        assert event is not None
        assert event.source == "silence"


# ---------------------------------------------------------------------------
# Cooldown tests
# ---------------------------------------------------------------------------


class TestCooldown:
    def test_cooldown_prevents_rapid_fire(self):
        """Boundaries should not fire faster than min_song_seconds."""
        det = _make(min_song_seconds=30.0)

        # Fire a Spotify boundary at t=0
        det.notify_track_changed(FakeTrack(), None)
        event = det.update(_features(), t=0.0)
        assert event is not None

        # Try another at t=10 (within cooldown)
        det.notify_track_changed(FakeTrack(name="Song 2"), None)
        event = det.update(_features(), t=10.0)
        assert event is None

    def test_cooldown_allows_after_interval(self):
        """Boundary should fire after cooldown has elapsed."""
        det = _make(min_song_seconds=5.0)

        det.notify_track_changed(FakeTrack(), None)
        det.update(_features(), t=0.0)

        # After cooldown
        det.notify_track_changed(FakeTrack(name="Song 2"), None)
        event = det.update(_features(), t=10.0)
        assert event is not None
        assert event.track_name == "Song 2"


# ---------------------------------------------------------------------------
# Callback tests
# ---------------------------------------------------------------------------


class TestCallback:
    def test_on_boundary_callback_fires(self):
        events: list[BoundaryEvent] = []
        det = _make(on_boundary=events.append)

        det.notify_track_changed(FakeTrack(), None)
        det.update(_features(), t=5.0)

        assert len(events) == 1
        assert events[0].source == "spotify"

    def test_callback_fires_exactly_once(self):
        events: list[BoundaryEvent] = []
        det = _make(on_boundary=events.append)

        det.notify_track_changed(FakeTrack(), None)
        det.update(_features(), t=5.0)
        det.update(_features(), t=6.0)
        det.update(_features(), t=7.0)

        assert len(events) == 1

    def test_no_callback_when_none(self):
        """Should not raise when on_boundary is None."""
        det = _make(on_boundary=None)
        det.notify_track_changed(FakeTrack(), None)
        det.update(_features(), t=5.0)  # Should not raise


# ---------------------------------------------------------------------------
# Reset tests
# ---------------------------------------------------------------------------


class TestReset:
    def test_reset_clears_count(self):
        det = _make()
        det.notify_track_changed(FakeTrack(), None)
        det.update(_features(), t=5.0)
        assert det.boundary_count == 1
        det.reset()
        assert det.boundary_count == 0

    def test_reset_clears_pending(self):
        det = _make()
        det.notify_track_changed(FakeTrack(), None)
        det.reset()
        event = det.update(_features(), t=5.0)
        assert event is None

    def test_reset_allows_immediate_fire(self):
        det = _make(min_song_seconds=30.0)
        det.notify_track_changed(FakeTrack(), None)
        det.update(_features(), t=0.0)
        det.reset()
        det.notify_track_changed(FakeTrack(name="After Reset"), None)
        event = det.update(_features(), t=1.0)
        assert event is not None
        assert event.track_name == "After Reset"


# ---------------------------------------------------------------------------
# Thread safety tests
# ---------------------------------------------------------------------------


class TestThreadSafety:
    def test_concurrent_notify_and_update(self):
        """notify_track_changed from one thread while update runs in another."""
        errors: list[Exception] = []
        det = _make()

        def notifier():
            try:
                for i in range(100):
                    det.notify_track_changed(FakeTrack(name=f"Song {i}"), None)
            except Exception as e:
                errors.append(e)

        def updater():
            try:
                for i in range(100):
                    det.update(_features(), t=float(i) * 100.0)
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(notifier)
            f2 = pool.submit(updater)
            f1.result()
            f2.result()

        assert len(errors) == 0


# ---------------------------------------------------------------------------
# Boundary count tests
# ---------------------------------------------------------------------------


class TestBoundaryCount:
    def test_count_increments(self):
        det = _make(min_song_seconds=0.0)
        for i in range(3):
            det.notify_track_changed(FakeTrack(name=f"Song {i}"), None)
            det.update(_features(), t=float(i) * 100.0)
        assert det.boundary_count == 3

    def test_count_starts_at_zero(self):
        det = _make()
        assert det.boundary_count == 0
