"""Persisted GUI preferences."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from dreamsync.gui.models.capture_settings import CaptureSettings
from dreamsync.gui.models.reactive_settings import ReactiveSettings


def default_settings_path() -> Path:
    """Return the default settings path for the local user."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "DreamSync" / "gui-settings.json"
    return Path.home() / ".dreamsync" / "gui-settings.json"


@dataclass(frozen=True)
class GuiSettings:
    last_config_path: str = ""
    last_profile_path: str = ""
    last_tab: str = "Devices / Spatial"
    window_geometry: str = ""
    splitter_sizes: tuple[int, ...] = field(default_factory=tuple)
    output_target_mode: str = "simulation"
    selected_output_audio_device_id: int | None = None
    selected_live_input_device_id: int | None = None
    hardware_fallback_to_simulation: bool = True
    recent_saved_show_paths: tuple[str, ...] = field(default_factory=tuple)
    show_editor_hidden_columns: tuple[int, ...] = field(default_factory=tuple)
    show_editor_meta_hidden: bool = False
    show_editor_focus_mode: bool = False
    show_editor_splitter_sizes: tuple[int, ...] = field(default_factory=tuple)
    capture_settings: CaptureSettings = field(default_factory=CaptureSettings)
    reactive_settings: ReactiveSettings = field(default_factory=ReactiveSettings)


class GuiSettingsStore:
    """Load and save GUI-only preferences as JSON."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or default_settings_path()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> GuiSettings:
        if not self._path.exists():
            return GuiSettings()
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        capture_raw = raw.get("capture_settings", {}) or {}
        reactive_raw = raw.get("reactive_settings", {}) or {}
        return GuiSettings(
            last_config_path=str(raw.get("last_config_path", "")),
            last_profile_path=str(raw.get("last_profile_path", "")),
            last_tab=str(raw.get("last_tab", "Devices / Spatial")),
            window_geometry=str(raw.get("window_geometry", "")),
            splitter_sizes=tuple(int(v) for v in raw.get("splitter_sizes", ())),
            output_target_mode=str(raw.get("output_target_mode", "simulation")),
            selected_output_audio_device_id=(
                int(raw["selected_output_audio_device_id"])
                if raw.get("selected_output_audio_device_id") is not None
                else None
            ),
            selected_live_input_device_id=(
                int(raw["selected_live_input_device_id"])
                if raw.get("selected_live_input_device_id") is not None
                else None
            ),
            hardware_fallback_to_simulation=bool(raw.get("hardware_fallback_to_simulation", True)),
            recent_saved_show_paths=tuple(str(v) for v in raw.get("recent_saved_show_paths", ())),
            show_editor_hidden_columns=tuple(
                int(v) for v in raw.get("show_editor_hidden_columns", ())
            ),
            show_editor_meta_hidden=bool(raw.get("show_editor_meta_hidden", False)),
            show_editor_focus_mode=bool(raw.get("show_editor_focus_mode", False)),
            show_editor_splitter_sizes=tuple(
                int(v) for v in raw.get("show_editor_splitter_sizes", ())
            ),
            capture_settings=CaptureSettings(
                capture_dir=str(capture_raw.get("capture_dir", "captured_songs")),
                naming_mode=str(capture_raw.get("naming_mode", "timestamp")),
                max_capture_buffer=int(capture_raw.get("max_capture_buffer", 0)),
                device_pattern=str(capture_raw.get("device_pattern", "CABLE Output")),
                sample_rate=int(capture_raw.get("sample_rate", 44100)),
                channels=int(capture_raw.get("channels", 2)),
                frame_size=int(capture_raw.get("frame_size", 2048)),
                hop_size=int(capture_raw.get("hop_size", 512)),
                blocksize=int(capture_raw.get("blocksize", 1024)),
                pipeline_playback_device_id=(
                    int(capture_raw["pipeline_playback_device_id"])
                    if capture_raw.get("pipeline_playback_device_id") is not None
                    else None
                ),
                purge_after_playback=bool(capture_raw.get("purge_after_playback", False)),
                debug_pipeline=bool(capture_raw.get("debug_pipeline", False)),
            ),
            reactive_settings=ReactiveSettings(
                render_mode=str(reactive_raw.get("render_mode", "scroll")),
                sample_rate=int(reactive_raw.get("sample_rate", 44100)),
                channels=int(reactive_raw.get("channels", 1)),
                frame_size=int(reactive_raw.get("frame_size", 2048)),
                hop_size=int(reactive_raw.get("hop_size", 512)),
                blocksize=int(reactive_raw.get("blocksize", 1024)),
                half_time=bool(reactive_raw.get("half_time", False)),
                max_brightness=bool(reactive_raw.get("max_brightness", False)),
                auto_cycle=bool(reactive_raw.get("auto_cycle", True)),
                cycle_interval=float(reactive_raw.get("cycle_interval", 16.0)),
                debug_mood=bool(reactive_raw.get("debug_mood", False)),
                telemetry_dir=str(reactive_raw.get("telemetry_dir", "")),
                crossfade_detect=bool(reactive_raw.get("crossfade_detect", False)),
                profile_strategy=str(reactive_raw.get("profile_strategy", "active_profile")),
                profile_override_path=str(reactive_raw.get("profile_override_path", "")),
                rotation_profiles=tuple(str(v) for v in reactive_raw.get("rotation_profiles", ())),
                rotation_interval=float(reactive_raw.get("rotation_interval", 300.0)),
                auto_palette=bool(reactive_raw.get("auto_palette", False)),
                smart_rotation=bool(reactive_raw.get("smart_rotation", False)),
                chain_blend_seconds=float(reactive_raw.get("chain_blend_seconds", 8.0)),
            ),
        )

    def save(self, settings: GuiSettings) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = asdict(settings)
        self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
