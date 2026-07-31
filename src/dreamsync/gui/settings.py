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
    last_tab: str = "Devices"
    window_geometry: str = ""
    splitter_sizes: tuple[int, ...] = field(default_factory=tuple)
    output_target_mode: str = "simulation"
    dark_mode: bool = False
    selected_output_audio_device_id: int | None = None
    selected_live_input_device_id: int | None = None
    hardware_fallback_to_simulation: bool = True
    baked_playback_mode: str = "auto"
    live_loopback_enabled: bool = False
    live_start_mode: str = "queue"
    profile_directory: str = ""
    show_directory: str = ""
    queue_directory: str = ""
    recent_saved_show_paths: tuple[str, ...] = field(default_factory=tuple)
    show_editor_hidden_columns: tuple[int, ...] = (8, 9, 13, 18)
    show_editor_meta_hidden: bool = False
    show_editor_focus_mode: bool = False
    show_editor_splitter_sizes: tuple[int, ...] = field(default_factory=tuple)
    reactive_diagnostics_splitter_sizes: tuple[int, ...] = field(default_factory=tuple)
    reactive_chord_panel_visible: bool = True
    reactive_waveform_panel_visible: bool = True
    reactive_harmonic_panel_visible: bool = True
    reactive_live_color_profile: str = ""
    reactive_live_active_effect: str = ""
    reactive_live_effect_speed: str = "auto"
    reactive_live_effect_origin: str = "auto"
    reactive_live_effect_bank: tuple[str, ...] = (
        "pulse",
        "wave",
        "ripple",
        "scroll",
        "breathe",
        "gradient",
        "solid",
    )
    show_compile_seed: int | None = None
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
        rotation_profiles = tuple(
            str(value) for value in reactive_raw.get("rotation_profiles", ())
        )
        profile_strategy = str(
            reactive_raw.get("profile_strategy", "active_profile")
        )
        auto_palette = bool(reactive_raw.get("auto_palette", False))
        if profile_strategy in {
            "song_change_rotation",
            "profile_rotation",
            "smart_rotation",
        } and not rotation_profiles and not auto_palette:
            # Older saved settings can retain a rotation mode after its profile
            # list was cleared.  Starting Reactive should still be possible;
            # use the active profile until the user configures a new rotation.
            profile_strategy = "active_profile"
        return GuiSettings(
            last_config_path=str(raw.get("last_config_path", "")),
            last_profile_path=str(raw.get("last_profile_path", "")),
            last_tab=(
                "Devices"
                if str(raw.get("last_tab", "Devices")) == "Device Discovery"
                else str(raw.get("last_tab", "Devices"))
            ),
            window_geometry=str(raw.get("window_geometry", "")),
            splitter_sizes=tuple(int(v) for v in raw.get("splitter_sizes", ())),
            output_target_mode=str(raw.get("output_target_mode", "simulation")),
            dark_mode=bool(raw.get("dark_mode", False)),
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
            baked_playback_mode=str(raw.get("baked_playback_mode", "auto")),
            live_loopback_enabled=bool(raw.get("live_loopback_enabled", False)),
            live_start_mode=(
                "reactive" if str(raw.get("live_start_mode", "queue")) == "reactive" else "queue"
            ),
            profile_directory=str(raw.get("profile_directory", "")),
            show_directory=str(raw.get("show_directory", "")),
            queue_directory=str(raw.get("queue_directory", "")),
            recent_saved_show_paths=tuple(str(v) for v in raw.get("recent_saved_show_paths", ())),
            show_editor_hidden_columns=tuple(
                int(v)
                for v in raw.get(
                    "show_editor_hidden_columns",
                    GuiSettings().show_editor_hidden_columns,
                )
            ),
            show_editor_meta_hidden=bool(raw.get("show_editor_meta_hidden", False)),
            show_editor_focus_mode=bool(raw.get("show_editor_focus_mode", False)),
            show_editor_splitter_sizes=tuple(
                int(v) for v in raw.get("show_editor_splitter_sizes", ())
            ),
            reactive_diagnostics_splitter_sizes=tuple(
                int(v)
                for v in raw.get("reactive_diagnostics_splitter_sizes", ())
            ),
            reactive_chord_panel_visible=bool(
                raw.get("reactive_chord_panel_visible", True)
            ),
            reactive_waveform_panel_visible=bool(
                raw.get("reactive_waveform_panel_visible", True)
            ),
            reactive_harmonic_panel_visible=bool(
                raw.get("reactive_harmonic_panel_visible", True)
            ),
            reactive_live_color_profile=str(
                raw.get("reactive_live_color_profile", "")
            ),
            reactive_live_active_effect=str(
                raw.get("reactive_live_active_effect", "")
            ),
            reactive_live_effect_speed=str(
                raw.get("reactive_live_effect_speed", "auto")
            ),
            reactive_live_effect_origin=str(
                raw.get("reactive_live_effect_origin", "auto")
            ),
            reactive_live_effect_bank=tuple(
                str(v)
                for v in raw.get(
                    "reactive_live_effect_bank",
                    (
                        "pulse",
                        "wave",
                        "ripple",
                        "scroll",
                        "breathe",
                        "gradient",
                        "solid",
                    ),
                )
            ),
            show_compile_seed=(
                int(raw["show_compile_seed"])
                if raw.get("show_compile_seed") is not None
                else None
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
                mirror=bool(reactive_raw.get("mirror", True)),
                master_brightness=float(reactive_raw.get("master_brightness", 1.0)),
                auto_cycle=bool(reactive_raw.get("auto_cycle", True)),
                cycle_interval=float(reactive_raw.get("cycle_interval", 16.0)),
                debug_mood=bool(reactive_raw.get("debug_mood", False)),
                telemetry_dir=str(reactive_raw.get("telemetry_dir", "")),
                crossfade_detect=bool(reactive_raw.get("crossfade_detect", False)),
                harmonic_structure_enabled=bool(
                    reactive_raw.get("harmonic_structure_enabled", False)
                ),
                beats_per_bar=int(reactive_raw.get("beats_per_bar", 4)),
                bars_per_phrase=int(reactive_raw.get("bars_per_phrase", 4)),
                harmonic_frame_size=int(
                    reactive_raw.get("harmonic_frame_size", 4096)
                ),
                harmonic_hop_multiplier=(
                    1
                    if reactive_raw.get("harmonic_hop_multiplier", 1) == 4
                    else int(
                        reactive_raw.get(
                            "harmonic_hop_multiplier",
                            1,
                        )
                    )
                ),
                structure_sensitivity=float(
                    reactive_raw.get("structure_sensitivity", 0.5)
                ),
                downbeat_min_confidence=float(
                    reactive_raw.get("downbeat_min_confidence", 0.22)
                ),
                debug_harmonics=bool(
                    reactive_raw.get("debug_harmonics", False)
                ),
                predictive_analysis_enabled=bool(
                    reactive_raw.get("predictive_analysis_enabled", False)
                ),
                predictive_diagnostics_enabled=bool(
                    reactive_raw.get("predictive_diagnostics_enabled", False)
                ),
                predictive_shadow_mode=bool(
                    reactive_raw.get("predictive_shadow_mode", True)
                ),
                predictive_cues_enabled=bool(
                    reactive_raw.get("predictive_cues_enabled", False)
                ),
                predictive_high_impact_cues_enabled=bool(
                    reactive_raw.get(
                        "predictive_high_impact_cues_enabled",
                        False,
                    )
                ),
                predictive_cue_prepare_threshold=float(
                    reactive_raw.get("predictive_cue_prepare_threshold", 0.54)
                ),
                predictive_cue_schedule_threshold=float(
                    reactive_raw.get("predictive_cue_schedule_threshold", 0.68)
                ),
                predictive_cue_high_impact_threshold=float(
                    reactive_raw.get(
                        "predictive_cue_high_impact_threshold",
                        0.80,
                    )
                ),
                predictive_maximum_anticipatory_intensity=float(
                    reactive_raw.get(
                        "predictive_maximum_anticipatory_intensity",
                        0.28,
                    )
                ),
                predictive_cue_cooldown_seconds=float(
                    reactive_raw.get("predictive_cue_cooldown_seconds", 2.0)
                ),
                predictive_allowed_cue_classes=tuple(
                    str(item)
                    for item in reactive_raw.get(
                        "predictive_allowed_cue_classes",
                        (
                            "chord_accent",
                            "resolution_bloom",
                            "phrase_reset",
                            "section_recall",
                            "chorus_lift",
                        ),
                    )
                ),
                structure_similarity_enabled=bool(
                    reactive_raw.get("structure_similarity_enabled", False)
                ),
                structure_similarity_diagnostics=bool(
                    reactive_raw.get("structure_similarity_diagnostics", False)
                ),
                structure_similarity_shadow_mode=bool(
                    reactive_raw.get("structure_similarity_shadow_mode", True)
                ),
                structure_bar_actions_enabled=bool(
                    reactive_raw.get("structure_bar_actions_enabled", False)
                ),
                structure_phrase_actions_enabled=bool(
                    reactive_raw.get("structure_phrase_actions_enabled", False)
                ),
                structure_section_actions_enabled=bool(
                    reactive_raw.get("structure_section_actions_enabled", False)
                ),
                structure_allow_secondary_beat_modulation=bool(
                    reactive_raw.get(
                        "structure_allow_secondary_beat_modulation",
                        False,
                    )
                ),
                structure_use_tonal_sidecar=bool(
                    reactive_raw.get("structure_use_tonal_sidecar", False)
                ),
                structure_memory_bars=int(
                    reactive_raw.get("structure_memory_bars", 256)
                ),
                structure_min_meter_confidence=float(
                    reactive_raw.get("structure_min_meter_confidence", 0.22)
                ),
                structure_phrase_threshold=float(
                    reactive_raw.get("structure_phrase_threshold", 0.48)
                ),
                structure_section_threshold=float(
                    reactive_raw.get("structure_section_threshold", 0.62)
                ),
                structure_large_action_threshold=float(
                    reactive_raw.get("structure_large_action_threshold", 0.80)
                ),
                profile_strategy=profile_strategy,
                profile_override_path=str(reactive_raw.get("profile_override_path", "")),
                show_palette_set=str(reactive_raw.get("show_palette_set", "")),
                rotation_profiles=rotation_profiles,
                rotation_interval=float(reactive_raw.get("rotation_interval", 300.0)),
                auto_palette=auto_palette,
                smart_rotation=bool(reactive_raw.get("smart_rotation", False)),
                chain_blend_seconds=float(reactive_raw.get("chain_blend_seconds", 8.0)),
                auto_palette_seed=(
                    int(reactive_raw["auto_palette_seed"])
                    if reactive_raw.get("auto_palette_seed") is not None
                    else None
                ),
                auto_palette_pool_size=int(reactive_raw.get("auto_palette_pool_size", 8)),
                chain_dwell_range_enabled=bool(
                    reactive_raw.get("chain_dwell_range_enabled", False)
                ),
                chain_min_dwell_seconds=float(
                    reactive_raw.get("chain_min_dwell_seconds", 60.0)
                ),
                chain_max_dwell_seconds=float(
                    reactive_raw.get("chain_max_dwell_seconds", 180.0)
                ),
            ),
        )

    def save(self, settings: GuiSettings) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = asdict(settings)
        self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
