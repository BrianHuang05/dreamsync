"""Tests for dreamsync.capture.timing_integrator — external timing integration."""

import threading
import time

import pytest

from dreamsync.capture.boundary_queue import BoundaryQueue
from dreamsync.capture.timing_integrator import TimingIntegrator


@pytest.fixture
def queue():
    return BoundaryQueue(safety_margin_frames=24000)


@pytest.fixture
def integrator(queue):
    return TimingIntegrator(
        boundary_queue=queue,
        get_current_frame=lambda: 0,
        sample_rate=48000,
        refresh_interval=0.1,
    )


class TestUpdate:
    def test_basic_update(self, queue, integrator):
        timing = {
            "current_playback_time": 0.0,
            "song_durations": [10.0, 20.0],
        }
        count = integrator.update(timing)
        assert count == 2
        assert len(queue) > 0

    def test_boundaries_are_absolute(self, queue):
        current_frame = 100000
        integ = TimingIntegrator(
            boundary_queue=queue,
            get_current_frame=lambda: current_frame,
            sample_rate=48000,
        )
        timing = {
            "current_playback_time": 0.0,
            "song_durations": [10.0],
        }
        integ.update(timing)
        entry = queue.peek_next()
        assert entry is not None
        # 10s * 48000 = 480000, plus current_frame offset
        assert entry.frame_position == current_frame + 480000

    def test_empty_durations_no_update(self, integrator):
        count = integrator.update({"song_durations": []})
        assert count == 0

    def test_missing_durations_no_update(self, integrator):
        count = integrator.update({})
        assert count == 0


class TestOnTrackChange:
    def test_immediate_refresh(self, queue, integrator):
        timing = {
            "current_playback_time": 0.0,
            "song_durations": [5.0, 15.0],
            "track_changed": True,
        }
        count = integrator.on_track_change(timing)
        assert count == 2


class TestMultipleUpdates:
    def test_replace_future_on_update(self, queue):
        current = [0]
        integ = TimingIntegrator(
            boundary_queue=queue,
            get_current_frame=lambda: current[0],
            sample_rate=48000,
        )

        # First update
        integ.update({"current_playback_time": 0.0, "song_durations": [10.0, 20.0]})
        first_entries = queue.entries()

        # Advance frame position and update again
        current[0] = 240000  # 5 seconds
        integ.update({"current_playback_time": 5.0, "song_durations": [10.0, 30.0]})
        second_entries = queue.entries()

        # Should have updated boundaries
        assert len(second_entries) >= 1


class TestPerSongMetadata:
    """Boundaries get per-song metadata from the songs list."""

    def test_boundaries_get_per_song_metadata(self, queue):
        integ = TimingIntegrator(
            boundary_queue=queue,
            get_current_frame=lambda: 0,
            sample_rate=48000,
        )
        timing = {
            "current_playback_time": 0.0,
            "song_durations": [10.0, 20.0],
            "current_song": {"song_title": "Fallback"},
            "songs": [
                {"song_title": "Song1", "artist": "A1"},
                {"song_title": "Song2", "artist": "A2"},
            ],
        }
        integ.update(timing)
        entries = queue.entries()
        assert len(entries) == 2
        assert entries[0].metadata == {"song_title": "Song1", "artist": "A1"}
        assert entries[1].metadata == {"song_title": "Song2", "artist": "A2"}

    def test_fallback_when_songs_absent(self, queue):
        integ = TimingIntegrator(
            boundary_queue=queue,
            get_current_frame=lambda: 0,
            sample_rate=48000,
        )
        timing = {
            "current_playback_time": 0.0,
            "song_durations": [10.0],
            "current_song": {"song_title": "Fallback"},
        }
        integ.update(timing)
        entries = queue.entries()
        assert entries[0].metadata == {"song_title": "Fallback"}

    def test_partial_songs_list(self, queue):
        integ = TimingIntegrator(
            boundary_queue=queue,
            get_current_frame=lambda: 0,
            sample_rate=48000,
        )
        timing = {
            "current_playback_time": 0.0,
            "song_durations": [10.0, 20.0, 30.0],
            "current_song": {"song_title": "Fallback"},
            "songs": [
                {"song_title": "Song1"},
            ],
        }
        integ.update(timing)
        entries = queue.entries()
        assert len(entries) == 3
        assert entries[0].metadata == {"song_title": "Song1"}
        # Remaining fall back to current_song
        assert entries[1].metadata == {"song_title": "Fallback"}
        assert entries[2].metadata == {"song_title": "Fallback"}


class TestPeriodicRefresh:
    def test_start_stop(self, queue):
        calls = []

        def fetch():
            calls.append(1)
            return {"current_playback_time": 0.0, "song_durations": [60.0]}

        integ = TimingIntegrator(
            boundary_queue=queue,
            get_current_frame=lambda: 0,
            refresh_interval=0.05,
        )
        integ.start_periodic_refresh(fetch)
        time.sleep(0.2)
        integ.stop()

        assert len(calls) >= 2  # Should have ticked at least twice

    def test_fetch_failure_doesnt_crash(self, queue):
        def bad_fetch():
            raise ConnectionError("no connection")

        integ = TimingIntegrator(
            boundary_queue=queue,
            get_current_frame=lambda: 0,
            refresh_interval=0.05,
        )
        integ.start_periodic_refresh(bad_fetch)
        time.sleep(0.15)
        integ.stop()
        # Should not raise — errors are logged and swallowed

    def test_fetch_returns_none(self, queue):
        integ = TimingIntegrator(
            boundary_queue=queue,
            get_current_frame=lambda: 0,
            refresh_interval=0.05,
        )
        integ.start_periodic_refresh(lambda: None)
        time.sleep(0.15)
        integ.stop()
        assert len(queue) == 0
