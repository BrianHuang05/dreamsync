"""Top-level GUI main window factory."""

from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import replace
from html import escape
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
from dreamsync.gui.models.spatial_scene import SPATIAL_STEP, SceneNode
from dreamsync.gui.services.device_discovery_service import DeviceTestSpec
from dreamsync.gui.qt import QtModules
from dreamsync.gui.services import (
    AudioDeviceService,
    AppInfoService,
    DeviceDiscoveryService,
    DeviceHealthService,
    DeviceService,
    ProfileService,
    QueueService,
    RuntimeSupervisor,
    RuntimeTelemetryService,
    SessionService,
    ShowService,
    ShowPatchStore,
    SongPaletteStore,
    StorageService,
)
from dreamsync.gui.settings import GuiSettings, GuiSettingsStore
from dreamsync.profile import BUILTIN_PROFILES_DIR, VALID_MOODS, VALID_RENDER_MODES, resolve_profile_path
from dreamsync.profile_overrides import (
    SongPaletteAssignment,
    derive_profile_with_palette_assignment,
    track_key_for_path,
)
from dreamsync.show.control_patch import apply_show_control_patch
from dreamsync.show.baked_frames import default_baked_frame_path
from dreamsync.show.models import Show, ShowCue, ShowTimeline, ShowTrack
from dreamsync.gui.widgets.color_picker import build_color_picker
from dreamsync.gui.widgets.device_discovery_panel import build_device_discovery_panel
from dreamsync.gui.widgets.palette_strip import build_palette_strip
from dreamsync.gui.widgets.profile_editor_panel import build_profile_editor_panel
from dreamsync.gui.widgets.queue_panel import build_queue_panel
from dreamsync.gui.widgets.reactive_chord_history_view import (
    build_reactive_chord_history_view,
)
from dreamsync.gui.widgets.reactive_harmonic_debug_view import (
    build_reactive_harmonic_debug_view,
)
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


def _reactive_display_chord(
    snapshot: dict[str, object],
) -> tuple[str, bool]:
    """Choose the low-latency estimate without discarding commit status."""

    committed = str(snapshot.get("detected_chord", "") or "")
    observed = str(
        snapshot.get("harmonic_chord", "")
        or snapshot.get("harmonic_debug_chord", "")
        or ""
    )
    displayed = observed or committed
    return displayed, bool(
        displayed
        and committed
        and displayed != committed
    )


def _format_reactive_cycle(cycle: dict[str, object]) -> str:
    if not cycle.get("available", False):
        return "Chord cycle: acquiring meter and phrase length"
    current_bar = int(cycle.get("current_bar", 0) or 0)
    current_beat = int(cycle.get("current_beat", 0) or 0)
    cycle_bars = int(cycle.get("cycle_bars", 0) or 0)
    cycle_beats = int(cycle.get("cycle_beats", 0) or 0)
    bars_to_next = int(
        cycle.get("bars_to_next_cycle", 0) or 0
    )
    beats_to_next = int(
        cycle.get("beats_to_next_cycle", 0) or 0
    )
    beats_since = int(
        cycle.get("beats_since_cycle_start", 0) or 0
    )
    signature = tuple(cycle.get("time_signature", ()) or ())
    signature_text = (
        f"{int(signature[0])}/{int(signature[1])}"
        if len(signature) == 2
        else "acquiring"
    )
    return (
        f"Current Bar: {current_bar} / {cycle_bars}    "
        f"Current Beat: {current_beat} / {cycle_beats}\n"
        f"Cycle Length: {cycle_bars} bars ({cycle_beats} beats)    "
        f"Bars to next Cycle: {bars_to_next}    "
        f"Beats to next Cycle: {beats_to_next}\n"
        f"Beats Since Cycle Start: {beats_since}    "
        f"Detected Time Signature: {signature_text}"
    )


def _format_reactive_bpm_heading(bpm: float) -> str:
    value = float(bpm)
    return (
        f"Beat detector: {value:.1f} BPM"
        if value > 0.0
        else "Beat detector: acquiring BPM"
    )


def _format_structure_similarity(snapshot: dict[str, object]) -> str:
    meter = tuple(snapshot.get("structure_configured_meter", ()) or ())
    meter_text = (
        f"{int(meter[0])}/{int(meter[1])}" if len(meter) == 2 else "acquiring"
    )
    bar = snapshot.get("structure_current_bar")
    phrases = tuple(snapshot.get("structure_phrase_hypotheses", ()) or ())
    leading = phrases[0] if phrases and isinstance(phrases[0], dict) else {}
    phrase_text = (
        f"{leading.get('bars')} bars ({float(leading.get('probability', 0.0)):.2f}, "
        f"{leading.get('source', 'duration_prior')})"
        if leading and leading.get("bars") is not None
        else "acquiring"
    )
    section_id = snapshot.get("structure_section_id") or "unassigned"
    matches = tuple(snapshot.get("structure_top_matches", ()) or ())
    top_match = matches[0] if matches and isinstance(matches[0], dict) else {}
    match_text = (
        f"bar {top_match.get('right_bar')} "
        f"({float(top_match.get('combined', 0.0)):.2f})"
        if top_match
        else "none yet"
    )
    upcoming = tuple(snapshot.get("structure_upcoming_boundaries", ()) or ())
    target = upcoming[0] if upcoming and isinstance(upcoming[0], dict) else {}
    target_text = (
        f"bar {target.get('target_bar')} "
        f"({float(target.get('predicted_probability', 0.0)):.2f})"
        if target
        else "abstaining"
    )
    actions = tuple(snapshot.get("structure_action_history", ()) or ())
    action = actions[-1] if actions and isinstance(actions[-1], dict) else {}
    action_text = (
        f"{action.get('outcome')}: "
        f"{action.get('requested_effect') or 'none'} -> "
        f"{action.get('applied_effect') or 'none'}"
        if action
        else "none"
    )
    return (
        f"Configured Meter: {meter_text}    Current Bar: {bar if bar is not None else '—'}\n"
        f"Leading phrase hypothesis: {phrase_text}    Section: {section_id}\n"
        f"Top earlier match: {match_text}    Predicted target: {target_text}\n"
        f"Last structural action: {action_text}"
    )


PROFILE_SPATIAL_PRESET_OPTIONS = (
    "",
    "ripple_from_center",
    "flash_floor_only",
    "flash_top_only",
    "blend_left_to_right",
    "blend_front_to_back",
)
SHOW_CUE_LAYER_CATEGORY_OPTIONS = ("static", "slice", "expand")
SHOW_CUE_LAYER_TARGET_OPTIONS = (
    "whole_room",
    "top",
    "floor",
    "left",
    "right",
    "front",
    "back",
    "left_to_right",
    "right_to_left",
    "front_to_back",
    "back_to_front",
    "bottom_to_top",
    "top_to_bottom",
    "center",
)
SHOW_CUE_LAYER_TRIGGER_OPTIONS = ("continuous", "oneshot", "latched")
SHOW_CUE_LAYER_FALLOFF_OPTIONS = ("hard", "linear", "smoothstep", "radial")
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
SHOW_CUE_COL_LAYER_CATEGORY = 20
SHOW_CUE_COL_LAYER_TARGET = 21
SHOW_CUE_COL_LAYER_TRIGGER = 22
SHOW_CUE_COL_LAYER_FALLOFF = 23
SHOW_CUE_COL_LAYER_THICKNESS = 24
SHOW_CUE_COL_LAYER_SPEED = 25
SHOW_CUE_COL_LAYER_PRIORITY = 26


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
    settings_store: GuiSettingsStore | None = None,
) -> object:
    QtWidgets = qt_modules.QtWidgets
    QtCore = qt_modules.QtCore
    QtGui = qt_modules.QtGui

    def _apply_application_theme(dark_mode: bool) -> None:
        """Apply the application chrome theme without changing simulation canvases."""
        application = QtWidgets.QApplication.instance()
        if application is None:
            return
        if not dark_mode:
            application.setPalette(application.style().standardPalette())
            return
        palette = QtGui.QPalette(application.style().standardPalette())
        colors = {
            QtGui.QPalette.ColorRole.Window: "#121212",
            QtGui.QPalette.ColorRole.WindowText: "#f4f4f5",
            QtGui.QPalette.ColorRole.Base: "#1d1d1f",
            QtGui.QPalette.ColorRole.AlternateBase: "#29292c",
            QtGui.QPalette.ColorRole.ToolTipBase: "#f4f4f5",
            QtGui.QPalette.ColorRole.ToolTipText: "#121212",
            QtGui.QPalette.ColorRole.Text: "#f4f4f5",
            QtGui.QPalette.ColorRole.Button: "#2a2a2d",
            QtGui.QPalette.ColorRole.ButtonText: "#f4f4f5",
            QtGui.QPalette.ColorRole.BrightText: "#ffffff",
            QtGui.QPalette.ColorRole.Highlight: "#2563a9",
            QtGui.QPalette.ColorRole.HighlightedText: "#ffffff",
            QtGui.QPalette.ColorRole.Link: "#93c5fd",
            QtGui.QPalette.ColorRole.PlaceholderText: "#a1a1aa",
        }
        for role, color in colors.items():
            palette.setColor(role, QtGui.QColor(color))
        application.setPalette(palette)

    _apply_application_theme(settings.dark_mode)

    if config_path is None and settings.last_config_path:
        config_path = Path(settings.last_config_path)

    device_service = DeviceService()
    device_discovery_service = DeviceDiscoveryService()
    audio_device_service = AudioDeviceService()
    profile_service = ProfileService()
    queue_service = QueueService()
    device_health_service = DeviceHealthService()
    storage_service = StorageService()
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
    profile_library_state = {
        "entries": (),
        "generated": (),
        "generated_seed": None,
    }
    runtime_supervisor.set_config_path(config_path)
    runtime_supervisor.set_output_target_mode(settings.output_target_mode)
    runtime_supervisor.set_hardware_fallback_to_simulation(settings.hardware_fallback_to_simulation)
    runtime_supervisor.set_selected_output_audio_device(settings.selected_output_audio_device_id)
    runtime_supervisor.set_selected_live_input_device(settings.selected_live_input_device_id)
    runtime_supervisor.set_capture_settings(settings.capture_settings)
    runtime_supervisor.set_reactive_settings(settings.reactive_settings)
    runtime_supervisor.set_baked_playback_mode(settings.baked_playback_mode)
    runtime_supervisor.set_live_loopback_enabled(settings.live_loopback_enabled)
    runtime_supervisor.set_recent_saved_shows(settings.recent_saved_show_paths)
    persisted_settings_state = {"value": settings}

    spatial_controller = SpatialController(device_service)
    palette_controller = PaletteController(profile_service)
    queue_controller = QueueController(queue_service)
    show_patch_controller = ShowPatchController(show_patch_store)
    queue_controller.set_status("Load a local file or folder to start pairing palettes to songs.")
    assignments = song_palette_store.load()

    window = QtWidgets.QMainWindow()
    window.setWindowTitle("DreamSync Desktop")
    window.resize(1120, 760)
    if settings.window_geometry:
        try:
            geometry = QtCore.QByteArray.fromBase64(
                settings.window_geometry.encode("ascii")
            )
            if not geometry.isEmpty():
                window.restoreGeometry(geometry)
        except Exception:
            pass

    app_info_service = AppInfoService()
    help_menu = window.menuBar().addMenu("&Help")
    about_action = QtGui.QAction("About DreamSync", window)
    about_action.setObjectName("aboutDreamSyncAction")
    help_menu.addAction(about_action)

    def _show_about_dialog() -> None:  # pragma: no cover - Qt only
        app_info = app_info_service.snapshot(config_path=config_path)
        dialog = QtWidgets.QDialog(window)
        dialog.setWindowTitle("About DreamSync")
        dialog.setObjectName("aboutDreamSyncDialog")
        layout = QtWidgets.QVBoxLayout(dialog)
        heading = QtWidgets.QLabel(f"<h2>{escape(app_info.product_name)}</h2>")
        layout.addWidget(heading)
        support_text = QtWidgets.QPlainTextEdit(app_info.support_text())
        support_text.setReadOnly(True)
        support_text.setObjectName("aboutSupportText")
        layout.addWidget(support_text)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Close)
        copy_button = buttons.addButton("Copy Support Info", QtWidgets.QDialogButtonBox.ButtonRole.ActionRole)
        copy_button.setObjectName("copySupportInfoButton")
        copy_button.clicked.connect(
            lambda: QtWidgets.QApplication.clipboard().setText(app_info.support_text())
        )
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(520, 280)
        dialog.exec()

    about_action.triggered.connect(_show_about_dialog)
    window._dreamsync_about_action = about_action

    class _SpinBoxWheelBlocker(QtCore.QObject):
        """Prevent wheel scrolling from silently changing numeric fields."""

        def eventFilter(self, watched, event):  # pragma: no cover - Qt only
            if (
                event.type() == QtCore.QEvent.Type.Wheel
                and isinstance(watched, QtWidgets.QAbstractSpinBox)
            ):
                parent = watched.parentWidget()
                while parent is not None and not isinstance(parent, QtWidgets.QAbstractScrollArea):
                    parent = parent.parentWidget()
                if parent is not None:
                    viewport = parent.viewport()
                    position = QtCore.QPointF(
                        viewport.mapFromGlobal(event.globalPosition().toPoint())
                    )
                    forwarded = QtGui.QWheelEvent(
                        position,
                        event.globalPosition(),
                        event.pixelDelta(),
                        event.angleDelta(),
                        event.buttons(),
                        event.modifiers(),
                        event.phase(),
                        event.inverted(),
                    )
                    QtWidgets.QApplication.sendEvent(viewport, forwarded)
                event.ignore()
                return True
            return super().eventFilter(watched, event)

    spinbox_wheel_blocker = _SpinBoxWheelBlocker(window)
    app_instance = QtWidgets.QApplication.instance()
    if app_instance is not None:
        app_instance.installEventFilter(spinbox_wheel_blocker)
    window._dreamsync_spinbox_wheel_blocker = spinbox_wheel_blocker

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
    spatial_heading = QtWidgets.QLabel("Room Layout")
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
    mode_row = QtWidgets.QHBoxLayout()
    mode_row.addWidget(QtWidgets.QLabel("Edit mode"))
    spatial_edit_mode_combo = QtWidgets.QComboBox()
    spatial_edit_mode_combo.setObjectName("spatialEditModeCombo")
    spatial_edit_mode_combo.addItem("Move Strip", "move_strip")
    spatial_edit_mode_combo.addItem("Orient Strip", "orient_strip")
    spatial_edit_mode_combo.addItem("Individual Nodes", "fine_tune")
    mode_row.addWidget(spatial_edit_mode_combo, 1)
    spatial_editor_layout.addLayout(mode_row)
    spatial_direction_widget = QtWidgets.QWidget()
    spatial_direction_widget.setObjectName("spatialDirectionWidget")
    direction_row = QtWidgets.QHBoxLayout(spatial_direction_widget)
    direction_row.setContentsMargins(0, 0, 0, 0)
    direction_row.addWidget(QtWidgets.QLabel("Direction"))
    spatial_direction_combo = QtWidgets.QComboBox()
    spatial_direction_combo.setObjectName("spatialDirectionCombo")
    for direction_label, direction_value in (
        ("X+", "x+"),
        ("X-", "x-"),
        ("Y+", "y+"),
        ("Y-", "y-"),
        ("Z+", "z+"),
        ("Z-", "z-"),
    ):
        spatial_direction_combo.addItem(direction_label, direction_value)
    direction_row.addWidget(spatial_direction_combo, 1)
    apply_spatial_line_button = QtWidgets.QPushButton("Apply Line")
    apply_spatial_line_button.setObjectName("applySpatialLineButton")
    reverse_spatial_strip_button = QtWidgets.QPushButton("Reverse Order")
    reverse_spatial_strip_button.setObjectName("reverseSpatialStripButton")
    direction_row.addWidget(apply_spatial_line_button)
    direction_row.addWidget(reverse_spatial_strip_button)
    spatial_editor_layout.addWidget(spatial_direction_widget)
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
        spin.setSingleStep(SPATIAL_STEP)
        spin.setDecimals(2)
    spin_grid.addWidget(spatial_x_spin, 0, 1)
    spin_grid.addWidget(spatial_y_spin, 1, 1)
    spin_grid.addWidget(spatial_z_spin, 2, 1)
    spatial_editor_layout.addLayout(spin_grid)
    spatial_validation_label = QtWidgets.QLabel("Layout validity: no strip chains loaded.")
    spatial_validation_label.setWordWrap(True)
    spatial_validation_label.setObjectName("spatialValidationLabel")
    spatial_editor_layout.addWidget(spatial_validation_label)
    spatial_status_label = QtWidgets.QLabel(
        "Pick a strip from the list or canvas to edit its rectangular layout. Press Enter to edit individual nodes; "
        "Orient Strip applies a straight cardinal line; Fine Tune Dots edits one section on the room grid. "
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
    device_discovery_panel = build_device_discovery_panel(qt_modules)
    tabs.addTab(device_discovery_panel.widget, "Device Discovery")
    tabs.addTab(spatial_widget, "Room Layout")

    if active_profile_ref["path"] and active_profile_ref["path"].exists():
        palette_state = palette_controller.load(active_profile_ref["path"])
    else:
        palette_state = PaletteEditorState()
    profile_panel = build_profile_editor_panel(qt_modules)
    tabs.addTab(profile_panel.widget, "Palettes")

    queue_panel = build_queue_panel(qt_modules)
    if settings.show_compile_seed is not None:
        queue_panel.compile_seed_spin.setValue(int(settings.show_compile_seed))
        queue_panel.compile_seed_check.setChecked(True)
    queue_panel.live_loopback_check.setChecked(settings.live_loopback_enabled)
    queue_panel.dark_mode_check.setChecked(settings.dark_mode)
    queue_panel.device_room_config_path_edit.setText(str(config_path or settings.last_config_path))
    queue_panel.profile_directory_edit.setText(settings.profile_directory)
    queue_panel.show_directory_edit.setText(settings.show_directory)
    queue_panel.queue_directory_edit.setText(settings.queue_directory)
    queue_panel.reactive_chord_panel_check.setChecked(
        settings.reactive_chord_panel_visible
    )
    queue_panel.reactive_waveform_panel_check.setChecked(
        settings.reactive_waveform_panel_visible
    )
    queue_panel.reactive_harmonic_panel_check.setChecked(
        settings.reactive_harmonic_panel_visible
    )
    color_profile_index = (
        queue_panel.reactive_live_color_profile_combo.findData(
            settings.reactive_live_color_profile
        )
    )
    queue_panel.reactive_live_color_profile_combo.setCurrentIndex(
        color_profile_index if color_profile_index >= 0 else 0
    )
    active_effect_index = (
        queue_panel.reactive_live_active_effect_combo.findData(
            settings.reactive_live_active_effect
        )
    )
    queue_panel.reactive_live_active_effect_combo.setCurrentIndex(
        active_effect_index if active_effect_index >= 0 else 0
    )
    saved_effect_bank = {
        str(value).strip().lower()
        for value in settings.reactive_live_effect_bank
    }
    for effect_button in queue_panel.reactive_live_effect_buttons:
        effect_button.setChecked(
            str(effect_button.property("effectMode")) in saved_effect_bank
        )
    if not any(
        effect_button.isChecked()
        for effect_button in queue_panel.reactive_live_effect_buttons
    ):
        queue_panel.reactive_live_effect_buttons[0].setChecked(True)
    if settings.reactive_diagnostics_splitter_sizes:
        queue_panel.reactive_diagnostics_splitter.setSizes(
            list(settings.reactive_diagnostics_splitter_sizes)
        )
    startup_mode_index = queue_panel.startup_live_mode_combo.findData(settings.live_start_mode)
    queue_panel.startup_live_mode_combo.setCurrentIndex(
        startup_mode_index if startup_mode_index >= 0 else 0
    )
    for control, tooltip in (
        (queue_panel.load_saved_show_button, "Open a saved multi-track Show"),
        (queue_panel.load_saved_track_button, "Cue a precompiled single-track lightshow"),
        (queue_panel.cue_track_button, "Cue local audio after the current item for hot compilation"),
        (queue_panel.refresh_button, "Refresh the Live queue (R or Ctrl+R)"),
        (queue_panel.start_preview_button, "Start Live playback (Space when stopped)"),
        (queue_panel.pause_playback_button, "Pause or resume Live playback (Space)"),
        (queue_panel.stop_preview_button, "Stop Live playback (Esc)"),
        (queue_panel.queue_mode_button, "Show the Live queue (M)"),
        (queue_panel.reactive_mode_button, "Cue the Reactive interface without listening (M)"),
        (queue_panel.start_reactive_button, "Start listening in Reactive mode (Space)"),
        (queue_panel.stop_reactive_button, "Stop Reactive listening (Space or Esc)"),
        (queue_panel.start_capture_button, "Start system-audio loopback capture after confirming the OS loopback device"),
        (queue_panel.stop_capture_button, "Stop system-audio loopback capture"),
        (queue_panel.switch_pipeline_button, "Auto-play captured shows as they become ready"),
        (queue_panel.cycle_palette_button, "Cycle the selected song palette (Ctrl+P)"),
        (queue_panel.recompile_palette_button, "Recolor prepared cue data without recompiling (Ctrl+C)"),
        (queue_panel.load_show_button, "Open a saved Show (Ctrl+O)"),
        (queue_panel.compile_show_button, "Compile the selected Track (Ctrl+C)"),
        (queue_panel.compile_all_tracks_button, "Compile every pending Track (Ctrl+Shift+C)"),
        (queue_panel.save_show_button, "Save this Show (Ctrl+S)"),
        (queue_panel.clear_show_button, "Create a new Show (Ctrl+N)"),
        (queue_panel.add_show_track_button, "Add Tracks to this Show (Ctrl+A when not editing text)"),
        (queue_panel.remove_show_track_button, "Remove the selected Track; Delete removes a selected cue or beat line in the editor"),
        (queue_panel.move_show_track_up_button, "Move the selected Track up (Shift+Up)"),
        (queue_panel.move_show_track_down_button, "Move the selected Track down (Shift+Down)"),
        (queue_panel.play_selected_cue_button, "Play from the selected cue (Play/Pause when stopped)"),
        (queue_panel.show_pause_button, "Pause or resume playback (Play/Pause)"),
        (queue_panel.show_stop_button, "Stop playback (Esc while playing)"),
        (queue_panel.show_editor_focus_button, "Toggle editor focus mode"),
    ):
        control.setToolTip(tooltip)
    for control, tooltip in (
        (profile_panel.load_profile_button, "Load a profile (L)"),
        (profile_panel.add_palette_button, "Create a new palette (N)"),
        (profile_panel.generate_seed_palette_button, "Generate a palette from the seed colors (G)"),
        (profile_panel.generate_quickshow_button, "Generate a Quickshow profile (Q)"),
        (profile_panel.seed_randomness_check, "Toggle seed randomness (R)"),
        (profile_panel.save_palette_button, "Save the selected palette (S)"),
        (profile_panel.save_all_button, "Save all palette changes (Ctrl+S)"),
    ):
        control.setToolTip(tooltip)

    # Routing is runtime/show behavior, but it remains persisted in the profile
    # model. Reparent the existing widgets so the UI reflects that ownership
    # without changing the YAML contract or controller callbacks.
    routing_groups = (
        profile_panel.profile_eq_group,
        profile_panel.mood_eq_group,
        profile_panel.profile_instrument_group,
        profile_panel.mood_instrument_group,
    )
    routing_layout = queue_panel.show_routing_host.layout()
    routing_expanded = {group: False for group in routing_groups}

    def _set_routing_content_visible(layout, visible: bool) -> None:
        for item_index in range(layout.count()):
            item = layout.itemAt(item_index)
            child_widget = item.widget()
            if child_widget is not None:
                child_widget.setVisible(visible)
            child_layout = item.layout()
            if child_layout is not None:
                _set_routing_content_visible(child_layout, visible)

    def _relayout_routing_groups() -> None:
        for group in routing_groups:
            routing_layout.removeWidget(group)
        for row, group in enumerate(routing_groups, start=1):
            routing_layout.addWidget(group, row, 0)
        routing_layout.setColumnStretch(0, 1)

    for index, group in enumerate(routing_groups):
        group.setParent(queue_panel.show_routing_host)
        group.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Maximum,
        )
        title = group.title()
        group.setTitle("")
        header = QtWidgets.QToolButton()
        header.setText(f"▸ {title}")
        header.setCheckable(True)
        header.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly)
        header.setStyleSheet("QToolButton { font-weight: 600; text-align: left; }")
        group.layout().insertWidget(0, header)

        def _toggle_routing(checked, selected_group=group, selected_header=header, selected_title=title):
            routing_expanded[selected_group] = bool(checked)
            selected_header.setText(f"{'▾' if checked else '▸'} {selected_title}")
            for item_index in range(1, selected_group.layout().count()):
                item = selected_group.layout().itemAt(item_index)
                child_widget = item.widget()
                if child_widget is not None:
                    child_widget.setVisible(bool(checked))
                child_layout = item.layout()
                if child_layout is not None:
                    _set_routing_content_visible(child_layout, bool(checked))
            selected_group.setMaximumHeight(
                16777215 if checked else selected_header.sizeHint().height() + 18
            )
            _relayout_routing_groups()

        header.toggled.connect(_toggle_routing)
        _toggle_routing(False)
    _relayout_routing_groups()
    queue_panel.show_routing_group.toggled.connect(queue_panel.show_routing_host.setVisible)
    simulation_canvas = build_spatial_canvas(
        qt_modules,
        spatial_controller.snapshot(),
        interactive=False,
        background_theme="dark",
    )
    queue_panel.simulation_layout.addWidget(simulation_canvas)
    show_simulation_canvas = build_spatial_canvas(
        qt_modules,
        spatial_controller.snapshot(),
        interactive=False,
        background_theme="dark",
    )
    show_simulation_canvas.setObjectName("showSimulationSpatialCanvas")
    queue_panel.show_simulation_layout.addWidget(show_simulation_canvas)
    tabs.addTab(queue_panel.shows_widget, "Shows")
    tabs.addTab(queue_panel.widget, "Live")
    tabs.addTab(queue_panel.config_widget, "Config")

    diagnostics_panel = build_runtime_diagnostics_panel(qt_modules)
    tabs.addTab(diagnostics_panel.widget, "Diagnostics")

    status_bar = QtWidgets.QStatusBar()
    status_bar.showMessage(
        f"profile={active_profile_ref['path'] or settings.last_profile_path or 'none'} | "
        f"config={config_path or settings.last_config_path or 'none'}"
    )
    window.setStatusBar(status_bar)

    error_toast_state = {"widget": None, "message": ""}
    error_toast_timer = QtCore.QTimer(window)
    error_toast_timer.setSingleShot(True)
    success_toast_state = {"widget": None, "message": ""}
    success_toast_timer = QtCore.QTimer(window)
    success_toast_timer.setSingleShot(True)

    def _dismiss_error_toast() -> None:  # pragma: no cover - Qt only
        toast = error_toast_state.get("widget")
        if toast is not None:
            toast.hide()
            toast.deleteLater()
        error_toast_state["widget"] = None
        error_toast_state["message"] = ""

    def _position_error_toast() -> None:  # pragma: no cover - Qt only
        toast = error_toast_state.get("widget")
        if toast is None:
            return
        toast.setFixedWidth(min(620, max(280, tabs.width() - 48)))
        toast.adjustSize()
        toast.move(
            max(24, (tabs.width() - toast.width()) // 2),
            max(24, (tabs.height() - toast.height()) // 2),
        )

    def _dismiss_success_toast() -> None:  # pragma: no cover - Qt only
        toast = success_toast_state.get("widget")
        if toast is not None:
            toast.hide()
            toast.deleteLater()
        success_toast_state["widget"] = None
        success_toast_state["message"] = ""

    def _position_success_toast() -> None:  # pragma: no cover - Qt only
        toast = success_toast_state.get("widget")
        if toast is None:
            return
        toast.setFixedWidth(min(480, max(280, tabs.width() - 48)))
        toast.adjustSize()
        toast.move(
            max(24, (tabs.width() - toast.width()) // 2),
            max(24, tabs.height() - toast.height() - 32),
        )

    def _is_error_message(message: object) -> bool:
        value = str(message or "").strip().lower()
        return bool(value) and any(
            marker in value
            for marker in (
                "error",
                "failed",
                "failure",
                "could not",
                "cannot ",
                "can't ",
                "unable",
                "invalid",
                "not found",
                "requires ",
                "must ",
                "unavailable",
            )
        )

    def _show_error(message: object) -> None:  # pragma: no cover - Qt only
        text = str(message or "").strip()
        if not text:
            return
        toast = error_toast_state.get("widget")
        if toast is not None and error_toast_state.get("message") == text:
            error_toast_timer.start(5000)
            return
        _dismiss_error_toast()
        toast = QtWidgets.QFrame(tabs)
        toast.setObjectName("dreamsyncErrorToast")
        toast.setStyleSheet(
            "QFrame#dreamsyncErrorToast { background: #5f151b; border: 1px solid #ff6673; "
            "border-radius: 8px; } QLabel { color: #fff4f4; } "
            "QToolButton { color: #fff4f4; border: 0; font-size: 18px; padding: 0 4px; }"
        )
        layout = QtWidgets.QHBoxLayout(toast)
        layout.setContentsMargins(16, 12, 8, 12)
        icon = QtWidgets.QLabel("Error")
        icon.setStyleSheet("font-weight: 700; color: #ffd6d9;")
        layout.addWidget(icon, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        label = QtWidgets.QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(label, 1)
        close = QtWidgets.QToolButton()
        close.setText("×")
        close.setToolTip("Dismiss error")
        close.clicked.connect(_dismiss_error_toast)
        layout.addWidget(close, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        error_toast_state["widget"] = toast
        error_toast_state["message"] = text
        _position_error_toast()
        toast.show()
        toast.raise_()
        error_toast_timer.start(5000)

    def _show_success(message: object) -> None:  # pragma: no cover - Qt only
        text = str(message or "").strip()
        if not text:
            return
        toast = success_toast_state.get("widget")
        if toast is not None and success_toast_state.get("message") == text:
            success_toast_timer.start(3500)
            return
        _dismiss_success_toast()
        toast = QtWidgets.QFrame(tabs)
        toast.setObjectName("dreamsyncSuccessToast")
        toast.setStyleSheet(
            "QFrame#dreamsyncSuccessToast { background: #14532d; border: 1px solid #4ade80; "
            "border-radius: 8px; } QLabel { color: #f0fdf4; } "
            "QToolButton { color: #f0fdf4; border: 0; font-size: 18px; padding: 0 4px; }"
        )
        layout = QtWidgets.QHBoxLayout(toast)
        layout.setContentsMargins(16, 12, 8, 12)
        icon = QtWidgets.QLabel("Saved")
        icon.setStyleSheet("font-weight: 700; color: #bbf7d0;")
        layout.addWidget(icon, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        label = QtWidgets.QLabel(text)
        label.setWordWrap(True)
        layout.addWidget(label, 1)
        close = QtWidgets.QToolButton()
        close.setText("×")
        close.setToolTip("Dismiss notification")
        close.clicked.connect(_dismiss_success_toast)
        layout.addWidget(close, 0, QtCore.Qt.AlignmentFlag.AlignTop)
        success_toast_state["widget"] = toast
        success_toast_state["message"] = text
        _position_success_toast()
        toast.show()
        toast.raise_()
        success_toast_timer.start(3500)

    def _set_page_error(label, message: object) -> None:  # pragma: no cover - Qt only
        text = str(message or "").strip()
        label.setText(text)
        _show_error(text)

    error_toast_timer.timeout.connect(_dismiss_error_toast)
    success_toast_timer.timeout.connect(_dismiss_success_toast)

    class _ErrorToastPositioner(QtCore.QObject):
        def eventFilter(self, watched, event):  # pragma: no cover - Qt only
            if event.type() in (QtCore.QEvent.Type.Resize, QtCore.QEvent.Type.Show):
                QtCore.QTimer.singleShot(0, _position_error_toast)
                QtCore.QTimer.singleShot(0, _position_success_toast)
            return super().eventFilter(watched, event)

    error_toast_positioner = _ErrorToastPositioner(window)
    tabs.installEventFilter(error_toast_positioner)
    window._dreamsync_error_toast_positioner = error_toast_positioner
    window._dreamsync_success_toast_timer = success_toast_timer

    _queue_set_status = queue_controller.set_status

    def _set_queue_status(message: str) -> None:
        _queue_set_status(message)
        if _is_error_message(message):
            _show_error(message)

    queue_controller.set_status = _set_queue_status

    playlist_state = {"playlist": None}
    precompiled_queue_state = {"timelines": {}, "sources": {}}
    live_mode_state = {
        "value": "reactive" if settings.live_start_mode == "reactive" else "queue"
    }
    reactive_effect_tempo_state = {"multiplier": 1.0}
    reactive_cycle_tempo_state = {"multiplier": 1.0}
    queue_drag_state = {"active": False}
    palette_choices: list[dict[str, object]] = []
    spotify_runtime = {"client": None, "watcher": None, "error": ""}
    spatial_entries_state = {"entries": list(scene_entries)}
    discovery_entries_state = {"entries": []}
    discovery_scan_state = {"thread": None, "worker": None}
    discovery_seen_entries_state = {"by_address": {}}
    discovery_identify_state = {"thread": None, "worker": None, "key": None}
    discovery_test_state = {"thread": None, "worker": None}
    spatial_signal_block = {"value": False}
    spatial_object_mode_state = {"value": "move_strip"}
    simulation_frame_state = {"node_colors": {}}
    simulation_window_state = {"window": None, "canvas": None}
    reactive_diagnostic_window_state = {
        "chord": {
            "window": None,
            "view": None,
            "exit_button": None,
            "escape_shortcut": None,
            "mode_label": None,
            "mode": "",
        },
        "harmonic": {
            "window": None,
            "view": None,
            "exit_button": None,
            "escape_shortcut": None,
            "mode_label": None,
            "mode": "",
        },
    }
    reactive_diagnostic_data = {
        "chord": ("", (), "Waiting for live input"),
        "harmonic": (
            (),
            (),
            "",
            0.0,
            (),
            "",
            (),
            "",
            None,
            0.0,
            {},
        ),
    }
    window._dreamsync_reactive_diagnostic_windows = (
        reactive_diagnostic_window_state
    )
    selected_show_path = {"value": None}

    class _DiscoveryScanWorker(QtCore.QObject):  # pragma: no cover - Qt only
        completed = QtCore.Signal(str, object, str, str)

        def __init__(self, kind: str) -> None:
            super().__init__()
            self._kind = kind

        @QtCore.Slot()
        def run(self) -> None:
            """Run device I/O off the GUI thread and return usable partial results."""

            entries: list[object] = []
            warnings: list[str] = []
            try:
                if self._kind == "lan":
                    try:
                        entries = device_discovery_service.scan_lan()
                    except Exception as exc:
                        warnings.append(f"LAN scan failed: {str(exc).strip() or type(exc).__name__}")
                elif self._kind == "ble":
                    try:
                        entries = device_discovery_service.scan_ble()
                    except Exception as exc:
                        warnings.append(
                            f"BLE scan unavailable: {str(exc).strip() or type(exc).__name__}. "
                            "Ensure Bluetooth is enabled."
                        )
                else:
                    try:
                        entries.extend(device_discovery_service.scan_lan())
                    except Exception as exc:
                        warnings.append(f"LAN scan failed: {str(exc).strip() or type(exc).__name__}")
                    try:
                        entries.extend(device_discovery_service.scan_ble())
                    except Exception as exc:
                        warnings.append(
                            f"BLE scan unavailable: {str(exc).strip() or type(exc).__name__}. "
                            "Ensure Bluetooth is enabled."
                        )
                entries = device_discovery_service.measure_lan_latency(entries)
                entries = device_discovery_service.merge_with_config(entries, config_path)
            except Exception as exc:
                self.completed.emit(self._kind, [], "", str(exc).strip() or type(exc).__name__)
                return
            self.completed.emit(self._kind, entries, " ".join(warnings), "")

    class _DiscoveryScanReceiver(QtCore.QObject):  # pragma: no cover - Qt only
        @QtCore.Slot(str, object, str, str)
        def complete(self, kind: str, entries: list[object], warning: str, error: str) -> None:
            _finish_discovery_scan(kind, entries, warning, error)

    discovery_scan_receiver = _DiscoveryScanReceiver(window)

    class _DiscoveryIdentifyWorker(QtCore.QObject):  # pragma: no cover - Qt only
        completed = QtCore.Signal(object, str)

        def __init__(self, entry: object, segments: int, transport: object, protocol: object) -> None:
            super().__init__()
            self._entry = entry
            self._segments = segments
            self._transport = transport
            self._protocol = protocol

        @QtCore.Slot()
        def run(self) -> None:
            try:
                device_discovery_service.identify(
                    self._entry,
                    seconds=5.0,
                    segments=self._segments,
                    transport=self._transport,
                    protocol=self._protocol,
                )
            except Exception as exc:
                self.completed.emit(self._entry, str(exc).strip() or type(exc).__name__)
                return
            self.completed.emit(self._entry, "")

    class _DiscoveryIdentifyReceiver(QtCore.QObject):  # pragma: no cover - Qt only
        @QtCore.Slot(object, str)
        def complete(self, entry: object, error: str) -> None:
            _finish_discovery_identify(entry, error)

    discovery_identify_receiver = _DiscoveryIdentifyReceiver(window)

    class _DiscoveryTestWorker(QtCore.QObject):  # pragma: no cover - Qt only
        completed = QtCore.Signal(object, object, str)

        def __init__(self, entry: object, spec: DeviceTestSpec) -> None:
            super().__init__()
            self._entry = entry
            self._spec = spec

        @QtCore.Slot()
        def run(self) -> None:
            try:
                result = device_discovery_service.test_device(self._entry, self._spec)
            except Exception as exc:
                self.completed.emit(self._entry, None, str(exc).strip() or type(exc).__name__)
                return
            self.completed.emit(self._entry, result, "")

    class _DiscoveryTestReceiver(QtCore.QObject):  # pragma: no cover - Qt only
        @QtCore.Slot(object, object, str)
        def complete(self, entry: object, result: object, error: str) -> None:
            device_discovery_panel.advanced_test_device_button.setEnabled(True)
            if error:
                _set_discovery_status(f"Advanced test failed for {entry.address}: {error}")
            else:
                _set_discovery_status(
                    f"Advanced test finished for {entry.address}: "
                    f"{getattr(result, 'frames_sent', 0)} frame(s) sent."
                )

    discovery_test_receiver = _DiscoveryTestReceiver(window)
    show_editor_state = {
        "show": show_service.new_show(),
        "track_index": None,
        "timeline": None,
        "path": None,
        "audio_path": None,
        "context": None,
        "notice": "Add a Track to this Show, then compile it to start editing.",
        "focus_sizes": tuple(settings.show_editor_splitter_sizes) or None,
        "focus_enabled": bool(settings.show_editor_focus_mode),
        "meta_hidden": bool(settings.show_editor_meta_hidden),
        "hidden_columns": tuple(int(value) for value in settings.show_editor_hidden_columns),
        "show_palette_dirty": False,
        "show_palette_colors": None,
        "busy": False,
        "baked_artifact_path": None,
        "baked_validation": None,
    }

    def _select_combo_data(combo: object, value: object) -> None:
        for index in range(combo.count()):
            if combo.itemData(index) == value:
                combo.setCurrentIndex(index)
                return

    def _current_discovery_entry() -> object | None:
        row = device_discovery_panel.devices_table.currentRow()
        entries = discovery_entries_state["entries"]
        if row < 0 or row >= len(entries):
            return None
        return entries[row]

    def _discovery_config_for_entry(entry: object) -> object | None:
        if config_path is None or not config_path.exists():
            return None
        try:
            for config in device_service.load_config(config_path):
                if config.address == entry.address:
                    return config
        except Exception:
            return None
        return None

    def _set_discovery_entries(entries: list[object], *, status: str) -> None:
        selected_entry = _current_discovery_entry()
        selected_key = getattr(selected_entry, "key", None)
        discovery_entries_state["entries"] = list(entries)
        table = device_discovery_panel.devices_table
        table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            configuration_issue = entry.assigned and not entry.connected
            identifying = entry.key == discovery_identify_state["key"]
            values = (
                "Offline (configured)" if configuration_issue else ("Assigned" if entry.assigned else "Unassigned"),
                entry.source.upper(),
                entry.existing_name or entry.name,
                entry.address,
                "—" if entry.latency_ms is None else f"{entry.latency_ms:.1f} ms",
                "" if entry.rssi is None else str(entry.rssi),
            )
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(str(value))
                if identifying:
                    item.setBackground(QtGui.QColor("#DCFCE7"))
                    item.setForeground(QtGui.QColor("#166534"))
                    item.setToolTip("Identifying: this device is flashing blue.")
                elif configuration_issue:
                    item.setBackground(QtGui.QColor("#FEE2E2"))
                    item.setForeground(QtGui.QColor("#991B1B"))
                    item.setToolTip("Configured device was not found during this scan.")
                table.setItem(row, column, item)
        table.resizeColumnsToContents()
        if entries:
            selected_row = next(
                (row for row, entry in enumerate(entries) if entry.key == selected_key),
                0,
            )
            table.selectRow(selected_row)
        device_discovery_panel.status_label.setText(status)
        if _is_error_message(status):
            _show_error(status)

    def _populate_discovery_form(entry: object | None) -> None:
        if entry is None:
            return
        existing_config = _discovery_config_for_entry(entry)
        name = getattr(existing_config, "name", None) or entry.existing_name or entry.name
        device_discovery_panel.name_edit.setText(name)
        _select_combo_data(device_discovery_panel.type_combo, getattr(existing_config, "type", None) or entry.source)
        device_discovery_panel.segments_spin.setValue(int(getattr(existing_config, "segments", 15) or 15))
        _select_combo_data(
            device_discovery_panel.transport_combo,
            getattr(existing_config, "transport", None) or "ptreal",
        )
        _select_combo_data(
            device_discovery_panel.protocol_combo,
            getattr(existing_config, "protocol", None) or "segment",
        )
        _select_combo_data(device_discovery_panel.role_combo, getattr(existing_config, "role", None) or "")
        brightness = getattr(existing_config, "brightness_scale", None)
        device_discovery_panel.brightness_spin.setValue(float(1.0 if brightness is None else brightness))
        placement = getattr(existing_config, "placement", None)
        device_discovery_panel.x_spin.setValue(float(getattr(placement, "x", 0.0)))
        device_discovery_panel.y_spin.setValue(float(getattr(placement, "y", 0.0)))
        device_discovery_panel.z_spin.setValue(float(getattr(placement, "z", 0.0)))

    def _set_discovery_status(message: str) -> None:  # pragma: no cover - Qt only
        """Show the current discovery state in the persistent tab status area."""

        device_discovery_panel.status_label.setText(message)
        if _is_error_message(message):
            _show_error(message)

    def _discovery_scan_buttons() -> tuple[object, ...]:  # pragma: no cover - Qt only
        return (
            device_discovery_panel.scan_lan_devices_button,
            device_discovery_panel.scan_ble_devices_button,
            device_discovery_panel.scan_all_devices_button,
        )

    def _finish_discovery_scan(
        kind: str,
        entries: list[object],
        warning: str,
        error: str,
    ) -> None:  # pragma: no cover - Qt only
        for button in _discovery_scan_buttons():
            button.setEnabled(True)

        thread = discovery_scan_state["thread"]
        if thread is not None:
            thread.quit()
        if error:
            label = {"lan": "LAN", "ble": "BLE"}.get(kind, "LAN/BLE")
            if kind == "ble":
                _set_discovery_status(
                    f"BLE scan unavailable: {error}. Ensure Bluetooth is enabled."
                )
            else:
                _set_discovery_status(f"{label} scan failed: {error}")
            return

        scan_live_count = sum(1 for entry in entries if entry.connected)
        seen_by_address = discovery_seen_entries_state["by_address"]
        for entry in entries:
            if entry.connected:
                seen_by_address[entry.address] = entry
        entries = device_discovery_service.merge_with_config(seen_by_address.values(), config_path)

        label = {"lan": "LAN", "ble": "BLE"}.get(kind, "LAN/BLE")
        configured_count = sum(1 for entry in entries if entry.assigned)
        online_count = sum(1 for entry in entries if entry.connected)
        configured_note = (
            f", including {configured_count} configured device(s)" if configured_count else ""
        )
        if entries:
            status = (
                f"{label} scan completed: found {scan_live_count} live device(s); "
                f"{online_count} online this session{configured_note}."
            )
        else:
            status = f"{label} scan completed: no devices found."
        if warning:
            status = f"{status} {warning}"
        _set_discovery_entries(entries, status=status)

    def _scan_discovery_devices(kind: str) -> None:  # pragma: no cover - Qt only
        if discovery_scan_state["thread"] is not None:
            return
        label = {"lan": "LAN", "ble": "BLE"}.get(kind, "LAN/BLE")
        for button in _discovery_scan_buttons():
            button.setEnabled(False)
        _set_discovery_status(f"Scanning {label} devices…")
        thread = QtCore.QThread()
        worker = _DiscoveryScanWorker(kind)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(discovery_scan_receiver.complete)
        worker.completed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(
            lambda: discovery_scan_state.update({"thread": None, "worker": None})
        )
        discovery_scan_state.update({"thread": thread, "worker": worker})
        thread.start()

    def _identify_discovery_device() -> None:  # pragma: no cover - Qt only
        if discovery_identify_state["thread"] is not None:
            return
        entry = _current_discovery_entry()
        if entry is None:
            _set_discovery_status("Select a discovered device first.")
            return
        discovery_identify_state["key"] = entry.key
        device_discovery_panel.identify_device_button.setEnabled(False)
        _set_discovery_entries(
            discovery_entries_state["entries"],
            status=f"Identifying {entry.address}: hard flashing blue at maximum brightness for 5 seconds…",
        )
        thread = QtCore.QThread()
        worker = _DiscoveryIdentifyWorker(
            entry,
            int(device_discovery_panel.segments_spin.value()),
            device_discovery_panel.transport_combo.currentData(),
            device_discovery_panel.protocol_combo.currentData(),
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(discovery_identify_receiver.complete)
        worker.completed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(
            lambda: discovery_identify_state.update({"thread": None, "worker": None})
        )
        discovery_identify_state.update({"thread": thread, "worker": worker})
        thread.start()

    def _finish_discovery_identify(entry: object, error: str) -> None:  # pragma: no cover - Qt only
        discovery_identify_state["key"] = None
        device_discovery_panel.identify_device_button.setEnabled(True)
        thread = discovery_identify_state["thread"]
        if thread is not None:
            thread.quit()
        status = (
            f"Identify failed for {entry.address}: {error}"
            if error
            else f"Finished flashing {entry.address} blue."
        )
        _set_discovery_entries(discovery_entries_state["entries"], status=status)

    def _show_advanced_device_test() -> None:  # pragma: no cover - Qt only
        if discovery_test_state["thread"] is not None:
            return
        entry = _current_discovery_entry()
        if entry is None:
            _set_discovery_status("Select a discovered device first.")
            return
        dialog = QtWidgets.QDialog(window)
        dialog.setWindowTitle(f"Advanced Test — {entry.name}")
        dialog.setObjectName("advancedDeviceTestDialog")
        layout = QtWidgets.QFormLayout(dialog)
        color_edit = QtWidgets.QLineEdit("#3366ff")
        color_edit.setObjectName("deviceTestColorEdit")
        pattern_combo = QtWidgets.QComboBox()
        patterns = ("solid", "alternate", "rainbow", "walk") if entry.source == "lan" else ("solid", "walk")
        for pattern in patterns:
            pattern_combo.addItem(pattern.title(), pattern)
        pattern_combo.setObjectName("deviceTestPatternCombo")
        duration_spin = QtWidgets.QDoubleSpinBox()
        duration_spin.setRange(0.1, 15.0)
        duration_spin.setValue(3.0)
        duration_spin.setSuffix(" s")
        duration_spin.setObjectName("deviceTestDurationSpin")
        brightness_spin = QtWidgets.QDoubleSpinBox()
        brightness_spin.setRange(0.05, 1.0)
        brightness_spin.setSingleStep(0.05)
        brightness_spin.setValue(0.2)
        brightness_spin.setObjectName("deviceTestBrightnessSpin")
        layout.addRow("Color", color_edit)
        layout.addRow("Pattern", pattern_combo)
        layout.addRow("Duration", duration_spin)
        layout.addRow("Brightness", brightness_spin)
        warning = QtWidgets.QLabel(
            f"Only {escape(entry.name)} ({escape(entry.address)}) will be tested. "
            "The device will be turned off when the test ends."
        )
        warning.setWordWrap(True)
        layout.addRow(warning)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText("Run Test")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        spec = DeviceTestSpec(
            color=color_edit.text().strip(),
            pattern=str(pattern_combo.currentData()),
            duration_seconds=float(duration_spin.value()),
            brightness=float(brightness_spin.value()),
            segments=int(device_discovery_panel.segments_spin.value()),
            transport=str(device_discovery_panel.transport_combo.currentData()),
            protocol=str(device_discovery_panel.protocol_combo.currentData()),
        )
        try:
            spec.validate(entry.source)
        except ValueError as exc:
            _set_discovery_status(str(exc))
            return
        device_discovery_panel.advanced_test_device_button.setEnabled(False)
        _set_discovery_status(
            f"Testing only {entry.address}: {spec.pattern}, {spec.duration_seconds:.1f}s, "
            f"{spec.brightness:.0%} brightness…"
        )
        thread = QtCore.QThread()
        worker = _DiscoveryTestWorker(entry, spec)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(discovery_test_receiver.complete)
        worker.completed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(
            lambda: discovery_test_state.update({"thread": None, "worker": None})
        )
        discovery_test_state.update({"thread": thread, "worker": worker})
        thread.start()

    def _discovery_form_config(entry: object) -> object:  # pragma: no cover - Qt only
        brightness = float(device_discovery_panel.brightness_spin.value())
        return device_discovery_service.assignment_to_config(
            entry,
            name=device_discovery_panel.name_edit.text(),
            device_type=device_discovery_panel.type_combo.currentData(),
            segments=int(device_discovery_panel.segments_spin.value()),
            transport=device_discovery_panel.transport_combo.currentData(),
            protocol=device_discovery_panel.protocol_combo.currentData(),
            role=device_discovery_panel.role_combo.currentData(),
            brightness_scale=brightness,
            x=float(device_discovery_panel.x_spin.value()),
            y=float(device_discovery_panel.y_spin.value()),
            z=float(device_discovery_panel.z_spin.value()),
        )

    def _save_discovery_assignment() -> None:  # pragma: no cover - Qt only
        entry = _current_discovery_entry()
        if entry is None:
            _set_discovery_status("Select a discovered device first.")
            return
        if config_path is None:
            _set_discovery_status(
                "No config path loaded. Open the GUI with --config before saving discovery assignments."
            )
            return
        config = _discovery_form_config(entry)
        try:
            device_discovery_service.upsert_device_config(config_path, config)
            merged = device_discovery_service.merge_with_config(discovery_entries_state["entries"], config_path)
            _set_discovery_entries(merged, status=f"Saved assignment for {config.name} to {config_path.name}.")
            _reload_spatial_scene(status=f"Reloaded spatial layout after assigning {config.name}.")
        except Exception as exc:
            _set_page_error(device_discovery_panel.status_label, f"Could not save assignment: {exc}")

    def _save_all_discovery_assignments() -> None:  # pragma: no cover - Qt only
        if config_path is None:
            _set_discovery_status(
                "No config path loaded. Open the GUI with --config before saving discovery assignments."
            )
            return
        try:
            configs_by_address = {
                config.address: config
                for config in (device_service.load_config(config_path) if config_path.exists() else [])
            }
            entry = _current_discovery_entry()
            if entry is not None:
                config = _discovery_form_config(entry)
                configs_by_address[config.address] = config
            if not configs_by_address:
                _set_discovery_status("No device assignments to save.")
                return
            for config in configs_by_address.values():
                device_discovery_service.upsert_device_config(config_path, config)
            merged = device_discovery_service.merge_with_config(discovery_entries_state["entries"], config_path)
            _set_discovery_entries(
                merged,
                status=f"Saved all {len(configs_by_address)} device assignment(s) to {config_path.name}.",
            )
            _reload_spatial_scene(status=f"Reloaded spatial layout after saving {len(configs_by_address)} assignments.")
        except Exception as exc:
            _set_page_error(device_discovery_panel.status_label, f"Could not save all assignments: {exc}")

    _select_combo_data(queue_panel.baked_playback_combo, settings.baked_playback_mode)

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

    def _set_reactive_profile_override_path(value: str) -> None:
        selected_path = str(value).strip()
        queue_panel.reactive_profile_override_label.setProperty(
            "profilePath",
            selected_path,
        )
        queue_panel.reactive_profile_override_label.setText(
            selected_path or "No profile selected"
        )
        queue_panel.reactive_profile_override_label.setToolTip(selected_path)

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
            mirror=bool(queue_panel.reactive_mirror_combo.currentData()),
            master_brightness=float(queue_panel.reactive_master_brightness_spin.value()),
            auto_cycle=bool(queue_panel.reactive_auto_cycle_check.isChecked()),
            cycle_interval=float(queue_panel.reactive_cycle_interval_spin.value()),
            debug_mood=bool(queue_panel.reactive_debug_mood_check.isChecked()),
            telemetry_dir=queue_panel.reactive_telemetry_dir_edit.text().strip(),
            crossfade_detect=bool(queue_panel.reactive_crossfade_check.isChecked()),
            harmonic_structure_enabled=bool(
                queue_panel.reactive_harmonic_structure_check.isChecked()
            ),
            structure_sensitivity=float(
                queue_panel.reactive_structure_sensitivity_spin.value()
            ),
            debug_harmonics=bool(
                queue_panel.reactive_debug_harmonics_check.isChecked()
            ),
            predictive_analysis_enabled=False,
            predictive_diagnostics_enabled=False,
            predictive_shadow_mode=True,
            predictive_cues_enabled=False,
            predictive_high_impact_cues_enabled=False,
            structure_similarity_enabled=bool(
                queue_panel.reactive_predictive_analysis_check.isChecked()
            ),
            structure_similarity_diagnostics=bool(
                queue_panel.reactive_predictive_diagnostics_check.isChecked()
            ),
            structure_similarity_shadow_mode=bool(
                queue_panel.reactive_predictive_shadow_check.isChecked()
            ),
            structure_bar_actions_enabled=bool(
                queue_panel.reactive_predictive_cues_check.isChecked()
            ),
            structure_phrase_actions_enabled=bool(
                queue_panel.reactive_structure_phrase_actions_check.isChecked()
            ),
            structure_section_actions_enabled=bool(
                queue_panel.reactive_predictive_high_impact_check.isChecked()
            ),
            profile_strategy=str(queue_panel.reactive_profile_strategy_combo.currentData() or "active_profile"),
            profile_override_path=str(
                queue_panel.reactive_profile_override_label.property("profilePath") or ""
            ).strip(),
            show_palette_set=str(queue_panel.reactive_show_palette_set_combo.currentData() or ""),
            rotation_profiles=profiles,
            rotation_interval=float(queue_panel.reactive_rotation_interval_spin.value()),
            auto_palette=bool(queue_panel.reactive_auto_palette_check.isChecked()),
            smart_rotation=bool(queue_panel.reactive_smart_rotation_check.isChecked()),
            chain_blend_seconds=float(queue_panel.reactive_chain_blend_spin.value()),
            auto_palette_seed=(
                int(queue_panel.reactive_auto_palette_seed_spin.value())
                if queue_panel.reactive_auto_palette_seed_check.isChecked()
                else None
            ),
            auto_palette_pool_size=int(queue_panel.reactive_auto_palette_pool_size_spin.value()),
            chain_dwell_range_enabled=bool(
                queue_panel.reactive_chain_dwell_range_check.isChecked()
            ),
            chain_min_dwell_seconds=float(queue_panel.reactive_chain_min_dwell_spin.value()),
            chain_max_dwell_seconds=float(queue_panel.reactive_chain_max_dwell_spin.value()),
        )

    def _update_reactive_configuration_warning() -> tuple[str, ...]:  # pragma: no cover - Qt only
        reactive_settings = _reactive_settings_from_form()
        errors = reactive_settings.validate()
        warning_label = queue_panel.reactive_configuration_warning_label
        warning_label.setVisible(bool(errors))
        warning_label.setText(
            "Invalid Reactive configuration:\n"
            + "\n".join(f"• {message}" for message in errors)
            if errors
            else ""
        )
        invalid_override = (
            reactive_settings.profile_strategy == "override_profile"
            and not reactive_settings.profile_override_path
        )
        override_requested = reactive_settings.profile_strategy == "override_profile"
        invalid_rotation = (
            reactive_settings.profile_strategy in {
                "song_change_rotation",
                "profile_rotation",
                "smart_rotation",
            }
            and not reactive_settings.rotation_profiles
            and not reactive_settings.auto_palette
        )
        queue_panel.reactive_profile_override_label.setStyleSheet(
            "border: 1px solid #dc2626;" if invalid_override else ""
        )
        queue_panel.reactive_profile_override_label.setEnabled(override_requested)
        queue_panel.browse_reactive_profile_button.setEnabled(override_requested)
        queue_panel.reactive_rotation_profiles_edit.setStyleSheet(
            "border: 1px solid #dc2626;" if invalid_rotation else ""
        )
        return errors

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
            queue_panel.reactive_mirror_combo,
            queue_panel.reactive_master_brightness_spin,
            queue_panel.reactive_auto_cycle_check,
            queue_panel.reactive_cycle_interval_spin,
            queue_panel.reactive_debug_mood_check,
            queue_panel.reactive_crossfade_check,
            queue_panel.reactive_harmonic_structure_check,
            queue_panel.reactive_structure_sensitivity_spin,
            queue_panel.reactive_debug_harmonics_check,
            queue_panel.reactive_predictive_analysis_check,
            queue_panel.reactive_predictive_diagnostics_check,
            queue_panel.reactive_predictive_shadow_check,
            queue_panel.reactive_predictive_cues_check,
            queue_panel.reactive_structure_phrase_actions_check,
            queue_panel.reactive_predictive_high_impact_check,
            queue_panel.reactive_telemetry_dir_edit,
            queue_panel.reactive_profile_strategy_combo,
            queue_panel.reactive_profile_override_label,
            queue_panel.browse_reactive_profile_button,
            queue_panel.reactive_show_palette_set_combo,
            queue_panel.reactive_rotation_profiles_edit,
            queue_panel.reactive_rotation_interval_spin,
            queue_panel.reactive_auto_palette_check,
            queue_panel.reactive_smart_rotation_check,
            queue_panel.reactive_chain_blend_spin,
            queue_panel.reactive_auto_palette_seed_check,
            queue_panel.reactive_auto_palette_seed_spin,
            queue_panel.reactive_auto_palette_pool_size_spin,
            queue_panel.reactive_chain_dwell_range_check,
            queue_panel.reactive_chain_min_dwell_spin,
            queue_panel.reactive_chain_max_dwell_spin,
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
        _select_combo_data(queue_panel.reactive_mirror_combo, settings_state.mirror)
        queue_panel.reactive_master_brightness_spin.setValue(settings_state.master_brightness)
        queue_panel.reactive_auto_cycle_check.setChecked(settings_state.auto_cycle)
        queue_panel.reactive_cycle_interval_spin.setValue(settings_state.cycle_interval)
        queue_panel.reactive_debug_mood_check.setChecked(settings_state.debug_mood)
        queue_panel.reactive_crossfade_check.setChecked(settings_state.crossfade_detect)
        queue_panel.reactive_harmonic_structure_check.setChecked(
            settings_state.harmonic_structure_enabled
        )
        queue_panel.reactive_structure_sensitivity_spin.setValue(
            settings_state.structure_sensitivity
        )
        queue_panel.reactive_debug_harmonics_check.setChecked(
            settings_state.debug_harmonics
        )
        queue_panel.reactive_predictive_analysis_check.setChecked(
            settings_state.structure_similarity_enabled
        )
        queue_panel.reactive_predictive_diagnostics_check.setChecked(
            settings_state.structure_similarity_diagnostics
        )
        queue_panel.reactive_predictive_shadow_check.setChecked(
            settings_state.structure_similarity_shadow_mode
        )
        queue_panel.reactive_predictive_cues_check.setChecked(
            settings_state.structure_bar_actions_enabled
        )
        queue_panel.reactive_structure_phrase_actions_check.setChecked(
            settings_state.structure_phrase_actions_enabled
        )
        queue_panel.reactive_predictive_high_impact_check.setChecked(
            settings_state.structure_section_actions_enabled
        )
        queue_panel.reactive_telemetry_dir_edit.setText(settings_state.telemetry_dir)
        _select_combo_data(queue_panel.reactive_profile_strategy_combo, settings_state.profile_strategy)
        _set_reactive_profile_override_path(settings_state.profile_override_path)
        if (
            settings_state.show_palette_set
            and queue_panel.reactive_show_palette_set_combo.findData(
                settings_state.show_palette_set
            ) < 0
        ):
            queue_panel.reactive_show_palette_set_combo.addItem(
                settings_state.show_palette_set,
                settings_state.show_palette_set,
            )
        _select_combo_data(queue_panel.reactive_show_palette_set_combo, settings_state.show_palette_set)
        queue_panel.reactive_rotation_profiles_edit.setText(", ".join(settings_state.rotation_profiles))
        queue_panel.reactive_rotation_interval_spin.setValue(settings_state.rotation_interval)
        queue_panel.reactive_auto_palette_check.setChecked(settings_state.auto_palette)
        queue_panel.reactive_smart_rotation_check.setChecked(settings_state.smart_rotation)
        queue_panel.reactive_chain_blend_spin.setValue(settings_state.chain_blend_seconds)
        queue_panel.reactive_auto_palette_seed_check.setChecked(
            settings_state.auto_palette_seed is not None
        )
        if settings_state.auto_palette_seed is not None:
            queue_panel.reactive_auto_palette_seed_spin.setValue(
                settings_state.auto_palette_seed
            )
        queue_panel.reactive_auto_palette_seed_spin.setEnabled(
            settings_state.auto_palette_seed is not None
        )
        queue_panel.reactive_auto_palette_pool_size_spin.setValue(
            settings_state.auto_palette_pool_size
        )
        queue_panel.reactive_chain_dwell_range_check.setChecked(
            settings_state.chain_dwell_range_enabled
        )
        queue_panel.reactive_chain_min_dwell_spin.setValue(
            settings_state.chain_min_dwell_seconds
        )
        queue_panel.reactive_chain_max_dwell_spin.setValue(
            settings_state.chain_max_dwell_seconds
        )
        for widget in widgets:
            widget.blockSignals(False)
        _update_reactive_configuration_warning()

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

    def _snap_spatial_axis(value: float) -> float:
        return round(_clamp_axis(value) / SPATIAL_STEP) * SPATIAL_STEP

    def _current_spatial_edit_mode() -> str:
        return str(spatial_edit_mode_combo.currentData() or "move_strip")

    def _set_spatial_edit_mode(mode: str, *, status: str | None = None) -> str:
        target_mode = mode if mode in {"move_strip", "orient_strip", "fine_tune"} else "move_strip"
        if target_mode != "fine_tune":
            spatial_object_mode_state["value"] = target_mode
        for index in range(spatial_edit_mode_combo.count()):
            if spatial_edit_mode_combo.itemData(index) == target_mode:
                spatial_edit_mode_combo.setCurrentIndex(index)
                break
        spatial_canvas.set_individual_node_editing(target_mode == "fine_tune")
        _refresh_spatial_controls()
        if status is not None:
            spatial_status_label.setText(status)
        return target_mode

    def _spatial_navigation_nodes() -> list[SceneNode]:
        nodes_snapshot = spatial_controller.snapshot()
        if _current_spatial_edit_mode() == "fine_tune":
            return nodes_snapshot
        navigation_nodes: list[SceneNode] = []
        seen_strips: set[str] = set()
        for node in nodes_snapshot:
            if node.is_section:
                if node.chain_key in seen_strips:
                    continue
                seen_strips.add(node.chain_key)
            navigation_nodes.append(node)
        return navigation_nodes

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
        canvases = [simulation_canvas, show_simulation_canvas]
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
            node_colors = preview_snapshot.get(
                "display_node_colors",
                preview_snapshot.get("node_colors", {}),
            )
            frozen = bool(queue_panel.simulation_freeze_check.isChecked())
            if isinstance(node_colors, dict) and not frozen:
                merged_colors = dict(simulation_frame_state["node_colors"])
                merged_colors.update(
                    {
                        str(key): str(value)
                        for key, value in node_colors.items()
                    }
                )
                simulation_frame_state["node_colors"] = merged_colors
            frame = preview_snapshot.get("frame_diagnostics", {})
            frame = frame if isinstance(frame, dict) else {}
            actions = frame.get("structural_actions", ())
            last_action = actions[-1] if isinstance(actions, (list, tuple)) and actions else {}
            last_action = last_action if isinstance(last_action, dict) else {}
            flags = []
            if frame.get("unexpected_achromatic"):
                flags.append("UNEXPECTED ACHROMATIC")
            if frame.get("all_black"):
                flags.append("ALL BLACK")
            parity = preview_snapshot.get("hardware_mirror_parity", {})
            parity = parity if isinstance(parity, dict) else {}
            if parity.get("available"):
                flags.append(
                    "MIRROR MATCH"
                    if parity.get("matches")
                    else "MIRROR MISMATCH"
                )
            queue_panel.simulation_frame_diagnostics_label.setText(
                "Frame{}: mode={} color={} intensity={:.3f} "
                "accent={:.2f}{} action={} spatial={} RGB={}..{}{}".format(
                    " (frozen)" if frozen else "",
                    frame.get("effective_render_mode") or "unknown",
                    frame.get("base_color") or "none",
                    float(frame.get("base_intensity", 0.0) or 0.0),
                    float(frame.get("beat_accent", 0.0) or 0.0),
                    " downbeat" if frame.get("downbeat") else "",
                    last_action.get("outcome", "none"),
                    "changed" if frame.get("spatial_changed") else "same",
                    int(frame.get("min_rgb_level", 0) or 0),
                    int(frame.get("max_rgb_level", 0) or 0),
                    f" · {' · '.join(flags)}" if flags else "",
                )
            )
        for canvas in _simulation_canvases():
            _apply_preview_canvas_state(canvas)

    def _sync_preview_combo(source_combo: object, target_combo: object) -> None:
        target_index = target_combo.findData(source_combo.currentData())
        if target_index >= 0 and target_combo.currentIndex() != target_index:
            target_combo.setCurrentIndex(target_index)
        _render_preview_simulation()

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

    def _set_reactive_chord_surface_data(
        current: str = "",
        previous=(),
        *,
        status: str = "",
    ) -> None:  # pragma: no cover - Qt only
        normalized = (
            str(current or "").strip(),
            tuple(
                str(chord).strip()
                for chord in (previous or ())
                if str(chord).strip()
            )[-3:],
            str(status or "").strip(),
        )
        reactive_diagnostic_data["chord"] = normalized
        queue_panel.reactive_chord_history_view.set_chord_history(
            normalized[0],
            normalized[1],
            status=normalized[2],
        )
        mirror = reactive_diagnostic_window_state["chord"]["view"]
        if mirror is not None:
            mirror.set_chord_history(
                normalized[0],
                normalized[1],
                status=normalized[2],
            )

    def _set_reactive_harmonic_surface_data(
        spectrum=(),
        chroma=(),
        *,
        chord: str = "",
        chord_confidence: float = 0.0,
        chord_tones=(),
        root_note: str = "",
        non_chord_tones=(),
        predicted_chord: str = "",
        prediction_seconds: float | None = None,
        prediction_confidence: float = 0.0,
        prediction_mismatch=None,
    ) -> None:  # pragma: no cover - Qt only
        normalized = (
            tuple(spectrum or ()),
            tuple(chroma or ()),
            str(chord or ""),
            float(chord_confidence or 0.0),
            tuple(chord_tones or ()),
            str(root_note or ""),
            tuple(non_chord_tones or ()),
            str(predicted_chord or ""),
            (
                float(prediction_seconds)
                if prediction_seconds is not None
                else None
            ),
            float(prediction_confidence or 0.0),
            dict(prediction_mismatch or {}),
        )
        reactive_diagnostic_data["harmonic"] = normalized
        queue_panel.reactive_harmonic_debug_view.set_debug_data(
            normalized[0],
            normalized[1],
            chord=normalized[2],
            chord_confidence=normalized[3],
            chord_tones=normalized[4],
            root_note=normalized[5],
            non_chord_tones=normalized[6],
            predicted_chord=normalized[7],
            prediction_seconds=normalized[8],
            prediction_confidence=normalized[9],
            prediction_mismatch=normalized[10],
        )
        mirror = reactive_diagnostic_window_state["harmonic"]["view"]
        if mirror is not None:
            mirror.set_debug_data(
                normalized[0],
                normalized[1],
                chord=normalized[2],
                chord_confidence=normalized[3],
                chord_tones=normalized[4],
                root_note=normalized[5],
                non_chord_tones=normalized[6],
                predicted_chord=normalized[7],
                prediction_seconds=normalized[8],
                prediction_confidence=normalized[9],
                prediction_mismatch=normalized[10],
            )

    def _clear_reactive_diagnostic_window(
        kind: str,
        diagnostic_window,
    ) -> None:  # pragma: no cover - Qt only
        state = reactive_diagnostic_window_state[kind]
        if state["window"] is diagnostic_window:
            state["window"] = None
            state["view"] = None
            state["exit_button"] = None
            state["escape_shortcut"] = None
            state["mode_label"] = None
            state["mode"] = ""

    def _return_to_reactive_canvas(
        kind: str,
    ) -> None:  # pragma: no cover - Qt only
        if not window.isVisible():
            return
        tabs.setCurrentWidget(queue_panel.widget)
        _set_live_mode("reactive", announce=False)
        if window.isMinimized():
            window.showNormal()
        else:
            window.show()
        window.raise_()
        window.activateWindow()
        target = (
            queue_panel.reactive_chord_history_view
            if kind == "chord"
            else queue_panel.reactive_harmonic_debug_view
        )
        target.setFocus(
            QtCore.Qt.FocusReason.OtherFocusReason
        )

    def _ensure_reactive_diagnostic_window(
        kind: str,
    ) -> object:  # pragma: no cover - Qt only
        state = reactive_diagnostic_window_state[kind]
        existing = state["window"]
        if existing is not None:
            return existing

        diagnostic_window = QtWidgets.QMainWindow(window)
        diagnostic_window.setAttribute(
            QtCore.Qt.WidgetAttribute.WA_DeleteOnClose,
            True,
        )
        if kind == "chord":
            diagnostic_window.setWindowTitle(
                "DreamSync Reactive · Chord History"
            )
            diagnostic_window.setObjectName(
                "reactiveChordHistoryPopoutWindow"
            )
            diagnostic_view = build_reactive_chord_history_view(
                qt_modules,
                object_name="reactiveChordHistoryPopoutView",
            )
            diagnostic_window.resize(960, 440)
            diagnostic_window.setMinimumSize(520, 260)
        else:
            diagnostic_window.setWindowTitle(
                "DreamSync Reactive · FFT + Chord Wheel"
            )
            diagnostic_window.setObjectName(
                "reactiveHarmonicPopoutWindow"
            )
            diagnostic_view = build_reactive_harmonic_debug_view(
                qt_modules,
                object_name="reactiveHarmonicPopoutView",
            )
            diagnostic_window.resize(1180, 760)
            diagnostic_window.setMinimumSize(640, 400)
        diagnostic_host = QtWidgets.QWidget()
        diagnostic_host.setObjectName(
            f"reactive{kind.title()}PopoutHost"
        )
        diagnostic_layout = QtWidgets.QVBoxLayout(diagnostic_host)
        diagnostic_layout.setContentsMargins(8, 8, 8, 8)
        diagnostic_header = QtWidgets.QHBoxLayout()
        mode_label = QtWidgets.QLabel("Popped out")
        mode_label.setObjectName(
            f"reactive{kind.title()}PopoutModeLabel"
        )
        mode_label.setStyleSheet(
            "color: #94a3b8; font-weight: 600;"
        )
        diagnostic_header.addWidget(mode_label)
        diagnostic_header.addStretch(1)
        exit_button = QtWidgets.QPushButton("Exit Pop-out Mode")
        exit_button.setObjectName(
            f"reactive{kind.title()}ExitPopoutButton"
        )
        exit_button.setToolTip(
            "Close this window and return focus to the main Reactive "
            "canvas."
        )
        diagnostic_header.addWidget(exit_button)
        diagnostic_layout.addLayout(diagnostic_header)
        diagnostic_layout.addWidget(diagnostic_view, 1)
        diagnostic_window.setCentralWidget(diagnostic_host)
        state["window"] = diagnostic_window
        state["view"] = diagnostic_view
        state["exit_button"] = exit_button
        state["mode_label"] = mode_label
        escape_shortcut = QtGui.QShortcut(
            QtGui.QKeySequence("Escape"),
            diagnostic_window,
        )
        escape_shortcut.setContext(
            QtCore.Qt.ShortcutContext.WindowShortcut
        )
        escape_shortcut.activated.connect(diagnostic_window.close)
        state["escape_shortcut"] = escape_shortcut

        original_close_event = diagnostic_window.closeEvent
        original_key_press_event = diagnostic_window.keyPressEvent

        def _on_close(event) -> None:
            _clear_reactive_diagnostic_window(
                kind,
                diagnostic_window,
            )
            original_close_event(event)
            if event.isAccepted():
                QtCore.QTimer.singleShot(
                    0,
                    lambda: _return_to_reactive_canvas(kind),
                )

        def _on_key_press(event) -> None:
            if (
                event.key() == QtCore.Qt.Key.Key_Escape
            ):
                event.accept()
                diagnostic_window.close()
                return
            original_key_press_event(event)

        diagnostic_window.closeEvent = _on_close
        diagnostic_window.keyPressEvent = _on_key_press
        exit_button.clicked.connect(diagnostic_window.close)
        if kind == "chord":
            current, previous, status = reactive_diagnostic_data["chord"]
            diagnostic_view.set_chord_history(
                current,
                previous,
                status=status,
            )
        else:
            (
                spectrum,
                chroma,
                chord,
                confidence,
                tones,
                root_note,
                non_chord_tones,
                predicted_chord,
                prediction_seconds,
                prediction_confidence,
                prediction_mismatch,
            ) = (
                reactive_diagnostic_data["harmonic"]
            )
            diagnostic_view.set_debug_data(
                spectrum,
                chroma,
                chord=chord,
                chord_confidence=confidence,
                chord_tones=tones,
                root_note=root_note,
                non_chord_tones=non_chord_tones,
                predicted_chord=predicted_chord,
                prediction_seconds=prediction_seconds,
                prediction_confidence=prediction_confidence,
                prediction_mismatch=prediction_mismatch,
            )
        return diagnostic_window

    def _show_reactive_diagnostic_window(
        kind: str,
        *,
        fullscreen: bool,
    ) -> None:  # pragma: no cover - Qt only
        diagnostic_window = _ensure_reactive_diagnostic_window(kind)
        state = reactive_diagnostic_window_state[kind]
        state["mode"] = "fullscreen" if fullscreen else "popout"
        diagnostic_window.setProperty(
            "diagnosticMode",
            state["mode"],
        )
        mode_label = state["mode_label"]
        if mode_label is not None:
            mode_label.setText(
                "Fullscreen · Escape returns to Reactive canvas"
                if fullscreen
                else "Popped out · window is independently resizable"
            )
        if fullscreen:
            diagnostic_window.showFullScreen()
        else:
            if diagnostic_window.isFullScreen():
                diagnostic_window.showNormal()
            diagnostic_window.show()
        diagnostic_window.raise_()
        diagnostic_window.activateWindow()

    def _refresh_spatial_controls() -> None:
        spatial_signal_block["value"] = True
        spatial_node_list.clear()
        nodes_snapshot = spatial_controller.snapshot()
        selected_node = spatial_controller.nodes.get(spatial_controller.selected_key)
        selected_chain_key = (
            selected_node.chain_key
            if selected_node is not None and selected_node.is_section
            else ""
        )
        for node in _spatial_navigation_nodes():
            label = (
                f"{node.physical_name} [{node.section_count} sections]"
                if node.is_section and _current_spatial_edit_mode() != "fine_tune"
                else _format_spatial_node_label(node)
            )
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, node.key)
            spatial_node_list.addItem(item)
            if node.selected or (
                node.is_section
                and _current_spatial_edit_mode() != "fine_tune"
                and node.chain_key == selected_chain_key
            ):
                item.setSelected(True)
        if selected_node is None:
            selected_spatial_label.setText("Selected node: none")
            spatial_x_spin.setValue(0.0)
            spatial_y_spin.setValue(0.0)
            spatial_z_spin.setValue(0.0)
        else:
            selected_chain = spatial_controller.chain_for_node(selected_node.key)
            edit_mode = _current_spatial_edit_mode()
            chain_mode = bool(selected_chain and edit_mode in {"move_strip", "orient_strip"})
            selected_spatial_label.setText(
                (
                    f"Selected strip: {selected_node.physical_name} | "
                    f"section {(selected_node.section_index or 0) + 1}/{selected_node.section_count}"
                    if selected_chain and edit_mode != "fine_tune"
                    else f"Selected node: {_format_spatial_node_label(selected_node)}"
                )
                + f" | address={selected_node.address}"
            )
            if chain_mode:
                x, y, z = spatial_controller.selected_center()
            else:
                x, y, z = selected_node.x, selected_node.y, selected_node.z
            spatial_x_spin.setValue(x)
            spatial_y_spin.setValue(y)
            spatial_z_spin.setValue(z)
            apply_spatial_line_button.setEnabled(bool(selected_chain))
            reverse_spatial_strip_button.setEnabled(bool(selected_chain))
            spatial_direction_combo.setEnabled(bool(selected_chain))
        spatial_direction_widget.setVisible(
            _current_spatial_edit_mode() == "orient_strip"
            and bool(selected_node is not None and selected_node.is_section)
        )
        if selected_node is None:
            apply_spatial_line_button.setEnabled(False)
            reverse_spatial_strip_button.setEnabled(False)
            spatial_direction_combo.setEnabled(False)
        validations = spatial_controller.validate_layout()
        invalid = [validation for validation in validations if not validation.valid]
        if not validations:
            spatial_validation_label.setText("Layout validity: no strip chains loaded.")
        elif not invalid:
            spatial_validation_label.setText(
                f"Layout valid: {len(validations)} strip chain(s) are physically connected."
            )
        else:
            first = invalid[0]
            spatial_validation_label.setText(
                f"Layout invalid: {len(invalid)} strip chain(s) need attention. "
                f"{first.label}: {first.errors[0]}"
            )
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
        nodes_snapshot = _spatial_navigation_nodes()
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
        if (
            _current_spatial_edit_mode() != "fine_tune"
            and spatial_controller.selected_key not in keys
        ):
            selected_node = spatial_controller.nodes.get(spatial_controller.selected_key)
            if selected_node is not None and selected_node.is_section:
                representative = next(
                    (
                        node
                        for node in nodes_snapshot
                        if node.is_section and node.chain_key == selected_node.chain_key
                    ),
                    None,
                )
                if representative is not None:
                    current_index = keys.index(representative.key)
                    next_key = keys[(current_index + step) % len(keys)]
        node = spatial_controller.nodes[next_key]
        _select_spatial_node(next_key, status=f"Selected {_format_spatial_node_label(node)} via keyboard.")

    def _update_spatial_node(key: str, *, x: float | None = None, y: float | None = None, z: float | None = None, status: str) -> None:
        node = spatial_controller.nodes[key]
        chain = spatial_controller.chain_for_node(key)
        edit_mode = _current_spatial_edit_mode()
        try:
            if chain and edit_mode in {"move_strip", "orient_strip"}:
                current_x, current_y, current_z = spatial_controller.selected_center()
                spatial_controller.set_chain_center(
                    node.chain_key,
                    _clamp_axis(current_x if x is None else x),
                    _clamp_axis(current_y if y is None else y),
                    _clamp_axis(current_z if z is None else z),
                )
            else:
                snap = node.is_section and edit_mode == "fine_tune"
                normalize = _snap_spatial_axis if snap else _clamp_axis
                spatial_controller.update_position(
                    key,
                    x=normalize(node.x if x is None else x),
                    y=normalize(node.y if y is None else y),
                    z=normalize(node.z if z is None else z),
                )
        except ValueError as exc:
            _set_page_error(spatial_status_label, exc)
            _refresh_spatial_controls()
            return
        _select_spatial_node(key, status=status)

    def _nudge_selected_spatial_node(direction: int, axis_name: str | None = None) -> None:
        key = spatial_controller.selected_key
        if not key or key not in spatial_controller.nodes:
            return
        step = SPATIAL_STEP if direction >= 0 else -SPATIAL_STEP
        axis_name = axis_name or _current_drag_axis()
        node = spatial_controller.nodes[key]
        chain_mode = bool(
            spatial_controller.chain_for_node(key)
            and _current_spatial_edit_mode() in {"move_strip", "orient_strip"}
        )
        base_x, base_y, base_z = spatial_controller.selected_center() if chain_mode else (node.x, node.y, node.z)
        if axis_name == "x":
            _update_spatial_node(
                key,
                x=base_x + step,
                status=f"Nudged X (left/right) for {_format_spatial_node_label(node)}.",
            )
            return
        if axis_name == "y":
            _update_spatial_node(
                key,
                y=base_y + step,
                status=f"Nudged Y height for {_format_spatial_node_label(node)}.",
            )
            return
        _update_spatial_node(
            key,
            z=base_z + step,
            status=f"Nudged Z depth for {_format_spatial_node_label(node)}.",
        )

    def _save_spatial_scene() -> None:  # pragma: no cover - Qt only
        if config_path is None or not config_path.exists():
            spatial_status_label.setText("No config loaded for spatial editing.")
            return
        invalid = [validation for validation in spatial_controller.validate_layout() if not validation.valid]
        if invalid:
            first = invalid[0]
            spatial_status_label.setText(
                f"Save blocked: {first.label} is invalid. {first.errors[0]}"
            )
            _refresh_spatial_controls()
            return
        placements = {
            key: (node.x, node.y, node.z)
            for key, node in spatial_controller.nodes.items()
        }
        try:
            device_service.save_scene(config_path, placements)
        except Exception as exc:
            _set_page_error(spatial_status_label, f"Could not save spatial config: {exc}")
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
        chain_mode = bool(
            spatial_controller.chain_for_node(key)
            and _current_spatial_edit_mode() in {"move_strip", "orient_strip"}
        )
        base_x, base_y, base_z = spatial_controller.selected_center() if chain_mode else (node.x, node.y, node.z)
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
                next_x = base_x + (delta_x / 140.0)
            else:
                next_x = base_x + (delta_x / 140.0)
            _update_spatial_node(key, x=next_x, status=f"Adjusted X (left/right) for {_format_spatial_node_label(node)}.")
            return
        if axis_name == "y":
            next_y = base_y - (delta_y / 120.0)
            _update_spatial_node(key, y=next_y, status=f"Adjusted Y height for {_format_spatial_node_label(node)}.")
            return
        if view_mode == "room":
            z_from_x = delta_x / 64.0
            z_from_y = delta_y / 28.0
            next_z = base_z + ((z_from_x + z_from_y) / 2.0)
        elif view_mode == "xz":
            next_z = base_z - (delta_y / 120.0)
        else:
            next_z = base_z + (delta_x / 140.0)
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

    def _apply_spatial_line() -> None:  # pragma: no cover - Qt only
        key = spatial_controller.selected_key
        node = spatial_controller.nodes.get(key)
        if node is None or not node.is_section:
            spatial_status_label.setText("Select a strip section before applying a direction.")
            return
        center = (
            float(spatial_x_spin.value()),
            float(spatial_y_spin.value()),
            float(spatial_z_spin.value()),
        )
        direction = str(spatial_direction_combo.currentData() or "x+")
        try:
            spatial_controller.orient_chain(node.chain_key, direction, center=center)
        except ValueError as exc:
            _set_page_error(spatial_status_label, exc)
            return
        _select_spatial_node(
            key,
            status=f"Oriented {node.physical_name} along {direction.upper()} from its centerpoint.",
        )

    def _reverse_spatial_strip() -> None:  # pragma: no cover - Qt only
        key = spatial_controller.selected_key
        node = spatial_controller.nodes.get(key)
        if node is None or not node.is_section:
            spatial_status_label.setText("Select a strip section before reversing installation order.")
            return
        spatial_controller.reverse_chain(node.chain_key)
        _select_spatial_node(key, status=f"Reversed section order for {node.physical_name}.")

    def _toggle_spatial_object_mode() -> None:  # pragma: no cover - Qt only
        node = spatial_controller.nodes.get(spatial_controller.selected_key)
        if node is None or not node.is_section:
            spatial_status_label.setText("Select a strip before toggling its edit mode.")
            return
        current_mode = _current_spatial_edit_mode()
        if current_mode == "fine_tune":
            spatial_status_label.setText("Press Enter to leave Fine Tune Dots before toggling object mode.")
            return
        target = "orient_strip" if current_mode == "move_strip" else "move_strip"
        label = "Orient Strip" if target == "orient_strip" else "Move Strip"
        _set_spatial_edit_mode(target, status=f"Edit mode: {label}.")

    def _toggle_spatial_fine_tune() -> None:  # pragma: no cover - Qt only
        node = spatial_controller.nodes.get(spatial_controller.selected_key)
        if node is None or not node.is_section:
            spatial_status_label.setText("Select a strip before entering Fine Tune Dots.")
            return
        if _current_spatial_edit_mode() == "fine_tune":
            restored_mode = spatial_object_mode_state["value"]
            label = "Orient Strip" if restored_mode == "orient_strip" else "Move Strip"
            _set_spatial_edit_mode(restored_mode, status=f"Exited Fine Tune Dots. Edit mode: {label}.")
            return
        _set_spatial_edit_mode("fine_tune", status="Entered individual node editing. Tab now selects individual sections.")

    def _cycle_spatial_direction() -> None:  # pragma: no cover - Qt only
        node = spatial_controller.nodes.get(spatial_controller.selected_key)
        if node is None or not node.is_section:
            spatial_status_label.setText("Select a strip before changing direction.")
            return
        if _current_spatial_edit_mode() != "orient_strip":
            _set_spatial_edit_mode("orient_strip")
        next_index = (spatial_direction_combo.currentIndex() + 1) % spatial_direction_combo.count()
        spatial_direction_combo.setCurrentIndex(next_index)
        spatial_status_label.setText(
            f"Strip direction: {str(spatial_direction_combo.currentData()).upper()}."
        )

    def _apply_spatial_line_shortcut() -> None:  # pragma: no cover - Qt only
        if _current_spatial_edit_mode() != "orient_strip":
            spatial_status_label.setText("Switch to Orient Strip mode before applying a line.")
            return
        _apply_spatial_line()

    def _on_spatial_edit_mode_changed() -> None:  # pragma: no cover - Qt only
        mode = _current_spatial_edit_mode()
        if mode != "fine_tune":
            spatial_object_mode_state["value"] = mode
        spatial_canvas.set_individual_node_editing(mode == "fine_tune")
        _refresh_spatial_controls()

    def _cycle_main_tab(step: int) -> None:  # pragma: no cover - Qt only
        if tabs.count() <= 0:
            return
        tabs.setCurrentIndex((tabs.currentIndex() + step) % tabs.count())

    def _on_spatial_selection_step_requested(step: int) -> None:  # pragma: no cover - Qt only
        _step_selected_spatial_node(step if step != 0 else 1)

    def _on_spatial_axis_nudge_requested(axis_name: str, direction: int) -> None:  # pragma: no cover - Qt only
        _nudge_selected_spatial_node(direction if direction != 0 else 1, axis_name)

    def _on_spatial_view_cycle_requested(view_mode: str) -> None:  # pragma: no cover - Qt only
        selected_view = _set_view_mode(view_mode)
        spatial_status_label.setText(f"Switched spatial view to {selected_view.upper()}.")

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

    def _show_cue_layer_target_from_descriptor(layer: dict[str, object]) -> str:
        def _point_float(point: dict[str, object], key: str, default: float) -> float:
            raw = point.get(key)
            if raw is None:
                return default
            try:
                return float(raw)
            except (TypeError, ValueError):
                return default

        category = str(layer.get("layer_category", "")).strip().lower()
        direction = layer.get("direction")
        origin = layer.get("origin")
        extent = layer.get("extent")
        if category == "slice" and isinstance(direction, dict):
            vector = (
                _point_float(direction, "x", 0.0),
                _point_float(direction, "y", 0.0),
                _point_float(direction, "z", 0.0),
            )
            if abs(vector[0]) >= abs(vector[1]) and abs(vector[0]) >= abs(vector[2]):
                return "left_to_right" if vector[0] >= 0 else "right_to_left"
            if abs(vector[1]) >= abs(vector[2]):
                return "bottom_to_top" if vector[1] >= 0 else "top_to_bottom"
            return "front_to_back" if vector[2] >= 0 else "back_to_front"
        if category == "expand" and isinstance(origin, dict):
            x = _point_float(origin, "x", 0.0)
            z = _point_float(origin, "z", 0.0)
            if x <= -0.5:
                return "left"
            if x >= 0.5:
                return "right"
            if z <= -0.5:
                return "front"
            if z >= 0.5:
                return "back"
            return "center"
        if category == "static" and isinstance(extent, dict):
            min_point = extent.get("min")
            max_point = extent.get("max")
            if isinstance(min_point, dict) and isinstance(max_point, dict):
                min_y = _point_float(min_point, "y", -1.0)
                max_y = _point_float(max_point, "y", 1.0)
                min_x = _point_float(min_point, "x", -1.0)
                max_x = _point_float(max_point, "x", 1.0)
                min_z = _point_float(min_point, "z", -1.0)
                max_z = _point_float(max_point, "z", 1.0)
                if min_y >= 0.0:
                    return "top"
                if max_y <= 0.0:
                    return "floor"
                if max_x <= 0.0:
                    return "left"
                if min_x >= 0.0:
                    return "right"
                if max_z <= 0.0:
                    return "front"
                if min_z >= 0.0:
                    return "back"
        return ""

    def _show_cue_layer_state_from_params(
        params: dict[str, object],
        eq_route: dict[str, object],
        instrument_route: dict[str, object],
    ) -> dict[str, object]:
        layer = (
            instrument_route.get("effect_layer")
            or eq_route.get("effect_layer")
            or params.get("effect_layer")
        )
        layer_mapping = dict(layer) if isinstance(layer, dict) else {}

        def _float_value(raw: object, default: float) -> float:
            try:
                return float(raw)
            except (TypeError, ValueError):
                return default

        def _int_value(raw: object, default: int) -> int:
            try:
                return int(raw)
            except (TypeError, ValueError):
                return default

        priority = (
            instrument_route.get("layer_priority")
            or eq_route.get("layer_priority")
            or params.get("layer_priority")
            or 0
        )
        return {
            "layer_category": str(layer_mapping.get("layer_category", "") or ""),
            "layer_target": _show_cue_layer_target_from_descriptor(layer_mapping),
            "layer_trigger": str(layer_mapping.get("trigger_mode", "") or ""),
            "layer_falloff": str(layer_mapping.get("falloff", "") or ""),
            "layer_thickness": _float_value(layer_mapping.get("thickness"), 0.25),
            "layer_speed": _float_value(layer_mapping.get("speed_units_per_second"), 1.0),
            "layer_priority": _int_value(priority, 0),
        }

    def _layer_extent_for_target(target: str) -> dict[str, dict[str, float]]:
        bounds = {
            "whole_room": ((-1.0, -1.0, -1.0), (1.0, 1.0, 1.0)),
            "top": ((-1.0, 0.0, -1.0), (1.0, 1.0, 1.0)),
            "floor": ((-1.0, -1.0, -1.0), (1.0, 0.0, 1.0)),
            "left": ((-1.0, -1.0, -1.0), (0.0, 1.0, 1.0)),
            "right": ((0.0, -1.0, -1.0), (1.0, 1.0, 1.0)),
            "front": ((-1.0, -1.0, -1.0), (1.0, 1.0, 0.0)),
            "back": ((-1.0, -1.0, 0.0), (1.0, 1.0, 1.0)),
        }
        min_point, max_point = bounds.get(target, bounds["whole_room"])
        return {
            "min": {"x": min_point[0], "y": min_point[1], "z": min_point[2]},
            "max": {"x": max_point[0], "y": max_point[1], "z": max_point[2]},
        }

    def _layer_direction_for_target(target: str) -> dict[str, float]:
        directions = {
            "right_to_left": {"x": -1.0, "y": 0.0, "z": 0.0},
            "front_to_back": {"x": 0.0, "y": 0.0, "z": 1.0},
            "back_to_front": {"x": 0.0, "y": 0.0, "z": -1.0},
            "bottom_to_top": {"x": 0.0, "y": 1.0, "z": 0.0},
            "top_to_bottom": {"x": 0.0, "y": -1.0, "z": 0.0},
        }
        return directions.get(target, {"x": 1.0, "y": 0.0, "z": 0.0})

    def _layer_origin_for_target(target: str) -> dict[str, float]:
        origins = {
            "left": {"x": -1.0, "y": 0.0, "z": 0.0},
            "right": {"x": 1.0, "y": 0.0, "z": 0.0},
            "front": {"x": 0.0, "y": 0.0, "z": -1.0},
            "back": {"x": 0.0, "y": 0.0, "z": 1.0},
        }
        return origins.get(target, {"x": 0.0, "y": 0.0, "z": 0.0})

    def _show_cue_effect_layer_from_controls(
        *,
        category: str,
        target: str,
        trigger: str,
        falloff: str,
        thickness: float,
        speed: float,
        render_mode: str,
        palette: tuple[str, ...],
        color_bias: str,
    ) -> dict[str, object] | None:
        category = category.strip().lower()
        if category not in SHOW_CUE_LAYER_CATEGORY_OPTIONS:
            return None
        target = target.strip().lower() or (
            "whole_room" if category == "static" else "center" if category == "expand" else "left_to_right"
        )
        layer: dict[str, object] = {
            "layer_category": category,
            "effect_mode": render_mode or "solid",
            "trigger_mode": trigger if trigger in SHOW_CUE_LAYER_TRIGGER_OPTIONS else "continuous",
            "thickness": max(0.01, float(thickness)),
            "speed_units_per_second": max(0.0, float(speed)),
            "falloff": falloff if falloff in SHOW_CUE_LAYER_FALLOFF_OPTIONS else "linear",
        }
        if palette:
            layer["palette"] = list(palette)
        if color_bias:
            layer["color_bias"] = color_bias
        if category == "static":
            layer["origin"] = {"x": 0.0, "y": 0.0, "z": 0.0}
            layer["extent"] = _layer_extent_for_target(target)
        elif category == "slice":
            layer["origin"] = {"x": 0.0, "y": 0.0, "z": 0.0}
            layer["direction"] = _layer_direction_for_target(target)
        else:
            layer["origin"] = _layer_origin_for_target(target)
            layer["radius"] = 0.0
        return layer

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

        known_layer_keys = {"effect_layer", "layer_priority"}
        known_eq_keys = {"band", "when", "intensity_boost", "spatial_preset", "color_bias"} | known_layer_keys
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
        } | known_layer_keys
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

        layer_state = _show_cue_layer_state_from_params(params, eq_route, instrument_route)
        params.pop("effect_layer", None)
        params.pop("layer_priority", None)

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
            **layer_state,
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
            f"Track: {audio_path.name if audio_path else 'none'}"
        )
        queue_panel.show_editor_path_label.setText(
            f"Show file: {show_path.name if show_path else 'unsaved'}"
        )

    def _set_show_editor_notice(message: str) -> None:
        show_editor_state["notice"] = message
        queue_panel.show_editor_status_label.setText(message)
        if _is_error_message(message):
            _show_error(message)

    def _current_show() -> Show:
        return show_editor_state["show"]

    def _show_name_from_form() -> str:
        return queue_panel.show_name_edit.text().strip() or "Untitled Show"

    def _current_show_track() -> ShowTrack | None:
        track_index = show_editor_state.get("track_index")
        tracks = _current_show().tracks
        if not isinstance(track_index, int) or not 0 <= track_index < len(tracks):
            return None
        return tracks[track_index]

    def _replace_show(*, tracks: tuple[ShowTrack, ...] | None = None, name: str | None = None) -> Show:
        current = _current_show()
        next_show = Show(
            name=(name if name is not None else _show_name_from_form()),
            tracks=(tracks if tracks is not None else current.tracks),
            metadata=dict(current.metadata),
        )
        show_editor_state["show"] = next_show
        return next_show

    def _replace_current_show_track(track: ShowTrack) -> None:
        track_index = show_editor_state.get("track_index")
        show = _current_show()
        if not isinstance(track_index, int) or not 0 <= track_index < len(show.tracks):
            return
        tracks = list(show.tracks)
        tracks[track_index] = track
        _replace_show(tracks=tuple(tracks))

    def _render_show_tracks() -> None:
        show = _current_show()
        current_index = show_editor_state.get("track_index")
        list_widget = queue_panel.show_tracks_list
        name_blocker = QtCore.QSignalBlocker(queue_panel.show_name_edit)
        queue_panel.show_name_edit.setText(show.name)
        del name_blocker
        list_blocker = QtCore.QSignalBlocker(list_widget)
        list_widget.clear()
        for index, track in enumerate(show.tracks):
            state = "Compiled" if track.is_compiled else "Needs compile"
            item = QtWidgets.QListWidgetItem(f"{index + 1}. {track.display_name} — {state}")
            item.setData(QtCore.Qt.ItemDataRole.UserRole, index)
            item.setToolTip(track.audio_path)
            list_widget.addItem(item)
            if index == current_index:
                item.setSelected(True)
        del list_blocker

    def _live_show_edit_lock_reason(track: ShowTrack | None) -> str:
        if track is None:
            return ""
        try:
            track_path = Path(track.audio_path).resolve()
        except OSError:
            return ""
        for queue_track in queue_controller.state.local_tracks:
            try:
                matches = Path(queue_track.path).resolve() == track_path
            except OSError:
                matches = queue_track.path == track.audio_path
            if matches and not queue_track.show_editable:
                return queue_track.edit_lock_reason
        return ""

    def _current_show_is_editable() -> bool:
        reason = _live_show_edit_lock_reason(_current_show_track())
        if not reason:
            return True
        queue_controller.set_status(f"{reason}. This show is locked in Live.")
        _render_queue_state(queue_controller.state)
        return False

    def _sync_show_action_states() -> None:
        show = _current_show()
        track = _current_show_track()
        busy = bool(show_editor_state.get("busy"))
        has_track = track is not None
        has_pending = any(not entry.is_compiled for entry in show.tracks)
        lock_reason = _live_show_edit_lock_reason(track)
        editable = not busy and not lock_reason
        queue_panel.compile_show_button.setEnabled(bool(has_track and not track.is_compiled and editable))
        queue_panel.compile_all_tracks_button.setEnabled(bool(show.tracks and has_pending and editable))
        queue_panel.save_show_button.setEnabled(bool(show.tracks and editable))
        queue_panel.play_saved_show_button.setEnabled(bool(show.is_fully_compiled and not busy))
        queue_panel.play_selected_cue_button.setEnabled(bool(has_track and track.is_compiled and not busy))
        queue_panel.add_show_track_button.setEnabled(editable)
        queue_panel.remove_show_track_button.setEnabled(bool(has_track and editable))
        track_index = show_editor_state.get("track_index")
        queue_panel.move_show_track_up_button.setEnabled(
            bool(isinstance(track_index, int) and track_index > 0 and editable)
        )
        queue_panel.move_show_track_down_button.setEnabled(
            bool(
                isinstance(track_index, int)
                and track_index + 1 < len(show.tracks)
                and editable
            )
        )
        for control in (
            queue_panel.show_name_edit,
            queue_panel.show_bpm_spin,
            queue_panel.show_time_signature_combo,
            queue_panel.show_duration_spin,
            queue_panel.show_title_edit,
            queue_panel.show_artist_edit,
            queue_panel.show_palette_widget,
            queue_panel.show_import_palette_button,
            queue_panel.show_routing_host,
            queue_panel.add_show_cue_button,
            queue_panel.duplicate_show_cue_button,
            queue_panel.remove_show_cue_button,
            queue_panel.show_cues_table,
            queue_panel.show_timeline_view,
        ):
            control.setEnabled(editable)
        if lock_reason:
            queue_panel.show_editor_status_label.setText(
                f"{lock_reason}. Live show editing is disabled until the transition completes."
            )
        # Frame baking still targets legacy one-track files.  The new Show
        # manifest embeds multiple timelines, so it is deliberately not
        # offered as a misleading action here.
        queue_panel.bake_show_button.setEnabled(False)
        source_show_path = show_editor_state.get("path")
        artifact_path = (
            default_baked_frame_path(source_show_path)
            if isinstance(source_show_path, Path)
            else None
        )
        queue_panel.validate_baked_button.setEnabled(
            bool(artifact_path is not None and artifact_path.exists() and not busy)
        )
        validation = show_editor_state.get("baked_validation")
        queue_panel.save_baked_as_button.setEnabled(
            bool(validation is not None and validation.valid and not busy)
        )

    def _validate_current_baked_artifact(*, manual: bool) -> bool:
        source_show_path = show_editor_state.get("path")
        if not isinstance(source_show_path, Path):
            show_editor_state["baked_artifact_path"] = None
            show_editor_state["baked_validation"] = None
            if manual:
                queue_controller.set_status("Save or load a Show before validating baked frames.")
            _sync_show_action_states()
            return False
        artifact_path = default_baked_frame_path(source_show_path)
        show_editor_state["baked_artifact_path"] = artifact_path
        if not artifact_path.exists():
            show_editor_state["baked_validation"] = None
            if manual:
                queue_controller.set_status(f"No baked artifact found at {artifact_path.name}.")
            _sync_show_action_states()
            return False
        device_config_path = _device_health_config_path()
        if device_config_path is None or not device_config_path.exists():
            show_editor_state["baked_validation"] = None
            if manual:
                queue_controller.set_status(
                    "Choose an existing device / room-layout config before validating baked frames."
                )
            _sync_show_action_states()
            return False
        validation = show_service.validate_baked_artifact(
            artifact_path,
            source_show_path=source_show_path,
            device_config_path=device_config_path,
        )
        show_editor_state["baked_validation"] = validation
        if manual:
            if validation.valid:
                queue_controller.set_status(f"Baked artifact is valid: {artifact_path.name}.")
            else:
                queue_controller.set_status(
                    f"Baked artifact rejected: {validation.reason or 'invalid_artifact'}."
                )
            _render_queue_state(queue_controller.state)
        _sync_show_action_states()
        return bool(validation.valid)

    def _commit_current_show_track_editor() -> None:
        track = _current_show_track()
        if track is None or track.timeline is None:
            return
        timeline = _timeline_from_editor()
        if timeline is None:
            return
        _replace_current_show_track(
            ShowTrack(
                audio_path=track.audio_path,
                timeline=timeline,
                metadata=dict(track.metadata or {}),
            )
        )
        show_editor_state["timeline"] = timeline

    def _activate_show_track(index: int, *, status: str, context=None) -> None:
        show = _current_show()
        if not 0 <= index < len(show.tracks):
            show_editor_state["track_index"] = None
            show_editor_state["timeline"] = None
            show_editor_state["audio_path"] = None
            show_editor_state["context"] = None
            show_editor_state["notice"] = status
            _render_show_tracks()
            _render_show_editor()
            _sync_show_action_states()
            return
        track = show.tracks[index]
        audio_path = Path(track.audio_path)
        show_editor_state["track_index"] = index
        show_editor_state["timeline"] = track.timeline
        show_editor_state["audio_path"] = audio_path
        # A pending Track has no timeline to edit.  Do not synchronously
        # decode/analyze it merely because it was selected in the playlist.
        # Compilation supplies its detailed context when it is actually ready.
        show_editor_state["context"] = (
            context
            if context is not None
            else (
                show_service.build_timeline_context(audio_path=audio_path, timeline=track.timeline)
                if track.timeline is not None
                else None
            )
        )
        show_editor_state["notice"] = status
        show_editor_state["show_palette_dirty"] = False
        show_editor_state["show_palette_colors"] = _show_palette_colors_from_timeline(track.timeline)
        show_path = show_editor_state.get("path")
        if isinstance(show_path, Path):
            selected_show_path["value"] = show_path
            runtime_supervisor.select_saved_show(show_path)
        _render_show_tracks()
        _render_show_editor()
        _sync_show_action_states()

    def _load_show_into_editor(show: Show, *, show_path: Path | None, status: str, index: int = 0) -> None:
        show_editor_state["show"] = show
        show_editor_state["path"] = show_path
        if show_path is not None:
            selected_show_path["value"] = show_path
            runtime_supervisor.select_saved_show(show_path)
        else:
            selected_show_path["value"] = None
            runtime_supervisor.select_saved_show(None)
        if show.tracks:
            _activate_show_track(min(max(index, 0), len(show.tracks) - 1), status=status)
        else:
            _activate_show_track(-1, status=status)
        _validate_current_baked_artifact(manual=False)

    def _set_show_palette_import_notice(message: str, *, actionable: bool) -> None:
        queue_panel.show_palette_import_label.setText(message)
        queue_panel.show_palette_import_label.setVisible(bool(message))
        queue_panel.show_import_palette_button.setVisible(bool(message))
        queue_panel.show_import_palette_button.setEnabled(bool(actionable))

    def _track_compile_notice(audio_path: Path | None) -> str:
        if audio_path is None:
            return "Add a Track to this Show, then compile it to start editing."
        return f"{audio_path.name} is ready. Click Compile Track to create its editable lighting timeline."

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
        show_editor_state["show"] = show_service.new_show()
        show_editor_state["track_index"] = None
        show_editor_state["timeline"] = None
        show_editor_state["path"] = None
        show_editor_state["audio_path"] = None
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
        _render_show_tracks()
        _render_show_editor()
        _sync_show_action_states()

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
        # The local Queue is now separate from the saved Show arrangement.
        # Selecting a queue item must not silently replace the Show the user
        # is composing.  Add Track is the explicit bridge between them.
        if not _current_show().tracks:
            selected = queue_controller.state.selected_track
            if selected is not None:
                _set_show_editor_notice(
                    f"Select Add Track… to include {Path(selected.path).name} in this Show."
                )

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
            queue_panel.compile_all_tracks_button,
            queue_panel.load_show_button,
            queue_panel.clear_show_button,
            queue_panel.save_show_button,
            queue_panel.add_show_track_button,
            queue_panel.remove_show_track_button,
            queue_panel.move_show_track_up_button,
            queue_panel.move_show_track_down_button,
            queue_panel.play_saved_show_button,
            queue_panel.play_selected_cue_button,
            queue_panel.show_import_palette_button,
            queue_panel.show_name_edit,
            queue_panel.show_tracks_list,
        )

    def _run_show_editor_busy_task(message: str, callback, *, track_total: int | None = None):
        app = QtWidgets.QApplication.instance()
        previous_enabled = {
            button: bool(button.isEnabled())
            for button in _show_editor_busy_buttons()
            if button is not None
        }
        progress_lock = threading.Lock()
        progress_updates: list[tuple[int, int, str, str, int]] = []

        if track_total is None:
            dialog = QtWidgets.QProgressDialog(message, "", 0, 0, window)
            dialog.setWindowTitle("Working")
            dialog.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
            dialog.setCancelButton(None)
            dialog.setMinimumDuration(0)
            dialog.setAutoClose(False)
            dialog.setAutoReset(False)
            dialog.setRange(0, 0)
            progress_label = None
            phase_label = None
            total_progress = None
            track_progress = None
        else:
            dialog = QtWidgets.QDialog(window)
            dialog.setWindowTitle("Compiling Show")
            dialog.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
            dialog.setMinimumWidth(420)
            dialog.setWindowFlag(QtCore.Qt.WindowType.WindowCloseButtonHint, False)
            dialog_layout = QtWidgets.QVBoxLayout(dialog)
            progress_label = QtWidgets.QLabel(message)
            progress_label.setWordWrap(True)
            phase_label = QtWidgets.QLabel("Preparing compiler…")
            phase_label.setWordWrap(True)
            total_progress = QtWidgets.QProgressBar()
            total_progress.setRange(0, max(1, track_total))
            total_progress.setValue(0)
            total_progress.setFormat("%v of %m Tracks complete")
            track_progress = QtWidgets.QProgressBar()
            track_progress.setRange(0, 0)
            track_progress.setFormat("Current Track: working…")
            dialog_layout.addWidget(progress_label)
            dialog_layout.addWidget(phase_label)
            dialog_layout.addWidget(total_progress)
            dialog_layout.addWidget(track_progress)

        def _report_track_progress(
            track_number: int,
            total_tracks: int,
            track_name: str,
            phase: str,
            completed_tracks: int,
        ) -> None:
            """Queue worker-thread progress for the Qt event-loop thread."""
            with progress_lock:
                progress_updates.append(
                    (
                        int(track_number),
                        int(total_tracks),
                        str(track_name),
                        str(phase),
                        int(completed_tracks),
                    )
                )

        def _apply_progress_update() -> None:
            if track_total is None:
                return
            with progress_lock:
                if not progress_updates:
                    return
                track_number, total_tracks, track_name, phase, completed_tracks = progress_updates[-1]
                progress_updates.clear()
            label = f"Compiling Track {track_number} of {total_tracks}: {track_name}"
            if progress_label is not None:
                progress_label.setText(label)
            if phase_label is not None:
                phase_label.setText(phase)
            if total_progress is not None:
                total_progress.setRange(0, max(1, total_tracks))
                total_progress.setValue(max(0, min(completed_tracks, total_tracks)))
            if track_progress is not None:
                track_progress.setRange(0, 0)
            _set_show_editor_notice(label)
            queue_controller.set_status(label)
            _render_queue_state(queue_controller.state)

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
                result_box["value"] = callback(_report_track_progress)
            except BaseException as exc:  # pragma: no cover - surfaced through caller
                error_box["value"] = exc

        worker = threading.Thread(target=_worker, daemon=True)
        worker.start()
        try:
            while worker.is_alive():
                worker.join(0.05)
                _apply_progress_update()
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
        meta_group = queue_panel.show_meta_group
        group_blocker = QtCore.QSignalBlocker(meta_group)
        meta_group.setChecked(not bool(hidden))
        del group_blocker
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

    def _move_show_cue(index: int, next_time: float) -> None:
        if not _current_show_is_editable():
            return
        table = queue_panel.show_cues_table
        if not 0 <= index < table.rowCount():
            return
        time_widget = table.cellWidget(index, SHOW_CUE_COL_TIME)
        if time_widget is None:
            return
        lower = 0.0
        upper = float(show_editor_state["timeline"].duration) if show_editor_state["timeline"] is not None else 7200.0
        if index > 0:
            lower = _row_effective_cue_time(index - 1) + SHOW_CUE_MIN_GAP_SECONDS
        if index + 1 < table.rowCount():
            upper = _row_effective_cue_time(index + 1) - SHOW_CUE_MIN_GAP_SECONDS
        time_widget.setValue(max(lower, min(float(next_time), upper)))
        _commit_cue_row_timing_edit(index, manual_free=True)

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

    def _add_beat_marker(at_time: float) -> None:  # pragma: no cover - Qt only
        if not _current_show_is_editable():
            return
        timeline = _safe_editor_timeline()
        if timeline is None:
            return
        next_time = max(0.0, min(float(at_time), float(timeline.duration)))
        if any(abs(float(beat_time) - next_time) < 0.01 for beat_time in timeline.beat_times):
            _set_show_editor_notice("A beat line already exists at that point.")
            return
        beat_times = tuple(sorted((*timeline.beat_times, next_time)))
        _replace_editor_timeline(beat_times=beat_times)
        _sync_cue_time_widgets_from_beat_grid()
        _set_show_editor_notice("Beat line added to the editor timeline.")
        _render_show_timeline_view(playhead_seconds=_current_show_timeline_position())

    def _delete_beat_marker(index: int) -> None:  # pragma: no cover - Qt only
        if not _current_show_is_editable():
            return
        timeline = _safe_editor_timeline()
        if timeline is None or not (0 <= index < len(timeline.beat_times)):
            return
        removed_time = float(timeline.beat_times[index])
        beat_times = list(timeline.beat_times)
        beat_times.pop(index)
        downbeat_times = tuple(
            value for value in timeline.downbeat_times if abs(float(value) - removed_time) > 1e-6
        )
        _replace_editor_timeline(beat_times=tuple(beat_times), downbeat_times=downbeat_times)
        _sync_cue_time_widgets_from_beat_grid()
        queue_panel.show_timeline_view.clear_marker_selection()
        _set_show_editor_notice("Beat line removed from the editor timeline.")
        _render_show_timeline_view(playhead_seconds=_current_show_timeline_position())

    def _delete_downbeat_marker(index: int) -> None:  # pragma: no cover - Qt only
        if not _current_show_is_editable():
            return
        timeline = _safe_editor_timeline()
        if timeline is None or not (0 <= index < len(timeline.downbeat_times)):
            return
        removed_time = float(timeline.downbeat_times[index])
        downbeat_times = list(timeline.downbeat_times)
        downbeat_times.pop(index)
        beat_times = tuple(
            value for value in timeline.beat_times if abs(float(value) - removed_time) > 1e-6
        )
        _replace_editor_timeline(beat_times=beat_times, downbeat_times=tuple(downbeat_times))
        _sync_cue_time_widgets_from_beat_grid()
        queue_panel.show_timeline_view.clear_marker_selection()
        _set_show_editor_notice("Downbeat line removed from the editor timeline.")
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

        layer_category_combo = _combo_widget(
            SHOW_CUE_LAYER_CATEGORY_OPTIONS,
            str(cue_state["layer_category"]),
            blank_label="Preset/default",
        )
        layer_category_combo.currentIndexChanged.connect(refresh_timeline)
        table.setCellWidget(row, SHOW_CUE_COL_LAYER_CATEGORY, layer_category_combo)

        layer_target_combo = _combo_widget(
            SHOW_CUE_LAYER_TARGET_OPTIONS,
            str(cue_state["layer_target"]),
            blank_label="Auto target",
        )
        layer_target_combo.currentIndexChanged.connect(refresh_timeline)
        table.setCellWidget(row, SHOW_CUE_COL_LAYER_TARGET, layer_target_combo)

        layer_trigger_combo = _combo_widget(
            SHOW_CUE_LAYER_TRIGGER_OPTIONS,
            str(cue_state["layer_trigger"]),
            blank_label="Continuous",
        )
        layer_trigger_combo.currentIndexChanged.connect(refresh_timeline)
        table.setCellWidget(row, SHOW_CUE_COL_LAYER_TRIGGER, layer_trigger_combo)

        layer_falloff_combo = _combo_widget(
            SHOW_CUE_LAYER_FALLOFF_OPTIONS,
            str(cue_state["layer_falloff"]),
            blank_label="Linear",
        )
        layer_falloff_combo.currentIndexChanged.connect(refresh_timeline)
        table.setCellWidget(row, SHOW_CUE_COL_LAYER_FALLOFF, layer_falloff_combo)

        layer_thickness_spin = _double_spin(
            float(cue_state["layer_thickness"]),
            minimum=0.01,
            maximum=4.0,
            step=0.05,
            decimals=3,
        )
        layer_thickness_spin.valueChanged.connect(refresh_timeline)
        table.setCellWidget(row, SHOW_CUE_COL_LAYER_THICKNESS, layer_thickness_spin)

        layer_speed_spin = _double_spin(
            float(cue_state["layer_speed"]),
            minimum=0.0,
            maximum=12.0,
            step=0.05,
            decimals=3,
        )
        layer_speed_spin.valueChanged.connect(refresh_timeline)
        table.setCellWidget(row, SHOW_CUE_COL_LAYER_SPEED, layer_speed_spin)

        layer_priority_spin = QtWidgets.QSpinBox()
        layer_priority_spin.setRange(-100, 100)
        layer_priority_spin.setValue(int(cue_state["layer_priority"]))
        layer_priority_spin.valueChanged.connect(refresh_timeline)
        table.setCellWidget(row, SHOW_CUE_COL_LAYER_PRIORITY, layer_priority_spin)
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
            layer_category = str(
                _row_value(queue_panel.show_cues_table, row, SHOW_CUE_COL_LAYER_CATEGORY) or ""
            ).strip()
            layer_target = str(
                _row_value(queue_panel.show_cues_table, row, SHOW_CUE_COL_LAYER_TARGET) or ""
            ).strip()
            layer_trigger = str(
                _row_value(queue_panel.show_cues_table, row, SHOW_CUE_COL_LAYER_TRIGGER) or ""
            ).strip()
            layer_falloff = str(
                _row_value(queue_panel.show_cues_table, row, SHOW_CUE_COL_LAYER_FALLOFF) or ""
            ).strip()
            layer_thickness = float(
                _row_value(queue_panel.show_cues_table, row, SHOW_CUE_COL_LAYER_THICKNESS)
            )
            layer_speed = float(_row_value(queue_panel.show_cues_table, row, SHOW_CUE_COL_LAYER_SPEED))
            layer_priority = int(_row_value(queue_panel.show_cues_table, row, SHOW_CUE_COL_LAYER_PRIORITY))

            if abs(wave_rate - 1.0) > 1e-9:
                params["wave_rate_mult"] = wave_rate
            if palette_override:
                params["palette_override"] = True
            else:
                params.pop("palette_override", None)
                palette = show_palette
            effect_layer = _show_cue_effect_layer_from_controls(
                category=layer_category,
                target=layer_target,
                trigger=layer_trigger,
                falloff=layer_falloff,
                thickness=layer_thickness,
                speed=layer_speed,
                render_mode=render_mode,
                palette=palette,
                color_bias=color_bias,
            )
            if effect_layer is not None:
                for key in ("time_offset_s", "duration_s", "intensity_scale", "radius"):
                    if key in params:
                        effect_layer[key] = params.pop(key)

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
                if effect_layer is not None:
                    instrument_route["effect_layer"] = effect_layer
                    if layer_priority:
                        instrument_route["layer_priority"] = layer_priority
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
                if effect_layer is not None:
                    eq_route["effect_layer"] = effect_layer
                    if layer_priority:
                        eq_route["layer_priority"] = layer_priority
                params["eq_routes"] = [eq_route]
                params["active_eq_routes"] = [dict(eq_route)]
            elif instrument_route is None:
                if abs(intensity_boost) > 1e-9:
                    params["intensity_boost"] = intensity_boost
                if spatial_preset:
                    params["spatial_preset"] = spatial_preset
                if color_bias:
                    params["color_bias"] = color_bias
                if effect_layer is not None:
                    params["effect_layer"] = effect_layer
                    if layer_priority:
                        params["layer_priority"] = layer_priority

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
        _sync_show_action_states()
        _toggle_show_meta_panel(bool(show_editor_state.get("meta_hidden")))
        queue_panel.show_cues_table.setRowCount(0)
        if timeline is None:
            queue_panel.show_title_edit.setText("")
            queue_panel.show_artist_edit.setText("")
            queue_panel.show_bpm_spin.setValue(120.0)
            _select_combo_data(queue_panel.show_time_signature_combo, 4)
            queue_panel.show_duration_spin.setValue(180.0)
            _set_show_timeline_zoom_slider(1.0)
            _render_show_timeline_view(playhead_seconds=None)
            _sync_show_action_states()
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
        _sync_show_action_states()

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

    def _configured_directory(value: str, fallback: Path) -> Path:
        configured = str(value).strip()
        return Path(configured).expanduser() if configured else fallback

    def _profile_load_directory() -> Path:
        return _configured_directory(
            queue_panel.profile_directory_edit.text(),
            BUILTIN_PROFILES_DIR,
        )

    def _show_save_directory() -> Path:
        return _configured_directory(
            queue_panel.show_directory_edit.text(),
            show_service.default_show_directory(),
        )

    def _queue_load_directory() -> Path:
        return _configured_directory(queue_panel.queue_directory_edit.text(), Path.cwd())

    def _quickshow_directory() -> Path:
        """Quickshows and saved Shows deliberately share one configured folder."""
        return _show_save_directory()

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
        validation = profile_service.validate_profile_file(path)
        if validation.valid:
            warning_text = "\n".join(validation.warnings[:4])
            profile_panel.validation_label.setText(
                "Validation: structurally valid."
                + (f"\nHarmony warnings:\n{warning_text}" if warning_text else "")
            )
            profile_panel.validation_label.setStyleSheet(
                "color: #b45309;" if validation.warnings else "color: #15803d;"
            )
        else:
            profile_panel.validation_label.setText(
                "Validation failed:\n" + "\n".join(validation.errors)
            )
            profile_panel.validation_label.setStyleSheet("color: #dc2626; font-weight: 600;")
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
        profile_panel.show_palette_set_combo.blockSignals(True)
        profile_panel.show_palette_set_combo.clear()
        for set_name in state.show_palette_sets:
            profile_panel.show_palette_set_combo.addItem(set_name, set_name)
        _select_combo_data(
            profile_panel.show_palette_set_combo,
            state.selected_show_palette_set,
        )
        profile_panel.show_palette_set_combo.blockSignals(False)
        profile_panel.show_palette_set_members_edit.setText(
            state.show_palette_set_members_text
        )

        selected_reactive_set = str(
            queue_panel.reactive_show_palette_set_combo.currentData() or ""
        )
        queue_panel.reactive_show_palette_set_combo.blockSignals(True)
        queue_panel.reactive_show_palette_set_combo.clear()
        queue_panel.reactive_show_palette_set_combo.addItem("Use mood palettes", "")
        for set_name in state.show_palette_sets:
            queue_panel.reactive_show_palette_set_combo.addItem(set_name, set_name)
        if selected_reactive_set and selected_reactive_set not in state.show_palette_sets:
            queue_panel.reactive_show_palette_set_combo.addItem(
                f"{selected_reactive_set} (not in active profile)",
                selected_reactive_set,
            )
        _select_combo_data(queue_panel.reactive_show_palette_set_combo, selected_reactive_set)
        queue_panel.reactive_show_palette_set_combo.blockSignals(False)
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
        if _is_error_message(state.status_message):
            _show_error(state.status_message)
        _update_show_palette_import_affordance()

    def _render_show_patch_state() -> None:
        state = show_patch_controller.state
        queue_panel.patch_name_edit.setText(state.patch_name)
        queue_panel.patch_rules_edit.setPlainText(state.patch_rules_text)
        queue_panel.patch_summary_label.setText(state.patch_summary)

    def _render_palette_swatches(
        label: object,
        colors: tuple[str, ...],
        *,
        empty_text: str,
        tooltip_prefix: str,
    ) -> None:
        if not colors:
            label.setText(empty_text)
            label.setToolTip(tooltip_prefix)
            return
        swatches: list[str] = []
        for index, color in enumerate(colors):
            candidate = str(color).strip()
            valid_color = candidate if QtGui.QColor(candidate).isValid() else "#6b7280"
            if index:
                swatches.append('<span style="color:#94a3b8;">━</span>')
            swatches.append(
                f'<span style="color:{escape(valid_color)};">●</span>'
            )
        label.setText(
            '<span style="font-size:18px; letter-spacing:2px;">'
            f"{''.join(swatches)}"
            "</span>"
        )
        label.setToolTip(
            tooltip_prefix + ": " + ", ".join(str(color) for color in colors)
        )

    def _render_live_palette_preview(colors: tuple[str, ...]) -> None:
        _render_palette_swatches(
            queue_panel.live_palette_preview_label,
            colors,
            empty_text="No palette selected",
            tooltip_prefix=(
                "Read-only palette preview. Edit palette definitions in the "
                "Palettes tab"
            ),
        )

    def _render_reactive_live_state(
        runtime_state: RuntimeModeState | None = None,
        snapshot: dict[str, object] | None = None,
    ) -> None:  # pragma: no cover - Qt only
        """Render the real input/beat state from the refined reactive engine."""
        runtime_state = runtime_state or runtime_supervisor.snapshot()
        active_reactive = runtime_state.active_output_mode in {"reactive", "reactive_live"}
        queue_panel.reactive_downbeat_nudge_button.setEnabled(
            active_reactive
        )
        queue_panel.reactive_chord_history_group.setVisible(
            queue_panel.reactive_chord_panel_check.isChecked()
        )
        queue_panel.reactive_waveform_panel.setVisible(
            queue_panel.reactive_waveform_panel_check.isChecked()
        )
        if snapshot is None and active_reactive:
            session = runtime_supervisor.active_session()
            if session is not None and hasattr(session, "session_snapshot"):
                snapshot = session.session_snapshot()
        snapshot = snapshot or {}

        if not active_reactive:
            queue_panel.reactive_listening_label.setText("○ Input not listening")
            queue_panel.reactive_listening_label.setStyleSheet("color: #94a3b8; font-weight: 600;")
            queue_panel.reactive_beat_indicator_label.setStyleSheet("color: #475569; font-size: 22px;")
            queue_panel.reactive_beat_indicator_label.setToolTip(
                "Beat detector: waiting for the refined BPM detector."
            )
            queue_panel.reactive_bpm_label.setText("Beat detector: waiting for audio")
            queue_panel.reactive_bpm_label.setToolTip("")
            queue_panel.reactive_effect_tempo_label.setText(
                "Effect tempo: "
                f"{reactive_effect_tempo_state['multiplier']:g}× "
                "cycle BPM"
            )
            queue_panel.reactive_cycle_tempo_label.setText(
                "Cycle tempo: "
                f"{reactive_cycle_tempo_state['multiplier']:g}× detector"
                "  {  }"
            )
            queue_panel.reactive_cycle_label.setText(
                _format_reactive_cycle({})
            )
            queue_panel.reactive_chord_label.setText(
                "Current detected chord: — · Previous 3: —"
            )
            _set_reactive_chord_surface_data(
                status="Waiting for live input",
            )
            queue_panel.reactive_profile_label.setText("Active profile: awaiting Reactive session")
            queued_palette_name = str(
                queue_panel.reactive_live_color_profile_combo.currentData()
                or ""
            )
            queued_palette_colors = tuple(
                PALETTES.get(queued_palette_name, ())
            )
            queue_panel.reactive_active_palette_label.setText(
                (
                    "Queued live colors: "
                    + queued_palette_name.replace("_", " ").title()
                )
                if queued_palette_name
                else "Active palette: awaiting Reactive session"
            )
            _render_palette_swatches(
                queue_panel.reactive_active_palette_preview_label,
                queued_palette_colors,
                empty_text="No active colors",
                tooltip_prefix=(
                    "Colors queued for Reactive output"
                    if queued_palette_name
                    else "Colors currently applied to Reactive output"
                ),
            )
            queue_panel.reactive_palette_next_label.setText("Time to next palette: —")
            queue_panel.reactive_palette_queue_label.setText("Palette queue: —")
            queue_panel.reactive_waveform_view.set_diagnostic_data(
                (),
                (),
                chord_changes=(),
            )
            debug_visible = bool(
                runtime_supervisor.reactive_settings().debug_harmonics
            )
            queue_panel.reactive_harmonic_debug_group.setVisible(
                debug_visible
                and queue_panel.reactive_harmonic_panel_check.isChecked()
            )
            _set_reactive_harmonic_surface_data(
                (),
                (),
            )
            if live_mode_state["value"] == "reactive":
                queue_panel.reactive_mode_status_label.setText(
                    "Reactive mode is cued. Start Reactive or press Space to begin listening."
                )
            return

        listening = bool(snapshot.get("listening", False))
        input_device = str(snapshot.get("input_device", "system default") or "system default")
        bpm = float(snapshot.get("bpm", 0.0) or 0.0)
        detected_bpm = float(
            snapshot.get("detected_bpm", bpm) or bpm
        )
        cycle_tempo_multiplier = float(
            snapshot.get(
                "cycle_tempo_multiplier",
                reactive_cycle_tempo_state["multiplier"],
            )
            or 1.0
        )
        reactive_cycle_tempo_state["multiplier"] = (
            cycle_tempo_multiplier
        )
        stability = float(snapshot.get("stability", 0.0) or 0.0)
        cyclic_grid_bpm = float(snapshot.get("cyclic_grid_bpm", 0.0) or 0.0)
        cyclic_grid_confidence = float(snapshot.get("cyclic_grid_confidence", 0.0) or 0.0)
        active_profile_name = str(snapshot.get("active_profile_name", "") or "")
        profile_cycle_mode = str(snapshot.get("profile_cycle_mode", "") or "")
        active_palette_name = str(snapshot.get("active_palette_name", "") or "")
        active_palette_colors = tuple(snapshot.get("current_palette", ()) or ())
        palette_queue = tuple(snapshot.get("palette_queue", ()) or ())
        palette_cycle_mode = str(snapshot.get("palette_cycle_mode", "") or "")
        palette_seconds_until_next = snapshot.get("palette_seconds_until_next")
        song_boundaries = int(snapshot.get("song_boundaries", 0) or 0)
        automatic_reset_count = int(
            snapshot.get("automatic_detection_reset_count", 0) or 0
        )
        last_reset_reason = str(
            snapshot.get("last_detection_reset_reason", "") or ""
        )
        last_beat = float(snapshot.get("last_beat_monotonic", 0.0) or 0.0)
        downbeat_nudge_pending = bool(
            snapshot.get("manual_downbeat_nudge_pending", False)
        )
        manual_beat_pending_kind = str(
            snapshot.get("manual_beat_latch_pending_kind", "") or ""
        )
        downbeat_nudge_count = int(
            snapshot.get("manual_downbeat_nudge_count", 0) or 0
        )
        manual_tempo_goal_bpm = float(
            snapshot.get("manual_tempo_goal_bpm", 0.0) or 0.0
        )
        manual_tempo_confidence = float(
            snapshot.get("manual_tempo_confidence", 0.0) or 0.0
        )
        meter_time_signature = tuple(
            snapshot.get("meter_time_signature", ()) or ()
        )
        meter_text = (
            f"{int(meter_time_signature[0])}/{int(meter_time_signature[1])}"
            if len(meter_time_signature) >= 2
            else ""
        )
        beat_is_flashing = last_beat > 0.0 and time.monotonic() - last_beat <= 0.14
        waveform_points = snapshot.get("waveform_points", ()) or ()
        eq_band_points = snapshot.get("eq_band_points", {}) or {}
        detected_beats = snapshot.get("detected_beat_times", ()) or ()
        detected_downbeats = (
            snapshot.get("detected_downbeat_times", ()) or ()
        )
        manual_beats = snapshot.get("manual_beat_markers", ()) or ()
        predicted_beats = snapshot.get("predicted_beat_times", ()) or ()
        upcoming_effects = snapshot.get("upcoming_effect_cues", ()) or ()
        stream_t = float(snapshot.get("stream_t", 0.0) or 0.0)
        detected_chord = str(snapshot.get("detected_chord", "") or "")
        live_chord, chord_change_confirming = _reactive_display_chord(
            snapshot
        )
        detected_bar_chord_history = tuple(
            str(chord)
            for chord in (
                snapshot.get(
                    "detected_bar_chord_history",
                    (),
                )
                or ()
            )
        )[-3:]
        detected_chord_changes = (
            snapshot.get("detected_chord_changes", ()) or ()
        )
        harmonic_enabled = bool(snapshot.get("harmonic_enabled", False))
        harmonic_debug_enabled = bool(
            snapshot.get(
                "harmonic_debug_enabled",
                runtime_supervisor.reactive_settings().debug_harmonics,
            )
        )
        harmonic_debug_spectrum = (
            snapshot.get("harmonic_debug_spectrum", ()) or ()
        )
        harmonic_debug_chroma = (
            snapshot.get("harmonic_debug_chroma", ()) or ()
        )
        harmonic_debug_chord = str(
            snapshot.get("harmonic_debug_chord", "") or ""
        )
        harmonic_debug_chord_tones = (
            snapshot.get("harmonic_debug_chord_tones", ()) or ()
        )
        harmonic_debug_root_note = str(
            snapshot.get("harmonic_debug_root_note", "") or ""
        )
        harmonic_debug_non_chord_tones = (
            snapshot.get("harmonic_debug_non_chord_tones", ()) or ()
        )
        chord_prediction = dict(
            snapshot.get("detected_chord_prediction", {}) or {}
        )
        chord_prediction_seconds = snapshot.get(
            "detected_chord_prediction_seconds"
        )
        predictive_events = tuple(
            item
            for item in (snapshot.get("predictive_events", ()) or ())
            if isinstance(item, dict)
        )
        predictive_chord_event = next(
            (
                item
                for item in predictive_events
                if item.get("target_function")
            ),
            None,
        )
        if predictive_chord_event is not None:
            chord_prediction = {
                "chord": predictive_chord_event.get(
                    "target_function",
                    "",
                ),
                "confidence": predictive_chord_event.get(
                    "probability",
                    0.0,
                ),
            }
            chord_prediction_seconds = predictive_chord_event.get(
                "seconds_until"
            )
        chord_prediction_mismatch = (
            dict(
                snapshot.get(
                    "detected_chord_prediction_mismatch",
                    {},
                )
                or {}
            )
            if snapshot.get(
                "detected_chord_prediction_mismatch_active",
                False,
            )
            else {}
        )
        harmonic_debug_confidence = float(
            snapshot.get("harmonic_chord_confidence", 0.0) or 0.0
        )
        predictive_cycle = dict(
            snapshot.get("predictive_cycle", {}) or {}
        )
        runtime_control = dict(
            snapshot.get("runtime_control", {}) or {}
        )
        runtime_palette_override = tuple(
            str(color)
            for color in runtime_control.get("palette_override", ())
            if str(color).strip()
        )
        effect_tempo_multiplier = float(
            runtime_control.get(
                "speed_multiplier",
                reactive_effect_tempo_state["multiplier"],
            )
            or 1.0
        )
        effective_effect_bpm = bpm * effect_tempo_multiplier
        queue_panel.reactive_cycle_tempo_label.setText(
            f"Cycle tempo: {cycle_tempo_multiplier:g}× detector"
            + (
                f" ({detected_bpm:.1f} → {bpm:.1f} BPM)"
                if detected_bpm > 0.0
                else ""
            )
            + "  {  }"
        )
        queue_panel.reactive_effect_tempo_label.setText(
            f"Effect tempo: {effect_tempo_multiplier:g}× cycle BPM"
            + (
                f" ({effective_effect_bpm:.1f} BPM)"
                if bpm > 0.0
                else ""
            )
        )
        queue_panel.reactive_cycle_label.setText(
            _format_structure_similarity(snapshot)
            if snapshot.get("structure_similarity_enabled", False)
            else _format_reactive_cycle(predictive_cycle)
        )
        waveform_window = float(snapshot.get("waveform_window_seconds", 10.0) or 10.0)
        queue_panel.reactive_waveform_view.set_diagnostic_data(
            waveform_points,
            detected_beats,
            band_points=eq_band_points,
            chord_changes=detected_chord_changes,
            downbeats=detected_downbeats,
            manual_beats=manual_beats,
            predicted_beats=predicted_beats,
            upcoming_effects=upcoming_effects,
            now_t=stream_t if stream_t > 0.0 else None,
            window_seconds=waveform_window,
            bpm=bpm,
        )
        queue_panel.reactive_harmonic_debug_group.setVisible(
            harmonic_debug_enabled
            and queue_panel.reactive_harmonic_panel_check.isChecked()
        )
        _set_reactive_harmonic_surface_data(
            harmonic_debug_spectrum,
            harmonic_debug_chroma,
            chord=harmonic_debug_chord,
            chord_confidence=harmonic_debug_confidence,
            chord_tones=harmonic_debug_chord_tones,
            root_note=harmonic_debug_root_note,
            non_chord_tones=harmonic_debug_non_chord_tones,
            predicted_chord=str(
                chord_prediction.get("chord", "") or ""
            ),
            prediction_seconds=chord_prediction_seconds,
            prediction_confidence=float(
                chord_prediction.get("confidence", 0.0) or 0.0
            ),
            prediction_mismatch=chord_prediction_mismatch,
        )
        # The visualizer follows the analyzer's causal estimate immediately.
        # The history remains confirmation/beat-aligned for prediction and
        # lighting events, so a deliberate commit delay cannot masquerade as
        # analyzer lag in the diagnostic UI.
        if live_chord:
            previous_chords = detected_bar_chord_history
            if chord_prediction_mismatch:
                prediction_text = (
                    " | Prediction miss: "
                    f"{chord_prediction_mismatch.get('predicted_chord', '?')}"
                    " -> "
                    f"{chord_prediction_mismatch.get('actual_chord', live_chord)}"
                )
            elif chord_prediction:
                prediction_text = (
                    f" | Next: {chord_prediction.get('chord', '?')}"
                    + (
                        f" in {float(chord_prediction_seconds):.1f}s"
                        if chord_prediction_seconds is not None
                        else ""
                    )
                )
            else:
                prediction_text = ""
            previous_text = (
                " → ".join(previous_chords) if previous_chords else "—"
            )
            confirmation_text = (
                " (confirming change)"
                if chord_change_confirming
                else ""
            )
            queue_panel.reactive_chord_label.setText(
                f"Current detected chord: {live_chord}{confirmation_text} · "
                f"Previous bars: {previous_text}{prediction_text}"
            )
            _set_reactive_chord_surface_data(
                live_chord,
                previous_chords,
            )
        elif harmonic_enabled:
            queue_panel.reactive_chord_label.setText(
                "Current detected chord: acquiring · Previous bars: —"
            )
            _set_reactive_chord_surface_data(
                status="Acquiring",
            )
        else:
            queue_panel.reactive_chord_label.setText(
                "Current detected chord: — · Harmonic structure is off"
            )
            _set_reactive_chord_surface_data(
                status="Harmonic structure is off",
            )

        if active_profile_name:
            profile_text = f"Active profile: {active_profile_name}"
            if profile_cycle_mode == "song_change":
                profile_text += " · cycles on detected song changes"
            queue_panel.reactive_profile_label.setText(profile_text)
        else:
            queue_panel.reactive_profile_label.setText("Active profile: active/default profile")

        live_color_profile = str(
            queue_panel.reactive_live_color_profile_combo.currentData()
            or ""
        )
        if live_color_profile:
            selected_live_colors = tuple(PALETTES.get(live_color_profile, ()))
            live_colors = (
                runtime_palette_override
                if active_reactive and runtime_palette_override
                else selected_live_colors
            )
            queue_panel.reactive_active_palette_label.setText(
                (
                    "Active live colors: "
                    if active_reactive and runtime_palette_override
                    else "Queued live colors: "
                )
                + f"{live_color_profile.replace('_', ' ').title()}"
            )
        elif active_palette_name:
            live_colors = runtime_palette_override or active_palette_colors
            queue_panel.reactive_active_palette_label.setText(
                f"Active palette: {active_palette_name}"
            )
        elif active_palette_colors:
            live_colors = runtime_palette_override or active_palette_colors
            queue_panel.reactive_active_palette_label.setText("Active palette")
        else:
            live_colors = runtime_palette_override
            queue_panel.reactive_active_palette_label.setText("Active palette: awaiting selection")
        _render_palette_swatches(
            queue_panel.reactive_active_palette_preview_label,
            live_colors,
            empty_text="No active colors",
            tooltip_prefix="Colors currently applied to Reactive output",
        )

        if palette_cycle_mode == "song_detection":
            queue_panel.reactive_palette_next_label.setText(
                "Time to next palette: Auto (on detected song change)"
            )
        elif palette_cycle_mode == "timed" and palette_seconds_until_next is not None:
            queue_panel.reactive_palette_next_label.setText(
                "Time to next palette: ≤ "
                f"{max(0.0, float(palette_seconds_until_next)):.1f}s "
                "(mood may change sooner)"
            )
        elif palette_cycle_mode == "timed":
            queue_panel.reactive_palette_next_label.setText(
                "Time to next palette: acquiring effect cycle"
            )
        else:
            queue_panel.reactive_palette_next_label.setText("Time to next palette: — (manual)")

        if palette_queue:
            queue_panel.reactive_palette_queue_label.setText(
                f"Palette queue: {'  →  '.join(str(name) for name in palette_queue)}"
            )
        elif palette_cycle_mode == "timed":
            queue_panel.reactive_palette_queue_label.setText(
                "Palette queue: mood-selected (no fixed queue)"
            )
        else:
            queue_panel.reactive_palette_queue_label.setText("Palette queue: —")

        if listening:
            queue_panel.reactive_listening_label.setText("● Input hot — listening")
            queue_panel.reactive_listening_label.setStyleSheet("color: #22c55e; font-weight: 700;")
            queue_panel.reactive_mode_status_label.setText(
                "Reactive engine is listening. Refined song-change, BPM, and stability detection are active."
            )
        else:
            queue_panel.reactive_listening_label.setText("○ Input open — waiting for audio")
            queue_panel.reactive_listening_label.setStyleSheet("color: #f59e0b; font-weight: 700;")
            queue_panel.reactive_mode_status_label.setText(
                "Reactive engine started, but no input callbacks have arrived yet. Check the Reactive audio input in Config."
            )

        if bpm > 0.0:
            cycle_text = (
                f"cycle fit {cyclic_grid_confidence:.0%}"
                if cyclic_grid_bpm > 0.0
                else "cycle fit acquiring"
            )
            queue_panel.reactive_bpm_label.setText(
                f"{_format_reactive_bpm_heading(bpm)} · {cycle_text} · "
                f"timing variation {stability:.3f} · changes {song_boundaries}"
                + (
                    f" · detector resets {automatic_reset_count}"
                    f" ({last_reset_reason})"
                    if automatic_reset_count
                    else ""
                )
                + (f" · meter {meter_text}" if meter_text else "")
                + (
                    f" · tap pulse {manual_tempo_goal_bpm:.1f} BPM"
                    f" ({manual_tempo_confidence:.0%})"
                    if manual_tempo_goal_bpm > 0.0
                    else ""
                )
                + (
                    (
                        " · manual downbeat queued (D)"
                        if manual_beat_pending_kind == "downbeat"
                        else " · manual beat queued (S)"
                    )
                    if downbeat_nudge_pending
                    else (
                        f" · manual downbeat ×{downbeat_nudge_count}"
                        if downbeat_nudge_count
                        else ""
                    )
                )
            )
            queue_panel.reactive_bpm_label.setToolTip(
                "Timing variation is the Director's raw beat-jitter metric: lower values are steadier. "
                "It is not a percentage confidence score. Cycle fit is the confidence of the 24-second "
                "recurring-onset grid; it treats syncopated onsets as pattern evidence, not individual beats."
            )
        else:
            queue_panel.reactive_bpm_label.setText(
                _format_reactive_bpm_heading(bpm)
            )
            queue_panel.reactive_bpm_label.setToolTip(
                "The refined detector needs several seconds of active input before it can stabilize BPM."
            )
        queue_panel.reactive_beat_indicator_label.setStyleSheet(
            "color: #facc15; font-size: 28px;" if beat_is_flashing else "color: #475569; font-size: 22px;"
        )
        queue_panel.reactive_beat_indicator_label.setToolTip(
            "Beat detector: flashing on the refined BPM detector's metronome."
        )

    def _set_live_mode(mode: str, *, announce: bool = True) -> None:  # pragma: no cover - Qt only
        selected_mode = "reactive" if mode == "reactive" else "queue"
        live_mode_state["value"] = selected_mode
        for button, button_mode in (
            (queue_panel.queue_mode_button, "queue"),
            (queue_panel.reactive_mode_button, "reactive"),
        ):
            blocker = QtCore.QSignalBlocker(button)
            button.setChecked(button_mode == selected_mode)
            del blocker
        queue_visible = selected_mode == "queue"
        queue_panel.live_queue_toolbar.setVisible(queue_visible)
        queue_panel.live_queue_group.setVisible(queue_visible)
        queue_panel.live_palette_group.setVisible(queue_visible)
        queue_panel.show_override_group.setVisible(queue_visible)
        queue_panel.live_reactive_group.setVisible(not queue_visible)
        if queue_visible:
            queue_panel.reactive_mode_status_label.setText(
                "Reactive settings are ready in Config. Choose Reactive Mode to cue them."
            )
        else:
            _render_reactive_live_state()
        if announce:
            queue_controller.set_status(
                "Reactive mode cued. Press Space to start listening."
                if selected_mode == "reactive"
                else "Queue mode selected. Local and Spotify queues are available."
            )
        _render_queue_state(queue_controller.state)

    def _render_queue_state(state: QueueState) -> None:
        selected = state.selected_track
        spotify_available = bool(
            queue_panel.live_loopback_check.isChecked() and live_mode_state["value"] == "queue"
        )
        queue_panel.spotify_group.setVisible(spotify_available)
        queue_panel.playlist_label.setText(
            f"Live source: {state.playlist_source_path}" if state.playlist_source_path else "No local queue loaded."
        )
        queue_panel.local_list.blockSignals(True)
        queue_panel.local_list.clear()
        for track in state.local_tracks:
            item = QtWidgets.QListWidgetItem()
            item.setData(QtCore.Qt.ItemDataRole.UserRole, track.track_key)
            item.setData(QtCore.Qt.ItemDataRole.UserRole + 1, track.queue_index)
            item.setToolTip(track.path)
            queue_panel.local_list.addItem(item)
            row = QtWidgets.QWidget(queue_panel.local_list)
            row_layout = QtWidgets.QHBoxLayout(row)
            row_layout.setContentsMargins(6, 2, 4, 2)
            row_layout.setSpacing(6)
            title = QtWidgets.QToolButton()
            prefix = "▶ " if track.is_current else ""
            title.setText(f"{prefix}{track.display_name}")
            title.setToolTip(track.display_name)
            title.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly)
            title.setMinimumWidth(0)
            title.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
            title.setStyleSheet("QToolButton { text-align: left; border: 0; padding: 2px; }")
            title.clicked.connect(
                lambda _checked=False, target_item=item: queue_panel.local_list.setCurrentItem(target_item)
            )
            row_layout.addWidget(title, 1)
            palette_label = track.assignment_label or "Palette: default"
            if track.edit_lock_reason:
                palette_label = f"{palette_label} — {track.edit_lock_reason}"
            detail = QtWidgets.QLabel(palette_label)
            detail.setToolTip(palette_label)
            detail.setStyleSheet("color: #9ca8bb;")
            detail.setMinimumWidth(0)
            detail.setMaximumWidth(300)
            detail.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
            detail.setAlignment(
                QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
            )
            row_layout.addWidget(detail, 0)
            if track.is_current:
                skip_button = QtWidgets.QToolButton()
                skip_button.setText("⏭")
                skip_button.setToolTip("Skip current song")
                skip_button.clicked.connect(lambda _checked=False: _skip_current_queue_track())
                row_layout.addWidget(skip_button)
            else:
                remove_button = QtWidgets.QToolButton()
                remove_button.setText("−")
                remove_button.setToolTip("Remove from queue (Delete when the row is selected)")
                remove_button.clicked.connect(
                    lambda _checked=False, index=track.queue_index: _remove_queue_item(index)
                )
                row_layout.addWidget(remove_button)
            item.setSizeHint(row.sizeHint())
            queue_panel.local_list.setItemWidget(item, row)
            if track.track_key == state.selected_track_key:
                queue_panel.local_list.setCurrentItem(item)
        queue_panel.local_list.blockSignals(False)

        if spotify_available:
            queue_panel.spotify_list.clear()
            if state.spotify_current:
                queue_panel.spotify_list.addItem(f"Now Playing: {state.spotify_current}")
            for item in state.spotify_upcoming:
                queue_panel.spotify_list.addItem(item)

        if selected is None:
            queue_panel.selected_song_label.setText("Selected song: none")
            queue_panel.selected_assignment_label.setText("Assigned palette: none")
            queue_panel.selected_path_label.setText("Path: none")
            queue_panel.live_show_source_label.setText("Show: waiting for a selected song")
            _render_live_palette_preview(())
            if show_patch_controller.state.selected_track_key:
                show_patch_controller.bind_track("", "", "")
        else:
            queue_panel.selected_song_label.setText(f"Selected song: {selected.display_name}")
            queue_panel.selected_assignment_label.setText(
                f"Assigned palette: {selected.assignment_label or 'none'}"
            )
            queue_panel.selected_path_label.setText(f"Path: {selected.path}")
            session = _active_local_session()
            source = ""
            if session is not None:
                snapshot = session.session_snapshot()
                if selected.is_current:
                    source = str(snapshot.get("timeline_source", "") or "")
                elif hasattr(session, "prepared_timeline_source"):
                    source = str(session.prepared_timeline_source(selected.queue_index) or "")
            if not source:
                source = str(
                    precompiled_queue_state["sources"].get(
                        str(Path(selected.path).resolve()), ""
                    )
                    or ""
                )
            if source:
                state_label = "playing" if selected.is_current else "prepared"
                queue_panel.live_show_source_label.setText(f"Show ({state_label}): {source}")
            elif session is not None:
                queue_panel.live_show_source_label.setText(
                    "Show: not prepared yet — will use cache/analysis when available, otherwise hot compile."
                )
            else:
                queue_panel.live_show_source_label.setText(
                    "Show: pending — starts from a prepared show/cache when available, otherwise hot compile."
                )
            preview_colors = tuple(selected.assignment_colors)
            if not preview_colors and palette_choices:
                preview_colors = tuple(str(color) for color in palette_choices[0]["colors"])
            _render_live_palette_preview(preview_colors)
            if show_patch_controller.state.selected_track_key != selected.track_key:
                show_patch_controller.bind_track(
                    selected.track_key,
                    selected.display_name,
                    selected.path,
                )

        editable = bool(selected is not None and selected.show_editable)
        queue_panel.cycle_palette_button.setEnabled(editable and bool(palette_choices))
        queue_panel.recompile_palette_button.setEnabled(editable)
        for control in (
            queue_panel.patch_name_edit,
            queue_panel.patch_rules_edit,
            queue_panel.save_patch_button,
            queue_panel.clear_patch_button,
        ):
            control.setEnabled(editable)
        if selected is None:
            queue_panel.live_palette_status_label.setText("Select an upcoming song to cycle its pre-generated palette.")
        elif not editable:
            queue_panel.live_palette_status_label.setText(f"{selected.edit_lock_reason}. Queue order may still be changed.")
        elif not palette_choices:
            queue_panel.live_palette_status_label.setText("Load or select a palette profile in the Palettes tab first.")
        else:
            queue_panel.live_palette_status_label.setText(
                "Cycle Palette selects from the active profile. Recompile Show recolors only the prepared cue data."
            )
        queue_panel.status_label.setText(state.status_message)
        _render_show_patch_state()
        _sync_show_action_states()

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
            "local",
            "local_preview",
            "pipeline_playback",
            "local_playlist",
        } and duration_seconds > 0.0
        queue_panel.show_pause_button.setText("Resume" if playback_state == "paused" else "Pause")
        queue_panel.show_pause_button.setEnabled(show_capable and playback_state in {"playing", "paused"})
        queue_panel.show_stop_button.setEnabled(show_capable and playback_state not in {"idle", "finished", "stopped"})
        live_capable = active_mode in {"local", "local_preview", "local_playlist"}
        queue_panel.start_preview_button.setEnabled(active_mode == "idle")
        queue_panel.pause_playback_button.setText("Resume" if playback_state == "paused" else "Pause")
        queue_panel.pause_playback_button.setEnabled(
            live_capable and playback_state in {"playing", "paused"}
        )

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

    def _sync_list_widget(
        list_widget,
        entries: tuple[tuple[str, object], ...],
    ) -> None:
        current = tuple(
            (
                list_widget.item(index).text(),
                list_widget.item(index).data(QtCore.Qt.ItemDataRole.UserRole),
            )
            for index in range(list_widget.count())
        )
        if current == entries:
            return
        blocker = QtCore.QSignalBlocker(list_widget)
        try:
            while list_widget.count() > len(entries):
                item = list_widget.takeItem(list_widget.count() - 1)
                del item
            while list_widget.count() < len(entries):
                list_widget.addItem(QtWidgets.QListWidgetItem(""))
            for index, (text, data) in enumerate(entries):
                item = list_widget.item(index)
                item.setText(text)
                item.setData(QtCore.Qt.ItemDataRole.UserRole, data)
        finally:
            del blocker

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
        _sync_list_widget(
            queue_panel.recent_saved_list,
            tuple((Path(path).name, path) for path in runtime_state.recent_saved_shows),
        )
        _sync_show_playback_controls(runtime_state)
        ready_entries: list[tuple[str, object]] = []
        for item in runtime_state.ready_items:
            title = item.display_label
            duration_suffix = f" ({item.duration:.1f}s)" if item.duration is not None else ""
            suffix = f" [{item.state}]{duration_suffix}"
            ready_entries.append((f"{title}{suffix}", item.item_id))
        _sync_list_widget(queue_panel.ready_list, tuple(ready_entries))
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
            queue_panel.baked_status_label.setText("Baked: unavailable")
            queue_panel.playback_track_label.setText(f"Current track: error ({error})")
            queue_panel.playback_time_label.setText("Time: 00:00 / 00:00")
            queue_panel.device_status_label.setText("Devices: unavailable")
            queue_panel.audio_output_label.setText("Audio output: unavailable")
            queue_panel.input_device_status_label.setText("Input device: unavailable")
            return

        if snapshot is None:
            queue_panel.playback_status_label.setText("Playback: idle")
            queue_panel.baked_status_label.setText(
                f"Baked: {queue_panel.baked_playback_combo.currentData() or 'auto'}"
            )
            queue_panel.playback_track_label.setText("Current track: none")
            queue_panel.playback_time_label.setText("Time: 00:00 / 00:00")
            queue_panel.device_status_label.setText("Devices: preview mode (no connected devices)")
            queue_panel.audio_output_label.setText("Audio output: system default")
            queue_panel.input_device_status_label.setText("Input device: system default")
            return

        state = str(snapshot.get("playback_state", "idle")).replace("_", " ")
        prefix = "running" if running else "stopped"
        queue_panel.playback_status_label.setText(f"Playback: {state} ({prefix})")
        baked_mode = str(snapshot.get("playback_mode_used", "") or "")
        baked_reason = str(snapshot.get("baked_validation_reason", "") or "")
        if baked_mode == "baked":
            queue_panel.baked_status_label.setText("Baked: active")
        elif baked_mode == "live" and baked_reason:
            queue_panel.baked_status_label.setText(f"Baked: skipped ({baked_reason})")
        else:
            queue_panel.baked_status_label.setText(
                f"Baked: {queue_panel.baked_playback_combo.currentData() or 'auto'}"
            )

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
        if not queue_drag_state["active"]:
            _render_queue_state(queue_controller.state)

    def _remove_queue_item(index: int | None) -> None:  # pragma: no cover - Qt only
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

    def _remove_selected_queue_item() -> None:  # pragma: no cover - Qt only
        _remove_queue_item(_selected_queue_index())

    def _on_live_queue_drag_started() -> None:  # pragma: no cover - Qt only
        queue_drag_state["active"] = True

    def _on_live_queue_drag_finished() -> None:  # pragma: no cover - Qt only
        queue_drag_state["active"] = False
        _rebind_local_queue()

    def _skip_current_queue_track() -> None:  # pragma: no cover - Qt only
        session = _active_local_session()
        playlist = playlist_state["playlist"]
        if session is not None and hasattr(session, "queue_snapshot"):
            try:
                queue_controller.skip_current(session)
                queue_controller.set_status("Skip requested for the current song.")
            except Exception as exc:
                queue_controller.set_status(str(exc))
        elif session is not None:
            runtime_supervisor.stop_output_only()
            queue_controller.set_status("Single-song playback stopped.")
        elif playlist is not None:
            if playlist.next() is None:
                queue_controller.set_status("The queue has no next song to skip to.")
            else:
                queue_controller.bind_playlist(
                    playlist,
                    assignments=assignments,
                    source_path=queue_controller.state.playlist_source_path,
                )
                queue_controller.set_status("Current queued song skipped.")
        else:
            queue_controller.set_status("Load a local queue before skipping a song.")
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

    def _toggle_queue_repeat(enabled: bool) -> None:  # pragma: no cover - Qt only
        session = _active_local_session()
        playlist = playlist_state["playlist"]
        try:
            if session is not None and hasattr(session, "set_repeat"):
                queue_service.set_local_repeat(session, enabled)
            elif playlist is not None:
                queue_service.set_playlist_repeat(playlist, enabled)
            queue_controller.set_status(f"Queue repeat {'enabled' if enabled else 'disabled'}.")
        except Exception as exc:
            queue_controller.set_status(str(exc))
        _render_queue_state(queue_controller.state)

    def _spotify_client_for_management():  # pragma: no cover - Qt only
        if not queue_panel.live_loopback_check.isChecked():
            queue_controller.set_status("Enable Spotify live loopback in Config before managing the Spotify queue.")
            return None
        client = spotify_runtime.get("client")
        if client is None:
            queue_controller.set_status("Spotify loopback is unavailable; connect Spotify in Config first.")
        return client

    def _refresh_spotify_queue() -> None:  # pragma: no cover - Qt only
        client = _spotify_client_for_management()
        if client is not None:
            try:
                queue_controller.refresh_spotify(client=client)
                queue_controller.set_status("Spotify queue refreshed.")
            except Exception as exc:
                queue_controller.set_status(f"Could not refresh Spotify queue: {exc}")
        _render_queue_state(queue_controller.state)

    def _spotify_skip() -> None:  # pragma: no cover - Qt only
        client = _spotify_client_for_management()
        if client is not None:
            try:
                queue_service.spotify_skip(client)
                queue_controller.set_status("Spotify skip requested.")
            except Exception as exc:
                queue_controller.set_status(f"Could not skip Spotify: {exc}")
        _render_queue_state(queue_controller.state)

    def _spotify_toggle_shuffle() -> None:  # pragma: no cover - Qt only
        client = _spotify_client_for_management()
        if client is not None:
            try:
                watcher = spotify_runtime.get("watcher")
                playback_state = getattr(watcher, "playback_state", None) if watcher else None
                enabled = not bool(getattr(playback_state, "shuffle", False))
                queue_service.spotify_set_shuffle(client, enabled)
                queue_controller.set_status(f"Spotify shuffle {'enabled' if enabled else 'disabled'}.")
            except Exception as exc:
                queue_controller.set_status(f"Could not set Spotify shuffle: {exc}")
        _render_queue_state(queue_controller.state)

    def _spotify_add_to_queue() -> None:  # pragma: no cover - Qt only
        client = _spotify_client_for_management()
        uri = queue_panel.spotify_uri_edit.text().strip()
        if client is not None:
            if not uri:
                queue_controller.set_status("Enter a Spotify track or episode URI first.")
            else:
                try:
                    queue_service.spotify_add_to_queue(client, uri)
                    queue_panel.spotify_uri_edit.clear()
                    queue_controller.set_status("Added to the Spotify queue.")
                except Exception as exc:
                    queue_controller.set_status(f"Could not add to Spotify queue: {exc}")
        _render_queue_state(queue_controller.state)

    def _spotify_capture_timing_snapshot():  # pragma: no cover - Qt only
        watcher = spotify_runtime.get("watcher")
        if watcher is None:
            return None
        return queue_service.spotify_capture_timing_snapshot(watcher)

    def _spotify_capture_track_changed(new_track, old_track) -> None:  # pragma: no cover - Qt only
        timing_data = queue_service.spotify_track_change_timing(new_track, old_track)
        runtime_supervisor.notify_capture_track_change(timing_data)

    def _set_live_loopback_enabled(enabled: bool) -> None:  # pragma: no cover - Qt only
        runtime_supervisor.set_live_loopback_enabled(enabled)
        watcher = spotify_runtime.get("watcher")
        client = spotify_runtime.get("client")
        if not enabled:
            runtime_supervisor.set_capture_timing_source(None)
            if watcher is not None:
                watcher.stop()
            if client is not None:
                client.close()
            spotify_runtime.update(client=None, watcher=None, error="")
            queue_controller.set_status("Spotify live loopback disabled.")
        elif client is None:
            try:
                from dreamsync.spotify.auth import TokenStore
                from dreamsync.spotify.client import SpotifyClient
                from dreamsync.spotify.queue_watcher import SpotifyQueueWatcher

                token_store = TokenStore()
                if not token_store.access_token:
                    raise RuntimeError("No Spotify token is available. Connect Spotify before enabling live loopback.")
                client = SpotifyClient(token_store)
                watcher = SpotifyQueueWatcher(
                    client,
                    on_track_changed=_spotify_capture_track_changed,
                )
                spotify_runtime.update(client=client, watcher=watcher, error="")
                runtime_supervisor.set_capture_timing_source(_spotify_capture_timing_snapshot)
                watcher.start()
                queue_controller.set_status("Spotify live loopback enabled.")
            except Exception as exc:
                runtime_supervisor.set_capture_timing_source(None)
                if watcher is not None:
                    watcher.stop()
                if client is not None:
                    client.close()
                spotify_runtime.update(client=None, watcher=None, error=str(exc))
                queue_controller.set_status(f"Spotify live loopback could not start: {exc}")
        _render_queue_state(queue_controller.state)
        _render_runtime_state(runtime_supervisor.snapshot())

    def _apply_palette_choices(
        choices: list[dict[str, object]],
        *,
        status: str,
    ) -> None:
        palette_choices[:] = list(choices)
        queue_controller.set_status(status)
        _render_queue_state(queue_controller.state)

    def _load_playlist(path: Path) -> None:
        try:
            playlist = queue_service.build_playlist(
                path,
                repeat=bool(queue_panel.repeat_button.isChecked()),
            )
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

    def _validate_reactive_configuration(
        reactive_settings: ReactiveSettings,
    ) -> tuple[str, ...]:
        errors = list(reactive_settings.validate())
        candidate_profiles: list[object] = []

        def _load_candidate(value: str, label: str) -> None:
            try:
                candidate_profiles.append(
                    profile_service.load_profile(resolve_profile_path(value))
                )
            except Exception as exc:
                errors.append(f"{label} could not be loaded: {exc}")

        if reactive_settings.profile_strategy == "override_profile":
            if reactive_settings.profile_override_path:
                _load_candidate(
                    reactive_settings.profile_override_path,
                    "Selected Reactive profile",
                )
        elif reactive_settings.profile_strategy in {
            "song_change_rotation",
            "profile_rotation",
            "smart_rotation",
        }:
            for value in reactive_settings.rotation_profiles:
                _load_candidate(value, f"Rotation profile '{value}'")
        else:
            try:
                active_profile = _current_base_profile()
            except Exception as exc:
                errors.append(f"Active Reactive profile could not be loaded: {exc}")
            else:
                if active_profile is not None:
                    candidate_profiles.append(active_profile)

        if reactive_settings.show_palette_set:
            if not candidate_profiles:
                errors.append(
                    "Choose a valid Reactive profile before using a Show Palette set."
                )
            for candidate in candidate_profiles:
                if reactive_settings.show_palette_set not in candidate.show_palette_sets:
                    errors.append(
                        f"Show Palette set '{reactive_settings.show_palette_set}' "
                        f"does not exist in profile '{candidate.name}'."
                    )

        return tuple(dict.fromkeys(errors))

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

    def _on_show_palette_set_selected() -> None:  # pragma: no cover - Qt only
        set_name = str(profile_panel.show_palette_set_combo.currentData() or "")
        if not set_name:
            return
        _render_profile_state(palette_controller.select_show_palette_set(set_name))

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
            rng_seed = secrets.randbits(64) if profile_panel.seed_randomness_check.isChecked() else None
            generated = profile_service.generate_palette_from_seed_colors(
                colors,
                scheme=scheme,
                rng_seed=rng_seed,
            )
            state = palette_controller.set_palette_colors(generated)
        except Exception as exc:
            _set_page_error(profile_panel.status_label, exc)
            return
        _render_profile_state(state)

    def _load_profile_editor_file() -> None:  # pragma: no cover - Qt only
        selected, _filter = QtWidgets.QFileDialog.getOpenFileName(
            window,
            "Load Profile",
            str(_profile_load_directory()),
            "YAML Files (*.yaml *.yml);;All Files (*.*)",
        )
        if not selected:
            return
        try:
            _load_profile_into_editor(Path(selected), status="Loaded profile.")
        except Exception as exc:
            _set_page_error(profile_panel.status_label, exc)

    def _refresh_profile_library() -> None:  # pragma: no cover - Qt only
        configured = queue_panel.profile_directory_edit.text().strip()
        directories = (Path(configured),) if configured else ()
        requested_tags = tuple(
            value.strip()
            for value in profile_panel.library_tags_edit.text().split(",")
            if value.strip()
        )
        entries = profile_service.search_profiles(
            directories,
            query=profile_panel.library_search_edit.text(),
            tags=requested_tags,
        )
        profile_library_state["entries"] = entries
        profile_panel.profile_library_list.clear()
        for entry in entries:
            tags = f" [{', '.join(entry.tags)}]" if entry.tags else ""
            validity = "valid" if entry.valid else f"invalid: {entry.error}"
            item = QtWidgets.QListWidgetItem(
                f"{entry.name}{tags} — {entry.palette_count} palettes, "
                f"{len(entry.moods)} moods — {validity}"
            )
            item.setData(QtCore.Qt.ItemDataRole.UserRole, str(entry.path))
            item.setToolTip(str(entry.path))
            profile_panel.profile_library_list.addItem(item)
        profile_panel.status_label.setText(f"Profile library: {len(entries)} result(s).")

    def _load_selected_library_profile() -> None:  # pragma: no cover - Qt only
        item = profile_panel.profile_library_list.currentItem()
        if item is None:
            profile_panel.status_label.setText("Select a profile in the library first.")
            return
        path = Path(str(item.data(QtCore.Qt.ItemDataRole.UserRole)))
        try:
            _load_profile_into_editor(path, status="Loaded library profile.")
        except Exception as exc:
            _set_page_error(profile_panel.status_label, exc)

    def _preview_generated_profile_pool() -> None:  # pragma: no cover - Qt only
        seed = (
            int(profile_panel.generation_seed_spin.value())
            if profile_panel.generation_seed_check.isChecked()
            else None
        )
        try:
            profiles = profile_service.generate_profile_pool(
                int(profile_panel.generation_pool_size_spin.value()),
                seed=seed,
            )
        except Exception as exc:
            _set_page_error(profile_panel.status_label, exc)
            return
        profile_library_state["generated"] = profiles
        profile_library_state["generated_seed"] = seed
        profile_panel.generated_profile_combo.clear()
        for index, profile in enumerate(profiles):
            profile_panel.generated_profile_combo.addItem(
                f"{index + 1}. {profile.name} [{', '.join(profile.tags)}]",
                index,
            )
        profile_panel.status_label.setText(
            f"Previewing {len(profiles)} generated profiles"
            + (f" with seed {seed}." if seed is not None else ".")
        )

    def _export_generated_profile() -> None:  # pragma: no cover - Qt only
        profiles = profile_library_state["generated"]
        index = int(profile_panel.generated_profile_combo.currentData() or 0)
        if not profiles or not 0 <= index < len(profiles):
            profile_panel.status_label.setText("Preview a generated profile pool first.")
            return
        profile = profiles[index]
        selected, _filter = QtWidgets.QFileDialog.getSaveFileName(
            window,
            "Save Generated Profile As",
            str(_profile_load_directory() / f"{profile.name.lower().replace(' ', '-')}.yaml"),
            "YAML Profiles (*.yaml *.yml)",
        )
        if not selected:
            return
        try:
            path = profile_service.export_generated_profile(
                profile,
                Path(selected),
                seed=profile_library_state["generated_seed"],
                index=index,
            )
        except Exception as exc:
            _set_page_error(profile_panel.status_label, exc)
            return
        profile_panel.status_label.setText(f"Generated profile saved: {path.name}.")
        _refresh_profile_library()

    def _preview_profile_crossfade() -> None:  # pragma: no cover - Qt only
        try:
            source = _current_base_profile()
        except Exception as exc:
            _set_page_error(profile_panel.status_label, exc)
            return
        generated = profile_library_state["generated"]
        if source is None or not generated:
            profile_panel.status_label.setText(
                "Load a source profile and preview a generated pool before cross-fade preview."
            )
            return
        index = int(profile_panel.generated_profile_combo.currentData() or 0)
        target = generated[index]
        source_colors = next(iter(source.palettes.values()))
        target_colors = next(iter(target.palettes.values()))
        dialog = QtWidgets.QDialog(window)
        dialog.setWindowTitle("Profile Cross-fade Preview — Simulation Only")
        layout = QtWidgets.QVBoxLayout(dialog)
        title = QtWidgets.QLabel(f"{source.name} → {target.name}")
        preview = QtWidgets.QLabel()
        preview.setMinimumHeight(100)
        preview.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        slider.setRange(0, 100)
        slider.setValue(50)

        def _render_blend(value: int) -> None:
            phase = value / 100.0
            left = QtGui.QColor(source_colors[0])
            right = QtGui.QColor(target_colors[0])
            red = round(left.red() + ((right.red() - left.red()) * phase))
            green = round(left.green() + ((right.green() - left.green()) * phase))
            blue = round(left.blue() + ((right.blue() - left.blue()) * phase))
            color = QtGui.QColor(red, green, blue).name()
            preview.setText(f"{value}% · {color}\nSimulation preview; no output adapter is started.")
            preview.setStyleSheet(
                f"background-color: {color}; color: {'#ffffff' if left.lightness() < 140 else '#111111'};"
                "border: 1px solid #64748b; font-weight: 600;"
            )

        slider.valueChanged.connect(_render_blend)
        _render_blend(slider.value())
        close_button = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Close
        )
        close_button.rejected.connect(dialog.reject)
        layout.addWidget(title)
        layout.addWidget(preview)
        layout.addWidget(slider)
        layout.addWidget(close_button)
        dialog.exec()

    def _browse_reactive_profile_file() -> None:  # pragma: no cover - Qt only
        selected, _filter = QtWidgets.QFileDialog.getOpenFileName(
            window,
            "Choose Reactive Profile",
            str(_profile_load_directory()),
            "YAML Profile Files (*.yaml *.yml);;All Files (*.*)",
        )
        if not selected:
            return
        selected_path = Path(selected).resolve()
        try:
            selected_profile = profile_service.load_profile(selected_path)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                window,
                "Invalid Profile File",
                f"The selected file could not be loaded as a DreamSync profile:\n\n{exc}",
            )
            return

        _set_reactive_profile_override_path(str(selected_path))
        selected_set = str(
            queue_panel.reactive_show_palette_set_combo.currentData() or ""
        )
        queue_panel.reactive_show_palette_set_combo.blockSignals(True)
        queue_panel.reactive_show_palette_set_combo.clear()
        queue_panel.reactive_show_palette_set_combo.addItem("Use mood palettes", "")
        for set_name in selected_profile.show_palette_sets:
            queue_panel.reactive_show_palette_set_combo.addItem(set_name, set_name)
        if selected_set and selected_set not in selected_profile.show_palette_sets:
            queue_panel.reactive_show_palette_set_combo.addItem(
                f"{selected_set} (not in selected profile)",
                selected_set,
            )
        _select_combo_data(queue_panel.reactive_show_palette_set_combo, selected_set)
        queue_panel.reactive_show_palette_set_combo.blockSignals(False)
        _on_runtime_settings_changed()

    def _browse_directory(edit, title: str) -> None:  # pragma: no cover - Qt only
        current = str(edit.text()).strip()
        initial = Path(current).expanduser() if current else Path.cwd()
        selected = QtWidgets.QFileDialog.getExistingDirectory(window, title, str(initial))
        if selected:
            edit.setText(str(Path(selected)))

    def _browse_device_room_config() -> None:  # pragma: no cover - Qt only
        current = str(queue_panel.device_room_config_path_edit.text()).strip()
        initial = Path(current).expanduser() if current else Path.cwd()
        selected, _filter = QtWidgets.QFileDialog.getOpenFileName(
            window,
            "Select Device / Room Layout Config",
            str(initial),
            "YAML Files (*.yaml *.yml);;All Files (*.*)",
        )
        if selected:
            queue_panel.device_room_config_path_edit.setText(str(Path(selected)))

    def _apply_file_locations() -> bool:  # pragma: no cover - Qt only
        nonlocal config_path
        raw_config_path = str(queue_panel.device_room_config_path_edit.text()).strip()
        if not raw_config_path:
            queue_controller.set_status("Choose a device / room-layout config file before applying locations.")
            queue_panel.file_locations_status_label.setText(
                "Changes not applied: choose a device / room-layout config first."
            )
            _render_queue_state(queue_controller.state)
            return False
        config_path = Path(raw_config_path).expanduser()
        runtime_supervisor.set_config_path(config_path)
        _reload_spatial_scene(status=f"Using device / room layout config: {config_path.name}.")
        queue_controller.set_status("File locations applied. Folder defaults will be used by the next file dialog.")
        queue_panel.file_locations_status_label.setText(
            "Changes applied. Local audio, profile, and Show dialogs will use these folders."
        )
        queue_panel.file_locations_status_label.setStyleSheet("color: #15803d; font-weight: 600;")
        _sync_device_health_monitoring(refresh=True)
        _render_queue_state(queue_controller.state)
        _render_runtime_state(runtime_supervisor.snapshot())
        _refresh_storage()
        return True

    def _format_storage_bytes(value: int) -> str:
        size = float(max(0, value))
        for unit in ("B", "KiB", "MiB", "GiB"):
            if size < 1024.0 or unit == "GiB":
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} GiB"

    def _capture_storage_path() -> Path:
        value = queue_panel.capture_dir_edit.text().strip() or "captured_songs"
        return Path(value).expanduser()

    def _refresh_storage() -> None:  # pragma: no cover - Qt only
        try:
            snapshot = storage_service.snapshot(_capture_storage_path())
        except Exception as exc:
            queue_panel.storage_cache_label.setText(f"Storage unavailable: {exc}")
            queue_panel.storage_capture_label.setText("")
            return
        queue_panel.storage_cache_label.setText(
            f"Cache: {snapshot.cache.file_count} entries · "
            f"{_format_storage_bytes(snapshot.cache.total_bytes)} · {snapshot.cache.path}"
        )
        queue_panel.storage_capture_label.setText(
            f"Captures: {snapshot.captures.file_count} MP3 files · "
            f"{_format_storage_bytes(snapshot.captures.total_bytes)} · {snapshot.captures.path}"
        )

    def _clear_all_cache() -> None:  # pragma: no cover - Qt only
        answer = QtWidgets.QMessageBox.question(
            window,
            "Clear All Show Cache?",
            f"Delete all compiled cache entries under:\n{storage_service.cache_dir}",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        try:
            count = storage_service.clear_all_cache()
            queue_controller.set_status(f"Cleared {count} compiled cache entr{'y' if count == 1 else 'ies'}.")
        except Exception as exc:
            queue_controller.set_status(f"Could not clear cache: {exc}")
        _refresh_storage()
        _render_queue_state(queue_controller.state)

    def _clear_selected_track_cache() -> None:  # pragma: no cover - Qt only
        selected = queue_controller.state.selected_track
        if selected is None:
            queue_controller.set_status("Select a local queue track before clearing its cache.")
            _render_queue_state(queue_controller.state)
            return
        track_path = Path(selected.path)
        answer = QtWidgets.QMessageBox.question(
            window,
            "Clear Selected Track Cache?",
            f"Delete compiled cache entries for {track_path.name}?",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        try:
            count = storage_service.clear_track_cache(track_path)
            queue_controller.set_status(
                f"Cleared {count} compiled cache entr{'y' if count == 1 else 'ies'} for {track_path.name}."
            )
        except Exception as exc:
            queue_controller.set_status(f"Could not clear selected-track cache: {exc}")
        _refresh_storage()
        _render_queue_state(queue_controller.state)

    def _archive_captures() -> None:  # pragma: no cover - Qt only
        if runtime_supervisor.snapshot().capture_state == "running":
            queue_controller.set_status("Stop Audio Loopback Capture before archiving capture files.")
            _render_queue_state(queue_controller.state)
            return
        preview = storage_service.capture_preview(_capture_storage_path())
        if not preview.files:
            queue_controller.set_status("No MP3 capture files are available to archive.")
            _render_queue_state(queue_controller.state)
            return
        dialog = QtWidgets.QDialog(window)
        dialog.setWindowTitle("Archive Captures")
        layout = QtWidgets.QVBoxLayout(dialog)
        summary = QtWidgets.QLabel(
            f"Archive {len(preview.files)} MP3 file(s) "
            f"({_format_storage_bytes(preview.total_bytes)}) from:\n{preview.directory}\n\n"
            "JSON sidecars will remain untouched."
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)
        keep_originals = QtWidgets.QCheckBox("Keep original MP3 files")
        keep_originals.setChecked(True)
        layout.addWidget(keep_originals)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        if not keep_originals.isChecked():
            answer = QtWidgets.QMessageBox.warning(
                window,
                "Delete Originals After Archive?",
                "The ZIP will be verified first, then the original MP3 files will be deleted. Continue?",
                QtWidgets.QMessageBox.StandardButton.Yes
                | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No,
            )
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                return
        default_path = storage_service.default_archive_path(preview.directory)
        selected, _filter = QtWidgets.QFileDialog.getSaveFileName(
            window,
            "Save Capture Archive",
            str(default_path),
            "ZIP Archives (*.zip)",
        )
        if not selected:
            return
        try:
            archive_path = storage_service.archive_captures(
                preview.directory,
                Path(selected),
                keep_originals=keep_originals.isChecked(),
            )
            queue_controller.set_status(f"Capture archive created: {archive_path.name}.")
        except Exception as exc:
            queue_controller.set_status(f"Could not archive captures: {exc}")
        _refresh_storage()
        _render_queue_state(queue_controller.state)

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
            _set_page_error(profile_panel.status_label, exc)

    def _add_profile_palette() -> None:  # pragma: no cover - Qt only
        _render_profile_state(palette_controller.create_palette())

    def _add_show_palette_set() -> None:  # pragma: no cover - Qt only
        name, accepted = QtWidgets.QInputDialog.getText(
            window,
            "New Show Palette Set",
            "Set name:",
        )
        if not accepted:
            return
        try:
            _render_profile_state(
                palette_controller.create_show_palette_set(name=name)
            )
        except Exception as exc:
            _set_page_error(profile_panel.status_label, exc)

    def _save_show_palette_set() -> None:  # pragma: no cover - Qt only
        try:
            palette_controller.set_show_palette_set_members_text(
                profile_panel.show_palette_set_members_edit.text()
            )
            state = palette_controller.save_show_palette_set()
        except Exception as exc:
            _set_page_error(profile_panel.status_label, exc)
            return
        _render_profile_state(state)

    def _add_profile_palette_color() -> None:  # pragma: no cover - Qt only
        try:
            state = palette_controller.append_palette_color()
        except Exception as exc:
            _set_page_error(profile_panel.status_label, exc)
            return
        _render_profile_state(state)

    def _remove_profile_palette_color() -> None:  # pragma: no cover - Qt only
        try:
            state = palette_controller.remove_palette_color()
        except Exception as exc:
            _set_page_error(profile_panel.status_label, exc)
            return
        _render_profile_state(state)

    def _save_profile_palette() -> None:  # pragma: no cover - Qt only
        try:
            state = palette_controller.save()
        except Exception as exc:
            _set_page_error(profile_panel.status_label, exc)
            return
        _render_profile_state(state)

    def _save_profile_sections() -> None:  # pragma: no cover - Qt only
        try:
            _sync_profile_editor_structured_data()
            state = palette_controller.save_sections()
        except Exception as exc:
            _set_page_error(profile_panel.status_label, exc)
            return
        _render_profile_state(state)

    def _save_profile_all() -> None:  # pragma: no cover - Qt only
        try:
            _sync_profile_editor_structured_data()
            state = palette_controller.save_all()
        except Exception as exc:
            _set_page_error(profile_panel.status_label, exc)
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

    def _current_reactive_effect_bank() -> tuple[str, ...]:
        return tuple(
            str(button.property("effectMode"))
            for button in queue_panel.reactive_live_effect_buttons
            if button.isChecked()
        )

    def _apply_reactive_live_look(
        *_args,
    ) -> None:  # pragma: no cover - Qt only
        """Apply live-only palette and effect-bank controls to Reactive."""
        effect_bank = _current_reactive_effect_bank()
        if not effect_bank:
            first_button = queue_panel.reactive_live_effect_buttons[0]
            blocker = QtCore.QSignalBlocker(first_button)
            first_button.setChecked(True)
            del blocker
            effect_bank = _current_reactive_effect_bank()
            queue_panel.reactive_live_look_status_label.setText(
                "At least one effect must remain enabled; Flash was restored."
            )

        palette_name = str(
            queue_panel.reactive_live_color_profile_combo.currentData()
            or ""
        )
        active_effect = str(
            queue_panel.reactive_live_active_effect_combo.currentData()
            or ""
        )
        runtime_state = runtime_supervisor.snapshot()
        active_reactive = runtime_state.active_output_mode in {
            "reactive",
            "reactive_live",
        }
        if not active_reactive:
            queue_panel.reactive_live_look_status_label.setText(
                "Live look queued; it will apply when Reactive starts."
            )
            _render_reactive_live_state(runtime_state)
            return

        result = runtime_supervisor.update_runtime_control(
            palette_override=tuple(PALETTES.get(palette_name, ())),
            render_mode=active_effect,
            effect_bank=effect_bank,
            speed_multiplier=reactive_effect_tempo_state[
                "multiplier"
            ],
        )
        if result:
            palette_label = (
                palette_name.replace("_", " ").title()
                if palette_name
                else "active profile"
            )
            effect_label = (
                queue_panel.reactive_live_active_effect_combo.currentText()
            )
            queue_panel.reactive_live_look_status_label.setText(
                f"Applied {palette_label} colors with {effect_label}."
            )
        else:
            queue_panel.reactive_live_look_status_label.setText(
                "Reactive is starting; the live look will be retried."
            )
        _render_reactive_live_state(runtime_supervisor.snapshot())

    def _set_reactive_effect_tempo(
        multiplier: float,
    ) -> None:  # pragma: no cover - Qt only
        selected = max(0.5, min(2.0, float(multiplier)))
        reactive_effect_tempo_state["multiplier"] = selected
        runtime_state = runtime_supervisor.snapshot()
        active_reactive = runtime_state.active_output_mode in {
            "reactive",
            "reactive_live",
        }
        result = (
            runtime_supervisor.update_runtime_control(
                speed_multiplier=selected
            )
            if active_reactive
            else {}
        )
        queue_panel.reactive_effect_tempo_label.setText(
            f"Effect tempo: {selected:g}× cycle BPM"
            + ("" if result else " (queued)")
        )
        queue_controller.set_status(
            f"Reactive effect tempo set to {selected:g}× cycle BPM."
        )
        _render_queue_state(queue_controller.state)

    def _set_reactive_cycle_tempo(
        multiplier: float,
    ) -> None:  # pragma: no cover - Qt only
        if _show_text_input_has_focus():
            return
        selected = float(multiplier)
        reactive_cycle_tempo_state["multiplier"] = selected
        applied = runtime_supervisor.set_reactive_cycle_tempo_multiplier(
            selected
        )
        if applied is None:
            queue_controller.set_status(
                "Start Reactive listening before overriding cycle tempo."
            )
        else:
            direction = (
                "half-time"
                if selected == 0.5
                else "double-time"
                if selected == 2.0
                else "normal"
            )
            queue_controller.set_status(
                f"Detector cycle locked to {direction} "
                f"({selected:g}× detected BPM)."
            )
        queue_panel.reactive_cycle_tempo_label.setText(
            f"Cycle tempo: {selected:g}× detector  {{  }}"
        )
        _render_queue_state(queue_controller.state)

    def _adjust_reactive_cycle_tempo(
        factor: float,
    ) -> None:  # pragma: no cover - Qt only
        current = float(
            reactive_cycle_tempo_state.get("multiplier", 1.0) or 1.0
        )
        selected = max(0.5, min(2.0, current * float(factor)))
        _set_reactive_cycle_tempo(selected)

    def _latch_reactive_beat(
        kind: str,
    ) -> None:  # pragma: no cover - Qt only
        if _show_text_input_has_focus():
            return
        normalized = "downbeat" if kind == "downbeat" else "beat"
        revision = runtime_supervisor.request_reactive_beat_latch(normalized)
        if revision is None:
            queue_controller.set_status(
                "Start Reactive listening before registering manual beats."
            )
        else:
            label = "Downbeat" if normalized == "downbeat" else "Beat"
            queue_controller.set_status(
                f"{label} latch requested; it will snap to the nearest "
                "detected beat."
            )
        _render_queue_state(queue_controller.state)
        _render_reactive_live_state()

    def _nudge_reactive_downbeat() -> None:  # pragma: no cover - Qt only
        _latch_reactive_beat("downbeat")

    def _latch_reactive_secondary_beat() -> None:  # pragma: no cover - Qt only
        _latch_reactive_beat("beat")

    def _reset_reactive_detection() -> None:  # pragma: no cover - Qt only
        if _show_text_input_has_focus():
            return
        revision = runtime_supervisor.request_reactive_detection_reset()
        if revision is None:
            queue_controller.set_status(
                "Start Reactive listening before resetting beat detection."
            )
        else:
            queue_controller.set_status(
                "Beat history cleared; reacquiring BPM and meter as a new "
                "detector session."
            )
        _render_queue_state(queue_controller.state)
        _render_reactive_live_state()

    def _refresh_reactive_panel_visibility(
        *_args,
    ) -> None:  # pragma: no cover - Qt only
        _render_reactive_live_state()

    def _start_local_preview() -> None:  # pragma: no cover - Qt only
        source_path = queue_controller.state.playlist_source_path
        if not source_path:
            queue_controller.set_status("Cue audio to compile before starting local playback.")
            _render_queue_state(queue_controller.state)
            return
        playlist = playlist_state["playlist"]
        audio_path = playlist.current if playlist is not None else Path(source_path)
        if audio_path is None:
            queue_controller.set_status("The local queue is empty; cue audio before starting playback.")
            _render_queue_state(queue_controller.state)
            return
        base_profile = _current_base_profile()
        try:
            runtime_supervisor.start_local_playlist(
                Path(audio_path),
                playlist=playlist,
                config_path=config_path,
                profile=base_profile,
                profile_resolver=_profile_resolver,
                precompiled_timelines=dict(precompiled_queue_state["timelines"]),
                precompiled_timeline_sources=dict(precompiled_queue_state["sources"]),
            )
        except Exception as exc:
            _render_session_status(None, running=False, error=exc)
            queue_controller.set_status(str(exc))
            _render_queue_state(queue_controller.state)
            return
        runtime_state = runtime_supervisor.snapshot()
        pending_snapshot = _pending_preview_snapshot(runtime_state)
        _render_session_status(pending_snapshot, running=True)
        queue_controller.set_status(f"Live playback started for {Path(audio_path).name}.")
        _render_preview_simulation({"node_colors": {}})
        _render_queue_state(queue_controller.state)
        _render_runtime_state(runtime_state)
        queue_timer.start()
        preview_timer.start()

    def _toggle_live_playback() -> None:  # pragma: no cover - Qt only
        runtime_state = runtime_supervisor.snapshot()
        if runtime_state.active_output_mode == "idle":
            _start_local_preview()
            return
        if runtime_state.active_output_mode not in {"local", "local_preview", "local_playlist"}:
            queue_controller.set_status("Another output mode is active; stop it before controlling Live playback.")
            _render_queue_state(queue_controller.state)
            return
        result = runtime_supervisor.toggle_output_pause()
        if result == "paused":
            queue_controller.set_status("Live playback paused.")
        elif result == "playing":
            queue_controller.set_status("Live playback resumed.")
        else:
            queue_controller.set_status("Live playback is not ready to pause yet.")
        _render_queue_state(queue_controller.state)
        _render_runtime_state(runtime_supervisor.snapshot())
        if result in {"paused", "playing"}:
            queue_timer.start()
            preview_timer.start()

    def _stop_local_preview() -> None:  # pragma: no cover - Qt only
        runtime_supervisor.stop_output_only()
        preview_timer.stop()
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
        # Compatibility adapter for callers that still hand the UI a legacy
        # one-track timeline.  It becomes a one-Track Show instead of
        # reviving the former single-track Show concept.
        resolved_audio = audio_path or Path(timeline.song_path)
        imported_show = Show(
            name=str(timeline.metadata.get("track_name") or resolved_audio.stem),
            tracks=(ShowTrack(audio_path=str(resolved_audio), timeline=timeline),),
            metadata={"imported_legacy_timeline": True},
        )
        _load_show_into_editor(imported_show, show_path=show_path, status=status)
        if context is not None:
            show_editor_state["context"] = context
        queue_controller.set_status(status)
        _render_queue_state(queue_controller.state)
        _render_runtime_state(runtime_supervisor.snapshot())

    def _compile_show_for_editor() -> None:  # pragma: no cover - Qt only
        if not _current_show_is_editable():
            return
        track = _current_show_track()
        if track is None:
            queue_controller.set_status("Add and select a Track before compiling it.")
            _render_queue_state(queue_controller.state)
            return
        if track.is_compiled:
            queue_controller.set_status(f"{track.display_name} is already compiled.")
            _render_queue_state(queue_controller.state)
            _sync_show_action_states()
            return
        audio_path = Path(track.audio_path)
        base_profile = _current_base_profile()
        profile = _profile_resolver(audio_path, base_profile) if base_profile is not None else None
        patch = show_patch_controller.patch_for_path(audio_path)
        compile_seed = (
            int(queue_panel.compile_seed_spin.value())
            if queue_panel.compile_seed_check.isChecked()
            else None
        )
        try:
            structure, compiled_track, context = _run_show_editor_busy_task(
                f"Compiling Track {audio_path.name}...",
                lambda _report_progress: (
                    lambda compiled: (
                        compiled[0],
                        compiled[1],
                        show_service.build_timeline_context(
                            audio_path=audio_path,
                            timeline=compiled[1].timeline,
                            structure=compiled[0],
                        ),
                    )
                )(
                    show_service.compile_track(
                        track,
                        profile=profile,
                        patch=patch,
                        seed=compile_seed,
                    )
                ),
            )
        except Exception as exc:
            _set_show_editor_notice(f"Could not compile show: {exc}")
            queue_controller.set_status(f"Could not compile show: {exc}")
            _render_queue_state(queue_controller.state)
            return
        _replace_current_show_track(compiled_track)
        _activate_show_track(
            int(show_editor_state["track_index"]),
            status=(
                f"Compiled Track {audio_path.name}"
                + (f" with seed {compile_seed}." if compile_seed is not None else ".")
            ),
            context=context,
        )
        queue_controller.set_status(
            f"Compiled Track {audio_path.name}"
            + (f" with seed {compile_seed}." if compile_seed is not None else ".")
        )
        _render_queue_state(queue_controller.state)

    def _compile_all_show_tracks() -> None:  # pragma: no cover - Qt only
        if not _current_show_is_editable():
            return
        _commit_current_show_track_editor()
        show = _replace_show(name=_show_name_from_form())
        pending = [track for track in show.tracks if not track.is_compiled]
        if not pending:
            queue_controller.set_status("Every Track in this Show is already compiled.")
            _render_queue_state(queue_controller.state)
            _sync_show_action_states()
            return
        base_profile = _current_base_profile()
        compile_seed = (
            int(queue_panel.compile_seed_spin.value())
            if queue_panel.compile_seed_check.isChecked()
            else None
        )

        def _compile_pending_tracks(report_progress):
            compiled_tracks: list[ShowTrack] = []
            pending_index = 0
            for track in show.tracks:
                if track.is_compiled:
                    compiled_tracks.append(track)
                    continue
                pending_index += 1
                audio_path = Path(track.audio_path)
                report_progress(
                    pending_index,
                    len(pending),
                    track.display_name,
                    "Analyzing audio and generating its lighting timeline…",
                    pending_index - 1,
                )
                compiled_track = show_service.compile_track(
                    track,
                    profile=(
                        _profile_resolver(audio_path, base_profile)
                        if base_profile is not None
                        else None
                    ),
                    patch=show_patch_controller.patch_for_path(audio_path),
                    seed=compile_seed,
                )[1]
                compiled_tracks.append(compiled_track)
                report_progress(
                    pending_index,
                    len(pending),
                    track.display_name,
                    "Track compiled. Preparing the next Track…",
                    pending_index,
                )
            return tuple(compiled_tracks)

        try:
            compiled_tracks = _run_show_editor_busy_task(
                f"Preparing {len(pending)} pending Track(s)…",
                _compile_pending_tracks,
                track_total=len(pending),
            )
        except Exception as exc:
            _set_show_editor_notice(f"Could not compile all Tracks: {exc}")
            queue_controller.set_status(f"Could not compile all Tracks: {exc}")
            _render_queue_state(queue_controller.state)
            return
        _replace_show(tracks=tuple(compiled_tracks), name=show.name)
        selected_index = show_editor_state.get("track_index")
        _activate_show_track(
            int(selected_index) if isinstance(selected_index, int) else 0,
            status=(
                f"Compiled all {len(pending)} pending Track(s)"
                + (f" with seed {compile_seed}." if compile_seed is not None else ".")
            ),
        )
        queue_controller.set_status(
            f"Compiled all {len(pending)} pending Track(s)"
            + (f" with seed {compile_seed}." if compile_seed is not None else ".")
        )
        _render_queue_state(queue_controller.state)

    def _choose_saved_show_file(title: str) -> Path | None:  # pragma: no cover - Qt only
        default_directory = _show_save_directory()
        default_directory.mkdir(parents=True, exist_ok=True)
        selected, _filter = QtWidgets.QFileDialog.getOpenFileName(
            window,
            title,
            str(default_directory),
            "Show JSON Files (*.show.json *.json);;All Files (*.*)",
        )
        if not selected:
            return None
        return Path(selected)

    def _load_saved_show_file() -> None:  # pragma: no cover - Qt only
        show_path = _choose_saved_show_file("Load Saved Show")
        if show_path is None:
            return
        try:
            show = _run_show_editor_busy_task(
                f"Loading saved Show {show_path.name}...",
                lambda _report_progress: show_service.load_show_manifest(show_path),
            )
        except Exception as exc:
            _set_show_editor_notice(f"Could not load saved Show: {exc}")
            queue_controller.set_status(f"Could not load saved Show: {exc}")
            _render_queue_state(queue_controller.state)
            return
        _load_show_into_editor(show, show_path=show_path, status=f"Saved Show loaded: {show.name}")
        queue_controller.set_status(f"Saved Show loaded: {show.name}")
        _render_queue_state(queue_controller.state)

    def _load_saved_track_show_file() -> None:  # pragma: no cover - Qt only
        show_path = _choose_saved_show_file("Cue Compiled Track")
        if show_path is None:
            return
        try:
            show = _run_show_editor_busy_task(
                f"Loading compiled track {show_path.name}...",
                lambda _report_progress: show_service.load_precompiled_track_show(show_path),
            )
        except Exception as exc:
            queue_controller.set_status(f"Could not cue compiled track: {exc}")
            _render_queue_state(queue_controller.state)
            return
        track = show.tracks[0]
        track_path = Path(track.audio_path).expanduser()
        if not track_path.is_file():
            queue_controller.set_status(f"Audio Track not found: {track_path}")
            _render_queue_state(queue_controller.state)
            return

        timeline = track.timeline
        if timeline is None:
            queue_controller.set_status("The selected compiled track has no timeline data.")
            _render_queue_state(queue_controller.state)
            return

        resolved_track_path = str(track_path.resolve())
        source_label = f"precompiled track: {show_path.resolve()}"
        precompiled_queue_state["timelines"][resolved_track_path] = timeline
        precompiled_queue_state["sources"][resolved_track_path] = source_label

        palette = tuple(str(color) for color in timeline.metadata.get("show_palette", ()))
        if not palette:
            palette = next(
                (tuple(cue.color_palette) for cue in timeline.cues if cue.color_palette),
                (),
            )
        if palette:
            nonlocal assignments
            assignments = song_palette_store.upsert(
                SongPaletteAssignment(
                    track_key=track_key_for_path(track_path),
                    palette_name=show.name or "Precompiled Show",
                    colors=palette,
                    source_label="compiled track",
                    source_profile_path=str(show_path),
                )
            )

        session = _active_local_session()
        playlist = playlist_state["playlist"]
        try:
            if session is not None and hasattr(session, "queue_snapshot"):
                set_timeline = getattr(session, "set_precompiled_timeline", None)
                if not callable(set_timeline):
                    raise RuntimeError("The active queue cannot accept compiled tracks yet.")
                set_timeline(track_path, timeline, source=source_label)
                snapshot = session.queue_snapshot()
                insert_at = min(
                    len(snapshot.get("tracks", ())),
                    max(0, int(snapshot.get("current_index", -1)) + 1),
                )
                queue_controller.insert_local(session, insert_at, track_path)
                status = f"Cued compiled track {track_path.name} after the current track."
            elif session is not None:
                raise RuntimeError("Stop the active saved Show before cueing a compiled track into Live.")
            elif playlist is not None:
                insert_at = min(len(playlist), max(0, playlist.current_index + 1))
                queue_controller.insert_playlist(playlist, insert_at, track_path)
                status = f"Cued compiled track {track_path.name}; it is ready for Live playback."
            else:
                _load_playlist(track_path)
                status = f"Cued compiled track {track_path.name}; it is ready for Live playback."
        except Exception as exc:
            precompiled_queue_state["timelines"].pop(resolved_track_path, None)
            precompiled_queue_state["sources"].pop(resolved_track_path, None)
            queue_controller.set_status(f"Could not cue compiled track: {exc}")
            _render_queue_state(queue_controller.state)
            return
        queue_controller.select_local_track(track_key_for_path(track_path))
        _rebind_local_queue(status=status)

    def _clear_show_file() -> None:  # pragma: no cover - Qt only
        _load_show_into_editor(
            show_service.new_show(),
            show_path=None,
            status="New Show created. Add Track… to build its ordered playlist.",
        )
        queue_controller.set_status("New Show created.")
        _render_queue_state(queue_controller.state)
        _render_runtime_state(runtime_supervisor.snapshot())

    def _save_current_show() -> None:  # pragma: no cover - Qt only
        if not _current_show_is_editable():
            return
        _commit_current_show_track_editor()
        show = _replace_show(name=_show_name_from_form())
        if not show.tracks:
            queue_controller.set_status("Add at least one Track before saving this Show.")
            _render_queue_state(queue_controller.state)
            return
        target = show_editor_state["path"]
        if target is None:
            show_directory = _show_save_directory()
            show_directory.mkdir(parents=True, exist_ok=True)
            if show.name == "Untitled Show":
                first_track_stem = Path(show.tracks[0].audio_path).stem
                automatic_name = (
                    f"{first_track_stem}_setlist" if len(show.tracks) > 1 else first_track_stem
                )
                show = _replace_show(name=automatic_name)
            safe_name = "".join(
                character if character.isalnum() or character in {"-", "_"} else "_"
                for character in show.name
            ).strip("_") or "untitled_show"
            target = show_directory / f"{safe_name}.show.json"
            suffix = 2
            while target.exists():
                target = show_directory / f"{safe_name}_{suffix}.show.json"
                suffix += 1
        try:
            saved_path = show_service.save_show(show, target)
        except Exception as exc:
            queue_controller.set_status(f"Could not save show: {exc}")
            _render_queue_state(queue_controller.state)
            return
        selected_index = show_editor_state.get("track_index")
        _load_show_into_editor(
            show,
            show_path=saved_path,
            status=f"Show saved: {saved_path.name}",
            index=int(selected_index) if isinstance(selected_index, int) else 0,
        )
        queue_controller.set_status(f"Show saved: {saved_path.name}")
        _render_queue_state(queue_controller.state)

    def _bake_current_show() -> None:  # pragma: no cover - Qt only
        queue_controller.set_status(
            "Frame baking is currently available for legacy single-Track files only."
        )
        _render_queue_state(queue_controller.state)

    def _save_baked_artifact_as() -> None:  # pragma: no cover - Qt only
        if not _validate_current_baked_artifact(manual=True):
            _render_queue_state(queue_controller.state)
            return
        source_path = show_editor_state.get("baked_artifact_path")
        if not isinstance(source_path, Path):
            return
        selected, _filter = QtWidgets.QFileDialog.getSaveFileName(
            window,
            "Save Baked Frames As",
            str(source_path),
            "Baked Frame JSON (*.frames.json *.json);;All Files (*.*)",
        )
        if not selected:
            return
        target_path = Path(selected)
        if target_path.exists():
            answer = QtWidgets.QMessageBox.question(
                window,
                "Replace Baked Artifact?",
                f"{target_path.name} already exists. Replace it?",
                QtWidgets.QMessageBox.StandardButton.Yes
                | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No,
            )
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                return
        try:
            exported = show_service.export_baked_artifact(source_path, target_path)
        except Exception as exc:
            queue_controller.set_status(f"Could not export baked frames: {exc}")
        else:
            queue_controller.set_status(f"Baked frames exported: {exported.name}.")
        _render_queue_state(queue_controller.state)

    def _add_show_tracks() -> None:  # pragma: no cover - Qt only
        selected_track = queue_controller.state.selected_track
        initial_directory = (
            str(Path(selected_track.path).parent)
            if selected_track is not None
            else str(_queue_load_directory())
        )
        selected_paths, _filter = QtWidgets.QFileDialog.getOpenFileNames(
            window,
            "Add Tracks to Show",
            initial_directory,
            "Audio Files (*.mp3 *.wav *.flac *.ogg *.aac);;All Files (*.*)",
        )
        if not selected_paths:
            return
        _commit_current_show_track_editor()
        show = _current_show()
        tracks = [*show.tracks, *(ShowTrack(audio_path=str(Path(path))) for path in selected_paths)]
        _replace_show(tracks=tuple(tracks))
        first_added = len(show.tracks)
        _activate_show_track(
            first_added,
            status=(
                f"Added {len(selected_paths)} Track(s). Select Compile Track, or Compile All Tracks."
            ),
        )
        queue_controller.set_status(f"Added {len(selected_paths)} Track(s) to {_current_show().name}.")
        _render_queue_state(queue_controller.state)

    def _remove_current_show_track() -> None:  # pragma: no cover - Qt only
        if not _current_show_is_editable():
            return
        track_index = show_editor_state.get("track_index")
        show = _current_show()
        if not isinstance(track_index, int) or not 0 <= track_index < len(show.tracks):
            queue_controller.set_status("Select a Track to remove.")
            _render_queue_state(queue_controller.state)
            return
        _commit_current_show_track_editor()
        show = _current_show()
        removed = show.tracks[track_index]
        tracks = tuple(track for index, track in enumerate(show.tracks) if index != track_index)
        _replace_show(tracks=tracks)
        next_index = min(track_index, len(tracks) - 1)
        _activate_show_track(next_index, status=f"Removed Track {removed.display_name}.")
        queue_controller.set_status(f"Removed Track {removed.display_name}.")
        _render_queue_state(queue_controller.state)

    def _move_current_show_track(direction: int) -> None:  # pragma: no cover - Qt only
        if not _current_show_is_editable():
            return
        track_index = show_editor_state.get("track_index")
        show = _current_show()
        if not isinstance(track_index, int):
            return
        target_index = track_index + direction
        if not 0 <= target_index < len(show.tracks):
            return
        _commit_current_show_track_editor()
        show = _current_show()
        tracks = list(show.tracks)
        tracks[track_index], tracks[target_index] = tracks[target_index], tracks[track_index]
        _replace_show(tracks=tuple(tracks))
        _activate_show_track(target_index, status="Track order updated.")
        queue_controller.set_status("Show Track order updated.")
        _render_queue_state(queue_controller.state)

    def _on_show_track_selected() -> None:  # pragma: no cover - Qt only
        item = queue_panel.show_tracks_list.currentItem()
        if item is None:
            return
        target_index = item.data(QtCore.Qt.ItemDataRole.UserRole)
        if not isinstance(target_index, int) or target_index == show_editor_state.get("track_index"):
            return
        _commit_current_show_track_editor()
        show = _current_show()
        if not 0 <= target_index < len(show.tracks):
            return
        track = show.tracks[target_index]
        status = (
            f"Editing compiled Track {track.display_name}."
            if track.is_compiled
            else _track_compile_notice(Path(track.audio_path))
        )
        _activate_show_track(target_index, status=status)

    def _add_show_cue(at_time: float | None = None) -> None:  # pragma: no cover - Qt only
        if not _current_show_is_editable():
            return
        timeline = _safe_editor_timeline()
        if timeline is None:
            queue_controller.set_status("Compile or load a saved show before adding cues.")
            _render_queue_state(queue_controller.state)
            return
        next_time = 0.0
        if at_time is not None:
            next_time = max(0.0, min(float(at_time), float(timeline.duration)))
        elif timeline.cues:
            next_time = min(float(timeline.duration), float(timeline.cues[-1].t) + 1.0)
        if any(abs(float(cue.t) - next_time) < SHOW_CUE_MIN_GAP_SECONDS for cue in timeline.cues):
            _set_show_editor_notice("A cue already exists too close to that point.")
            return
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
        row = next(
            (index for index, existing in enumerate(timeline.cues) if float(existing.t) > next_time),
            queue_panel.show_cues_table.rowCount(),
        )
        queue_panel.show_cues_table.insertRow(row)
        _populate_show_cue_row(row, cue, timeline.beat_times, show_palette=_current_show_palette_colors())
        queue_panel.show_cues_table.selectRow(row)
        _render_show_timeline_view(playhead_seconds=_current_show_timeline_position())

    def _duplicate_show_cue() -> None:  # pragma: no cover - Qt only
        if not _current_show_is_editable():
            return
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

    def _remove_show_cue(row: int | None = None) -> None:  # pragma: no cover - Qt only
        if not _current_show_is_editable():
            return
        if row is None:
            row = queue_panel.show_cues_table.currentRow()
        if row < 0:
            queue_controller.set_status("Select a cue row to remove.")
            _render_queue_state(queue_controller.state)
            return
        queue_panel.show_cues_table.removeRow(row)
        _render_show_timeline_view(playhead_seconds=_current_show_timeline_position())

    def _select_show_cue_row(index: int) -> None:  # pragma: no cover - Qt only
        if 0 <= index < queue_panel.show_cues_table.rowCount():
            table = queue_panel.show_cues_table
            table.setCurrentCell(index, 0)
            table.selectRow(index)
            item = table.item(index, 0)
            if item is not None:
                table.scrollToItem(
                    item,
                    QtWidgets.QAbstractItemView.ScrollHint.PositionAtCenter,
                )
            _render_show_timeline_view(playhead_seconds=_current_show_timeline_position())

    def _on_show_cue_table_selection_changed() -> None:  # pragma: no cover - Qt only
        # An explicit table selection switches the timeline back from an orange
        # beat marker to cue navigation. Routine playback refreshes do not.
        queue_panel.show_timeline_view.clear_marker_selection()
        _render_show_timeline_view(playhead_seconds=_current_show_timeline_position())

    def _selected_editor_cue_start() -> float:
        timeline = _safe_editor_timeline()
        row = queue_panel.show_cues_table.currentRow()
        if timeline is None or row < 0 or row >= len(timeline.cues):
            return 0.0
        return float(timeline.cues[row].t)

    def _start_saved_show(*, from_selected_cue: bool = False) -> None:  # pragma: no cover - Qt only
        _commit_current_show_track_editor()
        show = _current_show()
        track = _current_show_track()
        if from_selected_cue and queue_panel.show_cues_table.currentRow() < 0:
            queue_controller.set_status("Select a cue row before starting from a cue.")
            _render_queue_state(queue_controller.state)
            return
        start_seconds = _selected_editor_cue_start() if from_selected_cue else 0.0
        try:
            _sync_runtime_settings_from_form()
            if from_selected_cue:
                if track is None or track.timeline is None:
                    raise ValueError("Compile the selected Track before playing from one of its cues.")
                runtime_supervisor.start_timeline_show(
                    Path(track.audio_path),
                    track.timeline,
                    show_path=show_editor_state["path"],
                    config_path=config_path,
                    start_seconds=start_seconds,
                )
            else:
                runtime_supervisor.start_compiled_show(
                    show,
                    show_path=show_editor_state["path"],
                    config_path=config_path,
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
                f"Track started from {start_seconds:.2f}s for {Path(track.audio_path).name}."
            )
        else:
            queue_controller.set_status(f"Show started: {show.name} ({len(show.tracks)} Tracks).")
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
        warning = QtWidgets.QMessageBox(window)
        warning.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        warning.setWindowTitle("System Audio Loopback Required")
        warning.setText("Audio loopback must be enabled in system settings to capture.")
        warning.setInformativeText(
            "Confirm that your operating system exposes the selected loopback device "
            "(for example, Stereo Mix or VB-Audio CABLE Output) before continuing."
        )
        warning.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Ok)
        warning.setDefaultButton(QtWidgets.QMessageBox.StandardButton.Ok)
        warning.exec()
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
        queue_controller.set_status(f"Audio loopback capture running in {capture_dir}.")
        _render_queue_state(queue_controller.state)
        _render_runtime_state(runtime_supervisor.snapshot())
        queue_timer.start()

    def _stop_capture_pipeline() -> None:  # pragma: no cover - Qt only
        runtime_supervisor.stop_capture_pipeline()
        queue_controller.set_status("Audio loopback capture stopped.")
        _render_queue_state(queue_controller.state)
        _render_runtime_state(runtime_supervisor.snapshot())

    def _switch_to_pipeline_playback() -> None:  # pragma: no cover - Qt only
        started = runtime_supervisor.switch_to_pipeline_playback(config_path=config_path)
        if started:
            queue_controller.set_status("Captured-show playback started.")
        else:
            queue_controller.set_status("Auto-play captured shows armed; waiting for the next ready capture.")
        _render_queue_state(queue_controller.state)
        _render_runtime_state(runtime_supervisor.snapshot())
        queue_timer.start()

    def _start_reactive_output() -> None:  # pragma: no cover - Qt only
        # Selecting Reactive only cues its interface.  This handler is the
        # explicit point at which the microphone/input session is started.
        _set_live_mode("reactive", announce=False)
        _capture_settings, reactive_settings = _sync_runtime_settings_from_form()
        validation_errors = _validate_reactive_configuration(reactive_settings)
        if validation_errors:
            _update_reactive_configuration_warning()
            QtWidgets.QMessageBox.warning(
                window,
                "Invalid Reactive Configuration",
                "Reactive listening cannot start until these issues are fixed:\n\n"
                + "\n".join(f"• {message}" for message in validation_errors),
            )
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
        _apply_reactive_live_look()
        runtime_state = runtime_supervisor.snapshot()
        _render_session_status(_pending_preview_snapshot(runtime_state), running=True)
        queue_controller.set_status("Reactive output started with the refined live detector.")
        _render_queue_state(queue_controller.state)
        _render_runtime_state(runtime_state)
        _render_reactive_live_state(runtime_state)
        queue_timer.start()

    def _stop_output_runtime() -> None:  # pragma: no cover - Qt only
        runtime_supervisor.stop_output_only()
        queue_controller.set_status("Output stop requested.")
        _render_queue_state(queue_controller.state)
        runtime_state = runtime_supervisor.snapshot()
        _render_runtime_state(runtime_state)
        _render_reactive_live_state(runtime_state)

    def _cue_uncompiled_track() -> None:  # pragma: no cover - Qt only
        selected, _filter = QtWidgets.QFileDialog.getOpenFileName(
            window,
            "Cue Audio to Compile",
            str(_queue_load_directory()),
            "Audio Files (*.mp3 *.wav *.flac *.ogg *.aac);;All Files (*.*)",
        )
        if not selected:
            return
        track_path = Path(selected)
        session = _active_local_session()
        playlist = playlist_state["playlist"]
        try:
            if session is not None and hasattr(session, "queue_snapshot"):
                snapshot = session.queue_snapshot()
                insert_at = min(
                    len(snapshot.get("tracks", ())),
                    max(0, int(snapshot.get("current_index", -1)) + 1),
                )
                queue_controller.insert_local(session, insert_at, track_path)
                status = f"Cued {track_path.name} after the current track; it will hot-compile before playback."
            elif session is not None:
                queue_controller.set_status(
                    "Stop the active saved Show before cueing raw audio into the local compile queue."
                )
                _render_queue_state(queue_controller.state)
                return
            elif playlist is not None:
                insert_at = min(len(playlist), max(0, playlist.current_index + 1))
                queue_controller.insert_playlist(playlist, insert_at, track_path)
                status = f"Cued {track_path.name}; it will compile when local playback starts."
            else:
                _load_playlist(track_path)
                status = f"Cued {track_path.name}; it will compile when local playback starts."
        except Exception as exc:
            queue_controller.set_status(f"Could not cue audio: {exc}")
            _render_queue_state(queue_controller.state)
            return
        _rebind_local_queue(status=status)

    def _refresh_queue() -> None:  # pragma: no cover - Qt only
        refresh_button = queue_panel.refresh_button
        refresh_button.setText("Refreshing…")
        refresh_button.setEnabled(False)
        QtWidgets.QApplication.processEvents()
        try:
            _rebind_local_queue(status="Queue refreshed.")
        finally:
            def _finish_refresh() -> None:
                refresh_button.setText("Refresh")
                refresh_button.setEnabled(True)

            QtCore.QTimer.singleShot(200, _finish_refresh)

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
            show = show_service.load_show(show_path)
        except Exception as exc:
            queue_controller.set_status(f"Could not load saved show: {exc}")
            _render_queue_state(queue_controller.state)
            return
        _load_show_into_editor(show, show_path=show_path, status=f"Show selected: {show.name}")

    def _on_output_target_changed() -> None:  # pragma: no cover - Qt only
        runtime_supervisor.set_output_target_mode(str(queue_panel.output_target_combo.currentData() or "simulation"))
        _sync_device_health_monitoring()
        _render_runtime_state(runtime_supervisor.snapshot())

    def _device_health_config_path() -> Path | None:
        value = queue_panel.device_room_config_path_edit.text().strip()
        if not value:
            return None
        return Path(value).expanduser()

    def _render_device_health() -> None:  # pragma: no cover - Qt only
        hardware_enabled = str(queue_panel.output_target_combo.currentData() or "simulation") == "hardware"
        queue_panel.refresh_device_health_button.setEnabled(hardware_enabled)
        if not hardware_enabled:
            queue_panel.device_health_label.setText("Off in simulation mode.")
            return
        snapshot = device_health_service.snapshot()
        if snapshot.error:
            queue_panel.device_health_label.setText(f"Health unavailable: {snapshot.error}")
            return
        if not snapshot.entries:
            queue_panel.device_health_label.setText("Waiting for passive health check…")
            return
        counts = snapshot.counts()
        queue_panel.device_health_label.setText(
            f"{counts['online']} online · {counts['degraded']} degraded · "
            f"{counts['offline']} offline · {counts['unknown']} unknown"
        )
        detail = "\n".join(
            f"{entry.name} ({entry.device_type}): {entry.status}"
            + (f", {entry.latency_ms:.0f} ms" if entry.latency_ms is not None else "")
            + (f" — {entry.error}" if entry.error else "")
            for entry in snapshot.entries
        )
        queue_panel.device_health_label.setToolTip(detail)

    def _sync_device_health_monitoring(*, refresh: bool = False) -> None:  # pragma: no cover - Qt only
        hardware_enabled = str(queue_panel.output_target_combo.currentData() or "simulation") == "hardware"
        config_file = _device_health_config_path()
        if hardware_enabled and config_file is not None:
            device_health_service.start(config_file)
            if refresh:
                device_health_service.request_refresh()
        else:
            device_health_service.stop()
        _render_device_health()

    def _on_hardware_fallback_changed() -> None:  # pragma: no cover - Qt only
        runtime_supervisor.set_hardware_fallback_to_simulation(bool(queue_panel.hardware_fallback_check.isChecked()))
        _render_runtime_state(runtime_supervisor.snapshot())

    def _on_dark_mode_changed(enabled: bool) -> None:  # pragma: no cover - Qt only
        _apply_application_theme(bool(enabled))
        queue_panel.configuration_save_status_label.setText(
            "Application theme updated. Save Configuration to keep it."
        )
        queue_panel.configuration_save_status_label.setStyleSheet("color: #64748b;")

    def _on_baked_playback_mode_changed() -> None:  # pragma: no cover - Qt only
        mode = str(queue_panel.baked_playback_combo.currentData() or "auto")
        runtime_supervisor.set_baked_playback_mode(mode)
        queue_panel.baked_status_label.setText(f"Baked: {mode}")

    def _on_output_device_changed() -> None:  # pragma: no cover - Qt only
        runtime_supervisor.set_selected_output_audio_device(queue_panel.output_device_combo.currentData())
        _render_runtime_state(runtime_supervisor.snapshot())

    def _on_input_device_changed() -> None:  # pragma: no cover - Qt only
        runtime_supervisor.set_selected_live_input_device(queue_panel.input_device_combo.currentData())
        _render_runtime_state(runtime_supervisor.snapshot())

    def _on_runtime_settings_changed() -> None:  # pragma: no cover - Qt only
        _sync_runtime_settings_from_form()
        _update_reactive_configuration_warning()
        _render_runtime_state(runtime_supervisor.snapshot())

    def _cycle_live_palette() -> None:  # pragma: no cover - Qt only
        selected = queue_controller.state.selected_track
        if selected is None:
            queue_controller.set_status("Select an upcoming song first.")
        elif not selected.show_editable:
            queue_controller.set_status(f"{selected.edit_lock_reason}. Its queue position is still available.")
        else:
            if not palette_choices:
                current_profile_path = active_profile_ref["path"]
                if current_profile_path and current_profile_path.exists():
                    _apply_palette_choices(
                        profile_service.load_palette_choices(current_profile_path),
                        status="Loaded pre-generated palettes from the active profile.",
                    )
            if not palette_choices:
                queue_controller.set_status("Create or load palettes in the Palettes tab before cycling one here.")
            else:
                current = song_palette_store.assignment_for_path(selected.path)
                current_index = next(
                    (
                        index
                        for index, choice in enumerate(palette_choices)
                        if tuple(str(color) for color in choice["colors"])
                        == tuple(current.colors if current is not None else ())
                    ),
                    -1,
                )
                choice = palette_choices[(current_index + 1) % len(palette_choices)]
                song_palette_store.upsert(
                    SongPaletteAssignment(
                        track_key=selected.track_key,
                        palette_name=str(choice["palette_name"]),
                        colors=tuple(str(color) for color in choice["colors"]),
                        source_label=str(choice["profile_name"]),
                        source_profile_path=str(choice.get("profile_path", "")),
                        generated_seed=(
                            int(choice["generated_seed"])
                            if choice.get("generated_seed") is not None
                            else None
                        ),
                    )
                )
                _rebind_local_queue(
                    status=(
                        f"Palette set to {choice['palette_name']}. "
                        "Recompile Show applies it to the prepared cues without analysis."
                    )
                )
                return
        _render_queue_state(queue_controller.state)

    def _recompile_selected_live_show() -> None:  # pragma: no cover - Qt only
        selected = queue_controller.state.selected_track
        session = _active_local_session()
        if selected is None:
            queue_controller.set_status("Select an upcoming song first.")
        elif not selected.show_editable:
            queue_controller.set_status(f"{selected.edit_lock_reason}; its show cannot be changed now.")
        elif session is None or not hasattr(session, "prepared_timeline"):
            queue_controller.set_status("Start Live playback so the upcoming show can be prepared first.")
        else:
            assignment = song_palette_store.assignment_for_path(selected.path)
            timeline = session.prepared_timeline(selected.queue_index)
            if assignment is None:
                queue_controller.set_status("Cycle Palette first to choose a pre-generated palette.")
            elif timeline is None:
                queue_controller.set_status("That upcoming show is not prepared yet; try again in a moment.")
            else:
                try:
                    retinted = show_service.retint_timeline(timeline, assignment.colors)
                    session.replace_prepared_timeline(selected.queue_index, retinted)
                    show = _current_show()
                    replacement_tracks = tuple(
                        ShowTrack(
                            audio_path=track.audio_path,
                            timeline=(
                                show_service.retint_timeline(track.timeline, assignment.colors)
                                if track.timeline is not None
                                and Path(track.audio_path).resolve() == Path(selected.path).resolve()
                                else track.timeline
                            ),
                            metadata=dict(track.metadata or {}),
                        )
                        for track in show.tracks
                    )
                    if replacement_tracks != show.tracks:
                        _replace_show(tracks=replacement_tracks)
                        active_track = _current_show_track()
                        if active_track is not None and Path(active_track.audio_path).resolve() == Path(selected.path).resolve():
                            show_editor_state["timeline"] = active_track.timeline
                            _render_show_editor()
                    _rebind_local_queue(
                        status="Prepared show recolored. Cue timing and effects were preserved; no analysis was rerun."
                    )
                    return
                except Exception as exc:
                    queue_controller.set_status(str(exc))
        _render_queue_state(queue_controller.state)

    queue_timer = QtCore.QTimer(window)
    queue_timer.setInterval(250)
    preview_timer = QtCore.QTimer(window)
    preview_timer.setInterval(33)
    device_health_timer = QtCore.QTimer(window)
    device_health_timer.setInterval(1000)
    device_health_timer.timeout.connect(_render_device_health)
    device_health_timer.start()

    _initialize_show_columns_menu()
    _render_show_tracks()
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
        session_snapshot: dict[str, object] | None = None
        if runtime_state.error_message:
            _render_session_status(None, running=False, error=RuntimeError(runtime_state.error_message))
            queue_controller.set_status(runtime_state.error_message)
        elif session is not None and hasattr(session, "session_snapshot"):
            session_snapshot = session.session_snapshot()
            _render_session_status(session_snapshot, running=runtime_state.active_output_mode != "idle")
        elif _pending_preview_snapshot(runtime_state) is not None:
            _render_session_status(
                _pending_preview_snapshot(runtime_state),
                running=runtime_state.active_output_mode != "idle",
            )
        else:
            _render_session_status(None, running=False)
        _render_reactive_live_state(runtime_state, session_snapshot)
        preview_snapshot = runtime_supervisor.preview_frame_snapshot()
        if preview_snapshot is not None:
            _render_preview_simulation(preview_snapshot)
        watcher = spotify_runtime.get("watcher")
        if (
            queue_panel.live_loopback_check.isChecked()
            and live_mode_state["value"] == "queue"
            and watcher is not None
        ):
            queue_controller.refresh_spotify(watcher=watcher)
        if not queue_drag_state["active"]:
            _rebind_local_queue()
        _render_show_timeline_view(playhead_seconds=_current_show_timeline_position())
        if runtime_state.active_output_mode != "idle":
            if not preview_timer.isActive():
                preview_timer.start()
        else:
            preview_timer.stop()
        if runtime_state.active_output_mode == "idle" and runtime_state.capture_state != "running":
            queue_timer.stop()

    def _poll_preview_frame() -> None:  # pragma: no cover - Qt only
        preview_snapshot = runtime_supervisor.preview_frame_snapshot()
        if preview_snapshot is not None:
            _render_preview_simulation(preview_snapshot)
        runtime_state = runtime_supervisor.snapshot()
        _render_reactive_live_state(runtime_state)
        if preview_snapshot is None and runtime_state.active_output_mode == "idle":
            preview_timer.stop()

    queue_panel.start_preview_button.clicked.connect(_start_local_preview)
    queue_panel.pause_playback_button.clicked.connect(_toggle_live_playback)
    queue_panel.play_saved_show_button.clicked.connect(_start_saved_show)
    queue_panel.play_selected_cue_button.clicked.connect(lambda: _start_saved_show(from_selected_cue=True))
    queue_panel.show_pause_button.clicked.connect(_toggle_show_output_pause)
    queue_panel.show_stop_button.clicked.connect(_stop_output_runtime)
    queue_panel.browse_device_room_config_button.clicked.connect(_browse_device_room_config)
    queue_panel.browse_profile_directory_button.clicked.connect(
        lambda: _browse_directory(queue_panel.profile_directory_edit, "Select Profile Files Folder")
    )
    queue_panel.browse_reactive_profile_button.clicked.connect(
        _browse_reactive_profile_file
    )
    queue_panel.browse_show_directory_button.clicked.connect(
        lambda: _browse_directory(queue_panel.show_directory_edit, "Select Show / Quickshow Folder")
    )
    queue_panel.browse_queue_directory_button.clicked.connect(
        lambda: _browse_directory(
            queue_panel.queue_directory_edit,
            "Select Local Audio / Playlist Source Folder",
        )
    )
    queue_panel.apply_file_locations_button.clicked.connect(_apply_file_locations)
    queue_panel.refresh_storage_button.clicked.connect(_refresh_storage)
    queue_panel.clear_all_cache_button.clicked.connect(_clear_all_cache)
    queue_panel.clear_selected_cache_button.clicked.connect(_clear_selected_track_cache)
    queue_panel.archive_captures_button.clicked.connect(_archive_captures)
    queue_panel.compile_show_button.clicked.connect(_compile_show_for_editor)
    queue_panel.compile_all_tracks_button.clicked.connect(_compile_all_show_tracks)
    queue_panel.save_show_button.clicked.connect(_save_current_show)
    queue_panel.bake_show_button.clicked.connect(_bake_current_show)
    queue_panel.validate_baked_button.clicked.connect(
        lambda: _validate_current_baked_artifact(manual=True)
    )
    queue_panel.save_baked_as_button.clicked.connect(_save_baked_artifact_as)
    queue_panel.start_capture_button.clicked.connect(_start_capture_pipeline)
    queue_panel.stop_capture_button.clicked.connect(_stop_capture_pipeline)
    queue_panel.switch_pipeline_button.clicked.connect(_switch_to_pipeline_playback)
    queue_panel.start_reactive_button.clicked.connect(_start_reactive_output)
    queue_panel.stop_output_button.clicked.connect(_stop_output_runtime)
    queue_panel.stop_preview_button.clicked.connect(_stop_local_preview)
    queue_panel.load_saved_show_button.clicked.connect(_load_saved_show_file)
    queue_panel.load_saved_track_button.clicked.connect(_load_saved_track_show_file)
    queue_panel.cue_track_button.clicked.connect(_cue_uncompiled_track)
    queue_panel.load_show_button.clicked.connect(_load_saved_show_file)
    queue_panel.clear_show_button.clicked.connect(_clear_show_file)
    queue_panel.add_show_track_button.clicked.connect(_add_show_tracks)
    queue_panel.remove_show_track_button.clicked.connect(_remove_current_show_track)
    queue_panel.move_show_track_up_button.clicked.connect(lambda: _move_current_show_track(-1))
    queue_panel.move_show_track_down_button.clicked.connect(lambda: _move_current_show_track(1))
    queue_panel.show_tracks_list.itemSelectionChanged.connect(_on_show_track_selected)
    queue_panel.show_import_palette_button.clicked.connect(_import_profile_palette_into_show)
    queue_panel.add_show_cue_button.clicked.connect(_add_show_cue)
    queue_panel.duplicate_show_cue_button.clicked.connect(_duplicate_show_cue)
    queue_panel.remove_show_cue_button.clicked.connect(_remove_show_cue)
    queue_panel.show_cues_table.itemSelectionChanged.connect(
        _on_show_cue_table_selection_changed
    )
    queue_panel.show_timeline_view.cueSelected.connect(_select_show_cue_row)
    queue_panel.show_timeline_view.cueMoved.connect(_move_show_cue)
    queue_panel.show_timeline_view.beatMoved.connect(_move_beat_marker)
    queue_panel.show_timeline_view.downbeatMoved.connect(_move_downbeat_marker)
    queue_panel.show_timeline_view.cueDeleteRequested.connect(_remove_show_cue)
    queue_panel.show_timeline_view.beatDeleteRequested.connect(_delete_beat_marker)
    queue_panel.show_timeline_view.downbeatDeleteRequested.connect(_delete_downbeat_marker)
    queue_panel.show_timeline_view.addCueRequested.connect(_add_show_cue)
    queue_panel.show_timeline_view.addBeatRequested.connect(_add_beat_marker)
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
    queue_panel.show_meta_group.toggled.connect(lambda expanded: _toggle_show_meta_panel(not expanded))
    queue_panel.show_simulation_group.toggled.connect(queue_panel.show_simulation_content.setVisible)

    def _show_text_input_has_focus() -> bool:
        focused = QtWidgets.QApplication.focusWidget()
        return isinstance(
            focused,
            (
                QtWidgets.QLineEdit,
                QtWidgets.QTextEdit,
                QtWidgets.QPlainTextEdit,
                QtWidgets.QAbstractSpinBox,
            ),
        )

    def _focus_show_waveform() -> None:  # pragma: no cover - Qt only
        if not _show_text_input_has_focus():
            queue_panel.show_timeline_view.setFocus()

    def _add_show_tracks_shortcut() -> None:  # pragma: no cover - Qt only
        if not _show_text_input_has_focus():
            _add_show_tracks()

    def _compile_show_track_shortcut() -> None:  # pragma: no cover - Qt only
        if not _show_text_input_has_focus():
            _compile_show_for_editor()

    def _compile_all_show_tracks_shortcut() -> None:  # pragma: no cover - Qt only
        if not _show_text_input_has_focus():
            _compile_all_show_tracks()

    def _move_show_track_shortcut(direction: int) -> None:  # pragma: no cover - Qt only
        if not _show_text_input_has_focus():
            if queue_panel.show_timeline_view.hasFocus():
                queue_panel.show_timeline_view.switch_selection_kind()
                return
            _move_current_show_track(direction)

    def _remove_show_track_shortcut() -> None:  # pragma: no cover - Qt only
        if _show_text_input_has_focus():
            return
        if queue_panel.show_timeline_view.hasFocus() and queue_panel.show_timeline_view.delete_selected_item():
            return
        focused = QtWidgets.QApplication.focusWidget()
        table = queue_panel.show_cues_table
        if focused is table or (focused is not None and table.isAncestorOf(focused)):
            _remove_show_cue()
            return
        _remove_current_show_track()

    def _play_pause_show_shortcut() -> None:  # pragma: no cover - Qt only
        session = runtime_supervisor.active_session()
        if session is not None and bool(getattr(session, "running", False)):
            _toggle_show_output_pause()
        elif queue_panel.show_cues_table.currentRow() >= 0:
            _start_saved_show(from_selected_cue=True)
        else:
            _start_saved_show()

    queue_panel.show_timeline_view.playPauseRequested.connect(_play_pause_show_shortcut)

    def _stop_show_shortcut() -> None:  # pragma: no cover - Qt only
        runtime_state = runtime_supervisor.snapshot()
        if runtime_state.active_output_mode != "idle":
            _stop_output_runtime()

    show_shortcuts = []
    for sequence, handler in (
        ("Ctrl+C", _compile_show_track_shortcut),
        ("Ctrl+Shift+C", _compile_all_show_tracks_shortcut),
        ("Ctrl+O", _load_saved_show_file),
        ("Ctrl+L", _load_profile_editor_file),
        ("Ctrl+S", _save_current_show),
        ("Ctrl+N", _clear_show_file),
        ("Ctrl+A", _add_show_tracks_shortcut),
        ("Shift+Up", lambda: _move_show_track_shortcut(-1)),
        ("Shift+Down", lambda: _move_show_track_shortcut(1)),
        ("Delete", _remove_show_track_shortcut),
        ("W", _focus_show_waveform),
        ("Space", lambda: None if _show_text_input_has_focus() else _play_pause_show_shortcut()),
        ("Escape", _stop_show_shortcut),
    ):
        shortcut = QtGui.QShortcut(QtGui.QKeySequence(sequence), queue_panel.shows_widget)
        shortcut.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(handler)
        show_shortcuts.append(shortcut)
    media_play_pause_shortcut = QtGui.QShortcut(
        QtGui.QKeySequence(
            QtCore.QKeyCombination(
                QtCore.Qt.KeyboardModifier.NoModifier,
                QtCore.Qt.Key.Key_MediaTogglePlayPause,
            )
        ),
        queue_panel.shows_widget,
    )
    media_play_pause_shortcut.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
    media_play_pause_shortcut.activated.connect(_play_pause_show_shortcut)
    show_shortcuts.append(media_play_pause_shortcut)
    window._dreamsync_show_shortcuts = show_shortcuts

    def _live_start_pause_shortcut() -> None:  # pragma: no cover - Qt only
        if not _show_text_input_has_focus():
            if live_mode_state["value"] == "reactive":
                active_mode = runtime_supervisor.snapshot().active_output_mode
                if active_mode in {"reactive", "reactive_live"}:
                    _stop_output_runtime()
                elif active_mode == "idle":
                    _start_reactive_output()
                else:
                    queue_controller.set_status(
                        "Stop the active output before starting Reactive listening."
                    )
                    _render_queue_state(queue_controller.state)
            else:
                _toggle_live_playback()

    def _toggle_live_mode_shortcut() -> None:  # pragma: no cover - Qt only
        if not _show_text_input_has_focus():
            _set_live_mode("reactive" if live_mode_state["value"] == "queue" else "queue")

    def _live_stop_shortcut() -> None:  # pragma: no cover - Qt only
        active_mode = runtime_supervisor.snapshot().active_output_mode
        if active_mode in {"reactive", "reactive_live"}:
            _stop_output_runtime()
        elif active_mode in {"local", "local_preview", "local_playlist"}:
            _stop_local_preview()

    def _remove_selected_live_queue_shortcut() -> None:  # pragma: no cover - Qt only
        if not _show_text_input_has_focus():
            _remove_selected_queue_item()

    def _move_selected_live_queue_shortcut(direction: int) -> None:  # pragma: no cover - Qt only
        if _show_text_input_has_focus():
            return
        selected_index = _selected_queue_index()
        if selected_index is None:
            queue_controller.set_status("Select a song in the local queue first.")
            _render_queue_state(queue_controller.state)
            return
        target_index = selected_index + direction
        if not 0 <= target_index < len(queue_controller.state.local_tracks):
            return
        _move_queue_item(selected_index, target_index)

    live_shortcut_actions = (
        ("L", lambda: None if _show_text_input_has_focus() else _load_saved_track_show_file()),
        ("Ctrl+L", lambda: None if _show_text_input_has_focus() else _load_saved_show_file()),
        ("C", lambda: None if _show_text_input_has_focus() else _compile_show_for_editor()),
        ("R", lambda: None if _show_text_input_has_focus() else _refresh_queue()),
        ("Ctrl+R", lambda: None if _show_text_input_has_focus() else _refresh_queue()),
        ("Ctrl+P", lambda: None if _show_text_input_has_focus() else _cycle_live_palette()),
        ("Ctrl+C", lambda: None if _show_text_input_has_focus() else _recompile_selected_live_show()),
        ("[", lambda: None if _show_text_input_has_focus() else _set_reactive_effect_tempo(0.5)),
        ("\\", lambda: None if _show_text_input_has_focus() else _set_reactive_effect_tempo(1.0)),
        ("]", lambda: None if _show_text_input_has_focus() else _set_reactive_effect_tempo(2.0)),
        ("Shift+[", lambda: _adjust_reactive_cycle_tempo(0.5)),
        ("Shift+]", lambda: _adjust_reactive_cycle_tempo(2.0)),
        ("D", _nudge_reactive_downbeat),
        ("S", _latch_reactive_secondary_beat),
        ("N", _reset_reactive_detection),
        ("M", _toggle_live_mode_shortcut),
        ("Delete", _remove_selected_live_queue_shortcut),
        ("Shift+Up", lambda: _move_selected_live_queue_shortcut(-1)),
        ("Shift+Down", lambda: _move_selected_live_queue_shortcut(1)),
        ("Space", _live_start_pause_shortcut),
        ("Escape", _live_stop_shortcut),
    )
    live_shortcuts = []
    for sequence, handler in live_shortcut_actions:
        shortcut = QtGui.QShortcut(QtGui.QKeySequence(sequence), queue_panel.widget)
        shortcut.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
        if sequence in {"D", "S", "N"}:
            shortcut.setAutoRepeat(False)
        shortcut.activated.connect(handler)
        live_shortcuts.append(shortcut)
    window._dreamsync_live_shortcuts = live_shortcuts

    queue_panel.refresh_button.clicked.connect(_refresh_queue)
    queue_panel.shuffle_button.clicked.connect(_shuffle_upcoming_queue)
    queue_panel.repeat_button.toggled.connect(_toggle_queue_repeat)
    queue_panel.cycle_palette_button.clicked.connect(_cycle_live_palette)
    queue_panel.recompile_palette_button.clicked.connect(_recompile_selected_live_show)
    queue_panel.live_loopback_check.toggled.connect(_set_live_loopback_enabled)
    queue_panel.spotify_refresh_button.clicked.connect(_refresh_spotify_queue)
    queue_panel.spotify_skip_button.clicked.connect(_spotify_skip)
    queue_panel.spotify_shuffle_button.clicked.connect(_spotify_toggle_shuffle)
    queue_panel.spotify_add_button.clicked.connect(_spotify_add_to_queue)
    queue_panel.ready_preview_button.clicked.connect(_preview_ready_item)
    queue_panel.ready_play_button.clicked.connect(_play_ready_item_now)
    queue_panel.ready_prioritize_button.clicked.connect(_prioritize_ready_item)
    queue_panel.ready_discard_button.clicked.connect(_discard_ready_item)
    queue_panel.recent_saved_list.itemSelectionChanged.connect(_on_recent_saved_selected)
    queue_panel.local_list.itemSelectionChanged.connect(_on_queue_selection_changed)
    queue_panel.local_list.dragStarted.connect(_on_live_queue_drag_started)
    queue_panel.local_list.dragFinished.connect(_on_live_queue_drag_finished)
    queue_panel.local_list.reordered.connect(_move_queue_item)
    queue_panel.queue_mode_button.clicked.connect(lambda: _set_live_mode("queue"))
    queue_panel.reactive_mode_button.clicked.connect(lambda: _set_live_mode("reactive"))
    queue_panel.reactive_effect_tempo_half_button.clicked.connect(
        lambda: _set_reactive_effect_tempo(0.5)
    )
    queue_panel.reactive_effect_tempo_normal_button.clicked.connect(
        lambda: _set_reactive_effect_tempo(1.0)
    )
    queue_panel.reactive_effect_tempo_double_button.clicked.connect(
        lambda: _set_reactive_effect_tempo(2.0)
    )
    queue_panel.reactive_cycle_tempo_half_button.clicked.connect(
        lambda: _set_reactive_cycle_tempo(0.5)
    )
    queue_panel.reactive_cycle_tempo_normal_button.clicked.connect(
        lambda: _set_reactive_cycle_tempo(1.0)
    )
    queue_panel.reactive_cycle_tempo_double_button.clicked.connect(
        lambda: _set_reactive_cycle_tempo(2.0)
    )
    queue_panel.reactive_downbeat_nudge_button.clicked.connect(
        _nudge_reactive_downbeat
    )
    queue_panel.stop_reactive_button.clicked.connect(_stop_output_runtime)
    queue_panel.output_target_combo.currentIndexChanged.connect(lambda _index: _on_output_target_changed())
    queue_panel.refresh_device_health_button.clicked.connect(
        lambda: _sync_device_health_monitoring(refresh=True)
    )
    queue_panel.hardware_fallback_check.toggled.connect(lambda _checked: _on_hardware_fallback_changed())
    queue_panel.dark_mode_check.toggled.connect(_on_dark_mode_changed)
    queue_panel.baked_playback_combo.currentIndexChanged.connect(
        lambda _index: _on_baked_playback_mode_changed()
    )
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
    queue_panel.reactive_mirror_combo.currentIndexChanged.connect(lambda _index: _on_runtime_settings_changed())
    queue_panel.reactive_master_brightness_spin.valueChanged.connect(
        lambda _value: _on_runtime_settings_changed()
    )
    queue_panel.reactive_auto_cycle_check.toggled.connect(lambda _checked: _on_runtime_settings_changed())
    queue_panel.reactive_cycle_interval_spin.valueChanged.connect(lambda _value: _on_runtime_settings_changed())
    queue_panel.reactive_debug_mood_check.toggled.connect(lambda _checked: _on_runtime_settings_changed())
    queue_panel.reactive_crossfade_check.toggled.connect(lambda _checked: _on_runtime_settings_changed())
    queue_panel.reactive_harmonic_structure_check.toggled.connect(
        lambda _checked: _on_runtime_settings_changed()
    )
    queue_panel.reactive_structure_sensitivity_spin.valueChanged.connect(
        lambda _value: _on_runtime_settings_changed()
    )
    queue_panel.reactive_debug_harmonics_check.toggled.connect(
        lambda _checked: _on_runtime_settings_changed()
    )
    queue_panel.reactive_predictive_analysis_check.toggled.connect(
        lambda _checked: _on_runtime_settings_changed()
    )
    queue_panel.reactive_predictive_diagnostics_check.toggled.connect(
        lambda _checked: _on_runtime_settings_changed()
    )
    queue_panel.reactive_predictive_shadow_check.toggled.connect(
        lambda _checked: _on_runtime_settings_changed()
    )
    queue_panel.reactive_predictive_cues_check.toggled.connect(
        lambda _checked: _on_runtime_settings_changed()
    )
    queue_panel.reactive_structure_phrase_actions_check.toggled.connect(
        lambda _checked: _on_runtime_settings_changed()
    )
    queue_panel.reactive_predictive_high_impact_check.toggled.connect(
        lambda _checked: _on_runtime_settings_changed()
    )
    queue_panel.reactive_telemetry_dir_edit.editingFinished.connect(_on_runtime_settings_changed)
    queue_panel.reactive_profile_strategy_combo.currentIndexChanged.connect(lambda _index: _on_runtime_settings_changed())
    queue_panel.reactive_rotation_profiles_edit.textChanged.connect(
        lambda _text: _update_reactive_configuration_warning()
    )
    queue_panel.reactive_rotation_profiles_edit.editingFinished.connect(_on_runtime_settings_changed)
    queue_panel.reactive_rotation_interval_spin.valueChanged.connect(lambda _value: _on_runtime_settings_changed())
    queue_panel.reactive_auto_palette_check.toggled.connect(lambda _checked: _on_runtime_settings_changed())
    queue_panel.reactive_smart_rotation_check.toggled.connect(lambda _checked: _on_runtime_settings_changed())
    queue_panel.reactive_chain_blend_spin.valueChanged.connect(lambda _value: _on_runtime_settings_changed())
    queue_panel.reactive_auto_palette_seed_check.toggled.connect(lambda _checked: _on_runtime_settings_changed())
    queue_panel.reactive_auto_palette_seed_spin.valueChanged.connect(lambda _value: _on_runtime_settings_changed())
    queue_panel.reactive_auto_palette_pool_size_spin.valueChanged.connect(lambda _value: _on_runtime_settings_changed())
    queue_panel.reactive_chain_dwell_range_check.toggled.connect(lambda _checked: _on_runtime_settings_changed())
    queue_panel.reactive_chain_min_dwell_spin.valueChanged.connect(lambda _value: _on_runtime_settings_changed())
    queue_panel.reactive_chain_max_dwell_spin.valueChanged.connect(lambda _value: _on_runtime_settings_changed())
    queue_panel.preview_profile_chain_button.clicked.connect(_preview_profile_crossfade)
    queue_panel.runtime_apply_button.clicked.connect(_apply_runtime_control)
    queue_panel.runtime_clear_button.clicked.connect(_clear_runtime_control)
    queue_panel.save_patch_button.clicked.connect(_save_show_patch)
    queue_panel.clear_patch_button.clicked.connect(_clear_show_patch)
    profile_panel.load_profile_button.clicked.connect(_load_profile_editor_file)
    profile_panel.refresh_library_button.clicked.connect(_refresh_profile_library)
    profile_panel.library_search_edit.textChanged.connect(lambda _text: _refresh_profile_library())
    profile_panel.library_tags_edit.editingFinished.connect(_refresh_profile_library)
    profile_panel.load_library_profile_button.clicked.connect(_load_selected_library_profile)
    profile_panel.profile_library_list.itemDoubleClicked.connect(
        lambda _item: _load_selected_library_profile()
    )
    profile_panel.preview_generated_profiles_button.clicked.connect(_preview_generated_profile_pool)
    profile_panel.export_generated_profile_button.clicked.connect(_export_generated_profile)
    profile_panel.preview_transition_button.clicked.connect(_preview_profile_crossfade)
    profile_panel.palette_combo.currentIndexChanged.connect(lambda _index: _on_profile_palette_selected())
    profile_panel.show_palette_set_combo.currentIndexChanged.connect(
        lambda _index: _on_show_palette_set_selected()
    )
    profile_panel.mood_combo.currentIndexChanged.connect(lambda _index: _on_profile_mood_selected())
    profile_panel.add_palette_button.clicked.connect(_add_profile_palette)
    profile_panel.add_show_palette_set_button.clicked.connect(_add_show_palette_set)
    profile_panel.add_color_button.clicked.connect(_add_profile_palette_color)
    profile_panel.remove_color_button.clicked.connect(_remove_profile_palette_color)
    profile_panel.generate_seed_palette_button.clicked.connect(_generate_seed_palette)
    profile_panel.generate_quickshow_button.clicked.connect(_generate_quickshow_profile)
    profile_panel.save_palette_button.clicked.connect(_save_profile_palette)
    profile_panel.save_show_palette_set_button.clicked.connect(_save_show_palette_set)
    profile_panel.save_all_button.clicked.connect(_save_profile_all)
    def _unfocus_palette_textbox() -> None:  # pragma: no cover - Qt only
        focused = QtWidgets.QApplication.focusWidget()
        if isinstance(
            focused,
            (
                QtWidgets.QLineEdit,
                QtWidgets.QTextEdit,
                QtWidgets.QPlainTextEdit,
                QtWidgets.QAbstractSpinBox,
            ),
        ):
            focused.clearFocus()

    palette_shortcuts = []
    for sequence, handler in (
        ("L", lambda: None if _show_text_input_has_focus() else _load_profile_editor_file()),
        ("N", lambda: None if _show_text_input_has_focus() else _add_profile_palette()),
        ("G", lambda: None if _show_text_input_has_focus() else _generate_seed_palette()),
        ("Q", lambda: None if _show_text_input_has_focus() else _generate_quickshow_profile()),
        (
            "R",
            lambda: None
            if _show_text_input_has_focus()
            else profile_panel.seed_randomness_check.toggle(),
        ),
        ("S", lambda: None if _show_text_input_has_focus() else _save_profile_palette()),
        ("Ctrl+S", _save_profile_all),
        ("Escape", _unfocus_palette_textbox),
    ):
        shortcut = QtGui.QShortcut(QtGui.QKeySequence(sequence), profile_panel.widget)
        shortcut.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(handler)
        palette_shortcuts.append(shortcut)
    window._dreamsync_palette_shortcuts = palette_shortcuts
    profile_panel.add_effect_button.clicked.connect(_add_effect_row)
    profile_panel.add_param_button.clicked.connect(_add_param_row)
    profile_panel.add_profile_eq_route_button.clicked.connect(
        lambda: _add_eq_route_row(profile_panel.profile_eq_routes_table)
    )
    profile_panel.add_mood_eq_route_button.clicked.connect(
        lambda: _add_eq_route_row(profile_panel.mood_eq_routes_table)
    )
    profile_panel.add_profile_instrument_route_button.clicked.connect(
        lambda: _add_instrument_route_row(profile_panel.profile_instrument_routes_table)
    )
    profile_panel.add_mood_instrument_route_button.clicked.connect(
        lambda: _add_instrument_route_row(profile_panel.mood_instrument_routes_table)
    )
    profile_panel.add_transition_button.clicked.connect(_add_transition_row)
    for index, button in enumerate(profile_panel.palette_color_buttons):
        button.clicked.connect(lambda _checked=False, color_index=index: _on_profile_palette_color_clicked(color_index))
    for index, button in enumerate(profile_panel.seed_color_buttons):
        button.clicked.connect(lambda _checked=False, seed_index=index: _on_seed_color_clicked(seed_index))
    queue_panel.simulation_view_combo.currentIndexChanged.connect(
        lambda _index: _sync_preview_combo(
            queue_panel.simulation_view_combo,
            queue_panel.show_simulation_view_combo,
        )
    )
    queue_panel.simulation_background_combo.currentIndexChanged.connect(
        lambda _index: _sync_preview_combo(
            queue_panel.simulation_background_combo,
            queue_panel.show_simulation_background_combo,
        )
    )
    queue_panel.show_simulation_view_combo.currentIndexChanged.connect(
        lambda _index: _sync_preview_combo(
            queue_panel.show_simulation_view_combo,
            queue_panel.simulation_view_combo,
        )
    )
    queue_panel.show_simulation_background_combo.currentIndexChanged.connect(
        lambda _index: _sync_preview_combo(
            queue_panel.show_simulation_background_combo,
            queue_panel.simulation_background_combo,
        )
    )
    queue_panel.simulation_popout_button.clicked.connect(lambda: _show_simulation_window(fullscreen=False))
    queue_panel.simulation_fullscreen_button.clicked.connect(lambda: _show_simulation_window(fullscreen=True))
    queue_panel.show_simulation_popout_button.clicked.connect(lambda: _show_simulation_window(fullscreen=False))
    queue_panel.show_simulation_fullscreen_button.clicked.connect(lambda: _show_simulation_window(fullscreen=True))
    queue_panel.reactive_chord_popout_button.clicked.connect(
        lambda: _show_reactive_diagnostic_window(
            "chord",
            fullscreen=False,
        )
    )
    queue_panel.reactive_chord_fullscreen_button.clicked.connect(
        lambda: _show_reactive_diagnostic_window(
            "chord",
            fullscreen=True,
        )
    )
    queue_panel.reactive_harmonic_popout_button.clicked.connect(
        lambda: _show_reactive_diagnostic_window(
            "harmonic",
            fullscreen=False,
        )
    )
    queue_panel.reactive_harmonic_fullscreen_button.clicked.connect(
        lambda: _show_reactive_diagnostic_window(
            "harmonic",
            fullscreen=True,
        )
    )
    queue_panel.reactive_live_color_profile_combo.currentIndexChanged.connect(
        _apply_reactive_live_look
    )
    queue_panel.reactive_live_active_effect_combo.currentIndexChanged.connect(
        _apply_reactive_live_look
    )
    for effect_button in queue_panel.reactive_live_effect_buttons:
        effect_button.toggled.connect(_apply_reactive_live_look)
    queue_panel.reactive_chord_panel_check.toggled.connect(
        _refresh_reactive_panel_visibility
    )
    queue_panel.reactive_waveform_panel_check.toggled.connect(
        _refresh_reactive_panel_visibility
    )
    queue_panel.reactive_harmonic_panel_check.toggled.connect(
        _refresh_reactive_panel_visibility
    )
    queue_timer.timeout.connect(_poll_session_queue)
    preview_timer.timeout.connect(_poll_preview_frame)
    if settings.live_loopback_enabled:
        _set_live_loopback_enabled(True)

    def _stop_spotify_loopback_on_close(*_args) -> None:  # pragma: no cover - Qt only
        watcher = spotify_runtime.get("watcher")
        client = spotify_runtime.get("client")
        runtime_supervisor.set_capture_timing_source(None)
        if watcher is not None:
            watcher.stop()
        if client is not None:
            client.close()

    def _stop_device_health_on_close(*_args) -> None:  # pragma: no cover - Qt only
        device_health_service.stop()

    window.destroyed.connect(_stop_spotify_loopback_on_close)
    window.destroyed.connect(_stop_device_health_on_close)
    _sync_device_health_monitoring()

    # Live is the launch workspace. The saved choice selects its Queue or
    # cued Reactive interface, but never starts Reactive listening by itself.
    _set_live_mode(live_mode_state["value"], announce=False)
    for index in range(tabs.count()):
        if tabs.tabText(index) == "Live":
            tabs.setCurrentIndex(index)
            break

    reload_spatial_button.clicked.connect(lambda: _reload_spatial_scene(status="Spatial config reloaded from disk."))
    save_spatial_button.clicked.connect(_save_spatial_scene)
    reload_spatial_button.setToolTip("Reload the room-layout config (Ctrl+R)")
    save_spatial_button.setToolTip("Save the room layout (Ctrl+S)")
    apply_spatial_line_button.setToolTip("Apply the selected layout line (L)")
    spatial_shortcuts = []
    for sequence, handler in (
        ("M", _toggle_spatial_object_mode),
        ("D", _cycle_spatial_direction),
        ("L", _apply_spatial_line_shortcut),
        ("Return", _toggle_spatial_fine_tune),
        ("Enter", _toggle_spatial_fine_tune),
        ("Ctrl+R", lambda: _reload_spatial_scene(status="Spatial config reloaded from disk.")),
        ("Ctrl+S", _save_spatial_scene),
    ):
        shortcut = QtGui.QShortcut(QtGui.QKeySequence(sequence), spatial_widget)
        shortcut.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(handler)
        spatial_shortcuts.append(shortcut)
    window._dreamsync_spatial_shortcuts = spatial_shortcuts

    tab_shortcut_hints = (
        (device_discovery_panel.widget, "Shortcuts: L scan LAN, B scan BLE, S scan all, I identify, A add/update, Ctrl+S save."),
        (spatial_widget, "Shortcuts: M toggle object mode, D cycle direction, L apply line, Enter fine tune, Ctrl+R reload, Ctrl+S save."),
        (profile_panel.widget, "Shortcuts: L load profile, N new palette, G generate from seeds, Q Quickshow, R randomness, S save palette, Ctrl+S save all, Esc unfocus text."),
        (queue_panel.shows_widget, "Shortcuts: Ctrl+O open saved Show, Ctrl+L load a Profile, Ctrl+C compile Track, Ctrl+S save Show, Esc stop playback."),
        (queue_panel.widget, "Shortcuts: M toggle Queue/Reactive interface, [ half effect BPM, \\ reset effect BPM, ] double effect BPM, L load a precompiled track, Ctrl+L load a saved Show, C compile the selected Show Track, R/Ctrl+R refresh, Ctrl+P cycle palette, Ctrl+C recolor prepared Show, Shift+Up/Down reorder selected row, Delete remove selected row, Space pause/resume or start/stop Reactive, Esc stop."),
        (queue_panel.config_widget, "Shortcuts: Ctrl+S validate and save configuration; Ctrl+Tab and Ctrl+Shift+Tab switch tabs."),
        (diagnostics_panel.widget, "Keyboard: Ctrl+Tab and Ctrl+Shift+Tab switch tabs."),
    )
    for tab_widget, hint in tab_shortcut_hints:
        tab_index = tabs.indexOf(tab_widget)
        if tab_index >= 0:
            tabs.setTabToolTip(tab_index, hint)
    main_tab_shortcuts = []
    for sequence, step in (("Ctrl+Tab", 1), ("Ctrl+Shift+Tab", -1)):
        shortcut = QtGui.QShortcut(QtGui.QKeySequence(sequence), window)
        shortcut.setContext(QtCore.Qt.ShortcutContext.WindowShortcut)
        shortcut.activated.connect(
            lambda selected_step=step: _cycle_main_tab(selected_step)
        )
        main_tab_shortcuts.append(shortcut)
    window._dreamsync_main_tab_shortcuts = main_tab_shortcuts
    device_discovery_panel.scan_lan_devices_button.clicked.connect(lambda: _scan_discovery_devices("lan"))
    device_discovery_panel.scan_ble_devices_button.clicked.connect(lambda: _scan_discovery_devices("ble"))
    device_discovery_panel.scan_all_devices_button.clicked.connect(lambda: _scan_discovery_devices("all"))
    device_discovery_panel.identify_device_button.clicked.connect(_identify_discovery_device)
    device_discovery_panel.advanced_test_device_button.clicked.connect(_show_advanced_device_test)
    device_discovery_panel.assign_discovered_device_button.clicked.connect(_save_discovery_assignment)
    device_discovery_panel.save_discovered_config_button.clicked.connect(_save_discovery_assignment)
    discovery_shortcuts = []
    for sequence, handler in (
        ("L", lambda: _scan_discovery_devices("lan")),
        ("B", lambda: _scan_discovery_devices("ble")),
        ("S", lambda: _scan_discovery_devices("all")),
        ("I", _identify_discovery_device),
        ("A", _save_discovery_assignment),
        ("Ctrl+S", _save_discovery_assignment),
        ("Ctrl+Shift+S", _save_all_discovery_assignments),
    ):
        shortcut = QtGui.QShortcut(QtGui.QKeySequence(sequence), device_discovery_panel.widget)
        shortcut.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(handler)
        discovery_shortcuts.append(shortcut)
    window._dreamsync_discovery_shortcuts = discovery_shortcuts
    device_discovery_panel.devices_table.itemSelectionChanged.connect(
        lambda: _populate_discovery_form(_current_discovery_entry())
    )
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
    spatial_edit_mode_combo.currentIndexChanged.connect(
        lambda _index: _on_spatial_edit_mode_changed()
    )
    apply_spatial_line_button.clicked.connect(_apply_spatial_line)
    reverse_spatial_strip_button.clicked.connect(_reverse_spatial_strip)
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
    _render_show_tracks()
    _render_show_editor()
    _render_runtime_state(runtime_supervisor.snapshot())
    _render_diagnostics()
    _refresh_storage()
    _refresh_profile_library()

    def _current_gui_settings() -> GuiSettings:
        capture_settings, reactive_settings = _sync_runtime_settings_from_form()
        window_geometry = bytes(
            window.saveGeometry().toBase64()
        ).decode("ascii")
        return GuiSettings(
            last_config_path=(
                str(queue_panel.device_room_config_path_edit.text()).strip()
                or str(config_path or settings.last_config_path)
            ),
            last_profile_path=str(active_profile_ref["path"] or settings.last_profile_path),
            last_tab=tabs.tabText(tabs.currentIndex()),
            window_geometry=window_geometry,
            splitter_sizes=settings.splitter_sizes,
            output_target_mode=str(queue_panel.output_target_combo.currentData() or "simulation"),
            dark_mode=bool(queue_panel.dark_mode_check.isChecked()),
            selected_output_audio_device_id=queue_panel.output_device_combo.currentData(),
            selected_live_input_device_id=queue_panel.input_device_combo.currentData(),
            hardware_fallback_to_simulation=bool(queue_panel.hardware_fallback_check.isChecked()),
            baked_playback_mode=str(queue_panel.baked_playback_combo.currentData() or "auto"),
            live_loopback_enabled=bool(queue_panel.live_loopback_check.isChecked()),
            live_start_mode=str(queue_panel.startup_live_mode_combo.currentData() or "queue"),
            profile_directory=str(queue_panel.profile_directory_edit.text()).strip(),
            show_directory=str(queue_panel.show_directory_edit.text()).strip(),
            queue_directory=str(queue_panel.queue_directory_edit.text()).strip(),
            recent_saved_show_paths=runtime_supervisor.recent_saved_shows(),
            show_editor_hidden_columns=tuple(
                index
                for index in range(queue_panel.show_cues_table.columnCount())
                if queue_panel.show_cues_table.isColumnHidden(index)
            ),
            show_editor_meta_hidden=bool(show_editor_state.get("meta_hidden")),
            show_editor_focus_mode=bool(show_editor_state.get("focus_enabled")),
            show_editor_splitter_sizes=tuple(int(value) for value in queue_panel.shows_splitter.sizes()),
            reactive_diagnostics_splitter_sizes=tuple(
                int(value)
                for value in queue_panel.reactive_diagnostics_splitter.sizes()
            ),
            reactive_chord_panel_visible=bool(
                queue_panel.reactive_chord_panel_check.isChecked()
            ),
            reactive_waveform_panel_visible=bool(
                queue_panel.reactive_waveform_panel_check.isChecked()
            ),
            reactive_harmonic_panel_visible=bool(
                queue_panel.reactive_harmonic_panel_check.isChecked()
            ),
            reactive_live_color_profile=str(
                queue_panel.reactive_live_color_profile_combo.currentData()
                or ""
            ),
            reactive_live_active_effect=str(
                queue_panel.reactive_live_active_effect_combo.currentData()
                or ""
            ),
            reactive_live_effect_bank=_current_reactive_effect_bank(),
            show_compile_seed=(
                int(queue_panel.compile_seed_spin.value())
                if queue_panel.compile_seed_check.isChecked()
                else None
            ),
            capture_settings=capture_settings,
            reactive_settings=reactive_settings,
        )

    def _save_configuration() -> bool:  # pragma: no cover - Qt only
        snapshot = _current_gui_settings()
        validation_errors = _validate_reactive_configuration(
            snapshot.reactive_settings
        )
        if validation_errors:
            message = "Configuration was not saved:\n\n" + "\n".join(
                f"• {error}" for error in validation_errors
            )
            queue_panel.configuration_save_status_label.setText(
                f"Not saved: {validation_errors[0]}"
            )
            queue_panel.configuration_save_status_label.setStyleSheet(
                "color: #dc2626; font-weight: 600;"
            )
            queue_panel.reactive_configuration_warning_label.setText(
                "Invalid Reactive configuration:\n"
                + "\n".join(f"• {error}" for error in validation_errors)
            )
            queue_panel.reactive_configuration_warning_label.setVisible(True)
            QtWidgets.QMessageBox.warning(
                window,
                "Invalid Configuration",
                message,
            )
            return False

        try:
            if settings_store is not None:
                settings_store.save(snapshot)
                persisted_settings_state["value"] = snapshot
        except Exception as exc:
            queue_panel.configuration_save_status_label.setText(
                f"Configuration could not be saved: {exc}"
            )
            queue_panel.configuration_save_status_label.setStyleSheet(
                "color: #dc2626; font-weight: 600;"
            )
            QtWidgets.QMessageBox.critical(
                window,
                "Save Configuration Failed",
                str(exc),
            )
            return False

        queue_panel.configuration_save_status_label.setText(
            "Configuration saved."
            if settings_store is not None
            else "Configuration is valid; no settings store is attached to this window."
        )
        queue_panel.configuration_save_status_label.setStyleSheet(
            "color: #16a34a; font-weight: 600;"
        )
        queue_panel.reactive_configuration_warning_label.clear()
        queue_panel.reactive_configuration_warning_label.setVisible(False)
        status_bar.showMessage("Configuration saved.", 5000)
        if settings_store is not None:
            _show_success("Configuration saved.")
        return True

    queue_panel.save_configuration_button.clicked.connect(_save_configuration)
    window._dreamsync_save_configuration = _save_configuration

    class _ReliablePageShortcutFilter(QtCore.QObject):
        """Dispatch active-page shortcuts before focused buttons consume them."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self._pending_sequence = ""

        @staticmethod
        def _event_sequence(event) -> str:
            return QtGui.QKeySequence(event.keyCombination()).toString(
                QtGui.QKeySequence.SequenceFormat.PortableText
            )

        def _handler_for(self, sequence: str):
            if _show_text_input_has_focus():
                return None
            current_widget = tabs.currentWidget()
            if current_widget is queue_panel.config_widget and sequence == "Ctrl+S":
                return _save_configuration
            if current_widget is queue_panel.shows_widget and sequence == "Ctrl+O":
                return _load_saved_show_file
            if current_widget is queue_panel.widget:
                for configured_sequence, handler in live_shortcut_actions:
                    if configured_sequence == sequence:
                        return handler
            return None

        def eventFilter(self, watched, event):  # pragma: no cover - Qt only
            event_type = event.type()
            if event_type == QtCore.QEvent.Type.ShortcutOverride and _show_text_input_has_focus():
                event.accept()
                return True
            if (
                event_type == QtCore.QEvent.Type.KeyPress
                and event.key() == QtCore.Qt.Key.Key_Escape
                and _show_text_input_has_focus()
            ):
                focused = QtWidgets.QApplication.focusWidget()
                file_location_edits = {
                    queue_panel.device_room_config_path_edit,
                    queue_panel.profile_directory_edit,
                    queue_panel.show_directory_edit,
                    queue_panel.queue_directory_edit,
                }
                if focused in file_location_edits and _apply_file_locations():
                    _save_configuration()
                if focused is not None:
                    focused.clearFocus()
                window.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)
                event.accept()
                return True
            if event_type == QtCore.QEvent.Type.ShortcutOverride:
                sequence = self._event_sequence(event)
                if self._handler_for(sequence) is not None:
                    self._pending_sequence = sequence
                    event.accept()
                    return True
            elif event_type == QtCore.QEvent.Type.KeyPress:
                sequence = self._event_sequence(event)
                if sequence == self._pending_sequence:
                    self._pending_sequence = ""
                    handler = self._handler_for(sequence)
                    if handler is not None and not event.isAutoRepeat():
                        handler()
                    return True
            elif event_type == QtCore.QEvent.Type.KeyRelease:
                self._pending_sequence = ""
            return super().eventFilter(watched, event)

    reliable_page_shortcut_filter = _ReliablePageShortcutFilter(window)
    app_instance = QtWidgets.QApplication.instance()
    if app_instance is not None:
        app_instance.installEventFilter(reliable_page_shortcut_filter)
    window._dreamsync_reliable_page_shortcut_filter = reliable_page_shortcut_filter

    window._dreamsync_settings_snapshot = _current_gui_settings
    window._dreamsync_apply_reactive_live_look = _apply_reactive_live_look

    original_main_close_event = window.closeEvent

    def _persist_preferences_on_close(event) -> None:
        if settings_store is not None:
            base = persisted_settings_state["value"]
            preference_snapshot = replace(
                base,
                last_tab=tabs.tabText(tabs.currentIndex()),
                window_geometry=bytes(
                    window.saveGeometry().toBase64()
                ).decode("ascii"),
                dark_mode=bool(queue_panel.dark_mode_check.isChecked()),
                reactive_diagnostics_splitter_sizes=tuple(
                    int(value)
                    for value in queue_panel.reactive_diagnostics_splitter.sizes()
                ),
                reactive_chord_panel_visible=bool(
                    queue_panel.reactive_chord_panel_check.isChecked()
                ),
                reactive_waveform_panel_visible=bool(
                    queue_panel.reactive_waveform_panel_check.isChecked()
                ),
                reactive_harmonic_panel_visible=bool(
                    queue_panel.reactive_harmonic_panel_check.isChecked()
                ),
                reactive_live_color_profile=str(
                    queue_panel.reactive_live_color_profile_combo.currentData()
                    or ""
                ),
                reactive_live_active_effect=str(
                    queue_panel.reactive_live_active_effect_combo.currentData()
                    or ""
                ),
                reactive_live_effect_bank=_current_reactive_effect_bank(),
            )
            try:
                settings_store.save(preference_snapshot)
                persisted_settings_state["value"] = preference_snapshot
            except Exception:
                pass
        original_main_close_event(event)

    window.closeEvent = _persist_preferences_on_close

    return window
