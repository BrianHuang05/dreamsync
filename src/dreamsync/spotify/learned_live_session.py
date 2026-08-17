"""Coordinator for cache-first Spotify playback with Reactive learning fallback."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, replace
from typing import Any, Callable

from dreamsync.cache import ShowCache, spotify_track_cache_id
from dreamsync.spotify.learned_track import LearnedTrackStore
from dreamsync.v3_session import PositionInterpolator

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LearnedLiveSnapshot:
    active_track_id: str = ""
    active_track_title: str = ""
    active_strategy: str = "waiting"
    active_learning_state: str = "idle"
    active_learning_reason: str = ""
    active_source: str = "waiting"
    active_source_detail: str = "Waiting for a Spotify track."
    learning_state: str = "idle"
    learning_track_id: str = ""
    learning_reason: str = ""
    paused: bool = False
    cache_hits: int = 0
    cache_misses: int = 0
    existing_mp3_reuses: int = 0
    tracks_learned: int = 0
    compile_failures: int = 0
    background_jobs: tuple[tuple[str, str, str], ...] = ()


class SpotifyLearnedLiveSession:
    """Own track strategy selection while injected factories own output/capture."""

    def __init__(
        self,
        watcher,
        *,
        cache: ShowCache,
        profile=None,
        start_compiled: Callable[[Any, PositionInterpolator], None],
        start_reactive: Callable[[], None],
        stop_output: Callable[[], None],
        start_capture: Callable[[Any, float], None] | None = None,
        find_existing_capture: Callable[[Any], tuple[str, dict] | None] | None = None,
        queue_existing_capture: Callable[[str, dict], Any] | None = None,
        invalidate_capture: Callable[[str], None] | None = None,
        diagnostic_callback: Callable[[str], None] | None = None,
        learning_enabled: bool = True,
        learned_store: LearnedTrackStore | None = None,
    ) -> None:
        self._watcher = watcher
        self._cache = cache
        self._profile = profile
        self._start_compiled = start_compiled
        self._start_reactive = start_reactive
        self._stop_output = stop_output
        self._start_capture = start_capture
        self._find_existing_capture = find_existing_capture
        self._queue_existing_capture = queue_existing_capture
        self._invalidate_capture = invalidate_capture
        self._diagnostic_callback = diagnostic_callback
        self._learning_enabled = learning_enabled
        self._store = learned_store or LearnedTrackStore(cache)
        self._interpolator = PositionInterpolator()
        self._lock = threading.RLock()
        self._snapshot = LearnedLiveSnapshot()
        self._active_track = None
        self._capture_valid = False
        self._unsubscribers: list[Callable[[], None]] = []

    def snapshot(self) -> LearnedLiveSnapshot:
        with self._lock:
            return self._snapshot

    def run(self, stop_event: threading.Event) -> dict[str, Any]:
        self._subscribe()
        initial = self._watcher.snapshot()
        state = initial.get("playback_state")
        track = initial.get("current_track") or getattr(state, "track", None)
        if state is not None:
            self._on_playback_state(state)
        if track is not None:
            self._on_track_changed(track, None)
        try:
            while not stop_event.wait(0.1):
                pass
        finally:
            for unsubscribe in reversed(self._unsubscribers):
                unsubscribe()
            self._unsubscribers.clear()
            self._invalidate_active_capture("session_stopped")
            self._stop_output()
        snap = self.snapshot()
        return {
            "mode": "spotify_learned_live",
            "cache_hits": snap.cache_hits,
            "cache_misses": snap.cache_misses,
            "existing_mp3_reuses": snap.existing_mp3_reuses,
            "tracks_learned": snap.tracks_learned,
            "compile_failures": snap.compile_failures,
        }

    def notify_learning_state(self, track_id: str, state: str, reason: str = "") -> None:
        with self._lock:
            jobs = [job for job in self._snapshot.background_jobs if job[0] != track_id]
            jobs.append((track_id, state, reason))
            jobs = jobs[-20:]
            updates = {
                "learning_track_id": track_id,
                "learning_state": state,
                "learning_reason": reason,
                "background_jobs": tuple(jobs),
            }
            if state == "learned":
                updates["tracks_learned"] = self._snapshot.tracks_learned + 1
            elif state == "failed":
                updates["compile_failures"] = self._snapshot.compile_failures + 1
            self._snapshot = replace(self._snapshot, **updates)

    def _subscribe(self) -> None:
        self._unsubscribers = [
            self._watcher.subscribe_track_changed(self._on_track_changed),
            self._watcher.subscribe_playback_state(self._on_playback_state),
        ]

    def _on_track_changed(self, new_track, old_track) -> None:
        # A normal track transition is the event that finalizes a complete
        # capture.  Do not label it as a skip: the downstream eligibility
        # validator uses the captured/expected duration to distinguish a
        # natural completion from an early track change.
        self._finish_active_capture()
        saved_show_path = ""
        timeline = self._store.lookup(new_track.track_id, self._profile)
        if timeline is not None:
            saved_show_path = str(
                self._cache.entry_path(
                    spotify_track_cache_id(new_track.track_id), self._profile
                )
            )
        if timeline is None:
            timeline = self._cache.get(spotify_track_cache_id(new_track.track_id), self._profile)
            if timeline is not None:
                saved_show_path = str(
                    self._cache.entry_path(
                        spotify_track_cache_id(new_track.track_id), self._profile
                    )
                )
        if timeline is None:
            # Existing v3 entries were keyed by the raw Spotify ID.
            timeline = self._cache.get(new_track.track_id, self._profile)
            if timeline is not None:
                saved_show_path = str(
                    self._cache.entry_path(new_track.track_id, self._profile)
                )
        with self._lock:
            self._active_track = new_track
        if timeline is not None:
            self._start_compiled(timeline, self._interpolator)
            detail = f"Using saved show file: {saved_show_path}"
            with self._lock:
                self._capture_valid = False
                self._snapshot = replace(
                    self._snapshot,
                    active_track_id=new_track.track_id,
                    active_track_title=new_track.name,
                    active_strategy="compiled",
                    active_learning_state="idle",
                    active_learning_reason="",
                    active_source="saved_show",
                    active_source_detail=detail,
                    learning_state="using_saved_show",
                    learning_reason=detail,
                    cache_hits=self._snapshot.cache_hits + 1,
                )
            self._report_diagnostic(detail)
            return

        reusable = None
        if self._learning_enabled and self._find_existing_capture is not None:
            try:
                reusable = self._find_existing_capture(new_track)
            except Exception:
                logger.exception("Existing capture lookup failed for %s", new_track.track_id)
        self._start_reactive()
        playback = self._watcher.snapshot().get("playback_state")
        progress = float(getattr(playback, "progress_ms", 0)) / 1000.0
        if reusable is not None and self._queue_existing_capture is not None:
            mp3_path, metadata = reusable
            self._queue_existing_capture(mp3_path, metadata)
            detail = (
                "Saved show file is not compiled yet; reusing existing MP3 "
                f"without a new capture and queueing its compile: {mp3_path}"
            )
            with self._lock:
                self._capture_valid = False
                self._snapshot = replace(
                    self._snapshot,
                    active_track_id=new_track.track_id,
                    active_track_title=new_track.name,
                    active_strategy="reactive",
                    active_learning_state="compiling_existing_mp3",
                    active_learning_reason=detail,
                    active_source="existing_mp3",
                    active_source_detail=detail,
                    learning_state="compiling_existing_mp3",
                    learning_track_id=new_track.track_id,
                    learning_reason=detail,
                    existing_mp3_reuses=self._snapshot.existing_mp3_reuses + 1,
                )
            self._report_diagnostic(detail)
            return
        learning = bool(
            self._learning_enabled
            and self._start_capture is not None
            and bool(getattr(playback, "is_playing", False))
            and progress <= 2.0
        )
        if learning:
            self._start_capture(new_track, progress)
        if learning:
            source = "new_mp3"
            detail = (
                "Creating a new MP3 while running the Reactive on-the-fly show; "
                "the show compile will be queued when the track finishes."
            )
            state = "capturing_new_mp3"
        else:
            source = "reactive_only"
            reason = (
                "paused" if playback is not None and not getattr(playback, "is_playing", False)
                else "started_mid_track" if progress > 2 else "disabled"
            )
            detail = f"Running the Reactive on-the-fly show without learning: {reason}."
            state = "not_learning"
        with self._lock:
            self._capture_valid = learning
            self._snapshot = replace(
                self._snapshot,
                active_track_id=new_track.track_id,
                active_track_title=new_track.name,
                active_strategy="reactive",
                active_learning_state=state,
                active_learning_reason=(
                    detail if learning else "paused"
                    if playback is not None and not getattr(playback, "is_playing", False)
                    else "started_mid_track" if progress > 2 else "disabled"
                ),
                active_source=source,
                active_source_detail=detail,
                learning_state=state,
                learning_track_id=new_track.track_id if learning else "",
                learning_reason=detail,
                cache_misses=self._snapshot.cache_misses + (1 if learning else 0),
            )
        self._report_diagnostic(detail)

    def _on_playback_state(self, state) -> None:
        previous = self._interpolator.position_seconds
        incoming = float(state.progress_ms) / 1000.0
        if self._active_track is not None and abs(incoming - previous) > 2.0:
            self._invalidate_active_capture("seek_detected")
        if self._active_track is not None and not state.is_playing:
            self._invalidate_active_capture("paused")
        self._interpolator.update(state.progress_ms, state.is_playing, state.timestamp)
        with self._lock:
            self._snapshot = replace(self._snapshot, paused=not state.is_playing)

    def _invalidate_active_capture(self, reason: str) -> None:
        with self._lock:
            valid = self._capture_valid
            track_id = getattr(self._active_track, "track_id", "")
            self._capture_valid = False
            if valid and reason:
                self._snapshot = replace(
                    self._snapshot,
                    active_learning_state="not_learned",
                    active_learning_reason=reason,
                    learning_state="not_learned",
                    learning_reason=reason,
                )
        if valid and reason and self._invalidate_capture is not None:
            self._invalidate_capture(reason)

    def _finish_active_capture(self) -> None:
        """Close the active candidate without invalidating a natural boundary."""
        with self._lock:
            self._capture_valid = False

    def _report_diagnostic(self, message: str) -> None:
        logger.info("Learning track source: %s", message)
        if self._diagnostic_callback is not None:
            self._diagnostic_callback(f"Learning track source: {message}")
