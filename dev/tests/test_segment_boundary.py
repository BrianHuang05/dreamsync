"""Tests for dreamsync.capture.segment_boundary — boundary computation."""

import pytest

from dreamsync.capture.segment_boundary import compute_boundaries


class TestComputeBoundaries:
    def test_plan_example(self):
        """The exact example from the plan document."""
        result = compute_boundaries([30, 210, 180], 15.0, 48000)
        assert result == [720000, 10800000, 19440000]

    def test_empty_durations(self):
        assert compute_boundaries([], 0.0) == []

    def test_single_song(self):
        result = compute_boundaries([60.0], 10.0, 48000)
        # remaining = 50s -> 2400000 frames
        assert result == [2400000]

    def test_playback_past_first_song(self):
        """currentPlaybackTime >= d0 means r0 = 0 -> immediate split."""
        result = compute_boundaries([30, 120], 30.0, 48000)
        # r0 = 0, second = 120*48000 = 5760000
        assert result == [0, 5760000]

    def test_playback_beyond_first_song(self):
        result = compute_boundaries([30, 120], 45.0, 48000)
        assert result == [0, 5760000]

    def test_many_songs(self):
        durations = [10.0] * 10
        result = compute_boundaries(durations, 0.0, 48000)
        assert len(result) == 10
        assert result[-1] == 10 * 10 * 48000  # 4800000

    def test_all_boundaries_are_integers(self):
        result = compute_boundaries([3.7, 5.3, 2.1], 1.2, 48000)
        assert all(isinstance(b, int) for b in result)

    def test_cumulative_property(self):
        """Each boundary should be >= the previous one."""
        result = compute_boundaries([100, 200, 300], 50.0, 48000)
        for i in range(1, len(result)):
            assert result[i] >= result[i - 1]

    def test_zero_duration_song(self):
        result = compute_boundaries([0, 60], 0.0, 48000)
        assert result[0] == 0
        assert result[1] == 60 * 48000

    def test_custom_sample_rate(self):
        result = compute_boundaries([10.0], 0.0, 44100)
        assert result == [441000]

    def test_very_short_durations(self):
        """Durations shorter than 1 frame worth of time."""
        result = compute_boundaries([0.00001], 0.0, 48000)
        # round(0.00001 * 48000) = round(0.48) = 0
        assert result == [0]
