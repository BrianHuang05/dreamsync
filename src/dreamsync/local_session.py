"""Local Show Session — offline playback with pre-compiled timelines."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dreamsync.analyzer.analyze import analyze_song
from dreamsync.cache import ShowCache, cached_compile_show, path_based_track_id
from dreamsync.show.models import ShowTimeline
from dreamsync.show.player import AudioPlayer
from dreamsync.show.runtime import ShowPlaybackRuntime

if TYPE_CHECKING:
    from dreamsync.playlist import PlaylistManager
    from dreamsync.profile import ProfileConfig

logger = logging.getLogger(__name__)


class LocalShowSession:
    """Standalone local show session: offline playback with pre-compiled timelines.

    Manages the lifecycle of:
    - Audio playback via AudioPlayer (position tracking + audio output)
    - Show compilation on track load (cached_compile_show)
    - ShowPlaybackRuntime tick loop
    - End-of-track detection
    """

    def __init__(
        self,
        multi_adapter,
        *,
        cache: ShowCache,
        profile: ProfileConfig | None = None,
        sample_rate: int = 44100,
        audio_device: int | None = None,
        debug: bool = False,
    ) -> None:
        self._multi_adapter = multi_adapter
        self._cache = cache
        self._profile = profile
        self._sample_rate = sample_rate
        self._audio_device = audio_device
        self._debug = debug

        # Stats
        self._cache_hits: int = 0
        self._cache_misses: int = 0
        self._compile_errors: int = 0

    def run(
        self,
        audio_path: Path | str,
        stop_event: threading.Event,
    ) -> dict[str, Any]:
        """Play a single track. Blocks until finished or stop_event is set."""
        audio_path = Path(audio_path)

        # 1. Activate devices
        self._multi_adapter.activate(brightness=100)

        logger.info("local: loading '%s'", audio_path.name)
        if self._debug:
            print(f"[local] Loading: {audio_path.name}")

        # 2. Compile show (cache-aware)
        timeline = self._compile_for_file(audio_path)
        if timeline is None:
            self._multi_adapter.deactivate()
            return {
                "mode": "local",
                "track": audio_path.name,
                "error": "compilation_failed",
                "cache_hits": self._cache_hits,
                "cache_misses": self._cache_misses,
                "compile_errors": self._compile_errors,
                "frames_sent": 0,
            }

        # 3. Create AudioPlayer
        player = AudioPlayer(
            audio_path,
            sample_rate=self._sample_rate,
            device=self._audio_device,
        )

        # 4. Create ShowPlaybackRuntime
        runtime = ShowPlaybackRuntime(timeline, self._multi_adapter)

        # 5. Start playback
        player.play()
        if self._debug:
            print(f"[local] Playing: {audio_path.name} ({timeline.duration:.1f}s, {len(timeline.cues)} cues)")

        # 6. Main tick loop
        frames_sent = 0
        try:
            while not stop_event.is_set():
                if player.finished:
                    break

                if not player.playing:
                    time.sleep(0.05)  # 20 Hz idle (paused)
                    continue

                t = player.position_seconds
                sent = runtime.tick(t)
                if sent:
                    frames_sent += 1
                time.sleep(0.005)  # ~200 Hz
        except KeyboardInterrupt:
            pass

        # 7. Cleanup
        player.stop()
        self._multi_adapter.deactivate()

        # 8. Build summary
        summary = {
            "mode": "local",
            "track": audio_path.name,
            "duration": timeline.duration,
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
            "compile_errors": self._compile_errors,
            "frames_sent": frames_sent,
            **runtime.stats,
        }

        if self._debug:
            print(f"[local] Done. {summary}")

        return summary

    def load_track(self, audio_path: Path | str) -> ShowTimeline | None:
        """Compile (or retrieve from cache) a show for a track.
        Public API for Feature 8 (PlaylistManager) to precompile tracks."""
        return self._compile_for_file(Path(audio_path))

    def _compile_for_file(self, audio_path: Path) -> ShowTimeline | None:
        """Internal: check cache, analyze, compile, cache. Returns None on error."""
        track_id = self._track_id_for_file(audio_path)

        # 1. Check cache
        if self._cache.has(track_id, self._profile):
            timeline = self._cache.get(track_id, self._profile)
            if timeline is not None:
                self._cache_hits += 1
                logger.info("local: cache HIT for '%s'", audio_path.name)
                if self._debug:
                    print(f"[local] Cache hit: {audio_path.name}")
                return timeline

        # 2. Analyze + compile
        try:
            logger.info("local: analyzing '%s'", audio_path.name)
            if self._debug:
                print(f"[local] Analyzing: {audio_path.name}...")

            structure = analyze_song(audio_path)
            timeline, from_cache = cached_compile_show(
                structure,
                self._profile,
                cache=self._cache,
                track_id=track_id,
            )
            if from_cache:
                self._cache_hits += 1
            else:
                self._cache_misses += 1
            logger.info("local: compiled '%s' (%d cues)", audio_path.name, len(timeline.cues))
            return timeline
        except Exception as exc:
            self._compile_errors += 1
            logger.error("local: compile failed for '%s': %s", audio_path.name, exc)
            if self._debug:
                print(f"[local] Error compiling '{audio_path.name}': {exc}")
            return None

    def _track_id_for_file(self, audio_path: Path) -> str:
        """Generate a cache-safe track ID from a file path."""
        return path_based_track_id(audio_path)


# ---------------------------------------------------------------------------
# D7.2 — Top-level entry point
# ---------------------------------------------------------------------------


class LocalPlaylistSession:
    """Multi-track local show session with playlist management.

    Wraps LocalShowSession with:
    - Track advancement on end-of-track (AudioPlayer.finished)
    - Background precompilation of upcoming tracks
    - Interactive control signals (next/prev/stop)
    """

    def __init__(
        self,
        multi_adapter,
        playlist: PlaylistManager,
        *,
        cache: ShowCache,
        profile: ProfileConfig | None = None,
        sample_rate: int = 44100,
        audio_device: int | None = None,
        debug: bool = False,
    ) -> None:
        self._multi_adapter = multi_adapter
        self._playlist = playlist
        self._cache = cache
        self._profile = profile
        self._sample_rate = sample_rate
        self._audio_device = audio_device
        self._debug = debug

        # Control signals
        self._signal_next = threading.Event()
        self._signal_prev = threading.Event()

        # Background precompilation
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="precompile")

        # Inner session (reused across tracks)
        self._session = LocalShowSession(
            multi_adapter,
            cache=cache,
            profile=profile,
            sample_rate=sample_rate,
            audio_device=audio_device,
            debug=debug,
        )

        # Stats
        self._tracks_played: int = 0
        self._tracks_skipped: int = 0
        self._precompiled: int = 0

    def run(self, stop_event: threading.Event) -> dict[str, Any]:
        """Play through the playlist. Blocks until exhausted or stopped."""
        if not self._playlist.current:
            logger.warning("Playlist is empty — nothing to play")
            return {"mode": "local_playlist", "tracks_played": 0, "error": "empty_playlist"}

        logger.info("playlist: starting %d tracks", len(self._playlist))
        if self._debug:
            print(f"[playlist] Starting playlist: {len(self._playlist)} tracks")

        # Activate devices once for the whole playlist
        self._multi_adapter.activate(brightness=100)

        # Start precompilation for upcoming tracks
        self._executor.submit(self._precompile_upcoming)

        try:
            while not stop_event.is_set():
                current = self._playlist.current
                if current is None:
                    break  # playlist exhausted

                if self._debug:
                    idx = self._playlist.current_index + 1
                    total = len(self._playlist)
                    print(f"[playlist] Track {idx}/{total}: {current.name}")

                result = self._play_track(current, stop_event)
                self._tracks_played += 1

                if result == "stopped":
                    break
                elif result == "next" or result == "finished":
                    next_track = self._playlist.next()
                    if next_track is None:
                        break  # exhausted (or no repeat)
                    # Precompile upcoming from new position
                    self._executor.submit(self._precompile_upcoming)
                elif result == "prev":
                    self._playlist.prev()
                    self._executor.submit(self._precompile_upcoming)
        except KeyboardInterrupt:
            pass

        # Cleanup
        self._executor.shutdown(wait=False)
        self._multi_adapter.deactivate()

        summary = {
            "mode": "local_playlist",
            "total_tracks": len(self._playlist),
            "tracks_played": self._tracks_played,
            "tracks_skipped": self._tracks_skipped,
            "precompiled": self._precompiled,
            "cache_hits": self._session._cache_hits,
            "cache_misses": self._session._cache_misses,
            "compile_errors": self._session._compile_errors,
        }

        if self._debug:
            print(f"[playlist] Done. Played {self._tracks_played}/{len(self._playlist)} tracks.")

        return summary

    def signal_next(self) -> None:
        """Signal to skip to the next track (thread-safe)."""
        self._signal_next.set()

    def signal_prev(self) -> None:
        """Signal to go to the previous track (thread-safe)."""
        self._signal_prev.set()

    def _play_track(
        self, audio_path: Path, stop_event: threading.Event,
    ) -> str:
        """Play a single track. Returns stop reason: 'finished', 'next', 'prev', 'stopped'."""
        # Clear any pending signals
        self._signal_next.clear()
        self._signal_prev.clear()

        # Compile show
        timeline = self._session.load_track(audio_path)
        if timeline is None:
            logger.warning("playlist: skipping '%s' (compilation failed)", audio_path.name)
            self._tracks_skipped += 1
            return "next"  # skip to next track

        # Create AudioPlayer
        player = AudioPlayer(
            audio_path,
            sample_rate=self._sample_rate,
            device=self._audio_device,
        )

        # Create runtime
        runtime = ShowPlaybackRuntime(timeline, self._multi_adapter)

        # Play
        player.play()

        try:
            while not stop_event.is_set():
                # Check control signals
                if self._signal_next.is_set():
                    return "next"
                if self._signal_prev.is_set():
                    return "prev"

                if player.finished:
                    return "finished"

                if not player.playing:
                    time.sleep(0.05)
                    continue

                t = player.position_seconds
                runtime.tick(t)
                time.sleep(0.005)
        finally:
            player.stop()

        return "stopped"

    def _precompile_upcoming(self) -> None:
        """Compile next 1-2 tracks in background thread."""
        upcoming = self._playlist.peek_next(count=2)
        for track_path in upcoming:
            try:
                from dreamsync.playlist import content_hash_track_id
                track_id = content_hash_track_id(track_path)
                if not self._cache.has(track_id, self._profile):
                    timeline = self._session.load_track(track_path)
                    if timeline is not None:
                        self._precompiled += 1
                        logger.info("playlist: precompiled '%s'", track_path.name)
            except Exception as exc:
                logger.warning("playlist: precompile failed for '%s': %s", track_path.name, exc)


# ---------------------------------------------------------------------------
# D7.2 — Top-level entry point
# ---------------------------------------------------------------------------


def run_local_session(
    multi_adapter,
    audio_path: Path | str,
    *,
    cache_dir: str = "~/.dreamsync/cache",
    profile: ProfileConfig | None = None,
    sample_rate: int = 44100,
    audio_device: int | None = None,
    stop_event: threading.Event,
    debug: bool = False,
    playlist: PlaylistManager | None = None,
) -> dict[str, Any]:
    """Top-level entry point for local session. Called from run_session() or CLI."""
    cache = ShowCache(cache_dir)

    if playlist is not None and len(playlist) > 1:
        session = LocalPlaylistSession(
            multi_adapter,
            playlist,
            cache=cache,
            profile=profile,
            sample_rate=sample_rate,
            audio_device=audio_device,
            debug=debug,
        )
        return session.run(stop_event)
    else:
        session = LocalShowSession(
            multi_adapter,
            cache=cache,
            profile=profile,
            sample_rate=sample_rate,
            audio_device=audio_device,
            debug=debug,
        )
        return session.run(Path(audio_path), stop_event)
