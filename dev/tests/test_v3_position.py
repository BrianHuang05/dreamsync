"""Tests for PositionInterpolator — D5.1."""

import time

import pytest
from unittest.mock import patch

from dreamsync.v3_session import PositionInterpolator


class TestPositionInterpolator:
    """Position interpolation, drift correction, seek detection, and pause handling."""

    def test_linear_advance(self):
        """Position advances linearly between polls at 1x speed."""
        interp = PositionInterpolator()
        t0 = 1000.0

        # First poll: playing at 10s
        interp.update(10_000, is_playing=True, mono_time=t0)

        # 0.5s later, no new poll → should be ~10.5s
        with patch("time.monotonic", return_value=t0 + 0.5):
            pos = interp.position_seconds
        assert abs(pos - 10.5) < 0.01

        # 2.0s later → should be ~12.0s
        with patch("time.monotonic", return_value=t0 + 2.0):
            pos = interp.position_seconds
        assert abs(pos - 12.0) < 0.01

    def test_small_drift_correction(self):
        """Small drift (<2s) is corrected gradually, not snapped."""
        interp = PositionInterpolator()
        t0 = 1000.0

        # First poll: playing at 10.0s
        interp.update(10_000, is_playing=True, mono_time=t0)

        # 2s later, Spotify says 12.4s (we'd interpolate 12.0s → 0.4s drift)
        interp.update(12_400, is_playing=True, mono_time=t0 + 2.0)

        # Immediately after this update, position should be ~12.4s (anchor)
        with patch("time.monotonic", return_value=t0 + 2.0):
            pos = interp.position_seconds
        assert abs(pos - 12.4) < 0.01

        # 1s later, correction_rate should add a small positive offset
        with patch("time.monotonic", return_value=t0 + 3.0):
            pos = interp.position_seconds
        # Should be 12.4 + 1.0 + (correction_rate * 1.0)
        # correction_rate = min(0.5, 0.4/2) = 0.2
        assert abs(pos - 13.6) < 0.05

    def test_seek_snaps_immediately(self):
        """Drift > 2s (seek) snaps position immediately."""
        interp = PositionInterpolator()
        t0 = 1000.0

        interp.update(10_000, is_playing=True, mono_time=t0)

        # 2s later, Spotify says 60.0s (user seeked forward)
        interp.update(60_000, is_playing=True, mono_time=t0 + 2.0)

        with patch("time.monotonic", return_value=t0 + 2.0):
            pos = interp.position_seconds
        assert abs(pos - 60.0) < 0.01

        # Verify no correction rate applied
        with patch("time.monotonic", return_value=t0 + 3.0):
            pos = interp.position_seconds
        assert abs(pos - 61.0) < 0.01  # pure linear, no correction

    def test_pause_freezes_position(self):
        """Position freezes when is_playing=False."""
        interp = PositionInterpolator()
        t0 = 1000.0

        interp.update(30_000, is_playing=True, mono_time=t0)

        # Pause after 1s
        interp.update(31_000, is_playing=False, mono_time=t0 + 1.0)

        # 5s later, position should still be 31.0s
        with patch("time.monotonic", return_value=t0 + 6.0):
            pos = interp.position_seconds
        assert abs(pos - 31.0) < 0.01
        assert interp.is_playing is False

    def test_resume_snaps_to_new_position(self):
        """Pause → play transition snaps to the new position."""
        interp = PositionInterpolator()
        t0 = 1000.0

        interp.update(30_000, is_playing=True, mono_time=t0)
        interp.update(31_000, is_playing=False, mono_time=t0 + 1.0)

        # Resume at a different position (user seeked while paused)
        interp.update(45_000, is_playing=True, mono_time=t0 + 10.0)

        with patch("time.monotonic", return_value=t0 + 10.0):
            pos = interp.position_seconds
        assert abs(pos - 45.0) < 0.01

        # 1s after resume → should advance linearly
        with patch("time.monotonic", return_value=t0 + 11.0):
            pos = interp.position_seconds
        assert abs(pos - 46.0) < 0.05

    def test_position_accuracy_over_10s(self):
        """Position stays accurate through multiple polls over 10s."""
        interp = PositionInterpolator()
        t0 = 1000.0

        # Simulate 5 polls at 2s intervals, Spotify position perfectly on time
        for i in range(6):
            poll_time = t0 + i * 2.0
            poll_ms = int((10.0 + i * 2.0) * 1000)
            interp.update(poll_ms, is_playing=True, mono_time=poll_time)

        # After 10s of perfect polls, check mid-point between last two polls
        with patch("time.monotonic", return_value=t0 + 11.0):
            pos = interp.position_seconds
        # Last poll was at t0+10 with position 20.0s → 1s later should be ~21.0
        assert abs(pos - 21.0) < 0.1
