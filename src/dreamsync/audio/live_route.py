"""App-owned PulseAudio PCM pass-through for Live Learning.

Capture and Reactive subscribe to the same source. Only this bridge delivers
that source to the physical sink; it survives lighting-runtime changes.
"""

from __future__ import annotations

import subprocess


class LiveAudioRoute:
    def __init__(self, source: str, sink: str) -> None:
        if not source or not sink or source == sink or source == f"{sink}.monitor":
            raise ValueError("Live audio needs a capture source separate from its speaker sink")
        self.source = source
        self.sink = sink
        self._capture = None
        self._playback = None

    def start(self) -> None:
        if self._capture is not None:
            return
        # Pulse owns resampling between the two device clocks. Pipes are
        # bounded, so a slow output cannot accumulate unbounded Python PCM.
        common = ["--raw", "--format=float32le", "--rate=48000", "--channels=2",
                  "--latency-msec=20"]
        try:
            self._capture = subprocess.Popen(
                ["parec", f"--device={self.source}", *common],
                stdout=subprocess.PIPE,
            )
            self._playback = subprocess.Popen(
                ["pacat", "--playback", f"--device={self.sink}", *common],
                stdin=self._capture.stdout,
            )
            self._capture.stdout.close()
        except BaseException:
            self.stop()
            raise

    def check(self) -> None:
        for process in (self._capture, self._playback):
            if process is not None and process.poll() is not None:
                raise RuntimeError("Live audio routing stopped; check the configured Pulse source and sink")

    def stop(self) -> None:
        for process in (self._playback, self._capture):
            if process is None:
                continue
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
        self._capture = self._playback = None
