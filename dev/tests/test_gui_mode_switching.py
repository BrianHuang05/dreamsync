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
    show_preview = window.findChild(QtWidgets.QGroupBox, "showSimulationGroup")
    show_preview_canvas = window.findChild(QtWidgets.QWidget, "showSimulationSpatialCanvas")
    show_preview_view = window.findChild(QtWidgets.QComboBox, "showSimulationViewCombo")
    show_preview_background = window.findChild(QtWidgets.QComboBox, "showSimulationBackgroundCombo")
    routing_status = window.findChild(QtWidgets.QLabel, "routingStatusLabel")
    ready_list = window.findChild(QtWidgets.QListWidget, "capturedReadyList")
    recent_saved = window.findChild(QtWidgets.QListWidget, "recentSavedShowsList")
    compile_show_button = window.findChild(QtWidgets.QPushButton, "compileShowButton")
    load_show_button = window.findChild(QtWidgets.QPushButton, "loadShowButton")
    load_saved_show_button = window.findChild(QtWidgets.QPushButton, "loadSavedShowButton")
    load_saved_track_button = window.findChild(QtWidgets.QPushButton, "loadSavedTrackButton")
    cue_track_button = window.findChild(QtWidgets.QPushButton, "cueUncompiledTrackButton")
    save_show_button = window.findChild(QtWidgets.QPushButton, "saveShowButton")
    bake_show_button = window.findChild(QtWidgets.QPushButton, "bakeShowButton")
    play_saved_show_button = window.findChild(QtWidgets.QPushButton, "playSavedShowButton")
    baked_playback_combo = window.findChild(QtWidgets.QComboBox, "bakedPlaybackModeCombo")
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
    reactive_live_settings_group = window.findChild(
        QtWidgets.QGroupBox,
        "reactiveLiveSettingsGroup",
    )
    reactive_render_mode = window.findChild(
        QtWidgets.QComboBox,
        "reactiveRenderModeCombo",
    )
    reactive_half_time = window.findChild(
        QtWidgets.QCheckBox,
        "reactiveHalfTimeCheck",
    )
    reactive_sample_rate = window.findChild(
        QtWidgets.QSpinBox,
        "reactiveSampleRateSpin",
    )
    reactive_auto_cycle = window.findChild(
        QtWidgets.QCheckBox,
        "reactiveAutoCycleCheck",
    )
    reactive_bar_actions = window.findChild(
        QtWidgets.QCheckBox,
        "reactivePredictiveCuesCheck",
    )
    reactive_phrase_actions = window.findChild(
        QtWidgets.QCheckBox,
        "reactiveStructurePhraseActionsCheck",
    )
    reactive_section_actions = window.findChild(
        QtWidgets.QCheckBox,
        "reactivePredictiveHighImpactCheck",
    )
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
    discovery_panel = window.findChild(QtWidgets.QWidget, "deviceDiscoveryPanel")
    discovery_status = window.findChild(QtWidgets.QLabel, "deviceDiscoveryStatusLabel")
    discovery_table = window.findChild(QtWidgets.QTableWidget, "discoveredDevicesTable")
    scan_lan_button = window.findChild(QtWidgets.QPushButton, "scanLanDevicesButton")
    scan_ble_button = window.findChild(QtWidgets.QPushButton, "scanBleDevicesButton")
    scan_all_button = window.findChild(QtWidgets.QPushButton, "scanAllDevicesButton")
    identify_device_button = window.findChild(QtWidgets.QPushButton, "identifyDeviceButton")
    assign_device_button = window.findChild(QtWidgets.QPushButton, "assignDiscoveredDeviceButton")
    save_discovery_button = window.findChild(QtWidgets.QPushButton, "saveDiscoveredConfigButton")
    discovered_name_edit = window.findChild(QtWidgets.QLineEdit, "discoveredDeviceNameEdit")
    discovered_type_combo = window.findChild(QtWidgets.QComboBox, "discoveredDeviceTypeCombo")
    discovered_segments_spin = window.findChild(QtWidgets.QSpinBox, "discoveredDeviceSegmentsSpin")
    discovered_transport_combo = window.findChild(QtWidgets.QComboBox, "discoveredDeviceTransportCombo")
    discovered_protocol_combo = window.findChild(QtWidgets.QComboBox, "discoveredDeviceProtocolCombo")
    discovered_role_combo = window.findChild(QtWidgets.QComboBox, "discoveredDeviceRoleCombo")
    discovered_brightness_spin = window.findChild(QtWidgets.QDoubleSpinBox, "discoveredDeviceBrightnessSpin")
    discovered_x_spin = window.findChild(QtWidgets.QDoubleSpinBox, "discoveredDeviceXSpin")
    discovered_y_spin = window.findChild(QtWidgets.QDoubleSpinBox, "discoveredDeviceYSpin")
    discovered_z_spin = window.findChild(QtWidgets.QDoubleSpinBox, "discoveredDeviceZSpin")

    assert isinstance(tabs, QtWidgets.QTabWidget)
    assert [tabs.tabText(index) for index in range(tabs.count())] == [
        "Device Discovery",
        "Room Layout",
        "Palettes",
        "Shows",
        "Live",
        "Config",
        "Diagnostics",
    ]
    assert output_mode is not None
    assert capture_status is not None
    assert pipeline_status is not None
    assert saved_show is not None
    assert show_preview is not None
    assert show_preview_canvas is not None
    assert show_preview_view is not None
    assert show_preview_background is not None
    assert routing_status is not None
    assert ready_list is not None
    assert recent_saved is not None
    assert compile_show_button is not None
    assert load_show_button is not None
    assert load_show_button.text() == "Open Saved Show"
    assert load_saved_show_button is not None
    assert load_saved_show_button.text() == "Load Saved Show"
    assert load_saved_track_button is not None
    assert load_saved_track_button.text() == "Cue Compiled Track"
    assert cue_track_button is not None
    assert cue_track_button.text() == "Cue Audio to Compile"
    assert save_show_button is not None
    assert bake_show_button is not None
    assert play_saved_show_button is not None
    assert baked_playback_combo is not None
    assert [
        baked_playback_combo.itemData(index)
        for index in range(baked_playback_combo.count())
    ] == ["auto", "off", "require"]
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
    assert show_cues_table.columnCount() == 27
    assert [
        show_cues_table.horizontalHeaderItem(index).text()
        for index in range(20, show_cues_table.columnCount())
    ] == [
        "Layer Category",
        "Layer Target",
        "Layer Trigger",
        "Layer Falloff",
        "Layer Thickness",
        "Layer Speed",
        "Layer Priority",
    ]
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
    assert reactive_group.title() == "Reactive Technical Settings"
    assert reactive_live_settings_group is not None
    assert reactive_render_mode is not None
    assert reactive_render_mode.isHidden()
    assert reactive_half_time is not None
    assert reactive_half_time.isHidden()
    assert reactive_sample_rate is not None
    assert reactive_auto_cycle is not None
    assert reactive_bar_actions is not None
    assert reactive_phrase_actions is not None
    assert reactive_section_actions is not None

    def is_descendant(widget, ancestor):
        parent = widget.parentWidget()
        while parent is not None:
            if parent is ancestor:
                return True
            parent = parent.parentWidget()
        return False

    assert is_descendant(reactive_sample_rate, reactive_group)
    assert reactive_auto_cycle.isHidden()
    assert not is_descendant(reactive_auto_cycle, reactive_live_settings_group)
    for live_control in (
        reactive_bar_actions,
        reactive_phrase_actions,
        reactive_section_actions,
    ):
        assert is_descendant(live_control, reactive_live_settings_group)
    reactive_render_mode.setCurrentIndex(
        reactive_render_mode.findData("breathe")
    )
    reactive_half_time.setChecked(True)
    reactive_snapshot = (
        window._dreamsync_settings_snapshot().reactive_settings
    )
    assert reactive_snapshot.render_mode == "scroll"
    assert reactive_snapshot.half_time is False
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
    assert discovery_panel is not None
    assert discovery_status is not None
    assert discovery_table is not None
    assert [
        discovery_table.horizontalHeaderItem(index).text()
        for index in range(discovery_table.columnCount())
    ] == ["Status", "Source", "Name", "Address", "Latency", "RSSI"]
    assert scan_lan_button is not None
    assert scan_ble_button is not None
    assert scan_all_button is not None
    assert identify_device_button is not None
    assert assign_device_button is not None
    assert save_discovery_button is not None
    assert discovered_name_edit is not None
    assert discovered_type_combo is not None
    assert discovered_segments_spin is not None
    assert discovered_transport_combo is not None
    assert discovered_protocol_combo is not None
    assert discovered_role_combo is not None
    assert discovered_brightness_spin is not None
    assert discovered_x_spin is not None
    assert discovered_y_spin is not None
    assert discovered_z_spin is not None
    assert [shortcut.key().toString() for shortcut in window._dreamsync_discovery_shortcuts] == [
        "L",
        "B",
        "S",
        "I",
        "A",
        "Ctrl+S",
        "Ctrl+Shift+S",
    ]
    assert profile_palette_combo.count() > 0
    assert profile_mood_combo.count() > 0
    assert seed_scheme_combo.count() >= 4
    assert output_mode.text() == "Output Mode: idle"
    assert capture_status.text() == "Capture: Off"
    assert saved_show.text() == "Saved show: none"
    assert routing_status.text() == "Routing: Simulation only."

    if app is not None and QtWidgets.QApplication.instance() is app:
        window.close()
