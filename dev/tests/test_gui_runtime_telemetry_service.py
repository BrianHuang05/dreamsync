from __future__ import annotations

from dreamsync.gui.services.runtime_telemetry_service import RuntimeTelemetryService
from dreamsync.gui.services.runtime_supervisor import RuntimeSupervisor


class _FakeHandle:
    def __init__(self, mode: str, session) -> None:
        self.mode = mode
        self.session_ref = [session]
        self.summary = None
        self.error = None

    @property
    def running(self) -> bool:
        return True

    def stop(self) -> None:
        return None


class _FakeSession:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.runtime_state = {}
        self.runtime_control = {}

    def session_snapshot(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "current_track": "Reactive Live",
            "playback_state": "playing",
            "device_status": "simulation mode (no connected devices)",
            "audio_output": "system default",
            "runtime_state": dict(self.runtime_state),
            "runtime_control": dict(self.runtime_control),
            "playback_mode_used": "baked",
            "baked_validation_valid": True,
            "frame_lookup_count": 3,
            "frame_lookup_avg_ms": 0.02,
        }


class FakeSessionService:
    def start_reactive_live_session(self, **kwargs):
        return _FakeHandle("reactive_live", _FakeSession("reactive_live"))


def test_runtime_telemetry_service_surfaces_runtime_state(tmp_path):
    session_service = FakeSessionService()
    supervisor = RuntimeSupervisor(session_service=session_service)
    handle = supervisor.start_reactive_live(config_path=tmp_path / "config.yaml")
    session = handle.session_ref[0]
    session.runtime_state = {
        "render_mode": "gradient",
        "dominant_band": "bass",
        "dominant_proxy": "vocals",
        "pan_center": 0.42,
        "pan_width": 0.31,
        "active_eq_routes": [{"band": "bass", "when": "dominant"}],
        "active_instrument_routes": [{"instrument": "vocals", "when": "dominant"}],
        "active_scene_layers": [{"instrument": "vocals", "layer_weight": 0.8}],
    }
    session.runtime_control = {"active": True, "render_mode": "gradient"}

    snapshot = RuntimeTelemetryService().snapshot(supervisor)

    assert snapshot.current_render_mode == "gradient"
    assert snapshot.dominant_band == "bass"
    assert snapshot.dominant_proxy == "vocals"
    assert snapshot.pan_center == 0.42
    assert snapshot.active_eq_routes[0]["band"] == "bass"
    assert snapshot.active_scene_layers[0]["instrument"] == "vocals"
    assert snapshot.runtime_control["active"] is True
    assert snapshot.metrics["playback_mode_used"] == "baked"
    assert snapshot.metrics["baked_validation_valid"] is True
    assert snapshot.metrics["frame_lookup_count"] == 3
    assert snapshot.metrics["frame_lookup_avg_ms"] == 0.02
