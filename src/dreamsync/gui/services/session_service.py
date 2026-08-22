"""Background session start/stop helpers for the GUI."""

from __future__ import annotations

import faulthandler
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from dreamsync.capture.writer import check_ffmpeg
from dreamsync.local_session import (
    _device_status_for_adapter,
    _format_audio_output_label,
    run_precompiled_show_session,
    run_local_session,
)
from dreamsync.output.auto_detect import load_device_config
from dreamsync.output.null_adapter import NullMultiAdapter, PreviewMirrorAdapter, SimulationMultiAdapter
from dreamsync.playlist import PlaylistManager
from dreamsync.profile import ProfileRotation, load_profile, resolve_profile_path
from dreamsync.profile_chain import ChainConfig, ProfileChain
from dreamsync.profile_generator import generate_profile_set
from dreamsync.render import RenderMode
from dreamsync.show.models import Show, ShowTimeline
from dreamsync.show.player import AudioPlayer
from dreamsync.show.playback_selector import choose_show_playback_runtime
from dreamsync.show.runtime_control import RuntimeControlBus, runtime_control_to_dict
from dreamsync.show.runtime import ShowPlaybackRuntime

from .output_lease_service import OutputLeaseService, QUEUE_AUDIO_OWNER

_PLAYBACK_FAULT_FILE = None
_PLAYBACK_DIAGNOSTICS_PATH = Path("out") / "playback_diagnostics.log"


def _write_playback_diagnostic(message: str) -> None:
    try:
        _PLAYBACK_DIAGNOSTICS_PATH.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().isoformat(timespec="seconds")
        with _PLAYBACK_DIAGNOSTICS_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {message}\n")
    except OSError:
        pass


def _enable_playback_fault_handler() -> None:
    global _PLAYBACK_FAULT_FILE
    if _PLAYBACK_FAULT_FILE is not None:
        return
    try:
        _PLAYBACK_DIAGNOSTICS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PLAYBACK_FAULT_FILE = _PLAYBACK_DIAGNOSTICS_PATH.open("a", encoding="utf-8")
        faulthandler.enable(file=_PLAYBACK_FAULT_FILE, all_threads=True)
    except OSError:
        _PLAYBACK_FAULT_FILE = None


@dataclass
class SessionHandle:
    mode: str
    stop_event: threading.Event
    thread: threading.Thread
    session_ref: list[Any] = field(default_factory=lambda: [None])
    summary: dict[str, Any] | None = None
    error: BaseException | None = None

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


class SpotifyTimelineSession:
    """Tick a compiled show from Spotify's interpolated clock without audio replay."""

    def __init__(self, multi_adapter, timeline: ShowTimeline, interpolator) -> None:
        self._multi_adapter = multi_adapter
        self._timeline = timeline
        self._interpolator = interpolator
        self._runtime = ShowPlaybackRuntime(timeline, multi_adapter)
        self._playback_state = "starting"
        self._frames_sent = 0

    def run(self, stop_event: threading.Event) -> dict[str, Any]:
        self._multi_adapter.activate(brightness=100)
        try:
            while not stop_event.wait(0.005):
                self._playback_state = (
                    "playing" if self._interpolator.is_playing else "paused"
                )
                if self._interpolator.is_playing and self._runtime.tick(
                    self._interpolator.position_seconds
                ):
                    self._frames_sent += 1
        finally:
            self._multi_adapter.deactivate()
        return {"mode": "spotify_compiled_live", "frames_sent": self._frames_sent}

    def session_snapshot(self) -> dict[str, Any]:
        return {
            "playback_state": self._playback_state,
            "current_track": str(self._timeline.metadata.get("track_name", "")),
            "device_status": _device_status_for_adapter(self._multi_adapter),
            "audio_output": "Spotify",
        }


class TimelineLightingPreviewSession:
    """Drive a captured timeline from a wall clock without audible playback."""

    def __init__(self, multi_adapter, timeline: ShowTimeline, *, mode: str) -> None:
        self._multi_adapter = multi_adapter
        self._timeline = timeline
        self._mode = mode
        self._runtime = ShowPlaybackRuntime(timeline, multi_adapter)
        self._playback_state = "idle"
        self._position_seconds = 0.0
        self._frames_sent = 0

    def run(self, stop_event: threading.Event) -> dict[str, Any]:
        started_at = time.monotonic()
        self._multi_adapter.activate(brightness=100)
        self._playback_state = "playing"
        try:
            while not stop_event.wait(0.005):
                self._position_seconds = max(0.0, time.monotonic() - started_at)
                if self._position_seconds >= self._timeline.duration:
                    self._playback_state = "finished"
                    break
                if self._runtime.tick(self._position_seconds):
                    self._frames_sent += 1
        finally:
            if self._playback_state != "finished":
                self._playback_state = "stopped"
            self._multi_adapter.deactivate()
        return {
            "mode": self._mode,
            "duration": self._timeline.duration,
            "frames_sent": self._frames_sent,
            "audio_output": "none (lighting-only preview)",
        }

    def session_snapshot(self) -> dict[str, Any]:
        return {
            "mode": self._mode,
            "playback_state": self._playback_state,
            "position_seconds": self._position_seconds,
            "duration_seconds": self._timeline.duration,
            "audio_output": "none (lighting-only preview)",
            "device_status": _device_status_for_adapter(self._multi_adapter),
        }

    def preview_frame_snapshot(self) -> dict[str, Any]:
        preview_snapshot = getattr(self._multi_adapter, "preview_snapshot", None)
        if callable(preview_snapshot):
            return dict(preview_snapshot())
        return {"node_colors": {}}


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
        audio_device: int | str | None = None,
        input_device_label: str = "",
        routing_mode: str = "simulation",
        routing_status: str = "",
        runtime_control: RuntimeControlBus | None = None,
        start_seconds: float = 0.0,
        config_path: Path | None = None,
        baked_playback_mode: str = "auto",
        audio_player_factory=None,
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
        self._config_path = Path(config_path) if config_path is not None else None
        self._baked_playback_mode = baked_playback_mode
        self._audio_player_factory = audio_player_factory
        self._status_lock = threading.RLock()
        self._player: AudioPlayer | None = None
        self._runtime: Any | None = None
        self._playback_state = "idle"
        self._playback_diagnostics: dict[str, Any] = {}

    def run(self, stop_event: threading.Event) -> dict[str, Any]:
        _enable_playback_fault_handler()
        _write_playback_diagnostic(
            f"timeline session start mode={self._mode} track={self._audio_path} "
            f"start_seconds={self._start_seconds:.3f} cues={len(self._timeline.cues)}"
        )
        player: AudioPlayer | None = None
        runtime: Any | None = None
        playback_diagnostics: dict[str, Any] = {}
        frames_sent = 0
        started_at = time.monotonic()
        try:
            _write_playback_diagnostic("activate adapter")
            self._multi_adapter.activate(brightness=100)
            self._set_playback_state("loading_audio")
            _write_playback_diagnostic("decode audio")
            player_factory = self._audio_player_factory or AudioPlayer
            player = player_factory(
                self._audio_path,
                sample_rate=44100,
                device=self._audio_device,
            )
            if self._start_seconds > 0.0:
                _write_playback_diagnostic(f"seek audio {self._start_seconds:.3f}s")
                player.seek(self._start_seconds)
            _write_playback_diagnostic("choose show runtime")
            choice = choose_show_playback_runtime(
                self._timeline,
                self._multi_adapter,
                show_path=self._show_path,
                device_config_path=self._config_path,
                baked_playback_mode=self._baked_playback_mode,
                control_state_getter=self._runtime_control.snapshot,
            )
            runtime = choice.runtime
            playback_diagnostics = choice.diagnostics
            _write_playback_diagnostic(
                "selected playback runtime "
                f"mode={choice.playback_mode_used} "
                f"reason={choice.baked_validation.reason or 'valid'}"
            )
            self._set_playback_state("preparing_spatial")
            prepare_spatial = getattr(runtime, "prepare_spatial_playback", None)
            if callable(prepare_spatial):
                _write_playback_diagnostic("prepare spatial start")
                prepared_count = prepare_spatial()
                _write_playback_diagnostic(f"prepare spatial done count={prepared_count}")
            with self._status_lock:
                self._player = player
                self._runtime = runtime
                self._playback_diagnostics = playback_diagnostics
                self._playback_state = "starting_audio"
            _write_playback_diagnostic("start audio stream")
            player.play()
            self._set_playback_state("playing")
            frame_interval = self._target_frame_interval()
            next_frame_at = 0.0
            while not stop_event.is_set():
                loop_now = time.monotonic()
                if player.finished:
                    self._set_playback_state("finished")
                    break
                if not player.playing:
                    self._set_playback_state("paused")
                    time.sleep(0.05)
                    continue
                if loop_now < next_frame_at:
                    time.sleep(min(0.01, max(0.001, next_frame_at - loop_now)))
                    continue
                self._set_playback_state("playing")
                tick_position = player.position_seconds
                if runtime.tick(tick_position):
                    frames_sent += 1
                next_frame_at = time.monotonic() + frame_interval
                time.sleep(0.005)
        finally:
            runtime_stats = runtime.stats if runtime is not None and hasattr(runtime, "stats") else {}
            _write_playback_diagnostic(
                f"stop session frames_sent={frames_sent} runtime_stats={runtime_stats}"
            )
            if player is not None:
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
            **playback_diagnostics,
            **(runtime.stats if runtime is not None and hasattr(runtime, "stats") else {}),
        }

    def session_snapshot(self) -> dict[str, Any]:
        with self._status_lock:
            player = self._player
            runtime = self._runtime
            state = self._playback_state
            playback_diagnostics = dict(self._playback_diagnostics)
            position_seconds = player.position_seconds if player is not None else 0.0
            is_playing = player.playing if player is not None else False
            current_cue = getattr(runtime, "current_cue", None) if runtime is not None else None
            runtime_stats = dict(runtime.stats) if runtime is not None and hasattr(runtime, "stats") else {}
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
            **playback_diagnostics,
            **runtime_stats,
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

    def _target_frame_interval(self) -> float:
        fps_values: list[float] = []
        for adapter, *_rest in getattr(self._multi_adapter, "devices", ()):
            config = getattr(adapter, "config", None)
            fps = getattr(config, "fps", None)
            try:
                fps_value = float(fps)
            except (TypeError, ValueError):
                continue
            if fps_value > 0:
                fps_values.append(fps_value)
        target_fps = min(fps_values) if fps_values else 30.0
        target_fps = max(1.0, min(60.0, target_fps))
        return 1.0 / target_fps


class ReactiveLiveSession:
    """Thin GUI wrapper around the live reactive runtime."""

    def __init__(
        self,
        multi_adapter,
        *,
        audio_device: int | str | None = None,
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
        master_brightness: float = 1.0,
        debug_mood: bool = False,
        cycle_interval: float = 16.0,
        telemetry_dir: Path | None = None,
        crossfade_detect: bool = False,
        input_device_label: str = "",
        routing_mode: str = "simulation",
        routing_status: str = "",
        profile_rotation=None,
        profile_chain=None,
        profile_switch_on_song_change: bool = False,
        show_palette_cycle: tuple[str, ...] = (),
        runtime_control: RuntimeControlBus | None = None,
        structure_config=None,
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
        self._master_brightness = master_brightness
        self._debug_mood = debug_mood
        self._cycle_interval = cycle_interval
        self._telemetry_dir = telemetry_dir
        self._crossfade_detect = crossfade_detect
        self._input_device_label = input_device_label
        self._routing_mode = routing_mode
        self._routing_status = routing_status
        self._profile_rotation = profile_rotation
        self._profile_chain = profile_chain
        self._profile_switch_on_song_change = profile_switch_on_song_change
        self._show_palette_cycle = show_palette_cycle
        self._runtime_control = runtime_control or RuntimeControlBus()
        self._structure_config = structure_config
        self._raw_visualizer_noise_threshold = (
            raw_visualizer_noise_threshold
        )
        self._raw_visualizer_gradient_points = tuple(
            raw_visualizer_gradient_points
        )
        self._raw_visualizer_origins = dict(
            raw_visualizer_origins or {}
        )
        self._raw_palette_lock = threading.RLock()
        self._raw_palette_control_state = {
            "revision": 0,
            "pool": tuple(raw_visualizer_palette_pool),
            "selected_name": str(raw_visualizer_palette_name),
            "use_custom": not bool(raw_visualizer_palette_name),
            "auto_chain": bool(raw_visualizer_auto_palette),
            "interval_seconds": float(
                raw_visualizer_palette_interval
            ),
        }
        self._predictive_cues_enabled = bool(
            getattr(structure_config, "predictive_cues_enabled", False)
            or getattr(structure_config, "structure_bar_actions_enabled", False)
            or getattr(structure_config, "structure_phrase_actions_enabled", False)
            or getattr(structure_config, "structure_section_actions_enabled", False)
        )
        self._structure_action_controls = (
            bool(
                getattr(
                    structure_config,
                    "structure_similarity_shadow_mode",
                    True,
                )
            ),
            bool(getattr(structure_config, "structure_bar_actions_enabled", False)),
            bool(getattr(structure_config, "structure_phrase_actions_enabled", False)),
            bool(getattr(structure_config, "structure_section_actions_enabled", False)),
        )
        self._downbeat_nudge_revision = 0
        self._downbeat_nudge_requested_at = 0.0
        self._manual_beat_kind = "downbeat"
        self._cycle_tempo_multiplier = 1.0
        self._status_lock = threading.RLock()
        self._playback_state = "idle"
        self._started_at: float | None = None
        self._summary: dict[str, Any] | None = None
        self._latest_runtime_state: dict[str, Any] = {}

    def run(self, stop_event: threading.Event) -> dict[str, Any]:
        from dreamsync.live import run_live_to_govee

        raw_visualizer = self._effect_mode == "raw_visualizer"
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
            master_brightness=self._master_brightness,
            auto_cycle=self._auto_cycle and not raw_visualizer,
            cycle_interval=self._cycle_interval,
            debug_mood=self._debug_mood,
            stop_event=stop_event,
            telemetry_dir=self._telemetry_dir,
            crossfade_detect=self._crossfade_detect and not raw_visualizer,
            profile=self._profile,
            show_palette_cycle=self._show_palette_cycle,
            profile_rotation=self._profile_rotation,
            profile_chain=self._profile_chain,
            profile_switch_on_song_change=self._profile_switch_on_song_change,
            runtime_control_getter=self._runtime_control.snapshot,
            predictive_cues_enabled_getter=(
                self.predictive_cues_enabled
            ),
            structure_action_controls_getter=self.structure_action_controls,
            downbeat_nudge_request_getter=self.downbeat_nudge_request,
            cycle_tempo_multiplier_getter=self.cycle_tempo_multiplier,
            state_callback=self._update_runtime_state,
            render_mode_policy="fixed" if raw_visualizer else "adaptive",
            render_mode="solid" if raw_visualizer else self._render_mode,
            structure_config=(None if raw_visualizer else self._structure_config),
            raw_visualizer=raw_visualizer,
            raw_visualizer_noise_threshold=(
                self._raw_visualizer_noise_threshold
            ),
            raw_visualizer_gradient_points=(
                self._raw_visualizer_gradient_points
            ),
            raw_visualizer_origins=self._raw_visualizer_origins,
            raw_visualizer_palette_control_getter=(
                self.raw_visualizer_palette_control
            ),
        )
        with self._status_lock:
            self._summary = dict(summary)
            self._playback_state = "stopped" if stop_event.is_set() else "finished"
        return {
            "mode": (
                "raw_visualizer_live"
                if raw_visualizer
                else "reactive_live"
            ),
            **summary,
        }

    def predictive_cues_enabled(self) -> bool:
        with self._status_lock:
            return self._predictive_cues_enabled

    def set_predictive_cues_enabled(self, enabled: bool) -> None:
        with self._status_lock:
            self._predictive_cues_enabled = bool(enabled)

    def structure_action_controls(self) -> tuple[bool, bool, bool, bool]:
        with self._status_lock:
            return self._structure_action_controls

    def set_structure_action_controls(
        self,
        *,
        shadow_mode: bool,
        bar_actions: bool,
        phrase_actions: bool,
        section_actions: bool,
    ) -> None:
        with self._status_lock:
            self._structure_action_controls = (
                bool(shadow_mode),
                bool(bar_actions),
                bool(phrase_actions),
                bool(section_actions),
            )

    def request_downbeat_nudge(self) -> int:
        return self.request_manual_beat("downbeat")

    def request_detection_reset(self) -> int:
        with self._status_lock:
            self._cycle_tempo_multiplier = 1.0
        return self.request_manual_beat("reset")

    def request_manual_beat(self, kind: str) -> int:
        normalized = str(kind).strip().lower()
        if normalized not in {"beat", "downbeat", "reset"}:
            raise ValueError(
                "manual beat kind must be 'beat', 'downbeat', or 'reset'"
            )
        with self._status_lock:
            self._downbeat_nudge_revision += 1
            self._downbeat_nudge_requested_at = time.monotonic()
            self._manual_beat_kind = normalized
            return self._downbeat_nudge_revision

    def downbeat_nudge_revision(self) -> int:
        with self._status_lock:
            return self._downbeat_nudge_revision

    def downbeat_nudge_request(self) -> tuple[int, float, str]:
        with self._status_lock:
            return (
                self._downbeat_nudge_revision,
                self._downbeat_nudge_requested_at,
                self._manual_beat_kind,
            )

    def set_cycle_tempo_multiplier(self, multiplier: float) -> float:
        value = float(multiplier)
        if value not in {0.5, 1.0, 2.0}:
            raise ValueError(
                "cycle tempo multiplier must be 0.5, 1, or 2"
            )
        with self._status_lock:
            self._cycle_tempo_multiplier = value
            return value

    def cycle_tempo_multiplier(self) -> float:
        with self._status_lock:
            return self._cycle_tempo_multiplier

    def session_snapshot(self) -> dict[str, Any]:
        with self._status_lock:
            started_at = self._started_at
            state = self._playback_state
            runtime_state = dict(self._latest_runtime_state)
        elapsed = 0.0 if started_at is None else max(0.0, time.monotonic() - started_at)
        return {
            "mode": (
                "raw_visualizer_live"
                if self._effect_mode == "raw_visualizer"
                else "reactive_live"
            ),
            "current_track": (
                "Raw Visualizer"
                if self._effect_mode == "raw_visualizer"
                else "Reactive Live"
            ),
            "current_index": -1,
            "tracks_played": 0,
            "tracks_skipped": 0,
            "precompiled": 0,
            "queue": {"current_index": -1, "tracks": ()},
            "playback_state": state,
            "is_playing": state in {"starting", "playing", "waiting_for_audio", "listening"},
            "position_seconds": elapsed,
            "duration_seconds": 0.0,
            # Reactive live mode is capture-only. The selected device is an
            # input source and must never be represented or opened as output.
            "audio_output": "none (reactive capture-only)",
            "input_device": self._input_device_label,
            "routing_mode": self._routing_mode,
            "routing_status": self._routing_status,
            "device_status": _device_status_for_adapter(self._multi_adapter),
            "effect_mode": self._effect_mode,
            "render_mode": self._render_mode,
            "current_palette": tuple(runtime_state.get("current_palette", ())),
            "active_palette_name": str(runtime_state.get("active_palette_name", "") or ""),
            "palette_queue": tuple(runtime_state.get("palette_queue", ()) or ()),
            "palette_cycle_mode": str(runtime_state.get("palette_cycle_mode", "") or ""),
            "palette_seconds_until_next": runtime_state.get("palette_seconds_until_next"),
            "active_profile_name": str(runtime_state.get("active_profile_name", "") or ""),
            "profile_cycle_mode": str(runtime_state.get("profile_cycle_mode", "") or ""),
            "runtime_control": runtime_control_to_dict(self._runtime_control.snapshot()),
            "runtime_state": runtime_state,
            "dominant_band": runtime_state.get("dominant_band", ""),
            "dominant_proxy": runtime_state.get("dominant_proxy", ""),
            "pan_center": runtime_state.get("pan_center", 0.0),
            "pan_width": runtime_state.get("pan_width", 0.0),
            "listening": bool(runtime_state.get("listening", False)),
            "bpm": float(runtime_state.get("bpm", 0.0) or 0.0),
            "detected_bpm": float(
                runtime_state.get("detected_bpm", 0.0) or 0.0
            ),
            "cycle_bpm": float(
                runtime_state.get("cycle_bpm", 0.0) or 0.0
            ),
            "cycle_tempo_multiplier": float(
                runtime_state.get("cycle_tempo_multiplier", 1.0) or 1.0
            ),
            "beat": bool(runtime_state.get("beat", False)),
            "last_beat_monotonic": float(runtime_state.get("last_beat_monotonic", 0.0) or 0.0),
            "stability": float(runtime_state.get("stability", 0.0) or 0.0),
            "cyclic_grid_bpm": float(runtime_state.get("cyclic_grid_bpm", 0.0) or 0.0),
            "cyclic_grid_confidence": float(
                runtime_state.get("cyclic_grid_confidence", 0.0) or 0.0
            ),
            "meter_downbeat": bool(runtime_state.get("meter_downbeat", False)),
            "meter_bar_phase": runtime_state.get("meter_bar_phase"),
            "meter_relative_phase": runtime_state.get("meter_relative_phase"),
            "meter_confidence": float(
                runtime_state.get("meter_confidence", 0.0) or 0.0
            ),
            "meter_confident": bool(runtime_state.get("meter_confident", False)),
            "meter_evidence": float(
                runtime_state.get("meter_evidence", 0.0) or 0.0
            ),
            "harmonic_enabled": bool(
                runtime_state.get("harmonic_enabled", False)
            ),
            "harmonic_chord": str(
                runtime_state.get("harmonic_chord", "") or ""
            ),
            "detected_chord": str(
                runtime_state.get("detected_chord", "") or ""
            ),
            "detected_chord_history": tuple(
                runtime_state.get("detected_chord_history", ()) or ()
            ),
            "detected_bar_chord_history": tuple(
                runtime_state.get(
                    "detected_bar_chord_history",
                    (),
                )
                or ()
            ),
            "detected_chord_changes": tuple(
                runtime_state.get("detected_chord_changes", ()) or ()
            ),
            "detected_chord_change": dict(
                runtime_state.get("detected_chord_change", {}) or {}
            ),
            "detected_chord_prediction": dict(
                runtime_state.get(
                    "detected_chord_prediction",
                    {},
                )
                or {}
            ),
            "detected_chord_prediction_seconds": runtime_state.get(
                "detected_chord_prediction_seconds"
            ),
            "detected_chord_prediction_mismatch": dict(
                runtime_state.get(
                    "detected_chord_prediction_mismatch",
                    {},
                )
                or {}
            ),
            "detected_chord_prediction_mismatch_active": bool(
                runtime_state.get(
                    "detected_chord_prediction_mismatch_active",
                    False,
                )
            ),
            "harmonic_debug_enabled": bool(
                runtime_state.get("harmonic_debug_enabled", False)
            ),
            "harmonic_debug_spectrum": tuple(
                runtime_state.get("harmonic_debug_spectrum", ()) or ()
            ),
            "harmonic_debug_chroma": tuple(
                runtime_state.get("harmonic_debug_chroma", ()) or ()
            ),
            "harmonic_debug_chord": str(
                runtime_state.get("harmonic_debug_chord", "") or ""
            ),
            "harmonic_debug_chord_tones": tuple(
                runtime_state.get("harmonic_debug_chord_tones", ()) or ()
            ),
            "harmonic_debug_root_note": str(
                runtime_state.get("harmonic_debug_root_note", "") or ""
            ),
            "harmonic_debug_non_chord_tones": tuple(
                runtime_state.get(
                    "harmonic_debug_non_chord_tones",
                    (),
                )
                or ()
            ),
            "harmonic_confidence": float(
                runtime_state.get("harmonic_confidence", 0.0) or 0.0
            ),
            "harmonic_chord_confidence": float(
                runtime_state.get("harmonic_chord_confidence", 0.0) or 0.0
            ),
            "harmonic_novelty": float(
                runtime_state.get("harmonic_novelty", 0.0) or 0.0
            ),
            "harmonic_change": bool(
                runtime_state.get("harmonic_change", False)
            ),
            "harmonic_accent": float(
                runtime_state.get("harmonic_accent", 0.0) or 0.0
            ),
            "macro_candidate": bool(
                runtime_state.get("macro_candidate", False)
            ),
            "macro_change": bool(runtime_state.get("macro_change", False)),
            "structure_event": str(
                runtime_state.get("structure_event", "") or ""
            ),
            "structure_confidence": float(
                runtime_state.get("structure_confidence", 0.0) or 0.0
            ),
            "structure_bar_index": runtime_state.get(
                "structure_bar_index"
            ),
            "structure_phrase_index": runtime_state.get(
                "structure_phrase_index"
            ),
            "predictive_cycle": dict(
                runtime_state.get("predictive_cycle", {}) or {}
            ),
            "song_boundaries": int(runtime_state.get("song_boundaries", 0) or 0),
            "automatic_detection_reset_count": int(
                runtime_state.get("automatic_detection_reset_count", 0) or 0
            ),
            "last_detection_reset_reason": str(
                runtime_state.get("last_detection_reset_reason", "") or ""
            ),
            "captured_samples": int(runtime_state.get("captured_samples", 0) or 0),
            "input_overflows": int(runtime_state.get("input_overflows", 0) or 0),
            "audio_ring_capacity": int(runtime_state.get("audio_ring_capacity", 0) or 0),
            "audio_ring_depth": int(runtime_state.get("audio_ring_depth", 0) or 0),
            "audio_ring_fill": float(runtime_state.get("audio_ring_fill", 0.0) or 0.0),
            "audio_lag_ms": float(runtime_state.get("audio_lag_ms", 0.0) or 0.0),
            "analysis_dropped_blocks": int(
                runtime_state.get("analysis_dropped_blocks", 0) or 0
            ),
            "analysis_discontinuities": int(
                runtime_state.get("analysis_discontinuities", 0) or 0
            ),
            "analysis_frame_ms_p95": float(
                runtime_state.get("analysis_frame_ms_p95", 0.0) or 0.0
            ),
            "harmonic_frame_ms_p95": float(
                runtime_state.get("harmonic_frame_ms_p95", 0.0) or 0.0
            ),
            "waveform_points": tuple(runtime_state.get("waveform_points", ()) or ()),
            "eq_band_points": {
                str(name): tuple(points or ())
                for name, points in dict(runtime_state.get("eq_band_points", {}) or {}).items()
            },
            "detected_beat_times": tuple(runtime_state.get("detected_beat_times", ()) or ()),
            "detected_downbeat_times": tuple(
                runtime_state.get("detected_downbeat_times", ()) or ()
            ),
            "predicted_beat_times": tuple(
                runtime_state.get("predicted_beat_times", ()) or ()
            ),
            "upcoming_effect_cues": tuple(
                runtime_state.get("upcoming_effect_cues", ()) or ()
            ),
            "active_effects": tuple(
                runtime_state.get("active_effects", ()) or ()
            ),
            "effect_trigger_history": tuple(
                runtime_state.get("effect_trigger_history", ()) or ()
            ),
            "stream_t": float(runtime_state.get("stream_t", 0.0) or 0.0),
            "waveform_window_seconds": float(
                runtime_state.get("waveform_window_seconds", 10.0) or 10.0
            ),
            # Structure-similarity diagnostics are produced by the live
            # runtime as a forward-compatible family. Promote the complete
            # family so Reactive UI consumers do not silently fall back to
            # legacy chord-cycle fields when new diagnostics are added.
            **{
                key: value
                for key, value in runtime_state.items()
                if key.startswith("structure_")
                or key.startswith("manual_")
                or key == "meter_time_signature"
            },
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

    def raw_visualizer_palette_control(self) -> dict[str, Any]:
        with self._raw_palette_lock:
            return dict(self._raw_palette_control_state)

    def update_raw_visualizer_palette_control(
        self,
        *,
        pool: tuple[tuple[str, tuple[str, ...]], ...],
        selected_name: str,
        auto_chain: bool,
        interval_seconds: float,
    ) -> dict[str, Any]:
        with self._raw_palette_lock:
            revision = int(
                self._raw_palette_control_state.get("revision", 0)
            ) + 1
            self._raw_palette_control_state = {
                "revision": revision,
                "pool": tuple(pool),
                "selected_name": str(selected_name),
                "use_custom": not bool(selected_name),
                "auto_chain": bool(auto_chain),
                "interval_seconds": float(interval_seconds),
            }
            return dict(self._raw_palette_control_state)

    def _update_runtime_state(self, state: dict[str, Any]) -> None:
        with self._status_lock:
            next_state = dict(self._latest_runtime_state)
            next_state.update(state)
            if state.get("beat"):
                next_state["last_beat_monotonic"] = time.monotonic()
            self._latest_runtime_state = next_state
            self._playback_state = (
                "listening" if bool(state.get("listening")) else "waiting_for_audio"
            )


class SessionService:
    """Start manageable background sessions for the GUI."""

    def __init__(self, *, output_leases: OutputLeaseService | None = None) -> None:
        self._output_leases = output_leases or OutputLeaseService()
        self._hardware_adapter_key: tuple | None = None
        self._hardware_adapter = None

    def bind_output_leases(self, output_leases: OutputLeaseService) -> None:
        """Use the supervisor's lease registry for subsequent session starts."""

        self._output_leases = output_leases

    def _require_queue_session(self, mode: str) -> None:
        allowed_modes = {
            "local",
            "saved_show",
            "queue",
            "queue_pipeline_playback",
        }
        if mode not in allowed_modes:
            # Route the violation through the lease service so diagnostics retain it.
            self._output_leases.acquire_audio(owner=mode, mode=mode)
        if self._output_leases.audio_snapshot().owner != QUEUE_AUDIO_OWNER:
            self._output_leases.acquire_audio(owner=QUEUE_AUDIO_OWNER, mode=mode)
        self._output_leases.require_queue_audio(mode=mode)

    def create_queue_audio_player(
        self,
        audio_path: Path,
        *,
        sample_rate: int = 44100,
        blocksize: int = 1024,
        device: int | None = None,
        mode: str = "queue",
    ) -> AudioPlayer:
        """Construct the sole audible player after verifying Queue ownership."""

        self._output_leases.require_queue_audio(mode=mode)
        return AudioPlayer(
            audio_path,
            sample_rate=sample_rate,
            blocksize=blocksize,
            device=device,
        )

    @staticmethod
    def require_local_preview_dependencies() -> None:
        """Validate runtime dependencies for local preview playback."""
        if not check_ffmpeg():
            raise RuntimeError(
                "ffmpeg is required for local preview playback. "
                "Install ffmpeg and confirm 'ffmpeg -version' works in this shell."
            )

    def warm_hardware_adapter(self, config_path: Path | None) -> None:
        """Create the persistent hardware adapter during health checking."""
        adapter = self.build_output_adapter(
            config_path,
            simulation_only=False,
            fallback_to_simulation=False,
        )
        connect = getattr(adapter, "connect_ble_followers", None)
        if callable(connect):
            connect()

    def hardware_health_snapshot(self) -> dict[str, dict[str, str | int]]:
        """Read observations from the active persistent adapter; never probe."""
        adapter = self._hardware_adapter
        snapshot = getattr(adapter, "device_health_snapshot", None)
        return dict(snapshot()) if callable(snapshot) else {}

    def build_output_adapter(
        self,
        config_path: Path | None,
        *,
        simulation_only: bool = True,
        fallback_to_simulation: bool = True,
        render_mode: str = "scroll",
        mirror: bool = True,
        brightness: float = 1.0,
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
            return SimulationMultiAdapter.from_configs(
                configs,
                render_mode=RenderMode(render_mode),
                mirror=mirror,
            )
        try:
            from dreamsync.output.auto_detect import build_multi_adapter, detect_all_devices

            adapter_key = (
                str(config_path.resolve()),
                config_path.stat().st_mtime_ns,
                render_mode,
                mirror,
                brightness,
            )
            if (
                self._hardware_adapter_key == adapter_key
                and self._hardware_adapter is not None
            ):
                return self._hardware_adapter

            previous_adapter = self._hardware_adapter
            self._hardware_adapter = None
            self._hardware_adapter_key = None
            shutdown = getattr(previous_adapter, "shutdown", None)
            if callable(shutdown):
                shutdown()

            detected = detect_all_devices(configs, parallel=True, probe_ble=False)
            adapter = build_multi_adapter(
                detected,
                render_mode=RenderMode(render_mode),
                mirror=mirror,
                brightness=brightness,
            )
            if isinstance(adapter, NullMultiAdapter):
                if not fallback_to_simulation:
                    raise RuntimeError("No configured hardware devices were reachable.")
                return SimulationMultiAdapter.from_configs(
                    configs,
                    render_mode=RenderMode(render_mode),
                    mirror=mirror,
                )
            result = PreviewMirrorAdapter(
                adapter,
                SimulationMultiAdapter.from_configs(
                    configs,
                    render_mode=RenderMode(render_mode),
                    mirror=mirror,
                ),
            )
            keep_connected = getattr(result, "keep_ble_connected", None)
            if callable(keep_connected):
                keep_connected(True)
            self._hardware_adapter_key = adapter_key
            self._hardware_adapter = result
            return result
        except Exception:
            if not fallback_to_simulation:
                raise
            return SimulationMultiAdapter.from_configs(
                configs,
                render_mode=RenderMode(render_mode),
                mirror=mirror,
            )

    def start_local_preview_session(
        self,
        audio_path: Path,
        *,
        playlist: PlaylistManager | None = None,
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
        precompiled_timelines: dict[str, ShowTimeline] | None = None,
        precompiled_timeline_sources: dict[str, str] | None = None,
    ) -> SessionHandle:
        self._require_queue_session("local")
        self.require_local_preview_dependencies()
        stop_event = threading.Event()
        session_ref: list[Any] = [None]
        playlist = playlist or PlaylistManager.from_path(
            audio_path,
            shuffle=shuffle,
            repeat=repeat,
        )
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
                    precompiled_timelines=precompiled_timelines,
                    precompiled_timeline_sources=precompiled_timeline_sources,
                    audio_player_factory=(
                        lambda path, **kwargs: self.create_queue_audio_player(
                            path,
                            mode="local",
                            **kwargs,
                        )
                    ),
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
        baked_playback_mode: str = "auto",
    ) -> SessionHandle:
        self._require_queue_session(mode)
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
            config_path=config_path,
            baked_playback_mode=baked_playback_mode,
            audio_player_factory=(
                lambda path, **kwargs: self.create_queue_audio_player(
                    path,
                    mode=mode,
                    **kwargs,
                )
            ),
        )
        session_ref[:] = [session]

        def _runner() -> None:
            try:
                handle.summary = session.run(stop_event)
            except BaseException as exc:  # pragma: no cover - surfaced via handle
                _write_playback_diagnostic(
                    "timeline session exception:\n" + "".join(
                        traceback.format_exception(type(exc), exc, exc.__traceback__)
                    ).rstrip()
                )
                handle.error = exc

        handle.thread = threading.Thread(target=_runner, name=f"gui-{mode}-session", daemon=True)
        handle.thread.start()
        return handle

    def start_timeline_preview_session(
        self,
        timeline: ShowTimeline,
        *,
        config_path: Path | None = None,
        mode: str = "simulation_preview",
        timeline_resolver=None,
        audio_path: Path | None = None,
    ) -> SessionHandle:
        """Start a silent, simulation-only timeline preview."""

        if timeline_resolver is not None and audio_path is not None:
            timeline = timeline_resolver(audio_path, timeline)
        stop_event = threading.Event()
        adapter = self.build_output_adapter(
            config_path,
            simulation_only=True,
            fallback_to_simulation=True,
        )
        session = TimelineLightingPreviewSession(adapter, timeline, mode=mode)
        handle = SessionHandle(
            mode=mode,
            stop_event=stop_event,
            thread=threading.Thread(),
            session_ref=[session],
        )

        def _runner() -> None:
            try:
                handle.summary = session.run(stop_event)
            except BaseException as exc:  # pragma: no cover - surfaced via handle
                handle.error = exc

        handle.thread = threading.Thread(
            target=_runner,
            name=f"gui-{mode}-session",
            daemon=True,
        )
        handle.thread.start()
        return handle

    def start_spotify_timeline_session(
        self,
        timeline: ShowTimeline,
        interpolator,
        *,
        config_path: Path | None = None,
        simulation_only: bool = True,
        fallback_to_simulation: bool = True,
    ) -> SessionHandle:
        stop_event = threading.Event()
        adapter = self.build_output_adapter(
            config_path,
            simulation_only=simulation_only,
            fallback_to_simulation=fallback_to_simulation,
        )
        session = SpotifyTimelineSession(adapter, timeline, interpolator)
        handle = SessionHandle(
            mode="spotify_compiled_live",
            stop_event=stop_event,
            thread=threading.Thread(),
            session_ref=[session],
        )

        def _runner() -> None:
            try:
                handle.summary = session.run(stop_event)
            except BaseException as exc:
                handle.error = exc

        handle.thread = threading.Thread(
            target=_runner, name="gui-spotify-compiled-live", daemon=True
        )
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
        baked_playback_mode: str = "auto",
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
            baked_playback_mode=baked_playback_mode,
        )

    def start_compiled_show_session(
        self,
        show: Show,
        *,
        show_path: Path | None = None,
        config_path: Path | None = None,
        simulation_only: bool = True,
        fallback_to_simulation: bool = True,
        audio_device: int | None = None,
    ) -> SessionHandle:
        """Start an ordered saved Show from its embedded Track timelines."""
        self._require_queue_session("saved_show")
        self.require_local_preview_dependencies()
        if not show.tracks:
            raise ValueError("Add at least one Track before playing this Show.")
        pending = [track.display_name for track in show.tracks if not track.is_compiled]
        if pending:
            raise ValueError("Compile every Track before playing this Show: " + ", ".join(pending))
        tracks: list[tuple[Path, ShowTimeline]] = []
        for track in show.tracks:
            audio_path = Path(track.audio_path)
            if not audio_path.is_file():
                raise FileNotFoundError(f"Audio Track not found: {audio_path}")
            assert track.timeline is not None
            tracks.append((audio_path, track.timeline))

        stop_event = threading.Event()
        session_ref: list[Any] = [None]
        adapter = self.build_output_adapter(
            config_path,
            simulation_only=simulation_only,
            fallback_to_simulation=fallback_to_simulation,
        )
        runtime_control = RuntimeControlBus()
        handle = SessionHandle(
            mode="saved_show",
            stop_event=stop_event,
            thread=threading.Thread(),
            session_ref=session_ref,
        )

        def _runner() -> None:
            try:
                handle.summary = run_precompiled_show_session(
                    adapter,
                    tuple(tracks),
                    source_path=show_path,
                    audio_device=audio_device,
                    stop_event=stop_event,
                    session_ref=session_ref,
                    runtime_control=runtime_control,
                    audio_player_factory=(
                        lambda path, **kwargs: self.create_queue_audio_player(
                            path,
                            mode="saved_show",
                            **kwargs,
                        )
                    ),
                )
            except BaseException as exc:  # pragma: no cover - surfaced via handle
                handle.error = exc

        handle.thread = threading.Thread(target=_runner, name="gui-saved-show-session", daemon=True)
        handle.thread.start()
        return handle

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
        mirror: bool = True,
        master_brightness: float = 1.0,
        debug_mood: bool = False,
        effect_mode: str = "reactive",
        render_mode: str = "scroll",
        cycle_interval: float = 16.0,
        telemetry_dir: Path | None = None,
        crossfade_detect: bool = False,
        profile_strategy: str = "active_profile",
        profile_override_path: str = "",
        show_palette_set: str = "",
        rotation_profiles: tuple[str, ...] = (),
        rotation_interval: float = 300.0,
        auto_palette: bool = False,
        smart_rotation: bool = False,
        chain_blend_seconds: float = 8.0,
        auto_palette_seed: int | None = None,
        auto_palette_pool_size: int = 8,
        chain_dwell_range_enabled: bool = False,
        chain_min_dwell_seconds: float = 60.0,
        chain_max_dwell_seconds: float = 180.0,
        input_device_label: str = "",
        routing_mode: str = "simulation",
        routing_status: str = "",
        structure_config=None,
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
        stop_event = threading.Event()
        session_ref: list[Any] = [None]
        raw_visualizer = effect_mode == "raw_visualizer"
        adapter = self.build_output_adapter(
            config_path,
            simulation_only=simulation_only,
            fallback_to_simulation=fallback_to_simulation,
            render_mode=render_mode,
            mirror=mirror,
            brightness=master_brightness,
        )
        runtime_control = RuntimeControlBus()
        resolved_profile = profile
        profile_rotation = None
        profile_chain = None
        profile_switch_on_song_change = False
        if raw_visualizer:
            # Raw mode owns its frequency gradient and never rotates profiles
            # or show palettes.
            profile_strategy = "active_profile"
            show_palette_set = ""
            rotation_profiles = ()
            auto_palette = False
        if profile_strategy == "override_profile" and profile_override_path:
            resolved_profile = load_profile(resolve_profile_path(profile_override_path))
        elif profile_strategy == "auto_profile":
            resolved_profile = profile
        elif profile_strategy in {"song_change_rotation", "profile_rotation", "smart_rotation"} and (
            rotation_profiles or auto_palette
        ):
            profiles = (
                list(generate_profile_set(auto_palette_pool_size, seed=auto_palette_seed))
                if auto_palette
                else [load_profile(resolve_profile_path(value)) for value in rotation_profiles]
            )
            if profile_strategy == "song_change_rotation":
                profile_rotation = ProfileRotation(profiles, interval_seconds=rotation_interval)
                profile_switch_on_song_change = True
                resolved_profile = profile_rotation.current
            elif profile_strategy == "smart_rotation" or smart_rotation or auto_palette:
                profile_chain = ProfileChain(
                    profiles,
                    config=ChainConfig(
                        min_profile_duration=(
                            chain_min_dwell_seconds
                            if chain_dwell_range_enabled
                            else rotation_interval
                        ),
                        max_profile_duration=(
                            chain_max_dwell_seconds
                            if chain_dwell_range_enabled
                            else rotation_interval
                        ),
                        blend_duration=chain_blend_seconds,
                    ),
                    seed=auto_palette_seed,
                )
                resolved_profile = profiles[0]
            else:
                profile_rotation = ProfileRotation(profiles, interval_seconds=rotation_interval)
                resolved_profile = profile_rotation.current
        show_palette_cycle: tuple[str, ...] = ()
        if show_palette_set:
            if resolved_profile is None:
                raise ValueError("Choose an active profile before selecting a Show Palette set.")
            try:
                show_palette_cycle = tuple(resolved_profile.show_palette_sets[show_palette_set])
            except KeyError as exc:
                raise ValueError(
                    f"Show Palette set '{show_palette_set}' was not found in profile "
                    f"'{resolved_profile.name}'."
                ) from exc
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
            master_brightness=master_brightness,
            debug_mood=debug_mood,
            cycle_interval=cycle_interval,
            telemetry_dir=telemetry_dir,
            crossfade_detect=crossfade_detect,
            input_device_label=input_device_label,
            routing_mode=routing_mode,
            routing_status=routing_status,
            profile_rotation=profile_rotation,
            profile_chain=profile_chain,
            profile_switch_on_song_change=profile_switch_on_song_change,
            show_palette_cycle=show_palette_cycle,
            runtime_control=runtime_control,
            structure_config=structure_config,
            raw_visualizer_noise_threshold=(
                raw_visualizer_noise_threshold
            ),
            raw_visualizer_gradient_points=(
                raw_visualizer_gradient_points
            ),
            raw_visualizer_origins=raw_visualizer_origins,
            raw_visualizer_palette_pool=raw_visualizer_palette_pool,
            raw_visualizer_palette_name=raw_visualizer_palette_name,
            raw_visualizer_auto_palette=raw_visualizer_auto_palette,
            raw_visualizer_palette_interval=(
                raw_visualizer_palette_interval
            ),
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
