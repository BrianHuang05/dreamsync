"""Dedicated playback thread — blocks on ready_queue, plays shows, purges files."""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path


class ShowPlaybackConsumer:
    """Dequeue ready shows and play audio + drive lights.

    Runs in a dedicated thread. Blocks on ``ready_queue.get()`` between
    tracks.  After each track, optionally purges files from disk.
    """

    def __init__(
        self,
        ready_queue: queue.Queue,
        multi_adapter,
        *,
        sample_rate: int = 44100,
        audio_device: int | None = None,
        purge: bool = False,
        debug: bool = False,
    ) -> None:
        self._queue = ready_queue
        self._adapter = multi_adapter
        self._sample_rate = sample_rate
        self._audio_device = audio_device
        self._purge = purge
        self._debug = debug

        self._tracks_played: int = 0
        self._tracks_purged: int = 0

    def run(self, stop_event: threading.Event) -> dict:
        """Main loop — blocks on queue, plays tracks, purges. Returns summary."""
        self._adapter.activate(brightness=100)

        try:
            while not stop_event.is_set():
                try:
                    mp3_path, timeline = self._queue.get(timeout=1.0)
                except queue.Empty:
                    continue

                self._play_one(mp3_path, timeline, stop_event)

                if self._purge:
                    self._purge_files(Path(mp3_path))
                    self._tracks_purged += 1

                self._tracks_played += 1
                self._queue.task_done()
        finally:
            self._adapter.deactivate()

        return {
            "tracks_played": self._tracks_played,
            "tracks_purged": self._tracks_purged,
        }

    @property
    def tracks_played(self) -> int:
        return self._tracks_played

    @property
    def tracks_purged(self) -> int:
        return self._tracks_purged

    def _play_one(self, mp3_path, timeline, stop_event):
        from .show.player import AudioPlayer
        from .show.runtime import ShowPlaybackRuntime

        player = AudioPlayer(
            mp3_path, sample_rate=self._sample_rate, device=self._audio_device
        )
        runtime = ShowPlaybackRuntime(timeline, self._adapter)

        if self._debug:
            print(
                f"[playback] Playing: {Path(mp3_path).name} "
                f"({timeline.duration:.1f}s, {len(timeline.cues)} cues)"
            )

        player.play()
        try:
            while not stop_event.is_set() and not player.finished:
                runtime.tick(player.position_seconds)
                time.sleep(0.005)
        finally:
            player.stop()

        if self._debug:
            print(f"[playback] Finished: {Path(mp3_path).name}")

    def _purge_files(self, mp3_path: Path) -> None:
        """Delete MP3 + sidecar + analysis after playback."""
        for path in [
            mp3_path,
            mp3_path.with_suffix(".json"),
            mp3_path.with_suffix(".analysis.json"),
        ]:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        if self._debug:
            print(f"[playback] Purged: {mp3_path.name}")
