"""Audio Playback Engine — decode mp3 and stream to system speakers via sounddevice."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from dreamsync.analyzer.decode import DecodeError, decode_mp3


class AudioPlayer:
    """Play an audio file through system speakers with accurate position tracking.

    The full file is decoded to memory upfront (mono float32, ~42 MB for 4 min
    at 44.1 kHz).  Playback uses a ``sounddevice.OutputStream`` callback that
    copies samples from the decoded buffer, advancing a sample counter for
    position tracking.
    """

    def __init__(
        self,
        audio_path: Path,
        sample_rate: int = 44100,
        blocksize: int = 1024,
        device: int | None = None,
    ) -> None:
        audio_path = Path(audio_path)
        audio_data = decode_mp3(audio_path, target_sr=sample_rate)
        self._signal: np.ndarray = audio_data.signal  # mono float32
        self._sr: int = sample_rate
        self._blocksize: int = blocksize
        self._device: int | None = device
        self._position: int = 0
        self._playing: bool = False
        self._finished: bool = False
        self._stream = None  # created lazily in play()
        self._clock_anchor: float | None = None

    # -- sounddevice callback ------------------------------------------------

    def _audio_callback(self, outdata, frames, time_info, status):  # noqa: ARG002
        if not self._playing:
            outdata[:] = 0.0
            return

        end = self._position + frames
        sig_len = len(self._signal)

        if self._position >= sig_len:
            outdata[:] = 0.0
            self._finished = True
            return

        if end > sig_len:
            valid = sig_len - self._position
            outdata[:valid, 0] = self._signal[self._position:sig_len]
            outdata[valid:] = 0.0
            self._position = sig_len
            self._finished = True
        else:
            outdata[:, 0] = self._signal[self._position:end]
            self._position = end

    # -- Public API ----------------------------------------------------------

    def play(self) -> None:
        """Start or resume playback from current position."""
        if self._stream is None:
            import sounddevice as sd

            self._stream = sd.OutputStream(
                samplerate=self._sr,
                blocksize=self._blocksize,
                device=self._device,
                channels=1,
                dtype="float32",
                callback=self._audio_callback,
            )
            self._stream.start()
        self._clock_anchor = time.monotonic() - (self._position / self._sr)
        self._playing = True

    def pause(self) -> None:
        """Pause playback (audio stops, position freezes)."""
        self._sync_position_to_clock()
        self._playing = False
        self._clock_anchor = None

    def seek(self, t: float) -> None:
        """Jump to time *t* (seconds).  Clamps to [0, duration]."""
        sample = int(t * self._sr)
        sample = max(0, min(sample, len(self._signal)))
        self._position = sample
        if sample < len(self._signal):
            self._finished = False
        if self._playing:
            self._clock_anchor = time.monotonic() - (self._position / self._sr)

    def stop(self) -> None:
        """Stop playback and release the audio stream."""
        self._sync_position_to_clock()
        self._playing = False
        self._clock_anchor = None
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    @property
    def position_seconds(self) -> float:
        """Current playback position in seconds."""
        position = self._position / self._sr
        if self._playing and self._clock_anchor is not None:
            estimated = max(0.0, time.monotonic() - self._clock_anchor)
            position = max(position, estimated)
        return min(position, self.duration)

    @property
    def duration(self) -> float:
        """Total duration in seconds."""
        return len(self._signal) / self._sr

    @property
    def playing(self) -> bool:
        """True if audio is actively playing."""
        return self._playing

    @property
    def finished(self) -> bool:
        """True if playback reached the end of the file."""
        if not self._finished and self.position_seconds >= self.duration:
            self._finished = True
        return self._finished

    def _sync_position_to_clock(self) -> None:
        if not self._playing or self._clock_anchor is None:
            return
        estimated_samples = int(max(0.0, time.monotonic() - self._clock_anchor) * self._sr)
        self._position = max(self._position, min(estimated_samples, len(self._signal)))
        if self._position >= len(self._signal):
            self._finished = True
