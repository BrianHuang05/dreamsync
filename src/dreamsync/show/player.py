"""Audio Playback Engine — decode mp3 and stream to system speakers via sounddevice."""

from __future__ import annotations

from pathlib import Path
import time

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
        # ``_position`` is the callback write head: it is necessarily ahead of
        # what has reached the speakers.  Keep a second clock anchored to the
        # host time at which the callback's first sample will hit the DAC.
        self._dac_anchor_host_seconds: float | None = None
        self._dac_anchor_track_seconds: float = 0.0
        self._paused_position_seconds: float = 0.0
        self._queued_to_end: bool = False
        self._output_latency_seconds: float = 0.0
        self._stream = None  # created lazily in play()

    # -- sounddevice callback ------------------------------------------------

    def _set_dac_anchor(self, start_position: int, time_info) -> None:
        """Anchor track time to the audio device's reported DAC time.

        ``sounddevice`` gives both the callback's current host time and when
        its output buffer reaches the DAC.  The difference includes the
        hardware/output buffering that made the visual clock lead audio.
        """
        try:
            lead_seconds = max(
                0.0,
                float(time_info.outputBufferDacTime) - float(time_info.currentTime),
            )
        except (AttributeError, TypeError, ValueError):
            lead_seconds = self._output_latency_seconds
        self._dac_anchor_track_seconds = start_position / self._sr
        self._dac_anchor_host_seconds = time.monotonic() + lead_seconds

    def _audio_callback(self, outdata, frames, time_info, status):  # noqa: ARG002
        if not self._playing:
            outdata[:] = 0.0
            return

        start_position = self._position
        end = self._position + frames
        sig_len = len(self._signal)

        if self._position >= sig_len:
            outdata[:] = 0.0
            self._queued_to_end = True
            return

        if end > sig_len:
            valid = sig_len - self._position
            outdata[:valid, 0] = self._signal[self._position:sig_len]
            outdata[valid:] = 0.0
            self._position = sig_len
            self._queued_to_end = True
        else:
            outdata[:, 0] = self._signal[self._position:end]
            self._position = end
        self._set_dac_anchor(start_position, time_info)

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
            try:
                self._output_latency_seconds = max(0.0, float(self._stream.latency))
            except (AttributeError, TypeError, ValueError):
                self._output_latency_seconds = 0.0
        # Set this before starting the stream so its first callback contains
        # audio, rather than one silent output block.
        self._playing = True
        self._finished = False
        self._queued_to_end = False
        self._dac_anchor_host_seconds = None
        self._dac_anchor_track_seconds = self._position / self._sr
        self._paused_position_seconds = self._dac_anchor_track_seconds
        if not self._stream.active:
            self._stream.start()

    def pause(self) -> None:
        """Pause playback (audio stops, position freezes)."""
        if self._playing:
            self._paused_position_seconds = self.position_seconds
            self._position = int(self._paused_position_seconds * self._sr)
        self._playing = False
        self._dac_anchor_host_seconds = None

    def seek(self, t: float) -> None:
        """Jump to time *t* (seconds).  Clamps to [0, duration]."""
        sample = int(t * self._sr)
        sample = max(0, min(sample, len(self._signal)))
        self._position = sample
        self._paused_position_seconds = sample / self._sr
        self._dac_anchor_track_seconds = self._paused_position_seconds
        self._dac_anchor_host_seconds = None
        self._queued_to_end = sample >= len(self._signal)
        if sample < len(self._signal):
            self._finished = False

    def stop(self) -> None:
        """Stop playback and release the audio stream."""
        self._playing = False
        self._dac_anchor_host_seconds = None
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    @property
    def position_seconds(self) -> float:
        """Current playback position in seconds."""
        if not self._playing:
            return min(self._paused_position_seconds, self.duration)
        if self._dac_anchor_host_seconds is None:
            # Before the first callback, compensate for the known output queue
            # rather than reporting its write-head position.
            return max(0.0, self._position / self._sr - self._output_latency_seconds)
        audible_position = self._dac_anchor_track_seconds + (
            time.monotonic() - self._dac_anchor_host_seconds
        )
        # A host timer cannot make sound arrive before a buffer the callback has
        # actually written; the write head remains an important upper bound.
        return max(0.0, min(audible_position, self._position / self._sr, self.duration))

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
        if self._queued_to_end and self.position_seconds >= self.duration:
            self._finished = True
        return self._finished
