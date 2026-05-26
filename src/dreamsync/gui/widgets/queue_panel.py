"""Queue and runtime dashboard widgets."""

from __future__ import annotations

from dataclasses import dataclass

from dreamsync.gui.widgets.show_timeline_view import build_show_timeline_view


@dataclass(frozen=True)
class QueuePanelWidgets:
    widget: object
    shows_widget: object
    config_widget: object
    load_file_button: object
    load_folder_button: object
    load_show_button: object
    compile_show_button: object
    clear_show_button: object
    save_show_button: object
    refresh_button: object
    start_preview_button: object
    play_saved_show_button: object
    play_selected_cue_button: object
    start_capture_button: object
    stop_capture_button: object
    switch_pipeline_button: object
    start_reactive_button: object
    stop_output_button: object
    stop_preview_button: object
    play_now_button: object
    remove_button: object
    shuffle_button: object
    playlist_label: object
    saved_show_label: object
    output_mode_label: object
    capture_status_label: object
    pipeline_status_label: object
    routing_status_label: object
    active_owner_label: object
    armed_owner_label: object
    output_target_combo: object
    hardware_fallback_check: object
    output_device_combo: object
    input_device_combo: object
    capture_dir_edit: object
    capture_naming_combo: object
    capture_buffer_spin: object
    capture_device_pattern_edit: object
    capture_sample_rate_spin: object
    capture_channels_spin: object
    capture_frame_size_spin: object
    capture_hop_size_spin: object
    capture_blocksize_spin: object
    pipeline_playback_device_combo: object
    purge_after_playback_check: object
    debug_pipeline_check: object
    reactive_render_mode_combo: object
    reactive_sample_rate_spin: object
    reactive_channels_spin: object
    reactive_frame_size_spin: object
    reactive_hop_size_spin: object
    reactive_blocksize_spin: object
    reactive_half_time_check: object
    reactive_max_brightness_check: object
    reactive_auto_cycle_check: object
    reactive_cycle_interval_spin: object
    reactive_debug_mood_check: object
    reactive_crossfade_check: object
    reactive_telemetry_dir_edit: object
    reactive_profile_strategy_combo: object
    reactive_profile_override_edit: object
    reactive_rotation_profiles_edit: object
    reactive_rotation_interval_spin: object
    reactive_auto_palette_check: object
    reactive_smart_rotation_check: object
    reactive_chain_blend_spin: object
    local_list: object
    recent_saved_list: object
    shows_splitter: object
    shows_left_panel: object
    show_audio_label: object
    show_editor_path_label: object
    show_editor_status_label: object
    show_pause_button: object
    show_stop_button: object
    show_editor_focus_button: object
    show_timeline_zoom_slider: object
    show_timeline_fit_button: object
    show_timeline_scroll_bar: object
    show_bpm_spin: object
    show_time_signature_combo: object
    show_duration_spin: object
    show_title_edit: object
    show_artist_edit: object
    show_palette_widget: object
    show_import_palette_button: object
    show_palette_import_label: object
    show_meta_panel: object
    show_meta_toggle_button: object
    show_columns_button: object
    show_cues_table: object
    show_timeline_view: object
    add_show_cue_button: object
    duplicate_show_cue_button: object
    remove_show_cue_button: object
    spotify_list: object
    ready_list: object
    ready_preview_button: object
    ready_play_button: object
    ready_prioritize_button: object
    ready_discard_button: object
    playback_status_label: object
    playback_track_label: object
    playback_time_label: object
    device_status_label: object
    audio_output_label: object
    input_device_status_label: object
    simulation_view_combo: object
    simulation_background_combo: object
    simulation_popout_button: object
    simulation_fullscreen_button: object
    simulation_host: object
    simulation_layout: object
    selected_song_label: object
    selected_assignment_label: object
    selected_path_label: object
    use_active_profile_button: object
    open_profile_button: object
    palette_combo: object
    generate_button: object
    seed_edit: object
    preview_label: object
    preview_host: object
    preview_layout: object
    assign_button: object
    clear_button: object
    patch_name_edit: object
    patch_rules_edit: object
    patch_summary_label: object
    save_patch_button: object
    clear_patch_button: object
    runtime_palette_override_edit: object
    runtime_color_bias_edit: object
    runtime_render_mode_combo: object
    runtime_intensity_multiplier_spin: object
    runtime_intensity_offset_spin: object
    runtime_speed_multiplier_spin: object
    runtime_speed_offset_spin: object
    runtime_muted_bands_edit: object
    runtime_muted_instruments_edit: object
    runtime_spatial_preset_edit: object
    runtime_spatial_width_spin: object
    runtime_apply_button: object
    runtime_clear_button: object
    runtime_control_status_label: object
    status_label: object


def build_queue_panel(qt_modules):
    QtWidgets = qt_modules.QtWidgets
    QtCore = qt_modules.QtCore

    class QueueListWidget(QtWidgets.QListWidget):
        reordered = QtCore.Signal(int, int)

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._drag_start_row = -1

        def startDrag(self, supportedActions):  # pragma: no cover - exercised through Qt runtime
            self._drag_start_row = self.currentRow()
            super().startDrag(supportedActions)

        def dropEvent(self, event):  # pragma: no cover - exercised through Qt runtime
            super().dropEvent(event)
            from_row = self._drag_start_row
            to_row = self.currentRow()
            if from_row >= 0 and to_row >= 0 and from_row != to_row:
                self.reordered.emit(from_row, to_row)
            self._drag_start_row = -1

    queue_widget = QtWidgets.QWidget()
    queue_widget.setObjectName("queueTabWidget")
    queue_layout = QtWidgets.QVBoxLayout(queue_widget)
    shows_widget = QtWidgets.QWidget()
    shows_widget.setObjectName("showsTabWidget")
    shows_layout = QtWidgets.QVBoxLayout(shows_widget)
    config_widget = QtWidgets.QWidget()
    config_widget.setObjectName("configTabWidget")
    config_layout = QtWidgets.QVBoxLayout(config_widget)

    load_file_button = QtWidgets.QPushButton("Load File")
    load_folder_button = QtWidgets.QPushButton("Load Folder")
    load_show_button = QtWidgets.QPushButton("Load Show")
    compile_show_button = QtWidgets.QPushButton("Compile Show")
    clear_show_button = QtWidgets.QPushButton("Clear Show")
    save_show_button = QtWidgets.QPushButton("Save Show")
    refresh_button = QtWidgets.QPushButton("Refresh")
    start_preview_button = QtWidgets.QPushButton("Start Preview")
    play_saved_show_button = QtWidgets.QPushButton("Play Saved Show")
    play_selected_cue_button = QtWidgets.QPushButton("Play From Cue")
    pause_show_button = QtWidgets.QPushButton("Pause")
    stop_show_button = QtWidgets.QPushButton("Stop")
    start_capture_button = QtWidgets.QPushButton("Start Capture")
    stop_capture_button = QtWidgets.QPushButton("Stop Capture")
    switch_pipeline_button = QtWidgets.QPushButton("Switch To Pipeline")
    start_reactive_button = QtWidgets.QPushButton("Start Reactive")
    stop_output_button = QtWidgets.QPushButton("Stop Output")
    stop_preview_button = QtWidgets.QPushButton("Stop")
    play_now_button = QtWidgets.QPushButton("Play Now")
    remove_button = QtWidgets.QPushButton("Remove")
    shuffle_button = QtWidgets.QPushButton("Shuffle Upcoming")
    playlist_label = QtWidgets.QLabel("No local queue loaded.")
    saved_show_label = QtWidgets.QLabel("Saved show: none")
    load_show_button.setObjectName("loadShowButton")
    compile_show_button.setObjectName("compileShowButton")
    clear_show_button.setObjectName("clearShowButton")
    save_show_button.setObjectName("saveShowButton")
    play_saved_show_button.setObjectName("playSavedShowButton")
    play_selected_cue_button.setObjectName("playSelectedCueButton")
    pause_show_button.setObjectName("pauseShowButton")
    stop_show_button.setObjectName("stopShowButton")
    start_capture_button.setObjectName("startCaptureButton")
    stop_capture_button.setObjectName("stopCaptureButton")
    switch_pipeline_button.setObjectName("switchPipelineButton")
    start_reactive_button.setObjectName("startReactiveButton")
    stop_output_button.setObjectName("stopOutputButton")
    saved_show_label.setObjectName("savedShowLabel")
    for control in (playlist_label, saved_show_label):
        control.setWordWrap(True)

    queue_toolbar = QtWidgets.QHBoxLayout()
    for button in (
        load_file_button,
        load_folder_button,
        refresh_button,
        start_preview_button,
        stop_preview_button,
        play_now_button,
        remove_button,
        shuffle_button,
    ):
        queue_toolbar.addWidget(button)
    queue_toolbar.addStretch(1)
    queue_layout.addLayout(queue_toolbar)
    queue_layout.addWidget(playlist_label)

    runtime_strip = QtWidgets.QGridLayout()
    output_mode_label = QtWidgets.QLabel("Output Mode: idle")
    capture_status_label = QtWidgets.QLabel("Capture: Off")
    pipeline_status_label = QtWidgets.QLabel("Pipeline: Idle")
    routing_status_label = QtWidgets.QLabel("Routing: Simulation only.")
    active_owner_label = QtWidgets.QLabel("Active owner: idle")
    armed_owner_label = QtWidgets.QLabel("Armed owner: none")
    output_mode_label.setObjectName("outputModeLabel")
    capture_status_label.setObjectName("captureStatusLabel")
    pipeline_status_label.setObjectName("pipelineStatusLabel")
    routing_status_label.setObjectName("routingStatusLabel")
    for control in (
        output_mode_label,
        capture_status_label,
        pipeline_status_label,
        routing_status_label,
        active_owner_label,
        armed_owner_label,
    ):
        control.setWordWrap(True)
    runtime_strip.addWidget(output_mode_label, 0, 0)
    runtime_strip.addWidget(capture_status_label, 0, 1)
    runtime_strip.addWidget(pipeline_status_label, 0, 2)
    runtime_strip.addWidget(routing_status_label, 1, 0, 1, 3)
    runtime_strip.addWidget(active_owner_label, 2, 0)
    runtime_strip.addWidget(armed_owner_label, 2, 1, 1, 2)
    config_layout.addLayout(runtime_strip)

    config_actions = QtWidgets.QHBoxLayout()
    for button in (
        start_capture_button,
        stop_capture_button,
        switch_pipeline_button,
        start_reactive_button,
        stop_output_button,
    ):
        config_actions.addWidget(button)
    config_actions.addStretch(1)
    config_layout.addLayout(config_actions)

    controls_scroll = QtWidgets.QScrollArea()
    controls_scroll.setWidgetResizable(True)
    config_layout.addWidget(controls_scroll, 1)
    controls_container = QtWidgets.QWidget()
    controls_column = QtWidgets.QVBoxLayout(controls_container)
    controls_column.setContentsMargins(0, 0, 0, 0)
    controls_column.setSpacing(12)
    controls_scroll.setWidget(controls_container)

    runtime_group = QtWidgets.QGroupBox("Runtime / Routing")
    runtime_group.setObjectName("runtimeRoutingGroup")
    runtime_layout = QtWidgets.QGridLayout(runtime_group)
    runtime_layout.addWidget(QtWidgets.QLabel("Output target"), 0, 0)
    output_target_combo = QtWidgets.QComboBox()
    output_target_combo.addItem("Simulation Only", "simulation")
    output_target_combo.addItem("Use Configured Hardware", "hardware")
    output_target_combo.setObjectName("outputTargetCombo")
    runtime_layout.addWidget(output_target_combo, 0, 1)
    hardware_fallback_check = QtWidgets.QCheckBox("Fallback to simulation if hardware is unavailable")
    hardware_fallback_check.setChecked(True)
    hardware_fallback_check.setObjectName("hardwareFallbackCheck")
    runtime_layout.addWidget(hardware_fallback_check, 1, 0, 1, 2)
    runtime_layout.addWidget(QtWidgets.QLabel("Output audio"), 2, 0)
    output_device_combo = QtWidgets.QComboBox()
    output_device_combo.setObjectName("outputAudioDeviceCombo")
    runtime_layout.addWidget(output_device_combo, 2, 1)
    runtime_layout.addWidget(QtWidgets.QLabel("Live input"), 3, 0)
    input_device_combo = QtWidgets.QComboBox()
    input_device_combo.setObjectName("liveInputDeviceCombo")
    runtime_layout.addWidget(input_device_combo, 3, 1)
    controls_column.addWidget(runtime_group)

    capture_group = QtWidgets.QGroupBox("Capture Settings")
    capture_group.setObjectName("captureSettingsGroup")
    capture_layout = QtWidgets.QGridLayout(capture_group)
    capture_layout.addWidget(QtWidgets.QLabel("Directory"), 0, 0)
    capture_dir_edit = QtWidgets.QLineEdit("captured_songs")
    capture_dir_edit.setObjectName("captureDirEdit")
    capture_layout.addWidget(capture_dir_edit, 0, 1)
    capture_layout.addWidget(QtWidgets.QLabel("Naming"), 1, 0)
    capture_naming_combo = QtWidgets.QComboBox()
    capture_naming_combo.addItem("Timestamp", "timestamp")
    capture_naming_combo.addItem("Metadata", "metadata")
    capture_naming_combo.setObjectName("captureNamingCombo")
    capture_layout.addWidget(capture_naming_combo, 1, 1)
    capture_layout.addWidget(QtWidgets.QLabel("Buffer"), 2, 0)
    capture_buffer_spin = QtWidgets.QSpinBox()
    capture_buffer_spin.setRange(0, 9999)
    capture_buffer_spin.setObjectName("captureBufferSpin")
    capture_layout.addWidget(capture_buffer_spin, 2, 1)
    capture_layout.addWidget(QtWidgets.QLabel("Device pattern"), 3, 0)
    capture_device_pattern_edit = QtWidgets.QLineEdit("CABLE Output")
    capture_device_pattern_edit.setObjectName("captureDevicePatternEdit")
    capture_layout.addWidget(capture_device_pattern_edit, 3, 1)
    capture_layout.addWidget(QtWidgets.QLabel("Sample rate"), 4, 0)
    capture_sample_rate_spin = QtWidgets.QSpinBox()
    capture_sample_rate_spin.setRange(8000, 192000)
    capture_sample_rate_spin.setValue(44100)
    capture_sample_rate_spin.setObjectName("captureSampleRateSpin")
    capture_layout.addWidget(capture_sample_rate_spin, 4, 1)
    capture_layout.addWidget(QtWidgets.QLabel("Channels"), 5, 0)
    capture_channels_spin = QtWidgets.QSpinBox()
    capture_channels_spin.setRange(1, 8)
    capture_channels_spin.setValue(2)
    capture_channels_spin.setObjectName("captureChannelsSpin")
    capture_layout.addWidget(capture_channels_spin, 5, 1)
    capture_layout.addWidget(QtWidgets.QLabel("Frame / hop / block"), 6, 0)
    capture_sizes_row = QtWidgets.QHBoxLayout()
    capture_frame_size_spin = QtWidgets.QSpinBox()
    capture_hop_size_spin = QtWidgets.QSpinBox()
    capture_blocksize_spin = QtWidgets.QSpinBox()
    for spin, value, name in (
        (capture_frame_size_spin, 2048, "captureFrameSizeSpin"),
        (capture_hop_size_spin, 512, "captureHopSizeSpin"),
        (capture_blocksize_spin, 1024, "captureBlocksizeSpin"),
    ):
        spin.setRange(32, 65536)
        spin.setSingleStep(32)
        spin.setValue(value)
        spin.setObjectName(name)
        capture_sizes_row.addWidget(spin)
    capture_layout.addLayout(capture_sizes_row, 6, 1)
    capture_layout.addWidget(QtWidgets.QLabel("Pipeline playback"), 7, 0)
    pipeline_playback_device_combo = QtWidgets.QComboBox()
    pipeline_playback_device_combo.setObjectName("pipelinePlaybackDeviceCombo")
    capture_layout.addWidget(pipeline_playback_device_combo, 7, 1)
    purge_after_playback_check = QtWidgets.QCheckBox("Purge items after pipeline playback")
    purge_after_playback_check.setObjectName("purgeAfterPlaybackCheck")
    capture_layout.addWidget(purge_after_playback_check, 8, 0, 1, 2)
    debug_pipeline_check = QtWidgets.QCheckBox("Verbose pipeline debug")
    debug_pipeline_check.setObjectName("debugPipelineCheck")
    capture_layout.addWidget(debug_pipeline_check, 9, 0, 1, 2)
    controls_column.addWidget(capture_group)

    reactive_group = QtWidgets.QGroupBox("Reactive Settings")
    reactive_group.setObjectName("reactiveSettingsGroup")
    reactive_layout = QtWidgets.QGridLayout(reactive_group)
    reactive_layout.addWidget(QtWidgets.QLabel("Render mode"), 0, 0)
    reactive_render_mode_combo = QtWidgets.QComboBox()
    for label, data in (("Scroll", "scroll"), ("Pulse", "pulse"), ("Solid", "solid"), ("Breathe", "breathe")):
        reactive_render_mode_combo.addItem(label, data)
    reactive_render_mode_combo.setObjectName("reactiveRenderModeCombo")
    reactive_layout.addWidget(reactive_render_mode_combo, 0, 1)
    reactive_layout.addWidget(QtWidgets.QLabel("Sample rate"), 1, 0)
    reactive_sample_rate_spin = QtWidgets.QSpinBox()
    reactive_sample_rate_spin.setRange(8000, 192000)
    reactive_sample_rate_spin.setValue(44100)
    reactive_sample_rate_spin.setObjectName("reactiveSampleRateSpin")
    reactive_layout.addWidget(reactive_sample_rate_spin, 1, 1)
    reactive_layout.addWidget(QtWidgets.QLabel("Channels"), 2, 0)
    reactive_channels_spin = QtWidgets.QSpinBox()
    reactive_channels_spin.setRange(1, 8)
    reactive_channels_spin.setValue(1)
    reactive_channels_spin.setObjectName("reactiveChannelsSpin")
    reactive_layout.addWidget(reactive_channels_spin, 2, 1)
    reactive_layout.addWidget(QtWidgets.QLabel("Frame / hop / block"), 3, 0)
    reactive_sizes_row = QtWidgets.QHBoxLayout()
    reactive_frame_size_spin = QtWidgets.QSpinBox()
    reactive_hop_size_spin = QtWidgets.QSpinBox()
    reactive_blocksize_spin = QtWidgets.QSpinBox()
    for spin, value, name in (
        (reactive_frame_size_spin, 2048, "reactiveFrameSizeSpin"),
        (reactive_hop_size_spin, 512, "reactiveHopSizeSpin"),
        (reactive_blocksize_spin, 1024, "reactiveBlocksizeSpin"),
    ):
        spin.setRange(32, 65536)
        spin.setSingleStep(32)
        spin.setValue(value)
        spin.setObjectName(name)
        reactive_sizes_row.addWidget(spin)
    reactive_layout.addLayout(reactive_sizes_row, 3, 1)
    reactive_half_time_check = QtWidgets.QCheckBox("Half-time BPM")
    reactive_half_time_check.setObjectName("reactiveHalfTimeCheck")
    reactive_layout.addWidget(reactive_half_time_check, 4, 0, 1, 2)
    reactive_max_brightness_check = QtWidgets.QCheckBox("Force max brightness")
    reactive_max_brightness_check.setObjectName("reactiveMaxBrightnessCheck")
    reactive_layout.addWidget(reactive_max_brightness_check, 5, 0, 1, 2)
    reactive_auto_cycle_check = QtWidgets.QCheckBox("Auto-cycle effects")
    reactive_auto_cycle_check.setChecked(True)
    reactive_auto_cycle_check.setObjectName("reactiveAutoCycleCheck")
    reactive_layout.addWidget(reactive_auto_cycle_check, 6, 0, 1, 2)
    reactive_layout.addWidget(QtWidgets.QLabel("Cycle interval"), 7, 0)
    reactive_cycle_interval_spin = QtWidgets.QDoubleSpinBox()
    reactive_cycle_interval_spin.setRange(1.0, 3600.0)
    reactive_cycle_interval_spin.setValue(16.0)
    reactive_cycle_interval_spin.setObjectName("reactiveCycleIntervalSpin")
    reactive_layout.addWidget(reactive_cycle_interval_spin, 7, 1)
    reactive_debug_mood_check = QtWidgets.QCheckBox("Debug mood/effect transitions")
    reactive_debug_mood_check.setObjectName("reactiveDebugMoodCheck")
    reactive_layout.addWidget(reactive_debug_mood_check, 8, 0, 1, 2)
    reactive_crossfade_check = QtWidgets.QCheckBox("Crossfade boundary detect")
    reactive_crossfade_check.setObjectName("reactiveCrossfadeCheck")
    reactive_layout.addWidget(reactive_crossfade_check, 9, 0, 1, 2)
    reactive_layout.addWidget(QtWidgets.QLabel("Telemetry dir"), 10, 0)
    reactive_telemetry_dir_edit = QtWidgets.QLineEdit()
    reactive_telemetry_dir_edit.setObjectName("reactiveTelemetryDirEdit")
    reactive_layout.addWidget(reactive_telemetry_dir_edit, 10, 1)
    reactive_layout.addWidget(QtWidgets.QLabel("Profile strategy"), 11, 0)
    reactive_profile_strategy_combo = QtWidgets.QComboBox()
    reactive_profile_strategy_combo.addItem("Active Profile", "active_profile")
    reactive_profile_strategy_combo.addItem("Override Profile", "override_profile")
    reactive_profile_strategy_combo.addItem("Auto Profile", "auto_profile")
    reactive_profile_strategy_combo.addItem("Profile Rotation", "profile_rotation")
    reactive_profile_strategy_combo.addItem("Smart Rotation", "smart_rotation")
    reactive_profile_strategy_combo.setObjectName("reactiveProfileStrategyCombo")
    reactive_layout.addWidget(reactive_profile_strategy_combo, 11, 1)
    reactive_layout.addWidget(QtWidgets.QLabel("Override profile"), 12, 0)
    reactive_profile_override_edit = QtWidgets.QLineEdit()
    reactive_profile_override_edit.setObjectName("reactiveProfileOverrideEdit")
    reactive_layout.addWidget(reactive_profile_override_edit, 12, 1)
    reactive_layout.addWidget(QtWidgets.QLabel("Rotation profiles"), 13, 0)
    reactive_rotation_profiles_edit = QtWidgets.QLineEdit()
    reactive_rotation_profiles_edit.setPlaceholderText("aurora, sunset, ./custom.yaml")
    reactive_rotation_profiles_edit.setObjectName("reactiveRotationProfilesEdit")
    reactive_layout.addWidget(reactive_rotation_profiles_edit, 13, 1)
    reactive_layout.addWidget(QtWidgets.QLabel("Rotation interval"), 14, 0)
    reactive_rotation_interval_spin = QtWidgets.QDoubleSpinBox()
    reactive_rotation_interval_spin.setRange(1.0, 86400.0)
    reactive_rotation_interval_spin.setValue(300.0)
    reactive_rotation_interval_spin.setObjectName("reactiveRotationIntervalSpin")
    reactive_layout.addWidget(reactive_rotation_interval_spin, 14, 1)
    reactive_auto_palette_check = QtWidgets.QCheckBox("Auto palette chaining")
    reactive_auto_palette_check.setObjectName("reactiveAutoPaletteCheck")
    reactive_layout.addWidget(reactive_auto_palette_check, 15, 0, 1, 2)
    reactive_smart_rotation_check = QtWidgets.QCheckBox("Smart rotation")
    reactive_smart_rotation_check.setObjectName("reactiveSmartRotationCheck")
    reactive_layout.addWidget(reactive_smart_rotation_check, 16, 0, 1, 2)
    reactive_layout.addWidget(QtWidgets.QLabel("Chain blend (s)"), 17, 0)
    reactive_chain_blend_spin = QtWidgets.QDoubleSpinBox()
    reactive_chain_blend_spin.setRange(0.5, 600.0)
    reactive_chain_blend_spin.setValue(8.0)
    reactive_chain_blend_spin.setObjectName("reactiveChainBlendSpin")
    reactive_layout.addWidget(reactive_chain_blend_spin, 17, 1)
    controls_column.addWidget(reactive_group)

    runtime_override_group = QtWidgets.QGroupBox("Live Override")
    runtime_override_group.setObjectName("runtimeOverrideGroup")
    runtime_override_layout = QtWidgets.QGridLayout(runtime_override_group)
    runtime_override_layout.addWidget(QtWidgets.QLabel("Palette override"), 0, 0)
    runtime_palette_override_edit = QtWidgets.QLineEdit()
    runtime_palette_override_edit.setPlaceholderText("#ff0000, #00ff00, #0000ff")
    runtime_palette_override_edit.setObjectName("runtimePaletteOverrideEdit")
    runtime_override_layout.addWidget(runtime_palette_override_edit, 0, 1)
    runtime_override_layout.addWidget(QtWidgets.QLabel("Color bias"), 1, 0)
    runtime_color_bias_edit = QtWidgets.QLineEdit()
    runtime_color_bias_edit.setPlaceholderText("#ffaa33")
    runtime_color_bias_edit.setObjectName("runtimeColorBiasEdit")
    runtime_override_layout.addWidget(runtime_color_bias_edit, 1, 1)
    runtime_override_layout.addWidget(QtWidgets.QLabel("Render mode"), 2, 0)
    runtime_render_mode_combo = QtWidgets.QComboBox()
    for label, data in (
        ("Auto", ""),
        ("Scroll", "scroll"),
        ("Pulse", "pulse"),
        ("Solid", "solid"),
        ("Breathe", "breathe"),
        ("Wave", "wave"),
        ("Gradient", "gradient"),
    ):
        runtime_render_mode_combo.addItem(label, data)
    runtime_render_mode_combo.setObjectName("runtimeRenderModeOverrideCombo")
    runtime_override_layout.addWidget(runtime_render_mode_combo, 2, 1)
    runtime_override_layout.addWidget(QtWidgets.QLabel("Intensity x / +"), 3, 0)
    runtime_dynamics_row = QtWidgets.QHBoxLayout()
    runtime_intensity_multiplier_spin = QtWidgets.QDoubleSpinBox()
    runtime_intensity_multiplier_spin.setRange(0.0, 4.0)
    runtime_intensity_multiplier_spin.setValue(1.0)
    runtime_intensity_multiplier_spin.setSingleStep(0.05)
    runtime_intensity_multiplier_spin.setObjectName("runtimeIntensityMultiplierSpin")
    runtime_intensity_offset_spin = QtWidgets.QDoubleSpinBox()
    runtime_intensity_offset_spin.setRange(-1.0, 1.0)
    runtime_intensity_offset_spin.setSingleStep(0.05)
    runtime_intensity_offset_spin.setObjectName("runtimeIntensityOffsetSpin")
    runtime_dynamics_row.addWidget(runtime_intensity_multiplier_spin)
    runtime_dynamics_row.addWidget(runtime_intensity_offset_spin)
    runtime_override_layout.addLayout(runtime_dynamics_row, 3, 1)
    runtime_override_layout.addWidget(QtWidgets.QLabel("Speed x / +"), 4, 0)
    runtime_speed_row = QtWidgets.QHBoxLayout()
    runtime_speed_multiplier_spin = QtWidgets.QDoubleSpinBox()
    runtime_speed_multiplier_spin.setRange(0.0, 4.0)
    runtime_speed_multiplier_spin.setValue(1.0)
    runtime_speed_multiplier_spin.setSingleStep(0.05)
    runtime_speed_multiplier_spin.setObjectName("runtimeSpeedMultiplierSpin")
    runtime_speed_offset_spin = QtWidgets.QDoubleSpinBox()
    runtime_speed_offset_spin.setRange(-4.0, 4.0)
    runtime_speed_offset_spin.setSingleStep(0.05)
    runtime_speed_offset_spin.setObjectName("runtimeSpeedOffsetSpin")
    runtime_speed_row.addWidget(runtime_speed_multiplier_spin)
    runtime_speed_row.addWidget(runtime_speed_offset_spin)
    runtime_override_layout.addLayout(runtime_speed_row, 4, 1)
    runtime_override_layout.addWidget(QtWidgets.QLabel("Muted EQ bands"), 5, 0)
    runtime_muted_bands_edit = QtWidgets.QLineEdit()
    runtime_muted_bands_edit.setPlaceholderText("bass, presence")
    runtime_muted_bands_edit.setObjectName("runtimeMutedBandsEdit")
    runtime_override_layout.addWidget(runtime_muted_bands_edit, 5, 1)
    runtime_override_layout.addWidget(QtWidgets.QLabel("Muted instruments"), 6, 0)
    runtime_muted_instruments_edit = QtWidgets.QLineEdit()
    runtime_muted_instruments_edit.setPlaceholderText("vocals, drums")
    runtime_muted_instruments_edit.setObjectName("runtimeMutedInstrumentsEdit")
    runtime_override_layout.addWidget(runtime_muted_instruments_edit, 6, 1)
    runtime_override_layout.addWidget(QtWidgets.QLabel("Spatial preset"), 7, 0)
    runtime_spatial_preset_edit = QtWidgets.QLineEdit()
    runtime_spatial_preset_edit.setPlaceholderText("blend_left_to_right")
    runtime_spatial_preset_edit.setObjectName("runtimeSpatialPresetEdit")
    runtime_override_layout.addWidget(runtime_spatial_preset_edit, 7, 1)
    runtime_override_layout.addWidget(QtWidgets.QLabel("Spatial width"), 8, 0)
    runtime_spatial_width_spin = QtWidgets.QDoubleSpinBox()
    runtime_spatial_width_spin.setRange(0.0, 4.0)
    runtime_spatial_width_spin.setSpecialValueText("Auto")
    runtime_spatial_width_spin.setSingleStep(0.05)
    runtime_spatial_width_spin.setObjectName("runtimeSpatialWidthSpin")
    runtime_override_layout.addWidget(runtime_spatial_width_spin, 8, 1)
    runtime_buttons = QtWidgets.QHBoxLayout()
    runtime_apply_button = QtWidgets.QPushButton("Apply Override")
    runtime_apply_button.setObjectName("applyRuntimeControlButton")
    runtime_clear_button = QtWidgets.QPushButton("Clear Override")
    runtime_clear_button.setObjectName("clearRuntimeControlButton")
    runtime_buttons.addWidget(runtime_apply_button)
    runtime_buttons.addWidget(runtime_clear_button)
    runtime_override_layout.addLayout(runtime_buttons, 9, 0, 1, 2)
    runtime_control_status_label = QtWidgets.QLabel("Live overrides are temporary and session-local.")
    runtime_control_status_label.setWordWrap(True)
    runtime_control_status_label.setObjectName("runtimeControlStatusLabel")
    runtime_override_layout.addWidget(runtime_control_status_label, 10, 0, 1, 2)
    controls_column.addWidget(runtime_override_group)
    controls_column.addStretch(1)

    queue_splitter = QtWidgets.QSplitter()
    queue_splitter.setOrientation(QtCore.Qt.Orientation.Horizontal)
    queue_layout.addWidget(queue_splitter, 1)

    queue_left_panel = QtWidgets.QWidget()
    queue_left_layout = QtWidgets.QVBoxLayout(queue_left_panel)
    local_group = QtWidgets.QGroupBox("Local Playlist")
    local_layout = QtWidgets.QVBoxLayout(local_group)
    local_list = QueueListWidget()
    local_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
    local_list.setDragEnabled(True)
    local_list.setAcceptDrops(True)
    local_list.setDropIndicatorShown(True)
    local_list.setDragDropMode(QtWidgets.QAbstractItemView.DragDropMode.InternalMove)
    local_list.setDefaultDropAction(QtCore.Qt.DropAction.MoveAction)
    local_layout.addWidget(local_list)
    queue_left_layout.addWidget(local_group, 3)

    spotify_group = QtWidgets.QGroupBox("Spotify Queue")
    spotify_layout = QtWidgets.QVBoxLayout(spotify_group)
    spotify_list = QtWidgets.QListWidget()
    spotify_layout.addWidget(spotify_list)
    queue_left_layout.addWidget(spotify_group, 2)
    queue_splitter.addWidget(queue_left_panel)

    queue_right_panel = QtWidgets.QWidget()
    queue_right_layout = QtWidgets.QVBoxLayout(queue_right_panel)
    playback_group = QtWidgets.QGroupBox("Playback / Preview")
    playback_layout = QtWidgets.QVBoxLayout(playback_group)
    playback_status_label = QtWidgets.QLabel("Playback: idle")
    playback_track_label = QtWidgets.QLabel("Current track: none")
    playback_time_label = QtWidgets.QLabel("Time: 00:00 / 00:00")
    device_status_label = QtWidgets.QLabel("Devices: preview mode (no connected devices)")
    audio_output_label = QtWidgets.QLabel("Audio output: system default")
    input_device_status_label = QtWidgets.QLabel("Input device: system default")
    for control in (
        playback_status_label,
        playback_track_label,
        playback_time_label,
        device_status_label,
        audio_output_label,
        input_device_status_label,
    ):
        control.setWordWrap(True)
        playback_layout.addWidget(control)
    simulation_controls = QtWidgets.QHBoxLayout()
    simulation_controls.addWidget(QtWidgets.QLabel("View"))
    simulation_view_combo = QtWidgets.QComboBox()
    for label, data in (("Room", "room"), ("XY", "xy"), ("XZ", "xz"), ("YZ", "yz")):
        simulation_view_combo.addItem(label, data)
    simulation_view_combo.setObjectName("simulationViewCombo")
    simulation_controls.addWidget(simulation_view_combo, 1)
    simulation_controls.addWidget(QtWidgets.QLabel("Background"))
    simulation_background_combo = QtWidgets.QComboBox()
    simulation_background_combo.addItem("Black", "dark")
    simulation_background_combo.addItem("White", "light")
    simulation_background_combo.setObjectName("simulationBackgroundCombo")
    simulation_controls.addWidget(simulation_background_combo, 1)
    simulation_popout_button = QtWidgets.QPushButton("Pop Out")
    simulation_popout_button.setObjectName("simulationPopoutButton")
    simulation_controls.addWidget(simulation_popout_button)
    simulation_fullscreen_button = QtWidgets.QPushButton("Fullscreen")
    simulation_fullscreen_button.setObjectName("simulationFullscreenButton")
    simulation_controls.addWidget(simulation_fullscreen_button)
    playback_layout.addLayout(simulation_controls)
    simulation_host = QtWidgets.QWidget()
    simulation_host.setMinimumHeight(280)
    simulation_layout = QtWidgets.QVBoxLayout(simulation_host)
    simulation_layout.setContentsMargins(0, 0, 0, 0)
    playback_layout.addWidget(simulation_host)
    queue_right_layout.addWidget(playback_group, 3)

    palette_group = QtWidgets.QGroupBox("Song Palette")
    palette_layout = QtWidgets.QVBoxLayout(palette_group)
    selected_song_label = QtWidgets.QLabel("Selected song: none")
    selected_assignment_label = QtWidgets.QLabel("Assigned palette: none")
    selected_path_label = QtWidgets.QLabel("Path: none")
    for control in (selected_song_label, selected_assignment_label, selected_path_label):
        control.setWordWrap(True)
        palette_layout.addWidget(control)
    source_buttons = QtWidgets.QHBoxLayout()
    use_active_profile_button = QtWidgets.QPushButton("Load Active Profile")
    open_profile_button = QtWidgets.QPushButton("Load Profile File")
    source_buttons.addWidget(use_active_profile_button)
    source_buttons.addWidget(open_profile_button)
    palette_layout.addLayout(source_buttons)
    palette_combo = QtWidgets.QComboBox()
    palette_layout.addWidget(palette_combo)
    generate_row = QtWidgets.QHBoxLayout()
    seed_edit = QtWidgets.QLineEdit()
    seed_edit.setPlaceholderText("Seed (optional)")
    generate_button = QtWidgets.QPushButton("Generate Palette")
    generate_row.addWidget(seed_edit)
    generate_row.addWidget(generate_button)
    palette_layout.addLayout(generate_row)
    preview_label = QtWidgets.QLabel("Palette Preview: none")
    preview_label.setWordWrap(True)
    palette_layout.addWidget(preview_label)
    preview_host = QtWidgets.QWidget()
    preview_layout = QtWidgets.QVBoxLayout(preview_host)
    preview_layout.setContentsMargins(0, 0, 0, 0)
    palette_layout.addWidget(preview_host)
    action_row = QtWidgets.QHBoxLayout()
    assign_button = QtWidgets.QPushButton("Assign Palette To Song")
    clear_button = QtWidgets.QPushButton("Clear Assignment")
    action_row.addWidget(assign_button)
    action_row.addWidget(clear_button)
    palette_layout.addLayout(action_row)
    status_label = QtWidgets.QLabel("")
    status_label.setWordWrap(True)
    palette_layout.addWidget(status_label)
    queue_right_layout.addWidget(palette_group, 2)
    patch_group = QtWidgets.QGroupBox("Show Override")
    patch_group.setObjectName("showPatchGroup")
    patch_layout = QtWidgets.QVBoxLayout(patch_group)
    patch_name_edit = QtWidgets.QLineEdit()
    patch_name_edit.setPlaceholderText("Patch name")
    patch_name_edit.setObjectName("showPatchNameEdit")
    patch_layout.addWidget(patch_name_edit)
    patch_rules_edit = QtWidgets.QPlainTextEdit()
    patch_rules_edit.setObjectName("showPatchRulesEdit")
    patch_rules_edit.setPlaceholderText(
        '[\n  {\n    "match": {"has_instrument": "vocals"},\n    "color_bias": "#ff88aa"\n  }\n]'
    )
    patch_layout.addWidget(patch_rules_edit, 1)
    patch_summary_label = QtWidgets.QLabel("No show override saved.")
    patch_summary_label.setWordWrap(True)
    patch_summary_label.setObjectName("showPatchSummaryLabel")
    patch_layout.addWidget(patch_summary_label)
    patch_button_row = QtWidgets.QHBoxLayout()
    save_patch_button = QtWidgets.QPushButton("Save Override")
    save_patch_button.setObjectName("saveShowPatchButton")
    clear_patch_button = QtWidgets.QPushButton("Clear Override")
    clear_patch_button.setObjectName("clearShowPatchButton")
    patch_button_row.addWidget(save_patch_button)
    patch_button_row.addWidget(clear_patch_button)
    patch_layout.addLayout(patch_button_row)
    queue_right_layout.addWidget(patch_group, 2)
    queue_right_layout.addStretch(1)
    queue_splitter.addWidget(queue_right_panel)
    queue_splitter.setStretchFactor(0, 3)
    queue_splitter.setStretchFactor(1, 2)

    shows_toolbar = QtWidgets.QHBoxLayout()
    for button in (
        compile_show_button,
        load_show_button,
        clear_show_button,
        save_show_button,
        play_saved_show_button,
        play_selected_cue_button,
        pause_show_button,
        stop_show_button,
    ):
        shows_toolbar.addWidget(button)
    shows_toolbar.addStretch(1)
    shows_layout.addLayout(shows_toolbar)
    shows_layout.addWidget(saved_show_label)

    shows_splitter = QtWidgets.QSplitter()
    shows_splitter.setOrientation(QtCore.Qt.Orientation.Horizontal)
    shows_layout.addWidget(shows_splitter, 1)

    shows_left_panel = QtWidgets.QWidget()
    shows_left_layout = QtWidgets.QVBoxLayout(shows_left_panel)

    saved_group = QtWidgets.QGroupBox("Saved Shows")
    saved_group.setObjectName("savedShowsGroup")
    saved_layout = QtWidgets.QVBoxLayout(saved_group)
    recent_saved_list = QtWidgets.QListWidget()
    recent_saved_list.setObjectName("recentSavedShowsList")
    saved_layout.addWidget(recent_saved_list)
    shows_left_layout.addWidget(saved_group, 1)

    ready_group = QtWidgets.QGroupBox("Captured Ready Shows")
    ready_layout = QtWidgets.QVBoxLayout(ready_group)
    ready_list = QtWidgets.QListWidget()
    ready_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
    ready_list.setObjectName("capturedReadyList")
    ready_layout.addWidget(ready_list)
    ready_actions = QtWidgets.QHBoxLayout()
    ready_preview_button = QtWidgets.QPushButton("Preview")
    ready_play_button = QtWidgets.QPushButton("Play Now")
    ready_prioritize_button = QtWidgets.QPushButton("Send To Top")
    ready_discard_button = QtWidgets.QPushButton("Discard")
    ready_preview_button.setObjectName("readyPreviewButton")
    ready_play_button.setObjectName("readyPlayButton")
    ready_prioritize_button.setObjectName("readyPrioritizeButton")
    ready_discard_button.setObjectName("readyDiscardButton")
    for button in (ready_preview_button, ready_play_button, ready_prioritize_button, ready_discard_button):
        ready_actions.addWidget(button)
    ready_layout.addLayout(ready_actions)
    shows_left_layout.addWidget(ready_group, 1)
    shows_splitter.addWidget(shows_left_panel)

    show_editor_group = QtWidgets.QGroupBox("Saved Show Editor")
    show_editor_group.setObjectName("savedShowEditorGroup")
    show_editor_layout = QtWidgets.QVBoxLayout(show_editor_group)
    show_audio_label = QtWidgets.QLabel("Audio track: none")
    show_audio_label.setObjectName("showEditorAudioLabel")
    show_audio_label.setWordWrap(True)
    show_editor_path_label = QtWidgets.QLabel("Show file: none")
    show_editor_path_label.setObjectName("showEditorPathLabel")
    show_editor_path_label.setWordWrap(True)
    show_editor_layout.addWidget(show_audio_label)
    show_editor_layout.addWidget(show_editor_path_label)
    show_editor_status_label = QtWidgets.QLabel("Load or compile a show to start editing.")
    show_editor_status_label.setObjectName("showEditorStatusLabel")
    show_editor_status_label.setWordWrap(True)
    show_editor_layout.addWidget(show_editor_status_label)
    timeline_toolbar = QtWidgets.QHBoxLayout()
    timeline_toolbar.addWidget(QtWidgets.QLabel("Timeline Zoom"))
    show_timeline_zoom_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
    show_timeline_zoom_slider.setObjectName("showTimelineZoomSlider")
    show_timeline_zoom_slider.setRange(100, 3000)
    show_timeline_zoom_slider.setValue(100)
    show_editor_focus_button = QtWidgets.QPushButton("Focus Editor")
    show_editor_focus_button.setObjectName("showEditorFocusButton")
    show_editor_focus_button.setCheckable(True)
    show_timeline_fit_button = QtWidgets.QPushButton("Fit")
    show_timeline_fit_button.setObjectName("showTimelineFitButton")
    timeline_toolbar.addWidget(show_timeline_zoom_slider, 1)
    timeline_toolbar.addWidget(show_editor_focus_button)
    timeline_toolbar.addWidget(show_timeline_fit_button)
    show_editor_layout.addLayout(timeline_toolbar)
    show_timeline_view = build_show_timeline_view(qt_modules)
    show_editor_layout.addWidget(show_timeline_view)
    show_timeline_scroll_bar = QtWidgets.QScrollBar(QtCore.Qt.Orientation.Horizontal)
    show_timeline_scroll_bar.setObjectName("showTimelineScrollBar")
    show_timeline_scroll_bar.setRange(0, 0)
    show_timeline_scroll_bar.setPageStep(0)
    show_editor_layout.addWidget(show_timeline_scroll_bar)

    show_meta_panel = QtWidgets.QWidget()
    show_meta_panel.setObjectName("showMetaPanel")
    show_meta_grid = QtWidgets.QGridLayout(show_meta_panel)
    show_meta_grid.addWidget(QtWidgets.QLabel("Title"), 0, 0)
    show_title_edit = QtWidgets.QLineEdit()
    show_title_edit.setObjectName("showTitleEdit")
    show_meta_grid.addWidget(show_title_edit, 0, 1)
    show_meta_grid.addWidget(QtWidgets.QLabel("Artist"), 1, 0)
    show_artist_edit = QtWidgets.QLineEdit()
    show_artist_edit.setObjectName("showArtistEdit")
    show_meta_grid.addWidget(show_artist_edit, 1, 1)
    show_meta_grid.addWidget(QtWidgets.QLabel("BPM"), 2, 0)
    show_bpm_spin = QtWidgets.QDoubleSpinBox()
    show_bpm_spin.setObjectName("showBpmSpin")
    show_bpm_spin.setRange(1.0, 400.0)
    show_bpm_spin.setDecimals(2)
    show_bpm_spin.setValue(120.0)
    show_meta_grid.addWidget(show_bpm_spin, 2, 1)
    show_meta_grid.addWidget(QtWidgets.QLabel("Time signature"), 3, 0)
    show_time_signature_combo = QtWidgets.QComboBox()
    show_time_signature_combo.setObjectName("showTimeSignatureCombo")
    show_time_signature_combo.addItem("4/4", 4)
    show_time_signature_combo.addItem("3/4", 3)
    show_meta_grid.addWidget(show_time_signature_combo, 3, 1)
    show_meta_grid.addWidget(QtWidgets.QLabel("Duration (s)"), 4, 0)
    show_duration_spin = QtWidgets.QDoubleSpinBox()
    show_duration_spin.setObjectName("showDurationSpin")
    show_duration_spin.setRange(0.1, 7200.0)
    show_duration_spin.setDecimals(3)
    show_duration_spin.setValue(180.0)
    show_meta_grid.addWidget(show_duration_spin, 4, 1)
    show_meta_grid.addWidget(QtWidgets.QLabel("Show palette"), 5, 0)
    show_palette_widget = QtWidgets.QWidget()
    show_palette_widget.setObjectName("showPaletteWidget")
    show_meta_grid.addWidget(show_palette_widget, 5, 1)
    show_import_palette_button = QtWidgets.QPushButton("Import Palette")
    show_import_palette_button.setObjectName("showImportPaletteButton")
    show_meta_grid.addWidget(show_import_palette_button, 5, 2)
    show_palette_import_label = QtWidgets.QLabel("")
    show_palette_import_label.setObjectName("showPaletteImportLabel")
    show_palette_import_label.setWordWrap(True)
    show_meta_grid.addWidget(show_palette_import_label, 6, 0, 1, 3)
    show_editor_layout.addWidget(show_meta_panel)

    show_editor_buttons = QtWidgets.QHBoxLayout()
    add_show_cue_button = QtWidgets.QPushButton("Add Cue")
    add_show_cue_button.setObjectName("addShowCueButton")
    duplicate_show_cue_button = QtWidgets.QPushButton("Duplicate Cue")
    duplicate_show_cue_button.setObjectName("duplicateShowCueButton")
    remove_show_cue_button = QtWidgets.QPushButton("Remove Cue")
    remove_show_cue_button.setObjectName("removeShowCueButton")
    show_meta_toggle_button = QtWidgets.QPushButton("Hide Details")
    show_meta_toggle_button.setObjectName("showMetaToggleButton")
    show_meta_toggle_button.setCheckable(True)
    show_columns_button = QtWidgets.QToolButton()
    show_columns_button.setObjectName("showColumnsButton")
    show_columns_button.setText("Columns")
    show_columns_button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
    show_editor_buttons.addWidget(add_show_cue_button)
    show_editor_buttons.addWidget(duplicate_show_cue_button)
    show_editor_buttons.addWidget(remove_show_cue_button)
    show_editor_buttons.addWidget(show_meta_toggle_button)
    show_editor_buttons.addWidget(show_columns_button)
    show_editor_buttons.addStretch(1)
    show_editor_layout.addLayout(show_editor_buttons)

    show_cues_table = QtWidgets.QTableWidget(0, 20)
    show_cues_table.setObjectName("showCuesTable")
    show_cues_table.setHorizontalHeaderLabels(
        [
            "Beat",
            "Time",
            "Render",
            "Palette",
            "Intensity",
            "Speed",
            "Transition",
            "Trans Beats",
            "Wave Rate",
            "Width",
            "When",
            "Pan Follow",
            "Int Boost",
            "Instrument",
            "Confidence",
            "EQ Band",
            "Spatial",
            "Color Bias",
            "Extra",
            "Intensity Start",
        ]
    )
    show_cues_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
    show_cues_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
    show_cues_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
    show_cues_table.verticalHeader().setVisible(False)
    show_cues_table.horizontalHeader().setStretchLastSection(True)
    show_cues_table.setAlternatingRowColors(True)
    show_cues_table.setMinimumHeight(320)
    show_editor_layout.addWidget(show_cues_table, 1)
    shows_splitter.addWidget(show_editor_group)
    shows_splitter.setStretchFactor(0, 1)
    shows_splitter.setStretchFactor(1, 2)

    return QueuePanelWidgets(
        widget=queue_widget,
        shows_widget=shows_widget,
        config_widget=config_widget,
        load_file_button=load_file_button,
        load_folder_button=load_folder_button,
        load_show_button=load_show_button,
        compile_show_button=compile_show_button,
        clear_show_button=clear_show_button,
        save_show_button=save_show_button,
        refresh_button=refresh_button,
        start_preview_button=start_preview_button,
        play_saved_show_button=play_saved_show_button,
        play_selected_cue_button=play_selected_cue_button,
        start_capture_button=start_capture_button,
        stop_capture_button=stop_capture_button,
        switch_pipeline_button=switch_pipeline_button,
        start_reactive_button=start_reactive_button,
        stop_output_button=stop_output_button,
        stop_preview_button=stop_preview_button,
        play_now_button=play_now_button,
        remove_button=remove_button,
        shuffle_button=shuffle_button,
        playlist_label=playlist_label,
        saved_show_label=saved_show_label,
        output_mode_label=output_mode_label,
        capture_status_label=capture_status_label,
        pipeline_status_label=pipeline_status_label,
        routing_status_label=routing_status_label,
        active_owner_label=active_owner_label,
        armed_owner_label=armed_owner_label,
        output_target_combo=output_target_combo,
        hardware_fallback_check=hardware_fallback_check,
        output_device_combo=output_device_combo,
        input_device_combo=input_device_combo,
        capture_dir_edit=capture_dir_edit,
        capture_naming_combo=capture_naming_combo,
        capture_buffer_spin=capture_buffer_spin,
        capture_device_pattern_edit=capture_device_pattern_edit,
        capture_sample_rate_spin=capture_sample_rate_spin,
        capture_channels_spin=capture_channels_spin,
        capture_frame_size_spin=capture_frame_size_spin,
        capture_hop_size_spin=capture_hop_size_spin,
        capture_blocksize_spin=capture_blocksize_spin,
        pipeline_playback_device_combo=pipeline_playback_device_combo,
        purge_after_playback_check=purge_after_playback_check,
        debug_pipeline_check=debug_pipeline_check,
        reactive_render_mode_combo=reactive_render_mode_combo,
        reactive_sample_rate_spin=reactive_sample_rate_spin,
        reactive_channels_spin=reactive_channels_spin,
        reactive_frame_size_spin=reactive_frame_size_spin,
        reactive_hop_size_spin=reactive_hop_size_spin,
        reactive_blocksize_spin=reactive_blocksize_spin,
        reactive_half_time_check=reactive_half_time_check,
        reactive_max_brightness_check=reactive_max_brightness_check,
        reactive_auto_cycle_check=reactive_auto_cycle_check,
        reactive_cycle_interval_spin=reactive_cycle_interval_spin,
        reactive_debug_mood_check=reactive_debug_mood_check,
        reactive_crossfade_check=reactive_crossfade_check,
        reactive_telemetry_dir_edit=reactive_telemetry_dir_edit,
        reactive_profile_strategy_combo=reactive_profile_strategy_combo,
        reactive_profile_override_edit=reactive_profile_override_edit,
        reactive_rotation_profiles_edit=reactive_rotation_profiles_edit,
        reactive_rotation_interval_spin=reactive_rotation_interval_spin,
        reactive_auto_palette_check=reactive_auto_palette_check,
        reactive_smart_rotation_check=reactive_smart_rotation_check,
        reactive_chain_blend_spin=reactive_chain_blend_spin,
        local_list=local_list,
        recent_saved_list=recent_saved_list,
        shows_splitter=shows_splitter,
        shows_left_panel=shows_left_panel,
        show_audio_label=show_audio_label,
        show_editor_path_label=show_editor_path_label,
        show_editor_status_label=show_editor_status_label,
        show_pause_button=pause_show_button,
        show_stop_button=stop_show_button,
        show_editor_focus_button=show_editor_focus_button,
        show_timeline_zoom_slider=show_timeline_zoom_slider,
        show_timeline_fit_button=show_timeline_fit_button,
        show_timeline_scroll_bar=show_timeline_scroll_bar,
        show_bpm_spin=show_bpm_spin,
        show_time_signature_combo=show_time_signature_combo,
        show_duration_spin=show_duration_spin,
        show_title_edit=show_title_edit,
        show_artist_edit=show_artist_edit,
        show_palette_widget=show_palette_widget,
        show_import_palette_button=show_import_palette_button,
        show_palette_import_label=show_palette_import_label,
        show_meta_panel=show_meta_panel,
        show_meta_toggle_button=show_meta_toggle_button,
        show_columns_button=show_columns_button,
        show_cues_table=show_cues_table,
        show_timeline_view=show_timeline_view,
        add_show_cue_button=add_show_cue_button,
        duplicate_show_cue_button=duplicate_show_cue_button,
        remove_show_cue_button=remove_show_cue_button,
        spotify_list=spotify_list,
        ready_list=ready_list,
        ready_preview_button=ready_preview_button,
        ready_play_button=ready_play_button,
        ready_prioritize_button=ready_prioritize_button,
        ready_discard_button=ready_discard_button,
        playback_status_label=playback_status_label,
        playback_track_label=playback_track_label,
        playback_time_label=playback_time_label,
        device_status_label=device_status_label,
        audio_output_label=audio_output_label,
        input_device_status_label=input_device_status_label,
        simulation_view_combo=simulation_view_combo,
        simulation_background_combo=simulation_background_combo,
        simulation_popout_button=simulation_popout_button,
        simulation_fullscreen_button=simulation_fullscreen_button,
        simulation_host=simulation_host,
        simulation_layout=simulation_layout,
        selected_song_label=selected_song_label,
        selected_assignment_label=selected_assignment_label,
        selected_path_label=selected_path_label,
        use_active_profile_button=use_active_profile_button,
        open_profile_button=open_profile_button,
        palette_combo=palette_combo,
        generate_button=generate_button,
        seed_edit=seed_edit,
        preview_label=preview_label,
        preview_host=preview_host,
        preview_layout=preview_layout,
        assign_button=assign_button,
        clear_button=clear_button,
        patch_name_edit=patch_name_edit,
        patch_rules_edit=patch_rules_edit,
        patch_summary_label=patch_summary_label,
        save_patch_button=save_patch_button,
        clear_patch_button=clear_patch_button,
        runtime_palette_override_edit=runtime_palette_override_edit,
        runtime_color_bias_edit=runtime_color_bias_edit,
        runtime_render_mode_combo=runtime_render_mode_combo,
        runtime_intensity_multiplier_spin=runtime_intensity_multiplier_spin,
        runtime_intensity_offset_spin=runtime_intensity_offset_spin,
        runtime_speed_multiplier_spin=runtime_speed_multiplier_spin,
        runtime_speed_offset_spin=runtime_speed_offset_spin,
        runtime_muted_bands_edit=runtime_muted_bands_edit,
        runtime_muted_instruments_edit=runtime_muted_instruments_edit,
        runtime_spatial_preset_edit=runtime_spatial_preset_edit,
        runtime_spatial_width_spin=runtime_spatial_width_spin,
        runtime_apply_button=runtime_apply_button,
        runtime_clear_button=runtime_clear_button,
        runtime_control_status_label=runtime_control_status_label,
        status_label=status_label,
    )
