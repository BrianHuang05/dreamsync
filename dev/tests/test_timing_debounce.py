"""Tests for TimingIntegrator tick debounce after track change."""

import time
from unittest.mock import patch

import pytest

from dreamsync.capture.boundary_queue import BoundaryQueue
from dreamsync.capture.timing_integrator import TimingIntegrator


@pytest.fixture
def queue():
    return BoundaryQueue(safety_margin_frames=22050)


def _make_integrator(queue, current_frame=0, logger=None):
    return TimingIntegrator(
        boundary_queue=queue,
        get_current_frame=lambda: current_frame,
        sample_rate=44100,
        refresh_interval=0.1,
        logger=logger,
    )


class TestTickDebounce:
    """_tick() should be suppressed shortly after on_track_change()."""

    def test_tick_suppressed_after_track_change(self, queue):
        """_tick() immediately after on_track_change() should be a no-op."""
        integ = _make_integrator(queue)
        fetch_calls = []

        def fetch():
            fetch_calls.append(1)
            return {"current_playback_time": 0.0, "song_durations": [60.0]}

        integ._fetch_fn = fetch
        integ._running = True

        # Simulate a track change
        integ.on_track_change({
            "current_playback_time": 0.0,
            "song_durations": [120.0],
        })

        entries_before = len(queue.entries())

        # Call _tick() directly — should be debounced
        integ._tick()

        # fetch_fn should NOT have been called
        assert len(fetch_calls) == 0
        # Queue should not have additional entries from the tick
        assert len(queue.entries()) == entries_before

    def test_tick_runs_after_debounce_expires(self, queue):
        """_tick() should work normally after the debounce window expires."""
        integ = _make_integrator(queue)
        fetch_calls = []

        def fetch():
            fetch_calls.append(1)
            return {"current_playback_time": 0.0, "song_durations": [60.0]}

        integ._fetch_fn = fetch
        integ._running = True

        # Simulate track change in the past (debounce expired)
        with patch("dreamsync.capture.timing_integrator.time") as mock_time:
            # on_track_change records time at 100.0
            mock_time.monotonic.return_value = 100.0
            integ.on_track_change({
                "current_playback_time": 0.0,
                "song_durations": [120.0],
            })

            # _tick() fires at 104.0 (> 3s debounce)
            mock_time.monotonic.return_value = 104.0
            integ._tick()

        assert len(fetch_calls) == 1

    def test_tick_works_without_prior_track_change(self, queue):
        """_tick() should process normally if no track change has ever fired."""
        integ = _make_integrator(queue)
        fetch_calls = []

        def fetch():
            fetch_calls.append(1)
            return {"current_playback_time": 0.0, "song_durations": [60.0]}

        integ._fetch_fn = fetch
        integ._running = True

        # No track change ever happened (_track_change_time = 0.0)
        # time.monotonic() will be >> 3s, so debounce won't trigger
        integ._tick()
        assert len(fetch_calls) == 1

    def test_debounce_logs_event(self, queue):
        """Debounced tick should log a timing event."""

        class FakeLogger:
            def __init__(self):
                self.events = []

            def timing_event(self, event_name, **kwargs):
                self.events.append(event_name)

        fake_logger = FakeLogger()
        integ = _make_integrator(queue, logger=fake_logger)
        integ._fetch_fn = lambda: {"current_playback_time": 0.0, "song_durations": [60.0]}
        integ._running = True

        integ.on_track_change({
            "current_playback_time": 0.0,
            "song_durations": [120.0],
        })
        integ._tick()

        assert "tick.debounced" in fake_logger.events

    def test_multiple_track_changes_reset_debounce(self, queue):
        """Each on_track_change() should reset the debounce window."""
        integ = _make_integrator(queue)
        fetch_calls = []

        def fetch():
            fetch_calls.append(1)
            return {"current_playback_time": 0.0, "song_durations": [60.0]}

        integ._fetch_fn = fetch
        integ._running = True

        with patch("dreamsync.capture.timing_integrator.time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            integ.on_track_change({
                "current_playback_time": 0.0,
                "song_durations": [120.0],
            })

            # At 102s, another track change resets the debounce
            mock_time.monotonic.return_value = 102.0
            integ.on_track_change({
                "current_playback_time": 0.0,
                "song_durations": [90.0],
            })

            # At 104s (only 2s since last track change), tick should still be debounced
            mock_time.monotonic.return_value = 104.0
            integ._tick()

            assert len(fetch_calls) == 0

            # At 106s (4s since last track change), tick should work
            mock_time.monotonic.return_value = 106.0
            integ._tick()

            assert len(fetch_calls) == 1
