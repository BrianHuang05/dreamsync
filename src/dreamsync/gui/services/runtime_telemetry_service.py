"""Structured runtime telemetry snapshots for GUI diagnostics."""

from __future__ import annotations

from dreamsync.gui.models.runtime_telemetry_state import RuntimeTelemetrySnapshot


class RuntimeTelemetryService:
    """Collect diagnostics from the runtime supervisor."""

    def snapshot(self, supervisor) -> RuntimeTelemetrySnapshot:
        state = supervisor.snapshot()
        metrics = supervisor.runtime_metrics()
        active_session = supervisor.active_session()
        session_snapshot = (
            active_session.session_snapshot()
            if active_session is not None and hasattr(active_session, "session_snapshot")
            else {}
        )
        runtime_state = session_snapshot.get("runtime_state", {})
        if not isinstance(runtime_state, dict):
            runtime_state = {}
        telemetry_metrics = dict(metrics)
        for key in (
            "playback_mode_used",
            "baked_validation_valid",
            "baked_validation_reason",
            "baked_artifact_path",
            "frame_count",
            "node_count",
            "frames_sent",
            "frame_lookup_count",
            "frame_lookup_avg_ms",
            "frame_lookup_max_ms",
        ):
            if key in session_snapshot:
                telemetry_metrics[key] = session_snapshot[key]
        return RuntimeTelemetrySnapshot(
            output_mode=state.active_output_mode,
            capture_state=state.capture_state,
            pipeline_state=state.pipeline_state,
            ready_queue_count=state.ready_queue_count,
            current_track=state.current_track,
            device_status=state.device_status,
            audio_output=state.audio_output,
            input_device=state.input_device,
            routing_status=state.routing_state.routing_status,
            elapsed_seconds=float(metrics.get("elapsed_seconds", 0.0) or 0.0),
            last_error=state.error_message,
            warnings=tuple(supervisor.recent_warnings()),
            recent_events=tuple(supervisor.recent_events()),
            current_render_mode=str(session_snapshot.get("current_render_mode", runtime_state.get("render_mode", "")) or ""),
            current_palette=tuple(session_snapshot.get("current_palette", runtime_state.get("current_palette", ()))),
            dominant_band=str(session_snapshot.get("dominant_band", runtime_state.get("dominant_band", "")) or ""),
            dominant_proxy=str(session_snapshot.get("dominant_proxy", runtime_state.get("dominant_proxy", "")) or ""),
            pan_center=float(session_snapshot.get("pan_center", runtime_state.get("pan_center", 0.0)) or 0.0),
            pan_width=float(session_snapshot.get("pan_width", runtime_state.get("pan_width", 0.0)) or 0.0),
            active_eq_routes=tuple(
                dict(route)
                for route in runtime_state.get("active_eq_routes", ())
                if isinstance(route, dict)
            ),
            active_instrument_routes=tuple(
                dict(route)
                for route in runtime_state.get("active_instrument_routes", ())
                if isinstance(route, dict)
            ),
            active_scene_layers=tuple(
                dict(layer)
                for layer in runtime_state.get("active_scene_layers", ())
                if isinstance(layer, dict)
            ),
            runtime_control=dict(session_snapshot.get("runtime_control", supervisor.runtime_control_snapshot())),
            metrics=telemetry_metrics,
        )
