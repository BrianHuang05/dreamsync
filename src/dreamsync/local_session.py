"""Local Show Session — offline playback with pre-compiled timelines."""

from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from dreamsync.analyzer.analyze import analyze_song
from dreamsync.analyzer.models import SongStructure
from dreamsync.cache import ShowCache, cached_compile_show, path_based_track_id
from dreamsync.playlist import PlaylistManager
from dreamsync.show.models import ShowTimeline
from dreamsync.show.player import AudioPlayer
from dreamsync.show.runtime import ShowPlaybackRuntime
from dreamsync.show.runtime_control import RuntimeControlBus, runtime_control_to_dict

if TYPE_CHECKING:
    from dreamsync.profile import ProfileConfig

logger = logging.getLogger(__name__)
ProfileResolver = Callable[[Path, "ProfileConfig | None"], "ProfileConfig | None"]
TimelineResolver = Callable[[Path, ShowTimeline], ShowTimeline]
_ANALYSIS_CACHE_VERSION = 5


def _format_audio_output_label(audio_device: int | None) -> str:
    return "system default" if audio_device is None else f"device #{audio_device}"


def _device_status_for_adapter(adapter) -> str:
    custom_status = getattr(adapter, "device_status_label", None)
    if custom_status:
        return str(custom_status)
    devices = getattr(adapter, "devices", []) or []
    if not devices:
        return "preview mode (no connected devices)"
    labels = []
    for device in devices:
        name = getattr(device, "name", None) or getattr(device, "address", None) or "device"
        labels.append(str(name))
    return ", ".join(labels)


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
        profile_resolver: ProfileResolver | None = None,
        timeline_resolver: TimelineResolver | None = None,
        precompiled_timelines: dict[str, ShowTimeline] | None = None,
        precompiled_timeline_sources: dict[str, str] | None = None,
        sample_rate: int = 44100,
        audio_device: int | None = None,
        debug: bool = False,
        runtime_control: RuntimeControlBus | None = None,
    ) -> None:
        self._multi_adapter = multi_adapter
        self._cache = cache
        self._profile = profile
        self._profile_resolver = profile_resolver
        self._timeline_resolver = timeline_resolver
        self._precompiled_timelines = {
            str(Path(path).resolve()): timeline
            for path, timeline in (precompiled_timelines or {}).items()
        }
        self._timeline_sources = {
            path: str((precompiled_timeline_sources or {}).get(path) or "saved/precompiled Show timeline")
            for path in self._precompiled_timelines
        }
        self._sample_rate = sample_rate
        self._audio_device = audio_device
        self._debug = debug
        self._runtime_control = runtime_control or RuntimeControlBus()

        # Stats
        self._cache_hits: int = 0
        self._cache_misses: int = 0
        self._compile_errors: int = 0
        self._status_lock = threading.RLock()
        self._current_track: Path | None = None
        self._current_player: AudioPlayer | None = None
        self._current_timeline: ShowTimeline | None = None
        self._current_runtime: ShowPlaybackRuntime | None = None
        self._current_timeline_source: str = ""
        self._playback_state: str = "idle"

    def _set_playback_state(self, state: str, *, track: Path | None = None) -> None:
        with self._status_lock:
            if track is not None:
                self._current_track = track
            self._playback_state = state

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
        self._set_playback_state("preparing", track=audio_path)

        # 2. Compile show (cache-aware)
        timeline = self._compile_for_file(audio_path)
        if timeline is None:
            self._set_playback_state("compile_failed", track=audio_path)
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
        self._set_playback_state("loading_audio", track=audio_path)
        player = AudioPlayer(
            audio_path,
            sample_rate=self._sample_rate,
            device=self._audio_device,
        )

        # 4. Create ShowPlaybackRuntime
        runtime = ShowPlaybackRuntime(
            timeline,
            self._multi_adapter,
            control_state_getter=self._runtime_control.snapshot,
        )
        with self._status_lock:
            self._current_track = audio_path
            self._current_player = player
            self._current_timeline = timeline
            self._current_runtime = runtime
            self._current_timeline_source = self.timeline_source_for_file(audio_path)
            self._playback_state = "starting_audio"

        # 5. Start playback
        player.play()
        with self._status_lock:
            self._playback_state = "playing"
        if self._debug:
            print(f"[local] Playing: {audio_path.name} ({timeline.duration:.1f}s, {len(timeline.cues)} cues)")

        # 6. Main tick loop
        frames_sent = 0
        try:
            while not stop_event.is_set():
                if player.finished:
                    with self._status_lock:
                        self._playback_state = "finished"
                    break

                if not player.playing:
                    with self._status_lock:
                        self._playback_state = "paused"
                    time.sleep(0.05)  # 20 Hz idle (paused)
                    continue

                with self._status_lock:
                    self._playback_state = "playing"
                t = player.position_seconds
                sent = runtime.tick(t)
                if sent:
                    frames_sent += 1
                time.sleep(0.005)  # ~200 Hz
        except KeyboardInterrupt:
            pass

        # 7. Cleanup
        player.stop()
        with self._status_lock:
            if self._playback_state != "finished":
                self._playback_state = "stopped"
            self._current_player = None
            self._current_runtime = None
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

    def session_snapshot(self) -> dict[str, Any]:
        with self._status_lock:
            player = self._current_player
            timeline = self._current_timeline
            track = self._current_track
            state = self._playback_state
            runtime = self._current_runtime
            position_seconds = player.position_seconds if player is not None else 0.0
            is_playing = player.playing if player is not None else False
            duration_seconds = timeline.duration if timeline is not None else 0.0
            current_cue = runtime.current_cue if runtime is not None else None
        return {
            "mode": "local",
            "current_track": track,
            "current_index": 0 if track is not None else -1,
            "tracks_played": 0,
            "tracks_skipped": 0,
            "precompiled": 0,
            "queue": {"current_index": 0 if track is not None else -1, "tracks": (track,) if track is not None else ()},
            "playback_state": state,
            "is_playing": is_playing,
            "position_seconds": position_seconds,
            "duration_seconds": duration_seconds,
            "timeline_source": self._current_timeline_source,
            "audio_output": _format_audio_output_label(self._audio_device),
            "device_status": _device_status_for_adapter(self._multi_adapter),
            "runtime_control": runtime_control_to_dict(self._runtime_control.snapshot()),
            "current_render_mode": current_cue.render_mode if current_cue is not None else "",
            "current_palette": tuple(current_cue.color_palette) if current_cue is not None else (),
            "runtime_state": {
                "active_eq_routes": tuple(
                    current_cue.params.get("active_eq_routes", ())
                    if current_cue is not None
                    else ()
                ),
                "active_instrument_routes": tuple(
                    current_cue.params.get("active_instrument_routes", ())
                    if current_cue is not None
                    else ()
                ),
                "active_scene_layers": tuple(
                    current_cue.params.get(
                        "scene_layers",
                        current_cue.params.get("eq_layers", ()),
                    )
                    if current_cue is not None
                    else ()
                ),
            },
        }

    def pause(self) -> bool:
        """Pause the active local track without stopping its show runtime."""

        with self._status_lock:
            player = self._current_player
            if player is None or not player.playing:
                return False
            player.pause()
            self._playback_state = "paused"
        return True

    def resume(self) -> bool:
        """Resume a paused local track from its audio-clock position."""

        with self._status_lock:
            player = self._current_player
            if player is None or player.playing or player.finished:
                return False
            player.play()
            self._playback_state = "playing"
        return True

    def toggle_pause(self) -> str:
        with self._status_lock:
            player = self._current_player
            if player is None:
                return "unsupported"
            is_playing = bool(player.playing)
        if is_playing:
            return "paused" if self.pause() else "unsupported"
        return "playing" if self.resume() else "unsupported"

    def preview_frame_snapshot(self) -> dict[str, Any]:
        preview_snapshot = getattr(self._multi_adapter, "preview_snapshot", None)
        if callable(preview_snapshot):
            return dict(preview_snapshot())
        return {"node_colors": {}}

    def update_runtime_control(self, **changes: Any) -> dict[str, Any]:
        return runtime_control_to_dict(self._runtime_control.update(**changes))

    def clear_runtime_control(self) -> dict[str, Any]:
        return runtime_control_to_dict(self._runtime_control.clear())

    def runtime_control_snapshot(self) -> dict[str, Any]:
        return runtime_control_to_dict(self._runtime_control.snapshot())

    def load_track(self, audio_path: Path | str) -> ShowTimeline | None:
        """Compile (or retrieve from cache) a show for a track.
        Public API for Feature 8 (PlaylistManager) to precompile tracks."""
        return self._compile_for_file(Path(audio_path))

    def prepared_timeline_for_file(self, audio_path: Path | str) -> ShowTimeline | None:
        """Return a timeline already prepared for this session without compiling."""

        return self._precompiled_timelines.get(str(Path(audio_path).resolve()))

    def timeline_source_for_file(self, audio_path: Path | str) -> str:
        """Return the provenance of a prepared timeline without compiling it."""

        return self._timeline_sources.get(str(Path(audio_path).resolve()), "")

    def replace_prepared_timeline(
        self,
        audio_path: Path | str,
        timeline: ShowTimeline,
        *,
        source: str = "prepared timeline",
    ) -> None:
        """Atomically replace a prepared future timeline with a retinted version."""

        with self._status_lock:
            key = str(Path(audio_path).resolve())
            self._precompiled_timelines[key] = timeline
            self._timeline_sources[key] = source

    def _compile_for_file(self, audio_path: Path) -> ShowTimeline | None:
        """Load a persisted timeline or fall back to cache-aware compilation."""
        persisted = self._precompiled_timelines.get(str(audio_path.resolve()))
        if persisted is not None:
            self._set_playback_state("loading_saved_track", track=audio_path)
            return self._resolve_timeline_for_track(audio_path, persisted)
        track_id = self._track_id_for_file(audio_path)
        effective_profile = self._effective_profile_for_track(audio_path)

        # 1. Check cache
        if self._cache.has(track_id, effective_profile):
            self._set_playback_state("loading_cached_show", track=audio_path)
            timeline = self._cache.get(track_id, effective_profile)
            if timeline is not None:
                self._cache_hits += 1
                logger.info("local: cache HIT for '%s'", audio_path.name)
                if self._debug:
                    print(f"[local] Cache hit: {audio_path.name}")
                self.replace_prepared_timeline(
                    audio_path,
                    timeline,
                    source=f"show cache: {self._cache.entry_path(track_id, effective_profile)}",
                )
                return self._resolve_timeline_for_track(audio_path, timeline)

        structure = self._load_analysis_sidecar(audio_path)
        if structure is not None:
            self._set_playback_state("loading_cached_analysis", track=audio_path)
            try:
                timeline, from_cache = cached_compile_show(
                    structure,
                    effective_profile,
                    cache=self._cache,
                    track_id=track_id,
                )
                if from_cache:
                    self._cache_hits += 1
                else:
                    self._cache_misses += 1
                logger.info("local: compiled '%s' from cached analysis (%d cues)", audio_path.name, len(timeline.cues))
                self.replace_prepared_timeline(
                    audio_path,
                    timeline,
                    source=f"analysis sidecar: {self._analysis_sidecar_path(audio_path)}",
                )
                return self._resolve_timeline_for_track(audio_path, timeline)
            except Exception as exc:
                logger.warning("local: cached analysis unusable for '%s': %s", audio_path.name, exc)

        # 2. Analyze + compile
        try:
            self._set_playback_state("analyzing", track=audio_path)
            logger.info("local: analyzing '%s'", audio_path.name)
            if self._debug:
                print(f"[local] Analyzing: {audio_path.name}...")

            structure = self._with_analysis_cache_metadata(analyze_song(audio_path))
            self._write_analysis_sidecar(audio_path, structure)
            self._set_playback_state("compiling_show", track=audio_path)
            timeline, from_cache = cached_compile_show(
                structure,
                effective_profile,
                cache=self._cache,
                track_id=track_id,
            )
            if from_cache:
                self._cache_hits += 1
            else:
                self._cache_misses += 1
            logger.info("local: compiled '%s' (%d cues)", audio_path.name, len(timeline.cues))
            self.replace_prepared_timeline(
                audio_path,
                timeline,
                source=(
                    f"show cache: {self._cache.entry_path(track_id, effective_profile)}"
                    if from_cache
                    else f"hot compile from {audio_path.name}"
                ),
            )
            return self._resolve_timeline_for_track(audio_path, timeline)
        except Exception as exc:
            self._compile_errors += 1
            logger.error("local: compile failed for '%s': %s", audio_path.name, exc)
            if self._debug:
                print(f"[local] Error compiling '{audio_path.name}': {exc}")
            return None

    @staticmethod
    def _analysis_sidecar_path(audio_path: Path) -> Path:
        return audio_path.with_suffix(".analysis.json")

    def _load_analysis_sidecar(self, audio_path: Path) -> SongStructure | None:
        path = self._analysis_sidecar_path(audio_path)
        if not path.exists():
            return None
        try:
            structure = SongStructure.from_json(path)
        except Exception as exc:
            logger.warning("local: ignoring corrupt analysis sidecar for '%s': %s", audio_path.name, exc)
            return None
        version = structure.metadata.get("_analysis_cache_version")
        if version is not None and int(version) != _ANALYSIS_CACHE_VERSION:
            logger.info(
                "local: analysis cache version mismatch for '%s' (found %s, expected %s)",
                audio_path.name,
                version,
                _ANALYSIS_CACHE_VERSION,
            )
            return None
        return structure

    def _write_analysis_sidecar(self, audio_path: Path, structure: SongStructure) -> None:
        path = self._analysis_sidecar_path(audio_path)
        try:
            path.write_text(json.dumps(structure.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.warning("local: failed to write analysis sidecar for '%s': %s", audio_path.name, exc)

    @staticmethod
    def _with_analysis_cache_metadata(structure: SongStructure) -> SongStructure:
        metadata = dict(structure.metadata)
        metadata["_analysis_cache_version"] = _ANALYSIS_CACHE_VERSION
        return SongStructure(
            path=structure.path,
            duration=structure.duration,
            bpm=structure.bpm,
            time_signature=structure.time_signature,
            beat_grid=structure.beat_grid,
            tempo_regions=structure.tempo_regions,
            sections=structure.sections,
            metadata=metadata,
            phrases=structure.phrases,
            instrument_events=structure.instrument_events,
            instrument_proxies=structure.instrument_proxies,
        )

    def _track_id_for_file(self, audio_path: Path) -> str:
        """Generate a cache-safe track ID from a file path."""
        return path_based_track_id(audio_path)

    def _effective_profile_for_track(self, audio_path: Path) -> ProfileConfig | None:
        if self._profile_resolver is None:
            return self._profile
        return self._profile_resolver(audio_path, self._profile)

    def _resolve_timeline_for_track(self, audio_path: Path, timeline: ShowTimeline) -> ShowTimeline:
        if self._timeline_resolver is None:
            return timeline
        return self._timeline_resolver(audio_path, timeline)


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
        profile_resolver: ProfileResolver | None = None,
        timeline_resolver: TimelineResolver | None = None,
        precompiled_timelines: dict[str, ShowTimeline] | None = None,
        precompiled_timeline_sources: dict[str, str] | None = None,
        sample_rate: int = 44100,
        audio_device: int | None = None,
        debug: bool = False,
        runtime_control: RuntimeControlBus | None = None,
    ) -> None:
        self._multi_adapter = multi_adapter
        self._playlist = playlist
        self._cache = cache
        self._profile = profile
        self._profile_resolver = profile_resolver
        self._timeline_resolver = timeline_resolver
        self._precompiled_timelines = dict(precompiled_timelines or {})
        self._sample_rate = sample_rate
        self._audio_device = audio_device
        self._debug = debug
        self._runtime_control = runtime_control or RuntimeControlBus()

        # Control signals
        self._signal_next = threading.Event()
        self._signal_prev = threading.Event()
        self._signal_jump = threading.Event()
        self._control_lock = threading.RLock()
        self._pending_jump_index: int | None = None

        # Background precompilation
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="precompile")

        # Inner session (reused across tracks)
        self._session = LocalShowSession(
            multi_adapter,
            cache=cache,
            profile=profile,
            profile_resolver=profile_resolver,
            timeline_resolver=timeline_resolver,
            precompiled_timelines=precompiled_timelines,
            precompiled_timeline_sources=precompiled_timeline_sources,
            sample_rate=sample_rate,
            audio_device=audio_device,
            debug=debug,
            runtime_control=self._runtime_control,
        )

        # Stats
        self._tracks_played: int = 0
        self._tracks_skipped: int = 0
        self._precompiled: int = 0
        self._status_lock = threading.RLock()
        self._current_track: Path | None = None
        self._current_player: AudioPlayer | None = None
        self._current_timeline: ShowTimeline | None = None
        self._current_runtime: ShowPlaybackRuntime | None = None
        self._current_timeline_source: str = ""
        self._playback_state: str = "idle"

    def _set_playback_state(self, state: str, *, track: Path | None = None) -> None:
        with self._status_lock:
            if track is not None:
                self._current_track = track
            self._playback_state = state

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
                elif result == "jump":
                    target = self._consume_jump_target()
                    if target is None:
                        continue
                    self._playlist.jump_to(target)
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

    def skip_current(self) -> None:
        """Public alias for GUI and controller code."""
        self.signal_next()

    def queue_snapshot(self) -> dict[str, Any]:
        """Return a stable snapshot of the current queue state."""
        tracks = self._playlist.snapshot()
        current_index = self._playlist.current_index
        return {
            "current_index": current_index,
            "tracks": tracks,
        }

    def session_snapshot(self) -> dict[str, Any]:
        """Return a lightweight status snapshot for controller/service consumers."""
        with self._status_lock:
            player = self._current_player
            timeline = self._current_timeline
            current_track = self._current_track
            playback_state = self._playback_state
            runtime = self._current_runtime
            position_seconds = player.position_seconds if player is not None else 0.0
            is_playing = player.playing if player is not None else False
            duration_seconds = timeline.duration if timeline is not None else 0.0
            current_cue = runtime.current_cue if runtime is not None else None
        return {
            "current_track": current_track or self._playlist.current,
            "current_index": self._playlist.current_index,
            "tracks_played": self._tracks_played,
            "tracks_skipped": self._tracks_skipped,
            "precompiled": self._precompiled,
            "queue": self.queue_snapshot(),
            "playback_state": playback_state,
            "is_playing": is_playing,
            "position_seconds": position_seconds,
            "duration_seconds": duration_seconds,
            "timeline_source": self._current_timeline_source,
            "audio_output": _format_audio_output_label(self._audio_device),
            "device_status": _device_status_for_adapter(self._multi_adapter),
            "runtime_control": runtime_control_to_dict(self._runtime_control.snapshot()),
            "current_render_mode": current_cue.render_mode if current_cue is not None else "",
            "current_palette": tuple(current_cue.color_palette) if current_cue is not None else (),
            "runtime_state": {
                "active_eq_routes": tuple(
                    current_cue.params.get("active_eq_routes", ())
                    if current_cue is not None
                    else ()
                ),
                "active_instrument_routes": tuple(
                    current_cue.params.get("active_instrument_routes", ())
                    if current_cue is not None
                    else ()
                ),
                "active_scene_layers": tuple(
                    current_cue.params.get(
                        "scene_layers",
                        current_cue.params.get("eq_layers", ()),
                    )
                    if current_cue is not None
                    else ()
                ),
            },
        }

    def pause(self) -> bool:
        """Pause the current playlist track without disturbing queue state."""

        with self._status_lock:
            player = self._current_player
            if player is None or not player.playing:
                return False
            player.pause()
            self._playback_state = "paused"
        return True

    def resume(self) -> bool:
        """Resume the current playlist track from its audio-clock position."""

        with self._status_lock:
            player = self._current_player
            if player is None or player.playing or player.finished:
                return False
            player.play()
            self._playback_state = "playing"
        return True

    def toggle_pause(self) -> str:
        with self._status_lock:
            player = self._current_player
            if player is None:
                return "unsupported"
            is_playing = bool(player.playing)
        if is_playing:
            return "paused" if self.pause() else "unsupported"
        return "playing" if self.resume() else "unsupported"

    def preview_frame_snapshot(self) -> dict[str, Any]:
        preview_snapshot = getattr(self._multi_adapter, "preview_snapshot", None)
        if callable(preview_snapshot):
            return dict(preview_snapshot())
        return {"node_colors": {}}

    def update_runtime_control(self, **changes: Any) -> dict[str, Any]:
        return runtime_control_to_dict(self._runtime_control.update(**changes))

    def clear_runtime_control(self) -> dict[str, Any]:
        return runtime_control_to_dict(self._runtime_control.clear())

    def runtime_control_snapshot(self) -> dict[str, Any]:
        return runtime_control_to_dict(self._runtime_control.snapshot())

    def remove_track(self, index: int) -> Path:
        """Remove an upcoming track from the queue."""
        with self._control_lock:
            current_index = self._playlist.current_index
            if index == current_index:
                raise ValueError("Cannot remove the currently playing track; use next instead.")
            if index < current_index:
                raise ValueError("Cannot remove a track that has already been played.")
            return self._playlist.remove(index)

    def move_track(self, from_index: int, to_index: int) -> None:
        """Reorder an upcoming track within the queue."""
        with self._control_lock:
            current_index = self._playlist.current_index
            if from_index <= current_index or to_index <= current_index:
                raise ValueError("Only upcoming tracks can be reordered.")
            self._playlist.move(from_index, to_index)

    def shuffle_queue(self) -> None:
        """Shuffle only the remaining upcoming tracks."""
        with self._control_lock:
            self._playlist.shuffle_upcoming()

    def set_repeat(self, enabled: bool) -> None:
        """Enable or disable whole-playlist repeat."""
        with self._control_lock:
            self._playlist.set_repeat(enabled)

    def append_track(self, track: Path | str) -> Path:
        """Append a new track to the queue and return the normalized path."""
        with self._control_lock:
            return self._playlist.append(track)

    def insert_track(self, index: int, track: Path | str) -> Path:
        """Insert a track into the upcoming queue and return the normalized path."""
        with self._control_lock:
            current_index = self._playlist.current_index
            if index <= current_index:
                index = current_index + 1
            return self._playlist.insert(index, track)

    def request_precompile_upcoming(self) -> bool:
        """Schedule preparation for a newly cued upcoming track when running."""
        with self._status_lock:
            active = self._playback_state in {"starting_audio", "playing", "paused"}
        if not active:
            return False
        self._executor.submit(self._precompile_upcoming)
        return True

    def set_precompiled_timeline(
        self,
        audio_path: Path | str,
        timeline: ShowTimeline,
        *,
        source: str,
    ) -> None:
        """Register a saved timeline for a queued track without recompiling it."""

        normalized_path = Path(audio_path).resolve()
        with self._control_lock:
            self._precompiled_timelines[str(normalized_path)] = timeline
            self._session.replace_prepared_timeline(
                normalized_path,
                timeline,
                source=source,
            )

    def play_now(self, index: int) -> None:
        """Interrupt the current track and jump to the selected queue entry."""
        with self._control_lock:
            self._playlist.snapshot()[index]
            current_index = self._playlist.current_index
            if index < current_index:
                raise ValueError("Cannot jump to a track that has already been played.")
            if index == current_index:
                return
            self._pending_jump_index = index
            self._signal_jump.set()

    def prepared_timeline(self, index: int) -> ShowTimeline | None:
        """Return a compiled future track without triggering analysis or compilation."""

        with self._control_lock:
            tracks = self._playlist.snapshot()
            current_index = self._playlist.current_index
            if index <= current_index or not 0 <= index < len(tracks):
                return None
            return self._session.prepared_timeline_for_file(tracks[index])

    def prepared_timeline_source(self, index: int) -> str:
        """Return the source of an already prepared upcoming timeline."""

        with self._control_lock:
            tracks = self._playlist.snapshot()
            current_index = self._playlist.current_index
            if index <= current_index or not 0 <= index < len(tracks):
                return ""
            return self._session.timeline_source_for_file(tracks[index])

    def replace_prepared_timeline(self, index: int, timeline: ShowTimeline) -> None:
        """Replace a future compiled track after a palette-only retint."""

        with self._control_lock:
            tracks = self._playlist.snapshot()
            current_index = self._playlist.current_index
            if index <= current_index:
                raise ValueError("Cannot edit the currently playing track.")
            if not 0 <= index < len(tracks):
                raise IndexError("Track index out of range.")
            self._session.replace_prepared_timeline(
                tracks[index],
                timeline,
                source="Live palette hot-swap (prepared cues)",
            )

    def _play_track(
        self, audio_path: Path, stop_event: threading.Event,
    ) -> str:
        """Play a single track. Returns stop reason: 'finished', 'next', 'prev', 'stopped'."""
        # Compile show
        self._set_playback_state("preparing", track=audio_path)
        timeline = self._session.load_track(audio_path)
        if timeline is None:
            self._set_playback_state("compile_failed", track=audio_path)
            logger.warning("playlist: skipping '%s' (compilation failed)", audio_path.name)
            self._tracks_skipped += 1
            return "next"  # skip to next track

        # Keep commands made while a track was preparing. In particular, this
        # makes Skip work even if the user presses it before decoding completes.
        if self._signal_next.is_set():
            self._signal_next.clear()
            self._tracks_skipped += 1
            self._set_playback_state("skipping", track=audio_path)
            return "next"
        if self._signal_prev.is_set():
            self._signal_prev.clear()
            self._set_playback_state("rewinding", track=audio_path)
            return "prev"
        if self._signal_jump.is_set():
            self._set_playback_state("jumping", track=audio_path)
            return "jump"

        # Create AudioPlayer
        self._set_playback_state("loading_audio", track=audio_path)
        player = AudioPlayer(
            audio_path,
            sample_rate=self._sample_rate,
            device=self._audio_device,
        )

        # Create runtime
        runtime = ShowPlaybackRuntime(
            timeline,
            self._multi_adapter,
            control_state_getter=self._runtime_control.snapshot,
        )
        with self._status_lock:
            self._current_track = audio_path
            self._current_player = player
            self._current_timeline = timeline
            self._current_runtime = runtime
            self._current_timeline_source = self._session.timeline_source_for_file(audio_path)
            self._playback_state = "starting_audio"

        # Play
        player.play()
        with self._status_lock:
            self._playback_state = "playing"

        try:
            while not stop_event.is_set():
                # Check control signals
                if self._signal_next.is_set():
                    self._signal_next.clear()
                    self._tracks_skipped += 1
                    with self._status_lock:
                        self._playback_state = "skipping"
                    return "next"
                if self._signal_prev.is_set():
                    self._signal_prev.clear()
                    with self._status_lock:
                        self._playback_state = "rewinding"
                    return "prev"
                if self._signal_jump.is_set():
                    with self._status_lock:
                        self._playback_state = "jumping"
                    return "jump"

                if player.finished:
                    with self._status_lock:
                        self._playback_state = "finished"
                    return "finished"

                if not player.playing:
                    with self._status_lock:
                        self._playback_state = "paused"
                    time.sleep(0.05)
                    continue

                with self._status_lock:
                    self._playback_state = "playing"
                t = player.position_seconds
                runtime.tick(t)
                time.sleep(0.005)
        finally:
            player.stop()
            with self._status_lock:
                if self._playback_state not in {"finished", "jumping", "skipping", "rewinding"}:
                    self._playback_state = "stopped"
                self._current_player = None
                self._current_runtime = None

        return "stopped"

    def _precompile_upcoming(self) -> None:
        """Compile next 1-2 tracks in background thread."""
        upcoming = self._playlist.peek_next(count=2)
        for track_path in upcoming:
            try:
                if str(track_path.resolve()) in self._session._precompiled_timelines:
                    continue
                from dreamsync.playlist import content_hash_track_id
                track_id = content_hash_track_id(track_path)
                effective_profile = self._session._effective_profile_for_track(track_path)
                if not self._cache.has(track_id, effective_profile):
                    timeline = self._session.load_track(track_path)
                    if timeline is not None:
                        self._precompiled += 1
                        logger.info("playlist: precompiled '%s'", track_path.name)
            except Exception as exc:
                logger.warning("playlist: precompile failed for '%s': %s", track_path.name, exc)

    def _consume_jump_target(self) -> int | None:
        with self._control_lock:
            target = self._pending_jump_index
            self._pending_jump_index = None
            self._signal_jump.clear()
            return target


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
    profile_resolver: ProfileResolver | None = None,
    timeline_resolver: TimelineResolver | None = None,
    debug: bool = False,
    playlist: PlaylistManager | None = None,
    session_ref: list[Any] | None = None,
    runtime_control: RuntimeControlBus | None = None,
    precompiled_timelines: dict[str, ShowTimeline] | None = None,
    precompiled_timeline_sources: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Top-level entry point for local session. Called from run_session() or CLI."""
    cache = ShowCache(cache_dir)

    if playlist is not None and len(playlist) > 1:
        session = LocalPlaylistSession(
            multi_adapter,
            playlist,
            cache=cache,
            profile=profile,
            profile_resolver=profile_resolver,
            timeline_resolver=timeline_resolver,
            precompiled_timelines=precompiled_timelines,
            precompiled_timeline_sources=precompiled_timeline_sources,
            sample_rate=sample_rate,
            audio_device=audio_device,
            debug=debug,
            runtime_control=runtime_control,
        )
        if session_ref is not None:
            session_ref[:] = [session]
        return session.run(stop_event)
    else:
        session = LocalShowSession(
            multi_adapter,
            cache=cache,
            profile=profile,
            profile_resolver=profile_resolver,
            timeline_resolver=timeline_resolver,
            precompiled_timelines=precompiled_timelines,
            precompiled_timeline_sources=precompiled_timeline_sources,
            sample_rate=sample_rate,
            audio_device=audio_device,
            debug=debug,
            runtime_control=runtime_control,
        )
        if session_ref is not None:
            session_ref[:] = [session]
        return session.run(Path(audio_path), stop_event)


def run_precompiled_show_session(
    multi_adapter,
    tracks: tuple[tuple[Path, ShowTimeline], ...],
    *,
    source_path: Path | None = None,
    audio_device: int | None = None,
    stop_event: threading.Event,
    session_ref: list[Any] | None = None,
    runtime_control: RuntimeControlBus | None = None,
) -> dict[str, Any]:
    """Play an ordered saved Show using its embedded compiled Track timelines."""
    if not tracks:
        raise ValueError("A Show needs at least one Track before playback.")
    playlist = PlaylistManager.from_tracks([audio_path for audio_path, _timeline in tracks])
    timelines = {str(audio_path.resolve()): timeline for audio_path, timeline in tracks}
    source_label = f"saved Show: {source_path.resolve()}" if source_path is not None else "saved/precompiled Show timeline"
    timeline_sources = {path: source_label for path in timelines}
    return run_local_session(
        multi_adapter,
        playlist.current,
        audio_device=audio_device,
        stop_event=stop_event,
        playlist=playlist,
        session_ref=session_ref,
        runtime_control=runtime_control,
        precompiled_timelines=timelines,
        precompiled_timeline_sources=timeline_sources,
    )
