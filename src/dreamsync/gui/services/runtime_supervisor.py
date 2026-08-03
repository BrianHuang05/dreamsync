"""Runtime supervisor for GUI control-room orchestration."""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from dreamsync.cache import ShowCache
from dreamsync.capture.orchestrator import CaptureOrchestrator, OrchestratorConfig
from dreamsync.gui.models.capture_settings import CaptureSettings, LearnedLiveSettings
from dreamsync.gui.models.reactive_settings import ReactiveSettings
from dreamsync.gui.models.runtime_mode_state import CapturedShowItem, OutputLease, RuntimeModeState
from dreamsync.gui.models.runtime_routing_state import OutputTarget, RuntimeRoutingState
from dreamsync.gui.services.audio_device_service import AudioDeviceService
from dreamsync.gui.services.session_service import SessionHandle, SessionService
from dreamsync.show.models import Show, ShowTimeline
from dreamsync.show_pipeline_worker import ShowPipelineWorker
from dreamsync.spotify.learned_live_session import SpotifyLearnedLiveSession
from dreamsync.spotify.learned_track import LearnedTrackStore


class PipelineCoordinator:
    """Manage capture + compile state independently from active output."""

    def __init__(
        self,
        *,
        capture_factory=CaptureOrchestrator,
        worker_factory=ShowPipelineWorker,
        cache_factory=ShowCache,
    ) -> None:
        self._capture_factory = capture_factory
        self._worker_factory = worker_factory
        self._cache_factory = cache_factory
        self._lock = threading.RLock()
        self._capture = None
        self._worker = None
        self._ready_queue: queue.Queue | None = None
        self._items: dict[str, CapturedShowItem] = {}
        self._timelines: dict[str, ShowTimeline] = {}
        self._order: list[str] = []
        self._running = False
        self._timing_fetcher: Callable[[], dict | None] | None = None
        self._capture_invalidation_reason = ""
        self._learning_state_callback = None
        self._item_track_ids: dict[str, str] = {}

    @property
    def running(self) -> bool:
        return self._running

    def start(
        self,
        *,
        capture_dir: Path,
        profile=None,
        sample_rate: int = 44100,
        capture_naming: str = "timestamp",
        capture_buffer: int = 0,
        device_pattern: str = "CABLE Output",
        debug: bool = False,
        learned_live: bool = False,
        retention_policy: str = "keep_recent",
        retained_mp3_limit: int = 10,
    ) -> None:
        if self._running:
            return
        ready_queue: queue.Queue = queue.Queue()
        worker = self._worker_factory(
            cache=self._cache_factory("~/.dreamsync/cache"),
            profile=profile,
            sample_rate=sample_rate,
            ready_queue=ready_queue,
            debug=debug,
            state_callback=self._on_worker_state,
            max_workers=1 if learned_live else 2,
            learned_live=learned_live,
            retention_policy=retention_policy,
            retained_mp3_limit=retained_mp3_limit,
        )
        capture = self._capture_factory(
            config=OrchestratorConfig(
                output_dir=str(capture_dir),
                naming=capture_naming,
                max_capture_files=capture_buffer,
                log_dir=str(capture_dir / "logs"),
                device_pattern=device_pattern,
                sample_rate=sample_rate,
            ),
            on_segment_saved=self._on_segment_saved,
        )
        capture.start()
        with self._lock:
            self._capture = capture
            self._worker = worker
            self._ready_queue = ready_queue
            self._running = True
            timing_fetcher = self._timing_fetcher
        if timing_fetcher is not None:
            try:
                self._activate_timing_source(capture, timing_fetcher)
            except Exception:
                self.stop()
                raise

    def stop(self) -> None:
        with self._lock:
            capture = self._capture
            worker = self._worker
            self._capture = None
            self._worker = None
            self._ready_queue = None
            self._running = False
        if capture is not None:
            capture.shutdown()
        if worker is not None:
            worker.shutdown()

    def set_timing_source(self, fetcher: Callable[[], dict | None] | None) -> None:
        """Attach or detach an external timing source for capture splitting."""
        with self._lock:
            self._timing_fetcher = fetcher
            capture = self._capture if self._running else None
        if capture is None:
            return
        capture.stop_periodic_timing()
        if fetcher is not None:
            self._activate_timing_source(capture, fetcher)

    def on_track_change(self, timing_data: dict) -> int:
        """Forward an immediate track boundary to the active capture."""
        with self._lock:
            capture = self._capture if self._running else None
        if capture is None:
            return 0
        return int(capture.on_track_change(timing_data))

    def begin_learning_candidate(self) -> None:
        with self._lock:
            self._capture_invalidation_reason = ""

    def invalidate_learning_candidate(self, reason: str) -> None:
        with self._lock:
            self._capture_invalidation_reason = str(reason or "invalidated")

    def set_learning_state_callback(self, callback) -> None:
        with self._lock:
            self._learning_state_callback = callback

    @staticmethod
    def _activate_timing_source(capture, fetcher: Callable[[], dict | None]) -> None:
        timing_data = fetcher()
        if timing_data is not None:
            capture.update_timing(timing_data)
        capture.start_periodic_timing(fetcher)

    def snapshot_items(self) -> tuple[CapturedShowItem, ...]:
        with self._lock:
            return tuple(self._items[item_id] for item_id in self._order if item_id in self._items)

    def snapshot_state(self) -> str:
        with self._lock:
            if not self._running:
                return "idle"
            worker = self._worker
            ready = sum(1 for item in self._items.values() if item.state in {"ready", "playing"})
        if worker is not None and worker.pending_count() > 0:
            return "processing"
        if ready:
            return "ready"
        return "running"

    def ready_queue_count(self) -> int:
        with self._lock:
            return sum(1 for item in self._items.values() if item.state in {"ready", "playing"})

    def stats(self) -> dict[str, Any]:
        with self._lock:
            capture = self._capture
            worker = self._worker
        capture_stats = dict(getattr(capture, "stats", {})) if capture is not None else {}
        worker_stats = worker.stats() if worker is not None else {}
        return {
            "capture": capture_stats,
            "worker": worker_stats,
        }

    def next_ready_item(self) -> CapturedShowItem | None:
        with self._lock:
            for item_id in self._order:
                item = self._items.get(item_id)
                if item is not None and item.state == "ready":
                    return item
        return None

    def item_for_id(self, item_id: str) -> CapturedShowItem | None:
        with self._lock:
            return self._items.get(item_id)

    def timeline_for_id(self, item_id: str) -> ShowTimeline | None:
        with self._lock:
            return self._timelines.get(item_id)

    def mark_playing(self, item_id: str) -> None:
        with self._lock:
            item = self._items.get(item_id)
            if item is not None:
                self._items[item_id] = replace(item, state="playing", error=None)

    def mark_ready(self, item_id: str) -> None:
        with self._lock:
            item = self._items.get(item_id)
            if item is not None:
                self._items[item_id] = replace(item, state="ready", error=None)

    def discard(self, item_id: str) -> None:
        with self._lock:
            self._items.pop(item_id, None)
            self._timelines.pop(item_id, None)
            self._order = [value for value in self._order if value != item_id]

    def move_to_top(self, item_id: str) -> None:
        with self._lock:
            if item_id not in self._order:
                return
            self._order = [item_id, *[value for value in self._order if value != item_id]]

    def _on_segment_saved(self, mp3_path: str, metadata: dict) -> None:
        metadata = dict(metadata)
        with self._lock:
            invalidation_reason = self._capture_invalidation_reason
            self._capture_invalidation_reason = ""
        if invalidation_reason == "seek_detected":
            metadata["seek_detected"] = True
        elif invalidation_reason == "paused":
            metadata["paused"] = True
        elif invalidation_reason:
            metadata["skipped"] = True
        path = Path(mp3_path)
        item = CapturedShowItem(
            item_id=str(path),
            mp3_path=str(path),
            analysis_path=str(path.with_suffix(".analysis.json")),
            show_path=str(path.with_suffix(".show.json")),
            state="captured",
            title=str(metadata.get("song_title", "") or ""),
            artist=str(metadata.get("artist", "") or ""),
            duration=float(metadata["duration"]) if metadata.get("duration") is not None else None,
            error=None,
        )
        with self._lock:
            if item.item_id not in self._order:
                self._order.append(item.item_id)
            self._items[item.item_id] = item
            track_id = str(metadata.get("spotify_track_id") or "")
            if track_id:
                self._item_track_ids[item.item_id] = track_id
            worker = self._worker
            learning_callback = self._learning_state_callback
        if learning_callback is not None and track_id:
            learning_callback(track_id, "queued", "")
        if worker is not None:
            worker.on_segment_saved(mp3_path, metadata)

    def _on_worker_state(
        self,
        mp3_path: Path,
        state: str,
        *,
        timeline: ShowTimeline | None = None,
        error: Exception | None = None,
    ) -> None:
        item_id = str(mp3_path)
        with self._lock:
            current = self._items.get(item_id)
            if current is None:
                current = CapturedShowItem(item_id=item_id, mp3_path=item_id)
                self._order.append(item_id)
            updated = replace(current, state=state, error=str(error) if error is not None else None)
            self._items[item_id] = updated
            if timeline is not None:
                self._timelines[item_id] = timeline
            track_id = self._item_track_ids.get(item_id, "")
            learning_callback = self._learning_state_callback
        if learning_callback is not None and track_id:
            learning_callback(
                track_id,
                state,
                str(error) if error is not None else "",
            )


class RuntimeSupervisor:
    """Coordinate background producers and the currently active output mode."""

    def __init__(
        self,
        *,
        session_service: SessionService | None = None,
        pipeline_factory=PipelineCoordinator,
        audio_device_service: AudioDeviceService | None = None,
    ) -> None:
        self._session_service = session_service or SessionService()
        self._pipeline_factory = pipeline_factory
        self._audio_device_service = audio_device_service or AudioDeviceService()
        self._pipeline: PipelineCoordinator | None = None
        self._output_handle: SessionHandle | None = None
        self._preview_handle: SessionHandle | None = None
        self._pipeline_current_item_id: str | None = None
        self._pipeline_playback_enabled = False
        self._armed_output_mode = ""
        self._local_source_path = ""
        self._saved_show_path = ""
        self._selected_show_path = ""
        self._reactive_effect_mode = ""
        self._status_message = "Runtime idle."
        self._error_message = ""
        self._started_at = time.monotonic()
        self._warnings: deque[str] = deque(maxlen=12)
        self._events: deque[str] = deque(maxlen=32)
        self._capture_settings = CaptureSettings()
        self._reactive_settings = ReactiveSettings()
        self._learned_live_settings = LearnedLiveSettings()
        self._learned_live_handle: SessionHandle | None = None
        self._learned_live_session: SpotifyLearnedLiveSession | None = None
        self._learned_store: LearnedTrackStore | None = None
        self._baked_playback_mode = "auto"
        self._live_loopback_enabled = False
        self._recent_saved_shows: list[str] = []
        self._timeline_resolver = None
        self._capture_timing_source: Callable[[], dict | None] | None = None
        self._routing_state = RuntimeRoutingState(
            output_target=OutputTarget(mode="simulation", fallback_to_simulation=True),
        )
        self.refresh_audio_devices()

    def refresh_audio_devices(self) -> RuntimeRoutingState:
        self._routing_state = replace(
            self._routing_state,
            available_output_devices=self._audio_device_service.list_output_options(),
            available_input_devices=self._audio_device_service.list_input_options(),
        )
        return self._routing_state

    def set_output_target_mode(self, mode: str) -> None:
        target = replace(self._routing_state.output_target, mode=mode)
        self._routing_state = replace(self._routing_state, output_target=target)
        self._record_event(f"Routing mode set to {mode}.")

    def set_hardware_fallback_to_simulation(self, enabled: bool) -> None:
        target = replace(self._routing_state.output_target, fallback_to_simulation=enabled)
        self._routing_state = replace(self._routing_state, output_target=target)

    def set_selected_output_audio_device(self, device_id: int | None) -> None:
        self._routing_state = replace(self._routing_state, selected_output_audio_device_id=device_id)

    def set_selected_live_input_device(self, device_id: int | None) -> None:
        self._routing_state = replace(self._routing_state, selected_live_input_device_id=device_id)

    def set_config_path(self, config_path: Path | None) -> None:
        target = replace(
            self._routing_state.output_target,
            config_path=str(config_path) if config_path is not None else "",
        )
        self._routing_state = replace(self._routing_state, output_target=target)

    def set_capture_settings(self, settings: CaptureSettings) -> None:
        self._capture_settings = settings

    def set_reactive_settings(self, settings: ReactiveSettings) -> None:
        self._reactive_settings = settings
        session = self.active_session()
        if session is not None and hasattr(
            session,
            "set_predictive_cues_enabled",
        ):
            session.set_predictive_cues_enabled(
                settings.predictive_cues_enabled
                or settings.structure_bar_actions_enabled
                or settings.structure_phrase_actions_enabled
                or settings.structure_section_actions_enabled
            )
        if session is not None and hasattr(
            session,
            "set_structure_action_controls",
        ):
            session.set_structure_action_controls(
                shadow_mode=settings.structure_similarity_shadow_mode,
                bar_actions=settings.structure_bar_actions_enabled,
                phrase_actions=settings.structure_phrase_actions_enabled,
                section_actions=settings.structure_section_actions_enabled,
            )

    def set_learned_live_settings(self, settings: LearnedLiveSettings) -> None:
        self._learned_live_settings = settings

    def set_baked_playback_mode(self, mode: str) -> None:
        self._baked_playback_mode = str(mode or "auto")

    def set_live_loopback_enabled(self, enabled: bool) -> None:
        self._live_loopback_enabled = bool(enabled)

    def set_timeline_resolver(self, resolver) -> None:
        self._timeline_resolver = resolver

    def capture_settings(self) -> CaptureSettings:
        return self._capture_settings

    def reactive_settings(self) -> ReactiveSettings:
        return self._reactive_settings

    def learned_live_settings(self) -> LearnedLiveSettings:
        return self._learned_live_settings

    def recent_saved_shows(self) -> tuple[str, ...]:
        return tuple(self._recent_saved_shows)

    def set_recent_saved_shows(self, paths: tuple[str, ...]) -> None:
        self._recent_saved_shows = [value for value in paths if value][:8]

    def recent_events(self) -> tuple[str, ...]:
        return tuple(self._events)

    def recent_warnings(self) -> tuple[str, ...]:
        return tuple(self._warnings)

    def runtime_metrics(self) -> dict[str, Any]:
        metrics: dict[str, Any] = {
            "elapsed_seconds": round(time.monotonic() - self._started_at, 3),
            "ready_queue_count": self._pipeline.ready_queue_count() if self._pipeline is not None else 0,
        }
        if self._pipeline is not None:
            metrics.update(self._pipeline.stats())
        return metrics

    def start_local_playlist(
        self,
        audio_path: Path,
        *,
        playlist=None,
        config_path: Path | None = None,
        profile=None,
        profile_resolver=None,
        simulation_only: bool | None = None,
        precompiled_timelines=None,
        precompiled_timeline_sources=None,
    ) -> SessionHandle:
        self._pipeline_playback_enabled = False
        self._armed_output_mode = ""
        self._local_source_path = str(audio_path)
        self._saved_show_path = ""
        self._selected_show_path = ""
        self._reactive_effect_mode = ""
        self._stop_output_handle()
        handle = self._session_service.start_local_preview_session(
            audio_path,
            playlist=playlist,
            config_path=config_path,
            profile=profile,
            profile_resolver=profile_resolver,
            timeline_resolver=self._timeline_resolver,
            simulation_only=self._simulation_only(simulation_only),
            fallback_to_simulation=self._fallback_to_simulation(),
            audio_device=self._routing_state.selected_output_audio_device_id,
            precompiled_timelines=precompiled_timelines,
            precompiled_timeline_sources=precompiled_timeline_sources,
        )
        self._output_handle = handle
        self._update_routing_resolution(config_path=config_path, session_handle=handle)
        self._status_message = f"Local playlist started for {audio_path.name}."
        self._error_message = ""
        self._record_event(self._status_message)
        return handle

    def start_saved_show(
        self,
        audio_path: Path,
        show_path: Path,
        *,
        config_path: Path | None = None,
        simulation_only: bool | None = None,
    ) -> SessionHandle:
        self._pipeline_playback_enabled = False
        self._armed_output_mode = ""
        self._local_source_path = str(audio_path)
        self._saved_show_path = str(show_path)
        self._selected_show_path = str(show_path)
        self._reactive_effect_mode = ""
        self._stop_output_handle()
        handle = self._session_service.start_precompiled_show_session(
            audio_path,
            show_path,
            config_path=config_path,
            simulation_only=self._simulation_only(simulation_only),
            fallback_to_simulation=self._fallback_to_simulation(),
            audio_device=self._routing_state.selected_output_audio_device_id,
            input_device_label=self._selected_input_label(),
            routing_mode=self._routing_state.output_target.mode,
            routing_status=self._routing_state.routing_status,
            timeline_resolver=self._timeline_resolver,
            baked_playback_mode=self._baked_playback_mode,
        )
        self._output_handle = handle
        self._push_recent_saved_show(show_path)
        self._update_routing_resolution(config_path=config_path, session_handle=handle)
        self._status_message = f"Saved show started: {show_path.name}."
        self._error_message = ""
        self._record_event(self._status_message)
        return handle

    def start_timeline_show(
        self,
        audio_path: Path,
        timeline,
        *,
        show_path: Path | None = None,
        config_path: Path | None = None,
        simulation_only: bool | None = None,
        start_seconds: float = 0.0,
        mode: str = "saved_show",
    ) -> SessionHandle:
        self._pipeline_playback_enabled = False
        self._armed_output_mode = ""
        self._local_source_path = str(audio_path)
        self._saved_show_path = str(show_path) if show_path is not None else ""
        self._selected_show_path = str(show_path) if show_path is not None else ""
        self._reactive_effect_mode = ""
        self._stop_output_handle()
        handle = self._session_service.start_timeline_playback_session(
            audio_path,
            timeline,
            config_path=config_path,
            simulation_only=self._simulation_only(simulation_only),
            fallback_to_simulation=self._fallback_to_simulation(),
            mode=mode,
            show_path=show_path,
            audio_device=self._routing_state.selected_output_audio_device_id,
            input_device_label=self._selected_input_label(),
            routing_mode=self._routing_state.output_target.mode,
            routing_status=self._routing_state.routing_status,
            timeline_resolver=self._timeline_resolver,
            start_seconds=start_seconds,
            baked_playback_mode=self._baked_playback_mode,
        )
        self._output_handle = handle
        if show_path is not None:
            self._push_recent_saved_show(show_path)
        self._update_routing_resolution(config_path=config_path, session_handle=handle)
        self._status_message = f"Saved show started: {(show_path.name if show_path is not None else audio_path.name)}."
        self._error_message = ""
        self._record_event(self._status_message)
        return handle

    def start_compiled_show(
        self,
        show: Show,
        *,
        show_path: Path | None = None,
        config_path: Path | None = None,
        simulation_only: bool | None = None,
    ) -> SessionHandle:
        """Play a saved multi-track Show in its persisted Track order."""
        if not show.tracks:
            raise ValueError("Add at least one Track before playing this Show.")
        first_track = Path(show.tracks[0].audio_path)
        self._pipeline_playback_enabled = False
        self._armed_output_mode = ""
        self._local_source_path = str(first_track)
        self._saved_show_path = str(show_path) if show_path is not None else ""
        self._selected_show_path = str(show_path) if show_path is not None else ""
        self._reactive_effect_mode = ""
        self._stop_output_handle()
        handle = self._session_service.start_compiled_show_session(
            show,
            show_path=show_path,
            config_path=config_path,
            simulation_only=self._simulation_only(simulation_only),
            fallback_to_simulation=self._fallback_to_simulation(),
            audio_device=self._routing_state.selected_output_audio_device_id,
        )
        self._output_handle = handle
        if show_path is not None:
            self._push_recent_saved_show(show_path)
        self._update_routing_resolution(config_path=config_path, session_handle=handle)
        self._status_message = f"Show started: {show.name} ({len(show.tracks)} Tracks)."
        self._error_message = ""
        self._record_event(self._status_message)
        return handle

    def select_saved_show(self, show_path: Path | None) -> None:
        self._selected_show_path = str(show_path) if show_path is not None else ""
        if show_path is not None:
            self._push_recent_saved_show(show_path)

    def start_reactive_live(
        self,
        *,
        config_path: Path | None = None,
        profile=None,
        director_config=None,
        simulation_only: bool | None = None,
        effect_mode: str = "reactive",
        raw_visualizer_noise_threshold: float = 0.004,
        raw_visualizer_gradient_points: tuple[
            tuple[float, str], ...
        ] = (),
        raw_visualizer_origins: dict[str, int] | None = None,
        raw_visualizer_palette_pool: tuple[
            tuple[str, tuple[str, ...]], ...
        ] = (),
        raw_visualizer_palette_name: str = "",
        raw_visualizer_auto_palette: bool = False,
        raw_visualizer_palette_interval: float = 16.0,
    ) -> SessionHandle:
        from dreamsync.live import LiveStructureConfig

        self._pipeline_playback_enabled = False
        self._armed_output_mode = ""
        self._saved_show_path = ""
        self._reactive_effect_mode = effect_mode
        self._stop_output_handle()
        handle = self._session_service.start_reactive_live_session(
            config_path=config_path,
            profile=profile,
            director_config=director_config,
            simulation_only=self._simulation_only(simulation_only),
            fallback_to_simulation=self._fallback_to_simulation(),
            audio_device=self._routing_state.selected_live_input_device_id,
            input_device_label=self._selected_input_label(),
            routing_mode=self._routing_state.output_target.mode,
            routing_status=self._routing_state.routing_status,
            effect_mode=effect_mode,
            raw_visualizer_noise_threshold=(
                raw_visualizer_noise_threshold
            ),
            raw_visualizer_gradient_points=(
                raw_visualizer_gradient_points
            ),
            raw_visualizer_origins=dict(
                raw_visualizer_origins or {}
            ),
            raw_visualizer_palette_pool=(
                raw_visualizer_palette_pool
            ),
            raw_visualizer_palette_name=(
                raw_visualizer_palette_name
            ),
            raw_visualizer_auto_palette=(
                raw_visualizer_auto_palette
            ),
            raw_visualizer_palette_interval=(
                raw_visualizer_palette_interval
            ),
            render_mode=self._reactive_settings.render_mode,
            sample_rate=self._reactive_settings.sample_rate,
            channels=self._reactive_settings.channels,
            frame_size=self._reactive_settings.frame_size,
            hop_size=self._reactive_settings.hop_size,
            blocksize=self._reactive_settings.blocksize,
            auto_cycle=self._reactive_settings.auto_cycle,
            half_time=self._reactive_settings.half_time,
            max_brightness=self._reactive_settings.max_brightness,
            mirror=self._reactive_settings.mirror,
            master_brightness=self._reactive_settings.master_brightness,
            debug_mood=self._reactive_settings.debug_mood,
            cycle_interval=self._reactive_settings.cycle_interval,
            telemetry_dir=(Path(self._reactive_settings.telemetry_dir) if self._reactive_settings.telemetry_dir else None),
            crossfade_detect=self._reactive_settings.crossfade_detect,
            profile_strategy=self._reactive_settings.profile_strategy,
            profile_override_path=self._reactive_settings.profile_override_path,
            show_palette_set=self._reactive_settings.show_palette_set,
            rotation_profiles=self._reactive_settings.rotation_profiles,
            rotation_interval=self._reactive_settings.rotation_interval,
            auto_palette=self._reactive_settings.auto_palette,
            smart_rotation=self._reactive_settings.smart_rotation,
            chain_blend_seconds=self._reactive_settings.chain_blend_seconds,
            auto_palette_seed=self._reactive_settings.auto_palette_seed,
            auto_palette_pool_size=self._reactive_settings.auto_palette_pool_size,
            chain_dwell_range_enabled=self._reactive_settings.chain_dwell_range_enabled,
            chain_min_dwell_seconds=self._reactive_settings.chain_min_dwell_seconds,
            chain_max_dwell_seconds=self._reactive_settings.chain_max_dwell_seconds,
            structure_config=LiveStructureConfig(
                harmonic_structure_enabled=(
                    self._reactive_settings.harmonic_structure_enabled
                ),
                beats_per_bar=self._reactive_settings.beats_per_bar,
                bars_per_phrase=self._reactive_settings.bars_per_phrase,
                harmonic_frame_size=(
                    self._reactive_settings.harmonic_frame_size
                ),
                harmonic_hop_multiplier=(
                    self._reactive_settings.harmonic_hop_multiplier
                ),
                sensitivity=self._reactive_settings.structure_sensitivity,
                downbeat_min_confidence=(
                    self._reactive_settings.downbeat_min_confidence
                ),
                debug_harmonics=self._reactive_settings.debug_harmonics,
                predictive_analysis_enabled=(
                    self._reactive_settings.predictive_analysis_enabled
                ),
                predictive_diagnostics_enabled=(
                    self._reactive_settings.predictive_diagnostics_enabled
                ),
                predictive_shadow_mode=(
                    self._reactive_settings.predictive_shadow_mode
                ),
                predictive_cues_enabled=(
                    self._reactive_settings.predictive_cues_enabled
                ),
                predictive_high_impact_cues_enabled=(
                    self._reactive_settings.predictive_high_impact_cues_enabled
                ),
                predictive_cue_prepare_threshold=(
                    self._reactive_settings.predictive_cue_prepare_threshold
                ),
                predictive_cue_schedule_threshold=(
                    self._reactive_settings.predictive_cue_schedule_threshold
                ),
                predictive_cue_high_impact_threshold=(
                    self._reactive_settings.predictive_cue_high_impact_threshold
                ),
                predictive_maximum_anticipatory_intensity=(
                    self._reactive_settings.predictive_maximum_anticipatory_intensity
                ),
                predictive_cue_cooldown_seconds=(
                    self._reactive_settings.predictive_cue_cooldown_seconds
                ),
                predictive_allowed_cue_classes=(
                    self._reactive_settings.predictive_allowed_cue_classes
                ),
                structure_similarity_enabled=(
                    self._reactive_settings.structure_similarity_enabled
                ),
                structure_similarity_diagnostics=(
                    self._reactive_settings.structure_similarity_diagnostics
                ),
                structure_similarity_shadow_mode=(
                    self._reactive_settings.structure_similarity_shadow_mode
                ),
                structure_bar_actions_enabled=(
                    self._reactive_settings.structure_bar_actions_enabled
                ),
                structure_phrase_actions_enabled=(
                    self._reactive_settings.structure_phrase_actions_enabled
                ),
                structure_section_actions_enabled=(
                    self._reactive_settings.structure_section_actions_enabled
                ),
                structure_allow_secondary_beat_modulation=(
                    self._reactive_settings.structure_allow_secondary_beat_modulation
                ),
                structure_use_tonal_sidecar=(
                    self._reactive_settings.structure_use_tonal_sidecar
                ),
                structure_memory_bars=(
                    self._reactive_settings.structure_memory_bars
                ),
                structure_min_meter_confidence=(
                    self._reactive_settings.structure_min_meter_confidence
                ),
                structure_phrase_threshold=(
                    self._reactive_settings.structure_phrase_threshold
                ),
                structure_section_threshold=(
                    self._reactive_settings.structure_section_threshold
                ),
                structure_large_action_threshold=(
                    self._reactive_settings.structure_large_action_threshold
                ),
            ),
        )
        self._output_handle = handle
        self._update_routing_resolution(config_path=config_path, session_handle=handle)
        self._status_message = (
            "Raw visualizer mode started."
            if effect_mode == "raw_visualizer"
            else "Reactive live mode started."
        )
        self._error_message = ""
        self._record_event(self._status_message)
        return handle

    def start_spotify_learned_live(
        self,
        watcher,
        *,
        cache_dir: Path | str = "~/.dreamsync/cache",
        capture_dir: Path | None = None,
        config_path: Path | None = None,
        profile=None,
        simulation_only: bool | None = None,
    ) -> SessionHandle:
        """Start cache-first Spotify output plus background learning capture."""
        self.stop_spotify_learned_live()
        settings = self._learned_live_settings
        capture_root = capture_dir or Path(self._capture_settings.capture_dir)
        cache = ShowCache(cache_dir)
        self._learned_store = LearnedTrackStore(cache)
        if settings.learning_enabled:
            self.start_capture_pipeline(
                capture_dir=Path(capture_root),
                profile=profile,
                sample_rate=self._capture_settings.sample_rate,
                capture_naming="metadata",
                capture_buffer=0,
                device_pattern=settings.capture_device_pattern,
                debug=settings.diagnostic_logging,
                learned_live=True,
                retention_policy=settings.mp3_retention_policy,
                retained_mp3_limit=settings.retained_mp3_limit,
            )

        def start_compiled(timeline, interpolator) -> None:
            self._stop_output_handle()
            self._reactive_effect_mode = ""
            self._output_handle = self._session_service.start_spotify_timeline_session(
                timeline,
                interpolator,
                config_path=config_path,
                simulation_only=self._simulation_only(simulation_only),
                fallback_to_simulation=self._fallback_to_simulation(),
            )
            self._record_event("learned_live_runtime_switched:compiled")

        def start_reactive() -> None:
            self.start_reactive_live(
                config_path=config_path,
                profile=profile,
                simulation_only=simulation_only,
            )
            self._record_event("learned_live_runtime_switched:reactive")

        session = SpotifyLearnedLiveSession(
            watcher,
            cache=cache,
            profile=profile,
            start_compiled=start_compiled,
            start_reactive=start_reactive,
            stop_output=self._stop_output_handle,
            start_capture=(
                (lambda _track, _progress: self._pipeline.begin_learning_candidate())
                if settings.learning_enabled and self._pipeline is not None else None
            ),
            invalidate_capture=(
                self._pipeline.invalidate_learning_candidate
                if settings.learning_enabled and self._pipeline is not None else None
            ),
            learning_enabled=settings.learning_enabled,
            learned_store=self._learned_store,
        )
        stop_event = threading.Event()
        handle = SessionHandle(
            mode="spotify_learned_live",
            stop_event=stop_event,
            thread=threading.Thread(),
            session_ref=[session],
        )

        def runner() -> None:
            try:
                handle.summary = session.run(stop_event)
            except BaseException as exc:
                handle.error = exc

        handle.thread = threading.Thread(
            target=runner, name="gui-spotify-learned-live", daemon=True
        )
        self._learned_live_session = session
        self._learned_live_handle = handle
        if self._pipeline is not None:
            self._pipeline.set_learning_state_callback(session.notify_learning_state)
        handle.thread.start()
        self._status_message = "Spotify Live — Learning started."
        self._record_event(self._status_message)
        return handle

    def stop_spotify_learned_live(self) -> None:
        handle = self._learned_live_handle
        self._learned_live_handle = None
        self._learned_live_session = None
        if handle is not None:
            handle.stop()
            handle.wait(timeout=5.0)
        self._stop_output_handle()
        if self._pipeline is not None and self._pipeline.running:
            self._pipeline.stop()

    def start_capture_pipeline(
        self,
        *,
        capture_dir: Path,
        profile=None,
        sample_rate: int = 44100,
        capture_naming: str = "timestamp",
        capture_buffer: int = 0,
        device_pattern: str = "CABLE Output",
        debug: bool = False,
        learned_live: bool = False,
        retention_policy: str = "keep_recent",
        retained_mp3_limit: int = 10,
    ) -> None:
        if self._pipeline is None:
            self._pipeline = self._pipeline_factory()
            self._pipeline.set_timing_source(self._capture_timing_source)
        settings = replace(
            self._capture_settings,
            capture_dir=str(capture_dir),
            sample_rate=sample_rate,
            naming_mode=capture_naming,
            max_capture_buffer=capture_buffer,
            device_pattern=device_pattern,
            debug_pipeline=debug,
        )
        self._capture_settings = settings
        self._pipeline.start(
            capture_dir=capture_dir,
            profile=profile,
            sample_rate=settings.sample_rate,
            capture_naming=settings.naming_mode,
            capture_buffer=settings.max_capture_buffer,
            device_pattern=settings.device_pattern,
            debug=settings.debug_pipeline,
            learned_live=learned_live,
            retention_policy=retention_policy,
            retained_mp3_limit=retained_mp3_limit,
        )
        self._status_message = f"Audio loopback capture running in {capture_dir}."
        self._error_message = ""
        self._record_event(self._status_message)

    def set_capture_timing_source(self, fetcher: Callable[[], dict | None] | None) -> None:
        """Attach Spotify queue timing to the capture pipeline when available."""
        self._capture_timing_source = fetcher
        if self._pipeline is not None:
            self._pipeline.set_timing_source(fetcher)
        state = "attached" if fetcher is not None else "detached"
        self._record_event(f"Capture timing source {state}.")

    def notify_capture_track_change(self, timing_data: dict) -> int:
        """Forward a Spotify track change to the active capture pipeline."""
        if self._pipeline is None:
            return 0
        return self._pipeline.on_track_change(timing_data)

    def stop_capture_pipeline(self) -> None:
        if self._pipeline is not None:
            self._pipeline.stop()
        self._pipeline_playback_enabled = False
        if self._output_handle is None:
            self._armed_output_mode = ""
        self._status_message = "Audio loopback capture stopped."
        self._record_event(self._status_message)

    def switch_to_pipeline_playback(
        self,
        *,
        config_path: Path | None = None,
        simulation_only: bool | None = None,
    ) -> bool:
        self._pipeline_playback_enabled = True
        self._saved_show_path = ""
        self._reactive_effect_mode = ""
        self._stop_output_handle()
        started = self._start_next_pipeline_item(
            config_path=config_path,
            simulation_only=self._simulation_only(simulation_only),
        )
        if not started:
            self._armed_output_mode = "pipeline_playback"
            self._status_message = "Captured-show playback armed for the next ready capture."
            self._record_event(self._status_message)
        return started

    def preview_captured_show(
        self,
        item_id: str,
        *,
        config_path: Path | None = None,
    ) -> SessionHandle:
        if self._pipeline is None:
            raise RuntimeError("Capture pipeline is not running.")
        item = self._pipeline.item_for_id(item_id)
        timeline = self._pipeline.timeline_for_id(item_id)
        if item is None or timeline is None:
            raise RuntimeError("Selected captured show is not ready for preview.")
        self.stop_preview()
        handle = self._session_service.start_timeline_playback_session(
            Path(item.mp3_path),
            timeline,
            config_path=config_path,
            simulation_only=True,
            mode="simulation_preview",
            timeline_resolver=self._timeline_resolver,
            baked_playback_mode="off",
        )
        self._preview_handle = handle
        self._status_message = f"Simulation preview started for {Path(item.mp3_path).name}."
        self._record_event(self._status_message)
        return handle

    def discard_captured_show(self, item_id: str) -> None:
        if self._pipeline is None:
            return
        self._pipeline.discard(item_id)
        self._status_message = "Captured show discarded."
        self._record_event(self._status_message)

    def prioritize_captured_show(self, item_id: str) -> None:
        if self._pipeline is None:
            return
        self._pipeline.move_to_top(item_id)
        self._status_message = "Captured show moved to the top of the ready queue."
        self._record_event(self._status_message)

    def stop_output_only(self) -> None:
        if self._learned_live_handle is not None:
            self.stop_spotify_learned_live()
        self._pipeline_playback_enabled = False
        self._armed_output_mode = ""
        self._pipeline_current_item_id = None
        self._stop_output_handle()
        self._status_message = "Output stop requested."
        self._record_event(self._status_message)

    def pause_output(self) -> bool:
        handle = self._output_handle
        if handle is None:
            return False
        paused = bool(handle.pause())
        if paused:
            self._status_message = "Output paused."
            self._record_event(self._status_message)
        return paused

    def resume_output(self) -> bool:
        handle = self._output_handle
        if handle is None:
            return False
        resumed = bool(handle.resume())
        if resumed:
            self._status_message = "Output resumed."
            self._record_event(self._status_message)
        return resumed

    def toggle_output_pause(self) -> str:
        handle = self._output_handle
        if handle is None:
            return "unsupported"
        result = str(handle.toggle_pause())
        if result == "paused":
            self._status_message = "Output paused."
            self._record_event(self._status_message)
        elif result == "playing":
            self._status_message = "Output resumed."
            self._record_event(self._status_message)
        return result

    def stop_preview(self) -> None:
        if self._preview_handle is not None:
            self._preview_handle.stop()
            self._preview_handle = None

    def stop_all(self) -> None:
        self.stop_preview()
        self.stop_output_only()
        self.stop_capture_pipeline()
        self._status_message = "All runtime services stopped."
        self._record_event(self._status_message)

    def active_session(self):
        return self._session_for_handle(self._output_handle)

    def preview_session(self):
        return self._session_for_handle(self._preview_handle)

    def update_runtime_control(self, **changes: Any) -> dict[str, Any]:
        session = self.active_session()
        if session is not None and hasattr(session, "update_runtime_control"):
            result = session.update_runtime_control(**changes)
            self._record_event("Runtime control updated.")
            return result
        return {}

    def update_raw_visualizer_palette_control(
        self,
        *,
        pool: tuple[tuple[str, tuple[str, ...]], ...],
        selected_name: str,
        auto_chain: bool,
        interval_seconds: float,
    ) -> dict[str, Any]:
        session = self.active_session()
        if session is not None and hasattr(
            session,
            "update_raw_visualizer_palette_control",
        ):
            result = session.update_raw_visualizer_palette_control(
                pool=pool,
                selected_name=selected_name,
                auto_chain=auto_chain,
                interval_seconds=interval_seconds,
            )
            self._record_event(
                "Raw Visualizer palette control updated."
            )
            return result
        return {}

    def clear_runtime_control(self) -> dict[str, Any]:
        session = self.active_session()
        if session is not None and hasattr(session, "clear_runtime_control"):
            result = session.clear_runtime_control()
            self._record_event("Runtime control cleared.")
            return result
        return {}

    def runtime_control_snapshot(self) -> dict[str, Any]:
        session = self.active_session()
        if session is not None and hasattr(session, "runtime_control_snapshot"):
            return session.runtime_control_snapshot()
        return {}

    def request_reactive_downbeat_nudge(self) -> int | None:
        return self.request_reactive_beat_latch("downbeat")

    def request_reactive_beat_latch(self, kind: str) -> int | None:
        session = self.active_session()
        if session is None or not hasattr(session, "request_manual_beat"):
            return None
        normalized = (
            "downbeat" if str(kind).strip().lower() == "downbeat"
            else "beat"
        )
        revision = int(session.request_manual_beat(normalized))
        label = "Downbeat" if normalized == "downbeat" else "Beat"
        self._status_message = (
            f"{label} latch requested for the nearest detected beat."
        )
        self._record_event(
            f"Reactive nearest-beat {normalized} latch requested "
            f"(revision {revision})."
        )
        return revision

    def request_reactive_detection_reset(self) -> int | None:
        session = self.active_session()
        if session is None or not hasattr(session, "request_detection_reset"):
            return None
        revision = int(session.request_detection_reset())
        self._status_message = (
            "Reactive beat history reset requested; reacquiring BPM and meter."
        )
        self._record_event(
            f"Reactive detector-session reset requested (revision {revision})."
        )
        return revision

    def set_reactive_cycle_tempo_multiplier(
        self,
        multiplier: float,
    ) -> float | None:
        session = self.active_session()
        if session is None or not hasattr(
            session,
            "set_cycle_tempo_multiplier",
        ):
            return None
        selected = float(
            session.set_cycle_tempo_multiplier(multiplier)
        )
        self._status_message = (
            f"Reactive cycle tempo locked to {selected:g}× detector BPM."
        )
        self._record_event(
            f"Reactive cycle tempo multiplier set to {selected:g}×."
        )
        return selected

    def preview_frame_snapshot(self) -> dict[str, Any] | None:
        session = self.preview_session()
        if session is not None and hasattr(session, "preview_frame_snapshot"):
            return session.preview_frame_snapshot()
        session = self.active_session()
        if session is not None and hasattr(session, "preview_frame_snapshot"):
            return session.preview_frame_snapshot()
        return None

    def poll(self) -> RuntimeModeState:
        self._reap_finished_preview()
        self._reap_finished_output()
        if self._pipeline_playback_enabled and self._output_handle is None:
            self._start_next_pipeline_item()
        return self.snapshot()

    def snapshot(self) -> RuntimeModeState:
        active_session = self.active_session()
        active_snapshot = (
            active_session.session_snapshot()
            if active_session is not None and hasattr(active_session, "session_snapshot")
            else {}
        )
        learned_snapshot = (
            self._learned_live_session.snapshot()
            if self._learned_live_session is not None else None
        )
        output_mode = self._output_handle.mode if self._output_handle is not None else ""
        active_mode = (
            "spotify_learned_live"
            if self._learned_live_handle is not None
            else output_mode or self._armed_output_mode or "idle"
        )
        playback_status = str(active_snapshot.get("playback_state", "armed" if self._armed_output_mode else "idle"))
        current_track = (
            learned_snapshot.active_track_title
            if learned_snapshot is not None
            else active_snapshot.get("current_track")
        )
        device_status = str(active_snapshot.get("device_status", "")) if active_snapshot else ""
        audio_output = str(active_snapshot.get("audio_output", "")) if active_snapshot else ""
        input_device = str(active_snapshot.get("input_device", self._selected_input_label())) if active_snapshot else self._selected_input_label()
        simulation_target = "captured preview" if self._preview_handle is not None else ""
        pipeline_state = "idle"
        ready_items: tuple[CapturedShowItem, ...] = ()
        ready_queue_count = 0
        if self._pipeline is not None:
            pipeline_state = self._pipeline.snapshot_state()
            ready_items = self._pipeline.snapshot_items()
            ready_queue_count = self._pipeline.ready_queue_count()
        lease = OutputLease(
            owner=output_mode if self._output_handle is not None else "",
            mode=output_mode or active_mode,
            simulation_only=bool(device_status.startswith("simulation") or device_status.startswith("preview")),
        )
        return RuntimeModeState(
            active_output_mode=active_mode,
            armed_output_mode=self._armed_output_mode,
            simulation_target=simulation_target,
            capture_state="running" if self._pipeline is not None and self._pipeline.running else "off",
            pipeline_state=pipeline_state,
            spotify_state="enabled" if self._live_loopback_enabled else "off",
            learned_live_strategy=(
                learned_snapshot.active_strategy if learned_snapshot is not None else "waiting"
            ),
            learned_live_badge=(
                "PAUSED" if learned_snapshot is not None and learned_snapshot.paused
                else "PRECOMPILED" if learned_snapshot is not None and learned_snapshot.active_strategy == "compiled"
                else "REACTIVE · LEARNING" if learned_snapshot is not None and learned_snapshot.active_learning_state == "capturing"
                else "REACTIVE · NOT LEARNING" if learned_snapshot is not None and learned_snapshot.active_strategy == "reactive"
                else ""
            ),
            learning_state=(learned_snapshot.learning_state if learned_snapshot is not None else "idle"),
            learning_reason=(learned_snapshot.learning_reason if learned_snapshot is not None else ""),
            learned_library_count=(
                len(self._learned_store.list_manifests()) if self._learned_store is not None else 0
            ),
            learned_cache_hits=(learned_snapshot.cache_hits if learned_snapshot is not None else 0),
            learned_cache_misses=(learned_snapshot.cache_misses if learned_snapshot is not None else 0),
            background_learning_items=(
                tuple(
                    f"{track_id}: {state}" + (f" · {reason}" if reason else "")
                    for track_id, state, reason in learned_snapshot.background_jobs
                ) if learned_snapshot is not None else ()
            ),
            ready_queue_count=ready_queue_count,
            ready_items=ready_items,
            output_lease=lease,
            playback_status=playback_status,
            current_track=str(current_track or ""),
            local_source_path=self._local_source_path,
            saved_show_path=self._saved_show_path,
            selected_show_path=self._selected_show_path,
            reactive_effect_mode=self._reactive_effect_mode,
            device_status=device_status,
            audio_output=audio_output,
            input_device=input_device,
            routing_state=replace(
                self._routing_state,
                selected_output_owner=active_mode,
            ),
            capture_settings=self._capture_settings,
            learned_live_settings=self._learned_live_settings,
            reactive_settings=self._reactive_settings,
            recent_saved_shows=tuple(self._recent_saved_shows),
            status_message=self._status_message,
            error_message=self._error_message,
        )

    def _start_next_pipeline_item(
        self,
        *,
        config_path: Path | None = None,
        simulation_only: bool | None = None,
    ) -> bool:
        if self._pipeline is None:
            return False
        item = self._pipeline.next_ready_item()
        if item is None:
            self._armed_output_mode = "pipeline_playback" if self._pipeline_playback_enabled else ""
            return False
        timeline = self._pipeline.timeline_for_id(item.item_id)
        if timeline is None:
            return False
        self._pipeline.mark_playing(item.item_id)
        self._pipeline_current_item_id = item.item_id
        self._armed_output_mode = ""
        self._output_handle = self._session_service.start_timeline_playback_session(
            Path(item.mp3_path),
            timeline,
            config_path=config_path,
            simulation_only=self._simulation_only(simulation_only),
            fallback_to_simulation=self._fallback_to_simulation(),
            mode="pipeline_playback",
            audio_device=self._capture_settings.pipeline_playback_device_id,
            input_device_label=self._selected_input_label(),
            routing_mode=self._routing_state.output_target.mode,
            routing_status=self._routing_state.routing_status,
            timeline_resolver=self._timeline_resolver,
            baked_playback_mode="off",
        )
        self._update_routing_resolution(config_path=config_path, session_handle=self._output_handle)
        self._status_message = f"Pipeline playback started for {Path(item.mp3_path).name}."
        self._error_message = ""
        self._record_event(self._status_message)
        return True

    def _reap_finished_output(self) -> None:
        handle = self._output_handle
        if handle is None:
            return
        if handle.error is not None:
            self._error_message = str(handle.error)
            self._status_message = f"Output failed: {handle.error}"
            self._warnings.append(self._status_message)
            self._record_event(self._status_message)
            self._clear_output_handle()
            return
        if handle.running:
            return
        if self._pipeline_current_item_id and self._pipeline is not None:
            item_id = self._pipeline_current_item_id
            if handle.mode == "pipeline_playback" and self._capture_settings.purge_after_playback:
                self._pipeline.discard(item_id)
            else:
                self._pipeline.mark_ready(item_id)
            self._pipeline_current_item_id = None
        if handle.summary is not None and handle.mode != "pipeline_playback":
            self._status_message = f"{handle.mode.replace('_', ' ')} finished."
            self._record_event(self._status_message)
        elif handle.mode == "pipeline_playback" and self._pipeline_playback_enabled:
            self._armed_output_mode = "pipeline_playback"
        self._clear_output_handle()

    def _reap_finished_preview(self) -> None:
        handle = self._preview_handle
        if handle is None:
            return
        if handle.error is not None or not handle.running:
            self._preview_handle = None

    def _stop_output_handle(self) -> None:
        if self._output_handle is not None:
            self._output_handle.stop()
            self._output_handle = None
        if self._pipeline_current_item_id and self._pipeline is not None:
            self._pipeline.mark_ready(self._pipeline_current_item_id)
            self._pipeline_current_item_id = None

    def _clear_output_handle(self) -> None:
        self._output_handle = None

    def _simulation_only(self, override: bool | None = None) -> bool:
        if override is not None:
            return override
        return self._routing_state.output_target.mode != "hardware"

    def _fallback_to_simulation(self) -> bool:
        return self._routing_state.output_target.fallback_to_simulation

    def _selected_input_label(self) -> str:
        return self._audio_device_service.find_label(
            self._routing_state.available_input_devices,
            self._routing_state.selected_live_input_device_id,
        )

    def _update_routing_resolution(self, *, config_path: Path | None, session_handle: SessionHandle | None) -> None:
        mode = self._routing_state.output_target.mode
        session = self._session_for_handle(session_handle)
        snapshot = session.session_snapshot() if session is not None and hasattr(session, "session_snapshot") else {}
        device_status = str(snapshot.get("device_status", ""))
        resolved_mode = "hardware" if mode == "hardware" and not device_status.startswith("simulation") else "simulation"
        if mode == "hardware" and resolved_mode == "simulation":
            if self._routing_state.output_target.fallback_to_simulation:
                status = "Hardware selected; fell back to simulation preview."
                self._warnings.append(status)
            else:
                status = "Hardware selected."
        elif mode == "hardware":
            status = f"Hardware output active via {Path(config_path).name if config_path else 'configured devices'}."
        else:
            status = "Simulation only."
        self._routing_state = replace(
            self._routing_state,
            resolved_output_mode=resolved_mode,
            resolved_config_path=str(config_path) if config_path is not None else "",
            routing_status=status,
        )

    def _push_recent_saved_show(self, show_path: Path) -> None:
        show = str(show_path)
        self._recent_saved_shows = [show, *[value for value in self._recent_saved_shows if value != show]][:8]

    def _record_event(self, message: str) -> None:
        if message:
            self._events.appendleft(message)

    @staticmethod
    def _session_for_handle(handle: SessionHandle | None):
        if handle is None or not handle.session_ref:
            return None
        return handle.session_ref[0]
