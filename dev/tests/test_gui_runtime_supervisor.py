from __future__ import annotations

import threading
from pathlib import Path

from dreamsync.gui.models.capture_settings import CaptureSettings
from dreamsync.gui.models.reactive_settings import ReactiveSettings
from dreamsync.gui.models.runtime_mode_state import CapturedShowItem
from dreamsync.gui.models.runtime_routing_state import AudioDeviceOption
from dreamsync.gui.services.audio_device_service import AudioDeviceService
from dreamsync.gui.services.runtime_supervisor import RuntimeSupervisor
from dreamsync.show.models import Show, ShowCue, ShowTimeline, ShowTrack


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
        self.downbeat_nudge_revision = 0
        self.cycle_tempo_multiplier = 1.0

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

    def request_manual_beat(self, kind: str) -> int:
        self.downbeat_nudge_revision += 1
        self.last_manual_beat_kind = kind
        return self.downbeat_nudge_revision

    def request_detection_reset(self) -> int:
        return self.request_manual_beat("reset")

    def set_cycle_tempo_multiplier(self, multiplier: float) -> float:
        self.cycle_tempo_multiplier = float(multiplier)
        return self.cycle_tempo_multiplier


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

    def start_compiled_show_session(self, show: Show, **kwargs) -> FakeHandle:
        return self._record(
            "saved_show",
            FakeSession(mode="saved_show", current_track=show.tracks[0].audio_path),
            show,
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

    def start_timeline_preview_session(self, timeline, **kwargs) -> FakeHandle:
        return self._record(
            str(kwargs.get("mode", "simulation_preview")),
            FakeSession(
                mode=str(kwargs.get("mode", "simulation_preview")),
                current_track=str(kwargs.get("audio_path", "")),
            ),
            timeline,
            **kwargs,
        )


class FakePipelineCoordinator:
    def __init__(self) -> None:
        self.running = False
        self.items: dict[str, CapturedShowItem] = {}
        self.timelines: dict[str, object] = {}
        self.order: list[str] = []
        self.timing_source = None
        self.track_changes: list[dict] = []

    def start(self, **kwargs) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False

    def set_timing_source(self, fetcher) -> None:
        self.timing_source = fetcher

    def on_track_change(self, timing_data: dict) -> int:
        self.track_changes.append(timing_data)
        return 1

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

    def mark_played(self, item_id: str) -> None:
        self.items[item_id] = self.items[item_id].__class__(**{
            **self.items[item_id].__dict__,
            "state": "played",
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


def test_runtime_supervisor_queues_downbeat_nudge_on_reactive_session():
    session_service = FakeSessionService()
    supervisor = RuntimeSupervisor(
        session_service=session_service,
        pipeline_factory=FakePipelineCoordinator,
    )
    supervisor.start_reactive_live()

    revision = supervisor.request_reactive_downbeat_nudge()

    assert revision == 1
    assert "nearest detected beat" in supervisor.snapshot().status_message
    assert "revision 1" in supervisor.recent_events()[0]
    assert supervisor.active_session().last_manual_beat_kind == "downbeat"

    assert supervisor.request_reactive_beat_latch("beat") == 2
    assert supervisor.active_session().last_manual_beat_kind == "beat"

    assert supervisor.request_reactive_detection_reset() == 3
    assert supervisor.active_session().last_manual_beat_kind == "reset"
    assert "reacquiring BPM and meter" in supervisor.snapshot().status_message

    assert supervisor.set_reactive_cycle_tempo_multiplier(0.5) == 0.5
    assert supervisor.active_session().cycle_tempo_multiplier == 0.5
    assert "0.5× detector BPM" in supervisor.snapshot().status_message


def test_runtime_supervisor_does_not_arm_pipeline_playback_when_capture_becomes_ready(tmp_path: Path):
    session_service = FakeSessionService()
    pipeline = FakePipelineCoordinator()
    supervisor = RuntimeSupervisor(
        session_service=session_service,
        pipeline_factory=lambda: pipeline,
    )

    supervisor.start_capture_pipeline(capture_dir=tmp_path / "captures")

    started = supervisor.start_next_queue_pipeline_item()
    idle_snapshot = supervisor.snapshot()

    assert started is False
    assert idle_snapshot.active_output_mode == "idle"
    assert idle_snapshot.playback_status == "idle"
    assert idle_snapshot.audio_output_lease.owner == ""

    item = _make_ready_item()
    pipeline.items[item.item_id] = item
    pipeline.timelines[item.item_id] = object()
    pipeline.order.append(item.item_id)

    snapshot = supervisor.poll()

    assert snapshot.active_output_mode == "idle"
    assert pipeline.items[item.item_id].state == "ready"

    assert supervisor.start_next_queue_pipeline_item() is True
    snapshot = supervisor.snapshot()

    assert snapshot.active_output_mode == "queue_pipeline_playback"
    assert snapshot.audio_output_lease.owner == "queue"
    assert session_service.calls[-1][0] == "queue_pipeline_playback"
    assert session_service.calls[-1][2]["baked_playback_mode"] == "off"
    assert pipeline.items[item.item_id].state == "playing"


def test_runtime_supervisor_continuously_consumes_ready_captures(tmp_path: Path):
    session_service = FakeSessionService()
    pipeline = FakePipelineCoordinator()
    supervisor = RuntimeSupervisor(
        session_service=session_service,
        pipeline_factory=lambda: pipeline,
    )
    supervisor.start_capture_pipeline(capture_dir=tmp_path / "captures")
    first = _make_ready_item("first.mp3")
    second = _make_ready_item("second.mp3")
    for item in (first, second):
        pipeline.items[item.item_id] = item
        pipeline.timelines[item.item_id] = object()
        pipeline.order.append(item.item_id)

    assert supervisor.start_queue_pipeline_playback() is True
    first_handle = session_service.handles[-1]
    assert supervisor.snapshot().queue_playback_state == "playing"
    assert pipeline.items[first.item_id].state == "playing"

    first_handle._running = False
    supervisor.poll()

    assert pipeline.items[first.item_id].state == "played"
    assert pipeline.items[second.item_id].state == "playing"
    assert len(session_service.handles) == 2

    second_handle = session_service.handles[-1]
    second_handle._running = False
    waiting = supervisor.poll()

    assert pipeline.items[second.item_id].state == "played"
    assert waiting.queue_playback_state == "waiting"

    third = _make_ready_item("third.mp3")
    pipeline.items[third.item_id] = third
    pipeline.timelines[third.item_id] = object()
    pipeline.order.append(third.item_id)

    resumed = supervisor.poll()

    assert resumed.queue_playback_state == "playing"
    assert pipeline.items[third.item_id].state == "playing"
    assert len(session_service.handles) == 3

    supervisor.stop_output_only()

    assert supervisor.snapshot().queue_playback_state == "stopped"
    assert pipeline.items[third.item_id].state == "ready"


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
    assert "audio_device" not in session_service.calls[-1][2]
    assert snapshot.audio_output_lease.owner == "queue"


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


def test_reactive_owns_lighting_but_never_audio() -> None:
    supervisor = RuntimeSupervisor(session_service=FakeSessionService())

    supervisor.start_reactive_live(effect_mode="raw_visualizer")
    snapshot = supervisor.snapshot()

    assert snapshot.lighting_output_lease.owner == "raw_visualizer"
    assert snapshot.audio_output_lease.owner == ""

    supervisor.stop_output_only()
    stopped = supervisor.snapshot()
    assert stopped.lighting_output_lease.owner == ""
    assert stopped.audio_output_lease.owner == ""


def test_runtime_supervisor_uses_routing_and_runtime_settings_for_launches(tmp_path: Path):
    session_service = FakeSessionService()
    supervisor = RuntimeSupervisor(session_service=session_service)

    supervisor.set_output_target_mode("hardware")
    supervisor.set_hardware_fallback_to_simulation(False)
    supervisor.set_baked_playback_mode("require")
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
            mirror=False,
            master_brightness=0.35,
            sample_rate=48000,
            frame_size=4096,
            hop_size=1024,
            blocksize=2048,
            telemetry_dir=str(tmp_path / "telemetry"),
            crossfade_detect=True,
            profile_strategy="smart_rotation",
            auto_palette=True,
            auto_palette_seed=42,
            auto_palette_pool_size=6,
            chain_dwell_range_enabled=True,
            chain_min_dwell_seconds=30.0,
            chain_max_dwell_seconds=90.0,
            structure_similarity_enabled=True,
            structure_similarity_diagnostics=True,
            structure_similarity_shadow_mode=False,
            structure_bar_actions_enabled=True,
            structure_phrase_actions_enabled=True,
            structure_section_actions_enabled=True,
        )
    )

    supervisor.start_local_playlist(tmp_path / "queue")
    supervisor.start_saved_show(tmp_path / "song.mp3", tmp_path / "song.show.json")
    supervisor.start_reactive_live(
        config_path=tmp_path / "config.yaml",
        effect_mode="raw_visualizer",
        raw_visualizer_palette_pool=(
            ("warm", ("#ff0000", "#ffff00", "#ffffff")),
        ),
        raw_visualizer_palette_name="warm",
        raw_visualizer_auto_palette=True,
        raw_visualizer_palette_interval=14.0,
    )

    local_call = session_service.calls[0]
    saved_call = session_service.calls[1]
    reactive_call = session_service.calls[2]

    assert local_call[2]["simulation_only"] is False
    assert local_call[2]["fallback_to_simulation"] is False
    assert local_call[2]["audio_device"] == 8
    assert saved_call[2]["baked_playback_mode"] == "require"
    assert reactive_call[2]["audio_device"] == 5
    assert reactive_call[2]["render_mode"] == "pulse"
    assert reactive_call[2]["mirror"] is False
    assert reactive_call[2]["master_brightness"] == 0.35
    assert reactive_call[2]["crossfade_detect"] is True
    assert reactive_call[2]["auto_palette_seed"] == 42
    assert reactive_call[2]["auto_palette_pool_size"] == 6
    assert reactive_call[2]["chain_min_dwell_seconds"] == 30.0
    assert reactive_call[2]["chain_max_dwell_seconds"] == 90.0
    assert reactive_call[2]["raw_visualizer_palette_name"] == "warm"
    assert reactive_call[2]["raw_visualizer_auto_palette"] is True
    assert reactive_call[2]["raw_visualizer_palette_interval"] == 14.0
    structure = reactive_call[2]["structure_config"]
    assert structure.structure_similarity_enabled is True
    assert structure.structure_similarity_diagnostics is True
    assert structure.structure_similarity_shadow_mode is False
    assert structure.structure_bar_actions_enabled is True
    assert structure.structure_phrase_actions_enabled is True
    assert structure.structure_section_actions_enabled is True


def test_reactive_uses_selected_input_native_sample_rate():
    class NativeRateAudioDeviceService(AudioDeviceService):
        def list_output_options(self):
            return (AudioDeviceOption(id=None, name="System default"),)

        def list_input_options(self):
            return (
                AudioDeviceOption(id=None, name="System default", kind="input"),
                AudioDeviceOption(
                    id=14,
                    name="CABLE Output",
                    hostapi="Windows WASAPI",
                    default_samplerate=48000.0,
                    channel_count=2,
                    kind="input",
                ),
            )

    session_service = FakeSessionService()
    supervisor = RuntimeSupervisor(
        session_service=session_service,
        audio_device_service=NativeRateAudioDeviceService(),
    )
    supervisor.set_selected_live_input_device(14)
    supervisor.set_reactive_settings(ReactiveSettings(sample_rate=44100))

    supervisor.start_reactive_live()

    reactive_call = session_service.calls[-1]
    assert reactive_call[2]["audio_device"] == 14
    assert reactive_call[2]["sample_rate"] == 48000
    assert any(
        "44100 Hz to 48000 Hz" in event
        for event in supervisor.recent_events()
    )


def test_reactive_settings_rejects_dual_structure_ownership():
    settings = ReactiveSettings(
        harmonic_structure_enabled=True,
        structure_similarity_enabled=True,
    )

    assert any(
        "either legacy harmonic structure or structure similarity" in error
        for error in settings.validate()
    )


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


def test_runtime_supervisor_starts_full_compiled_show_in_track_order(tmp_path: Path):
    session_service = FakeSessionService()
    supervisor = RuntimeSupervisor(session_service=session_service)
    first = tmp_path / "first.mp3"
    second = tmp_path / "second.mp3"
    timeline = ShowTimeline(
        song_path=str(first),
        duration=5.0,
        bpm=120.0,
        time_signature=4,
        beat_times=(0.0,),
        downbeat_times=(0.0,),
        cues=(ShowCue(0.0, "solid", ("#ffffff",), 1.0, 0.0, {}, "cut", 0),),
        metadata={},
    )
    show = Show(
        name="Two Track Show",
        tracks=(
            ShowTrack(str(first), timeline),
            ShowTrack(str(second), timeline),
        ),
        metadata={},
    )

    supervisor.start_compiled_show(show, show_path=tmp_path / "two-track.show.json")

    call = session_service.calls[-1]
    assert call[0] == "saved_show"
    assert call[1][0] == show
    assert supervisor.snapshot().active_output_mode == "saved_show"
    assert "Two Track Show" in supervisor.snapshot().status_message


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
    assert preview_call[2]["audio_path"] == Path(item.mp3_path)
