"""Top-level GUI main window factory."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import replace
from pathlib import Path

from dreamsync.effects import EFFECTS, PALETTES
from dreamsync.gui.controllers.palette_controller import PaletteController
from dreamsync.gui.controllers.queue_controller import QueueController
from dreamsync.gui.controllers.show_patch_controller import ShowPatchController
from dreamsync.gui.controllers.spatial_controller import SpatialController
from dreamsync.gui.models.capture_settings import CaptureSettings
from dreamsync.gui.models.palette_state import PaletteEditorState
from dreamsync.gui.models.queue_state import QueueState
from dreamsync.gui.models.reactive_settings import ReactiveSettings
from dreamsync.gui.models.runtime_mode_state import RuntimeModeState
from dreamsync.gui.models.runtime_routing_state import AudioDeviceOption
from dreamsync.gui.models.spatial_scene import SceneNode
from dreamsync.gui.qt import QtModules
from dreamsync.gui.services import (
    AudioDeviceService,
    DeviceService,
    ProfileService,
    QueueService,
    RuntimeSupervisor,
    RuntimeTelemetryService,
    SessionService,
    ShowService,
    ShowPatchStore,
    SongPaletteStore,
)
from dreamsync.gui.settings import GuiSettings
from dreamsync.profile import VALID_MOODS, VALID_RENDER_MODES, resolve_profile_path
from dreamsync.profile_overrides import (
    SongPaletteAssignment,
    derive_profile_with_palette_assignment,
)
from dreamsync.show.control_patch import apply_show_control_patch
from dreamsync.show.models import ShowCue, ShowTimeline
from dreamsync.gui.widgets.color_picker import build_color_picker
from dreamsync.gui.widgets.palette_strip import build_palette_strip
from dreamsync.gui.widgets.profile_editor_panel import build_profile_editor_panel
from dreamsync.gui.widgets.queue_panel import build_queue_panel
from dreamsync.gui.widgets.runtime_diagnostics_panel import build_runtime_diagnostics_panel
from dreamsync.gui.widgets.spatial_canvas import build_spatial_canvas


PROFILE_EFFECT_OPTIONS = tuple(sorted(EFFECTS))
PROFILE_PARAM_OPTIONS = (
    "breathe_rate_mult",
    "pulse_decay",
    "scroll_inject_width",
    "wave_rate_mult",
    "wave_wavelength",
    "gradient_speed",
)
PROFILE_EQ_BAND_OPTIONS = ("sub", "kick", "bass", "low_mid", "mid", "presence", "air")
PROFILE_EQ_WHEN_OPTIONS = ("dominant", "enter", "drop", "sustain", "exit", "lift", "swell")
PROFILE_INSTRUMENT_OPTIONS = ("drums", "bass", "vocals", "harmonic", "percussive")
PROFILE_INSTRUMENT_WHEN_OPTIONS = ("dominant", "present", "enter", "drop")
PROFILE_SPATIAL_PRESET_OPTIONS = (
    "",
    "ripple_from_center",
    "flash_floor_only",
    "flash_top_only",
    "blend_left_to_right",
    "blend_front_to_back",
)
SHOW_CUE_MIN_GAP_SECONDS = 0.005
SHOW_CUE_WHEN_OPTIONS = (
    "dominant",
    "present",
    "enter",
    "drop",
    "sustain",
    "exit",
    "lift",
    "swell",
)
SHOW_CUE_COL_BEAT = 0
SHOW_CUE_COL_TIME = 1
SHOW_CUE_COL_RENDER = 2
SHOW_CUE_COL_PALETTE = 3
SHOW_CUE_COL_INTENSITY = 4
SHOW_CUE_COL_SPEED = 5
SHOW_CUE_COL_TRANSITION = 6
SHOW_CUE_COL_TRANSITION_BEATS = 7
SHOW_CUE_COL_WAVE_RATE = 8
SHOW_CUE_COL_WIDTH_SCALE = 9
SHOW_CUE_COL_WHEN = 10
SHOW_CUE_COL_PAN_FOLLOW = 11
SHOW_CUE_COL_INTENSITY_BOOST = 12
SHOW_CUE_COL_INSTRUMENT = 13
SHOW_CUE_COL_CONFIDENCE = 14
SHOW_CUE_COL_EQ_BAND = 15
SHOW_CUE_COL_SPATIAL = 16
SHOW_CUE_COL_COLOR_BIAS = 17
SHOW_CUE_COL_EXTRA = 18
SHOW_CUE_COL_INTENSITY_START = 19


def _format_spatial_summary(scene_entries: list[object], config_path: Path | None) -> str:
    if config_path is None:
        return "No device config loaded. Open the GUI with --config to inspect device placement."
    if not scene_entries:
        return f"Config loaded from {config_path}, but no devices were found."

    physical_addresses = {entry.address for entry in scene_entries}
    section_count = sum(1 for entry in scene_entries if getattr(entry, "is_section", False))
    placed = sum(1 for entry in scene_entries if entry.x != 0.0 or entry.y != 0.0 or entry.z != 0.0)
    defaulted = len(scene_entries) - placed
    summary = [
        f"Loaded {len(physical_addresses)} device(s). "
        f"Loaded {len(physical_addresses)} physical device(s) as {len(scene_entries)} spatial node(s) from {config_path.name}.",
        f"{placed} node(s) have explicit spatial placement; {defaulted} are still using the default origin.",
    ]
    if section_count:
        summary.append(f"{section_count} node(s) are strip sections expanded for ordering/editing.")
    if defaulted:
        summary.append("Default-origin devices overlap at the canvas origin until x/y/z coordinates are set in the config.")
    return " ".join(summary)


def _format_spatial_details(scene_entries: list[object]) -> str:
    if not scene_entries:
        return "No devices loaded."

    rows: list[str] = []
    for entry in scene_entries:
        default_note = " [default placement]" if entry.x == 0.0 and entry.y == 0.0 and entry.z == 0.0 else ""
        section_note = (
            f" | section {entry.section_index + 1}/{entry.section_count}"
            if getattr(entry, "is_section", False) and entry.section_index is not None
            else ""
        )
        rows.append(
            f"{entry.name} | {entry.address}{section_note} | segments={entry.segments} | "
            f"pos=({entry.x:.2f}, {entry.y:.2f}, {entry.z:.2f}){default_note}"
        )
    return "\n".join(rows)


def _format_spatial_node_label(node: SceneNode) -> str:
    if node.is_section and node.section_index is not None:
        return f"{node.physical_name} [{node.section_index + 1}/{node.section_count}]"
    return node.label


def create_main_window(
    qt_modules: QtModules,
    settings: GuiSettings,
    *,
    config_path: Path | None = None,
    profile_path: Path | None = None,
) -> object:
    QtWidgets = qt_modules.QtWidgets
    QtCore = qt_modules.QtCore
    QtGui = qt_modules.QtGui

    device_service = DeviceService()
    audio_device_service = AudioDeviceService()
    profile_service = ProfileService()
    queue_service = QueueService()
    show_service = ShowService()
    session_service = SessionService()
    runtime_supervisor = RuntimeSupervisor(
        session_service=session_service,
        audio_device_service=audio_device_service,
    )
    telemetry_service = RuntimeTelemetryService()
    song_palette_store = SongPaletteStore()
    show_patch_store = ShowPatchStore()

    def _resolve_initial_profile_path() -> Path | None:
        if profile_path and profile_path.exists():
            return profile_path
        if settings.last_profile_path:
            remembered = Path(settings.last_profile_path)
            if remembered.exists():
                return remembered
        for metadata in profile_service.list_profiles():
            file_name = str(metadata.get("file", "")).strip()
            if not file_name:
                continue
            try:
                return resolve_profile_path(Path(file_name).stem)
            except Exception:
                continue
        return None

    active_profile_ref = {"path": _resolve_initial_profile_path()}
    runtime_supervisor.set_config_path(config_path)
    runtime_supervisor.set_output_target_mode(settings.output_target_mode)
    runtime_supervisor.set_hardware_fallback_to_simulation(settings.hardware_fallback_to_simulation)
    runtime_supervisor.set_selected_output_audio_device(settings.selected_output_audio_device_id)
    runtime_supervisor.set_selected_live_input_device(settings.selected_live_input_device_id)
    runtime_supervisor.set_capture_settings(settings.capture_settings)
    runtime_supervisor.set_reactive_settings(settings.reactive_settings)
    runtime_supervisor.set_recent_saved_shows(settings.recent_saved_show_paths)

    spatial_controller = SpatialController(device_service)
    palette_controller = PaletteController(profile_service)
    queue_controller = QueueController(queue_service)
    show_patch_controller = ShowPatchController(show_patch_store)
    queue_controller.set_status("Load a local file or folder to start pairing palettes to songs.")
    assignments = song_palette_store.load()

    window = QtWidgets.QMainWindow()
    window.setWindowTitle("DreamSync Desktop")
    window.resize(1120, 760)

    tabs = QtWidgets.QTabWidget()
    window.setCentralWidget(tabs)

    if config_path and config_path.exists():
        scene_entries = device_service.load_scene(config_path)
        nodes = spatial_controller.load(config_path)
    else:
        scene_entries = []
        nodes = []
    spatial_widget = QtWidgets.QWidget()
    spatial_layout = QtWidgets.QVBoxLayout(spatial_widget)
    spatial_heading = QtWidgets.QLabel("Devices / Spatial")
    spatial_heading.setObjectName("spatialHeadingLabel")
    spatial_layout.addWidget(spatial_heading)
    spatial_summary_label = QtWidgets.QLabel(_format_spatial_summary(scene_entries, config_path))
    spatial_summary_label.setWordWrap(True)
    spatial_summary_label.setObjectName("spatialSummaryLabel")
    spatial_layout.addWidget(spatial_summary_label)
    spatial_toolbar = QtWidgets.QHBoxLayout()
    reload_spatial_button = QtWidgets.QPushButton("Reload Config")
    save_spatial_button = QtWidgets.QPushButton("Save Layout")
    spatial_toolbar.addWidget(reload_spatial_button)
    spatial_toolbar.addWidget(save_spatial_button)
    spatial_toolbar.addStretch(1)
    spatial_layout.addLayout(spatial_toolbar)

    spatial_details = QtWidgets.QPlainTextEdit()
    spatial_details.setObjectName("spatialDetailsBox")
    spatial_details.setReadOnly(True)
    spatial_details.setPlainText(_format_spatial_details(scene_entries))
    spatial_details.setMaximumHeight(110)
    spatial_layout.addWidget(spatial_details)
    spatial_splitter = QtWidgets.QSplitter()
    spatial_layout.addWidget(spatial_splitter, 1)
    spatial_canvas = build_spatial_canvas(qt_modules, nodes)
    spatial_splitter.addWidget(spatial_canvas)
    spatial_editor = QtWidgets.QWidget()
    spatial_editor_layout = QtWidgets.QVBoxLayout(spatial_editor)
    spatial_editor_layout.addWidget(QtWidgets.QLabel("Spatial Nodes"))
    spatial_node_list = QtWidgets.QListWidget()
    spatial_node_list.setObjectName("spatialNodeList")
    spatial_editor_layout.addWidget(spatial_node_list, 1)
    selected_spatial_label = QtWidgets.QLabel("Selected node: none")
    selected_spatial_label.setWordWrap(True)
    selected_spatial_label.setObjectName("selectedSpatialLabel")
    spatial_editor_layout.addWidget(selected_spatial_label)
    view_row = QtWidgets.QHBoxLayout()
    view_row.addWidget(QtWidgets.QLabel("View"))
    spatial_view_combo = QtWidgets.QComboBox()
    spatial_view_combo.addItem("Room", "room")
    spatial_view_combo.addItem("XY Plane", "xy")
    spatial_view_combo.addItem("XZ Plane", "xz")
    spatial_view_combo.addItem("YZ Plane", "yz")
    spatial_view_combo.setObjectName("spatialViewCombo")
    view_row.addWidget(spatial_view_combo, 1)
    spatial_editor_layout.addLayout(view_row)
    axis_group_box = QtWidgets.QGroupBox("Drag Axis (X left/right, Y height, Z depth)")
    axis_row = QtWidgets.QHBoxLayout(axis_group_box)
    axis_button_group = QtWidgets.QButtonGroup(axis_group_box)
    axis_buttons: dict[str, object] = {}
    for axis_name, axis_label in (("x", "X"), ("y", "Y"), ("z", "Z")):
        button = QtWidgets.QRadioButton(axis_label)
        axis_buttons[axis_name] = button
        axis_button_group.addButton(button)
        axis_row.addWidget(button)
    axis_buttons["x"].setChecked(True)
    spatial_editor_layout.addWidget(axis_group_box)
    spin_grid = QtWidgets.QGridLayout()
    spin_grid.addWidget(QtWidgets.QLabel("X (left/right)"), 0, 0)
    spin_grid.addWidget(QtWidgets.QLabel("Y (height)"), 1, 0)
    spin_grid.addWidget(QtWidgets.QLabel("Z (depth)"), 2, 0)
    spatial_x_spin = QtWidgets.QDoubleSpinBox()
    spatial_y_spin = QtWidgets.QDoubleSpinBox()
    spatial_z_spin = QtWidgets.QDoubleSpinBox()
    spatial_x_spin.setObjectName("spatialXSpin")
    spatial_y_spin.setObjectName("spatialYSpin")
    spatial_z_spin.setObjectName("spatialZSpin")
    for spin in (spatial_x_spin, spatial_y_spin, spatial_z_spin):
        spin.setRange(-1.0, 1.0)
        spin.setSingleStep(0.05)
        spin.setDecimals(2)
    spin_grid.addWidget(spatial_x_spin, 0, 1)
    spin_grid.addWidget(spatial_y_spin, 1, 1)
    spin_grid.addWidget(spatial_z_spin, 2, 1)
    spatial_editor_layout.addLayout(spin_grid)
    spatial_status_label = QtWidgets.QLabel(
        "Pick a node from the list or canvas to start editing. "
        "Right-click a node or press Ctrl+A to cycle drag axis (X -> Y -> Z), press Tab or Shift+Tab to move between nodes, "
        "use Left/Right to nudge along the current axis, and press Ctrl+V to cycle Room -> XY -> XZ -> YZ views. "
        "Canonical axes: X left/right, Y height, Z front/back depth."
    )
    spatial_status_label.setWordWrap(True)
    spatial_status_label.setObjectName("spatialStatusLabel")
    spatial_editor_layout.addWidget(spatial_status_label)
    spatial_editor_layout.addStretch(1)
    spatial_splitter.addWidget(spatial_editor)
    spatial_splitter.setStretchFactor(0, 3)
    spatial_splitter.setStretchFactor(1, 2)
    tabs.addTab(spatial_widget, "Devices / Spatial")

    if active_profile_ref["path"] and active_profile_ref["path"].exists():
        palette_state = palette_controller.load(active_profile_ref["path"])
    else:
        palette_state = PaletteEditorState()
    profile_panel = build_profile_editor_panel(qt_modules)
    tabs.addTab(profile_panel.widget, "Palettes")

    queue_panel = build_queue_panel(qt_modules)
    simulation_canvas = build_spatial_canvas(
        qt_modules,
        spatial_controller.snapshot(),
        interactive=False,
        background_theme="dark",
    )
    queue_panel.simulation_layout.addWidget(simulation_canvas)
    tabs.addTab(queue_panel.widget, "Queue")
    tabs.addTab(queue_panel.shows_widget, "Shows")
    tabs.addTab(queue_panel.config_widget, "Config")

    diagnostics_panel = build_runtime_diagnostics_panel(qt_modules)
    tabs.addTab(diagnostics_panel.widget, "Logs / Diagnostics")

    status_bar = QtWidgets.QStatusBar()
    status_bar.showMessage(
        f"profile={active_profile_ref['path'] or settings.last_profile_path or 'none'} | "
        f"config={config_path or settings.last_config_path or 'none'}"
    )
    window.setStatusBar(status_bar)

    playlist_state = {"playlist": None}
    palette_choices: list[dict[str, object]] = []
    spatial_entries_state = {"entries": list(scene_entries)}
    spatial_signal_block = {"value": False}
    simulation_frame_state = {"node_colors": {}}
    simulation_window_state = {"window": None, "canvas": None}
    selected_show_path = {"value": None}
    show_editor_state = {
        "timeline": None,
        "path": None,
        "audio_path": None,
        "context": None,
        "notice": "Load or compile a show to start editing.",
        "focus_sizes": tuple(settings.show_editor_splitter_sizes) or None,
        "focus_enabled": bool(settings.show_editor_focus_mode),
        "meta_hidden": bool(settings.show_editor_meta_hidden),
        "hidden_columns": tuple(int(value) for value in settings.show_editor_hidden_columns),
        "show_palette_dirty": False,
        "show_palette_colors": None,
        "busy": False,
    }

    def _select_combo_data(combo: object, value: object) -> None:
        for index in range(combo.count()):
            if combo.itemData(index) == value:
                combo.setCurrentIndex(index)
                return

    def _populate_device_combo(combo: object, options: tuple[AudioDeviceOption, ...], selected_id: int | None) -> None:
        combo.blockSignals(True)
        combo.clear()
        for option in options:
            combo.addItem(option.label, option.id)
        _select_combo_data(combo, selected_id)
        combo.blockSignals(False)

    def _capture_settings_from_form() -> CaptureSettings:
        return CaptureSettings(
            capture_dir=queue_panel.capture_dir_edit.text().strip() or "captured_songs",
            naming_mode=str(queue_panel.capture_naming_combo.currentData() or "timestamp"),
            max_capture_buffer=int(queue_panel.capture_buffer_spin.value()),
            device_pattern=queue_panel.capture_device_pattern_edit.text().strip() or "CABLE Output",
            sample_rate=int(queue_panel.capture_sample_rate_spin.value()),
            channels=int(queue_panel.capture_channels_spin.value()),
            frame_size=int(queue_panel.capture_frame_size_spin.value()),
            hop_size=int(queue_panel.capture_hop_size_spin.value()),
            blocksize=int(queue_panel.capture_blocksize_spin.value()),
            pipeline_playback_device_id=queue_panel.pipeline_playback_device_combo.currentData(),
            purge_after_playback=bool(queue_panel.purge_after_playback_check.isChecked()),
            debug_pipeline=bool(queue_panel.debug_pipeline_check.isChecked()),
        )

    def _apply_capture_settings_to_form(settings_state: CaptureSettings) -> None:
        widgets = (
            queue_panel.capture_dir_edit,
            queue_panel.capture_naming_combo,
            queue_panel.capture_buffer_spin,
            queue_panel.capture_device_pattern_edit,
            queue_panel.capture_sample_rate_spin,
            queue_panel.capture_channels_spin,
            queue_panel.capture_frame_size_spin,
            queue_panel.capture_hop_size_spin,
            queue_panel.capture_blocksize_spin,
            queue_panel.pipeline_playback_device_combo,
            queue_panel.purge_after_playback_check,
            queue_panel.debug_pipeline_check,
        )
        for widget in widgets:
            widget.blockSignals(True)
        queue_panel.capture_dir_edit.setText(settings_state.capture_dir)
        _select_combo_data(queue_panel.capture_naming_combo, settings_state.naming_mode)
        queue_panel.capture_buffer_spin.setValue(settings_state.max_capture_buffer)
        queue_panel.capture_device_pattern_edit.setText(settings_state.device_pattern)
        queue_panel.capture_sample_rate_spin.setValue(settings_state.sample_rate)
        queue_panel.capture_channels_spin.setValue(settings_state.channels)
        queue_panel.capture_frame_size_spin.setValue(settings_state.frame_size)
        queue_panel.capture_hop_size_spin.setValue(settings_state.hop_size)
        queue_panel.capture_blocksize_spin.setValue(settings_state.blocksize)
        _select_combo_data(queue_panel.pipeline_playback_device_combo, settings_state.pipeline_playback_device_id)
        queue_panel.purge_after_playback_check.setChecked(settings_state.purge_after_playback)
        queue_panel.debug_pipeline_check.setChecked(settings_state.debug_pipeline)
        for widget in widgets:
            widget.blockSignals(False)

    def _reactive_settings_from_form() -> ReactiveSettings:
        profiles = tuple(
            value.strip()
            for value in queue_panel.reactive_rotation_profiles_edit.text().split(",")
            if value.strip()
        )
        return ReactiveSettings(
            render_mode=str(queue_panel.reactive_render_mode_combo.currentData() or "scroll"),
            sample_rate=int(queue_panel.reactive_sample_rate_spin.value()),
            channels=int(queue_panel.reactive_channels_spin.value()),
            frame_size=int(queue_panel.reactive_frame_size_spin.value()),
            hop_size=int(queue_panel.reactive_hop_size_spin.value()),
            blocksize=int(queue_panel.reactive_blocksize_spin.value()),
            half_time=bool(queue_panel.reactive_half_time_check.isChecked()),
            max_brightness=bool(queue_panel.reactive_max_brightness_check.isChecked()),
            auto_cycle=bool(queue_panel.reactive_auto_cycle_check.isChecked()),
            cycle_interval=float(queue_panel.reactive_cycle_interval_spin.value()),
            debug_mood=bool(queue_panel.reactive_debug_mood_check.isChecked()),
            telemetry_dir=queue_panel.reactive_telemetry_dir_edit.text().strip(),
            crossfade_detect=bool(queue_panel.reactive_crossfade_check.isChecked()),
            profile_strategy=str(queue_panel.reactive_profile_strategy_combo.currentData() or "active_profile"),
            profile_override_path=queue_panel.reactive_profile_override_edit.text().strip(),
            rotation_profiles=profiles,
            rotation_interval=float(queue_panel.reactive_rotation_interval_spin.value()),
            auto_palette=bool(queue_panel.reactive_auto_palette_check.isChecked()),
            smart_rotation=bool(queue_panel.reactive_smart_rotation_check.isChecked()),
            chain_blend_seconds=float(queue_panel.reactive_chain_blend_spin.value()),
        )

    def _apply_reactive_settings_to_form(settings_state: ReactiveSettings) -> None:
        widgets = (
            queue_panel.reactive_render_mode_combo,
            queue_panel.reactive_sample_rate_spin,
            queue_panel.reactive_channels_spin,
            queue_panel.reactive_frame_size_spin,
            queue_panel.reactive_hop_size_spin,
            queue_panel.reactive_blocksize_spin,
            queue_panel.reactive_half_time_check,
            queue_panel.reactive_max_brightness_check,
            queue_panel.reactive_auto_cycle_check,
            queue_panel.reactive_cycle_interval_spin,
            queue_panel.reactive_debug_mood_check,
            queue_panel.reactive_crossfade_check,
            queue_panel.reactive_telemetry_dir_edit,
            queue_panel.reactive_profile_strategy_combo,
            queue_panel.reactive_profile_override_edit,
            queue_panel.reactive_rotation_profiles_edit,
            queue_panel.reactive_rotation_interval_spin,
            queue_panel.reactive_auto_palette_check,
            queue_panel.reactive_smart_rotation_check,
            queue_panel.reactive_chain_blend_spin,
        )
        for widget in widgets:
            widget.blockSignals(True)
        _select_combo_data(queue_panel.reactive_render_mode_combo, settings_state.render_mode)
        queue_panel.reactive_sample_rate_spin.setValue(settings_state.sample_rate)
        queue_panel.reactive_channels_spin.setValue(settings_state.channels)
        queue_panel.reactive_frame_size_spin.setValue(settings_state.frame_size)
        queue_panel.reactive_hop_size_spin.setValue(settings_state.hop_size)
        queue_panel.reactive_blocksize_spin.setValue(settings_state.blocksize)
        queue_panel.reactive_half_time_check.setChecked(settings_state.half_time)
        queue_panel.reactive_max_brightness_check.setChecked(settings_state.max_brightness)
        queue_panel.reactive_auto_cycle_check.setChecked(settings_state.auto_cycle)
        queue_panel.reactive_cycle_interval_spin.setValue(settings_state.cycle_interval)
        queue_panel.reactive_debug_mood_check.setChecked(settings_state.debug_mood)
        queue_panel.reactive_crossfade_check.setChecked(settings_state.crossfade_detect)
        queue_panel.reactive_telemetry_dir_edit.setText(settings_state.telemetry_dir)
        _select_combo_data(queue_panel.reactive_profile_strategy_combo, settings_state.profile_strategy)
        queue_panel.reactive_profile_override_edit.setText(settings_state.profile_override_path)
        queue_panel.reactive_rotation_profiles_edit.setText(", ".join(settings_state.rotation_profiles))
        queue_panel.reactive_rotation_interval_spin.setValue(settings_state.rotation_interval)
        queue_panel.reactive_auto_palette_check.setChecked(settings_state.auto_palette)
        queue_panel.reactive_smart_rotation_check.setChecked(settings_state.smart_rotation)
        queue_panel.reactive_chain_blend_spin.setValue(settings_state.chain_blend_seconds)
        for widget in widgets:
            widget.blockSignals(False)

    def _sync_runtime_settings_from_form() -> tuple[CaptureSettings, ReactiveSettings]:
        capture_settings = _capture_settings_from_form()
        reactive_settings = _reactive_settings_from_form()
        runtime_supervisor.set_capture_settings(capture_settings)
        runtime_supervisor.set_reactive_settings(reactive_settings)
        return capture_settings, reactive_settings

    def _format_seconds(seconds: float) -> str:
        total = max(0, int(seconds))
        return f"{total // 60:02d}:{total % 60:02d}"

    def _clamp_axis(value: float) -> float:
        return max(-1.0, min(1.0, value))

    def _current_drag_axis() -> str:
        for axis_name, button in axis_buttons.items():
            if button.isChecked():
                return axis_name
        return "x"

    def _set_drag_axis(axis_name: str) -> str:
        target_axis = axis_name if axis_name in axis_buttons else "x"
        button = axis_buttons[target_axis]
        if button.isChecked():
            spatial_canvas.set_active_axis(target_axis)
        else:
            button.setChecked(True)
        return target_axis

    def _current_view_mode() -> str:
        data = spatial_view_combo.currentData()
        return str(data or "room")

    def _set_view_mode(view_mode: str) -> str:
        target_view = view_mode if view_mode in {"room", "xy", "xz", "yz"} else "room"
        for index in range(spatial_view_combo.count()):
            if spatial_view_combo.itemData(index) == target_view:
                spatial_view_combo.setCurrentIndex(index)
                break
        spatial_canvas.set_view_mode(target_view)
        return target_view

    def _current_simulation_view_mode() -> str:
        data = queue_panel.simulation_view_combo.currentData()
        return str(data or "room")

    def _current_simulation_background() -> str:
        data = queue_panel.simulation_background_combo.currentData()
        return str(data or "dark")

    def _preview_scene_nodes(color_overrides: dict[str, str] | None = None) -> list[SceneNode]:
        overrides = color_overrides or {}
        return [
            replace(node, color=overrides.get(node.key, node.color), selected=False)
            for node in spatial_controller.snapshot()
        ]

    def _simulation_canvases() -> list[object]:
        canvases = [simulation_canvas]
        extra_canvas = simulation_window_state["canvas"]
        if extra_canvas is not None:
            canvases.append(extra_canvas)
        return canvases

    def _apply_preview_canvas_state(canvas: object) -> None:
        canvas.set_nodes(_preview_scene_nodes(simulation_frame_state["node_colors"]))
        canvas.set_view_mode(_current_simulation_view_mode())
        canvas.set_background_theme(_current_simulation_background())

    def _render_preview_simulation(preview_snapshot: dict[str, object] | None = None) -> None:
        if preview_snapshot is not None:
            node_colors = preview_snapshot.get("node_colors", {})
            if isinstance(node_colors, dict):
                simulation_frame_state["node_colors"] = {
                    str(key): str(value) for key, value in node_colors.items()
                }
        for canvas in _simulation_canvases():
            _apply_preview_canvas_state(canvas)

    def _clear_simulation_window_state() -> None:
        simulation_window_state["window"] = None
        simulation_window_state["canvas"] = None

    def _ensure_simulation_window() -> object:  # pragma: no cover - Qt only
        existing = simulation_window_state["window"]
        if existing is not None:
            return existing

        simulation_window = QtWidgets.QMainWindow(window)
        simulation_window.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        simulation_window.setWindowTitle("DreamSync Show Simulation")
        simulation_window.resize(960, 720)
        simulation_window.setMinimumSize(480, 360)
        simulation_window.setCentralWidget(
            build_spatial_canvas(
                qt_modules,
                _preview_scene_nodes(simulation_frame_state["node_colors"]),
                interactive=False,
                background_theme=_current_simulation_background(),
            )
        )
        simulation_canvas_widget = simulation_window.centralWidget()
        simulation_window_state["window"] = simulation_window
        simulation_window_state["canvas"] = simulation_canvas_widget
        _apply_preview_canvas_state(simulation_canvas_widget)

        original_close_event = simulation_window.closeEvent
        original_key_press_event = simulation_window.keyPressEvent

        def _on_close(event) -> None:
            _clear_simulation_window_state()
            original_close_event(event)

        def _on_key_press(event) -> None:
            if (
                event.key() == QtCore.Qt.Key.Key_Escape
                and simulation_window.isFullScreen()
            ):
                simulation_window.showNormal()
                event.accept()
                return
            original_key_press_event(event)

        simulation_window.closeEvent = _on_close
        simulation_window.keyPressEvent = _on_key_press
        return simulation_window

    def _show_simulation_window(*, fullscreen: bool = False) -> None:  # pragma: no cover - Qt only
        simulation_window = _ensure_simulation_window()
        _render_preview_simulation()
        if fullscreen:
            simulation_window.showFullScreen()
        else:
            if simulation_window.isFullScreen():
                simulation_window.showNormal()
            simulation_window.show()
        simulation_window.raise_()
        simulation_window.activateWindow()

    def _refresh_spatial_controls() -> None:
        spatial_signal_block["value"] = True
        spatial_node_list.clear()
        nodes_snapshot = spatial_controller.snapshot()
        for node in nodes_snapshot:
            item = QtWidgets.QListWidgetItem(_format_spatial_node_label(node))
            item.setData(QtCore.Qt.ItemDataRole.UserRole, node.key)
            spatial_node_list.addItem(item)
            if node.selected:
                item.setSelected(True)
        selected_node = spatial_controller.nodes.get(spatial_controller.selected_key)
        if selected_node is None:
            selected_spatial_label.setText("Selected node: none")
            spatial_x_spin.setValue(0.0)
            spatial_y_spin.setValue(0.0)
            spatial_z_spin.setValue(0.0)
        else:
            selected_spatial_label.setText(
                f"Selected node: {_format_spatial_node_label(selected_node)} | "
                f"address={selected_node.address}"
            )
            spatial_x_spin.setValue(selected_node.x)
            spatial_y_spin.setValue(selected_node.y)
            spatial_z_spin.setValue(selected_node.z)
        spatial_canvas.set_nodes(nodes_snapshot)
        _render_preview_simulation()
        spatial_signal_block["value"] = False

    def _reload_spatial_scene(*, status: str | None = None) -> None:
        if config_path is None or not config_path.exists():
            spatial_entries_state["entries"] = []
            spatial_summary_label.setText(_format_spatial_summary([], config_path))
            spatial_details.setPlainText(_format_spatial_details([]))
            spatial_status_label.setText("No config loaded for spatial editing.")
            _render_preview_simulation({"node_colors": {}})
            return
        entries = device_service.load_scene(config_path)
        spatial_entries_state["entries"] = entries
        spatial_controller.load(config_path)
        spatial_summary_label.setText(_format_spatial_summary(entries, config_path))
        spatial_details.setPlainText(_format_spatial_details(entries))
        _refresh_spatial_controls()
        spatial_status_label.setText(status or "Spatial config loaded.")
        _render_preview_simulation()

    def _select_spatial_node(key: str, *, status: str | None = None) -> None:
        if not key:
            return
        node = spatial_controller.select(key)
        if node is None:
            return
        _refresh_spatial_controls()
        if status is not None:
            spatial_status_label.setText(status)

    def _step_selected_spatial_node(step: int) -> None:
        nodes_snapshot = spatial_controller.snapshot()
        if not nodes_snapshot:
            return
        keys = [node.key for node in nodes_snapshot]
        if not keys:
            return
        try:
            current_index = keys.index(spatial_controller.selected_key)
        except ValueError:
            current_index = -1 if step >= 0 else 0
        next_key = keys[(current_index + step) % len(keys)]
        node = spatial_controller.nodes[next_key]
        _select_spatial_node(next_key, status=f"Selected {_format_spatial_node_label(node)} via keyboard.")

    def _update_spatial_node(key: str, *, x: float | None = None, y: float | None = None, z: float | None = None, status: str) -> None:
        node = spatial_controller.nodes[key]
        spatial_controller.update_position(
            key,
            x=_clamp_axis(node.x if x is None else x),
            y=_clamp_axis(node.y if y is None else y),
            z=_clamp_axis(node.z if z is None else z),
        )
        _select_spatial_node(key, status=status)

    def _nudge_selected_spatial_node(direction: int) -> None:
        key = spatial_controller.selected_key
        if not key or key not in spatial_controller.nodes:
            return
        step = 0.05 if direction >= 0 else -0.05
        axis_name = _current_drag_axis()
        node = spatial_controller.nodes[key]
        if axis_name == "x":
            _update_spatial_node(
                key,
                x=node.x + step,
                status=f"Nudged X (left/right) for {_format_spatial_node_label(node)}.",
            )
            return
        if axis_name == "y":
            _update_spatial_node(
                key,
                y=node.y + step,
                status=f"Nudged Y height for {_format_spatial_node_label(node)}.",
            )
            return
        _update_spatial_node(
            key,
            z=node.z + step,
            status=f"Nudged Z depth for {_format_spatial_node_label(node)}.",
        )

    def _save_spatial_scene() -> None:  # pragma: no cover - Qt only
        if config_path is None or not config_path.exists():
            spatial_status_label.setText("No config loaded for spatial editing.")
            return
        placements = {
            key: (node.x, node.y, node.z)
            for key, node in spatial_controller.nodes.items()
        }
        try:
            device_service.save_scene(config_path, placements)
        except Exception as exc:
            spatial_status_label.setText(f"Could not save spatial config: {exc}")
            return
        _reload_spatial_scene(status=f"Saved spatial layout to {config_path.name}.")

    def _on_spatial_list_selection_changed() -> None:  # pragma: no cover - Qt only
        if spatial_signal_block["value"]:
            return
        item = spatial_node_list.currentItem()
        if item is None:
            return
        key = str(item.data(QtCore.Qt.ItemDataRole.UserRole))
        _select_spatial_node(key, status=f"Selected {_format_spatial_node_label(spatial_controller.nodes[key])}.")

    def _on_spatial_canvas_selected(key: str) -> None:  # pragma: no cover - Qt only
        _select_spatial_node(key, status=f"Selected {_format_spatial_node_label(spatial_controller.nodes[key])} from the canvas.")

    def _on_spatial_canvas_axis_cycled(key: str, axis_name: str) -> None:  # pragma: no cover - Qt only
        if key not in spatial_controller.nodes:
            return
        selected_axis = _set_drag_axis(axis_name)
        _select_spatial_node(
            key,
            status=(
                f"Selected {_format_spatial_node_label(spatial_controller.nodes[key])} from the canvas. "
                f"Drag axis set to {selected_axis.upper()}."
            ),
        )

    def _on_spatial_dragged(key: str, delta_x: float, delta_y: float) -> None:  # pragma: no cover - Qt only
        if key not in spatial_controller.nodes:
            return
        axis_name = _current_drag_axis()
        node = spatial_controller.nodes[key]
        view_mode = _current_view_mode()
        if view_mode == "xy" and axis_name == "z":
            spatial_status_label.setText("Switch to XZ, YZ, or Room view to drag along Z depth.")
            return
        if view_mode == "xz" and axis_name == "y":
            spatial_status_label.setText("Switch to XY, YZ, or Room view to drag along Y height.")
            return
        if view_mode == "yz" and axis_name == "x":
            spatial_status_label.setText("Switch to XY, XZ, or Room view to drag along X left/right.")
            return
        if axis_name == "x":
            if view_mode == "room":
                next_x = node.x + (delta_x / 140.0)
            else:
                next_x = node.x + (delta_x / 140.0)
            _update_spatial_node(key, x=next_x, status=f"Adjusted X (left/right) for {_format_spatial_node_label(node)}.")
            return
        if axis_name == "y":
            next_y = node.y - (delta_y / 120.0)
            _update_spatial_node(key, y=next_y, status=f"Adjusted Y height for {_format_spatial_node_label(node)}.")
            return
        if view_mode == "room":
            z_from_x = delta_x / 64.0
            z_from_y = delta_y / 28.0
            next_z = node.z + ((z_from_x + z_from_y) / 2.0)
        elif view_mode == "xz":
            next_z = node.z - (delta_y / 120.0)
        else:
            next_z = node.z + (delta_x / 140.0)
        _update_spatial_node(key, z=next_z, status=f"Adjusted Z depth for {_format_spatial_node_label(node)}.")

    def _on_spatial_spin_changed() -> None:  # pragma: no cover - Qt only
        if spatial_signal_block["value"]:
            return
        key = spatial_controller.selected_key
        if not key or key not in spatial_controller.nodes:
            return
        _update_spatial_node(
            key,
            x=float(spatial_x_spin.value()),
            y=float(spatial_y_spin.value()),
            z=float(spatial_z_spin.value()),
            status=f"Updated coordinates for {_format_spatial_node_label(spatial_controller.nodes[key])}.",
        )

    def _on_spatial_selection_step_requested(step: int) -> None:  # pragma: no cover - Qt only
        _step_selected_spatial_node(step if step != 0 else 1)

    def _on_spatial_axis_nudge_requested(direction: int) -> None:  # pragma: no cover - Qt only
        _nudge_selected_spatial_node(direction if direction != 0 else 1)

    def _on_spatial_view_cycle_requested(view_mode: str) -> None:  # pragma: no cover - Qt only
        selected_view = _set_view_mode(view_mode)
        spatial_status_label.setText(f"Switched spatial view to {selected_view.upper()}.")

    def _set_preview_strip(colors: tuple[str, ...]) -> None:
        while queue_panel.preview_layout.count():
            item = queue_panel.preview_layout.takeAt(0)
            child = item.widget()
            if child is not None:
                child.deleteLater()
        if colors:
            queue_panel.preview_layout.addWidget(build_palette_strip(qt_modules, colors))

    def _clear_layout(layout: object) -> None:
        while layout.count():
            item = layout.takeAt(0)
            child = item.widget()
            if child is not None:
                child.deleteLater()

    def _set_profile_palette_strip(colors: tuple[str, ...]) -> None:
        _clear_layout(profile_panel.palette_strip_layout)
        if colors:
            profile_panel.palette_strip_layout.addWidget(build_palette_strip(qt_modules, colors))
        profile_panel.palette_strip_layout.addStretch(1)

    def _safe_json_load(text: str, fallback):
        try:
            value = json.loads(text or "")
        except json.JSONDecodeError:
            return fallback
        return value if isinstance(value, type(fallback)) else fallback

    def _combo_widget(
        options: tuple[str, ...],
        current: str,
        *,
        allow_blank: bool = True,
        blank_label: str = "Auto",
    ):
        combo = QtWidgets.QComboBox()
        if allow_blank:
            combo.addItem(blank_label, "")
        for option in options:
            combo.addItem(option, option)
        if current and current not in options:
            combo.addItem(current, current)
        _select_combo_data(combo, current)
        return combo

    def _double_spin(
        value: float,
        *,
        minimum: float,
        maximum: float,
        step: float,
        decimals: int = 2,
    ):
        spin = QtWidgets.QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setSingleStep(step)
        spin.setValue(float(value))
        return spin

    def _color_text_for_background(color: str) -> str:
        if len(color) != 7 or not color.startswith("#"):
            return "#111111"
        try:
            red = int(color[1:3], 16)
            green = int(color[3:5], 16)
            blue = int(color[5:7], 16)
        except ValueError:
            return "#111111"
        luminance = (0.299 * red) + (0.587 * green) + (0.114 * blue)
        return "#111111" if luminance >= 160 else "#f8f8f8"

    def _set_color_button(button: object, color: str, *, visible: bool = True, label: str | None = None) -> None:
        if visible:
            text = label or color.upper()
            button.setText(text)
            if label is not None:
                button.setProperty("buttonLabel", label)
            button.setProperty("hexColor", color)
            button.setVisible(True)
            button.setStyleSheet(
                "padding: 6px; border: 1px solid #555;"
                f"background:{color}; color:{_color_text_for_background(color)};"
            )
        else:
            button.setText(label or "")
            if label is not None:
                button.setProperty("buttonLabel", label)
            button.setProperty("hexColor", "")
            button.setVisible(False)
            button.setStyleSheet("")

    def _set_optional_color_button(
        button: object,
        color: str | None,
        *,
        default_label: str,
    ) -> None:
        if color:
            _set_color_button(button, color, label=color.upper())
        else:
            button.setText(default_label)
            button.setProperty("buttonLabel", default_label)
            button.setProperty("hexColor", "")
            button.setStyleSheet("padding: 6px; border: 1px dashed #777; color: #dddddd;")

    def _color_button_widget(
        color: str = "#ffffff",
        *,
        label: str | None = None,
        dialog_title: str = "Choose color",
    ):
        button = QtWidgets.QPushButton()
        _set_color_button(button, color, label=label)
        button.clicked.connect(
            lambda _checked=False, target=button, title=dialog_title: _pick_button_color(target, title)
        )
        return button

    def _pick_button_color(button: object, title: str) -> None:  # pragma: no cover - Qt only
        current_hex = str(button.property("hexColor") or "#ffffff")
        initial = QtWidgets.QColorDialog().currentColor()
        initial.setNamedColor(current_hex)
        selected = QtWidgets.QColorDialog.getColor(initial, window, title)
        if selected.isValid():
            label = button.property("buttonLabel")
            _set_color_button(button, selected.name(), label=str(label) if label else None)

    def _row_value(table: object, row: int, column: int):
        widget = table.cellWidget(row, column)
        if widget is None:
            return ""
        if hasattr(widget, "currentData"):
            return widget.currentData() or ""
        if hasattr(widget, "value"):
            return widget.value()
        return widget.property("hexColor") or ""

    def _normalize_palette_colors(colors: tuple[str, ...] | list[str]) -> tuple[str, ...]:
        normalized = tuple(
            str(color).strip()
            for color in colors
            if str(color).strip().startswith("#") and len(str(color).strip()) == 7
        )
        return normalized or ("#ffffff",)

    def _show_palette_colors_from_timeline(timeline: ShowTimeline | None) -> tuple[str, ...]:
        if timeline is None:
            return ("#ffffff", "#88ccff")
        raw = timeline.metadata.get("show_palette")
        if isinstance(raw, list):
            colors = _normalize_palette_colors(tuple(str(value) for value in raw))
            if colors:
                return colors
        if timeline.cues:
            return _normalize_palette_colors(tuple(timeline.cues[0].color_palette))
        return ("#ffffff", "#88ccff")

    def _set_show_cue_palette_widget_state(widget: object) -> None:
        custom_colors = _normalize_palette_colors(_show_cue_palette_custom_colors(widget))
        base_colors = _normalize_palette_colors(_show_cue_palette_base_colors(widget))
        override_enabled = _show_cue_palette_override(widget)
        effective_colors = custom_colors if override_enabled else base_colors
        widget.setProperty("effectivePaletteColors", effective_colors)
        buttons = tuple(widget.property("paletteButtons") or ())
        for index, button in enumerate(buttons):
            if index < len(effective_colors):
                _set_color_button(button, effective_colors[index], label=f"Color {index + 1}")
                button.setVisible(True)
                button.setEnabled(bool(override_enabled or not _show_cue_palette_can_override(widget)))
            else:
                button.setVisible(False)
        add_button = widget.property("addButton")
        remove_button = widget.property("removeButton")
        if add_button is not None:
            add_button.setEnabled(bool(override_enabled and len(custom_colors) < 8))
        if remove_button is not None:
            remove_button.setEnabled(bool(override_enabled and len(custom_colors) > 1))
        override_box = widget.property("overrideCheckBox")
        if override_box is not None:
            override_box.setChecked(bool(override_enabled))
            override_box.setVisible(bool(_show_cue_palette_can_override(widget)))

    def _show_cue_palette_custom_colors(widget: object) -> tuple[str, ...]:
        raw = widget.property("paletteColors")
        if isinstance(raw, (list, tuple)):
            return _normalize_palette_colors(tuple(str(value) for value in raw))
        return ("#ffffff",)

    def _show_cue_palette_base_colors(widget: object) -> tuple[str, ...]:
        raw = widget.property("basePaletteColors")
        if isinstance(raw, (list, tuple)):
            return _normalize_palette_colors(tuple(str(value) for value in raw))
        return ("#ffffff",)

    def _show_cue_palette_colors(widget: object) -> tuple[str, ...]:
        if _show_cue_palette_override(widget):
            return _show_cue_palette_custom_colors(widget)
        return _show_cue_palette_base_colors(widget)

    def _show_cue_palette_override(widget: object) -> bool:
        return bool(widget.property("paletteOverride"))

    def _show_cue_palette_can_override(widget: object) -> bool:
        return bool(widget.property("paletteCanOverride"))

    def _set_show_cue_palette_base_colors(widget: object, colors: tuple[str, ...]) -> None:
        widget.setProperty("basePaletteColors", _normalize_palette_colors(colors))
        _set_show_cue_palette_widget_state(widget)

    def _set_show_cue_palette_override(widget: object, enabled: bool) -> None:
        widget.setProperty("paletteOverride", bool(enabled))
        _set_show_cue_palette_widget_state(widget)

    def _set_show_cue_palette_custom_colors(widget: object, colors: tuple[str, ...]) -> None:
        widget.setProperty("paletteColors", _normalize_palette_colors(colors))
        _set_show_cue_palette_widget_state(widget)

    def _pick_show_cue_palette_color(widget: object, index: int, on_change) -> None:  # pragma: no cover - Qt only
        if _show_cue_palette_can_override(widget) and not _show_cue_palette_override(widget):
            _set_show_cue_palette_override(widget, True)
        colors = list(_show_cue_palette_custom_colors(widget))
        if index >= len(colors):
            return
        initial = QtWidgets.QColorDialog().currentColor()
        initial.setNamedColor(colors[index])
        selected = QtWidgets.QColorDialog.getColor(initial, window, f"Choose cue color {index + 1}")
        if not selected.isValid():
            return
        colors[index] = selected.name()
        _set_show_cue_palette_custom_colors(widget, tuple(colors))
        on_change()

    def _add_show_cue_palette_color(widget: object, on_change) -> None:  # pragma: no cover - Qt only
        colors = list(_show_cue_palette_custom_colors(widget))
        if len(colors) >= 8:
            return
        colors.append(colors[-1] if colors else "#ffffff")
        _set_show_cue_palette_custom_colors(widget, tuple(colors))
        on_change()

    def _remove_show_cue_palette_color(widget: object, on_change) -> None:  # pragma: no cover - Qt only
        colors = list(_show_cue_palette_custom_colors(widget))
        if len(colors) <= 1:
            return
        colors.pop()
        _set_show_cue_palette_custom_colors(widget, tuple(colors))
        on_change()

    def _toggle_show_cue_palette_override(widget: object, checked: bool, on_change) -> None:  # pragma: no cover - Qt only
        _set_show_cue_palette_override(widget, bool(checked))
        on_change()

    def _show_cue_palette_edit(
        colors: tuple[str, ...],
        *,
        on_change,
        allow_override: bool = True,
        palette_override: bool = True,
        base_colors: tuple[str, ...] | None = None,
        object_name: str = "showCuePaletteEdit",
    ):
        host = QtWidgets.QWidget()
        host.setObjectName(object_name)
        layout = QtWidgets.QHBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        override_box = None
        if allow_override:
            override_box = QtWidgets.QCheckBox("Override")
            override_box.setObjectName("showCuePaletteOverrideCheck")
            override_box.toggled.connect(
                lambda checked, target=host: _toggle_show_cue_palette_override(target, checked, on_change)
            )
            layout.addWidget(override_box)
        buttons = []
        for index in range(8):
            button = QtWidgets.QPushButton()
            button.setObjectName(f"showCuePaletteColorButton{index + 1}")
            button.setMinimumHeight(28)
            button.setMaximumWidth(72)
            button.clicked.connect(
                lambda _checked=False, target=host, color_index=index: _pick_show_cue_palette_color(
                    target,
                    color_index,
                    on_change,
                )
            )
            layout.addWidget(button)
            buttons.append(button)
        add_button = QtWidgets.QToolButton()
        add_button.setObjectName("showCuePaletteAddButton")
        add_button.setText("+")
        add_button.clicked.connect(lambda _checked=False, target=host: _add_show_cue_palette_color(target, on_change))
        layout.addWidget(add_button)
        remove_button = QtWidgets.QToolButton()
        remove_button.setObjectName("showCuePaletteRemoveButton")
        remove_button.setText("-")
        remove_button.clicked.connect(
            lambda _checked=False, target=host: _remove_show_cue_palette_color(target, on_change)
        )
        layout.addWidget(remove_button)
        layout.addStretch(1)
        host.setProperty("paletteButtons", tuple(buttons))
        host.setProperty("addButton", add_button)
        host.setProperty("removeButton", remove_button)
        host.setProperty("overrideCheckBox", override_box)
        host.setProperty("paletteCanOverride", bool(allow_override))
        host.setProperty("basePaletteColors", _normalize_palette_colors(base_colors or colors))
        host.setProperty("paletteColors", _normalize_palette_colors(colors))
        host.setProperty("paletteOverride", bool(palette_override if allow_override else True))
        _set_show_cue_palette_widget_state(host)
        return host

    def _show_cue_params_edit(params: dict[str, object]):
        edit = _show_cue_text_edit(json.dumps(params, sort_keys=True), object_name="showCueParamsEdit")
        return edit

    def _show_cue_text_edit(text: str, *, object_name: str) -> object:
        edit = QtWidgets.QLineEdit(text)
        edit.setObjectName(object_name)
        return edit

    def _row_text_edit(table: object, row: int, column: int) -> str:
        widget = table.cellWidget(row, column)
        if widget is not None and widget.objectName() == "showCuePaletteEdit":
            return ", ".join(_show_cue_palette_colors(widget))
        if widget is not None and widget.objectName() == "showPaletteEdit":
            return ", ".join(_show_cue_palette_colors(widget))
        if widget is None or not hasattr(widget, "text"):
            return ""
        return str(widget.text()).strip()

    def _take_primary_route(
        params: dict[str, object],
        keys: tuple[str, ...],
        predicate,
    ) -> tuple[dict[str, object], str | None]:
        for key in keys:
            raw = params.get(key)
            if not isinstance(raw, list):
                continue
            routes = [dict(entry) for entry in raw if isinstance(entry, dict)]
            for index, route in enumerate(routes):
                if not predicate(route):
                    continue
                remaining = routes[:index] + routes[index + 1 :]
                if remaining:
                    params[key] = remaining
                else:
                    params.pop(key, None)
                return route, key
        return {}, None

    def _show_cue_column_state(cue: ShowCue, *, show_palette: tuple[str, ...]) -> dict[str, object]:
        params = dict(cue.params)
        palette_override = bool(params.pop("palette_override", False))
        eq_route, _eq_key = _take_primary_route(
            params,
            ("active_eq_routes", "eq_routes"),
            lambda route: bool(str(route.get("band", "")).strip()),
        )
        instrument_route, _instrument_key = _take_primary_route(
            params,
            ("active_instrument_routes", "instrument_routes"),
            lambda route: bool(str(route.get("instrument", "")).strip()),
        )

        known_eq_keys = {"band", "when", "intensity_boost", "spatial_preset", "color_bias"}
        known_instrument_keys = {
            "instrument",
            "when",
            "pan_follow",
            "width_scale",
            "confidence",
            "confidence_min",
            "intensity_boost",
            "spatial_preset",
            "color_bias",
        }
        eq_route_extra = {key: value for key, value in eq_route.items() if key not in known_eq_keys}
        instrument_route_extra = {
            key: value for key, value in instrument_route.items() if key not in known_instrument_keys
        }
        if eq_route_extra:
            params["eq_route_extra"] = eq_route_extra
        if instrument_route_extra:
            params["instrument_route_extra"] = instrument_route_extra

        def _float_value(raw: object, default: float) -> float:
            try:
                return float(raw)
            except (TypeError, ValueError):
                return default

        return {
            "palette_override": palette_override,
            "wave_rate_mult": _float_value(params.pop("wave_rate_mult", 1.0), 1.0),
            "width_scale": _float_value(
                instrument_route.get("width_scale", params.pop("width_scale", 1.0)),
                1.0,
            ),
            "when": str(instrument_route.get("when") or eq_route.get("when") or ""),
            "pan_follow": _float_value(
                instrument_route.get("pan_follow", params.pop("pan_follow", 0.0)),
                0.0,
            ),
            "intensity_boost": _float_value(
                instrument_route.get(
                    "intensity_boost",
                    eq_route.get("intensity_boost", params.pop("intensity_boost", 0.0)),
                ),
                0.0,
            ),
            "instrument": str(instrument_route.get("instrument", "")),
            "confidence": _float_value(
                instrument_route.get(
                    "confidence_min",
                    instrument_route.get("confidence", params.pop("confidence", 0.0)),
                ),
                0.0,
            ),
            "eq_band": str(eq_route.get("band", "")),
            "spatial_preset": str(
                instrument_route.get(
                    "spatial_preset",
                    eq_route.get("spatial_preset", params.pop("spatial_preset", "")),
                )
                or ""
            ),
            "color_bias": str(
                instrument_route.get(
                    "color_bias",
                    eq_route.get("color_bias", params.pop("color_bias", "")),
                )
                or ""
            ),
            "extra": json.dumps(params, sort_keys=True),
        }

    def _nearest_beat_index(beat_times: tuple[float, ...], cue_time: float) -> int:
        if not beat_times:
            return -1
        nearest = min(range(len(beat_times)), key=lambda idx: abs(beat_times[idx] - cue_time))
        if abs(beat_times[nearest] - cue_time) <= 0.05:
            return nearest
        return -1

    def _cue_time_from_inputs(beat_index: int, cue_time: float, beat_times: tuple[float, ...]) -> float:
        if beat_index >= 0 and beat_index < len(beat_times):
            return float(beat_times[beat_index])
        return float(cue_time)

    def _set_show_editor_identity(*, audio_path: Path | None, show_path: Path | None) -> None:
        queue_panel.show_audio_label.setText(
            f"Audio track: {audio_path.name if audio_path else 'none'}"
        )
        queue_panel.show_editor_path_label.setText(
            f"Show file: {show_path.name if show_path else 'unsaved'}"
        )

    def _set_show_editor_notice(message: str) -> None:
        show_editor_state["notice"] = message
        queue_panel.show_editor_status_label.setText(message)

    def _set_show_palette_import_notice(message: str, *, actionable: bool) -> None:
        queue_panel.show_palette_import_label.setText(message)
        queue_panel.show_palette_import_label.setVisible(bool(message))
        queue_panel.show_import_palette_button.setVisible(bool(message))
        queue_panel.show_import_palette_button.setEnabled(bool(actionable))

    def _track_compile_notice(audio_path: Path | None) -> str:
        if audio_path is None:
            return "Load or compile a show to start editing."
        return f"{audio_path.name} is ready for compile. Click Compile Show to create a new editable show."

    def _current_profile_palette_colors() -> tuple[str, ...]:
        state = palette_controller.state
        selected_palette = str(state.selected_palette or "")
        colors = state.palettes.get(selected_palette, ())
        return _normalize_palette_colors(tuple(str(value) for value in colors))

    def _replace_editor_timeline(
        *,
        beat_times: tuple[float, ...] | None = None,
        downbeat_times: tuple[float, ...] | None = None,
    ) -> None:
        timeline = show_editor_state["timeline"]
        if timeline is None:
            return
        show_editor_state["timeline"] = ShowTimeline(
            song_path=timeline.song_path,
            duration=timeline.duration,
            bpm=timeline.bpm,
            time_signature=timeline.time_signature,
            beat_times=tuple(beat_times if beat_times is not None else timeline.beat_times),
            downbeat_times=tuple(downbeat_times if downbeat_times is not None else timeline.downbeat_times),
            cues=timeline.cues,
            metadata=dict(timeline.metadata),
        )

    def _clear_show_editor_for_track(audio_path: Path | None, *, notice: str) -> None:
        selected_show_path["value"] = None
        show_editor_state["timeline"] = None
        show_editor_state["path"] = None
        show_editor_state["audio_path"] = audio_path
        show_editor_state["context"] = None
        show_editor_state["notice"] = notice
        show_editor_state["show_palette_dirty"] = False
        show_editor_state["show_palette_colors"] = None
        show_editor_state["busy"] = False
        runtime_supervisor.select_saved_show(None)
        list_widget = queue_panel.recent_saved_list
        blocker = QtCore.QSignalBlocker(list_widget)
        list_widget.clearSelection()
        list_widget.setCurrentRow(-1)
        del blocker
        _render_show_editor()

    def _sync_cue_time_widgets_from_beat_grid() -> None:
        timeline = show_editor_state["timeline"]
        if timeline is None:
            return
        for row in range(queue_panel.show_cues_table.rowCount()):
            beat_index = int(_row_value(queue_panel.show_cues_table, row, SHOW_CUE_COL_BEAT))
            if beat_index < 0 or beat_index >= len(timeline.beat_times):
                continue
            time_widget = queue_panel.show_cues_table.cellWidget(row, SHOW_CUE_COL_TIME)
            if time_widget is None:
                continue
            blocker = QtCore.QSignalBlocker(time_widget)
            time_widget.setValue(float(timeline.beat_times[beat_index]))
            del blocker

    def _show_palette_editor_colors() -> tuple[str, ...]:
        host = queue_panel.show_palette_widget
        if host is None:
            return ("#ffffff", "#88ccff")
        editor = host.property("paletteEditor")
        if editor is None:
            return ("#ffffff", "#88ccff")
        return _show_cue_palette_colors(editor)

    def _current_show_palette_colors() -> tuple[str, ...]:
        if bool(show_editor_state.get("show_palette_dirty")):
            return _show_palette_editor_colors()
        stored = show_editor_state.get("show_palette_colors")
        if isinstance(stored, (list, tuple)) and stored:
            return _normalize_palette_colors(tuple(str(value) for value in stored))
        return _show_palette_editor_colors()

    def _refresh_inherited_cue_palettes() -> None:
        show_palette = _current_show_palette_colors()
        for row in range(queue_panel.show_cues_table.rowCount()):
            widget = queue_panel.show_cues_table.cellWidget(row, SHOW_CUE_COL_PALETTE)
            if widget is None:
                continue
            _set_show_cue_palette_base_colors(widget, show_palette)

    def _update_show_palette_import_affordance() -> None:
        timeline = show_editor_state.get("timeline")
        profile_colors = _current_profile_palette_colors()
        if timeline is None or not profile_colors:
            _set_show_palette_import_notice("", actionable=False)
            return
        show_colors = _current_show_palette_colors()
        if _normalize_palette_colors(profile_colors) == _normalize_palette_colors(show_colors):
            _set_show_palette_import_notice("", actionable=False)
            return
        _set_show_palette_import_notice(
            "You have created a new Palette. Click Import Palette to update Show Palette.",
            actionable=True,
        )

    def _on_show_palette_changed() -> None:
        show_editor_state["show_palette_dirty"] = True
        show_editor_state["show_palette_colors"] = _show_palette_editor_colors()
        _refresh_inherited_cue_palettes()
        _update_show_palette_import_affordance()
        _set_show_editor_notice("Show palette updated. Non-overridden cues now follow the new default palette.")
        _render_show_timeline_view(playhead_seconds=_current_show_timeline_position())

    def _ensure_show_palette_editor() -> object:
        host = queue_panel.show_palette_widget
        editor = host.property("paletteEditor")
        if editor is not None:
            return editor
        layout = QtWidgets.QHBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        editor = _show_cue_palette_edit(
            ("#ffffff", "#88ccff"),
            on_change=_on_show_palette_changed,
            allow_override=False,
            object_name="showPaletteEdit",
        )
        layout.addWidget(editor)
        host.setProperty("paletteEditor", editor)
        return editor

    def _set_show_palette_editor_colors(colors: tuple[str, ...]) -> None:
        editor = _ensure_show_palette_editor()
        _set_show_cue_palette_custom_colors(editor, colors)
        _set_show_cue_palette_base_colors(editor, colors)

    def _apply_show_palette_editor_colors(
        colors: tuple[str, ...],
        *,
        mark_dirty: bool,
        notice: str | None = None,
    ) -> None:
        normalized = _normalize_palette_colors(colors)
        _set_show_palette_editor_colors(normalized)
        show_editor_state["show_palette_dirty"] = bool(mark_dirty)
        show_editor_state["show_palette_colors"] = normalized
        _refresh_inherited_cue_palettes()
        _update_show_palette_import_affordance()
        if notice:
            _set_show_editor_notice(notice)
        _render_show_timeline_view(playhead_seconds=_current_show_timeline_position())

    def _import_profile_palette_into_show() -> None:
        timeline = show_editor_state.get("timeline")
        if timeline is None:
            _set_show_editor_notice("Load or compile a show before importing a palette into it.")
            return
        profile_colors = _current_profile_palette_colors()
        if not profile_colors:
            _set_show_editor_notice("Select or create a palette in the Palettes tab first.")
            return
        _apply_show_palette_editor_colors(
            profile_colors,
            mark_dirty=True,
            notice="Imported the selected palette into the show palette. Non-overridden cues now follow it.",
        )

    def _row_effective_cue_time(row: int) -> float:
        timeline = show_editor_state["timeline"]
        beat_times = tuple(timeline.beat_times) if timeline is not None else ()
        beat_index = int(_row_value(queue_panel.show_cues_table, row, SHOW_CUE_COL_BEAT))
        cue_time = float(_row_value(queue_panel.show_cues_table, row, SHOW_CUE_COL_TIME))
        return _cue_time_from_inputs(beat_index, cue_time, beat_times)

    def _store_cue_row_timing_snapshot(row: int) -> None:
        beat_widget = queue_panel.show_cues_table.cellWidget(row, SHOW_CUE_COL_BEAT)
        time_widget = queue_panel.show_cues_table.cellWidget(row, SHOW_CUE_COL_TIME)
        if beat_widget is None or time_widget is None:
            return
        beat_widget.setProperty("lastValidValue", int(beat_widget.value()))
        time_widget.setProperty("lastValidValue", float(time_widget.value()))

    def _restore_cue_row_timing_snapshot(row: int) -> None:
        beat_widget = queue_panel.show_cues_table.cellWidget(row, SHOW_CUE_COL_BEAT)
        time_widget = queue_panel.show_cues_table.cellWidget(row, SHOW_CUE_COL_TIME)
        if beat_widget is None or time_widget is None:
            return
        last_valid_beat = beat_widget.property("lastValidValue")
        last_valid_time = time_widget.property("lastValidValue")
        beat_blocker = QtCore.QSignalBlocker(beat_widget)
        time_blocker = QtCore.QSignalBlocker(time_widget)
        if last_valid_beat is not None:
            beat_widget.setValue(int(last_valid_beat))
        if last_valid_time is not None:
            time_widget.setValue(float(last_valid_time))
        del beat_blocker
        del time_blocker

    def _commit_cue_row_timing_edit(row: int, *, manual_free: bool = False) -> None:
        beat_widget = queue_panel.show_cues_table.cellWidget(row, SHOW_CUE_COL_BEAT)
        time_widget = queue_panel.show_cues_table.cellWidget(row, SHOW_CUE_COL_TIME)
        if beat_widget is None or time_widget is None:
            return
        if manual_free and int(beat_widget.value()) >= 0:
            timeline = show_editor_state["timeline"]
            beat_times = tuple(timeline.beat_times) if timeline is not None else ()
            beat_index = int(beat_widget.value())
            if 0 <= beat_index < len(beat_times):
                if abs(float(time_widget.value()) - float(beat_times[beat_index])) > 1e-6:
                    blocker = QtCore.QSignalBlocker(beat_widget)
                    beat_widget.setValue(-1)
                    del blocker
        if _validate_cue_row_timing(row):
            _set_show_editor_notice("Cue timing updated in the editor timeline.")
            _render_show_timeline_view(playhead_seconds=_current_show_timeline_position())

    def _validate_cue_row_timing(row: int) -> bool:
        table = queue_panel.show_cues_table
        if row < 0 or row >= table.rowCount():
            return True
        try:
            current_time = _row_effective_cue_time(row)
        except Exception:
            return True
        prev_time = _row_effective_cue_time(row - 1) if row > 0 else None
        next_time = _row_effective_cue_time(row + 1) if row + 1 < table.rowCount() else None
        if prev_time is not None and current_time < prev_time + SHOW_CUE_MIN_GAP_SECONDS:
            _restore_cue_row_timing_snapshot(row)
            warning = (
                f"Cues must stay at least {SHOW_CUE_MIN_GAP_SECONDS:.3f}s apart. "
                "Resetting this cue to its previous position."
            )
            _set_show_editor_notice(warning)
            queue_controller.set_status(warning)
            _render_queue_state(queue_controller.state)
            _render_show_timeline_view(playhead_seconds=_current_show_timeline_position())
            return False
        if next_time is not None and current_time > next_time - SHOW_CUE_MIN_GAP_SECONDS:
            _restore_cue_row_timing_snapshot(row)
            warning = (
                f"Cues must stay at least {SHOW_CUE_MIN_GAP_SECONDS:.3f}s apart. "
                "Resetting this cue to its previous position."
            )
            _set_show_editor_notice(warning)
            queue_controller.set_status(warning)
            _render_queue_state(queue_controller.state)
            _render_show_timeline_view(playhead_seconds=_current_show_timeline_position())
            return False
        _store_cue_row_timing_snapshot(row)
        return True

    def _sync_show_editor_for_selected_track(*, load_existing_show: bool = True) -> None:
        selected = queue_controller.state.selected_track
        if selected is None:
            return
        audio_path = Path(selected.path)
        current_audio = show_editor_state["audio_path"]
        current_path = show_editor_state["path"]
        sibling_show = audio_path.with_suffix(".show.json")

        if (
            isinstance(current_audio, Path)
            and current_audio == audio_path
            and show_editor_state["timeline"] is not None
            and (not load_existing_show or current_path is None or current_path == sibling_show)
        ):
            return

        if load_existing_show and sibling_show.exists():
            try:
                timeline, context = _run_show_editor_busy_task(
                    f"Loading saved show for {audio_path.name}...",
                    lambda: (
                        lambda loaded_timeline: (
                            loaded_timeline,
                            show_service.build_timeline_context(
                                audio_path=audio_path,
                                timeline=loaded_timeline,
                            ),
                        )
                    )(show_service.load_timeline(sibling_show)),
                )
            except Exception as exc:
                queue_controller.set_status(f"Could not auto-load saved show: {exc}")
                _render_queue_state(queue_controller.state)
                _clear_show_editor_for_track(
                    audio_path,
                    notice=f"{audio_path.name} has a show file, but it could not be loaded: {exc}",
                )
                return
            _load_timeline_into_editor(
                timeline,
                audio_path=audio_path,
                show_path=sibling_show,
                status=f"Loaded saved show for {audio_path.name}.",
                context=context,
            )
            return

        notice = _track_compile_notice(audio_path)
        _clear_show_editor_for_track(audio_path, notice=notice)
        queue_controller.set_status(notice)
        _render_queue_state(queue_controller.state)

    def _current_show_timeline_position() -> float | None:
        session = runtime_supervisor.active_session()
        if session is None or not hasattr(session, "session_snapshot"):
            return None
        snapshot = session.session_snapshot()
        editor_audio_path = show_editor_state["audio_path"]
        current_track = snapshot.get("current_track")
        if isinstance(editor_audio_path, Path) and current_track:
            if Path(str(current_track)) != editor_audio_path:
                return None
        return float(snapshot.get("position_seconds", 0.0) or 0.0)

    def _active_show_cue_index(timeline: ShowTimeline | None, position_seconds: float | None) -> int | None:
        if timeline is None or position_seconds is None or not timeline.cues:
            return None
        active_index = None
        for index, cue in enumerate(timeline.cues):
            if cue.t <= position_seconds + 1e-9:
                active_index = index
            else:
                break
        return active_index

    def _highlight_show_cue_rows(active_index: int | None) -> None:
        table = queue_panel.show_cues_table
        highlight = QtGui.QColor("#233047")
        transparent = QtGui.QColor(0, 0, 0, 0)
        for row in range(table.rowCount()):
            is_active = row == active_index
            for column in range(table.columnCount()):
                item = table.item(row, column)
                if item is None:
                    item = QtWidgets.QTableWidgetItem("")
                    item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
                    table.setItem(row, column, item)
                item.setBackground(highlight if is_active else transparent)
                cell_widget = table.cellWidget(row, column)
                if cell_widget is not None:
                    cell_widget.setStyleSheet(
                        "background-color: rgba(95, 143, 255, 0.18);" if is_active else ""
                    )
        if active_index is not None and 0 <= active_index < table.rowCount():
            target_item = table.item(active_index, 0)
            if target_item is None:
                target_item = QtWidgets.QTableWidgetItem("")
                target_item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
                table.setItem(active_index, 0, target_item)
            table.scrollToItem(
                target_item,
                QtWidgets.QAbstractItemView.ScrollHint.PositionAtCenter,
            )

    def _safe_editor_timeline() -> ShowTimeline | None:
        try:
            return _timeline_from_editor()
        except Exception:
            return show_editor_state["timeline"]

    def _render_show_timeline_view(*, playhead_seconds: float | None = None) -> None:
        timeline = _safe_editor_timeline()
        context = show_editor_state.get("context")
        selected_index = queue_panel.show_cues_table.currentRow()
        if selected_index < 0:
            selected_index = None
        active_index = _active_show_cue_index(timeline, playhead_seconds)
        _highlight_show_cue_rows(active_index)
        if timeline is None:
            queue_panel.show_timeline_view.set_timeline_data(
                duration=1.0,
                waveform=(),
                beats=(),
                downbeats=(),
                sections=(),
                cue_times=(),
                selected_cue_index=None,
                active_cue_index=None,
                playhead_seconds=None,
            )
            return
        duration = float(timeline.duration)
        waveform = tuple(getattr(context, "waveform", ())) if context is not None else ()
        sections = tuple(getattr(context, "sections", ())) if context is not None else ()
        queue_panel.show_timeline_view.set_timeline_data(
            duration=duration,
            waveform=waveform,
            beats=tuple(float(value) for value in timeline.beat_times),
            downbeats=tuple(float(value) for value in timeline.downbeat_times),
            sections=sections,
            cue_times=tuple(float(cue.t) for cue in timeline.cues),
            selected_cue_index=selected_index,
            active_cue_index=active_index,
            playhead_seconds=playhead_seconds,
        )
        _sync_show_timeline_scroll_bar()

    def _show_editor_busy_buttons() -> tuple[object, ...]:
        return (
            queue_panel.compile_show_button,
            queue_panel.load_show_button,
            queue_panel.clear_show_button,
            queue_panel.save_show_button,
            queue_panel.play_saved_show_button,
            queue_panel.play_selected_cue_button,
            queue_panel.show_import_palette_button,
        )

    def _run_show_editor_busy_task(message: str, callback):
        app = QtWidgets.QApplication.instance()
        previous_enabled = {
            button: bool(button.isEnabled())
            for button in _show_editor_busy_buttons()
            if button is not None
        }
        dialog = QtWidgets.QProgressDialog(message, "", 0, 0, window)
        dialog.setWindowTitle("Working")
        dialog.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
        dialog.setCancelButton(None)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setRange(0, 0)
        show_editor_state["busy"] = True
        _set_show_editor_notice(message)
        queue_controller.set_status(message)
        _render_queue_state(queue_controller.state)
        for button in previous_enabled:
            button.setEnabled(False)
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        dialog.show()
        if app is not None:
            app.processEvents()
        result_box: dict[str, object] = {}
        error_box: dict[str, BaseException] = {}

        def _worker() -> None:
            try:
                result_box["value"] = callback()
            except BaseException as exc:  # pragma: no cover - surfaced through caller
                error_box["value"] = exc

        worker = threading.Thread(target=_worker, daemon=True)
        worker.start()
        try:
            while worker.is_alive():
                worker.join(0.05)
                if app is not None:
                    app.processEvents()
        finally:
            dialog.close()
            QtWidgets.QApplication.restoreOverrideCursor()
            for button, enabled in previous_enabled.items():
                button.setEnabled(enabled)
            show_editor_state["busy"] = False
            if app is not None:
                app.processEvents()
        if "value" in error_box:
            raise error_box["value"]
        return result_box.get("value")

    def _set_show_timeline_zoom_slider(factor: float) -> None:
        slider = queue_panel.show_timeline_zoom_slider
        blocker = QtCore.QSignalBlocker(slider)
        slider.setValue(int(round(max(1.0, min(float(factor), 30.0)) * 100.0)))
        del blocker

    def _sync_show_timeline_scroll_bar(*_args) -> None:
        scroll_bar = queue_panel.show_timeline_scroll_bar
        view = queue_panel.show_timeline_view
        max_start = float(view.max_view_start_seconds())
        blocker = QtCore.QSignalBlocker(scroll_bar)
        if max_start <= 1e-6:
            scroll_bar.setEnabled(False)
            scroll_bar.setRange(0, 0)
            scroll_bar.setPageStep(0)
            scroll_bar.setValue(0)
        else:
            scroll_bar.setEnabled(True)
            scroll_bar.setRange(0, int(round(max_start * 1000.0)))
            scroll_bar.setPageStep(int(round(view.visible_duration_seconds() * 1000.0)))
            scroll_bar.setValue(int(round(view.view_start_seconds() * 1000.0)))
        del blocker

    def _set_show_timeline_scroll_value(value: int) -> None:
        queue_panel.show_timeline_view.set_view_start_seconds(float(value) / 1000.0)

    def _toggle_show_editor_focus_mode(enabled: bool) -> None:  # pragma: no cover - Qt only
        splitter = queue_panel.shows_splitter
        left_panel = queue_panel.shows_left_panel
        button = queue_panel.show_editor_focus_button
        show_editor_state["focus_enabled"] = bool(enabled)
        blocker = QtCore.QSignalBlocker(button)
        button.setChecked(bool(enabled))
        button.setText("Restore Layout" if enabled else "Focus Editor")
        del blocker
        if enabled:
            show_editor_state["focus_sizes"] = splitter.sizes()
            left_panel.setVisible(False)
            splitter.setSizes([0, 1])
        else:
            left_panel.setVisible(True)
            splitter.setSizes(show_editor_state.get("focus_sizes") or [1, 2])

    def _toggle_show_meta_panel(hidden: bool) -> None:  # pragma: no cover - Qt only
        show_editor_state["meta_hidden"] = bool(hidden)
        queue_panel.show_meta_panel.setVisible(not bool(hidden))
        button = queue_panel.show_meta_toggle_button
        blocker = QtCore.QSignalBlocker(button)
        button.setChecked(bool(hidden))
        button.setText("Show Details" if hidden else "Hide Details")
        del blocker

    def _set_show_table_column_visible(column: int, visible: bool) -> None:
        queue_panel.show_cues_table.setColumnHidden(column, not bool(visible))
        hidden_columns = {
            index
            for index in range(queue_panel.show_cues_table.columnCount())
            if queue_panel.show_cues_table.isColumnHidden(index)
        }
        show_editor_state["hidden_columns"] = tuple(sorted(hidden_columns))

    def _initialize_show_columns_menu() -> None:
        button = queue_panel.show_columns_button
        menu = QtWidgets.QMenu(button)
        button.setMenu(menu)
        initial_hidden = set(show_editor_state.get("hidden_columns") or ())
        for column in range(queue_panel.show_cues_table.columnCount()):
            header_item = queue_panel.show_cues_table.horizontalHeaderItem(column)
            label = header_item.text() if header_item is not None else f"Column {column + 1}"
            action = menu.addAction(label)
            action.setCheckable(True)
            visible = column not in initial_hidden
            _set_show_table_column_visible(column, visible)
            action.setChecked(visible)
            action.toggled.connect(
                lambda checked, column_index=column: _set_show_table_column_visible(column_index, checked)
            )

    def _move_beat_marker(index: int, next_time: float) -> None:
        timeline = show_editor_state["timeline"]
        if timeline is None or not (0 <= index < len(timeline.beat_times)):
            return
        epsilon = 0.01
        beat_times = list(float(value) for value in timeline.beat_times)
        old_time = beat_times[index]
        lower = beat_times[index - 1] + epsilon if index > 0 else 0.0
        upper = beat_times[index + 1] - epsilon if index + 1 < len(beat_times) else float(timeline.duration)
        clamped = max(lower, min(float(next_time), upper))
        beat_times[index] = clamped
        downbeats = list(float(value) for value in timeline.downbeat_times)
        for downbeat_index, downbeat_time in enumerate(downbeats):
            if abs(downbeat_time - old_time) <= 1e-6:
                downbeats[downbeat_index] = clamped
        _replace_editor_timeline(beat_times=tuple(beat_times), downbeat_times=tuple(downbeats))
        _sync_cue_time_widgets_from_beat_grid()
        _set_show_editor_notice("Beat grid updated in the editor timeline.")
        _render_show_timeline_view(playhead_seconds=_current_show_timeline_position())

    def _move_downbeat_marker(index: int, next_time: float) -> None:
        timeline = show_editor_state["timeline"]
        if timeline is None or not (0 <= index < len(timeline.downbeat_times)):
            return
        epsilon = 0.01
        downbeats = list(float(value) for value in timeline.downbeat_times)
        old_time = downbeats[index]
        lower = downbeats[index - 1] + epsilon if index > 0 else 0.0
        upper = downbeats[index + 1] - epsilon if index + 1 < len(downbeats) else float(timeline.duration)
        clamped = max(lower, min(float(next_time), upper))
        downbeats[index] = clamped
        beat_times = list(float(value) for value in timeline.beat_times)
        nearest_beat_index = min(range(len(beat_times)), key=lambda beat_index: abs(beat_times[beat_index] - old_time))
        if abs(beat_times[nearest_beat_index] - old_time) <= 0.05:
            beat_times[nearest_beat_index] = clamped
        _replace_editor_timeline(beat_times=tuple(beat_times), downbeat_times=tuple(downbeats))
        _sync_cue_time_widgets_from_beat_grid()
        _set_show_editor_notice("Downbeat grid updated in the editor timeline.")
        _render_show_timeline_view(playhead_seconds=_current_show_timeline_position())

    def _move_section_boundary(index: int, edge: str, next_time: float) -> None:
        context = show_editor_state.get("context")
        if context is None or not (0 <= index < len(context.sections)):
            return
        timeline = _safe_editor_timeline()
        duration = float(timeline.duration) if timeline is not None else float(getattr(context, "duration", 0.0))
        epsilon = 0.01
        sections = list(context.sections)
        section = sections[index]
        if edge == "start":
            lower = sections[index - 1].end_t if index > 0 else 0.0
            upper = float(section.end_t) - epsilon
            next_start = max(lower, min(float(next_time), upper))
            sections[index] = replace(section, start_t=next_start)
        else:
            lower = float(section.start_t) + epsilon
            upper = sections[index + 1].start_t if index + 1 < len(sections) else duration
            next_end = max(lower, min(float(next_time), upper))
            sections[index] = replace(section, end_t=next_end)
        show_editor_state["context"] = replace(context, sections=tuple(sections))
        _set_show_editor_notice("Section boundaries updated in the editor timeline.")
        _render_show_timeline_view(playhead_seconds=_current_show_timeline_position())

    def _populate_show_cue_row(
        row: int,
        cue: ShowCue,
        beat_times: tuple[float, ...],
        *,
        show_palette: tuple[str, ...],
    ) -> None:
        table = queue_panel.show_cues_table
        refresh_timeline = lambda *_args: _render_show_timeline_view(  # noqa: E731
            playhead_seconds=_current_show_timeline_position()
        )
        cue_state = _show_cue_column_state(cue, show_palette=show_palette)
        beat_index = _nearest_beat_index(beat_times, cue.t)
        beat_spin = QtWidgets.QSpinBox()
        beat_spin.setRange(-1, max(len(beat_times) - 1, 0))
        beat_spin.setSpecialValueText("Free")
        beat_spin.setValue(beat_index)
        table.setCellWidget(row, SHOW_CUE_COL_BEAT, beat_spin)
        time_spin = _double_spin(cue.t, minimum=0.0, maximum=7200.0, step=0.01, decimals=3)
        table.setCellWidget(row, SHOW_CUE_COL_TIME, time_spin)

        def _sync_time_from_beat_index(index: int) -> None:
            current_timeline = show_editor_state["timeline"]
            current_beats = tuple(current_timeline.beat_times) if current_timeline is not None else beat_times
            if 0 <= int(index) < len(current_beats):
                blocker = QtCore.QSignalBlocker(time_spin)
                time_spin.setValue(float(current_beats[int(index)]))
                del blocker
            _commit_cue_row_timing_edit(row)

        beat_spin.valueChanged.connect(_sync_time_from_beat_index)
        time_spin.valueChanged.connect(refresh_timeline)
        time_spin.editingFinished.connect(lambda row_index=row: _commit_cue_row_timing_edit(row_index, manual_free=True))

        render_combo = _combo_widget(tuple(sorted(VALID_RENDER_MODES)), cue.render_mode, allow_blank=False)
        render_combo.currentIndexChanged.connect(refresh_timeline)
        table.setCellWidget(row, SHOW_CUE_COL_RENDER, render_combo)
        palette_edit = _show_cue_palette_edit(
            cue.color_palette,
            on_change=refresh_timeline,
            allow_override=True,
            palette_override=bool(cue_state["palette_override"]),
            base_colors=show_palette,
        )
        table.setCellWidget(row, SHOW_CUE_COL_PALETTE, palette_edit)
        intensity_spin = _double_spin(cue.intensity, minimum=0.0, maximum=1.5, step=0.05)
        intensity_spin.valueChanged.connect(refresh_timeline)
        table.setCellWidget(row, SHOW_CUE_COL_INTENSITY, intensity_spin)
        speed_spin = _double_spin(cue.speed, minimum=-10.0, maximum=10.0, step=0.05, decimals=3)
        speed_spin.valueChanged.connect(refresh_timeline)
        table.setCellWidget(row, SHOW_CUE_COL_SPEED, speed_spin)
        transition_combo = _combo_widget(("cut", "fade"), cue.transition, allow_blank=False)
        transition_combo.currentIndexChanged.connect(refresh_timeline)
        table.setCellWidget(row, SHOW_CUE_COL_TRANSITION, transition_combo)
        trans_beats = QtWidgets.QSpinBox()
        trans_beats.setRange(0, 64)
        trans_beats.setValue(int(cue.transition_beats))
        trans_beats.valueChanged.connect(refresh_timeline)
        table.setCellWidget(row, SHOW_CUE_COL_TRANSITION_BEATS, trans_beats)

        wave_rate_spin = _double_spin(
            float(cue_state["wave_rate_mult"]),
            minimum=0.0,
            maximum=12.0,
            step=0.05,
            decimals=3,
        )
        wave_rate_spin.setSpecialValueText("Auto")
        wave_rate_spin.valueChanged.connect(refresh_timeline)
        table.setCellWidget(row, SHOW_CUE_COL_WAVE_RATE, wave_rate_spin)

        width_scale_spin = _double_spin(
            float(cue_state["width_scale"]),
            minimum=0.0,
            maximum=6.0,
            step=0.05,
            decimals=3,
        )
        width_scale_spin.setSpecialValueText("Auto")
        width_scale_spin.valueChanged.connect(refresh_timeline)
        table.setCellWidget(row, SHOW_CUE_COL_WIDTH_SCALE, width_scale_spin)

        when_combo = _combo_widget(SHOW_CUE_WHEN_OPTIONS, str(cue_state["when"]))
        when_combo.currentIndexChanged.connect(refresh_timeline)
        table.setCellWidget(row, SHOW_CUE_COL_WHEN, when_combo)

        pan_follow_spin = _double_spin(
            float(cue_state["pan_follow"]),
            minimum=0.0,
            maximum=1.5,
            step=0.05,
            decimals=3,
        )
        pan_follow_spin.setSpecialValueText("Auto")
        pan_follow_spin.valueChanged.connect(refresh_timeline)
        table.setCellWidget(row, SHOW_CUE_COL_PAN_FOLLOW, pan_follow_spin)

        intensity_boost_spin = _double_spin(
            float(cue_state["intensity_boost"]),
            minimum=-1.0,
            maximum=2.0,
            step=0.05,
            decimals=3,
        )
        intensity_boost_spin.valueChanged.connect(refresh_timeline)
        table.setCellWidget(row, SHOW_CUE_COL_INTENSITY_BOOST, intensity_boost_spin)

        instrument_combo = _combo_widget(PROFILE_INSTRUMENT_OPTIONS, str(cue_state["instrument"]))
        instrument_combo.currentIndexChanged.connect(refresh_timeline)
        table.setCellWidget(row, SHOW_CUE_COL_INSTRUMENT, instrument_combo)

        confidence_spin = _double_spin(
            float(cue_state["confidence"]),
            minimum=0.0,
            maximum=1.0,
            step=0.05,
            decimals=3,
        )
        confidence_spin.setSpecialValueText("Auto")
        confidence_spin.valueChanged.connect(refresh_timeline)
        table.setCellWidget(row, SHOW_CUE_COL_CONFIDENCE, confidence_spin)

        eq_band_combo = _combo_widget(PROFILE_EQ_BAND_OPTIONS, str(cue_state["eq_band"]))
        eq_band_combo.currentIndexChanged.connect(refresh_timeline)
        table.setCellWidget(row, SHOW_CUE_COL_EQ_BAND, eq_band_combo)

        spatial_combo = _combo_widget(PROFILE_SPATIAL_PRESET_OPTIONS, str(cue_state["spatial_preset"]))
        spatial_combo.currentIndexChanged.connect(refresh_timeline)
        table.setCellWidget(row, SHOW_CUE_COL_SPATIAL, spatial_combo)

        color_bias_edit = _show_cue_text_edit(str(cue_state["color_bias"]), object_name="showCueColorBiasEdit")
        color_bias_edit.textChanged.connect(refresh_timeline)
        table.setCellWidget(row, SHOW_CUE_COL_COLOR_BIAS, color_bias_edit)

        extra_edit = _show_cue_params_edit(json.loads(str(cue_state["extra"])))
        extra_edit.textChanged.connect(refresh_timeline)
        table.setCellWidget(row, SHOW_CUE_COL_EXTRA, extra_edit)

        intensity_start_spin = _double_spin(
            cue.intensity_start if cue.intensity_start is not None else 0.0,
            minimum=0.0,
            maximum=1.5,
            step=0.05,
        )
        intensity_start_spin.setSpecialValueText("Auto")
        intensity_start_spin.valueChanged.connect(refresh_timeline)
        table.setCellWidget(row, SHOW_CUE_COL_INTENSITY_START, intensity_start_spin)
        _store_cue_row_timing_snapshot(row)

    def _timeline_from_editor() -> ShowTimeline | None:
        timeline = show_editor_state["timeline"]
        if timeline is None:
            return None
        beat_times = tuple(float(value) for value in timeline.beat_times)
        downbeat_times = tuple(float(value) for value in timeline.downbeat_times)
        show_palette = _current_show_palette_colors()
        cues: list[ShowCue] = []
        for row in range(queue_panel.show_cues_table.rowCount()):
            beat_index = int(_row_value(queue_panel.show_cues_table, row, SHOW_CUE_COL_BEAT))
            cue_time = float(_row_value(queue_panel.show_cues_table, row, SHOW_CUE_COL_TIME))
            render_mode = str(_row_value(queue_panel.show_cues_table, row, SHOW_CUE_COL_RENDER))
            palette_text = _row_text_edit(queue_panel.show_cues_table, row, SHOW_CUE_COL_PALETTE)
            params_text = _row_text_edit(queue_panel.show_cues_table, row, SHOW_CUE_COL_EXTRA)
            if not render_mode:
                continue
            palette_widget = queue_panel.show_cues_table.cellWidget(row, SHOW_CUE_COL_PALETTE)
            palette_override = True
            if palette_widget is not None and palette_widget.objectName() == "showCuePaletteEdit":
                palette_override = _show_cue_palette_override(palette_widget)
            palette = tuple(part.strip() for part in palette_text.split(",") if part.strip())
            if not palette:
                palette = show_palette
            try:
                params = json.loads(params_text) if params_text else {}
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid cue params on row {row + 1}: {exc.msg}") from exc
            if not isinstance(params, dict):
                raise ValueError(f"Cue params on row {row + 1} must be a JSON object.")
            eq_route_extra = params.pop("eq_route_extra", {})
            instrument_route_extra = params.pop("instrument_route_extra", {})
            if eq_route_extra and not isinstance(eq_route_extra, dict):
                raise ValueError(f"eq_route_extra on row {row + 1} must be a JSON object.")
            if instrument_route_extra and not isinstance(instrument_route_extra, dict):
                raise ValueError(f"instrument_route_extra on row {row + 1} must be a JSON object.")

            wave_rate = float(_row_value(queue_panel.show_cues_table, row, SHOW_CUE_COL_WAVE_RATE))
            width_scale = float(_row_value(queue_panel.show_cues_table, row, SHOW_CUE_COL_WIDTH_SCALE))
            when = str(_row_value(queue_panel.show_cues_table, row, SHOW_CUE_COL_WHEN) or "").strip()
            pan_follow = float(_row_value(queue_panel.show_cues_table, row, SHOW_CUE_COL_PAN_FOLLOW))
            intensity_boost = float(
                _row_value(queue_panel.show_cues_table, row, SHOW_CUE_COL_INTENSITY_BOOST)
            )
            instrument = str(_row_value(queue_panel.show_cues_table, row, SHOW_CUE_COL_INSTRUMENT) or "").strip()
            confidence = float(_row_value(queue_panel.show_cues_table, row, SHOW_CUE_COL_CONFIDENCE))
            eq_band = str(_row_value(queue_panel.show_cues_table, row, SHOW_CUE_COL_EQ_BAND) or "").strip()
            spatial_preset = str(
                _row_value(queue_panel.show_cues_table, row, SHOW_CUE_COL_SPATIAL) or ""
            ).strip()
            color_bias = _row_text_edit(queue_panel.show_cues_table, row, SHOW_CUE_COL_COLOR_BIAS)

            if abs(wave_rate - 1.0) > 1e-9:
                params["wave_rate_mult"] = wave_rate
            if palette_override:
                params["palette_override"] = True
            else:
                params.pop("palette_override", None)
                palette = show_palette

            instrument_route: dict[str, object] | None = None
            if instrument or instrument_route_extra:
                instrument_route = dict(instrument_route_extra)
                if instrument:
                    instrument_route["instrument"] = instrument
                if when:
                    instrument_route["when"] = when
                if abs(pan_follow) > 1e-9:
                    instrument_route["pan_follow"] = pan_follow
                if abs(width_scale - 1.0) > 1e-9:
                    instrument_route["width_scale"] = width_scale
                if abs(confidence) > 1e-9:
                    instrument_route["confidence_min"] = confidence
                if abs(intensity_boost) > 1e-9:
                    instrument_route["intensity_boost"] = intensity_boost
                if spatial_preset:
                    instrument_route["spatial_preset"] = spatial_preset
                if color_bias:
                    instrument_route["color_bias"] = color_bias
                params["instrument_routes"] = [instrument_route]
                params["active_instrument_routes"] = [dict(instrument_route)]
            else:
                if when:
                    params["when"] = when
                if abs(pan_follow) > 1e-9:
                    params["pan_follow"] = pan_follow
                if abs(width_scale - 1.0) > 1e-9:
                    params["width_scale"] = width_scale
                if abs(confidence) > 1e-9:
                    params["confidence"] = confidence

            eq_route: dict[str, object] | None = None
            if eq_band or eq_route_extra:
                eq_route = dict(eq_route_extra)
                if eq_band:
                    eq_route["band"] = eq_band
                if when:
                    eq_route["when"] = when
                if abs(intensity_boost) > 1e-9:
                    eq_route["intensity_boost"] = intensity_boost
                if spatial_preset:
                    eq_route["spatial_preset"] = spatial_preset
                if color_bias:
                    eq_route["color_bias"] = color_bias
                params["eq_routes"] = [eq_route]
                params["active_eq_routes"] = [dict(eq_route)]
            elif instrument_route is None:
                if abs(intensity_boost) > 1e-9:
                    params["intensity_boost"] = intensity_boost
                if spatial_preset:
                    params["spatial_preset"] = spatial_preset
                if color_bias:
                    params["color_bias"] = color_bias

            intensity_start_raw = float(
                _row_value(queue_panel.show_cues_table, row, SHOW_CUE_COL_INTENSITY_START)
            )
            intensity_raw = float(_row_value(queue_panel.show_cues_table, row, SHOW_CUE_COL_INTENSITY))
            actual_time = _cue_time_from_inputs(beat_index, cue_time, beat_times)
            cues.append(
                ShowCue(
                    t=actual_time,
                    render_mode=render_mode,
                    color_palette=palette,
                    intensity=float(intensity_raw),
                    speed=float(_row_value(queue_panel.show_cues_table, row, SHOW_CUE_COL_SPEED)),
                    params=params,
                    transition=str(
                        _row_value(queue_panel.show_cues_table, row, SHOW_CUE_COL_TRANSITION) or "cut"
                    ),
                    transition_beats=int(
                        _row_value(queue_panel.show_cues_table, row, SHOW_CUE_COL_TRANSITION_BEATS)
                    ),
                    intensity_start=(
                        None
                        if abs(intensity_start_raw) < 1e-9
                        else float(intensity_start_raw)
                    ),
                )
            )
        cues.sort(key=lambda cue: cue.t)
        return ShowTimeline(
            song_path=str(show_editor_state["audio_path"] or timeline.song_path),
            duration=float(queue_panel.show_duration_spin.value()),
            bpm=float(queue_panel.show_bpm_spin.value()),
            time_signature=int(queue_panel.show_time_signature_combo.currentData() or 4),
            beat_times=beat_times,
            downbeat_times=downbeat_times,
            cues=tuple(cues),
            metadata={
                **dict(timeline.metadata),
                "track_name": queue_panel.show_title_edit.text().strip(),
                "artist": queue_panel.show_artist_edit.text().strip(),
                "show_palette": list(show_palette),
                "editor_sections": [
                    {
                        "start_t": round(float(section.start_t), 4),
                        "end_t": round(float(section.end_t), 4),
                        "label": str(section.label),
                    }
                    for section in tuple(getattr(show_editor_state.get("context"), "sections", ()))
                ],
            },
        )

    def _render_show_editor() -> None:
        timeline = show_editor_state["timeline"]
        audio_path = show_editor_state["audio_path"]
        show_path = show_editor_state["path"]
        show_palette_state = show_editor_state.get("show_palette_colors")
        if isinstance(show_palette_state, (list, tuple)) and show_palette_state:
            show_palette = _normalize_palette_colors(tuple(str(value) for value in show_palette_state))
        else:
            show_palette = _show_palette_colors_from_timeline(timeline)
            show_editor_state["show_palette_colors"] = show_palette
        _set_show_palette_editor_colors(show_palette)
        if timeline is None:
            show_editor_state["show_palette_dirty"] = False
        _set_show_editor_identity(audio_path=audio_path, show_path=show_path)
        _set_show_editor_notice(str(show_editor_state.get("notice") or _track_compile_notice(audio_path)))
        _update_show_palette_import_affordance()
        queue_panel.show_meta_panel.setVisible(not bool(show_editor_state.get("meta_hidden")))
        meta_hidden = bool(show_editor_state.get("meta_hidden"))
        queue_panel.show_meta_toggle_button.setChecked(meta_hidden)
        queue_panel.show_meta_toggle_button.setText("Show Details" if meta_hidden else "Hide Details")
        queue_panel.show_cues_table.setRowCount(0)
        if timeline is None:
            queue_panel.show_title_edit.setText("")
            queue_panel.show_artist_edit.setText("")
            queue_panel.show_bpm_spin.setValue(120.0)
            _select_combo_data(queue_panel.show_time_signature_combo, 4)
            queue_panel.show_duration_spin.setValue(180.0)
            _set_show_timeline_zoom_slider(1.0)
            _render_show_timeline_view(playhead_seconds=None)
            return
        queue_panel.show_title_edit.setText(str(timeline.metadata.get("track_name", "")))
        queue_panel.show_artist_edit.setText(str(timeline.metadata.get("artist", "")))
        queue_panel.show_bpm_spin.setValue(float(timeline.bpm))
        _select_combo_data(queue_panel.show_time_signature_combo, timeline.time_signature)
        queue_panel.show_duration_spin.setValue(float(timeline.duration))
        for cue in timeline.cues:
            row = queue_panel.show_cues_table.rowCount()
            queue_panel.show_cues_table.insertRow(row)
            _populate_show_cue_row(row, cue, timeline.beat_times, show_palette=show_palette)
        _set_show_timeline_zoom_slider(queue_panel.show_timeline_view.zoom_factor())
        _render_show_timeline_view(playhead_seconds=_current_show_timeline_position())

    def _populate_effects_table(effects: list[dict[str, object]]) -> None:
        table = profile_panel.effects_table
        table.setRowCount(0)
        for entry in effects:
            row = table.rowCount()
            table.insertRow(row)
            table.setCellWidget(
                row,
                0,
                _combo_widget(PROFILE_EFFECT_OPTIONS, str(entry.get("name", "warm_glow")), allow_blank=False),
            )
            table.setCellWidget(
                row,
                1,
                _double_spin(
                    float(entry.get("weight", 1.0) or 1.0),
                    minimum=0.0,
                    maximum=10.0,
                    step=0.1,
                ),
            )

    def _populate_params_table(params: dict[str, object]) -> None:
        table = profile_panel.params_table
        table.setRowCount(0)
        for key, value in params.items():
            row = table.rowCount()
            table.insertRow(row)
            table.setCellWidget(
                row,
                0,
                _combo_widget(PROFILE_PARAM_OPTIONS, str(key), allow_blank=False),
            )
            try:
                numeric_value = float(value)
            except (TypeError, ValueError):
                numeric_value = 0.0
            table.setCellWidget(
                row,
                1,
                _double_spin(numeric_value, minimum=-1000.0, maximum=1000.0, step=0.05, decimals=3),
            )

    def _populate_eq_routes_table(table: object, routes: list[dict[str, object]]) -> None:
        table.setRowCount(0)
        for entry in routes:
            row = table.rowCount()
            table.insertRow(row)
            table.setCellWidget(row, 0, _combo_widget(PROFILE_EQ_BAND_OPTIONS, str(entry.get("band", "bass")), allow_blank=False))
            table.setCellWidget(row, 1, _combo_widget(PROFILE_EQ_WHEN_OPTIONS, str(entry.get("when", "dominant")), allow_blank=False))
            table.setCellWidget(row, 2, _combo_widget(tuple(sorted(VALID_RENDER_MODES)), str(entry.get("render_mode", ""))))
            table.setCellWidget(row, 3, _color_button_widget(str(entry.get("color_bias", "#ffffff")), label="Pick"))
            table.setCellWidget(
                row,
                4,
                _double_spin(
                    float(entry.get("intensity_boost", 0.0) or 0.0),
                    minimum=-1.0,
                    maximum=2.0,
                    step=0.05,
                ),
            )
            table.setCellWidget(
                row,
                5,
                _combo_widget(PROFILE_SPATIAL_PRESET_OPTIONS, str(entry.get("spatial_preset", "")), blank_label="None"),
            )

    def _populate_instrument_routes_table(table: object, routes: list[dict[str, object]]) -> None:
        table.setRowCount(0)
        for entry in routes:
            row = table.rowCount()
            table.insertRow(row)
            table.setCellWidget(row, 0, _combo_widget(PROFILE_INSTRUMENT_OPTIONS, str(entry.get("instrument", "vocals")), allow_blank=False))
            table.setCellWidget(row, 1, _combo_widget(PROFILE_INSTRUMENT_WHEN_OPTIONS, str(entry.get("when", "dominant")), allow_blank=False))
            table.setCellWidget(row, 2, _combo_widget(tuple(sorted(VALID_RENDER_MODES)), str(entry.get("render_mode", ""))))
            table.setCellWidget(row, 3, _color_button_widget(str(entry.get("color_bias", "#ffffff")), label="Pick"))
            table.setCellWidget(
                row,
                4,
                _double_spin(
                    float(entry.get("intensity_boost", 0.0) or 0.0),
                    minimum=-1.0,
                    maximum=2.0,
                    step=0.05,
                ),
            )
            table.setCellWidget(
                row,
                5,
                _combo_widget(PROFILE_SPATIAL_PRESET_OPTIONS, str(entry.get("spatial_preset", "")), blank_label="None"),
            )
            table.setCellWidget(
                row,
                6,
                _double_spin(float(entry.get("pan_follow", 0.0) or 0.0), minimum=0.0, maximum=1.0, step=0.05),
            )
            table.setCellWidget(
                row,
                7,
                _double_spin(float(entry.get("width_scale", 1.0) or 1.0), minimum=0.1, maximum=4.0, step=0.05),
            )
            table.setCellWidget(
                row,
                8,
                _double_spin(float(entry.get("confidence_min", 0.45) or 0.45), minimum=0.0, maximum=1.0, step=0.05),
            )

    def _palette_choices_for_transitions(state: PaletteEditorState) -> tuple[str, ...]:
        merged = list(state.palettes.keys())
        for palette_name in PALETTES:
            if palette_name not in merged:
                merged.append(palette_name)
        return tuple(merged)

    def _current_seed_colors() -> tuple[str, ...]:
        colors = []
        for button in profile_panel.seed_color_buttons:
            color = str(button.property("hexColor") or "").strip()
            if color:
                colors.append(color)
        return tuple(colors)

    def _quickshow_directory() -> Path:
        appdata = Path.home() / ".dreamsync"
        if "APPDATA" in os.environ:
            appdata = Path(os.environ["APPDATA"]) / "DreamSync"
        return appdata / "quickshows"

    def _set_active_profile_path(path: Path | None) -> None:
        active_profile_ref["path"] = path
        status_bar.showMessage(
            f"profile={path or settings.last_profile_path or 'none'} | "
            f"config={config_path or settings.last_config_path or 'none'}"
        )

    def _load_profile_into_editor(path: Path, *, status: str) -> None:
        state = palette_controller.load(path)
        _set_active_profile_path(path)
        _render_profile_state(state)
        _apply_palette_choices(
            profile_service.load_palette_choices(path),
            status=f"{status} Active profile palettes ready from {path.name}.",
        )

    def _populate_transitions_table(transitions: list[dict[str, object]], state: PaletteEditorState) -> None:
        table = profile_panel.transitions_table
        table.setRowCount(0)
        palette_options = _palette_choices_for_transitions(state)
        mood_options = tuple(sorted(VALID_MOODS))
        for entry in transitions:
            row = table.rowCount()
            table.insertRow(row)
            table.setCellWidget(row, 0, _combo_widget(mood_options, str(entry.get("from", state.selected_mood or "chill")), allow_blank=False))
            table.setCellWidget(row, 1, _combo_widget(mood_options, str(entry.get("to", "groove")), allow_blank=False))
            table.setCellWidget(row, 2, _combo_widget(palette_options, str(entry.get("palette", state.selected_palette)), allow_blank=False))

    def _render_profile_state(state: PaletteEditorState) -> None:
        profile_panel.profile_name_label.setText(
            f"Profile: {state.profile_name or 'none'}"
            + (f" ({Path(state.profile_path).name})" if state.profile_path else "")
        )
        profile_panel.palette_combo.blockSignals(True)
        profile_panel.palette_combo.clear()
        for palette_name in state.palettes:
            profile_panel.palette_combo.addItem(palette_name, palette_name)
        _select_combo_data(profile_panel.palette_combo, state.selected_palette)
        profile_panel.palette_combo.blockSignals(False)
        current_colors = state.palettes.get(state.selected_palette, ())
        _set_profile_palette_strip(current_colors)
        seed_defaults = ("Seed 1", "Optional Seed 2")
        for index, button in enumerate(profile_panel.seed_color_buttons):
            current_seed = str(button.property("hexColor") or "").strip()
            _set_optional_color_button(button, current_seed or None, default_label=seed_defaults[index])
        for index, button in enumerate(profile_panel.palette_color_buttons):
            if index < len(current_colors):
                _set_color_button(button, current_colors[index])
            else:
                _set_color_button(button, "#ffffff", visible=False)
        profile_panel.add_color_button.setEnabled(len(current_colors) < 8 and bool(state.selected_palette))
        profile_panel.remove_color_button.setEnabled(len(current_colors) > 3 and bool(state.selected_palette))

        profile_panel.mood_combo.blockSignals(True)
        profile_panel.mood_combo.clear()
        for mood in state.moods or tuple(sorted(VALID_MOODS)):
            profile_panel.mood_combo.addItem(mood, mood)
        _select_combo_data(profile_panel.mood_combo, state.selected_mood)
        profile_panel.mood_combo.blockSignals(False)

        _populate_effects_table(_safe_json_load(state.mood_effects_text, []))
        _populate_params_table(_safe_json_load(state.mood_params_text, {}))
        _populate_eq_routes_table(profile_panel.profile_eq_routes_table, _safe_json_load(state.profile_eq_routes_text, []))
        _populate_eq_routes_table(profile_panel.mood_eq_routes_table, _safe_json_load(state.mood_eq_routes_text, []))
        _populate_instrument_routes_table(
            profile_panel.profile_instrument_routes_table,
            _safe_json_load(state.profile_instrument_routes_text, []),
        )
        _populate_instrument_routes_table(
            profile_panel.mood_instrument_routes_table,
            _safe_json_load(state.mood_instrument_routes_text, []),
        )
        _populate_transitions_table(_safe_json_load(state.transitions_text, []), state)
        profile_panel.status_label.setText(state.status_message)
        _update_show_palette_import_affordance()

    def _render_show_patch_state() -> None:
        state = show_patch_controller.state
        queue_panel.patch_name_edit.setText(state.patch_name)
        queue_panel.patch_rules_edit.setPlainText(state.patch_rules_text)
        queue_panel.patch_summary_label.setText(state.patch_summary)

    def _render_queue_state(state: QueueState) -> None:
        selected = state.selected_track
        queue_panel.playlist_label.setText(
            f"Queue source: {state.playlist_source_path}" if state.playlist_source_path else "No local queue loaded."
        )
        queue_panel.local_list.blockSignals(True)
        queue_panel.local_list.clear()
        for track in state.local_tracks:
            prefix = "▶ " if track.is_current else ""
            suffix = f" [{track.assignment_label}]" if track.assignment_label else ""
            item = QtWidgets.QListWidgetItem(f"{prefix}{track.display_name}{suffix}")
            item.setData(QtCore.Qt.ItemDataRole.UserRole, track.track_key)
            queue_panel.local_list.addItem(item)
            if track.track_key == state.selected_track_key:
                item.setSelected(True)
        queue_panel.local_list.blockSignals(False)

        queue_panel.spotify_list.clear()
        if state.spotify_current:
            queue_panel.spotify_list.addItem(f"Now Playing: {state.spotify_current}")
        for item in state.spotify_upcoming:
            queue_panel.spotify_list.addItem(item)

        if selected is None:
            queue_panel.selected_song_label.setText("Selected song: none")
            queue_panel.selected_assignment_label.setText("Assigned palette: none")
            queue_panel.selected_path_label.setText("Path: none")
            if show_patch_controller.state.selected_track_key:
                show_patch_controller.bind_track("", "", "")
        else:
            queue_panel.selected_song_label.setText(f"Selected song: {selected.display_name}")
            queue_panel.selected_assignment_label.setText(
                f"Assigned palette: {selected.assignment_label or 'none'}"
            )
            queue_panel.selected_path_label.setText(f"Path: {selected.path}")
            if show_patch_controller.state.selected_track_key != selected.track_key:
                show_patch_controller.bind_track(
                    selected.track_key,
                    selected.display_name,
                    selected.path,
                )

        if state.preview_colors:
            queue_panel.preview_label.setText(
                f"Palette Preview: {state.preview_source_label or 'palette'} / {state.preview_palette_name or 'palette'}"
            )
        else:
            queue_panel.preview_label.setText("Palette Preview: none")
        _set_preview_strip(state.preview_colors)
        queue_panel.status_label.setText(state.status_message)
        _render_show_patch_state()

    def _active_local_session():
        session = runtime_supervisor.active_session()
        if session is not None and hasattr(session, "session_snapshot"):
            return session
        return None

    def _pending_preview_snapshot(state: RuntimeModeState) -> dict[str, object] | None:
        if state.active_output_mode == "idle":
            return None
        state = "starting"
        track = None
        if queue_controller.state.selected_track is not None:
            track = queue_controller.state.selected_track.path
        elif queue_controller.state.local_tracks:
            track = queue_controller.state.local_tracks[0].path
        elif queue_controller.state.playlist_source_path:
            track = queue_controller.state.playlist_source_path
        return {
            "current_track": track,
            "playback_state": state,
            "position_seconds": 0.0,
            "duration_seconds": 0.0,
            "audio_output": "system default",
            "input_device": runtime_supervisor.snapshot().input_device or "system default",
            "device_status": "preparing preview session",
        }

    def _sync_show_playback_controls(runtime_state: RuntimeModeState) -> None:
        active_mode = str(runtime_state.active_output_mode or "idle")
        session = runtime_supervisor.active_session()
        snapshot = (
            session.session_snapshot()
            if session is not None and hasattr(session, "session_snapshot")
            else {}
        )
        playback_state = str(snapshot.get("playback_state", "idle") or "idle")
        duration_seconds = float(snapshot.get("duration_seconds", 0.0) or 0.0)
        show_capable = active_mode in {
            "saved_show",
            "timeline_playback",
            "local_preview",
            "pipeline_playback",
            "local_playlist",
        } and duration_seconds > 0.0
        queue_panel.show_pause_button.setText("Resume" if playback_state == "paused" else "Pause")
        queue_panel.show_pause_button.setEnabled(show_capable and playback_state in {"playing", "paused"})
        queue_panel.show_stop_button.setEnabled(show_capable and playback_state not in {"idle", "finished", "stopped"})

    def _toggle_show_output_pause() -> None:  # pragma: no cover - Qt only
        result = runtime_supervisor.toggle_output_pause()
        if result == "paused":
            queue_controller.set_status("Show playback paused.")
        elif result == "playing":
            queue_controller.set_status("Show playback resumed.")
        else:
            queue_controller.set_status("No active show playback is available for pause or resume.")
        _render_queue_state(queue_controller.state)
        _render_runtime_state(runtime_supervisor.snapshot())
        if result in {"paused", "playing"}:
            queue_timer.start()

    def _render_runtime_state(runtime_state: RuntimeModeState) -> None:
        output_mode = runtime_state.active_output_mode.replace("_", " ")
        capture_state = runtime_state.capture_state
        pipeline_state = runtime_state.pipeline_state
        queue_panel.output_mode_label.setText(f"Output Mode: {output_mode or 'idle'}")
        queue_panel.capture_status_label.setText(f"Capture: {capture_state.title()}")
        queue_panel.pipeline_status_label.setText(
            f"Pipeline: {pipeline_state.title()} / Ready {runtime_state.ready_queue_count}"
        )
        queue_panel.routing_status_label.setText(f"Routing: {runtime_state.routing_state.routing_status}")
        queue_panel.active_owner_label.setText(f"Active owner: {runtime_state.active_output_mode or 'idle'}")
        queue_panel.armed_owner_label.setText(
            f"Armed owner: {runtime_state.armed_output_mode or 'none'}"
        )
        saved_show_text = runtime_state.selected_show_path or "none"
        if saved_show_text and saved_show_text != "none":
            saved_show_text = Path(saved_show_text).name
        queue_panel.saved_show_label.setText(f"Saved show: {saved_show_text}")
        queue_panel.recent_saved_list.clear()
        for path in runtime_state.recent_saved_shows:
            recent_item = QtWidgets.QListWidgetItem(Path(path).name)
            recent_item.setData(QtCore.Qt.ItemDataRole.UserRole, path)
            queue_panel.recent_saved_list.addItem(recent_item)
        _sync_show_playback_controls(runtime_state)
        queue_panel.ready_list.clear()
        for item in runtime_state.ready_items:
            title = item.display_label
            duration_suffix = f" ({item.duration:.1f}s)" if item.duration is not None else ""
            suffix = f" [{item.state}]{duration_suffix}"
            ready_item = QtWidgets.QListWidgetItem(f"{title}{suffix}")
            ready_item.setData(QtCore.Qt.ItemDataRole.UserRole, item.item_id)
            queue_panel.ready_list.addItem(ready_item)
        _select_combo_data(queue_panel.output_target_combo, runtime_state.routing_state.output_target.mode)
        queue_panel.hardware_fallback_check.setChecked(
            runtime_state.routing_state.output_target.fallback_to_simulation
        )
        _populate_device_combo(
            queue_panel.output_device_combo,
            runtime_state.routing_state.available_output_devices,
            runtime_state.routing_state.selected_output_audio_device_id,
        )
        _populate_device_combo(
            queue_panel.input_device_combo,
            runtime_state.routing_state.available_input_devices,
            runtime_state.routing_state.selected_live_input_device_id,
        )
        _populate_device_combo(
            queue_panel.pipeline_playback_device_combo,
            runtime_state.routing_state.available_output_devices,
            runtime_state.capture_settings.pipeline_playback_device_id,
        )
        _apply_capture_settings_to_form(runtime_state.capture_settings)
        _apply_reactive_settings_to_form(runtime_state.reactive_settings)
        runtime_control = runtime_supervisor.runtime_control_snapshot()
        active_override = "active" if runtime_control.get("active") else "inactive"
        queue_panel.runtime_control_status_label.setText(
            f"Live overrides are {active_override}. "
            f"Revision {runtime_control.get('revision', 0)}."
        )
        _render_diagnostics()

    def _render_session_status(snapshot: dict[str, object] | None, *, running: bool, error: Exception | None = None) -> None:
        if error is not None:
            queue_panel.playback_status_label.setText("Playback: error")
            queue_panel.playback_track_label.setText(f"Current track: error ({error})")
            queue_panel.playback_time_label.setText("Time: 00:00 / 00:00")
            queue_panel.device_status_label.setText("Devices: unavailable")
            queue_panel.audio_output_label.setText("Audio output: unavailable")
            queue_panel.input_device_status_label.setText("Input device: unavailable")
            return

        if snapshot is None:
            queue_panel.playback_status_label.setText("Playback: idle")
            queue_panel.playback_track_label.setText("Current track: none")
            queue_panel.playback_time_label.setText("Time: 00:00 / 00:00")
            queue_panel.device_status_label.setText("Devices: preview mode (no connected devices)")
            queue_panel.audio_output_label.setText("Audio output: system default")
            queue_panel.input_device_status_label.setText("Input device: system default")
            return

        state = str(snapshot.get("playback_state", "idle")).replace("_", " ")
        prefix = "running" if running else "stopped"
        queue_panel.playback_status_label.setText(f"Playback: {state} ({prefix})")

        track = snapshot.get("current_track")
        track_name = Path(track).name if track else "none"
        queue_panel.playback_track_label.setText(f"Current track: {track_name}")

        position = float(snapshot.get("position_seconds", 0.0) or 0.0)
        duration = float(snapshot.get("duration_seconds", 0.0) or 0.0)
        queue_panel.playback_time_label.setText(
            f"Time: {_format_seconds(position)} / {_format_seconds(duration)}"
        )
        queue_panel.device_status_label.setText(
            f"Devices: {snapshot.get('device_status', 'preview mode (no connected devices)')}"
        )
        queue_panel.audio_output_label.setText(
            f"Audio output: {snapshot.get('audio_output', 'system default')}"
        )
        queue_panel.input_device_status_label.setText(
            f"Input device: {snapshot.get('input_device', 'system default')}"
        )

    def _render_diagnostics() -> None:
        telemetry = telemetry_service.snapshot(runtime_supervisor)
        diagnostics_panel.status_box.setPlainText(
            "\n".join(
                [
                    f"Output mode: {telemetry.output_mode}",
                    f"Capture: {telemetry.capture_state}",
                    f"Pipeline: {telemetry.pipeline_state}",
                    f"Ready queue: {telemetry.ready_queue_count}",
                    f"Current track: {telemetry.current_track or 'none'}",
                    f"Routing: {telemetry.routing_status}",
                    f"Devices: {telemetry.device_status or 'unknown'}",
                    f"Audio output: {telemetry.audio_output or 'system default'}",
                    f"Input device: {telemetry.input_device or 'system default'}",
                    f"Render mode: {telemetry.current_render_mode or 'auto'}",
                    f"Palette: {', '.join(telemetry.current_palette) or 'none'}",
                    f"Dominant band: {telemetry.dominant_band or 'none'}",
                    f"Dominant proxy: {telemetry.dominant_proxy or 'none'}",
                    f"Pan center / width: {telemetry.pan_center:.3f} / {telemetry.pan_width:.3f}",
                    f"Last error: {telemetry.last_error or 'none'}",
                ]
            )
        )
        metrics_lines = [
            f"{key}: {value}"
            for key, value in sorted(telemetry.metrics.items(), key=lambda item: item[0])
        ]
        metrics_lines.extend(
            [
                "",
                f"Active EQ routes: {len(telemetry.active_eq_routes)}",
                *[
                    f"  - {route.get('band', '?')} / {route.get('when', '?')}"
                    for route in telemetry.active_eq_routes
                ],
                f"Active instrument routes: {len(telemetry.active_instrument_routes)}",
                *[
                    f"  - {route.get('instrument', '?')} / {route.get('when', '?')}"
                    for route in telemetry.active_instrument_routes
                ],
                f"Active scene layers: {len(telemetry.active_scene_layers)}",
                *[
                    f"  - {layer.get('band') or layer.get('instrument') or layer.get('source', '?')}"
                    for layer in telemetry.active_scene_layers
                ],
            ]
        )
        diagnostics_panel.metrics_box.setPlainText("\n".join(metrics_lines) or "No metrics yet.")
        override_lines = [f"{key}: {value}" for key, value in sorted(telemetry.runtime_control.items())]
        diagnostics_panel.events_box.setPlainText(
            "\n".join(
                [
                    "Recent Events:",
                    *(telemetry.recent_events or ("No runtime events yet.",)),
                    "",
                    "Active Overrides:",
                    *(override_lines or ("No active overrides.",)),
                ]
            )
        )
        warning_lines = [*telemetry.warnings]
        if telemetry.last_error:
            warning_lines.insert(0, telemetry.last_error)
        diagnostics_panel.warnings_box.setPlainText("\n".join(warning_lines) or "No warnings.")

    def _selected_queue_index() -> int | None:
        row = queue_panel.local_list.currentRow()
        return row if row >= 0 else None

    def _rebind_local_queue(*, status: str | None = None) -> None:
        nonlocal assignments
        assignments = song_palette_store.load()
        session = _active_local_session()
        runtime_state = runtime_supervisor.snapshot()
        if session is not None and hasattr(session, "queue_snapshot"):
            queue_controller.bind_local_session(session, assignments=assignments)
            if hasattr(session, "session_snapshot"):
                _render_session_status(session.session_snapshot(), running=True)
        elif session is not None and hasattr(session, "session_snapshot"):
            _render_session_status(session.session_snapshot(), running=True)
        elif _pending_preview_snapshot(runtime_state) is not None:
            _render_session_status(_pending_preview_snapshot(runtime_state), running=True)
        elif playlist_state["playlist"] is not None:
            queue_controller.bind_playlist(
                playlist_state["playlist"],
                assignments=assignments,
                source_path=queue_controller.state.playlist_source_path,
            )
            playlist_snapshot = {
                "current_track": playlist_state["playlist"].current,
                "playback_state": "ready",
                "position_seconds": 0.0,
                "duration_seconds": 0.0,
                "audio_output": "system default",
                "device_status": "preview mode (no connected devices)",
            }
            _render_session_status(playlist_snapshot, running=False)
        else:
            _render_session_status(None, running=False)
        if status is not None:
            queue_controller.set_status(status)
        _render_queue_state(queue_controller.state)
        _render_runtime_state(runtime_state)
        _render_diagnostics()

    def _move_queue_item(from_index: int, to_index: int) -> None:  # pragma: no cover - Qt only
        session = _active_local_session()
        playlist = playlist_state["playlist"]
        if session is None and playlist is None:
            queue_controller.set_status("Load a queue before reordering.")
            _render_queue_state(queue_controller.state)
            return
        try:
            if session is not None and hasattr(session, "queue_snapshot"):
                queue_controller.move_local(session, from_index, to_index)
            else:
                queue_controller.move_playlist(playlist, from_index, to_index)
            queue_controller.set_status("Queue order updated.")
        except Exception as exc:
            queue_controller.set_status(str(exc))
        _render_queue_state(queue_controller.state)

    def _play_selected_queue_item() -> None:  # pragma: no cover - Qt only
        index = _selected_queue_index()
        session = _active_local_session()
        playlist = playlist_state["playlist"]
        if index is None:
            queue_controller.set_status("Select a song in the local queue first.")
        elif session is not None and hasattr(session, "queue_snapshot"):
            try:
                queue_controller.play_local_now(session, index)
                queue_controller.set_status("Selected song queued to play immediately.")
            except Exception as exc:
                queue_controller.set_status(str(exc))
        elif playlist is not None:
            try:
                queue_controller.play_playlist_now(playlist, index)
                queue_controller.set_status("Selected song is now first up for preview playback.")
            except Exception as exc:
                queue_controller.set_status(str(exc))
        else:
            queue_controller.set_status("Load a queue before using Play Now.")
        _render_queue_state(queue_controller.state)

    def _remove_selected_queue_item() -> None:  # pragma: no cover - Qt only
        index = _selected_queue_index()
        session = _active_local_session()
        playlist = playlist_state["playlist"]
        if index is None:
            queue_controller.set_status("Select a song in the local queue first.")
        elif session is not None and hasattr(session, "queue_snapshot"):
            try:
                queue_controller.remove_local(session, index)
                queue_controller.set_status("Selected song removed from the queue.")
            except Exception as exc:
                queue_controller.set_status(str(exc))
        elif playlist is not None:
            try:
                queue_controller.remove_playlist(playlist, index)
                queue_controller.set_status("Selected song removed from the queue.")
            except Exception as exc:
                queue_controller.set_status(str(exc))
        else:
            queue_controller.set_status("Load a queue before removing songs.")
        _render_queue_state(queue_controller.state)

    def _shuffle_upcoming_queue() -> None:  # pragma: no cover - Qt only
        session = _active_local_session()
        playlist = playlist_state["playlist"]
        try:
            if session is not None and hasattr(session, "queue_snapshot"):
                queue_controller.shuffle_local(session)
            elif playlist is not None:
                queue_controller.shuffle_playlist(playlist)
            else:
                queue_controller.set_status("Load a queue before shuffling.")
                _render_queue_state(queue_controller.state)
                return
            queue_controller.set_status("Upcoming queue shuffled.")
        except Exception as exc:
            queue_controller.set_status(str(exc))
        _render_queue_state(queue_controller.state)

    def _apply_palette_choices(
        choices: list[dict[str, object]],
        *,
        status: str,
    ) -> None:
        palette_choices[:] = list(choices)
        queue_panel.palette_combo.blockSignals(True)
        queue_panel.palette_combo.clear()
        for choice in palette_choices:
            label = f"{choice['profile_name']} / {choice['palette_name']}"
            queue_panel.palette_combo.addItem(label)
        queue_panel.palette_combo.blockSignals(False)
        if palette_choices:
            first = palette_choices[0]
            queue_controller.set_preview_palette(
                source_label=str(first["profile_name"]),
                palette_name=str(first["palette_name"]),
                colors=tuple(str(color) for color in first["colors"]),
                source_path=str(first.get("profile_path", "")),
                generated_seed=(
                    int(first["generated_seed"])
                    if first.get("generated_seed") is not None
                    else None
                ),
            )
        else:
            queue_controller.set_preview_palette(
                source_label="",
                palette_name="",
                colors=(),
                source_path="",
                generated_seed=None,
            )
        queue_controller.set_status(status)
        _render_queue_state(queue_controller.state)

    def _load_playlist(path: Path) -> None:
        try:
            playlist = queue_service.build_playlist(path)
        except Exception as exc:  # pragma: no cover - Qt only
            queue_controller.set_status(f"Could not load queue: {exc}")
            _render_queue_state(queue_controller.state)
            return
        playlist_state["playlist"] = playlist
        queue_controller.bind_playlist(playlist, assignments=assignments, source_path=path)
        if queue_controller.state.local_tracks:
            queue_controller.set_status("Queue loaded. Select a song, then load or generate a palette.")
        else:
            queue_controller.set_status("Queue loaded, but no audio files were found.")
        _render_queue_state(queue_controller.state)
        _render_runtime_state(runtime_supervisor.snapshot())

    def _current_base_profile():
        current_profile_path = active_profile_ref["path"]
        if current_profile_path and current_profile_path.exists():
            return profile_service.load_profile(current_profile_path)
        return None

    def _current_runtime_palette_override() -> tuple[str, ...]:
        return tuple(
            part.strip()
            for part in queue_panel.runtime_palette_override_edit.text().split(",")
            if part.strip()
        )

    def _current_runtime_muted_values(text: str) -> tuple[str, ...]:
        return tuple(part.strip() for part in text.split(",") if part.strip())

    def _selected_saved_show_audio_path() -> Path | None:
        selected = queue_controller.state.selected_track
        if selected is not None:
            return Path(selected.path)
        editor_audio_path = show_editor_state["audio_path"]
        if isinstance(editor_audio_path, Path):
            return editor_audio_path
        source_path = queue_controller.state.playlist_source_path
        if source_path:
            source = Path(source_path)
            if source.is_file():
                return source
        if len(queue_controller.state.local_tracks) == 1:
            return Path(queue_controller.state.local_tracks[0].path)
        return None

    def _profile_resolver(audio_path: Path, base_profile):
        assignment = song_palette_store.assignment_for_path(audio_path)
        return derive_profile_with_palette_assignment(base_profile, assignment)

    def _timeline_resolver(audio_path: Path, timeline):
        patch = show_patch_controller.patch_for_path(audio_path)
        if patch is None:
            return timeline
        return apply_show_control_patch(timeline, patch)

    runtime_supervisor.set_timeline_resolver(_timeline_resolver)

    def _effects_from_table() -> list[dict[str, object]]:
        return [
            {
                "name": str(_row_value(profile_panel.effects_table, row, 0)),
                "weight": float(_row_value(profile_panel.effects_table, row, 1)),
            }
            for row in range(profile_panel.effects_table.rowCount())
            if str(_row_value(profile_panel.effects_table, row, 0))
        ]

    def _params_from_table() -> dict[str, object]:
        params: dict[str, object] = {}
        for row in range(profile_panel.params_table.rowCount()):
            key = str(_row_value(profile_panel.params_table, row, 0))
            if key:
                params[key] = float(_row_value(profile_panel.params_table, row, 1))
        return params

    def _eq_routes_from_table(table: object) -> list[dict[str, object]]:
        routes: list[dict[str, object]] = []
        for row in range(table.rowCount()):
            band = str(_row_value(table, row, 0))
            when = str(_row_value(table, row, 1))
            if not band or not when:
                continue
            route: dict[str, object] = {"band": band, "when": when}
            render_mode = str(_row_value(table, row, 2))
            color_bias = str(_row_value(table, row, 3))
            intensity_boost = float(_row_value(table, row, 4))
            spatial_preset = str(_row_value(table, row, 5))
            if render_mode:
                route["render_mode"] = render_mode
            if color_bias and color_bias != "#ffffff":
                route["color_bias"] = color_bias
            if abs(intensity_boost) > 1e-9:
                route["intensity_boost"] = intensity_boost
            if spatial_preset:
                route["spatial_preset"] = spatial_preset
            routes.append(route)
        return routes

    def _instrument_routes_from_table(table: object) -> list[dict[str, object]]:
        routes: list[dict[str, object]] = []
        for row in range(table.rowCount()):
            instrument = str(_row_value(table, row, 0))
            when = str(_row_value(table, row, 1))
            if not instrument or not when:
                continue
            route: dict[str, object] = {"instrument": instrument, "when": when}
            render_mode = str(_row_value(table, row, 2))
            color_bias = str(_row_value(table, row, 3))
            intensity_boost = float(_row_value(table, row, 4))
            spatial_preset = str(_row_value(table, row, 5))
            pan_follow = float(_row_value(table, row, 6))
            width_scale = float(_row_value(table, row, 7))
            confidence_min = float(_row_value(table, row, 8))
            if render_mode:
                route["render_mode"] = render_mode
            if color_bias and color_bias != "#ffffff":
                route["color_bias"] = color_bias
            if abs(intensity_boost) > 1e-9:
                route["intensity_boost"] = intensity_boost
            if spatial_preset:
                route["spatial_preset"] = spatial_preset
            if abs(pan_follow) > 1e-9:
                route["pan_follow"] = pan_follow
            if abs(width_scale - 1.0) > 1e-9:
                route["width_scale"] = width_scale
            if abs(confidence_min - 0.45) > 1e-9:
                route["confidence_min"] = confidence_min
            routes.append(route)
        return routes

    def _transitions_from_table() -> list[dict[str, object]]:
        transitions: list[dict[str, object]] = []
        table = profile_panel.transitions_table
        for row in range(table.rowCount()):
            from_mood = str(_row_value(table, row, 0))
            to_mood = str(_row_value(table, row, 1))
            palette_name = str(_row_value(table, row, 2))
            if from_mood and to_mood and palette_name:
                transitions.append({"from": from_mood, "to": to_mood, "palette": palette_name})
        return transitions

    def _sync_profile_editor_structured_data() -> None:
        palette_controller.set_mood_effects_text(json.dumps(_effects_from_table()))
        palette_controller.set_mood_params_text(json.dumps(_params_from_table()))
        palette_controller.set_profile_eq_routes_text(json.dumps(_eq_routes_from_table(profile_panel.profile_eq_routes_table)))
        palette_controller.set_mood_eq_routes_text(json.dumps(_eq_routes_from_table(profile_panel.mood_eq_routes_table)))
        palette_controller.set_profile_instrument_routes_text(
            json.dumps(_instrument_routes_from_table(profile_panel.profile_instrument_routes_table))
        )
        palette_controller.set_mood_instrument_routes_text(
            json.dumps(_instrument_routes_from_table(profile_panel.mood_instrument_routes_table))
        )
        palette_controller.set_transitions_text(json.dumps(_transitions_from_table()))

    def _on_profile_palette_selected() -> None:  # pragma: no cover - Qt only
        palette_name = str(profile_panel.palette_combo.currentData() or "")
        if not palette_name:
            return
        _render_profile_state(palette_controller.select_palette(palette_name))

    def _on_profile_mood_selected() -> None:  # pragma: no cover - Qt only
        mood = str(profile_panel.mood_combo.currentData() or "")
        if not mood:
            return
        _render_profile_state(palette_controller.select_mood(mood))

    def _on_profile_palette_color_clicked(index: int) -> None:  # pragma: no cover - Qt only
        colors = palette_controller.state.palettes.get(palette_controller.state.selected_palette, ())
        if index >= len(colors):
            return
        initial = QtWidgets.QColorDialog().currentColor()
        initial.setNamedColor(colors[index])
        selected = QtWidgets.QColorDialog.getColor(initial, window, f"Choose palette color {index + 1}")
        if not selected.isValid():
            return
        state = palette_controller.update_hex(
            palette_controller.state.selected_palette,
            index,
            selected.name(),
        )
        _render_profile_state(state)

    def _on_seed_color_clicked(index: int) -> None:  # pragma: no cover - Qt only
        button = profile_panel.seed_color_buttons[index]
        current_hex = str(button.property("hexColor") or "#ffffff")
        initial = QtWidgets.QColorDialog().currentColor()
        initial.setNamedColor(current_hex)
        selected = QtWidgets.QColorDialog.getColor(initial, window, f"Choose seed color {index + 1}")
        if not selected.isValid():
            return
        default_label = "Seed 1" if index == 0 else "Optional Seed 2"
        _set_optional_color_button(button, selected.name(), default_label=default_label)

    def _generate_seed_palette() -> None:  # pragma: no cover - Qt only
        try:
            colors = _current_seed_colors()
            scheme = str(profile_panel.seed_scheme_combo.currentData() or "gradient")
            generated = profile_service.generate_palette_from_seed_colors(colors, scheme=scheme)
            state = palette_controller.set_palette_colors(generated)
        except Exception as exc:
            profile_panel.status_label.setText(str(exc))
            return
        _render_profile_state(state)

    def _load_profile_editor_file() -> None:  # pragma: no cover - Qt only
        selected, _filter = QtWidgets.QFileDialog.getOpenFileName(
            window,
            "Load Profile",
            str(Path(active_profile_ref["path"]).parent if active_profile_ref["path"] else Path.cwd()),
            "YAML Files (*.yaml *.yml);;All Files (*.*)",
        )
        if not selected:
            return
        try:
            _load_profile_into_editor(Path(selected), status="Loaded profile.")
        except Exception as exc:
            profile_panel.status_label.setText(str(exc))

    def _generate_quickshow_profile() -> None:  # pragma: no cover - Qt only
        try:
            seed_colors = _current_seed_colors()
            scheme = str(profile_panel.seed_scheme_combo.currentData() or "gradient")
            energy_modifier = float(profile_panel.quickshow_energy_spin.value())
            provided_name = profile_panel.quickshow_name_edit.text().strip()
            base_name = provided_name or "Quickshow"
            path = profile_service.save_generated_quickshow_profile(
                _quickshow_directory(),
                name=base_name,
                seed_colors=seed_colors,
                scheme=scheme,
                energy_modifier=energy_modifier,
            )
            _load_profile_into_editor(
                path,
                status=f"Generated quickshow '{path.stem}'.",
            )
        except Exception as exc:
            profile_panel.status_label.setText(str(exc))

    def _add_profile_palette() -> None:  # pragma: no cover - Qt only
        _render_profile_state(palette_controller.create_palette())

    def _add_profile_palette_color() -> None:  # pragma: no cover - Qt only
        try:
            state = palette_controller.append_palette_color()
        except Exception as exc:
            profile_panel.status_label.setText(str(exc))
            return
        _render_profile_state(state)

    def _remove_profile_palette_color() -> None:  # pragma: no cover - Qt only
        try:
            state = palette_controller.remove_palette_color()
        except Exception as exc:
            profile_panel.status_label.setText(str(exc))
            return
        _render_profile_state(state)

    def _save_profile_palette() -> None:  # pragma: no cover - Qt only
        try:
            state = palette_controller.save()
        except Exception as exc:
            profile_panel.status_label.setText(str(exc))
            return
        _render_profile_state(state)

    def _save_profile_sections() -> None:  # pragma: no cover - Qt only
        try:
            _sync_profile_editor_structured_data()
            state = palette_controller.save_sections()
        except Exception as exc:
            profile_panel.status_label.setText(str(exc))
            return
        _render_profile_state(state)

    def _save_profile_all() -> None:  # pragma: no cover - Qt only
        try:
            _sync_profile_editor_structured_data()
            state = palette_controller.save_all()
        except Exception as exc:
            profile_panel.status_label.setText(str(exc))
            return
        _render_profile_state(state)

    def _remove_selected_table_row(table: object) -> None:
        row = table.currentRow()
        if row >= 0:
            table.removeRow(row)

    def _add_effect_row() -> None:  # pragma: no cover - Qt only
        table = profile_panel.effects_table
        row = table.rowCount()
        table.insertRow(row)
        table.setCellWidget(row, 0, _combo_widget(PROFILE_EFFECT_OPTIONS, "warm_glow", allow_blank=False))
        table.setCellWidget(row, 1, _double_spin(1.0, minimum=0.0, maximum=10.0, step=0.1))

    def _add_param_row() -> None:  # pragma: no cover - Qt only
        table = profile_panel.params_table
        row = table.rowCount()
        table.insertRow(row)
        table.setCellWidget(row, 0, _combo_widget(PROFILE_PARAM_OPTIONS, PROFILE_PARAM_OPTIONS[0], allow_blank=False))
        table.setCellWidget(row, 1, _double_spin(1.0, minimum=-1000.0, maximum=1000.0, step=0.05, decimals=3))

    def _add_eq_route_row(table: object, *, band: str = "bass", when: str = "dominant") -> None:  # pragma: no cover - Qt only
        row = table.rowCount()
        table.insertRow(row)
        table.setCellWidget(row, 0, _combo_widget(PROFILE_EQ_BAND_OPTIONS, band, allow_blank=False))
        table.setCellWidget(row, 1, _combo_widget(PROFILE_EQ_WHEN_OPTIONS, when, allow_blank=False))
        table.setCellWidget(row, 2, _combo_widget(tuple(sorted(VALID_RENDER_MODES)), ""))
        table.setCellWidget(row, 3, _color_button_widget("#ffffff", label="Pick"))
        table.setCellWidget(row, 4, _double_spin(0.0, minimum=-1.0, maximum=2.0, step=0.05))
        table.setCellWidget(row, 5, _combo_widget(PROFILE_SPATIAL_PRESET_OPTIONS, "", blank_label="None"))

    def _add_instrument_route_row(
        table: object,
        *,
        instrument: str = "vocals",
        when: str = "dominant",
    ) -> None:  # pragma: no cover - Qt only
        row = table.rowCount()
        table.insertRow(row)
        table.setCellWidget(row, 0, _combo_widget(PROFILE_INSTRUMENT_OPTIONS, instrument, allow_blank=False))
        table.setCellWidget(row, 1, _combo_widget(PROFILE_INSTRUMENT_WHEN_OPTIONS, when, allow_blank=False))
        table.setCellWidget(row, 2, _combo_widget(tuple(sorted(VALID_RENDER_MODES)), ""))
        table.setCellWidget(row, 3, _color_button_widget("#ffffff", label="Pick"))
        table.setCellWidget(row, 4, _double_spin(0.0, minimum=-1.0, maximum=2.0, step=0.05))
        table.setCellWidget(row, 5, _combo_widget(PROFILE_SPATIAL_PRESET_OPTIONS, "", blank_label="None"))
        table.setCellWidget(row, 6, _double_spin(0.0, minimum=0.0, maximum=1.0, step=0.05))
        table.setCellWidget(row, 7, _double_spin(1.0, minimum=0.1, maximum=4.0, step=0.05))
        table.setCellWidget(row, 8, _double_spin(0.45, minimum=0.0, maximum=1.0, step=0.05))

    def _add_transition_row() -> None:  # pragma: no cover - Qt only
        table = profile_panel.transitions_table
        row = table.rowCount()
        table.insertRow(row)
        mood_options = tuple(sorted(VALID_MOODS))
        palette_options = _palette_choices_for_transitions(palette_controller.state)
        current_mood = palette_controller.state.selected_mood or mood_options[0]
        table.setCellWidget(row, 0, _combo_widget(mood_options, current_mood, allow_blank=False))
        table.setCellWidget(row, 1, _combo_widget(mood_options, "groove", allow_blank=False))
        table.setCellWidget(
            row,
            2,
            _combo_widget(palette_options, palette_controller.state.selected_palette or palette_options[0], allow_blank=False),
        )

    def _save_show_patch() -> None:  # pragma: no cover - Qt only
        try:
            show_patch_controller.set_patch_name(queue_panel.patch_name_edit.text())
            show_patch_controller.set_patch_rules_text(queue_panel.patch_rules_edit.toPlainText())
            show_patch_controller.save_selected_patch()
        except Exception as exc:
            queue_panel.patch_summary_label.setText(str(exc))
            return
        _render_show_patch_state()
        queue_controller.set_status(
            "Show override saved. It will apply on preview/playback for this song."
        )
        _render_queue_state(queue_controller.state)

    def _clear_show_patch() -> None:  # pragma: no cover - Qt only
        try:
            show_patch_controller.clear_selected_patch()
        except Exception as exc:
            queue_panel.patch_summary_label.setText(str(exc))
            return
        _render_show_patch_state()
        queue_controller.set_status("Show override cleared for selected song.")
        _render_queue_state(queue_controller.state)

    def _apply_runtime_control() -> None:  # pragma: no cover - Qt only
        palette_override = _current_runtime_palette_override()
        spatial_width_value = float(queue_panel.runtime_spatial_width_spin.value())
        result = runtime_supervisor.update_runtime_control(
            palette_override=palette_override,
            color_bias=queue_panel.runtime_color_bias_edit.text().strip(),
            render_mode=str(queue_panel.runtime_render_mode_combo.currentData() or ""),
            intensity_multiplier=float(queue_panel.runtime_intensity_multiplier_spin.value()),
            intensity_offset=float(queue_panel.runtime_intensity_offset_spin.value()),
            speed_multiplier=float(queue_panel.runtime_speed_multiplier_spin.value()),
            speed_offset=float(queue_panel.runtime_speed_offset_spin.value()),
            muted_bands=_current_runtime_muted_values(queue_panel.runtime_muted_bands_edit.text()),
            muted_instruments=_current_runtime_muted_values(
                queue_panel.runtime_muted_instruments_edit.text()
            ),
            spatial_preset=queue_panel.runtime_spatial_preset_edit.text().strip(),
            spatial_width=(spatial_width_value if spatial_width_value > 0.0 else None),
        )
        if result:
            queue_panel.runtime_control_status_label.setText(
                f"Live overrides applied. Revision {result.get('revision', 0)}."
            )
        else:
            queue_panel.runtime_control_status_label.setText(
                "No active session is available for live overrides."
            )
        _render_runtime_state(runtime_supervisor.snapshot())

    def _clear_runtime_control() -> None:  # pragma: no cover - Qt only
        result = runtime_supervisor.clear_runtime_control()
        if result:
            queue_panel.runtime_control_status_label.setText(
                f"Live overrides cleared. Revision {result.get('revision', 0)}."
            )
        else:
            queue_panel.runtime_control_status_label.setText(
                "No active session is available for live overrides."
            )
        _render_runtime_state(runtime_supervisor.snapshot())

    def _start_local_preview() -> None:  # pragma: no cover - Qt only
        source_path = queue_controller.state.playlist_source_path
        if not source_path:
            queue_controller.set_status("Load a local file or folder before starting preview.")
            _render_queue_state(queue_controller.state)
            return
        base_profile = _current_base_profile()
        try:
            runtime_supervisor.start_local_playlist(
                Path(source_path),
                config_path=config_path,
                profile=base_profile,
                profile_resolver=_profile_resolver,
            )
        except Exception as exc:
            _render_session_status(None, running=False, error=exc)
            queue_controller.set_status(str(exc))
            _render_queue_state(queue_controller.state)
            return
        runtime_state = runtime_supervisor.snapshot()
        pending_snapshot = _pending_preview_snapshot(runtime_state)
        _render_session_status(pending_snapshot, running=True)
        queue_controller.set_status(f"Local preview started for {Path(source_path).name}.")
        _render_preview_simulation({"node_colors": {}})
        _render_queue_state(queue_controller.state)
        _render_runtime_state(runtime_state)
        queue_timer.start()

    def _stop_local_preview() -> None:  # pragma: no cover - Qt only
        runtime_supervisor.stop_output_only()
        queue_controller.set_status("Stop requested.")
        _render_queue_state(queue_controller.state)
        _render_runtime_state(runtime_supervisor.snapshot())

    def _load_timeline_into_editor(
        timeline: ShowTimeline,
        *,
        audio_path: Path | None,
        show_path: Path | None,
        status: str,
        context=None,
    ) -> None:
        show_editor_state["timeline"] = timeline
        show_editor_state["audio_path"] = audio_path
        show_editor_state["path"] = show_path
        show_editor_state["notice"] = status
        show_editor_state["show_palette_dirty"] = False
        show_editor_state["show_palette_colors"] = _show_palette_colors_from_timeline(timeline)
        show_editor_state["context"] = (
            context
            if context is not None
            else show_service.build_timeline_context(audio_path=audio_path, timeline=timeline)
        )
        if show_path is not None:
            selected_show_path["value"] = show_path
            runtime_supervisor.select_saved_show(show_path)
        else:
            selected_show_path["value"] = None
            runtime_supervisor.select_saved_show(None)
        _render_show_editor()
        queue_controller.set_status(status)
        _render_queue_state(queue_controller.state)
        _render_runtime_state(runtime_supervisor.snapshot())

    def _compile_show_for_editor() -> None:  # pragma: no cover - Qt only
        audio_path = _selected_saved_show_audio_path()
        if audio_path is None:
            queue_controller.set_status("Select a local audio file before compiling a saved show.")
            _render_queue_state(queue_controller.state)
            return
        base_profile = _current_base_profile()
        profile = _profile_resolver(audio_path, base_profile) if base_profile is not None else None
        patch = show_patch_controller.patch_for_path(audio_path)
        try:
            structure, timeline, context = _run_show_editor_busy_task(
                f"Compiling editable show for {audio_path.name}...",
                lambda: (
                    lambda compiled: (
                        compiled[0],
                        compiled[1],
                        show_service.build_timeline_context(
                            audio_path=audio_path,
                            timeline=compiled[1],
                            structure=compiled[0],
                        ),
                    )
                )(
                    show_service.compile_with_analysis(
                        audio_path,
                        profile=profile,
                        patch=patch,
                    )
                ),
            )
        except Exception as exc:
            _set_show_editor_notice(f"Could not compile show: {exc}")
            queue_controller.set_status(f"Could not compile show: {exc}")
            _render_queue_state(queue_controller.state)
            return
        _load_timeline_into_editor(
            timeline,
            audio_path=audio_path,
            show_path=None,
            status=f"Compiled editable show for {audio_path.name}.",
            context=context,
        )

    def _load_show_file() -> None:  # pragma: no cover - Qt only
        selected, _filter = QtWidgets.QFileDialog.getOpenFileName(
            window,
            "Load Precompiled Show",
            str(Path.cwd()),
            "Show JSON Files (*.show.json *.json);;All Files (*.*)",
        )
        if not selected:
            return
        show_path = Path(selected)
        try:
            timeline, detected_audio_path = _run_show_editor_busy_task(
                f"Loading saved show {show_path.name}...",
                lambda: (
                    lambda loaded_timeline: (
                        loaded_timeline,
                        (
                            candidate
                            if str(loaded_timeline.song_path).strip()
                            and (candidate := Path(str(loaded_timeline.song_path))).exists()
                            else None
                        ),
                    )
                )(show_service.load_timeline(show_path)),
            )
        except Exception as exc:
            _set_show_editor_notice(f"Could not load show: {exc}")
            queue_controller.set_status(f"Could not load show: {exc}")
            _render_queue_state(queue_controller.state)
            return
        audio_path = _selected_saved_show_audio_path()
        if audio_path is None:
            audio_path = detected_audio_path
        try:
            context = _run_show_editor_busy_task(
                f"Preparing editor timeline for {show_path.name}...",
                lambda: show_service.build_timeline_context(audio_path=audio_path, timeline=timeline),
            )
        except Exception as exc:
            _set_show_editor_notice(f"Could not prepare show editor context: {exc}")
            queue_controller.set_status(f"Could not prepare show editor context: {exc}")
            _render_queue_state(queue_controller.state)
            return
        _load_timeline_into_editor(
            timeline,
            audio_path=audio_path,
            show_path=show_path,
            status=f"Saved show loaded: {show_path.name}",
            context=context,
        )

    def _clear_show_file() -> None:  # pragma: no cover - Qt only
        audio_path = _selected_saved_show_audio_path()
        _clear_show_editor_for_track(audio_path, notice=_track_compile_notice(audio_path))
        queue_controller.set_status("Saved show selection cleared.")
        _render_queue_state(queue_controller.state)
        _render_runtime_state(runtime_supervisor.snapshot())

    def _save_current_show() -> None:  # pragma: no cover - Qt only
        timeline = _timeline_from_editor()
        if timeline is None:
            queue_controller.set_status("Compile or load a saved show before saving.")
            _render_queue_state(queue_controller.state)
            return
        target = show_editor_state["path"]
        if target is None:
            suggested_audio = show_editor_state["audio_path"]
            default_path = (
                suggested_audio.with_suffix(".show.json")
                if isinstance(suggested_audio, Path)
                else (Path.cwd() / "untitled.show.json")
            )
            selected, _filter = QtWidgets.QFileDialog.getSaveFileName(
                window,
                "Save Editable Show",
                str(default_path),
                "Show JSON Files (*.show.json *.json);;All Files (*.*)",
            )
            if not selected:
                return
            target = Path(selected)
        try:
            saved_path = show_service.save_timeline(timeline, target)
        except Exception as exc:
            queue_controller.set_status(f"Could not save show: {exc}")
            _render_queue_state(queue_controller.state)
            return
        resolved_audio_path = show_editor_state["audio_path"]
        if not isinstance(resolved_audio_path, Path):
            resolved_audio_path = Path(timeline.song_path)
        _load_timeline_into_editor(
            timeline,
            audio_path=resolved_audio_path,
            show_path=saved_path,
            status=f"Saved show exported: {saved_path.name}",
        )

    def _add_show_cue() -> None:  # pragma: no cover - Qt only
        timeline = _safe_editor_timeline()
        if timeline is None:
            queue_controller.set_status("Compile or load a saved show before adding cues.")
            _render_queue_state(queue_controller.state)
            return
        next_time = 0.0
        if timeline.cues:
            next_time = min(float(timeline.duration), float(timeline.cues[-1].t) + 1.0)
        cue = ShowCue(
            t=next_time,
            render_mode="gradient",
            color_palette=("#ffffff", "#88ccff"),
            intensity=1.0,
            speed=1.0,
            params={},
            transition="cut",
            transition_beats=0,
            intensity_start=None,
        )
        row = queue_panel.show_cues_table.rowCount()
        queue_panel.show_cues_table.insertRow(row)
        _populate_show_cue_row(row, cue, timeline.beat_times, show_palette=_current_show_palette_colors())
        queue_panel.show_cues_table.selectRow(row)
        _render_show_timeline_view(playhead_seconds=_current_show_timeline_position())

    def _duplicate_show_cue() -> None:  # pragma: no cover - Qt only
        timeline = _timeline_from_editor()
        if timeline is None:
            queue_controller.set_status("Compile or load a saved show before duplicating cues.")
            _render_queue_state(queue_controller.state)
            return
        row = queue_panel.show_cues_table.currentRow()
        if row < 0 or row >= len(timeline.cues):
            queue_controller.set_status("Select a cue row to duplicate.")
            _render_queue_state(queue_controller.state)
            return
        source = timeline.cues[row]
        duplicate = ShowCue(
            t=min(float(timeline.duration), float(source.t) + 1.0),
            render_mode=source.render_mode,
            color_palette=tuple(source.color_palette),
            intensity=float(source.intensity),
            speed=float(source.speed),
            params=dict(source.params),
            transition=source.transition,
            transition_beats=int(source.transition_beats),
            intensity_start=source.intensity_start,
        )
        insert_row = row + 1
        queue_panel.show_cues_table.insertRow(insert_row)
        _populate_show_cue_row(
            insert_row,
            duplicate,
            timeline.beat_times,
            show_palette=_current_show_palette_colors(),
        )
        queue_panel.show_cues_table.selectRow(insert_row)
        _render_show_timeline_view(playhead_seconds=_current_show_timeline_position())

    def _remove_show_cue() -> None:  # pragma: no cover - Qt only
        row = queue_panel.show_cues_table.currentRow()
        if row < 0:
            queue_controller.set_status("Select a cue row to remove.")
            _render_queue_state(queue_controller.state)
            return
        queue_panel.show_cues_table.removeRow(row)
        _render_show_timeline_view(playhead_seconds=_current_show_timeline_position())

    def _select_show_cue_row(index: int) -> None:  # pragma: no cover - Qt only
        if 0 <= index < queue_panel.show_cues_table.rowCount():
            queue_panel.show_cues_table.selectRow(index)
            item = queue_panel.show_cues_table.item(index, 0)
            if item is not None:
                queue_panel.show_cues_table.scrollToItem(
                    item,
                    QtWidgets.QAbstractItemView.ScrollHint.PositionAtCenter,
                )
            _render_show_timeline_view(playhead_seconds=_current_show_timeline_position())

    def _selected_editor_cue_start() -> float:
        timeline = _safe_editor_timeline()
        row = queue_panel.show_cues_table.currentRow()
        if timeline is None or row < 0 or row >= len(timeline.cues):
            return 0.0
        return float(timeline.cues[row].t)

    def _start_saved_show(*, from_selected_cue: bool = False) -> None:  # pragma: no cover - Qt only
        timeline = _safe_editor_timeline()
        audio_path = _selected_saved_show_audio_path()
        if audio_path is None:
            queue_controller.set_status(
                "Select a single audio file in the local queue before starting saved-show playback."
            )
            _render_queue_state(queue_controller.state)
            return
        if timeline is None:
            queue_controller.set_status("Compile or load a show in the editor before starting playback.")
            _render_queue_state(queue_controller.state)
            return
        if from_selected_cue and queue_panel.show_cues_table.currentRow() < 0:
            queue_controller.set_status("Select a cue row before starting from a cue.")
            _render_queue_state(queue_controller.state)
            return
        start_seconds = _selected_editor_cue_start() if from_selected_cue else 0.0
        try:
            _sync_runtime_settings_from_form()
            runtime_supervisor.start_timeline_show(
                audio_path,
                timeline,
                show_path=show_editor_state["path"],
                config_path=config_path,
                start_seconds=start_seconds,
            )
        except Exception as exc:
            _render_session_status(None, running=False, error=exc)
            queue_controller.set_status(str(exc))
            _render_queue_state(queue_controller.state)
            return
        runtime_state = runtime_supervisor.snapshot()
        _render_session_status(_pending_preview_snapshot(runtime_state), running=True)
        if from_selected_cue and start_seconds > 0.0:
            queue_controller.set_status(
                f"Saved show started from {start_seconds:.2f}s for {audio_path.name}."
            )
        else:
            queue_controller.set_status(f"Saved show started for {audio_path.name}.")
        _render_queue_state(queue_controller.state)
        _render_runtime_state(runtime_state)
        _render_show_timeline_view(playhead_seconds=start_seconds)
        queue_timer.start()

    def _start_capture_pipeline() -> None:  # pragma: no cover - Qt only
        capture_settings, _reactive_settings = _sync_runtime_settings_from_form()
        validation_errors = capture_settings.validate()
        if validation_errors:
            queue_controller.set_status(validation_errors[0])
            _render_queue_state(queue_controller.state)
            return
        capture_dir = Path(capture_settings.capture_dir)
        try:
            runtime_supervisor.start_capture_pipeline(
                capture_dir=capture_dir,
                profile=_current_base_profile(),
                sample_rate=capture_settings.sample_rate,
                capture_naming=capture_settings.naming_mode,
                capture_buffer=capture_settings.max_capture_buffer,
                device_pattern=capture_settings.device_pattern,
                debug=capture_settings.debug_pipeline,
            )
        except Exception as exc:
            queue_controller.set_status(f"Could not start capture pipeline: {exc}")
            _render_queue_state(queue_controller.state)
            return
        queue_controller.set_status(f"Capture pipeline running in {capture_dir}.")
        _render_queue_state(queue_controller.state)
        _render_runtime_state(runtime_supervisor.snapshot())
        queue_timer.start()

    def _stop_capture_pipeline() -> None:  # pragma: no cover - Qt only
        runtime_supervisor.stop_capture_pipeline()
        queue_controller.set_status("Capture pipeline stopped.")
        _render_queue_state(queue_controller.state)
        _render_runtime_state(runtime_supervisor.snapshot())

    def _switch_to_pipeline_playback() -> None:  # pragma: no cover - Qt only
        started = runtime_supervisor.switch_to_pipeline_playback(config_path=config_path)
        if started:
            queue_controller.set_status("Pipeline playback switched in.")
        else:
            queue_controller.set_status("Pipeline playback armed and waiting for the next ready capture.")
        _render_queue_state(queue_controller.state)
        _render_runtime_state(runtime_supervisor.snapshot())
        queue_timer.start()

    def _start_reactive_output() -> None:  # pragma: no cover - Qt only
        _capture_settings, reactive_settings = _sync_runtime_settings_from_form()
        validation_errors = reactive_settings.validate()
        if validation_errors:
            queue_controller.set_status(validation_errors[0])
            _render_queue_state(queue_controller.state)
            return
        try:
            runtime_supervisor.start_reactive_live(
                config_path=config_path,
                profile=_current_base_profile(),
                effect_mode="reactive",
            )
        except Exception as exc:
            _render_session_status(None, running=False, error=exc)
            queue_controller.set_status(str(exc))
            _render_queue_state(queue_controller.state)
            return
        runtime_state = runtime_supervisor.snapshot()
        _render_session_status(_pending_preview_snapshot(runtime_state), running=True)
        queue_controller.set_status("Reactive output started.")
        _render_queue_state(queue_controller.state)
        _render_runtime_state(runtime_state)
        queue_timer.start()

    def _stop_output_runtime() -> None:  # pragma: no cover - Qt only
        runtime_supervisor.stop_output_only()
        queue_controller.set_status("Output stop requested.")
        _render_queue_state(queue_controller.state)
        _render_runtime_state(runtime_supervisor.snapshot())

    def _load_queue_file() -> None:  # pragma: no cover - Qt only
        selected, _filter = QtWidgets.QFileDialog.getOpenFileName(
            window,
            "Load Local Queue File",
            str(Path.cwd()),
            "Audio or Playlist Files (*.mp3 *.wav *.flac *.ogg *.aac *.m3u *.m3u8);;All Files (*.*)",
        )
        if selected:
            _load_playlist(Path(selected))

    def _load_queue_folder() -> None:  # pragma: no cover - Qt only
        selected = QtWidgets.QFileDialog.getExistingDirectory(
            window,
            "Load Local Queue Folder",
            str(Path.cwd()),
        )
        if selected:
            _load_playlist(Path(selected))

    def _refresh_queue() -> None:  # pragma: no cover - Qt only
        _rebind_local_queue(status="Queue refreshed.")

    def _selected_ready_item_id() -> str | None:
        item = queue_panel.ready_list.currentItem()
        if item is None:
            return None
        item_id = item.data(QtCore.Qt.ItemDataRole.UserRole)
        return str(item_id) if item_id else None

    def _preview_ready_item() -> None:  # pragma: no cover - Qt only
        item_id = _selected_ready_item_id()
        if not item_id:
            queue_controller.set_status("Select a captured ready show first.")
            _render_queue_state(queue_controller.state)
            return
        try:
            runtime_supervisor.preview_captured_show(item_id, config_path=config_path)
        except Exception as exc:
            queue_controller.set_status(str(exc))
            _render_queue_state(queue_controller.state)
            return
        queue_controller.set_status("Simulation preview started for the selected captured show.")
        _render_queue_state(queue_controller.state)
        _render_runtime_state(runtime_supervisor.snapshot())
        queue_timer.start()

    def _play_ready_item_now() -> None:  # pragma: no cover - Qt only
        item_id = _selected_ready_item_id()
        if not item_id:
            queue_controller.set_status("Select a captured ready show first.")
            _render_queue_state(queue_controller.state)
            return
        runtime_supervisor.prioritize_captured_show(item_id)
        started = runtime_supervisor.switch_to_pipeline_playback(config_path=config_path)
        if started:
            queue_controller.set_status("Selected captured show is now driving pipeline playback.")
        else:
            queue_controller.set_status("Selected captured show moved to the top; pipeline playback is armed.")
        _render_queue_state(queue_controller.state)
        _render_runtime_state(runtime_supervisor.snapshot())
        queue_timer.start()

    def _prioritize_ready_item() -> None:  # pragma: no cover - Qt only
        item_id = _selected_ready_item_id()
        if not item_id:
            queue_controller.set_status("Select a captured ready show first.")
            _render_queue_state(queue_controller.state)
            return
        runtime_supervisor.prioritize_captured_show(item_id)
        queue_controller.set_status("Captured show moved to the top of the ready queue.")
        _render_queue_state(queue_controller.state)
        _render_runtime_state(runtime_supervisor.snapshot())

    def _discard_ready_item() -> None:  # pragma: no cover - Qt only
        item_id = _selected_ready_item_id()
        if not item_id:
            queue_controller.set_status("Select a captured ready show first.")
            _render_queue_state(queue_controller.state)
            return
        runtime_supervisor.discard_captured_show(item_id)
        queue_controller.set_status("Captured show discarded.")
        _render_queue_state(queue_controller.state)
        _render_runtime_state(runtime_supervisor.snapshot())

    def _on_queue_selection_changed() -> None:  # pragma: no cover - Qt only
        item = queue_panel.local_list.currentItem()
        if item is None:
            return
        track_key = item.data(QtCore.Qt.ItemDataRole.UserRole)
        queue_controller.select_local_track(str(track_key))
        _render_queue_state(queue_controller.state)
        _sync_show_editor_for_selected_track()

    def _on_recent_saved_selected() -> None:  # pragma: no cover - Qt only
        item = queue_panel.recent_saved_list.currentItem()
        if item is None:
            return
        raw_path = item.data(QtCore.Qt.ItemDataRole.UserRole)
        if not raw_path:
            return
        show_path = Path(str(raw_path))
        try:
            timeline = show_service.load_timeline(show_path)
        except Exception as exc:
            queue_controller.set_status(f"Could not load saved show: {exc}")
            _render_queue_state(queue_controller.state)
            return
        audio_path = _selected_saved_show_audio_path()
        if audio_path is None and str(timeline.song_path).strip():
            candidate = Path(str(timeline.song_path))
            if candidate.exists():
                audio_path = candidate
        _load_timeline_into_editor(
            timeline,
            audio_path=audio_path,
            show_path=show_path,
            status=f"Saved show selected: {show_path.name}",
        )

    def _on_output_target_changed() -> None:  # pragma: no cover - Qt only
        runtime_supervisor.set_output_target_mode(str(queue_panel.output_target_combo.currentData() or "simulation"))
        _render_runtime_state(runtime_supervisor.snapshot())

    def _on_hardware_fallback_changed() -> None:  # pragma: no cover - Qt only
        runtime_supervisor.set_hardware_fallback_to_simulation(bool(queue_panel.hardware_fallback_check.isChecked()))
        _render_runtime_state(runtime_supervisor.snapshot())

    def _on_output_device_changed() -> None:  # pragma: no cover - Qt only
        runtime_supervisor.set_selected_output_audio_device(queue_panel.output_device_combo.currentData())
        _render_runtime_state(runtime_supervisor.snapshot())

    def _on_input_device_changed() -> None:  # pragma: no cover - Qt only
        runtime_supervisor.set_selected_live_input_device(queue_panel.input_device_combo.currentData())
        _render_runtime_state(runtime_supervisor.snapshot())

    def _on_runtime_settings_changed() -> None:  # pragma: no cover - Qt only
        _sync_runtime_settings_from_form()
        _render_runtime_state(runtime_supervisor.snapshot())

    def _load_active_profile_palettes() -> None:  # pragma: no cover - Qt only
        current_profile_path = active_profile_ref["path"]
        if not current_profile_path or not current_profile_path.exists():
            queue_controller.set_status("No active profile is loaded for this GUI session.")
            _render_queue_state(queue_controller.state)
            return
        choices = profile_service.load_palette_choices(current_profile_path)
        _apply_palette_choices(choices, status=f"Loaded palettes from active profile: {current_profile_path.name}")

    def _load_profile_file_palettes() -> None:  # pragma: no cover - Qt only
        selected, _filter = QtWidgets.QFileDialog.getOpenFileName(
            window,
            "Load Profile File",
            str(Path(active_profile_ref["path"]).parent if active_profile_ref["path"] else Path.cwd()),
            "YAML Files (*.yaml *.yml);;All Files (*.*)",
        )
        if not selected:
            return
        path = Path(selected)
        choices = profile_service.load_palette_choices(path)
        _apply_palette_choices(choices, status=f"Loaded palettes from profile file: {path.name}")

    def _on_palette_choice_changed(index: int) -> None:  # pragma: no cover - Qt only
        if index < 0 or index >= len(palette_choices):
            return
        choice = palette_choices[index]
        queue_controller.set_preview_palette(
            source_label=str(choice["profile_name"]),
            palette_name=str(choice["palette_name"]),
            colors=tuple(str(color) for color in choice["colors"]),
            source_path=str(choice.get("profile_path", "")),
            generated_seed=(
                int(choice["generated_seed"])
                if choice.get("generated_seed") is not None
                else None
            ),
        )
        queue_controller.set_status("Palette preview updated.")
        _render_queue_state(queue_controller.state)

    def _generate_palette() -> None:  # pragma: no cover - Qt only
        seed_text = queue_panel.seed_edit.text().strip()
        try:
            seed = int(seed_text) if seed_text else None
        except ValueError:
            queue_controller.set_status("Seed must be an integer.")
            _render_queue_state(queue_controller.state)
            return
        choice = profile_service.generate_palette_choice(seed=seed)
        _apply_palette_choices([choice], status="Generated a new palette preview.")

    def _assign_palette_to_song() -> None:  # pragma: no cover - Qt only
        selected = queue_controller.state.selected_track
        if selected is None:
            queue_controller.set_status("Select a song in the local queue first.")
            _render_queue_state(queue_controller.state)
            return
        if not queue_controller.state.preview_colors:
            queue_controller.set_status("Load or generate a palette preview first.")
            _render_queue_state(queue_controller.state)
            return
        assignment = SongPaletteAssignment(
            track_key=selected.track_key,
            palette_name=queue_controller.state.preview_palette_name,
            colors=queue_controller.state.preview_colors,
            source_label=queue_controller.state.preview_source_label,
            source_profile_path=queue_controller.state.preview_source_path,
            generated_seed=queue_controller.state.preview_generated_seed,
        )
        song_palette_store.upsert(assignment)
        status = "Palette assigned to song."
        session = runtime_supervisor.active_session()
        if session is not None and selected.is_current:
            status = "Palette assigned. The current song will use it on replay or next compile."
        _rebind_local_queue(status=status)

    def _clear_assignment() -> None:  # pragma: no cover - Qt only
        selected = queue_controller.state.selected_track
        if selected is None:
            queue_controller.set_status("Select a song in the local queue first.")
            _render_queue_state(queue_controller.state)
            return
        song_palette_store.clear(selected.track_key)
        _rebind_local_queue(status="Palette assignment cleared for selected song.")

    queue_timer = QtCore.QTimer(window)
    queue_timer.setInterval(250)

    _initialize_show_columns_menu()
    _toggle_show_meta_panel(bool(show_editor_state.get("meta_hidden")))
    if show_editor_state.get("focus_sizes"):
        queue_panel.shows_splitter.setSizes(list(show_editor_state["focus_sizes"]))
    if bool(show_editor_state.get("focus_enabled")):
        _toggle_show_editor_focus_mode(True)
    else:
        _toggle_show_editor_focus_mode(False)

    def _poll_session_queue() -> None:  # pragma: no cover - Qt only
        runtime_state = runtime_supervisor.poll()
        session = runtime_supervisor.active_session()
        if runtime_state.error_message:
            _render_session_status(None, running=False, error=RuntimeError(runtime_state.error_message))
            queue_controller.set_status(runtime_state.error_message)
        elif session is not None and hasattr(session, "session_snapshot"):
            _render_session_status(
                session.session_snapshot(),
                running=runtime_state.active_output_mode != "idle",
            )
        elif _pending_preview_snapshot(runtime_state) is not None:
            _render_session_status(
                _pending_preview_snapshot(runtime_state),
                running=runtime_state.active_output_mode != "idle",
            )
        else:
            _render_session_status(None, running=False)
        preview_snapshot = runtime_supervisor.preview_frame_snapshot()
        if preview_snapshot is not None:
            _render_preview_simulation(preview_snapshot)
        _rebind_local_queue()
        _render_show_timeline_view(playhead_seconds=_current_show_timeline_position())
        if runtime_state.active_output_mode == "idle" and runtime_state.capture_state != "running":
            queue_timer.stop()

    queue_panel.start_preview_button.clicked.connect(_start_local_preview)
    queue_panel.play_saved_show_button.clicked.connect(_start_saved_show)
    queue_panel.play_selected_cue_button.clicked.connect(lambda: _start_saved_show(from_selected_cue=True))
    queue_panel.show_pause_button.clicked.connect(_toggle_show_output_pause)
    queue_panel.show_stop_button.clicked.connect(_stop_output_runtime)
    queue_panel.compile_show_button.clicked.connect(_compile_show_for_editor)
    queue_panel.save_show_button.clicked.connect(_save_current_show)
    queue_panel.start_capture_button.clicked.connect(_start_capture_pipeline)
    queue_panel.stop_capture_button.clicked.connect(_stop_capture_pipeline)
    queue_panel.switch_pipeline_button.clicked.connect(_switch_to_pipeline_playback)
    queue_panel.start_reactive_button.clicked.connect(_start_reactive_output)
    queue_panel.stop_output_button.clicked.connect(_stop_output_runtime)
    queue_panel.stop_preview_button.clicked.connect(_stop_local_preview)
    queue_panel.load_file_button.clicked.connect(_load_queue_file)
    queue_panel.load_folder_button.clicked.connect(_load_queue_folder)
    queue_panel.load_show_button.clicked.connect(_load_show_file)
    queue_panel.clear_show_button.clicked.connect(_clear_show_file)
    queue_panel.show_import_palette_button.clicked.connect(_import_profile_palette_into_show)
    queue_panel.add_show_cue_button.clicked.connect(_add_show_cue)
    queue_panel.duplicate_show_cue_button.clicked.connect(_duplicate_show_cue)
    queue_panel.remove_show_cue_button.clicked.connect(_remove_show_cue)
    queue_panel.show_cues_table.itemSelectionChanged.connect(
        lambda: _render_show_timeline_view(playhead_seconds=_current_show_timeline_position())
    )
    queue_panel.show_timeline_view.cueSelected.connect(_select_show_cue_row)
    queue_panel.show_timeline_view.beatMoved.connect(_move_beat_marker)
    queue_panel.show_timeline_view.downbeatMoved.connect(_move_downbeat_marker)
    queue_panel.show_timeline_view.sectionBoundaryMoved.connect(_move_section_boundary)
    queue_panel.show_timeline_view.zoomChanged.connect(_set_show_timeline_zoom_slider)
    queue_panel.show_timeline_view.viewWindowChanged.connect(_sync_show_timeline_scroll_bar)
    queue_panel.show_timeline_zoom_slider.valueChanged.connect(
        lambda value: queue_panel.show_timeline_view.set_zoom_factor(float(value) / 100.0)
    )
    queue_panel.show_timeline_fit_button.clicked.connect(queue_panel.show_timeline_view.fit_to_duration)
    queue_panel.show_timeline_scroll_bar.valueChanged.connect(_set_show_timeline_scroll_value)
    queue_panel.show_editor_focus_button.toggled.connect(_toggle_show_editor_focus_mode)
    queue_panel.show_meta_toggle_button.toggled.connect(_toggle_show_meta_panel)
    queue_panel.refresh_button.clicked.connect(_refresh_queue)
    queue_panel.play_now_button.clicked.connect(_play_selected_queue_item)
    queue_panel.remove_button.clicked.connect(_remove_selected_queue_item)
    queue_panel.shuffle_button.clicked.connect(_shuffle_upcoming_queue)
    queue_panel.ready_preview_button.clicked.connect(_preview_ready_item)
    queue_panel.ready_play_button.clicked.connect(_play_ready_item_now)
    queue_panel.ready_prioritize_button.clicked.connect(_prioritize_ready_item)
    queue_panel.ready_discard_button.clicked.connect(_discard_ready_item)
    queue_panel.recent_saved_list.itemSelectionChanged.connect(_on_recent_saved_selected)
    queue_panel.local_list.itemSelectionChanged.connect(_on_queue_selection_changed)
    queue_panel.local_list.reordered.connect(_move_queue_item)
    queue_panel.output_target_combo.currentIndexChanged.connect(lambda _index: _on_output_target_changed())
    queue_panel.hardware_fallback_check.toggled.connect(lambda _checked: _on_hardware_fallback_changed())
    queue_panel.output_device_combo.currentIndexChanged.connect(lambda _index: _on_output_device_changed())
    queue_panel.input_device_combo.currentIndexChanged.connect(lambda _index: _on_input_device_changed())
    queue_panel.capture_naming_combo.currentIndexChanged.connect(lambda _index: _on_runtime_settings_changed())
    queue_panel.capture_dir_edit.editingFinished.connect(_on_runtime_settings_changed)
    queue_panel.capture_buffer_spin.valueChanged.connect(lambda _value: _on_runtime_settings_changed())
    queue_panel.capture_device_pattern_edit.editingFinished.connect(_on_runtime_settings_changed)
    queue_panel.capture_sample_rate_spin.valueChanged.connect(lambda _value: _on_runtime_settings_changed())
    queue_panel.capture_channels_spin.valueChanged.connect(lambda _value: _on_runtime_settings_changed())
    queue_panel.capture_frame_size_spin.valueChanged.connect(lambda _value: _on_runtime_settings_changed())
    queue_panel.capture_hop_size_spin.valueChanged.connect(lambda _value: _on_runtime_settings_changed())
    queue_panel.capture_blocksize_spin.valueChanged.connect(lambda _value: _on_runtime_settings_changed())
    queue_panel.pipeline_playback_device_combo.currentIndexChanged.connect(lambda _index: _on_runtime_settings_changed())
    queue_panel.purge_after_playback_check.toggled.connect(lambda _checked: _on_runtime_settings_changed())
    queue_panel.debug_pipeline_check.toggled.connect(lambda _checked: _on_runtime_settings_changed())
    queue_panel.reactive_render_mode_combo.currentIndexChanged.connect(lambda _index: _on_runtime_settings_changed())
    queue_panel.reactive_sample_rate_spin.valueChanged.connect(lambda _value: _on_runtime_settings_changed())
    queue_panel.reactive_channels_spin.valueChanged.connect(lambda _value: _on_runtime_settings_changed())
    queue_panel.reactive_frame_size_spin.valueChanged.connect(lambda _value: _on_runtime_settings_changed())
    queue_panel.reactive_hop_size_spin.valueChanged.connect(lambda _value: _on_runtime_settings_changed())
    queue_panel.reactive_blocksize_spin.valueChanged.connect(lambda _value: _on_runtime_settings_changed())
    queue_panel.reactive_half_time_check.toggled.connect(lambda _checked: _on_runtime_settings_changed())
    queue_panel.reactive_max_brightness_check.toggled.connect(lambda _checked: _on_runtime_settings_changed())
    queue_panel.reactive_auto_cycle_check.toggled.connect(lambda _checked: _on_runtime_settings_changed())
    queue_panel.reactive_cycle_interval_spin.valueChanged.connect(lambda _value: _on_runtime_settings_changed())
    queue_panel.reactive_debug_mood_check.toggled.connect(lambda _checked: _on_runtime_settings_changed())
    queue_panel.reactive_crossfade_check.toggled.connect(lambda _checked: _on_runtime_settings_changed())
    queue_panel.reactive_telemetry_dir_edit.editingFinished.connect(_on_runtime_settings_changed)
    queue_panel.reactive_profile_strategy_combo.currentIndexChanged.connect(lambda _index: _on_runtime_settings_changed())
    queue_panel.reactive_profile_override_edit.editingFinished.connect(_on_runtime_settings_changed)
    queue_panel.reactive_rotation_profiles_edit.editingFinished.connect(_on_runtime_settings_changed)
    queue_panel.reactive_rotation_interval_spin.valueChanged.connect(lambda _value: _on_runtime_settings_changed())
    queue_panel.reactive_auto_palette_check.toggled.connect(lambda _checked: _on_runtime_settings_changed())
    queue_panel.reactive_smart_rotation_check.toggled.connect(lambda _checked: _on_runtime_settings_changed())
    queue_panel.reactive_chain_blend_spin.valueChanged.connect(lambda _value: _on_runtime_settings_changed())
    queue_panel.runtime_apply_button.clicked.connect(_apply_runtime_control)
    queue_panel.runtime_clear_button.clicked.connect(_clear_runtime_control)
    queue_panel.use_active_profile_button.clicked.connect(_load_active_profile_palettes)
    queue_panel.open_profile_button.clicked.connect(_load_profile_file_palettes)
    queue_panel.palette_combo.currentIndexChanged.connect(_on_palette_choice_changed)
    queue_panel.generate_button.clicked.connect(_generate_palette)
    queue_panel.assign_button.clicked.connect(_assign_palette_to_song)
    queue_panel.clear_button.clicked.connect(_clear_assignment)
    queue_panel.save_patch_button.clicked.connect(_save_show_patch)
    queue_panel.clear_patch_button.clicked.connect(_clear_show_patch)
    profile_panel.load_profile_button.clicked.connect(_load_profile_editor_file)
    profile_panel.palette_combo.currentIndexChanged.connect(lambda _index: _on_profile_palette_selected())
    profile_panel.mood_combo.currentIndexChanged.connect(lambda _index: _on_profile_mood_selected())
    profile_panel.add_palette_button.clicked.connect(_add_profile_palette)
    profile_panel.add_color_button.clicked.connect(_add_profile_palette_color)
    profile_panel.remove_color_button.clicked.connect(_remove_profile_palette_color)
    profile_panel.generate_seed_palette_button.clicked.connect(_generate_seed_palette)
    profile_panel.generate_quickshow_button.clicked.connect(_generate_quickshow_profile)
    profile_panel.save_palette_button.clicked.connect(_save_profile_palette)
    profile_panel.save_sections_button.clicked.connect(_save_profile_sections)
    profile_panel.save_all_button.clicked.connect(_save_profile_all)
    profile_panel.add_effect_button.clicked.connect(_add_effect_row)
    profile_panel.remove_effect_button.clicked.connect(lambda: _remove_selected_table_row(profile_panel.effects_table))
    profile_panel.add_param_button.clicked.connect(_add_param_row)
    profile_panel.remove_param_button.clicked.connect(lambda: _remove_selected_table_row(profile_panel.params_table))
    profile_panel.add_profile_eq_route_button.clicked.connect(
        lambda: _add_eq_route_row(profile_panel.profile_eq_routes_table)
    )
    profile_panel.remove_profile_eq_route_button.clicked.connect(
        lambda: _remove_selected_table_row(profile_panel.profile_eq_routes_table)
    )
    profile_panel.add_mood_eq_route_button.clicked.connect(
        lambda: _add_eq_route_row(profile_panel.mood_eq_routes_table)
    )
    profile_panel.remove_mood_eq_route_button.clicked.connect(
        lambda: _remove_selected_table_row(profile_panel.mood_eq_routes_table)
    )
    profile_panel.add_profile_instrument_route_button.clicked.connect(
        lambda: _add_instrument_route_row(profile_panel.profile_instrument_routes_table)
    )
    profile_panel.remove_profile_instrument_route_button.clicked.connect(
        lambda: _remove_selected_table_row(profile_panel.profile_instrument_routes_table)
    )
    profile_panel.add_mood_instrument_route_button.clicked.connect(
        lambda: _add_instrument_route_row(profile_panel.mood_instrument_routes_table)
    )
    profile_panel.remove_mood_instrument_route_button.clicked.connect(
        lambda: _remove_selected_table_row(profile_panel.mood_instrument_routes_table)
    )
    profile_panel.add_transition_button.clicked.connect(_add_transition_row)
    profile_panel.remove_transition_button.clicked.connect(
        lambda: _remove_selected_table_row(profile_panel.transitions_table)
    )
    for index, button in enumerate(profile_panel.palette_color_buttons):
        button.clicked.connect(lambda _checked=False, color_index=index: _on_profile_palette_color_clicked(color_index))
    for index, button in enumerate(profile_panel.seed_color_buttons):
        button.clicked.connect(lambda _checked=False, seed_index=index: _on_seed_color_clicked(seed_index))
    queue_panel.simulation_view_combo.currentIndexChanged.connect(
        lambda _index: _render_preview_simulation()
    )
    queue_panel.simulation_background_combo.currentIndexChanged.connect(
        lambda _index: _render_preview_simulation()
    )
    queue_panel.simulation_popout_button.clicked.connect(lambda: _show_simulation_window(fullscreen=False))
    queue_panel.simulation_fullscreen_button.clicked.connect(lambda: _show_simulation_window(fullscreen=True))
    queue_timer.timeout.connect(_poll_session_queue)

    if settings.last_tab:
        requested_tab = "Queue" if settings.last_tab == "Queue / Playback" else settings.last_tab
        for index in range(tabs.count()):
            if tabs.tabText(index) == requested_tab:
                tabs.setCurrentIndex(index)
                break

    reload_spatial_button.clicked.connect(lambda: _reload_spatial_scene(status="Spatial config reloaded from disk."))
    save_spatial_button.clicked.connect(_save_spatial_scene)
    spatial_node_list.itemSelectionChanged.connect(_on_spatial_list_selection_changed)
    spatial_canvas.nodeSelected.connect(_on_spatial_canvas_selected)
    spatial_canvas.nodeAxisCycleRequested.connect(_on_spatial_canvas_axis_cycled)
    spatial_canvas.nodeDragged.connect(_on_spatial_dragged)
    spatial_canvas.selectionStepRequested.connect(_on_spatial_selection_step_requested)
    spatial_canvas.axisNudgeRequested.connect(_on_spatial_axis_nudge_requested)
    spatial_canvas.viewCycleRequested.connect(_on_spatial_view_cycle_requested)
    spatial_x_spin.valueChanged.connect(lambda _value: _on_spatial_spin_changed())
    spatial_y_spin.valueChanged.connect(lambda _value: _on_spatial_spin_changed())
    spatial_z_spin.valueChanged.connect(lambda _value: _on_spatial_spin_changed())
    spatial_view_combo.currentIndexChanged.connect(
        lambda _index: _set_view_mode(_current_view_mode())
    )
    for axis_name, button in axis_buttons.items():
        button.toggled.connect(
            lambda checked, selected_axis=axis_name: checked and spatial_canvas.set_active_axis(selected_axis)
        )

    if active_profile_ref["path"] and active_profile_ref["path"].exists():
        try:
            _apply_palette_choices(
                profile_service.load_palette_choices(active_profile_ref["path"]),
                status=f"Active profile palettes ready from {active_profile_ref['path'].name}.",
            )
            _render_profile_state(palette_controller.state)
        except Exception:
            _render_queue_state(queue_controller.state)
    else:
        _render_profile_state(palette_state)
        _render_queue_state(queue_controller.state)

    _reload_spatial_scene(status="Spatial config ready.")
    _set_view_mode(_current_view_mode())
    _set_drag_axis(_current_drag_axis())
    _render_preview_simulation()
    _render_show_editor()
    _render_runtime_state(runtime_supervisor.snapshot())
    _render_diagnostics()

    def _current_gui_settings() -> GuiSettings:
        capture_settings, reactive_settings = _sync_runtime_settings_from_form()
        return GuiSettings(
            last_config_path=str(config_path or settings.last_config_path),
            last_profile_path=str(active_profile_ref["path"] or settings.last_profile_path),
            last_tab=tabs.tabText(tabs.currentIndex()),
            window_geometry=settings.window_geometry,
            splitter_sizes=settings.splitter_sizes,
            output_target_mode=str(queue_panel.output_target_combo.currentData() or "simulation"),
            selected_output_audio_device_id=queue_panel.output_device_combo.currentData(),
            selected_live_input_device_id=queue_panel.input_device_combo.currentData(),
            hardware_fallback_to_simulation=bool(queue_panel.hardware_fallback_check.isChecked()),
            recent_saved_show_paths=runtime_supervisor.recent_saved_shows(),
            show_editor_hidden_columns=tuple(
                index
                for index in range(queue_panel.show_cues_table.columnCount())
                if queue_panel.show_cues_table.isColumnHidden(index)
            ),
            show_editor_meta_hidden=bool(show_editor_state.get("meta_hidden")),
            show_editor_focus_mode=bool(show_editor_state.get("focus_enabled")),
            show_editor_splitter_sizes=tuple(int(value) for value in queue_panel.shows_splitter.sizes()),
            capture_settings=capture_settings,
            reactive_settings=reactive_settings,
        )

    window._dreamsync_settings_snapshot = _current_gui_settings

    return window
