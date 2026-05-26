from __future__ import annotations

import threading
from pathlib import Path

from dreamsync.gui.models.capture_settings import CaptureSettings
from dreamsync.gui.models.reactive_settings import ReactiveSettings
from dreamsync.gui.models.runtime_mode_state import CapturedShowItem
from dreamsync.gui.services.runtime_supervisor import RuntimeSupervisor


class FakeHandle:
    def __init__(self, mode: str, session, *, running: bool = True) -> None:
        self.mode = mode
        self.session_ref = [session]
        self.summary = None
        self.error = None
        self._running = running
        self.stop_called = False

    def stop(self) -> None:
        self.stop_called = True
        self._running = False

    @property
    def running(self) -> bool:
        return self._running


class FakeSession:
    def __init__(self, *, mode: str, current_track: str, playback_state: str = "playing") -> None:
        self.mode = mode
        self.current_track = current_track
        self.playback_state = playback_state
        self.runtime_control = {"active": False}
        self.runtime_state = {
            "dominant_band": "",
            "dominant_proxy": "",
            "active_eq_routes": (),
            "active_instrument_routes": (),
            "active_scene_layers": (),
        }

    def session_snapshot(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "current_track": self.current_track,
            "playback_state": self.playback_state,
            "device_status": "simulation mode (no connected devices)",
            "audio_output": "system default",
            "runtime_control": dict(self.runtime_control),
            "runtime_state": dict(self.runtime_state),
        }

    def preview_frame_snapshot(self) -> dict[str, object]:
        return {"node_colors": {"node-1": "#112233"}}

    def update_runtime_control(self, **changes) -> dict[str, object]:
        self.runtime_control = {**self.runtime_control, **changes, "active": True}
        return dict(self.runtime_control)

    def clear_runtime_control(self) -> dict[str, object]:
        self.runtime_control = {"active": False}
        return dict(self.runtime_control)

    def runtime_control_snapshot(self) -> dict[str, object]:
        return dict(self.runtime_control)


class FakeSessionService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self.handles: list[FakeHandle] = []

    def _record(self, name: str, session: FakeSession, *args, **kwargs) -> FakeHandle:
        self.calls.append((name, args, kwargs))
        handle = FakeHandle(session.mode, session)
        self.handles.append(handle)
        return handle

    def start_local_preview_session(self, audio_path: Path, **kwargs) -> FakeHandle:
        return self._record(
            "local",
            FakeSession(mode="local", current_track=str(audio_path)),
            audio_path,
            **kwargs,
        )

    def start_precompiled_show_session(self, audio_path: Path, show_path: Path, **kwargs) -> FakeHandle:
        return self._record(
            "saved_show",
            FakeSession(mode="saved_show", current_track=str(audio_path)),
            audio_path,
            show_path,
            **kwargs,
        )

    def start_reactive_live_session(self, **kwargs) -> FakeHandle:
        return self._record(
            "reactive_live",
            FakeSession(mode="reactive_live", current_track="Reactive Live"),
            **kwargs,
        )

    def start_timeline_playback_session(self, audio_path: Path, timeline, **kwargs) -> FakeHandle:
        return self._record(
            str(kwargs.get("mode", "timeline")),
            FakeSession(mode=str(kwargs.get("mode", "timeline")), current_track=str(audio_path)),
            audio_path,
            timeline,
            **kwargs,
        )


class FakePipelineCoordinator:
    def __init__(self) -> None:
        self.running = False
        self.items: dict[str, CapturedShowItem] = {}
        self.timelines: dict[str, object] = {}
        self.order: list[str] = []

    def start(self, **kwargs) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False

    def snapshot_items(self) -> tuple[CapturedShowItem, ...]:
        return tuple(self.items[item_id] for item_id in self.order if item_id in self.items)

    def snapshot_state(self) -> str:
        if not self.running:
            return "idle"
        if any(item.state in {"ready", "playing"} for item in self.items.values()):
            return "ready"
        return "running"

    def ready_queue_count(self) -> int:
        return sum(1 for item in self.items.values() if item.state in {"ready", "playing"})

    def next_ready_item(self) -> CapturedShowItem | None:
        for item_id in self.order:
            item = self.items[item_id]
            if item.state == "ready":
                return item
        return None

    def item_for_id(self, item_id: str) -> CapturedShowItem | None:
        return self.items.get(item_id)

    def timeline_for_id(self, item_id: str):
        return self.timelines.get(item_id)

    def mark_playing(self, item_id: str) -> None:
        self.items[item_id] = self.items[item_id].__class__(**{
            **self.items[item_id].__dict__,
            "state": "playing",
            "error": None,
        })

    def mark_ready(self, item_id: str) -> None:
        self.items[item_id] = self.items[item_id].__class__(**{
            **self.items[item_id].__dict__,
            "state": "ready",
            "error": None,
        })

    def discard(self, item_id: str) -> None:
        self.items.pop(item_id, None)
        self.timelines.pop(item_id, None)
        self.order = [value for value in self.order if value != item_id]

    def move_to_top(self, item_id: str) -> None:
        self.order = [item_id, *[value for value in self.order if value != item_id]]


def _make_ready_item(name: str = "captured-song.mp3") -> CapturedShowItem:
    path = str(Path("captured") / name)
    return CapturedShowItem(
        item_id=path,
        mp3_path=path,
        state="ready",
        title="Captured Song",
        artist="Test Artist",
    )


def test_runtime_supervisor_switches_output_without_stopping_capture_pipeline(tmp_path: Path):
    session_service = FakeSessionService()
    pipeline = FakePipelineCoordinator()
    supervisor = RuntimeSupervisor(
        session_service=session_service,
        pipeline_factory=lambda: pipeline,
    )

    supervisor.start_capture_pipeline(capture_dir=tmp_path / "captures")
    reactive = supervisor.start_reactive_live()
    supervisor.start_saved_show(tmp_path / "song.mp3", tmp_path / "song.show.json")

    snapshot = supervisor.snapshot()

    assert reactive.stop_called is True
    assert snapshot.capture_state == "running"
    assert snapshot.active_output_mode == "saved_show"
    assert snapshot.saved_show_path.endswith("song.show.json")
    assert snapshot.recent_saved_shows[-1].endswith("song.show.json")


def test_runtime_supervisor_arms_pipeline_playback_then_starts_when_ready(tmp_path: Path):
    session_service = FakeSessionService()
    pipeline = FakePipelineCoordinator()
    supervisor = RuntimeSupervisor(
        session_service=session_service,
        pipeline_factory=lambda: pipeline,
    )

    supervisor.start_capture_pipeline(capture_dir=tmp_path / "captures")

    started = supervisor.switch_to_pipeline_playback()
    armed_snapshot = supervisor.snapshot()

    assert started is False
    assert armed_snapshot.active_output_mode == "pipeline_playback"
    assert armed_snapshot.playback_status == "armed"

    item = _make_ready_item()
    pipeline.items[item.item_id] = item
    pipeline.timelines[item.item_id] = object()
    pipeline.order.append(item.item_id)

    snapshot = supervisor.poll()

    assert snapshot.active_output_mode == "pipeline_playback"
    assert session_service.calls[-1][0] == "pipeline_playback"
    assert pipeline.items[item.item_id].state == "playing"


def test_runtime_supervisor_preview_does_not_replace_active_output(tmp_path: Path):
    session_service = FakeSessionService()
    pipeline = FakePipelineCoordinator()
    supervisor = RuntimeSupervisor(
        session_service=session_service,
        pipeline_factory=lambda: pipeline,
    )

    supervisor.start_saved_show(tmp_path / "song.mp3", tmp_path / "song.show.json")
    active_handle = supervisor._output_handle

    item = _make_ready_item()
    supervisor.start_capture_pipeline(capture_dir=tmp_path / "captures")
    pipeline.items[item.item_id] = item
    pipeline.timelines[item.item_id] = object()
    pipeline.order.append(item.item_id)

    preview_handle = supervisor.preview_captured_show(item.item_id)
    snapshot = supervisor.snapshot()

    assert supervisor._output_handle is active_handle
    assert preview_handle is supervisor._preview_handle
    assert snapshot.simulation_target == "captured preview"
    assert session_service.calls[-1][0] == "simulation_preview"


def test_runtime_supervisor_stop_output_keeps_pipeline_running(tmp_path: Path):
    session_service = FakeSessionService()
    pipeline = FakePipelineCoordinator()
    supervisor = RuntimeSupervisor(
        session_service=session_service,
        pipeline_factory=lambda: pipeline,
    )

    supervisor.start_capture_pipeline(capture_dir=tmp_path / "captures")
    active = supervisor.start_local_playlist(tmp_path / "queue")
    supervisor.stop_output_only()

    snapshot = supervisor.snapshot()

    assert active.stop_called is True
    assert snapshot.capture_state == "running"
    assert snapshot.active_output_mode == "idle"


def test_runtime_supervisor_uses_routing_and_runtime_settings_for_launches(tmp_path: Path):
    session_service = FakeSessionService()
    supervisor = RuntimeSupervisor(session_service=session_service)

    supervisor.set_output_target_mode("hardware")
    supervisor.set_hardware_fallback_to_simulation(False)
    supervisor.set_selected_output_audio_device(8)
    supervisor.set_selected_live_input_device(5)
    supervisor.set_capture_settings(
        CaptureSettings(
            capture_dir=str(tmp_path / "captures"),
            naming_mode="metadata",
            max_capture_buffer=3,
            device_pattern="Cable",
            sample_rate=48000,
            pipeline_playback_device_id=4,
            purge_after_playback=True,
        )
    )
    supervisor.set_reactive_settings(
        ReactiveSettings(
            render_mode="pulse",
            sample_rate=48000,
            frame_size=4096,
            hop_size=1024,
            blocksize=2048,
            telemetry_dir=str(tmp_path / "telemetry"),
            crossfade_detect=True,
            profile_strategy="active_profile",
        )
    )

    supervisor.start_local_playlist(tmp_path / "queue")
    supervisor.start_reactive_live(config_path=tmp_path / "config.yaml")

    local_call = session_service.calls[0]
    reactive_call = session_service.calls[1]

    assert local_call[2]["simulation_only"] is False
    assert local_call[2]["fallback_to_simulation"] is False
    assert local_call[2]["audio_device"] == 8
    assert reactive_call[2]["audio_device"] == 5
    assert reactive_call[2]["render_mode"] == "pulse"
    assert reactive_call[2]["crossfade_detect"] is True


def test_runtime_supervisor_updates_runtime_control_on_active_session(tmp_path: Path):
    session_service = FakeSessionService()
    supervisor = RuntimeSupervisor(session_service=session_service)

    supervisor.start_saved_show(tmp_path / "song.mp3", tmp_path / "song.show.json")
    updated = supervisor.update_runtime_control(render_mode="gradient", color_bias="#abcdef")

    assert updated["active"] is True
    assert updated["render_mode"] == "gradient"
    assert supervisor.runtime_control_snapshot()["color_bias"] == "#abcdef"

    cleared = supervisor.clear_runtime_control()
    assert cleared["active"] is False


def test_runtime_supervisor_passes_timeline_resolver_to_launch_paths(tmp_path: Path):
    session_service = FakeSessionService()
    pipeline = FakePipelineCoordinator()
    supervisor = RuntimeSupervisor(
        session_service=session_service,
        pipeline_factory=lambda: pipeline,
    )
    resolver = lambda audio_path, timeline: timeline
    supervisor.set_timeline_resolver(resolver)

    supervisor.start_local_playlist(tmp_path / "queue")
    supervisor.start_saved_show(tmp_path / "song.mp3", tmp_path / "song.show.json")
    supervisor.start_capture_pipeline(capture_dir=tmp_path / "captures")

    item = _make_ready_item()
    pipeline.items[item.item_id] = item
    pipeline.timelines[item.item_id] = object()
    pipeline.order.append(item.item_id)
    supervisor.preview_captured_show(item.item_id)

    local_call = session_service.calls[0]
    saved_call = session_service.calls[1]
    preview_call = session_service.calls[2]

    assert local_call[2]["timeline_resolver"] is resolver
    assert saved_call[2]["timeline_resolver"] is resolver
    assert preview_call[2]["timeline_resolver"] is resolver
