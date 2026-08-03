"""v3 Spotify Show Session — pre-compiled timeline playback with Spotify position tracking."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dreamsync.cache import ShowCache
from dreamsync.show.models import ShowTimeline
from dreamsync.show.runtime import ShowPlaybackRuntime

if TYPE_CHECKING:
    from dreamsync.profile import ProfileConfig
    from dreamsync.spotify.models import PlaybackState, QueueSnapshot, SpotifyTrack
    from dreamsync.spotify.queue_watcher import SpotifyQueueWatcher

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# D5.1 — PositionInterpolator
# ---------------------------------------------------------------------------


class PositionInterpolator:
    """Derive a continuous playback position from periodic Spotify polls.

    On each poll, call update(progress_ms, is_playing, mono_time).
    Between polls, position_seconds interpolates forward using wall-clock time.
    """

    def __init__(self, max_correction_per_sec: float = 0.5) -> None:
        """
        Args:
            max_correction_per_sec: Maximum seconds of drift correction
                applied per second. Larger jumps (seeks) snap immediately.
        """
        self._max_correction_per_sec = max_correction_per_sec
        self._seek_threshold = 2.0

        self._anchor_position: float = 0.0
        self._anchor_mono: float = 0.0
        self._is_playing: bool = False
        self._correction_rate: float = 0.0
        self._lock = threading.Lock()

    def update(
        self, progress_ms: int, is_playing: bool, mono_time: float | None = None,
    ) -> None:
        """Feed a new Spotify playback state poll."""
        now = mono_time if mono_time is not None else time.monotonic()
        new_position = progress_ms / 1000.0

        with self._lock:
            was_playing = self._is_playing
            self._is_playing = is_playing

            if not was_playing or self._anchor_mono == 0.0:
                self._anchor_position = new_position
                self._anchor_mono = now
                self._correction_rate = 0.0
                return

            elapsed = now - self._anchor_mono
            interpolated = self._anchor_position + elapsed + (self._correction_rate * elapsed)

            drift = new_position - interpolated

            if abs(drift) > self._seek_threshold:
                self._anchor_position = new_position
                self._anchor_mono = now
                self._correction_rate = 0.0
            else:
                self._anchor_position = new_position
                self._anchor_mono = now
                if abs(drift) > 0.001:
                    rate = max(-self._max_correction_per_sec,
                               min(self._max_correction_per_sec, drift / 2.0))
                    self._correction_rate = rate
                else:
                    self._correction_rate = 0.0

    @property
    def position_seconds(self) -> float:
        """Current interpolated position in seconds."""
        with self._lock:
            if not self._is_playing:
                return self._anchor_position
            now = time.monotonic()
            elapsed = now - self._anchor_mono
            correction = self._correction_rate * elapsed
            return self._anchor_position + elapsed + correction

    @property
    def is_playing(self) -> bool:
        """Whether Spotify is currently playing."""
        with self._lock:
            return self._is_playing


# ---------------------------------------------------------------------------
# D5.2 — SpotifyShowSession
# ---------------------------------------------------------------------------


class SpotifyShowSession:
    """Spotify-connected show session: pre-compiled timeline playback.

    Manages the lifecycle of:
    - Spotify position tracking (PositionInterpolator)
    - Show compilation on track change (cached_compile_show)
    - Background precompilation of queued tracks
    - ShowPlaybackRuntime tick loop
    """

    def __init__(
        self,
        multi_adapter,
        spotify_watcher: SpotifyQueueWatcher,
        *,
        cache: ShowCache,
        profile: ProfileConfig | None = None,
        capture_dir: Path | str = "captured_songs",
        debug: bool = False,
    ) -> None:
        self._multi_adapter = multi_adapter
        self._watcher = spotify_watcher
        self._cache = cache
        self._profile = profile
        self._capture_dir = Path(capture_dir)
        self._debug = debug

        self._interpolator = PositionInterpolator()
        self._runtime: ShowPlaybackRuntime | None = None
        self._runtime_lock = threading.Lock()

        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="v3-precompile")

        # Stats
        self._tracks_played: int = 0
        self._cache_hits: int = 0
        self._cache_misses: int = 0
        self._compile_errors: int = 0
        self._precompiled: int = 0

    def run(self, stop_event: threading.Event) -> dict[str, Any]:
        """Main loop. Blocks until stop_event is set. Returns summary."""
        # 1. Activate devices
        self._multi_adapter.activate(brightness=100)

        # 2. Subscribe without mutating the watcher's private callbacks.
        unsubscribers = (
            self._watcher.subscribe_track_changed(self._on_track_changed),
            self._watcher.subscribe_playback_state(self._on_playback_state),
            self._watcher.subscribe_queue_updated(self._on_queue_updated),
        )

        logger.info("v3 session started — waiting for Spotify track")
        if self._debug:
            print("[v3] Session started. Waiting for Spotify playback...")

        # 3. Main tick loop
        frames_sent = 0
        try:
            while not stop_event.is_set():
                if not self._interpolator.is_playing:
                    time.sleep(0.05)  # 20 Hz idle
                    continue

                with self._runtime_lock:
                    runtime = self._runtime

                if runtime is None:
                    time.sleep(0.05)  # 20 Hz idle — no show compiled yet
                    continue

                t = self._interpolator.position_seconds
                sent = runtime.tick(t)
                if sent:
                    frames_sent += 1
                time.sleep(0.005)  # ~200 Hz
        except KeyboardInterrupt:
            pass

        # 4. Cleanup
        self._executor.shutdown(wait=False)
        self._multi_adapter.deactivate()

        # 5. Detach subscriptions
        for unsubscribe in unsubscribers:
            unsubscribe()

        # 6. Build summary
        runtime_stats = {}
        with self._runtime_lock:
            if self._runtime is not None:
                runtime_stats = self._runtime.stats

        summary = {
            "mode": "v3",
            "tracks_played": self._tracks_played,
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
            "compile_errors": self._compile_errors,
            "precompiled": self._precompiled,
            "frames_sent": frames_sent,
            **runtime_stats,
        }

        if self._debug:
            print(f"[v3] Session ended. {summary}")

        return summary

    # -- Callbacks (fired from SpotifyQueueWatcher thread) --

    def _on_track_changed(self, new_track: SpotifyTrack, old_track: SpotifyTrack | None) -> None:
        """Compile show for the new track. Runs in watcher thread."""
        logger.info("v3: track changed → '%s' by %s", new_track.name, new_track.artist)
        if self._debug:
            print(f"[v3] Track: {new_track.name} — {new_track.artist}")

        self._tracks_played += 1

        timeline = self._compile_for_track(new_track)
        if timeline is not None:
            new_runtime = ShowPlaybackRuntime(timeline, self._multi_adapter)
            with self._runtime_lock:
                self._runtime = new_runtime
            logger.info("v3: show ready for '%s' (%d cues)", new_track.name, len(timeline.cues))
        else:
            with self._runtime_lock:
                self._runtime = None
            logger.warning("v3: no show available for '%s' (no mp3)", new_track.name)
            if self._debug:
                print(f"[v3] No mp3 found for '{new_track.name}' — lights idle")

    def _on_playback_state(self, state: PlaybackState) -> None:
        """Update position interpolator. Runs in watcher thread."""
        self._interpolator.update(state.progress_ms, state.is_playing, state.timestamp)

    def _on_queue_updated(self, snapshot: QueueSnapshot) -> None:
        """Precompile upcoming tracks in background. Runs in watcher thread."""
        if snapshot.queue:
            self._executor.submit(self._precompile_queue, snapshot.queue)

    # -- Internal --

    def _compile_for_track(self, track: SpotifyTrack) -> ShowTimeline | None:
        """Compile (or retrieve from cache) a show for a track.
        Returns None if no mp3 is available for analysis."""
        # 1. Check cache
        if self._cache.has(track.track_id, self._profile):
            timeline = self._cache.get(track.track_id, self._profile)
            if timeline is not None:
                self._cache_hits += 1
                logger.info("v3: cache HIT for '%s'", track.name)
                return timeline

        # 2. Find mp3
        mp3_path = self._find_mp3_for_track(track)
        if mp3_path is None:
            logger.warning("v3: no mp3 for '%s' — cannot compile", track.name)
            return None

        # 3. Analyze + compile
        try:
            from dreamsync.analyzer.analyze import analyze_song
            from dreamsync.cache import cached_compile_show

            structure = analyze_song(mp3_path)
            timeline, from_cache = cached_compile_show(
                structure,
                self._profile,
                cache=self._cache,
                track_id=track.track_id,
            )
            if from_cache:
                self._cache_hits += 1
            else:
                self._cache_misses += 1
            return timeline
        except Exception as exc:
            self._compile_errors += 1
            logger.error("v3: compile failed for '%s': %s", track.name, exc)
            return None

    def _find_mp3_for_track(self, track: SpotifyTrack) -> Path | None:
        """Search capture_dir for a captured mp3 matching the track."""
        if not self._capture_dir.exists():
            return None

        # Strategy 1: glob for track name (case-insensitive)
        track_name_lower = track.name.lower()
        for mp3 in self._capture_dir.glob("*.mp3"):
            if track_name_lower in mp3.stem.lower():
                return mp3

        # Strategy 2: glob for track_id
        for mp3 in self._capture_dir.glob("*.mp3"):
            if track.track_id in mp3.stem:
                return mp3

        return None

    def _precompile_queue(self, queue: tuple[SpotifyTrack, ...]) -> None:
        """Compile upcoming tracks in a background thread pool."""
        for track in queue:
            if self._cache.has(track.track_id, self._profile):
                continue
            try:
                timeline = self._compile_for_track(track)
                if timeline is not None:
                    self._precompiled += 1
                    logger.info("v3: precompiled '%s'", track.name)
            except Exception as exc:
                logger.warning("v3: precompile failed for '%s': %s", track.name, exc)


# ---------------------------------------------------------------------------
# D5.3 — Top-level entry point
# ---------------------------------------------------------------------------


def run_v3_session(
    multi_adapter,
    spotify_watcher,
    *,
    cache_dir: str = "~/.dreamsync/cache",
    profile: ProfileConfig | None = None,
    capture_dir: str = "captured_songs",
    stop_event: threading.Event,
    debug: bool = False,
) -> dict[str, Any]:
    """Top-level entry point for v3 session. Called from run_session()."""
    cache = ShowCache(cache_dir)
    session = SpotifyShowSession(
        multi_adapter,
        spotify_watcher,
        cache=cache,
        profile=profile,
        capture_dir=capture_dir,
        debug=debug,
    )
    return session.run(stop_event)
