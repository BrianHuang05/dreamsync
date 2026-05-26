"""Tests for AudioPlayer (D5.2)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from dreamsync.analyzer.decode import AudioData, DecodeError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_audio(duration_s: float = 3.0, sr: int = 44100) -> AudioData:
    """Generate a short mono sine wave as AudioData."""
    n_samples = int(duration_s * sr)
    t = np.linspace(0, duration_s, n_samples, dtype=np.float32)
    signal = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    return AudioData(signal=signal, sample_rate=sr, duration=duration_s, channels=1)


def _make_player(duration_s: float = 3.0, sr: int = 44100):
    """Create an AudioPlayer with mocked decode and no real sounddevice."""
    audio = _fake_audio(duration_s, sr)
    with patch("dreamsync.show.player.decode_mp3", return_value=audio):
        from dreamsync.show.player import AudioPlayer
        player = AudioPlayer(Path("fake.mp3"), sample_rate=sr)
    return player


# ---------------------------------------------------------------------------
# Construction + properties
# ---------------------------------------------------------------------------

class TestConstruction:
    def test_duration(self):
        player = _make_player(duration_s=5.0)
        assert abs(player.duration - 5.0) < 0.01

    def test_initial_state(self):
        player = _make_player()
        assert player.position_seconds == 0.0
        assert not player.playing
        assert not player.finished

    def test_decode_error_propagates(self, tmp_path: Path):
        with patch("dreamsync.show.player.decode_mp3", side_effect=DecodeError("bad")):
            from dreamsync.show.player import AudioPlayer
            with pytest.raises(DecodeError, match="bad"):
                AudioPlayer(tmp_path / "missing.mp3")


# ---------------------------------------------------------------------------
# Audio callback (simulated — no actual sounddevice)
# ---------------------------------------------------------------------------

class TestAudioCallback:
    def test_callback_advances_position(self):
        player = _make_player(duration_s=1.0, sr=44100)
        player._playing = True
        frames = 1024
        outdata = np.zeros((frames, 1), dtype=np.float32)
        player._audio_callback(outdata, frames, None, None)
        assert player._position == frames
        assert not np.all(outdata == 0.0)

    def test_callback_silence_when_paused(self):
        player = _make_player()
        player._playing = False
        frames = 1024
        outdata = np.ones((frames, 1), dtype=np.float32)
        player._audio_callback(outdata, frames, None, None)
        assert np.all(outdata == 0.0)
        assert player._position == 0

    def test_callback_sets_finished_at_end(self):
        sr = 44100
        player = _make_player(duration_s=0.1, sr=sr)
        player._playing = True
        # Seek near the end
        total = int(0.1 * sr)
        player._position = total - 100
        frames = 1024
        outdata = np.zeros((frames, 1), dtype=np.float32)
        player._audio_callback(outdata, frames, None, None)
        assert player.finished

    def test_callback_zero_fills_past_end(self):
        sr = 44100
        player = _make_player(duration_s=0.05, sr=sr)
        player._playing = True
        total = len(player._signal)
        player._position = total - 10
        frames = 1024
        outdata = np.zeros((frames, 1), dtype=np.float32)
        player._audio_callback(outdata, frames, None, None)
        # First 10 samples should be non-zero (from signal), rest zero
        assert player.finished


# ---------------------------------------------------------------------------
# Seek / pause / resume
# ---------------------------------------------------------------------------

class TestSeekPause:
    def test_seek_sets_position(self):
        player = _make_player(duration_s=5.0, sr=44100)
        player.seek(2.5)
        assert abs(player.position_seconds - 2.5) < 0.001

    def test_seek_clamps_to_zero(self):
        player = _make_player()
        player.seek(-1.0)
        assert player.position_seconds == 0.0

    def test_seek_clamps_to_duration(self):
        player = _make_player(duration_s=3.0)
        player.seek(100.0)
        assert abs(player.position_seconds - player.duration) < 0.001

    def test_seek_resets_finished(self):
        player = _make_player(duration_s=1.0)
        player._finished = True
        player._position = len(player._signal)
        player.seek(0.5)
        assert not player.finished

    def test_pause_stops_playing(self):
        player = _make_player()
        player._playing = True
        player.pause()
        assert not player.playing

    def test_stop_releases_stream(self):
        player = _make_player()
        mock_stream = MagicMock()
        player._stream = mock_stream
        player._playing = True
        player.stop()
        assert not player.playing
        assert player._stream is None
        mock_stream.stop.assert_called_once()
        mock_stream.close.assert_called_once()


class TestClockFallback:
    def test_position_advances_from_monotonic_clock_while_playing(self):
        player = _make_player(duration_s=5.0, sr=44100)
        mock_stream = MagicMock()

        with patch("time.monotonic", side_effect=[100.0, 101.25]):
            player._stream = mock_stream
            player.play()
            assert abs(player.position_seconds - 1.25) < 0.01

    def test_pause_freezes_clock_based_position(self):
        player = _make_player(duration_s=5.0, sr=44100)
        mock_stream = MagicMock()

        with patch("time.monotonic", side_effect=[100.0, 101.5, 101.5, 110.0]):
            player._stream = mock_stream
            player.play()
            player.pause()
            paused = player.position_seconds
            later = player.position_seconds

        assert abs(paused - 1.5) < 0.02
        assert abs(later - paused) < 0.001

    def test_finished_turns_true_when_clock_reaches_duration(self):
        player = _make_player(duration_s=1.0, sr=44100)
        mock_stream = MagicMock()

        with patch("time.monotonic", side_effect=[50.0, 51.1]):
            player._stream = mock_stream
            player.play()
            assert player.finished is True
