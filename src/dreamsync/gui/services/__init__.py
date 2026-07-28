"""GUI service layer exports."""

__all__ = [
    "AudioDeviceService",
    "AppInfoService",
    "DeviceDiscoveryService",
    "DeviceHealthService",
    "DeviceService",
    "ProfileService",
    "QueueService",
    "RuntimeSupervisor",
    "RuntimeTelemetryService",
    "SessionService",
    "ShowService",
    "ShowPatchStore",
    "SongPaletteStore",
    "StorageService",
]


def __getattr__(name: str):
    if name == "AppInfoService":
        from .app_info_service import AppInfoService

        return AppInfoService
    if name == "AudioDeviceService":
        from .audio_device_service import AudioDeviceService

        return AudioDeviceService
    if name == "DeviceDiscoveryService":
        from .device_discovery_service import DeviceDiscoveryService

        return DeviceDiscoveryService
    if name == "DeviceHealthService":
        from .device_health_service import DeviceHealthService

        return DeviceHealthService
    if name == "DeviceService":
        from .device_service import DeviceService

        return DeviceService
    if name == "ProfileService":
        from .profile_service import ProfileService

        return ProfileService
    if name == "QueueService":
        from .queue_service import QueueService

        return QueueService
    if name == "RuntimeSupervisor":
        from .runtime_supervisor import RuntimeSupervisor

        return RuntimeSupervisor
    if name == "RuntimeTelemetryService":
        from .runtime_telemetry_service import RuntimeTelemetryService

        return RuntimeTelemetryService
    if name == "SessionService":
        from .session_service import SessionService

        return SessionService
    if name == "ShowService":
        from .show_service import ShowService

        return ShowService
    if name == "ShowPatchStore":
        from .show_patch_store import ShowPatchStore

        return ShowPatchStore
    if name == "SongPaletteStore":
        from .song_palette_store import SongPaletteStore

        return SongPaletteStore
    if name == "StorageService":
        from .storage_service import StorageService

        return StorageService
    raise AttributeError(name)
