"""Spotify queue watcher — background polling thread.

Polls Spotify's playback state and queue at configurable intervals,
detects track changes, and fires callbacks. Follows the same daemon-thread
pattern as ConfigWatcher and DeviceHealthMonitor.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from .client import SpotifyAuthError, SpotifyClient
from .models import PlaybackState, QueueSnapshot, SpotifyTrack

_logger = logging.getLogger(__name__)

# Backoff thresholds
_BACKOFF_THRESHOLD_SLOW = 5   # After 5 consecutive errors, slow down
_BACKOFF_THRESHOLD_CRAWL = 10  # After 10, crawl
_SLOW_INTERVAL = 10.0
_CRAWL_INTERVAL = 30.0
_AUTH_BACKOFF_INTERVAL = 30.0


class SpotifyQueueWatcher:
    """Background thread that polls Spotify and dispatches events.

    Parameters
    ----------
    client : SpotifyClient
        Authenticated API client.
    poll_interval : float
        Seconds between playback state polls (default 2.0).
    queue_poll_interval : float
        Seconds between queue polls (default 10.0).
    on_track_changed : callback(new_track, old_track)
        Fired when the currently playing track changes.
    on_playback_state_changed : callback(playback_state)
        Fired on every playback state poll that succeeds.
    on_queue_updated : callback(queue_snapshot)
        Fired when the queue is polled.
    """

    def __init__(
        self,
        client: SpotifyClient,
        *,
        poll_interval: float = 2.0,
        queue_poll_interval: float = 10.0,
        on_track_changed: Callable[[SpotifyTrack, SpotifyTrack | None], None] | None = None,
        on_playback_state_changed: Callable[[PlaybackState], None] | None = None,
        on_queue_updated: Callable[[QueueSnapshot], None] | None = None,
    ) -> None:
        self._client = client
        self._poll_interval = poll_interval
        self._queue_poll_interval = queue_poll_interval
        self._on_track_changed = on_track_changed
        self._on_playback_state_changed = on_playback_state_changed
        self._on_queue_updated = on_queue_updated

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

        # State (protected by _lock for external reads)
        self._current_track: SpotifyTrack | None = None
        self._playback_state: PlaybackState | None = None
        self._queue: QueueSnapshot | None = None
        self._consecutive_errors: int = 0
        self._auth_failed: bool = False

    # -- Public properties ---------------------------------------------------

    @property
    def current_track(self) -> SpotifyTrack | None:
        with self._lock:
            return self._current_track

    @property
    def playback_state(self) -> PlaybackState | None:
        with self._lock:
            return self._playback_state

    @property
    def queue(self) -> QueueSnapshot | None:
        with self._lock:
            return self._queue

    @property
    def auth_failed(self) -> bool:
        with self._lock:
            return self._auth_failed

    # -- Lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Launch the background polling thread (idempotent)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop, name="spotify-queue-watcher", daemon=True
        )
        self._thread.start()
        _logger.info("Spotify queue watcher started (poll=%.1fs, queue=%.1fs)",
                      self._poll_interval, self._queue_poll_interval)

    def stop(self) -> None:
        """Signal the thread to stop and wait (idempotent)."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        _logger.info("Spotify queue watcher stopped")

    # -- Polling loop --------------------------------------------------------

    def _poll_loop(self) -> None:
        last_queue_poll = 0.0

        while not self._stop_event.is_set():
            interval = self._effective_interval()
            self._stop_event.wait(interval)
            if self._stop_event.is_set():
                break

            track_changed = self._poll_playback()
            now = time.monotonic()

            # Poll queue on track change, or on the slow interval
            should_poll_queue = (
                track_changed
                or (now - last_queue_poll) >= self._queue_poll_interval
            )
            if should_poll_queue:
                self._poll_queue()
                last_queue_poll = now

    def _effective_interval(self) -> float:
        """Return the current poll interval, adjusted for errors."""
        with self._lock:
            if self._auth_failed:
                return _AUTH_BACKOFF_INTERVAL
            errors = self._consecutive_errors
        if errors >= _BACKOFF_THRESHOLD_CRAWL:
            return _CRAWL_INTERVAL
        if errors >= _BACKOFF_THRESHOLD_SLOW:
            return _SLOW_INTERVAL
        return self._poll_interval

    def _poll_playback(self) -> bool:
        """Poll playback state. Returns True if the track changed."""
        try:
            state = self._client.get_playback_state()
        except SpotifyAuthError as exc:
            _logger.warning("Spotify auth error: %s", exc)
            with self._lock:
                self._auth_failed = True
                self._consecutive_errors += 1
            return False
        except Exception as exc:
            _logger.warning("Spotify playback poll error: %s", exc)
            with self._lock:
                self._consecutive_errors += 1
            return False

        # Success — reset error state
        with self._lock:
            self._consecutive_errors = 0
            self._auth_failed = False

        if state is None:
            # Nothing playing
            with self._lock:
                self._playback_state = None
            return False

        with self._lock:
            self._playback_state = state

        if self._on_playback_state_changed:
            try:
                self._on_playback_state_changed(state)
            except Exception as exc:
                _logger.warning("on_playback_state_changed callback error: %s", exc)

        # Track change detection
        new_track = state.track
        with self._lock:
            old_track = self._current_track
            new_id = new_track.track_id if new_track else None
            old_id = old_track.track_id if old_track else None

        if new_id != old_id and new_track is not None:
            with self._lock:
                self._current_track = new_track
            _logger.info("Track changed: %s — %s", new_track.artist, new_track.name)
            if self._on_track_changed:
                try:
                    self._on_track_changed(new_track, old_track)
                except Exception as exc:
                    _logger.warning("on_track_changed callback error: %s", exc)
            return True

        # Update current track even if unchanged (could be None → None)
        with self._lock:
            self._current_track = new_track
        return False

    def _poll_queue(self) -> None:
        """Poll the play queue."""
        try:
            snapshot = self._client.get_queue()
        except Exception as exc:
            _logger.warning("Spotify queue poll error: %s", exc)
            return

        with self._lock:
            self._queue = snapshot

        if self._on_queue_updated:
            try:
                self._on_queue_updated(snapshot)
            except Exception as exc:
                _logger.warning("on_queue_updated callback error: %s", exc)
