"""Background session start/stop helpers for the GUI."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dreamsync.capture.writer import check_ffmpeg
from dreamsync.local_session import (
    _device_status_for_adapter,
    _format_audio_output_label,
    run_local_session,
)
from dreamsync.output.auto_detect import load_device_config
from dreamsync.output.null_adapter import NullMultiAdapter, SimulationMultiAdapter
from dreamsync.playlist import PlaylistManager
from dreamsync.profile import ProfileRotation, load_profile, resolve_profile_path
from dreamsync.profile_chain import ChainConfig, ProfileChain
from dreamsync.show.models import ShowTimeline
from dreamsync.show.player import AudioPlayer
from dreamsync.show.runtime_control import RuntimeControlBus, runtime_control_to_dict
from dreamsync.show.runtime import ShowPlaybackRuntime


@dataclass
class SessionHandle:
    mode: str
    stop_event: threading.Event
    thread: threading.Thread
    session_ref: list[Any] = field(default_factory=lambda: [None])
    summary: dict[str, Any] | None = None
    error: Exception | None = None

    def stop(self) -> None:
        self.stop_event.set()

    def pause(self) -> bool:
        session = self.session_ref[0] if self.session_ref else None
        if session is not None and hasattr(session, "pause"):
            return bool(session.pause())
        return False

    def resume(self) -> bool:
        session = self.session_ref[0] if self.session_ref else None
        if session is not None and hasattr(session, "resume"):
            return bool(session.resume())
        return False

    def toggle_pause(self) -> str:
        session = self.session_ref[0] if self.session_ref else None
        if session is not None and hasattr(session, "toggle_pause"):
            return str(session.toggle_pause())
        return "unsupported"

    @property
    def running(self) -> bool:
        return self.thread.is_alive()

    def wait(self, timeout: float | None = None) -> dict[str, Any] | None:
        self.thread.join(timeout=timeout)
        return self.summary


class TimelinePlaybackSession:
    """Playback session for an already-compiled show timeline."""

    def __init__(
        self,
        multi_adapter,
        audio_path: Path,
        timeline: ShowTimeline,
        *,
        mode: str,
        show_path: Path | None = None,
        audio_device: int | None = None,
        input_device_label: str = "",
        routing_mode: str = "simulation",
        routing_status: str = "",
        runtime_control: RuntimeControlBus | None = None,
        start_seconds: float = 0.0,
    ) -> None:
        self._multi_adapter = multi_adapter
        self._audio_path = Path(audio_path)
        self._timeline = timeline
        self._mode = mode
        self._show_path = Path(show_path) if show_path is not None else None
        self._audio_device = audio_device
        self._input_device_label = input_device_label
        self._routing_mode = routing_mode
        self._routing_status = routing_status
        self._runtime_control = runtime_control or RuntimeControlBus()
        self._start_seconds = max(0.0, float(start_seconds))
        self._status_lock = threading.RLock()
        self._player: AudioPlayer | None = None
        self._runtime: ShowPlaybackRuntime | None = None
        self._playback_state = "idle"

    def run(self, stop_event: threading.Event) -> dict[str, Any]:
        self._multi_adapter.activate(brightness=100)
        self._set_playback_state("loading_audio")
        player = AudioPlayer(
            self._audio_path,
            sample_rate=44100,
            device=self._audio_device,
        )
        if self._start_seconds > 0.0:
            player.seek(self._start_seconds)
        runtime = ShowPlaybackRuntime(
            self._timeline,
            self._multi_adapter,
            control_state_getter=self._runtime_control.snapshot,
        )
        with self._status_lock:
            self._player = player
            self._runtime = runtime
            self._playback_state = "starting_audio"
        started_at = time.monotonic()
        player.play()
        self._set_playback_state("playing")
        frames_sent = 0
        try:
            while not stop_event.is_set():
                if player.finished:
                    self._set_playback_state("finished")
                    break
                if not player.playing:
                    self._set_playback_state("paused")
                    time.sleep(0.05)
                    continue
                self._set_playback_state("playing")
                if runtime.tick(player.position_seconds):
                    frames_sent += 1
                time.sleep(0.005)
        finally:
            player.stop()
            with self._status_lock:
                if self._playback_state != "finished":
                    self._playback_state = "stopped"
                self._player = None
                self._runtime = None
            self._multi_adapter.deactivate()

        return {
            "mode": self._mode,
            "track": self._audio_path.name,
            "show_path": str(self._show_path) if self._show_path is not None else "",
            "duration": self._timeline.duration,
            "elapsed": round(time.monotonic() - started_at, 2),
            "frames_sent": frames_sent,
            **runtime.stats,
        }

    def session_snapshot(self) -> dict[str, Any]:
        with self._status_lock:
            player = self._player
            runtime = self._runtime
            state = self._playback_state
            position_seconds = player.position_seconds if player is not None else 0.0
            is_playing = player.playing if player is not None else False
            current_cue = runtime.current_cue if runtime is not None else None
        return {
            "mode": self._mode,
            "current_track": self._audio_path,
            "current_index": 0,
            "tracks_played": 1 if state == "finished" else 0,
            "tracks_skipped": 0,
            "precompiled": 1,
            "queue": {"current_index": 0, "tracks": (self._audio_path,)},
            "playback_state": state,
            "is_playing": is_playing,
            "position_seconds": position_seconds,
            "duration_seconds": self._timeline.duration,
            "audio_output": _format_audio_output_label(self._audio_device),
            "input_device": self._input_device_label,
            "routing_mode": self._routing_mode,
            "routing_status": self._routing_status,
            "device_status": _device_status_for_adapter(self._multi_adapter),
            "show_path": str(self._show_path) if self._show_path is not None else "",
            "runtime_control": runtime_control_to_dict(self._runtime_control.snapshot()),
            "current_render_mode": current_cue.render_mode if current_cue is not None else "",
            "current_palette": tuple(current_cue.color_palette) if current_cue is not None else (),
        }

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

    def pause(self) -> bool:
        with self._status_lock:
            player = self._player
            if player is None or not player.playing:
                return False
            player.pause()
            self._playback_state = "paused"
        return True

    def resume(self) -> bool:
        with self._status_lock:
            player = self._player
            if player is None or player.playing or player.finished:
                return False
            player.play()
            self._playback_state = "playing"
        return True

    def toggle_pause(self) -> str:
        with self._status_lock:
            player = self._player
            if player is None:
                return "unsupported"
            currently_playing = bool(player.playing)
        if currently_playing:
            return "paused" if self.pause() else "unsupported"
        return "playing" if self.resume() else "unsupported"

    def _set_playback_state(self, state: str) -> None:
        with self._status_lock:
            self._playback_state = state


class ReactiveLiveSession:
    """Thin GUI wrapper around the live reactive runtime."""

    def __init__(
        self,
        multi_adapter,
        *,
        audio_device: int | None = None,
        director_config=None,
        profile=None,
        effect_mode: str = "reactive",
        render_mode: str = "scroll",
        sample_rate: int = 44100,
        channels: int = 1,
        frame_size: int = 2048,
        hop_size: int = 512,
        blocksize: int = 1024,
        auto_cycle: bool = True,
        half_time: bool = False,
        max_brightness: bool = False,
        debug_mood: bool = False,
        cycle_interval: float = 16.0,
        telemetry_dir: Path | None = None,
        crossfade_detect: bool = False,
        input_device_label: str = "",
        routing_mode: str = "simulation",
        routing_status: str = "",
        profile_rotation=None,
        profile_chain=None,
        runtime_control: RuntimeControlBus | None = None,
    ) -> None:
        self._multi_adapter = multi_adapter
        self._audio_device = audio_device
        self._director_config = director_config
        self._profile = profile
        self._effect_mode = effect_mode
        self._render_mode = render_mode
        self._sample_rate = sample_rate
        self._channels = channels
        self._frame_size = frame_size
        self._hop_size = hop_size
        self._blocksize = blocksize
        self._auto_cycle = auto_cycle
        self._half_time = half_time
        self._max_brightness = max_brightness
        self._debug_mood = debug_mood
        self._cycle_interval = cycle_interval
        self._telemetry_dir = telemetry_dir
        self._crossfade_detect = crossfade_detect
        self._input_device_label = input_device_label
        self._routing_mode = routing_mode
        self._routing_status = routing_status
        self._profile_rotation = profile_rotation
        self._profile_chain = profile_chain
        self._runtime_control = runtime_control or RuntimeControlBus()
        self._status_lock = threading.RLock()
        self._playback_state = "idle"
        self._started_at: float | None = None
        self._summary: dict[str, Any] | None = None
        self._latest_runtime_state: dict[str, Any] = {}

    def run(self, stop_event: threading.Event) -> dict[str, Any]:
        from dreamsync.live import run_live_to_govee

        with self._status_lock:
            self._playback_state = "starting"
            self._started_at = time.monotonic()
        _logs, summary = run_live_to_govee(
            multi_adapter=self._multi_adapter,
            duration_seconds=None,
            sample_rate=self._sample_rate,
            channels=self._channels,
            device=self._audio_device,
            frame_size=self._frame_size,
            hop_size=self._hop_size,
            blocksize=self._blocksize,
            director_config=self._director_config,
            half_time=self._half_time,
            max_brightness=self._max_brightness,
            auto_cycle=self._auto_cycle,
            cycle_interval=self._cycle_interval,
            debug_mood=self._debug_mood,
            stop_event=stop_event,
            telemetry_dir=self._telemetry_dir,
            crossfade_detect=self._crossfade_detect,
            profile=self._profile,
            profile_rotation=self._profile_rotation,
            profile_chain=self._profile_chain,
            runtime_control_getter=self._runtime_control.snapshot,
            state_callback=self._update_runtime_state,
        )
        with self._status_lock:
            self._summary = dict(summary)
            self._playback_state = "stopped" if stop_event.is_set() else "finished"
        return {"mode": "reactive_live", **summary}

    def session_snapshot(self) -> dict[str, Any]:
        with self._status_lock:
            started_at = self._started_at
            state = self._playback_state
            runtime_state = dict(self._latest_runtime_state)
        elapsed = 0.0 if started_at is None else max(0.0, time.monotonic() - started_at)
        return {
            "mode": "reactive_live",
            "current_track": "Reactive Live",
            "current_index": -1,
            "tracks_played": 0,
            "tracks_skipped": 0,
            "precompiled": 0,
            "queue": {"current_index": -1, "tracks": ()},
            "playback_state": state,
            "is_playing": state in {"starting", "playing"},
            "position_seconds": elapsed,
            "duration_seconds": 0.0,
            "audio_output": _format_audio_output_label(self._audio_device),
            "input_device": self._input_device_label,
            "routing_mode": self._routing_mode,
            "routing_status": self._routing_status,
            "device_status": _device_status_for_adapter(self._multi_adapter),
            "effect_mode": self._effect_mode,
            "render_mode": self._render_mode,
            "current_palette": tuple(runtime_state.get("current_palette", ())),
            "runtime_control": runtime_control_to_dict(self._runtime_control.snapshot()),
            "runtime_state": runtime_state,
            "dominant_band": runtime_state.get("dominant_band", ""),
            "dominant_proxy": runtime_state.get("dominant_proxy", ""),
            "pan_center": runtime_state.get("pan_center", 0.0),
            "pan_width": runtime_state.get("pan_width", 0.0),
        }

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

    def latest_runtime_state(self) -> dict[str, Any]:
        with self._status_lock:
            return dict(self._latest_runtime_state)

    def _update_runtime_state(self, state: dict[str, Any]) -> None:
        with self._status_lock:
            self._latest_runtime_state = dict(state)


class SessionService:
    """Start manageable background sessions for the GUI."""

    @staticmethod
    def require_local_preview_dependencies() -> None:
        """Validate runtime dependencies for local preview playback."""
        if not check_ffmpeg():
            raise RuntimeError(
                "ffmpeg is required for local preview playback. "
                "Install ffmpeg and confirm 'ffmpeg -version' works in this shell."
            )

    def build_output_adapter(
        self,
        config_path: Path | None,
        *,
        simulation_only: bool = True,
        fallback_to_simulation: bool = True,
    ):
        if config_path is None or not config_path.exists():
            if not simulation_only and not fallback_to_simulation:
                raise RuntimeError("Hardware output requires a valid config path.")
            return NullMultiAdapter()
        configs = load_device_config(config_path)
        if not configs:
            if not simulation_only and not fallback_to_simulation:
                raise RuntimeError("Hardware output requires at least one configured device.")
            return NullMultiAdapter()
        if simulation_only:
            return SimulationMultiAdapter.from_configs(configs)
        try:
            from dreamsync.output.auto_detect import build_multi_adapter, detect_all_devices

            detected = detect_all_devices(configs, parallel=True)
            adapter = build_multi_adapter(detected)
            if isinstance(adapter, NullMultiAdapter):
                if not fallback_to_simulation:
                    raise RuntimeError("No configured hardware devices were reachable.")
                return SimulationMultiAdapter.from_configs(configs)
            return adapter
        except Exception:
            if not fallback_to_simulation:
                raise
            return SimulationMultiAdapter.from_configs(configs)

    def start_local_preview_session(
        self,
        audio_path: Path,
        *,
        config_path: Path | None = None,
        profile=None,
        profile_resolver=None,
        timeline_resolver=None,
        cache_dir: str = "~/.dreamsync/cache",
        shuffle: bool = False,
        repeat: bool = False,
        simulation_only: bool = True,
        fallback_to_simulation: bool = True,
        audio_device: int | None = None,
    ) -> SessionHandle:
        self.require_local_preview_dependencies()
        stop_event = threading.Event()
        session_ref: list[Any] = [None]
        playlist = PlaylistManager.from_path(audio_path, shuffle=shuffle, repeat=repeat)
        adapter = self.build_output_adapter(
            config_path,
            simulation_only=simulation_only,
            fallback_to_simulation=fallback_to_simulation,
        )

        handle = SessionHandle(
            mode="local",
            stop_event=stop_event,
            thread=threading.Thread(),
            session_ref=session_ref,
        )
        runtime_control = RuntimeControlBus()

        def _runner() -> None:
            try:
                handle.summary = run_local_session(
                    adapter,
                    audio_path,
                    cache_dir=cache_dir,
                    profile=profile,
                    profile_resolver=profile_resolver,
                    timeline_resolver=timeline_resolver,
                    audio_device=audio_device,
                    stop_event=stop_event,
                    playlist=playlist if len(playlist) > 1 else None,
                    session_ref=session_ref,
                    runtime_control=runtime_control,
                )
            except Exception as exc:  # pragma: no cover - surfaced via handle
                handle.error = exc

        handle.thread = threading.Thread(target=_runner, name="gui-local-session", daemon=True)
        handle.thread.start()
        return handle

    def start_timeline_playback_session(
        self,
        audio_path: Path,
        timeline: ShowTimeline,
        *,
        config_path: Path | None = None,
        simulation_only: bool = True,
        fallback_to_simulation: bool = True,
        mode: str = "saved_show",
        show_path: Path | None = None,
        audio_device: int | None = None,
        input_device_label: str = "",
        routing_mode: str = "simulation",
        routing_status: str = "",
        timeline_resolver=None,
        start_seconds: float = 0.0,
    ) -> SessionHandle:
        stop_event = threading.Event()
        session_ref: list[Any] = [None]
        adapter = self.build_output_adapter(
            config_path,
            simulation_only=simulation_only,
            fallback_to_simulation=fallback_to_simulation,
        )
        if timeline_resolver is not None:
            timeline = timeline_resolver(audio_path, timeline)
        runtime_control = RuntimeControlBus()
        handle = SessionHandle(
            mode=mode,
            stop_event=stop_event,
            thread=threading.Thread(),
            session_ref=session_ref,
        )
        session = TimelinePlaybackSession(
            adapter,
            audio_path,
            timeline,
            mode=mode,
            show_path=show_path,
            audio_device=audio_device,
            input_device_label=input_device_label,
            routing_mode=routing_mode,
            routing_status=routing_status,
            runtime_control=runtime_control,
            start_seconds=start_seconds,
        )
        session_ref[:] = [session]

        def _runner() -> None:
            try:
                handle.summary = session.run(stop_event)
            except Exception as exc:  # pragma: no cover - surfaced via handle
                handle.error = exc

        handle.thread = threading.Thread(target=_runner, name=f"gui-{mode}-session", daemon=True)
        handle.thread.start()
        return handle

    def start_precompiled_show_session(
        self,
        audio_path: Path,
        show_path: Path,
        *,
        config_path: Path | None = None,
        simulation_only: bool = True,
        fallback_to_simulation: bool = True,
        audio_device: int | None = None,
        input_device_label: str = "",
        routing_mode: str = "simulation",
        routing_status: str = "",
        timeline_resolver=None,
    ) -> SessionHandle:
        timeline = ShowTimeline.from_json(show_path)
        return self.start_timeline_playback_session(
            audio_path,
            timeline,
            config_path=config_path,
            simulation_only=simulation_only,
            fallback_to_simulation=fallback_to_simulation,
            mode="saved_show",
            show_path=show_path,
            audio_device=audio_device,
            input_device_label=input_device_label,
            routing_mode=routing_mode,
            routing_status=routing_status,
            timeline_resolver=timeline_resolver,
        )

    def start_reactive_live_session(
        self,
        *,
        config_path: Path | None = None,
        profile=None,
        director_config=None,
        simulation_only: bool = True,
        fallback_to_simulation: bool = True,
        audio_device: int | None = None,
        sample_rate: int = 44100,
        channels: int = 1,
        frame_size: int = 2048,
        hop_size: int = 512,
        blocksize: int = 1024,
        auto_cycle: bool = True,
        half_time: bool = False,
        max_brightness: bool = False,
        debug_mood: bool = False,
        effect_mode: str = "reactive",
        render_mode: str = "scroll",
        cycle_interval: float = 16.0,
        telemetry_dir: Path | None = None,
        crossfade_detect: bool = False,
        profile_strategy: str = "active_profile",
        profile_override_path: str = "",
        rotation_profiles: tuple[str, ...] = (),
        rotation_interval: float = 300.0,
        auto_palette: bool = False,
        smart_rotation: bool = False,
        chain_blend_seconds: float = 8.0,
        input_device_label: str = "",
        routing_mode: str = "simulation",
        routing_status: str = "",
    ) -> SessionHandle:
        stop_event = threading.Event()
        session_ref: list[Any] = [None]
        adapter = self.build_output_adapter(
            config_path,
            simulation_only=simulation_only,
            fallback_to_simulation=fallback_to_simulation,
        )
        runtime_control = RuntimeControlBus()
        resolved_profile = profile
        profile_rotation = None
        profile_chain = None
        if profile_strategy == "override_profile" and profile_override_path:
            resolved_profile = load_profile(resolve_profile_path(profile_override_path))
        elif profile_strategy == "auto_profile":
            resolved_profile = profile
        elif profile_strategy in {"profile_rotation", "smart_rotation"} and rotation_profiles:
            profiles = [load_profile(resolve_profile_path(value)) for value in rotation_profiles]
            if profile_strategy == "smart_rotation" or smart_rotation or auto_palette:
                profile_chain = ProfileChain(
                    profiles,
                    config=ChainConfig(blend_duration=chain_blend_seconds),
                )
                resolved_profile = profiles[0]
            else:
                profile_rotation = ProfileRotation(profiles, interval_seconds=rotation_interval)
                resolved_profile = profile_rotation.current
        handle = SessionHandle(
            mode="reactive_live",
            stop_event=stop_event,
            thread=threading.Thread(),
            session_ref=session_ref,
        )
        session = ReactiveLiveSession(
            adapter,
            audio_device=audio_device,
            director_config=director_config,
            profile=resolved_profile,
            effect_mode=effect_mode,
            render_mode=render_mode,
            sample_rate=sample_rate,
            channels=channels,
            frame_size=frame_size,
            hop_size=hop_size,
            blocksize=blocksize,
            auto_cycle=auto_cycle,
            half_time=half_time,
            max_brightness=max_brightness,
            debug_mood=debug_mood,
            cycle_interval=cycle_interval,
            telemetry_dir=telemetry_dir,
            crossfade_detect=crossfade_detect,
            input_device_label=input_device_label,
            routing_mode=routing_mode,
            routing_status=routing_status,
            profile_rotation=profile_rotation,
            profile_chain=profile_chain,
            runtime_control=runtime_control,
        )
        session_ref[:] = [session]

        def _runner() -> None:
            try:
                handle.summary = session.run(stop_event)
            except Exception as exc:  # pragma: no cover - surfaced via handle
                handle.error = exc

        handle.thread = threading.Thread(target=_runner, name="gui-reactive-session", daemon=True)
        handle.thread.start()
        return handle
