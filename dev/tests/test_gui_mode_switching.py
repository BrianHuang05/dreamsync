from __future__ import annotations

from pathlib import Path

import pytest

from dreamsync.gui.main_window import create_main_window
from dreamsync.gui.qt import require_qt
from dreamsync.gui.settings import GuiSettings


def test_queue_shows_and_config_tabs_expose_runtime_control_room_widgets():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    window = create_main_window(
        require_qt(),
        GuiSettings(),
        config_path=Path("dev/devices-dummy.yaml"),
    )

    tabs = window.centralWidget()
    output_mode = window.findChild(QtWidgets.QLabel, "outputModeLabel")
    capture_status = window.findChild(QtWidgets.QLabel, "captureStatusLabel")
    pipeline_status = window.findChild(QtWidgets.QLabel, "pipelineStatusLabel")
    saved_show = window.findChild(QtWidgets.QLabel, "savedShowLabel")
    routing_status = window.findChild(QtWidgets.QLabel, "routingStatusLabel")
    ready_list = window.findChild(QtWidgets.QListWidget, "capturedReadyList")
    recent_saved = window.findChild(QtWidgets.QListWidget, "recentSavedShowsList")
    compile_show_button = window.findChild(QtWidgets.QPushButton, "compileShowButton")
    load_show_button = window.findChild(QtWidgets.QPushButton, "loadShowButton")
    save_show_button = window.findChild(QtWidgets.QPushButton, "saveShowButton")
    play_saved_show_button = window.findChild(QtWidgets.QPushButton, "playSavedShowButton")
    play_selected_cue_button = window.findChild(QtWidgets.QPushButton, "playSelectedCueButton")
    pause_show_button = window.findChild(QtWidgets.QPushButton, "pauseShowButton")
    stop_show_button = window.findChild(QtWidgets.QPushButton, "stopShowButton")
    show_editor_group = window.findChild(QtWidgets.QGroupBox, "savedShowEditorGroup")
    show_audio_label = window.findChild(QtWidgets.QLabel, "showEditorAudioLabel")
    show_path_label = window.findChild(QtWidgets.QLabel, "showEditorPathLabel")
    show_status_label = window.findChild(QtWidgets.QLabel, "showEditorStatusLabel")
    show_focus_button = window.findChild(QtWidgets.QPushButton, "showEditorFocusButton")
    show_meta_panel = window.findChild(QtWidgets.QWidget, "showMetaPanel")
    show_meta_toggle_button = window.findChild(QtWidgets.QPushButton, "showMetaToggleButton")
    show_columns_button = window.findChild(QtWidgets.QToolButton, "showColumnsButton")
    show_title_edit = window.findChild(QtWidgets.QLineEdit, "showTitleEdit")
    show_artist_edit = window.findChild(QtWidgets.QLineEdit, "showArtistEdit")
    show_palette_widget = window.findChild(QtWidgets.QWidget, "showPaletteWidget")
    show_import_palette_button = window.findChild(QtWidgets.QPushButton, "showImportPaletteButton")
    show_palette_import_label = window.findChild(QtWidgets.QLabel, "showPaletteImportLabel")
    show_bpm_spin = window.findChild(QtWidgets.QDoubleSpinBox, "showBpmSpin")
    show_time_signature_combo = window.findChild(QtWidgets.QComboBox, "showTimeSignatureCombo")
    show_duration_spin = window.findChild(QtWidgets.QDoubleSpinBox, "showDurationSpin")
    show_timeline_view = window.findChild(QtWidgets.QWidget, "showTimelineView")
    show_timeline_zoom_slider = window.findChild(QtWidgets.QSlider, "showTimelineZoomSlider")
    show_timeline_fit_button = window.findChild(QtWidgets.QPushButton, "showTimelineFitButton")
    show_timeline_scroll_bar = window.findChild(QtWidgets.QScrollBar, "showTimelineScrollBar")
    show_cues_table = window.findChild(QtWidgets.QTableWidget, "showCuesTable")
    add_show_cue_button = window.findChild(QtWidgets.QPushButton, "addShowCueButton")
    duplicate_show_cue_button = window.findChild(QtWidgets.QPushButton, "duplicateShowCueButton")
    remove_show_cue_button = window.findChild(QtWidgets.QPushButton, "removeShowCueButton")
    start_capture_button = window.findChild(QtWidgets.QPushButton, "startCaptureButton")
    switch_pipeline_button = window.findChild(QtWidgets.QPushButton, "switchPipelineButton")
    start_reactive_button = window.findChild(QtWidgets.QPushButton, "startReactiveButton")
    output_target_combo = window.findChild(QtWidgets.QComboBox, "outputTargetCombo")
    output_device_combo = window.findChild(QtWidgets.QComboBox, "outputAudioDeviceCombo")
    input_device_combo = window.findChild(QtWidgets.QComboBox, "liveInputDeviceCombo")
    runtime_group = window.findChild(QtWidgets.QGroupBox, "runtimeRoutingGroup")
    runtime_override_group = window.findChild(QtWidgets.QGroupBox, "runtimeOverrideGroup")
    capture_group = window.findChild(QtWidgets.QGroupBox, "captureSettingsGroup")
    reactive_group = window.findChild(QtWidgets.QGroupBox, "reactiveSettingsGroup")
    show_patch_group = window.findChild(QtWidgets.QGroupBox, "showPatchGroup")
    profile_name = window.findChild(QtWidgets.QLabel, "profileNameLabel")
    profile_palette_combo = window.findChild(QtWidgets.QComboBox, "profilePaletteCombo")
    profile_mood_combo = window.findChild(QtWidgets.QComboBox, "profileMoodCombo")
    show_patch_name = window.findChild(QtWidgets.QLineEdit, "showPatchNameEdit")
    show_patch_rules = window.findChild(QtWidgets.QPlainTextEdit, "showPatchRulesEdit")
    runtime_palette_override = window.findChild(QtWidgets.QLineEdit, "runtimePaletteOverrideEdit")
    runtime_render_mode_override = window.findChild(QtWidgets.QComboBox, "runtimeRenderModeOverrideCombo")
    add_palette_button = window.findChild(QtWidgets.QPushButton, "addProfilePaletteButton")
    load_profile_editor_button = window.findChild(QtWidgets.QPushButton, "loadProfileEditorButton")
    seed_scheme_combo = window.findChild(QtWidgets.QComboBox, "profileSeedSchemeCombo")
    generate_seed_palette_button = window.findChild(QtWidgets.QPushButton, "generateSeedPaletteButton")
    seed_color_1 = window.findChild(QtWidgets.QPushButton, "profileSeedColorButton1")
    seed_color_2 = window.findChild(QtWidgets.QPushButton, "profileSeedColorButton2")
    quickshow_name_edit = window.findChild(QtWidgets.QLineEdit, "quickshowNameEdit")
    quickshow_energy_spin = window.findChild(QtWidgets.QDoubleSpinBox, "quickshowEnergySpin")
    generate_quickshow_button = window.findChild(QtWidgets.QPushButton, "generateQuickshowButton")
    effects_table = window.findChild(QtWidgets.QTableWidget, "profileEffectsTable")
    params_table = window.findChild(QtWidgets.QTableWidget, "profileParamsTable")
    profile_eq_table = window.findChild(QtWidgets.QTableWidget, "profileEqRoutesTable")
    profile_instrument_table = window.findChild(QtWidgets.QTableWidget, "profileInstrumentRoutesTable")
    transitions_table = window.findChild(QtWidgets.QTableWidget, "profileTransitionsTable")

    assert isinstance(tabs, QtWidgets.QTabWidget)
    assert [tabs.tabText(index) for index in range(tabs.count())] == [
        "Devices / Spatial",
        "Palettes",
        "Queue",
        "Shows",
        "Config",
        "Logs / Diagnostics",
    ]
    assert output_mode is not None
    assert capture_status is not None
    assert pipeline_status is not None
    assert saved_show is not None
    assert routing_status is not None
    assert ready_list is not None
    assert recent_saved is not None
    assert compile_show_button is not None
    assert load_show_button is not None
    assert save_show_button is not None
    assert play_saved_show_button is not None
    assert play_selected_cue_button is not None
    assert pause_show_button is not None
    assert stop_show_button is not None
    assert show_editor_group is not None
    assert show_audio_label is not None
    assert show_path_label is not None
    assert show_status_label is not None
    assert show_focus_button is not None
    assert show_meta_panel is not None
    assert show_meta_toggle_button is not None
    assert show_columns_button is not None
    assert show_title_edit is not None
    assert show_artist_edit is not None
    assert show_palette_widget is not None
    assert show_import_palette_button is not None
    assert show_palette_import_label is not None
    assert show_bpm_spin is not None
    assert show_time_signature_combo is not None
    assert show_duration_spin is not None
    assert show_timeline_view is not None
    assert show_timeline_zoom_slider is not None
    assert show_timeline_fit_button is not None
    assert show_timeline_scroll_bar is not None
    assert show_cues_table is not None
    assert show_cues_table.columnCount() == 20
    assert add_show_cue_button is not None
    assert duplicate_show_cue_button is not None
    assert remove_show_cue_button is not None
    assert start_capture_button is not None
    assert switch_pipeline_button is not None
    assert start_reactive_button is not None
    assert output_target_combo is not None
    assert output_device_combo is not None
    assert input_device_combo is not None
    assert runtime_group is not None
    assert runtime_override_group is not None
    assert capture_group is not None
    assert reactive_group is not None
    assert show_patch_group is not None
    assert profile_name is not None
    assert profile_palette_combo is not None
    assert profile_mood_combo is not None
    assert show_patch_name is not None
    assert show_patch_rules is not None
    assert runtime_palette_override is not None
    assert runtime_render_mode_override is not None
    assert add_palette_button is not None
    assert load_profile_editor_button is not None
    assert seed_scheme_combo is not None
    assert generate_seed_palette_button is not None
    assert seed_color_1 is not None
    assert seed_color_2 is not None
    assert quickshow_name_edit is not None
    assert quickshow_energy_spin is not None
    assert generate_quickshow_button is not None
    assert effects_table is not None
    assert params_table is not None
    assert profile_eq_table is not None
    assert profile_instrument_table is not None
    assert transitions_table is not None
    assert profile_palette_combo.count() > 0
    assert profile_mood_combo.count() > 0
    assert seed_scheme_combo.count() >= 4
    assert output_mode.text() == "Output Mode: idle"
    assert capture_status.text() == "Capture: Off"
    assert saved_show.text() == "Saved show: none"
    assert routing_status.text() == "Routing: Simulation only."

    if app is not None and QtWidgets.QApplication.instance() is app:
        window.close()
