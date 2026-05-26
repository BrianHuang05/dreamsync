"""GUI view-model helpers."""

__all__ = [
    "AudioDeviceOption",
    "CaptureSettings",
    "CapturedShowItem",
    "OutputLease",
    "OutputTarget",
    "ReactiveSettings",
    "RuntimeModeState",
    "RuntimeRoutingState",
    "RuntimeTelemetrySnapshot",
]


def __getattr__(name: str):
    if name in {"CapturedShowItem", "OutputLease", "RuntimeModeState"}:
        from .runtime_mode_state import CapturedShowItem, OutputLease, RuntimeModeState

        return {
            "CapturedShowItem": CapturedShowItem,
            "OutputLease": OutputLease,
            "RuntimeModeState": RuntimeModeState,
        }[name]
    if name in {"AudioDeviceOption", "OutputTarget", "RuntimeRoutingState"}:
        from .runtime_routing_state import AudioDeviceOption, OutputTarget, RuntimeRoutingState

        return {
            "AudioDeviceOption": AudioDeviceOption,
            "OutputTarget": OutputTarget,
            "RuntimeRoutingState": RuntimeRoutingState,
        }[name]
    if name == "CaptureSettings":
        from .capture_settings import CaptureSettings

        return CaptureSettings
    if name == "ReactiveSettings":
        from .reactive_settings import ReactiveSettings

        return ReactiveSettings
    if name == "RuntimeTelemetrySnapshot":
        from .runtime_telemetry_state import RuntimeTelemetrySnapshot

        return RuntimeTelemetrySnapshot
    raise AttributeError(name)
