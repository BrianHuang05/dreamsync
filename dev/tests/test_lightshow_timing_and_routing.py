from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from dreamsync.audio.live_route import LiveAudioRoute
from dreamsync.gui.services.output_lease_service import OutputLeaseService, AudioOutputInvariantError
from dreamsync.show.runtime import ShowPlaybackRuntime
from dreamsync.show.timing import lighting_position
from test_show_runtime import _make_timeline, _make_cue, _mock_multi_adapter


def test_lighting_clock_compensates_delivery_without_changing_audio_clock(monkeypatch):
    monkeypatch.delenv("DREAMSYNC_LIGHTING_LATENCY_MS", raising=False)
    timeline = replace(_make_timeline(), metadata={"lighting_latency_ms": 125})
    assert lighting_position(3.0, timeline) == 3.125
    monkeypatch.setenv("DREAMSYNC_LIGHTING_LATENCY_MS", "-100")
    assert lighting_position(3.0, timeline) == 2.9
    assert lighting_position(0.0, timeline) == 0
    monkeypatch.setenv("DREAMSYNC_LIGHTING_LATENCY_MS", "nan")
    with pytest.raises(ValueError):
        lighting_position(0, timeline)


def test_skipped_beat_is_delivered_once_even_when_adapter_throttles():
    adapter = _mock_multi_adapter()
    runtime = ShowPlaybackRuntime(_make_timeline(), adapter)
    runtime.tick(0.1)
    adapter.send_frame.return_value = False
    runtime.tick(0.57)  # crossed the .5 beat, outside the old tolerance
    assert adapter.send_frame.call_args.kwargs["beat"] is True
    adapter.send_frame.return_value = True
    runtime.tick(0.60)
    assert adapter.send_frame.call_args.kwargs["beat"] is True
    runtime.tick(0.61)
    assert adapter.send_frame.call_args.kwargs["beat"] is False


def test_rhythmic_color_changes_on_the_same_tick():
    adapter = _mock_multi_adapter()
    runtime = ShowPlaybackRuntime(_make_timeline(cues=(_make_cue(render_mode="pulse"),)), adapter)
    runtime.tick(0)
    assert adapter.send_frame.call_args.args[1].color == "#00ff00"


def test_live_audio_cannot_overlap_queue_audio():
    leases = OutputLeaseService()
    leases.acquire_audio(owner="live_passthrough", mode="spotify_learned_live")
    with pytest.raises(AudioOutputInvariantError):
        leases.acquire_audio(owner="queue", mode="queue")
    leases.release_audio(owner="queue")  # lighting switches must not release live audio
    assert leases.audio_snapshot().owner == "live_passthrough"
    leases.release_audio(owner="live_passthrough")
    leases.acquire_audio(owner="queue", mode="queue")


def test_live_bridge_connects_pcm_and_cleans_up_partial_start(monkeypatch):
    capture, playback = MagicMock(), MagicMock()
    capture.poll.return_value = playback.poll.return_value = None
    popen = MagicMock(side_effect=[capture, playback])
    monkeypatch.setattr("dreamsync.audio.live_route.subprocess.Popen", popen)
    route = LiveAudioRoute("capture.monitor", "speakers")
    route.start()
    assert popen.call_args.kwargs["stdin"] is capture.stdout
    assert "--device=speakers" in popen.call_args.args[0]
    route.check()
    route.stop()
    capture.terminate.assert_called_once()
    playback.terminate.assert_called_once()
    popen.side_effect = [capture, FileNotFoundError("pacat")]
    with pytest.raises(FileNotFoundError):
        route.start()
    assert capture.terminate.call_count == 2


def test_live_route_rejects_feedback():
    with pytest.raises(ValueError):
        LiveAudioRoute("speakers.monitor", "speakers")


def test_live_start_selects_launcher_source_and_rolls_back_on_capture_failure(monkeypatch, tmp_path):
    from dreamsync.gui.services import runtime_supervisor as module
    from test_gui_runtime_supervisor import FakeSessionService
    from dreamsync.gui.services.audio_device_service import AudioDeviceService

    class FakeAudioDeviceService(AudioDeviceService):
        def list_input_options(self):
            return ()

        def list_output_options(self):
            return ()

    monkeypatch.setattr(module, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setenv("PULSE_SOURCE", "queue.monitor")
    monkeypatch.setenv("PULSE_SINK", "speakers")
    route = MagicMock()
    factory = MagicMock(return_value=route)
    monkeypatch.setattr("dreamsync.audio.live_route.LiveAudioRoute", factory)
    supervisor = module.RuntimeSupervisor(
        session_service=FakeSessionService(), audio_device_service=FakeAudioDeviceService(),
    )
    capture = MagicMock(side_effect=RuntimeError("capture failed"))
    monkeypatch.setattr(supervisor, "start_capture_pipeline", capture)
    with pytest.raises(RuntimeError, match="capture failed"):
        supervisor.start_spotify_learned_live(
            MagicMock(), cache_dir=tmp_path, simulation_only=False,
        )
    factory.assert_called_once_with("queue.monitor", "speakers")
    assert capture.call_args.kwargs["device_pattern"] == "queue.monitor"
    assert supervisor._routing_state.selected_live_input_device_id == "pulse-source:queue.monitor"
    route.stop.assert_called_once()
    assert supervisor._output_leases.audio_snapshot().owner == ""


def test_live_bridge_survives_lighting_switch_and_stops_for_queue(monkeypatch, tmp_path):
    from dreamsync.gui.services import runtime_supervisor as module
    from test_gui_runtime_supervisor import FakeSessionService
    from dreamsync.gui.services.audio_device_service import AudioDeviceService

    class FakeAudioDeviceService(AudioDeviceService):
        def list_input_options(self):
            return ()

        def list_output_options(self):
            return ()

    monkeypatch.setattr(module, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setenv("PULSE_SOURCE", "queue.monitor")
    monkeypatch.setenv("PULSE_SINK", "speakers")
    route = MagicMock()
    monkeypatch.setattr("dreamsync.audio.live_route.LiveAudioRoute", lambda *args: route)
    session = MagicMock()
    session.run.side_effect = lambda stop: stop.wait(5)
    session_factory = MagicMock(return_value=session)
    monkeypatch.setattr(module, "SpotifyLearnedLiveSession", session_factory)
    supervisor = module.RuntimeSupervisor(
        session_service=FakeSessionService(), audio_device_service=FakeAudioDeviceService(),
    )
    supervisor._audio_device_service.resolve_input_sample_rate = lambda options, device, rate: rate
    supervisor.set_learned_live_settings(replace(supervisor.learned_live_settings(), learning_enabled=False))
    try:
        supervisor.start_spotify_learned_live(MagicMock(), cache_dir=tmp_path, simulation_only=False)
        session_factory.call_args.kwargs["start_reactive"]()
        assert supervisor._output_leases.audio_snapshot().owner == "live_passthrough"
        route.stop.assert_not_called()
        supervisor.start_queue_pipeline_playback(simulation_only=True)
        route.stop.assert_called_once()
        assert supervisor._output_leases.audio_snapshot().owner == ""
    finally:
        supervisor.stop_all()
