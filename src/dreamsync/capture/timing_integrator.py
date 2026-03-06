"""Integrate external song timing data with the boundary queue."""

from __future__ import annotations

import logging
import threading
from typing import Callable

from dreamsync.capture.boundary_queue import BoundaryEntry, BoundaryQueue
from dreamsync.capture.segment_boundary import compute_boundaries

logger = logging.getLogger(__name__)


class TimingIntegrator:
    """Connect an external timing source to the pipeline's boundary queue.

    Periodically fetches timing data via a user-supplied callback,
    recomputes future boundaries, and applies them to the queue while
    respecting safety margins.

    Parameters
    ----------
    boundary_queue:
        The mutable boundary queue to update.
    get_current_frame:
        Callable returning the pipeline's current frame position.
    sample_rate:
        Audio sample rate for boundary computation.
    refresh_interval:
        Seconds between periodic timing refreshes.
    """

    def __init__(
        self,
        boundary_queue: BoundaryQueue,
        get_current_frame: Callable[[], int],
        sample_rate: int = 48000,
        refresh_interval: float = 5.0,
        logger: object | None = None,
    ) -> None:
        self._queue = boundary_queue
        self._get_current_frame = get_current_frame
        self._sample_rate = sample_rate
        self._refresh_interval = refresh_interval
        self._timer: threading.Timer | None = None
        self._fetch_fn: Callable[[], dict | None] | None = None
        self._running = False
        self._segment_counter = 0
        self._logger = logger

    # ------------------------------------------------------------------
    # One-shot update
    # ------------------------------------------------------------------

    def update(self, timing_data: dict) -> int:
        """Process *timing_data* and refresh the boundary queue.

        Returns the number of new boundaries applied.
        """
        durations = timing_data.get("song_durations")
        if not durations:
            logger.debug("Empty durations — skipping boundary update")
            return 0

        playback_time = timing_data.get("current_playback_time", 0.0)
        relative = compute_boundaries(durations, playback_time, self._sample_rate)

        current_frame = self._get_current_frame()
        songs = timing_data.get("songs")
        fallback_meta = timing_data.get("current_song")
        entries: list[BoundaryEntry] = []
        for i, rel_frame in enumerate(relative):
            abs_frame = current_frame + rel_frame
            # Per-boundary metadata: use songs[i] if available, else fallback
            if songs and i < len(songs):
                meta = songs[i]
            else:
                meta = fallback_meta
            entries.append(
                BoundaryEntry(
                    frame_position=abs_frame,
                    segment_index=self._segment_counter + i,
                    metadata=meta,
                )
            )

        self._queue.replace_future(entries, current_frame)
        self._segment_counter += len(entries)
        logger.info(
            "Boundary queue updated: %d boundaries, current_frame=%d",
            len(entries),
            current_frame,
        )
        if self._logger is not None:
            self._logger.timing_event(
                "update",
                frame_position=current_frame,
                num_boundaries=len(entries),
                durations_count=len(durations),
                playback_time=playback_time,
            )
        return len(entries)

    def on_track_change(self, timing_data: dict) -> int:
        """Handle track-change event with immediate refresh."""
        logger.info("Track change detected — immediate boundary refresh")
        return self.update(timing_data)

    # ------------------------------------------------------------------
    # Periodic refresh
    # ------------------------------------------------------------------

    def start_periodic_refresh(self, fetch_fn: Callable[[], dict | None]) -> None:
        """Start a background timer that calls *fetch_fn* every interval."""
        self._fetch_fn = fetch_fn
        self._running = True
        self._schedule_next()

    def stop(self) -> None:
        """Stop the periodic refresh."""
        self._running = False
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _schedule_next(self) -> None:
        if not self._running:
            return
        self._timer = threading.Timer(self._refresh_interval, self._tick)
        self._timer.daemon = True
        self._timer.start()

    def _tick(self) -> None:
        if not self._running or self._fetch_fn is None:
            return
        try:
            data = self._fetch_fn()
            if data is not None:
                count = self.update(data)
                if self._logger is not None:
                    self._logger.timing_event("tick.success", num_boundaries=count)
            else:
                if self._logger is not None:
                    self._logger.timing_event("tick.skip")
        except Exception:
            logger.exception("Timing refresh failed — keeping existing boundaries")
            if self._logger is not None:
                self._logger.timing_event("tick.failure")
        finally:
            self._schedule_next()
