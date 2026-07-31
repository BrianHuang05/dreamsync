"""Queue and runtime dashboard widgets."""

from __future__ import annotations

from dataclasses import dataclass

from dreamsync.gui.widgets.reactive_chord_history_view import (
    build_reactive_chord_history_view,
)
from dreamsync.gui.widgets.reactive_harmonic_debug_view import (
    build_reactive_harmonic_debug_view,
)
from dreamsync.gui.widgets.reactive_waveform_view import build_reactive_waveform_view
from dreamsync.gui.widgets.show_timeline_view import build_show_timeline_view


@dataclass(frozen=True)
class QueuePanelWidgets:
    widget: object
    shows_widget: object
    config_widget: object
    save_configuration_button: object
    configuration_save_status_label: object
    load_saved_show_button: object
    load_saved_track_button: object
    cue_track_button: object
    load_show_button: object
    compile_show_button: object
    compile_all_tracks_button: object
    clear_show_button: object
    save_show_button: object
    add_show_track_button: object
    remove_show_track_button: object
    move_show_track_up_button: object
    move_show_track_down_button: object
    bake_show_button: object
    validate_baked_button: object
    save_baked_as_button: object
    compile_seed_check: object
    compile_seed_spin: object
    refresh_button: object
    start_preview_button: object
    pause_playback_button: object
    play_saved_show_button: object
    baked_playback_combo: object
    play_selected_cue_button: object
    start_capture_button: object
    stop_capture_button: object
    switch_pipeline_button: object
    start_reactive_button: object
    stop_output_button: object
    stop_preview_button: object
    shuffle_button: object
    repeat_button: object
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
    dark_mode_check: object
    output_device_combo: object
    input_device_combo: object
    startup_live_mode_combo: object
    device_health_label: object
    refresh_device_health_button: object
    device_room_config_path_edit: object
    browse_device_room_config_button: object
    profile_directory_edit: object
    browse_profile_directory_button: object
    show_directory_edit: object
    browse_show_directory_button: object
    queue_directory_edit: object
    browse_queue_directory_button: object
    apply_file_locations_button: object
    file_locations_status_label: object
    storage_cache_label: object
    storage_capture_label: object
    refresh_storage_button: object
    clear_all_cache_button: object
    clear_selected_cache_button: object
    archive_captures_button: object
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
    reactive_mirror_combo: object
    reactive_master_brightness_spin: object
    reactive_auto_cycle_check: object
    reactive_cycle_interval_spin: object
    reactive_debug_mood_check: object
    reactive_crossfade_check: object
    reactive_harmonic_structure_check: object
    reactive_structure_sensitivity_spin: object
    reactive_debug_harmonics_check: object
    reactive_predictive_analysis_check: object
    reactive_predictive_diagnostics_check: object
    reactive_predictive_shadow_check: object
    reactive_predictive_cues_check: object
    reactive_structure_phrase_actions_check: object
    reactive_predictive_high_impact_check: object
    reactive_telemetry_dir_edit: object
    reactive_profile_strategy_combo: object
    reactive_profile_override_label: object
    browse_reactive_profile_button: object
    reactive_show_palette_set_combo: object
    reactive_palette_rotation_label: object
    reactive_rotation_profiles_picker: object
    reactive_configuration_warning_label: object
    reactive_rotation_interval_spin: object
    reactive_auto_palette_check: object
    reactive_smart_rotation_check: object
    reactive_chain_blend_spin: object
    reactive_auto_palette_seed_check: object
    reactive_auto_palette_seed_spin: object
    reactive_auto_palette_pool_size_spin: object
    reactive_chain_dwell_range_check: object
    reactive_chain_min_dwell_spin: object
    reactive_chain_max_dwell_spin: object
    preview_profile_chain_button: object
    local_list: object
    live_queue_toolbar: object
    queue_mode_button: object
    reactive_mode_button: object
    raw_visualizer_mode_button: object
    live_queue_group: object
    live_reactive_group: object
    reactive_mode_status_label: object
    reactive_profile_label: object
    reactive_active_palette_label: object
    reactive_active_palette_preview_label: object
    reactive_palette_next_label: object
    reactive_palette_queue_label: object
    reactive_live_look_group: object
    reactive_live_color_profile_combo: object
    reactive_live_active_effect_combo: object
    reactive_live_effect_speed_combo: object
    reactive_live_effect_origin_combo: object
    reactive_live_effect_buttons: tuple[object, ...]
    reactive_live_look_status_label: object
    reactive_listening_label: object
    reactive_beat_indicator_label: object
    reactive_bpm_label: object
    reactive_effect_tempo_label: object
    reactive_cycle_tempo_label: object
    reactive_cycle_tempo_half_button: object
    reactive_cycle_tempo_normal_button: object
    reactive_cycle_tempo_double_button: object
    reactive_effect_tempo_half_button: object
    reactive_effect_tempo_normal_button: object
    reactive_effect_tempo_double_button: object
    reactive_downbeat_nudge_button: object
    reactive_chord_panel_check: object
    reactive_waveform_panel_check: object
    reactive_harmonic_panel_check: object
    reactive_diagnostics_splitter: object
    reactive_chord_history_group: object
    reactive_chord_label: object
    reactive_chord_history_view: object
    reactive_chord_popout_button: object
    reactive_chord_fullscreen_button: object
    reactive_waveform_panel: object
    reactive_active_effects_label: object
    reactive_cycle_label: object
    reactive_waveform_view: object
    reactive_harmonic_debug_group: object
    reactive_harmonic_debug_view: object
    reactive_harmonic_popout_button: object
    reactive_harmonic_fullscreen_button: object
    stop_reactive_button: object
    spotify_group: object
    recent_saved_list: object
    show_tracks_list: object
    show_name_edit: object
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
    show_meta_group: object
    show_meta_panel: object
    show_routing_group: object
    show_routing_host: object
    show_meta_toggle_button: object
    show_columns_button: object
    show_cues_table: object
    show_timeline_view: object
    show_simulation_group: object
    show_simulation_content: object
    show_simulation_view_combo: object
    show_simulation_background_combo: object
    show_simulation_strip_mode_combo: object
    show_simulation_popout_button: object
    show_simulation_fullscreen_button: object
    show_simulation_host: object
    show_simulation_layout: object
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
    baked_status_label: object
    playback_track_label: object
    playback_time_label: object
    device_status_label: object
    audio_output_label: object
    input_device_status_label: object
    simulation_view_combo: object
    simulation_background_combo: object
    simulation_strip_mode_combo: object
    simulation_freeze_check: object
    simulation_frame_diagnostics_label: object
    simulation_popout_button: object
    simulation_fullscreen_button: object
    simulation_host: object
    simulation_layout: object
    selected_song_label: object
    selected_assignment_label: object
    selected_path_label: object
    live_show_source_label: object
    live_palette_preview_label: object
    live_palette_group: object
    cycle_palette_button: object
    recompile_palette_button: object
    live_palette_status_label: object
    spotify_refresh_button: object
    spotify_skip_button: object
    spotify_shuffle_button: object
    spotify_uri_edit: object
    spotify_add_button: object
    live_loopback_check: object
    patch_name_edit: object
    patch_rules_edit: object
    patch_summary_label: object
    save_patch_button: object
    clear_patch_button: object
    show_override_group: object
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
    runtime_disabled_groups_edit: object
    runtime_enabled_groups_edit: object
    runtime_solo_groups_edit: object
    runtime_apply_button: object
    runtime_clear_button: object
    runtime_control_status_label: object
    status_label: object


def build_queue_panel(qt_modules):
    QtWidgets = qt_modules.QtWidgets
    QtCore = qt_modules.QtCore

    class QueueListWidget(QtWidgets.QListWidget):
        reordered = QtCore.Signal(int, int)
        dragStarted = QtCore.Signal()
        dragFinished = QtCore.Signal()

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._drag_start_row = -1

        def startDrag(self, supportedActions):  # pragma: no cover - exercised through Qt runtime
            self._drag_start_row = self.currentRow()
            self.dragStarted.emit()
            try:
                super().startDrag(supportedActions)
            finally:
                self.dragFinished.emit()

        def dropEvent(self, event):  # pragma: no cover - exercised through Qt runtime
            from_row = self._drag_start_row
            to_row = self.indexAt(event.position().toPoint()).row()
            if to_row < 0:
                to_row = self.count() - 1
            if from_row >= 0 and to_row >= 0 and from_row != to_row:
                self.reordered.emit(from_row, to_row)
            event.setDropAction(QtCore.Qt.DropAction.MoveAction)
            event.accept()
            self._drag_start_row = -1

    queue_widget = QtWidgets.QWidget()
    queue_widget.setObjectName("liveTabWidget")
    queue_layout = QtWidgets.QVBoxLayout(queue_widget)
    shows_widget = QtWidgets.QWidget()
    shows_widget.setObjectName("showsTabWidget")
    shows_layout = QtWidgets.QVBoxLayout(shows_widget)
    config_widget = QtWidgets.QWidget()
    config_widget.setObjectName("configTabWidget")
    config_layout = QtWidgets.QVBoxLayout(config_widget)
    configuration_save_bar = QtWidgets.QHBoxLayout()
    save_configuration_button = QtWidgets.QPushButton("Save Configuration")
    save_configuration_button.setObjectName("saveConfigurationButton")
    save_configuration_button.setToolTip("Validate and save this configuration (Ctrl+S)")
    configuration_save_status_label = QtWidgets.QLabel(
        "Configuration changes are not saved until you use Save Configuration."
    )
    configuration_save_status_label.setObjectName("configurationSaveStatusLabel")
    configuration_save_status_label.setWordWrap(True)
    configuration_save_status_label.setStyleSheet("color: #64748b;")
    configuration_save_bar.addWidget(save_configuration_button)
    configuration_save_bar.addWidget(configuration_save_status_label, 1)
    config_layout.addLayout(configuration_save_bar)

    load_saved_show_button = QtWidgets.QPushButton("Load Saved Show")
    load_saved_track_button = QtWidgets.QPushButton("Cue Compiled Track")
    cue_track_button = QtWidgets.QPushButton("Cue Audio to Compile")
    load_show_button = QtWidgets.QPushButton("Open Saved Show")
    compile_show_button = QtWidgets.QPushButton("Compile Track")
    compile_all_tracks_button = QtWidgets.QPushButton("Compile All Tracks")
    clear_show_button = QtWidgets.QPushButton("New Show")
    save_show_button = QtWidgets.QPushButton("Save Show")
    add_show_track_button = QtWidgets.QPushButton("Add Track…")
    remove_show_track_button = QtWidgets.QPushButton("Remove Track")
    move_show_track_up_button = QtWidgets.QPushButton("Move Up")
    move_show_track_down_button = QtWidgets.QPushButton("Move Down")
    bake_show_button = QtWidgets.QPushButton("Bake Frames")
    validate_baked_button = QtWidgets.QPushButton("Validate Baked")
    validate_baked_button.setObjectName("validateBakedFramesButton")
    save_baked_as_button = QtWidgets.QPushButton("Save Baked As…")
    save_baked_as_button.setObjectName("saveBakedFramesAsButton")
    refresh_button = QtWidgets.QPushButton("Refresh")
    start_preview_button = QtWidgets.QPushButton("Start Playback")
    pause_playback_button = QtWidgets.QPushButton("Pause")
    play_saved_show_button = QtWidgets.QPushButton("Play Show")
    baked_playback_combo = QtWidgets.QComboBox()
    baked_playback_combo.addItem("Auto", "auto")
    baked_playback_combo.addItem("Off", "off")
    baked_playback_combo.addItem("Require", "require")
    play_selected_cue_button = QtWidgets.QPushButton("Play From Cue")
    pause_show_button = QtWidgets.QPushButton("Pause")
    stop_show_button = QtWidgets.QPushButton("Stop")
    start_capture_button = QtWidgets.QPushButton("Start Audio Loopback Capture")
    stop_capture_button = QtWidgets.QPushButton("Stop Audio Loopback Capture")
    switch_pipeline_button = QtWidgets.QPushButton("Auto-play Captured Shows")
    start_reactive_button = QtWidgets.QPushButton("Start Reactive")
    stop_output_button = QtWidgets.QPushButton("Stop Output")
    stop_preview_button = QtWidgets.QPushButton("Stop")
    shuffle_button = QtWidgets.QPushButton("Shuffle Upcoming")
    repeat_button = QtWidgets.QPushButton("Repeat Queue")
    repeat_button.setCheckable(True)
    repeat_button.setObjectName("repeatQueueButton")
    repeat_button.setToolTip("Repeat the local queue from the beginning after its final track.")
    playlist_label = QtWidgets.QLabel("No local queue loaded.")
    saved_show_label = QtWidgets.QLabel("Saved show: none")
    load_saved_show_button.setObjectName("loadSavedShowButton")
    load_saved_show_button.setToolTip(
        "Open a saved multi-track Show from the configured Show folder."
    )
    load_saved_track_button.setObjectName("loadSavedTrackButton")
    load_saved_track_button.setToolTip(
        "Queue one precompiled track lightshow from the configured Show folder."
    )
    cue_track_button.setObjectName("cueUncompiledTrackButton")
    cue_track_button.setToolTip(
        "Insert an MP3 after the current queue item; it will be compiled before playback."
    )
    load_show_button.setObjectName("loadShowButton")
    compile_show_button.setObjectName("compileShowButton")
    compile_all_tracks_button.setObjectName("compileAllTracksButton")
    clear_show_button.setObjectName("clearShowButton")
    save_show_button.setObjectName("saveShowButton")
    add_show_track_button.setObjectName("addShowTrackButton")
    remove_show_track_button.setObjectName("removeShowTrackButton")
    move_show_track_up_button.setObjectName("moveShowTrackUpButton")
    move_show_track_down_button.setObjectName("moveShowTrackDownButton")
    bake_show_button.setObjectName("bakeShowButton")
    play_saved_show_button.setObjectName("playSavedShowButton")
    baked_playback_combo.setObjectName("bakedPlaybackModeCombo")
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

    live_mode_row = QtWidgets.QHBoxLayout()
    queue_mode_button = QtWidgets.QPushButton("Queue Mode")
    queue_mode_button.setObjectName("queueLiveModeButton")
    queue_mode_button.setCheckable(True)
    reactive_mode_button = QtWidgets.QPushButton("Reactive Mode")
    reactive_mode_button.setObjectName("reactiveLiveModeButton")
    reactive_mode_button.setCheckable(True)
    raw_visualizer_mode_button = QtWidgets.QPushButton("Raw Visualizer")
    raw_visualizer_mode_button.setObjectName("rawVisualizerLiveModeButton")
    raw_visualizer_mode_button.setCheckable(True)
    raw_visualizer_mode_button.setToolTip(
        "Frequency-only center visualizer: bass red, mids green, highs violet; no tempo detection."
    )
    live_mode_row.addWidget(queue_mode_button)
    live_mode_row.addWidget(reactive_mode_button)
    live_mode_row.addWidget(raw_visualizer_mode_button)
    live_mode_row.addStretch(1)
    queue_layout.addLayout(live_mode_row)
    live_queue_toolbar = QtWidgets.QWidget()
    queue_toolbar = QtWidgets.QHBoxLayout(live_queue_toolbar)
    queue_toolbar.setContentsMargins(0, 0, 0, 0)
    for button in (
        load_saved_show_button,
        load_saved_track_button,
        cue_track_button,
        refresh_button,
        start_preview_button,
        pause_playback_button,
        stop_preview_button,
        shuffle_button,
        repeat_button,
    ):
        queue_toolbar.addWidget(button)
    queue_toolbar.addStretch(1)
    queue_layout.addWidget(live_queue_toolbar)
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

    playback_info_group = QtWidgets.QGroupBox("Playback Status")
    playback_info_group.setObjectName("playbackStatusGroup")
    playback_info_layout = QtWidgets.QGridLayout(playback_info_group)
    playback_status_label = QtWidgets.QLabel("Playback: idle")
    baked_status_label = QtWidgets.QLabel("Baked: auto")
    playback_track_label = QtWidgets.QLabel("Current track: none")
    playback_time_label = QtWidgets.QLabel("Time: 00:00 / 00:00")
    device_status_label = QtWidgets.QLabel("Devices: preview mode (no connected devices)")
    audio_output_label = QtWidgets.QLabel("Audio output: system default")
    input_device_status_label = QtWidgets.QLabel("Input device: system default")
    for control in (
        playback_status_label,
        baked_status_label,
        playback_track_label,
        playback_time_label,
        device_status_label,
        audio_output_label,
        input_device_status_label,
    ):
        control.setWordWrap(True)
    playback_info_layout.addWidget(playback_status_label, 0, 0)
    playback_info_layout.addWidget(baked_status_label, 0, 1)
    playback_info_layout.addWidget(playback_track_label, 1, 0)
    playback_info_layout.addWidget(playback_time_label, 1, 1)
    playback_info_layout.addWidget(device_status_label, 2, 0, 1, 2)
    playback_info_layout.addWidget(audio_output_label, 3, 0)
    playback_info_layout.addWidget(input_device_status_label, 3, 1)
    playback_info_layout.addWidget(QtWidgets.QLabel("Baked playback"), 4, 0)
    playback_info_layout.addWidget(baked_playback_combo, 4, 1)
    config_layout.addWidget(playback_info_group)

    config_actions = QtWidgets.QHBoxLayout()
    for button in (
        start_capture_button,
        stop_capture_button,
        switch_pipeline_button,
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

    appearance_group = QtWidgets.QGroupBox("Appearance")
    appearance_group.setObjectName("appearanceGroup")
    appearance_layout = QtWidgets.QVBoxLayout(appearance_group)
    dark_mode_check = QtWidgets.QCheckBox("Use dark application theme")
    dark_mode_check.setObjectName("darkModeCheck")
    dark_mode_check.setToolTip(
        "Use a dark application background and light text. The simulation background remains independent."
    )
    appearance_layout.addWidget(dark_mode_check)
    controls_column.addWidget(appearance_group)

    file_locations_group = QtWidgets.QGroupBox("File Locations")
    file_locations_group.setObjectName("fileLocationsGroup")
    file_locations_layout = QtWidgets.QGridLayout(file_locations_group)

    def _path_row(row: int, label: str, object_name: str):
        file_locations_layout.addWidget(QtWidgets.QLabel(label), row, 0)
        edit = QtWidgets.QLineEdit()
        edit.setObjectName(object_name)
        file_locations_layout.addWidget(edit, row, 1)
        browse = QtWidgets.QPushButton("Browse…")
        browse.setObjectName(f"browse{object_name[:1].upper()}{object_name[1:]}")
        file_locations_layout.addWidget(browse, row, 2)
        return edit, browse

    device_room_config_path_edit, browse_device_room_config_button = _path_row(
        0,
        "Device / room layout config",
        "deviceRoomConfigPathEdit",
    )
    device_room_config_path_edit.setPlaceholderText("Path to the shared device and room-layout YAML file")
    profile_directory_edit, browse_profile_directory_button = _path_row(
        1,
        "Profile files folder",
        "profileDirectoryEdit",
    )
    show_directory_edit, browse_show_directory_button = _path_row(
        2,
        "Show / Quickshow folder",
        "showDirectoryEdit",
    )
    queue_directory_edit, browse_queue_directory_button = _path_row(
        3,
        "Local audio / playlist source folder",
        "queueDirectoryEdit",
    )
    apply_file_locations_button = QtWidgets.QPushButton("Apply File Locations")
    apply_file_locations_button.setObjectName("applyFileLocationsButton")
    apply_file_locations_button.setToolTip("Apply the device / room-layout config path now; folders are used by their next file dialog.")
    file_locations_layout.addWidget(apply_file_locations_button, 4, 0, 1, 3)
    file_locations_status_label = QtWidgets.QLabel("")
    file_locations_status_label.setObjectName("fileLocationsStatusLabel")
    file_locations_status_label.setWordWrap(True)
    file_locations_layout.addWidget(file_locations_status_label, 5, 0, 1, 3)
    controls_column.addWidget(file_locations_group)

    storage_group = QtWidgets.QGroupBox("Storage")
    storage_group.setObjectName("storageGroup")
    storage_layout = QtWidgets.QVBoxLayout(storage_group)
    storage_cache_label = QtWidgets.QLabel("Cache: not refreshed")
    storage_cache_label.setObjectName("storageCacheLabel")
    storage_cache_label.setWordWrap(True)
    storage_capture_label = QtWidgets.QLabel("Captures: not refreshed")
    storage_capture_label.setObjectName("storageCaptureLabel")
    storage_capture_label.setWordWrap(True)
    storage_layout.addWidget(storage_cache_label)
    storage_layout.addWidget(storage_capture_label)
    storage_actions = QtWidgets.QHBoxLayout()
    refresh_storage_button = QtWidgets.QPushButton("Refresh")
    clear_all_cache_button = QtWidgets.QPushButton("Clear All Cache…")
    clear_selected_cache_button = QtWidgets.QPushButton("Clear Selected Track Cache…")
    archive_captures_button = QtWidgets.QPushButton("Archive Captures…")
    refresh_storage_button.setObjectName("refreshStorageButton")
    clear_all_cache_button.setObjectName("clearAllCacheButton")
    clear_selected_cache_button.setObjectName("clearSelectedTrackCacheButton")
    archive_captures_button.setObjectName("archiveCapturesButton")
    for button in (
        refresh_storage_button,
        clear_all_cache_button,
        clear_selected_cache_button,
        archive_captures_button,
    ):
        storage_actions.addWidget(button)
    storage_actions.addStretch(1)
    storage_layout.addLayout(storage_actions)
    controls_column.addWidget(storage_group)

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
    runtime_layout.addWidget(QtWidgets.QLabel("Reactive audio input"), 3, 0)
    input_device_combo = QtWidgets.QComboBox()
    input_device_combo.setObjectName("liveInputDeviceCombo")
    runtime_layout.addWidget(input_device_combo, 3, 1)
    live_loopback_check = QtWidgets.QCheckBox("Enable Spotify queue loopback")
    live_loopback_check.setObjectName("liveLoopbackCheck")
    live_loopback_check.setToolTip(
        "Show and manage the Spotify queue. This is separate from the system-audio loopback device used by Audio Loopback Capture."
    )
    runtime_layout.addWidget(live_loopback_check, 4, 0, 1, 2)
    runtime_layout.addWidget(QtWidgets.QLabel("Live startup screen"), 5, 0)
    startup_live_mode_combo = QtWidgets.QComboBox()
    startup_live_mode_combo.setObjectName("startupLiveModeCombo")
    startup_live_mode_combo.addItem("Queue", "queue")
    startup_live_mode_combo.addItem("Reactive (cued)", "reactive")
    startup_live_mode_combo.addItem("Raw Visualizer (cued)", "raw_visualizer")
    startup_live_mode_combo.setToolTip(
        "Chooses the Live screen shown when DreamSync opens. Listening modes are cued but do not start until the action button or Space."
    )
    runtime_layout.addWidget(startup_live_mode_combo, 5, 1)
    runtime_layout.addWidget(QtWidgets.QLabel("Configured device health"), 6, 0)
    device_health_widget = QtWidgets.QWidget()
    device_health_row = QtWidgets.QHBoxLayout(device_health_widget)
    device_health_row.setContentsMargins(0, 0, 0, 0)
    device_health_label = QtWidgets.QLabel("Off in simulation mode.")
    device_health_label.setObjectName("deviceHealthLabel")
    device_health_label.setWordWrap(True)
    refresh_device_health_button = QtWidgets.QPushButton("Refresh")
    refresh_device_health_button.setObjectName("refreshDeviceHealthButton")
    refresh_device_health_button.setToolTip(
        "Run passive LAN reachability probes for configured hardware. This does not scan BLE or send light output."
    )
    device_health_row.addWidget(device_health_label, 1)
    device_health_row.addWidget(refresh_device_health_button)
    runtime_layout.addWidget(device_health_widget, 6, 1)
    controls_column.addWidget(runtime_group)

    capture_group = QtWidgets.QGroupBox("Audio Loopback Capture Settings")
    capture_group.setObjectName("captureSettingsGroup")
    capture_layout = QtWidgets.QGridLayout(capture_group)
    capture_explanation = QtWidgets.QLabel(
        "Records a system-audio loopback device, splits the incoming audio into songs, and prepares shows for captured-show playback. "
        "Enable the loopback device in your operating system first."
    )
    capture_explanation.setWordWrap(True)
    capture_explanation.setObjectName("audioLoopbackCaptureExplanation")
    capture_layout.addWidget(capture_explanation, 0, 0, 1, 2)
    capture_layout.addWidget(QtWidgets.QLabel("Directory"), 1, 0)
    capture_dir_edit = QtWidgets.QLineEdit("captured_songs")
    capture_dir_edit.setObjectName("captureDirEdit")
    capture_layout.addWidget(capture_dir_edit, 1, 1)
    capture_layout.addWidget(QtWidgets.QLabel("Naming"), 2, 0)
    capture_naming_combo = QtWidgets.QComboBox()
    capture_naming_combo.addItem("Timestamp", "timestamp")
    capture_naming_combo.addItem("Metadata", "metadata")
    capture_naming_combo.setObjectName("captureNamingCombo")
    capture_layout.addWidget(capture_naming_combo, 2, 1)
    capture_layout.addWidget(QtWidgets.QLabel("Buffer"), 3, 0)
    capture_buffer_spin = QtWidgets.QSpinBox()
    capture_buffer_spin.setRange(0, 9999)
    capture_buffer_spin.setObjectName("captureBufferSpin")
    capture_layout.addWidget(capture_buffer_spin, 3, 1)
    capture_layout.addWidget(QtWidgets.QLabel("System-loopback device pattern"), 4, 0)
    capture_device_pattern_edit = QtWidgets.QLineEdit("CABLE Output")
    capture_device_pattern_edit.setObjectName("captureDevicePatternEdit")
    capture_layout.addWidget(capture_device_pattern_edit, 4, 1)
    capture_layout.addWidget(QtWidgets.QLabel("Sample rate"), 5, 0)
    capture_sample_rate_spin = QtWidgets.QSpinBox()
    capture_sample_rate_spin.setRange(8000, 192000)
    capture_sample_rate_spin.setValue(44100)
    capture_sample_rate_spin.setObjectName("captureSampleRateSpin")
    capture_layout.addWidget(capture_sample_rate_spin, 5, 1)
    capture_layout.addWidget(QtWidgets.QLabel("Channels"), 6, 0)
    capture_channels_spin = QtWidgets.QSpinBox()
    capture_channels_spin.setRange(1, 8)
    capture_channels_spin.setValue(2)
    capture_channels_spin.setObjectName("captureChannelsSpin")
    capture_layout.addWidget(capture_channels_spin, 6, 1)
    capture_layout.addWidget(QtWidgets.QLabel("Frame / hop / block"), 7, 0)
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
    capture_layout.addLayout(capture_sizes_row, 7, 1)
    capture_layout.addWidget(QtWidgets.QLabel("Captured-show playback device"), 8, 0)
    pipeline_playback_device_combo = QtWidgets.QComboBox()
    pipeline_playback_device_combo.setObjectName("pipelinePlaybackDeviceCombo")
    capture_layout.addWidget(pipeline_playback_device_combo, 8, 1)
    purge_after_playback_check = QtWidgets.QCheckBox("Purge items after pipeline playback")
    purge_after_playback_check.setObjectName("purgeAfterPlaybackCheck")
    capture_layout.addWidget(purge_after_playback_check, 9, 0, 1, 2)
    debug_pipeline_check = QtWidgets.QCheckBox("Verbose pipeline debug")
    debug_pipeline_check.setObjectName("debugPipelineCheck")
    capture_layout.addWidget(debug_pipeline_check, 10, 0, 1, 2)
    controls_column.addWidget(capture_group)

    reactive_group = QtWidgets.QGroupBox("Reactive Technical Settings")
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
    reactive_layout.addWidget(QtWidgets.QLabel("Direction"), 6, 0)
    reactive_mirror_combo = QtWidgets.QComboBox()
    reactive_mirror_combo.addItem("Center outward", True)
    reactive_mirror_combo.addItem("Left to right", False)
    reactive_mirror_combo.setObjectName("reactiveMirrorCombo")
    reactive_layout.addWidget(reactive_mirror_combo, 6, 1)
    reactive_layout.addWidget(QtWidgets.QLabel("Master brightness"), 7, 0)
    reactive_master_brightness_spin = QtWidgets.QDoubleSpinBox()
    reactive_master_brightness_spin.setRange(0.05, 1.0)
    reactive_master_brightness_spin.setSingleStep(0.05)
    reactive_master_brightness_spin.setDecimals(2)
    reactive_master_brightness_spin.setValue(1.0)
    reactive_master_brightness_spin.setObjectName("reactiveMasterBrightnessSpin")
    reactive_layout.addWidget(reactive_master_brightness_spin, 7, 1)
    reactive_auto_cycle_check = QtWidgets.QCheckBox("Auto-cycle effects")
    reactive_auto_cycle_check.setChecked(True)
    reactive_auto_cycle_check.setObjectName("reactiveAutoCycleCheck")
    reactive_layout.addWidget(reactive_auto_cycle_check, 8, 0, 1, 2)
    reactive_layout.addWidget(QtWidgets.QLabel("Cycle interval"), 9, 0)
    reactive_cycle_interval_spin = QtWidgets.QDoubleSpinBox()
    reactive_cycle_interval_spin.setRange(1.0, 3600.0)
    reactive_cycle_interval_spin.setValue(16.0)
    reactive_cycle_interval_spin.setObjectName("reactiveCycleIntervalSpin")
    reactive_layout.addWidget(reactive_cycle_interval_spin, 9, 1)
    reactive_debug_mood_check = QtWidgets.QCheckBox("Debug mood/effect transitions")
    reactive_debug_mood_check.setObjectName("reactiveDebugMoodCheck")
    reactive_layout.addWidget(reactive_debug_mood_check, 10, 0, 1, 2)
    reactive_crossfade_check = QtWidgets.QCheckBox("Crossfade boundary detect")
    reactive_crossfade_check.setObjectName("reactiveCrossfadeCheck")
    reactive_layout.addWidget(reactive_crossfade_check, 11, 0, 1, 2)
    reactive_layout.addWidget(QtWidgets.QLabel("Telemetry dir"), 12, 0)
    reactive_telemetry_dir_edit = QtWidgets.QLineEdit()
    reactive_telemetry_dir_edit.setObjectName("reactiveTelemetryDirEdit")
    reactive_layout.addWidget(reactive_telemetry_dir_edit, 12, 1)
    reactive_layout.addWidget(QtWidgets.QLabel("Reactive profile"), 13, 0)
    reactive_profile_strategy_combo = QtWidgets.QComboBox()
    reactive_profile_strategy_combo.addItem("Follow active profile", "active_profile")
    reactive_profile_strategy_combo.addItem("Use one profile", "override_profile")
    reactive_profile_strategy_combo.addItem(
        "Change profiles on song change", "song_change_rotation"
    )
    reactive_profile_strategy_combo.addItem("Rotate profiles on a timer", "profile_rotation")
    reactive_profile_strategy_combo.addItem("Smart profile rotation", "smart_rotation")
    reactive_profile_strategy_combo.setObjectName("reactiveProfileStrategyCombo")
    reactive_profile_strategy_combo.setToolTip(
        "Advanced behavior-profile automation. Follow the active profile, "
        "choose one file, rotate on detected song changes or a timer, or use "
        "smart blended rotation."
    )
    reactive_layout.addWidget(reactive_profile_strategy_combo, 13, 1)
    reactive_layout.addWidget(QtWidgets.QLabel("Selected profile"), 14, 0)
    reactive_profile_picker = QtWidgets.QWidget()
    reactive_profile_picker_layout = QtWidgets.QHBoxLayout(reactive_profile_picker)
    reactive_profile_picker_layout.setContentsMargins(0, 0, 0, 0)
    reactive_profile_override_label = QtWidgets.QLabel("No profile selected")
    reactive_profile_override_label.setObjectName("reactiveProfileOverrideLabel")
    reactive_profile_override_label.setProperty("profilePath", "")
    reactive_profile_override_label.setTextInteractionFlags(
        QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
    )
    reactive_profile_override_label.setWordWrap(True)
    browse_reactive_profile_button = QtWidgets.QPushButton("Choose Profile File…")
    browse_reactive_profile_button.setObjectName("browseReactiveProfileButton")
    browse_reactive_profile_button.setToolTip(
        "Choose a YAML profile from the configured Profile Files Folder."
    )
    reactive_profile_picker_layout.addWidget(reactive_profile_override_label, 1)
    reactive_profile_picker_layout.addWidget(browse_reactive_profile_button)
    reactive_layout.addWidget(reactive_profile_picker, 14, 1)
    reactive_layout.addWidget(QtWidgets.QLabel("Show Palette set"), 15, 0)
    reactive_show_palette_set_combo = QtWidgets.QComboBox()
    reactive_show_palette_set_combo.addItem("Use mood palettes", "")
    reactive_show_palette_set_combo.setObjectName("reactiveShowPaletteSetCombo")
    reactive_show_palette_set_combo.setToolTip(
        "Use one palette from the selected Show Palette set for each detected song."
    )
    reactive_layout.addWidget(reactive_show_palette_set_combo, 15, 1)
    reactive_layout.addWidget(QtWidgets.QLabel("Profile cycle list"), 16, 0)
    reactive_rotation_profiles_picker = QtWidgets.QToolButton()
    reactive_rotation_profiles_picker.setText("Choose profiles\u2026")
    reactive_rotation_profiles_picker.setObjectName(
        "reactiveRotationProfilesPicker"
    )
    reactive_rotation_profiles_picker.setPopupMode(
        QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup
    )
    reactive_rotation_profiles_picker.setToolButtonStyle(
        QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon
    )
    reactive_rotation_profiles_picker.setProperty("selectedProfiles", [])
    reactive_rotation_profiles_picker.setMenu(
        QtWidgets.QMenu(reactive_rotation_profiles_picker)
    )
    reactive_layout.addWidget(reactive_rotation_profiles_picker, 16, 1)
    reactive_configuration_warning_label = QtWidgets.QLabel()
    reactive_configuration_warning_label.setObjectName("reactiveConfigurationWarningLabel")
    reactive_configuration_warning_label.setStyleSheet("color: #dc2626; font-weight: 600;")
    reactive_configuration_warning_label.setWordWrap(True)
    reactive_configuration_warning_label.setVisible(False)
    reactive_layout.addWidget(reactive_configuration_warning_label, 17, 0, 1, 2)
    reactive_layout.addWidget(QtWidgets.QLabel("Timer rotation interval"), 18, 0)
    reactive_rotation_interval_spin = QtWidgets.QDoubleSpinBox()
    reactive_rotation_interval_spin.setRange(1.0, 86400.0)
    reactive_rotation_interval_spin.setValue(300.0)
    reactive_rotation_interval_spin.setObjectName("reactiveRotationIntervalSpin")
    reactive_layout.addWidget(reactive_rotation_interval_spin, 18, 1)
    reactive_auto_palette_check = QtWidgets.QCheckBox("Auto palette chaining")
    reactive_auto_palette_check.setObjectName("reactiveAutoPaletteCheck")
    reactive_layout.addWidget(reactive_auto_palette_check, 19, 0, 1, 2)
    reactive_smart_rotation_check = QtWidgets.QCheckBox("Smart rotation")
    reactive_smart_rotation_check.setObjectName("reactiveSmartRotationCheck")
    reactive_layout.addWidget(reactive_smart_rotation_check, 20, 0, 1, 2)
    reactive_layout.addWidget(QtWidgets.QLabel("Chain blend (s)"), 21, 0)
    reactive_chain_blend_spin = QtWidgets.QDoubleSpinBox()
    reactive_chain_blend_spin.setRange(0.5, 600.0)
    reactive_chain_blend_spin.setValue(8.0)
    reactive_chain_blend_spin.setObjectName("reactiveChainBlendSpin")
    reactive_layout.addWidget(reactive_chain_blend_spin, 21, 1)
    reactive_auto_palette_seed_check = QtWidgets.QCheckBox("Deterministic auto-palette seed")
    reactive_auto_palette_seed_check.setObjectName("reactiveAutoPaletteSeedCheck")
    reactive_auto_palette_seed_spin = QtWidgets.QSpinBox()
    reactive_auto_palette_seed_spin.setRange(-2147483647, 2147483647)
    reactive_auto_palette_seed_spin.setObjectName("reactiveAutoPaletteSeedSpin")
    reactive_auto_palette_seed_spin.setEnabled(False)
    reactive_auto_palette_seed_check.toggled.connect(reactive_auto_palette_seed_spin.setEnabled)
    seed_widget = QtWidgets.QWidget()
    seed_row = QtWidgets.QHBoxLayout(seed_widget)
    seed_row.setContentsMargins(0, 0, 0, 0)
    seed_row.addWidget(reactive_auto_palette_seed_check)
    seed_row.addWidget(reactive_auto_palette_seed_spin)
    reactive_layout.addWidget(seed_widget, 22, 0, 1, 2)
    reactive_layout.addWidget(QtWidgets.QLabel("Auto-palette pool size"), 23, 0)
    reactive_auto_palette_pool_size_spin = QtWidgets.QSpinBox()
    reactive_auto_palette_pool_size_spin.setRange(2, 32)
    reactive_auto_palette_pool_size_spin.setValue(8)
    reactive_auto_palette_pool_size_spin.setObjectName("reactiveAutoPalettePoolSizeSpin")
    reactive_layout.addWidget(reactive_auto_palette_pool_size_spin, 23, 1)
    reactive_chain_dwell_range_check = QtWidgets.QCheckBox("Use chain dwell range")
    reactive_chain_dwell_range_check.setObjectName("reactiveChainDwellRangeCheck")
    reactive_layout.addWidget(reactive_chain_dwell_range_check, 24, 0)
    dwell_widget = QtWidgets.QWidget()
    dwell_row = QtWidgets.QHBoxLayout(dwell_widget)
    dwell_row.setContentsMargins(0, 0, 0, 0)
    reactive_chain_min_dwell_spin = QtWidgets.QDoubleSpinBox()
    reactive_chain_max_dwell_spin = QtWidgets.QDoubleSpinBox()
    for spin, name, value in (
        (reactive_chain_min_dwell_spin, "reactiveChainMinDwellSpin", 60.0),
        (reactive_chain_max_dwell_spin, "reactiveChainMaxDwellSpin", 180.0),
    ):
        spin.setRange(1.0, 86400.0)
        spin.setValue(value)
        spin.setSuffix(" s")
        spin.setObjectName(name)
    dwell_row.addWidget(reactive_chain_min_dwell_spin)
    dwell_row.addWidget(QtWidgets.QLabel("to"))
    dwell_row.addWidget(reactive_chain_max_dwell_spin)
    reactive_layout.addWidget(dwell_widget, 24, 1)
    preview_profile_chain_button = QtWidgets.QPushButton("Preview Chain Cross-fade (Simulation)")
    preview_profile_chain_button.setObjectName("previewProfileChainButton")
    reactive_layout.addWidget(preview_profile_chain_button, 25, 0, 1, 2)
    reactive_harmonic_structure_check = QtWidgets.QCheckBox(
        "Live harmonic structure (analysis-only input)"
    )
    reactive_harmonic_structure_check.setObjectName(
        "reactiveHarmonicStructureCheck"
    )
    reactive_harmonic_structure_check.setToolTip(
        "Detect confident downbeats, chord changes, and four-bar macro "
        "changes from the live input. Reactive mode never outputs audio."
    )
    reactive_layout.addWidget(
        reactive_harmonic_structure_check,
        26,
        0,
        1,
        2,
    )
    reactive_layout.addWidget(QtWidgets.QLabel("Structure sensitivity"), 27, 0)
    reactive_structure_sensitivity_spin = QtWidgets.QDoubleSpinBox()
    reactive_structure_sensitivity_spin.setRange(0.0, 1.0)
    reactive_structure_sensitivity_spin.setSingleStep(0.05)
    reactive_structure_sensitivity_spin.setDecimals(2)
    reactive_structure_sensitivity_spin.setValue(0.5)
    reactive_structure_sensitivity_spin.setObjectName(
        "reactiveStructureSensitivitySpin"
    )
    reactive_layout.addWidget(reactive_structure_sensitivity_spin, 27, 1)
    reactive_debug_harmonics_check = QtWidgets.QCheckBox(
        "Show live FFT + chord wheel debug panel"
    )
    reactive_debug_harmonics_check.setObjectName(
        "reactiveDebugHarmonicsCheck"
    )
    reactive_debug_harmonics_check.setToolTip(
        "Adds a 10 Hz diagnostic panel for the live FFT, all 12 pitch "
        "classes, assumed triad, and chord confidence. Analysis remains "
        "capture-only and does not affect lighting decisions."
    )
    reactive_layout.addWidget(
        reactive_debug_harmonics_check,
        28,
        0,
        1,
        2,
    )
    reactive_predictive_analysis_check = QtWidgets.QCheckBox(
        "Structure similarity analysis"
    )
    reactive_predictive_analysis_check.setObjectName(
        "reactivePredictiveAnalysisCheck"
    )
    reactive_predictive_analysis_check.setToolTip(
        "Learn causal bar, phrase, and anonymous section structure from "
        "multi-feature similarity. Chord labels are optional diagnostics."
    )
    reactive_layout.addWidget(
        reactive_predictive_analysis_check,
        29,
        0,
        1,
        2,
    )
    reactive_predictive_diagnostics_check = QtWidgets.QCheckBox(
        "Log/show structure similarity evidence"
    )
    reactive_predictive_diagnostics_check.setObjectName(
        "reactivePredictiveDiagnosticsCheck"
    )
    reactive_layout.addWidget(
        reactive_predictive_diagnostics_check,
        30,
        0,
        1,
        2,
    )
    reactive_predictive_shadow_check = QtWidgets.QCheckBox(
        "Structure shadow mode (no optical actions)"
    )
    reactive_predictive_shadow_check.setObjectName(
        "reactivePredictiveShadowCheck"
    )
    reactive_predictive_shadow_check.setChecked(True)
    reactive_layout.addWidget(
        reactive_predictive_shadow_check,
        31,
        0,
        1,
        2,
    )
    reactive_predictive_cues_check = QtWidgets.QCheckBox(
        "Enable ordinary bar actions"
    )
    reactive_predictive_cues_check.setObjectName(
        "reactivePredictiveCuesCheck"
    )
    reactive_predictive_cues_check.setToolTip(
        "Allow restrained bar markers only on matching confident downbeats."
    )
    reactive_layout.addWidget(
        reactive_predictive_cues_check,
        32,
        0,
        1,
        2,
    )
    reactive_structure_phrase_actions_check = QtWidgets.QCheckBox(
        "Enable phrase-boundary actions"
    )
    reactive_structure_phrase_actions_check.setObjectName(
        "reactiveStructurePhraseActionsCheck"
    )
    reactive_structure_phrase_actions_check.setToolTip(
        "Allow medium phrase resets and palette movement on predicted downbeats."
    )
    reactive_layout.addWidget(
        reactive_structure_phrase_actions_check,
        33,
        0,
        1,
        2,
    )
    reactive_predictive_high_impact_check = QtWidgets.QCheckBox(
        "Enable section entrance/return actions"
    )
    reactive_predictive_high_impact_check.setObjectName(
        "reactivePredictiveHighImpactCheck"
    )
    reactive_layout.addWidget(
        reactive_predictive_high_impact_check,
        34,
        0,
        1,
        2,
    )
    # Operator-facing look, profile, and structural action controls live on
    # the Reactive Live screen. Keep only capture/analysis infrastructure in
    # Config, while retaining these widget instances so existing persistence
    # keys and signal bindings remain compatible.
    for row in (0, 6, 7, 9, 13, 14, 15, 16, 18, 21):
        item = reactive_layout.itemAtPosition(row, 0)
        if item is not None and item.widget() is not None:
            item.widget().setVisible(False)
    reactive_render_mode_combo.setVisible(False)
    reactive_half_time_check.setVisible(False)
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
    runtime_override_layout.addWidget(QtWidgets.QLabel("Disabled groups"), 9, 0)
    runtime_disabled_groups_edit = QtWidgets.QLineEdit()
    runtime_disabled_groups_edit.setPlaceholderText("group-a, top")
    runtime_disabled_groups_edit.setObjectName("runtimeDisabledGroupsEdit")
    runtime_override_layout.addWidget(runtime_disabled_groups_edit, 9, 1)
    runtime_override_layout.addWidget(QtWidgets.QLabel("Force-enabled groups"), 10, 0)
    runtime_enabled_groups_edit = QtWidgets.QLineEdit()
    runtime_enabled_groups_edit.setPlaceholderText("group-b")
    runtime_enabled_groups_edit.setObjectName("runtimeEnabledGroupsEdit")
    runtime_override_layout.addWidget(runtime_enabled_groups_edit, 10, 1)
    runtime_override_layout.addWidget(QtWidgets.QLabel("Solo groups"), 11, 0)
    runtime_solo_groups_edit = QtWidgets.QLineEdit()
    runtime_solo_groups_edit.setPlaceholderText("left")
    runtime_solo_groups_edit.setObjectName("runtimeSoloGroupsEdit")
    runtime_override_layout.addWidget(runtime_solo_groups_edit, 11, 1)
    runtime_buttons = QtWidgets.QHBoxLayout()
    runtime_apply_button = QtWidgets.QPushButton("Apply Override")
    runtime_apply_button.setObjectName("applyRuntimeControlButton")
    runtime_clear_button = QtWidgets.QPushButton("Clear Override")
    runtime_clear_button.setObjectName("clearRuntimeControlButton")
    runtime_buttons.addWidget(runtime_apply_button)
    runtime_buttons.addWidget(runtime_clear_button)
    runtime_override_layout.addLayout(runtime_buttons, 12, 0, 1, 2)
    runtime_control_status_label = QtWidgets.QLabel("Live overrides are temporary and session-local.")
    runtime_control_status_label.setWordWrap(True)
    runtime_control_status_label.setObjectName("runtimeControlStatusLabel")
    runtime_override_layout.addWidget(runtime_control_status_label, 13, 0, 1, 2)
    controls_column.addWidget(runtime_override_group)
    controls_column.addStretch(1)

    queue_splitter = QtWidgets.QSplitter()
    queue_splitter.setOrientation(QtCore.Qt.Orientation.Horizontal)
    queue_layout.addWidget(queue_splitter, 1)

    queue_left_panel = QtWidgets.QWidget()
    queue_left_layout = QtWidgets.QVBoxLayout(queue_left_panel)
    reactive_live_group = QtWidgets.QGroupBox("Reactive Live")
    reactive_live_group.setObjectName("reactiveLiveGroup")
    reactive_live_layout = QtWidgets.QVBoxLayout(reactive_live_group)
    reactive_live_scroll = QtWidgets.QScrollArea()
    reactive_live_scroll.setObjectName("reactiveLiveScrollArea")
    reactive_live_scroll.setWidgetResizable(True)
    reactive_live_scroll.setHorizontalScrollBarPolicy(
        QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    reactive_live_scroll.setVerticalScrollBarPolicy(
        QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
    )
    reactive_live_scroll.setFrameShape(
        QtWidgets.QFrame.Shape.NoFrame
    )
    reactive_live_content = QtWidgets.QWidget()
    reactive_live_content.setObjectName("reactiveLiveScrollContent")
    reactive_live_content_layout = QtWidgets.QVBoxLayout(
        reactive_live_content
    )
    reactive_live_content_layout.setContentsMargins(0, 0, 0, 0)
    reactive_live_content_layout.setSpacing(8)
    reactive_live_content_layout.setAlignment(
        QtCore.Qt.AlignmentFlag.AlignTop
    )
    reactive_live_scroll.setWidget(reactive_live_content)
    reactive_live_layout.addWidget(reactive_live_scroll, 1)

    def _reactive_collapsible_panel(
        group,
        *,
        title: str,
        object_name: str,
        expanded: bool,
    ):
        """Wrap a live section in the Palettes-style collapsible panel."""

        panel = QtWidgets.QWidget()
        panel.setObjectName(object_name)
        panel.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Maximum,
        )
        panel_layout = QtWidgets.QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(2)
        header = QtWidgets.QToolButton()
        header.setObjectName(f"{object_name}Header")
        header.setText(title)
        header.setCheckable(True)
        header.setChecked(bool(expanded))
        header.setToolButtonStyle(
            QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        header.setArrowType(
            QtCore.Qt.ArrowType.DownArrow
            if expanded
            else QtCore.Qt.ArrowType.RightArrow
        )
        header.setStyleSheet(
            "QToolButton { font-weight: 600; text-align: left; "
            "padding: 4px; }"
        )
        group.setTitle("")
        group.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Maximum,
        )
        group.setVisible(bool(expanded))

        def _toggle(checked: bool) -> None:
            group.setVisible(bool(checked))
            header.setArrowType(
                QtCore.Qt.ArrowType.DownArrow
                if checked
                else QtCore.Qt.ArrowType.RightArrow
            )

        header.toggled.connect(_toggle)
        panel_layout.addWidget(header)
        panel_layout.addWidget(group)
        return panel

    reactive_status_group = QtWidgets.QGroupBox(
        "Session + Palette Status"
    )
    reactive_status_group.setObjectName("reactiveStatusGroup")
    reactive_status_layout = QtWidgets.QVBoxLayout(
        reactive_status_group
    )
    reactive_mode_status_label = QtWidgets.QLabel(
        "Reactive mode is cued. Start Reactive or press Space to begin listening."
    )
    reactive_mode_status_label.setObjectName("reactiveModeStatusLabel")
    reactive_mode_status_label.setWordWrap(True)
    reactive_status_layout.addWidget(reactive_mode_status_label)
    reactive_profile_label = QtWidgets.QLabel(
        "Behavior profile queued: follows the active profile"
    )
    reactive_profile_label.setObjectName("reactiveProfileLabel")
    reactive_profile_label.setStyleSheet("color: #94a3b8;")
    reactive_profile_label.setWordWrap(True)
    reactive_status_layout.addWidget(reactive_profile_label)
    reactive_active_palette_label = QtWidgets.QLabel("Active palette: awaiting Reactive session")
    reactive_active_palette_label.setObjectName("reactiveActivePaletteLabel")
    reactive_active_palette_label.setStyleSheet("color: #94a3b8;")
    reactive_active_palette_label.setWordWrap(True)
    reactive_status_layout.addWidget(reactive_active_palette_label)
    reactive_active_palette_preview_label = QtWidgets.QLabel("No active colors")
    reactive_active_palette_preview_label.setObjectName(
        "reactiveActivePalettePreviewLabel"
    )
    reactive_active_palette_preview_label.setMinimumHeight(26)
    reactive_active_palette_preview_label.setToolTip(
        "Colors currently applied to Reactive output"
    )
    reactive_status_layout.addWidget(
        reactive_active_palette_preview_label
    )
    reactive_palette_next_label = QtWidgets.QLabel("Time to next palette: —")
    reactive_palette_next_label.setObjectName("reactivePaletteNextLabel")
    reactive_palette_next_label.setStyleSheet("color: #94a3b8;")
    reactive_status_layout.addWidget(reactive_palette_next_label)
    reactive_palette_queue_label = QtWidgets.QLabel("Palette queue: —")
    reactive_palette_queue_label.setObjectName("reactivePaletteQueueLabel")
    reactive_palette_queue_label.setStyleSheet("color: #94a3b8;")
    reactive_palette_queue_label.setWordWrap(True)
    reactive_status_layout.addWidget(reactive_palette_queue_label)
    reactive_live_content_layout.addWidget(
        _reactive_collapsible_panel(
            reactive_status_group,
            title="Session + Palette Status",
            object_name="reactiveStatusPanel",
            expanded=True,
        )
    )

    reactive_live_look_group = QtWidgets.QGroupBox("Color + Effects")
    reactive_live_look_group.setObjectName("reactiveLiveLookGroup")
    reactive_live_look_layout = QtWidgets.QGridLayout(
        reactive_live_look_group
    )
    reactive_live_look_layout.addWidget(
        QtWidgets.QLabel("Color profile"),
        0,
        0,
    )
    reactive_live_color_profile_combo = QtWidgets.QComboBox()
    reactive_live_color_profile_combo.setObjectName(
        "reactiveLiveColorProfileCombo"
    )
    reactive_live_color_profile_combo.addItem(
        "Follow active profile",
        "",
    )
    for palette_name in (
        "warm",
        "cool",
        "sunset",
        "vivid",
        "pastel",
        "neon",
        "fire",
        "ice",
    ):
        reactive_live_color_profile_combo.addItem(
            palette_name.replace("_", " ").title(),
            palette_name,
        )
    reactive_live_color_profile_combo.setToolTip(
        "Hot-swap the live color palette. This affects Reactive output "
        "only and does not alter saved or precompiled shows."
    )
    reactive_live_look_layout.addWidget(
        reactive_live_color_profile_combo,
        0,
        1,
    )
    reactive_live_look_layout.addWidget(
        QtWidgets.QLabel("Active effect"),
        0,
        2,
    )
    reactive_live_active_effect_combo = QtWidgets.QComboBox()
    reactive_live_active_effect_combo.setObjectName(
        "reactiveLiveActiveEffectCombo"
    )
    reactive_live_active_effect_combo.addItem(
        "Auto: mood + section boundaries",
        "",
    )
    effect_options = (
        ("Flash", "pulse"),
        ("Wave", "wave"),
        ("Ripple", "ripple"),
        ("Scroll", "scroll"),
        ("Breathe", "breathe"),
        ("Gradient", "gradient"),
        ("Solid", "solid"),
    )
    for effect_label, effect_mode in effect_options:
        reactive_live_active_effect_combo.addItem(
            effect_label,
            effect_mode,
        )
    reactive_live_active_effect_combo.setToolTip(
        "Force one live effect, or let the mood and structure detectors choose "
        "from the enabled bank at natural section boundaries."
    )
    reactive_live_look_layout.addWidget(
        reactive_live_active_effect_combo,
        0,
        3,
    )
    reactive_live_look_layout.addWidget(
        QtWidgets.QLabel("Enabled effects"),
        1,
        0,
    )
    reactive_live_effect_row = QtWidgets.QGridLayout()
    reactive_live_effect_row.setHorizontalSpacing(6)
    reactive_live_effect_row.setVerticalSpacing(4)
    reactive_live_effect_buttons = []
    for effect_index, (effect_label, effect_mode) in enumerate(
        effect_options
    ):
        effect_button = QtWidgets.QToolButton()
        effect_button.setText(effect_label)
        effect_button.setCheckable(True)
        effect_button.setChecked(True)
        effect_button.setProperty("effectMode", effect_mode)
        effect_button.setObjectName(
            f"reactiveLiveEffect{effect_mode.title()}Button"
        )
        effect_button.setToolTip(
            f"Allow {effect_label} when the active effect is automatic."
        )
        reactive_live_effect_row.addWidget(
            effect_button,
            effect_index // 4,
            effect_index % 4,
        )
        reactive_live_effect_buttons.append(effect_button)
    reactive_live_look_layout.addLayout(
        reactive_live_effect_row,
        1,
        1,
        1,
        3,
    )
    reactive_live_look_layout.addWidget(
        QtWidgets.QLabel("Effect speed"),
        2,
        0,
    )
    reactive_live_effect_speed_combo = QtWidgets.QComboBox()
    reactive_live_effect_speed_combo.setObjectName(
        "reactiveLiveEffectSpeedCombo"
    )
    for speed_label, speed_value in (
        ("Auto: detected energy", "auto"),
        ("1 beat", "1"),
        ("2 beats", "2"),
        ("4 beats", "4"),
        ("8 beats", "8"),
        ("Random per effect", "random"),
    ):
        reactive_live_effect_speed_combo.addItem(
            speed_label,
            speed_value,
        )
    reactive_live_effect_speed_combo.setToolTip(
        "Set one complete effect cycle or pulse decay in detected beats. "
        "Auto maps detected energy and beat stability to a musical duration. "
        "Random chooses 1, 2, 4, or 8 beats when an effect activates."
    )
    reactive_live_look_layout.addWidget(
        reactive_live_effect_speed_combo,
        2,
        1,
    )
    reactive_live_look_layout.addWidget(
        QtWidgets.QLabel("Global origin"),
        2,
        2,
    )
    reactive_live_effect_origin_combo = QtWidgets.QComboBox()
    reactive_live_effect_origin_combo.setObjectName(
        "reactiveLiveEffectOriginCombo"
    )
    for origin_label, origin_value in (
        ("Auto: effect + section", "auto"),
        ("Center", "center"),
        ("Left", "left"),
        ("Outer", "outer"),
        ("Right", "right"),
        ("Top", "top"),
        ("Bottom", "bottom"),
        ("Back", "back"),
        ("Front", "front"),
        ("Random per effect", "random"),
    ):
        reactive_live_effect_origin_combo.addItem(
            origin_label,
            origin_value,
        )
    reactive_live_effect_origin_combo.setToolTip(
        "Project every effect as a radial pattern from this room origin. "
        "Auto chooses a direction suited to the active effect and changes it "
        "only with detected sections. "
        "Outer begins at the room boundary and moves inward. Random chooses "
        "a stable origin whenever an effect activates."
    )
    reactive_live_look_layout.addWidget(
        reactive_live_effect_origin_combo,
        2,
        3,
    )
    reactive_live_look_status_label = QtWidgets.QLabel(
        "Selections apply immediately while Reactive is listening."
    )
    reactive_live_look_status_label.setObjectName(
        "reactiveLiveLookStatusLabel"
    )
    reactive_live_look_status_label.setStyleSheet("color: #94a3b8;")
    reactive_live_look_status_label.setWordWrap(True)
    reactive_live_look_layout.addWidget(
        reactive_live_look_status_label,
        3,
        0,
        1,
        4,
    )
    reactive_live_settings_group = QtWidgets.QGroupBox(
        "Reactive Live Settings"
    )
    reactive_live_settings_group.setObjectName(
        "reactiveLiveSettingsGroup"
    )
    reactive_live_settings_layout = QtWidgets.QVBoxLayout(
        reactive_live_settings_group
    )
    reactive_live_settings_layout.addWidget(reactive_live_look_group)

    reactive_live_output_group = QtWidgets.QGroupBox(
        "Output"
    )
    reactive_live_output_layout = QtWidgets.QGridLayout(
        reactive_live_output_group
    )
    reactive_live_output_layout.addWidget(
        reactive_max_brightness_check,
        0,
        0,
    )
    reactive_live_output_layout.addWidget(
        QtWidgets.QLabel("Master brightness"),
        0,
        1,
    )
    reactive_live_output_layout.addWidget(
        reactive_master_brightness_spin,
        0,
        2,
    )
    # These legacy widgets remain available to settings migration/tests, but
    # no longer appear as competing live policies. Automatic effect changes
    # are selected by the Active effect control and occur at structure
    # boundaries; Global origin owns spatial direction.
    reactive_auto_cycle_check.setVisible(False)
    reactive_cycle_interval_spin.setVisible(False)
    reactive_mirror_combo.setVisible(False)
    reactive_live_settings_layout.addWidget(
        reactive_live_output_group
    )

    reactive_live_profile_group = QtWidgets.QGroupBox("Palette Source + Rotation")
    reactive_live_profile_group.setObjectName("reactivePaletteSourceGroup")
    reactive_live_profile_layout = QtWidgets.QGridLayout(
        reactive_live_profile_group
    )
    reactive_live_profile_layout.addWidget(
        QtWidgets.QLabel("Palette pool"),
        0,
        0,
    )
    reactive_live_profile_layout.addWidget(
        reactive_show_palette_set_combo,
        0,
        1,
    )
    reactive_palette_rotation_label = QtWidgets.QLabel(
        "Mood palettes from the active profile"
    )
    reactive_palette_rotation_label.setObjectName(
        "reactivePaletteRotationLabel"
    )
    reactive_palette_rotation_label.setWordWrap(True)
    reactive_palette_rotation_label.setTextInteractionFlags(
        QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
    )
    reactive_live_profile_layout.addWidget(
        QtWidgets.QLabel("Palettes in rotation"),
        1,
        0,
    )
    reactive_live_profile_layout.addWidget(
        reactive_palette_rotation_label,
        1,
        1,
    )
    reactive_palette_rotation_help = QtWidgets.QLabel(
        "Show Palette sets advance at detected song boundaries. "
        "Mood palettes follow the active mood and effect."
    )
    reactive_palette_rotation_help.setObjectName(
        "reactivePaletteRotationHelpLabel"
    )
    reactive_palette_rotation_help.setWordWrap(True)
    reactive_palette_rotation_help.setStyleSheet("color: #94a3b8;")
    reactive_live_profile_layout.addWidget(
        reactive_palette_rotation_help,
        2,
        0,
        1,
        2,
    )
    reactive_live_settings_layout.addWidget(reactive_live_profile_group)

    reactive_profile_automation_group = QtWidgets.QGroupBox(
        "Advanced Profile Automation"
    )
    reactive_profile_automation_group.setObjectName(
        "reactiveProfileAutomationGroup"
    )
    reactive_profile_automation_layout = QtWidgets.QGridLayout(
        reactive_profile_automation_group
    )
    reactive_profile_automation_layout.addWidget(
        QtWidgets.QLabel("Profile automation"),
        0,
        0,
    )
    reactive_profile_automation_layout.addWidget(
        reactive_profile_strategy_combo,
        0,
        1,
    )
    reactive_profile_picker_label = QtWidgets.QLabel("Profile file")
    reactive_profile_picker_label.setObjectName(
        "reactiveProfilePickerLabel"
    )
    reactive_profile_automation_layout.addWidget(
        reactive_profile_picker_label,
        1,
        0,
    )
    reactive_profile_automation_layout.addWidget(
        reactive_profile_picker,
        1,
        1,
    )
    reactive_rotation_profiles_label = QtWidgets.QLabel(
        "Profiles in automation"
    )
    reactive_rotation_profiles_label.setObjectName(
        "reactiveRotationProfilesLabel"
    )
    reactive_profile_automation_layout.addWidget(
        reactive_rotation_profiles_label,
        2,
        0,
    )
    reactive_profile_automation_layout.addWidget(
        reactive_rotation_profiles_picker,
        2,
        1,
    )
    reactive_rotation_interval_label = QtWidgets.QLabel("Rotation interval")
    reactive_rotation_interval_label.setObjectName(
        "reactiveRotationIntervalLabel"
    )
    reactive_profile_automation_layout.addWidget(
        reactive_rotation_interval_label,
        3,
        0,
    )
    reactive_profile_automation_layout.addWidget(
        reactive_rotation_interval_spin,
        3,
        1,
    )
    reactive_auto_palette_check.setText("Generate the smart profile pool")
    reactive_profile_automation_layout.addWidget(
        reactive_auto_palette_check,
        4,
        0,
        1,
        2,
    )
    # Kept for settings migration only. The strategy selector is now the
    # single authority for smart profile rotation.
    reactive_smart_rotation_check.setVisible(False)
    reactive_chain_blend_label = QtWidgets.QLabel("Chain blend")
    reactive_chain_blend_label.setObjectName("reactiveChainBlendLabel")
    reactive_profile_automation_layout.addWidget(
        reactive_chain_blend_label,
        5,
        0,
    )
    reactive_profile_automation_layout.addWidget(
        reactive_chain_blend_spin,
        5,
        1,
    )
    seed_widget.setObjectName("reactiveAutoPaletteSeedWidget")
    reactive_profile_automation_layout.addWidget(
        seed_widget,
        6,
        0,
        1,
        2,
    )
    reactive_auto_palette_pool_size_label = QtWidgets.QLabel(
        "Generated profile count"
    )
    reactive_auto_palette_pool_size_label.setObjectName(
        "reactiveAutoPalettePoolSizeLabel"
    )
    reactive_profile_automation_layout.addWidget(
        reactive_auto_palette_pool_size_label,
        7,
        0,
    )
    reactive_profile_automation_layout.addWidget(
        reactive_auto_palette_pool_size_spin,
        7,
        1,
    )
    reactive_chain_dwell_range_check.setVisible(False)
    reactive_chain_dwell_label = QtWidgets.QLabel("Dwell range")
    reactive_chain_dwell_label.setObjectName("reactiveChainDwellLabel")
    reactive_profile_automation_layout.addWidget(
        reactive_chain_dwell_label,
        8,
        0,
    )
    dwell_widget.setObjectName("reactiveChainDwellWidget")
    reactive_profile_automation_layout.addWidget(
        dwell_widget,
        8,
        1,
    )
    reactive_profile_automation_layout.addWidget(
        preview_profile_chain_button,
        9,
        0,
        1,
        2,
    )
    reactive_profile_automation_layout.addWidget(
        reactive_configuration_warning_label,
        10,
        0,
        1,
        2,
    )
    reactive_live_settings_layout.addWidget(
        _reactive_collapsible_panel(
            reactive_profile_automation_group,
            title="Advanced Profile Automation",
            object_name="reactiveProfileAutomationPanel",
            expanded=False,
        )
    )

    reactive_live_structure_group = QtWidgets.QGroupBox(
        "Structure Detection + Actions"
    )
    reactive_live_structure_group.setObjectName(
        "reactiveLiveStructureSettingsGroup"
    )
    reactive_live_structure_layout = QtWidgets.QVBoxLayout(
        reactive_live_structure_group
    )
    for control in (
        reactive_harmonic_structure_check,
        reactive_predictive_analysis_check,
        reactive_predictive_shadow_check,
        reactive_predictive_cues_check,
        reactive_structure_phrase_actions_check,
        reactive_predictive_high_impact_check,
    ):
        reactive_live_structure_layout.addWidget(control)
    reactive_live_settings_layout.addWidget(
        reactive_live_structure_group
    )
    reactive_live_content_layout.addWidget(
        _reactive_collapsible_panel(
            reactive_live_settings_group,
            title="Reactive Live Settings",
            object_name="reactiveSettingsPanel",
            expanded=True,
        )
    )

    reactive_transport_group = QtWidgets.QGroupBox(
        "Beat + Tempo Controls"
    )
    reactive_transport_group.setObjectName("reactiveTransportGroup")
    reactive_transport_layout = QtWidgets.QVBoxLayout(
        reactive_transport_group
    )
    reactive_input_row = QtWidgets.QHBoxLayout()
    reactive_listening_label = QtWidgets.QLabel("○ Input not listening")
    reactive_listening_label.setObjectName("reactiveListeningLabel")
    reactive_listening_label.setStyleSheet("color: #94a3b8; font-weight: 600;")
    reactive_beat_indicator_label = QtWidgets.QLabel("●")
    reactive_beat_indicator_label.setObjectName("reactiveBeatIndicator")
    reactive_beat_indicator_label.setToolTip("Beat detector: waiting for the refined BPM detector.")
    reactive_beat_indicator_label.setStyleSheet("color: #475569; font-size: 22px;")
    reactive_bpm_label = QtWidgets.QLabel("Beat detector: waiting for audio")
    reactive_bpm_label.setObjectName("reactiveBpmLabel")
    reactive_input_row.addWidget(reactive_listening_label)
    reactive_input_row.addStretch(1)
    reactive_input_row.addWidget(reactive_beat_indicator_label)
    reactive_input_row.addWidget(reactive_bpm_label)
    reactive_transport_layout.addLayout(reactive_input_row)
    reactive_cycle_tempo_row = QtWidgets.QHBoxLayout()
    reactive_cycle_tempo_label = QtWidgets.QLabel(
        "Cycle tempo: 1× detector  {  }"
    )
    reactive_cycle_tempo_label.setObjectName(
        "reactiveCycleTempoLabel"
    )
    reactive_cycle_tempo_label.setToolTip(
        "Force the beat and meter cycle to half-time with { or double-time "
        "with }. This is independent of effect speed."
    )
    reactive_cycle_tempo_row.addWidget(
        reactive_cycle_tempo_label,
        1,
    )
    reactive_cycle_tempo_half_button = QtWidgets.QPushButton(
        "½×  {"
    )
    reactive_cycle_tempo_half_button.setObjectName(
        "reactiveCycleTempoHalfButton"
    )
    reactive_cycle_tempo_half_button.setToolTip(
        "Force the detector cycle to half-time. Hotkey: Shift+[ ({)"
    )
    reactive_cycle_tempo_row.addWidget(
        reactive_cycle_tempo_half_button
    )
    reactive_cycle_tempo_normal_button = QtWidgets.QPushButton("1×")
    reactive_cycle_tempo_normal_button.setObjectName(
        "reactiveCycleTempoNormalButton"
    )
    reactive_cycle_tempo_normal_button.setToolTip(
        "Return the detector cycle to its normal detected tempo."
    )
    reactive_cycle_tempo_row.addWidget(
        reactive_cycle_tempo_normal_button
    )
    reactive_cycle_tempo_double_button = QtWidgets.QPushButton(
        "2×  }"
    )
    reactive_cycle_tempo_double_button.setObjectName(
        "reactiveCycleTempoDoubleButton"
    )
    reactive_cycle_tempo_double_button.setToolTip(
        "Force the detector cycle to double-time. Hotkey: Shift+] (})"
    )
    reactive_cycle_tempo_row.addWidget(
        reactive_cycle_tempo_double_button
    )
    reactive_transport_layout.addLayout(reactive_cycle_tempo_row)
    reactive_effect_tempo_row = QtWidgets.QHBoxLayout()
    reactive_effect_tempo_label = QtWidgets.QLabel(
        "Effect tempo: 1× cycle BPM"
    )
    reactive_effect_tempo_label.setObjectName(
        "reactiveEffectTempoLabel"
    )
    reactive_effect_tempo_label.setStyleSheet(
        "color: #93c5fd; font-weight: 600;"
    )
    reactive_effect_tempo_row.addWidget(
        reactive_effect_tempo_label,
        1,
    )
    reactive_effect_tempo_half_button = QtWidgets.QPushButton(
        "½×  ["
    )
    reactive_effect_tempo_half_button.setObjectName(
        "reactiveEffectTempoHalfButton"
    )
    reactive_effect_tempo_half_button.setToolTip(
        "Run live effects at half the current cycle BPM. Hotkey: ["
    )
    reactive_effect_tempo_row.addWidget(
        reactive_effect_tempo_half_button
    )
    reactive_effect_tempo_normal_button = QtWidgets.QPushButton(
        "1×  \\"
    )
    reactive_effect_tempo_normal_button.setObjectName(
        "reactiveEffectTempoNormalButton"
    )
    reactive_effect_tempo_normal_button.setToolTip(
        "Run live effects at the current cycle BPM. Hotkey: \\"
    )
    reactive_effect_tempo_row.addWidget(
        reactive_effect_tempo_normal_button
    )
    reactive_effect_tempo_double_button = QtWidgets.QPushButton(
        "2×  ]"
    )
    reactive_effect_tempo_double_button.setObjectName(
        "reactiveEffectTempoDoubleButton"
    )
    reactive_effect_tempo_double_button.setToolTip(
        "Run live effects at double the current cycle BPM. Hotkey: ]"
    )
    reactive_effect_tempo_row.addWidget(
        reactive_effect_tempo_double_button
    )
    # Beat-count speed is now controlled explicitly in Live Color + Effect
    # Bank. Keep these legacy objects available to older integrations without
    # presenting a second, conflicting speed control.
    for legacy_effect_tempo_control in (
        reactive_effect_tempo_label,
        reactive_effect_tempo_half_button,
        reactive_effect_tempo_normal_button,
        reactive_effect_tempo_double_button,
    ):
        legacy_effect_tempo_control.setVisible(False)
    reactive_downbeat_nudge_button = QtWidgets.QPushButton(
        "Downbeat nearest  D"
    )
    reactive_downbeat_nudge_button.setObjectName(
        "reactiveDownbeatNudgeButton"
    )
    reactive_downbeat_nudge_button.setToolTip(
        "Make the beat nearest the keypress bar phase 0 without changing BPM "
        "or meter. The previous beat is used during the first half of an "
        "interval; otherwise the next beat is used. Learned structure and "
        "pending cues are preserved. Use D for downbeats and S for "
        "other beats; two D markers infer the meter (for example D S S S D "
        "sets 4/4). Press N to clear beat history and restart BPM/meter "
        "detection for a new song. Hotkeys: D, S, and N"
    )
    reactive_effect_tempo_row.addWidget(
        reactive_downbeat_nudge_button
    )
    reactive_transport_layout.addLayout(reactive_effect_tempo_row)
    reactive_live_content_layout.addWidget(
        _reactive_collapsible_panel(
            reactive_transport_group,
            title="Beat + Tempo Controls",
            object_name="reactiveTransportPanel",
            expanded=True,
        )
    )

    reactive_diagnostics_group = QtWidgets.QGroupBox(
        "Live Diagnostics"
    )
    reactive_diagnostics_group.setObjectName(
        "reactiveLiveDiagnosticsGroup"
    )
    reactive_diagnostics_layout = QtWidgets.QVBoxLayout(
        reactive_diagnostics_group
    )
    reactive_panel_row = QtWidgets.QHBoxLayout()
    reactive_panel_row.addWidget(QtWidgets.QLabel("Visible panels"))
    reactive_chord_panel_check = QtWidgets.QCheckBox("Chords")
    reactive_chord_panel_check.setObjectName(
        "reactiveChordPanelVisibleCheck"
    )
    reactive_chord_panel_check.setChecked(True)
    reactive_panel_row.addWidget(reactive_chord_panel_check)
    reactive_waveform_panel_check = QtWidgets.QCheckBox("Waveform")
    reactive_waveform_panel_check.setObjectName(
        "reactiveWaveformPanelVisibleCheck"
    )
    reactive_waveform_panel_check.setChecked(True)
    reactive_panel_row.addWidget(reactive_waveform_panel_check)
    reactive_harmonic_panel_check = QtWidgets.QCheckBox("FFT + wheel")
    reactive_harmonic_panel_check.setObjectName(
        "reactiveHarmonicPanelVisibleCheck"
    )
    reactive_harmonic_panel_check.setChecked(True)
    reactive_panel_row.addWidget(reactive_harmonic_panel_check)
    reactive_panel_row.addStretch(1)
    reactive_diagnostics_layout.addLayout(reactive_panel_row)
    reactive_diagnostics_splitter = QtWidgets.QSplitter(
        QtCore.Qt.Orientation.Vertical
    )
    reactive_diagnostics_splitter.setObjectName(
        "reactiveDiagnosticsSplitter"
    )
    reactive_diagnostics_splitter.setChildrenCollapsible(False)
    reactive_diagnostics_splitter.setHandleWidth(7)
    reactive_diagnostics_splitter.setOpaqueResize(True)
    reactive_diagnostics_splitter.setMinimumHeight(520)

    reactive_chord_history_group = QtWidgets.QGroupBox(
        "Chord History + Structural Prediction"
    )
    reactive_chord_history_group.setObjectName(
        "reactiveChordHistoryGroup"
    )
    reactive_chord_history_layout = QtWidgets.QVBoxLayout(
        reactive_chord_history_group
    )
    reactive_chord_header = QtWidgets.QHBoxLayout()
    reactive_chord_label = QtWidgets.QLabel(
        "Current detected chord: — · Previous bars: —"
    )
    reactive_chord_label.setObjectName("reactiveChordLabel")
    reactive_chord_label.setStyleSheet(
        "color: #c084fc; font-weight: 600;"
    )
    reactive_chord_label.setWordWrap(True)
    reactive_chord_label.setToolTip(
        "Confirmed live chord history. The current chord is followed by the "
        "three prior completed bars, locked on downbeats; repeated chords are "
        "preserved. A next chord and time appear only after a repeating "
        "section pattern has been established."
    )
    reactive_chord_header.addWidget(reactive_chord_label, 1)
    reactive_chord_popout_button = QtWidgets.QPushButton("Pop Out")
    reactive_chord_popout_button.setObjectName(
        "reactiveChordPopoutButton"
    )
    reactive_chord_popout_button.setToolTip(
        "Open a synchronized, independently resizable chord-history "
        "window."
    )
    reactive_chord_header.addWidget(reactive_chord_popout_button)
    reactive_chord_fullscreen_button = QtWidgets.QPushButton("Fullscreen")
    reactive_chord_fullscreen_button.setObjectName(
        "reactiveChordFullscreenButton"
    )
    reactive_chord_fullscreen_button.setToolTip(
        "Open a synchronized fullscreen chord-history view. Press Escape "
        "to close it and return to the Reactive canvas."
    )
    reactive_chord_header.addWidget(reactive_chord_fullscreen_button)
    reactive_chord_history_layout.addLayout(reactive_chord_header)
    reactive_chord_history_view = build_reactive_chord_history_view(
        qt_modules
    )
    reactive_chord_history_layout.addWidget(
        reactive_chord_history_view,
        1,
    )
    reactive_diagnostics_splitter.addWidget(reactive_chord_history_group)

    reactive_waveform_panel = QtWidgets.QWidget()
    reactive_waveform_panel.setObjectName("reactiveWaveformPanel")
    reactive_waveform_layout = QtWidgets.QVBoxLayout(
        reactive_waveform_panel
    )
    reactive_waveform_layout.setContentsMargins(0, 0, 0, 0)
    reactive_active_effects_label = QtWidgets.QLabel(
        "Active effects: awaiting renderer"
    )
    reactive_active_effects_label.setObjectName("reactiveActiveEffectsLabel")
    reactive_active_effects_label.setStyleSheet(
        "color: #f5d0fe; background: #111827; padding: 6px; "
        "font-family: Consolas, monospace;"
    )
    reactive_active_effects_label.setWordWrap(True)
    reactive_active_effects_label.setToolTip(
        "The final effect and render mode sent to output after preset, route, "
        "and Live-control overrides. Transient effects show total decay and "
        "time remaining."
    )
    reactive_waveform_layout.addWidget(reactive_active_effects_label)
    reactive_cycle_label = QtWidgets.QLabel(
        "Structure similarity: acquiring configured meter and bar fingerprints"
    )
    reactive_cycle_label.setObjectName("reactiveCyclePositionLabel")
    reactive_cycle_label.setStyleSheet(
        "color: #bfdbfe; background: #111827; padding: 6px; "
        "font-family: Consolas, monospace;"
    )
    reactive_cycle_label.setWordWrap(True)
    reactive_cycle_label.setToolTip(
        "Chord-optional bar similarity, phrase hypotheses, anonymous section "
        "identity, scheduled target downbeat, and actual applied action."
    )
    reactive_waveform_layout.addWidget(reactive_cycle_label)
    reactive_waveform_view = build_reactive_waveform_view(qt_modules)
    reactive_waveform_layout.addWidget(reactive_waveform_view, 1)
    reactive_diagnostics_splitter.addWidget(reactive_waveform_panel)

    reactive_harmonic_debug_group = QtWidgets.QGroupBox(
        "Harmonic Debug · FFT + Chord Wheel"
    )
    reactive_harmonic_debug_group.setObjectName(
        "reactiveHarmonicDebugGroup"
    )
    reactive_harmonic_debug_layout = QtWidgets.QVBoxLayout(
        reactive_harmonic_debug_group
    )
    reactive_harmonic_header = QtWidgets.QHBoxLayout()
    reactive_harmonic_header.addStretch(1)
    reactive_harmonic_popout_button = QtWidgets.QPushButton("Pop Out")
    reactive_harmonic_popout_button.setObjectName(
        "reactiveHarmonicPopoutButton"
    )
    reactive_harmonic_popout_button.setToolTip(
        "Open a synchronized, independently resizable FFT and chord-wheel "
        "window."
    )
    reactive_harmonic_header.addWidget(
        reactive_harmonic_popout_button
    )
    reactive_harmonic_fullscreen_button = QtWidgets.QPushButton(
        "Fullscreen"
    )
    reactive_harmonic_fullscreen_button.setObjectName(
        "reactiveHarmonicFullscreenButton"
    )
    reactive_harmonic_fullscreen_button.setToolTip(
        "Open a synchronized fullscreen FFT and chord-wheel view. Press "
        "Escape to close it and return to the Reactive canvas."
    )
    reactive_harmonic_header.addWidget(
        reactive_harmonic_fullscreen_button
    )
    reactive_harmonic_debug_layout.addLayout(reactive_harmonic_header)
    reactive_harmonic_debug_view = build_reactive_harmonic_debug_view(
        qt_modules
    )
    reactive_harmonic_debug_layout.addWidget(
        reactive_harmonic_debug_view
    )
    reactive_harmonic_debug_group.setVisible(False)
    reactive_diagnostics_splitter.addWidget(
        reactive_harmonic_debug_group
    )
    reactive_diagnostics_splitter.setStretchFactor(0, 1)
    reactive_diagnostics_splitter.setStretchFactor(1, 2)
    reactive_diagnostics_splitter.setStretchFactor(2, 3)
    reactive_diagnostics_splitter.setSizes((150, 260, 330))
    reactive_diagnostics_layout.addWidget(
        reactive_diagnostics_splitter,
        1,
    )
    reactive_live_content_layout.addWidget(
        _reactive_collapsible_panel(
            reactive_diagnostics_group,
            title="Live Diagnostics",
            object_name="reactiveDiagnosticsPanel",
            expanded=True,
        )
    )
    reactive_live_content_layout.addStretch(1)
    reactive_actions = QtWidgets.QHBoxLayout()
    start_reactive_button.setText("Start Reactive")
    reactive_actions.addWidget(start_reactive_button)
    stop_reactive_button = QtWidgets.QPushButton("Stop Reactive")
    stop_reactive_button.setObjectName("stopReactiveButton")
    reactive_actions.addWidget(stop_reactive_button)
    reactive_live_layout.addLayout(reactive_actions)
    reactive_live_group.setVisible(False)
    queue_left_layout.addWidget(reactive_live_group, 3)
    local_group = QtWidgets.QGroupBox("Live Queue")
    local_group.setObjectName("liveQueueGroup")
    local_layout = QtWidgets.QVBoxLayout(local_group)
    local_list = QueueListWidget()
    local_list.setObjectName("localQueueList")
    local_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
    local_list.setDragEnabled(True)
    local_list.setAcceptDrops(True)
    local_list.setDropIndicatorShown(True)
    local_list.setDragDropMode(QtWidgets.QAbstractItemView.DragDropMode.InternalMove)
    local_list.setDefaultDropAction(QtCore.Qt.DropAction.MoveAction)
    local_layout.addWidget(local_list)
    queue_left_layout.addWidget(local_group, 3)

    spotify_group = QtWidgets.QGroupBox("Spotify Queue")
    spotify_group.setObjectName("spotifyQueueGroup")
    spotify_group.setVisible(False)
    spotify_layout = QtWidgets.QVBoxLayout(spotify_group)
    spotify_list = QtWidgets.QListWidget()
    spotify_list.setObjectName("spotifyQueueList")
    spotify_layout.addWidget(spotify_list)
    spotify_actions = QtWidgets.QHBoxLayout()
    spotify_refresh_button = QtWidgets.QPushButton("Refresh")
    spotify_skip_button = QtWidgets.QPushButton("Skip")
    spotify_shuffle_button = QtWidgets.QPushButton("Shuffle")
    for button in (spotify_refresh_button, spotify_skip_button, spotify_shuffle_button):
        spotify_actions.addWidget(button)
    spotify_layout.addLayout(spotify_actions)
    spotify_add_row = QtWidgets.QHBoxLayout()
    spotify_uri_edit = QtWidgets.QLineEdit()
    spotify_uri_edit.setPlaceholderText("spotify:track:…")
    spotify_add_button = QtWidgets.QPushButton("Add")
    spotify_add_row.addWidget(spotify_uri_edit, 1)
    spotify_add_row.addWidget(spotify_add_button)
    spotify_layout.addLayout(spotify_add_row)
    queue_left_layout.addWidget(spotify_group, 2)
    queue_splitter.addWidget(queue_left_panel)

    queue_right_panel = QtWidgets.QWidget()
    queue_right_layout = QtWidgets.QVBoxLayout(queue_right_panel)
    playback_group = QtWidgets.QGroupBox("Preview")
    playback_layout = QtWidgets.QVBoxLayout(playback_group)
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
    simulation_controls.addWidget(QtWidgets.QLabel("Strips"))
    simulation_strip_mode_combo = QtWidgets.QComboBox()
    simulation_strip_mode_combo.addItem("Segments", "segments")
    simulation_strip_mode_combo.addItem("Bounds", "bounds")
    simulation_strip_mode_combo.setObjectName("simulationStripModeCombo")
    simulation_controls.addWidget(simulation_strip_mode_combo, 1)
    simulation_freeze_check = QtWidgets.QCheckBox("Freeze frame")
    simulation_freeze_check.setObjectName("simulationFreezeFrameCheck")
    simulation_freeze_check.setToolTip(
        "Hold the last valid per-device RGB frame for optical inspection."
    )
    simulation_controls.addWidget(simulation_freeze_check)
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
    simulation_frame_diagnostics_label = QtWidgets.QLabel(
        "Frame: waiting for output"
    )
    simulation_frame_diagnostics_label.setObjectName(
        "simulationFrameDiagnosticsLabel"
    )
    simulation_frame_diagnostics_label.setWordWrap(True)
    playback_layout.addWidget(simulation_frame_diagnostics_label)
    queue_right_layout.addWidget(playback_group, 3)

    palette_group = QtWidgets.QGroupBox("Live Palette")
    palette_layout = QtWidgets.QVBoxLayout(palette_group)
    selected_song_label = QtWidgets.QLabel("Selected song: none")
    selected_song_label.setObjectName("selectedSongLabel")
    selected_assignment_label = QtWidgets.QLabel("Assigned palette: none")
    selected_assignment_label.setObjectName("selectedAssignmentLabel")
    selected_path_label = QtWidgets.QLabel("Path: none")
    selected_path_label.setObjectName("selectedPathLabel")
    live_show_source_label = QtWidgets.QLabel("Show: waiting for playback")
    live_show_source_label.setObjectName("liveShowSourceLabel")
    live_show_source_label.setToolTip(
        "Reports whether Live is using prepared cue data, a cache, an analysis sidecar, or a fresh compile."
    )
    for control in (
        selected_song_label,
        selected_assignment_label,
        selected_path_label,
        live_show_source_label,
    ):
        control.setWordWrap(True)
        palette_layout.addWidget(control)
    live_palette_preview_label = QtWidgets.QLabel("No palette selected")
    live_palette_preview_label.setObjectName("livePalettePreviewLabel")
    live_palette_preview_label.setMinimumHeight(26)
    live_palette_preview_label.setToolTip(
        "Read-only palette preview. Edit palette definitions in the Palettes tab."
    )
    palette_layout.addWidget(live_palette_preview_label)
    action_row = QtWidgets.QHBoxLayout()
    cycle_palette_button = QtWidgets.QPushButton("Cycle Palette")
    cycle_palette_button.setObjectName("cycleLivePaletteButton")
    recompile_palette_button = QtWidgets.QPushButton("Recompile Show")
    recompile_palette_button.setObjectName("recompileLivePaletteButton")
    action_row.addWidget(cycle_palette_button)
    action_row.addWidget(recompile_palette_button)
    palette_layout.addLayout(action_row)
    live_palette_status_label = QtWidgets.QLabel(
        "Palette definitions are managed in Palettes. Cycling only selects a pre-generated palette."
    )
    live_palette_status_label.setWordWrap(True)
    palette_layout.addWidget(live_palette_status_label)
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
        load_show_button,
        clear_show_button,
        save_show_button,
        add_show_track_button,
        remove_show_track_button,
        move_show_track_up_button,
        move_show_track_down_button,
        compile_show_button,
        compile_all_tracks_button,
        bake_show_button,
        validate_baked_button,
        save_baked_as_button,
    ):
        shows_toolbar.addWidget(button)
    shows_toolbar.addStretch(1)
    shows_layout.addLayout(shows_toolbar)
    compile_settings_row = QtWidgets.QHBoxLayout()
    compile_seed_check = QtWidgets.QCheckBox("Deterministic compile seed")
    compile_seed_check.setObjectName("compileSeedCheck")
    compile_seed_spin = QtWidgets.QSpinBox()
    compile_seed_spin.setObjectName("compileSeedSpin")
    compile_seed_spin.setRange(-2147483647, 2147483647)
    compile_seed_spin.setValue(0)
    compile_seed_spin.setEnabled(False)
    compile_seed_check.toggled.connect(compile_seed_spin.setEnabled)
    compile_settings_row.addWidget(compile_seed_check)
    compile_settings_row.addWidget(compile_seed_spin)
    compile_settings_row.addStretch(1)
    shows_layout.addLayout(compile_settings_row)

    show_playback_toolbar = QtWidgets.QHBoxLayout()
    show_playback_toolbar.addWidget(play_saved_show_button)
    for button in (play_selected_cue_button, pause_show_button, stop_show_button):
        show_playback_toolbar.addWidget(button)
    show_playback_toolbar.addStretch(1)
    shows_layout.addLayout(show_playback_toolbar)
    shows_layout.addWidget(saved_show_label)

    shows_splitter = QtWidgets.QSplitter()
    shows_splitter.setOrientation(QtCore.Qt.Orientation.Horizontal)
    shows_layout.addWidget(shows_splitter, 1)

    shows_left_panel = QtWidgets.QWidget()
    shows_left_layout = QtWidgets.QVBoxLayout(shows_left_panel)

    show_tracks_group = QtWidgets.QGroupBox("Show Tracks")
    show_tracks_group.setObjectName("showTracksGroup")
    show_tracks_layout = QtWidgets.QVBoxLayout(show_tracks_group)
    show_name_edit = QtWidgets.QLineEdit("Untitled Show")
    show_name_edit.setObjectName("showNameEdit")
    show_name_edit.setPlaceholderText("Show name")
    show_tracks_layout.addWidget(show_name_edit)
    show_tracks_list = QtWidgets.QListWidget()
    show_tracks_list.setObjectName("showTracksList")
    show_tracks_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
    show_tracks_layout.addWidget(show_tracks_list, 1)
    shows_left_layout.addWidget(show_tracks_group, 2)

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

    show_editor_group = QtWidgets.QGroupBox("Track Editor")
    show_editor_group.setObjectName("savedShowEditorGroup")
    show_editor_outer_layout = QtWidgets.QVBoxLayout(show_editor_group)
    show_editor_scroll = QtWidgets.QScrollArea()
    show_editor_scroll.setObjectName("showEditorScrollArea")
    show_editor_scroll.setWidgetResizable(True)
    show_editor_content = QtWidgets.QWidget()
    show_editor_layout = QtWidgets.QVBoxLayout(show_editor_content)
    show_editor_scroll.setWidget(show_editor_content)
    show_editor_outer_layout.addWidget(show_editor_scroll)
    show_audio_label = QtWidgets.QLabel("Track: none")
    show_audio_label.setObjectName("showEditorAudioLabel")
    show_audio_label.setWordWrap(True)
    show_editor_path_label = QtWidgets.QLabel("Show file: none")
    show_editor_path_label.setObjectName("showEditorPathLabel")
    show_editor_path_label.setWordWrap(True)
    show_editor_layout.addWidget(show_audio_label)
    show_editor_layout.addWidget(show_editor_path_label)
    show_editor_status_label = QtWidgets.QLabel("Add a Track, then compile it to start editing.")
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

    show_simulation_group = QtWidgets.QGroupBox("Show Preview")
    show_simulation_group.setObjectName("showSimulationGroup")
    show_simulation_group.setCheckable(True)
    show_simulation_group.setChecked(True)
    show_simulation_group.setToolTip("Click the title checkbox to collapse or expand the Show Preview.")
    show_simulation_group_layout = QtWidgets.QVBoxLayout(show_simulation_group)
    show_simulation_content = QtWidgets.QWidget()
    show_simulation_content.setObjectName("showSimulationContent")
    show_simulation_content_layout = QtWidgets.QVBoxLayout(show_simulation_content)
    show_simulation_content_layout.setContentsMargins(0, 0, 0, 0)
    show_simulation_controls = QtWidgets.QHBoxLayout()
    show_simulation_controls.addWidget(QtWidgets.QLabel("View"))
    show_simulation_view_combo = QtWidgets.QComboBox()
    for label, data in (
        ("Room", "room"),
        ("Front (X/Y)", "xy"),
        ("Top (X/Z)", "xz"),
        ("Side (Z/Y)", "yz"),
    ):
        show_simulation_view_combo.addItem(label, data)
    show_simulation_view_combo.setObjectName("showSimulationViewCombo")
    show_simulation_controls.addWidget(show_simulation_view_combo, 1)
    show_simulation_controls.addWidget(QtWidgets.QLabel("Background"))
    show_simulation_background_combo = QtWidgets.QComboBox()
    show_simulation_background_combo.addItem("Black", "dark")
    show_simulation_background_combo.addItem("White", "light")
    show_simulation_background_combo.setObjectName("showSimulationBackgroundCombo")
    show_simulation_controls.addWidget(show_simulation_background_combo, 1)
    show_simulation_controls.addWidget(QtWidgets.QLabel("Strips"))
    show_simulation_strip_mode_combo = QtWidgets.QComboBox()
    show_simulation_strip_mode_combo.addItem("Segments", "segments")
    show_simulation_strip_mode_combo.addItem("Bounds", "bounds")
    show_simulation_strip_mode_combo.setObjectName(
        "showSimulationStripModeCombo"
    )
    show_simulation_controls.addWidget(
        show_simulation_strip_mode_combo,
        1,
    )
    show_simulation_popout_button = QtWidgets.QPushButton("Pop Out")
    show_simulation_popout_button.setObjectName("showSimulationPopoutButton")
    show_simulation_controls.addWidget(show_simulation_popout_button)
    show_simulation_fullscreen_button = QtWidgets.QPushButton("Fullscreen")
    show_simulation_fullscreen_button.setObjectName("showSimulationFullscreenButton")
    show_simulation_controls.addWidget(show_simulation_fullscreen_button)
    show_simulation_content_layout.addLayout(show_simulation_controls)
    show_simulation_host = QtWidgets.QWidget()
    show_simulation_host.setObjectName("showSimulationHost")
    show_simulation_layout = QtWidgets.QVBoxLayout(show_simulation_host)
    show_simulation_layout.setContentsMargins(0, 0, 0, 0)
    show_simulation_content_layout.addWidget(show_simulation_host)
    show_simulation_group_layout.addWidget(show_simulation_content)
    show_editor_layout.addWidget(show_simulation_group)

    show_meta_group = QtWidgets.QGroupBox("Show Details")
    show_meta_group.setObjectName("showMetaGroup")
    show_meta_group.setCheckable(True)
    show_meta_group.setChecked(True)
    show_meta_group.setToolTip("Click the title checkbox to collapse or expand Show Details.")
    show_meta_group_layout = QtWidgets.QVBoxLayout(show_meta_group)
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
    show_meta_group_layout.addWidget(show_meta_panel)
    show_editor_layout.addWidget(show_meta_group)

    show_routing_group = QtWidgets.QGroupBox("Show Routing Defaults and Overrides")
    show_routing_group.setObjectName("showRoutingGroup")
    show_routing_group.setCheckable(True)
    show_routing_group.setChecked(True)
    show_routing_group.setToolTip(
        "Click the title checkbox to collapse or expand show routing defaults and overrides."
    )
    show_routing_group_layout = QtWidgets.QVBoxLayout(show_routing_group)
    show_routing_host = QtWidgets.QWidget()
    show_routing_host.setObjectName("showRoutingHost")
    show_routing_layout = QtWidgets.QGridLayout(show_routing_host)
    show_routing_layout.setContentsMargins(0, 0, 0, 0)
    show_routing_layout.addWidget(QtWidgets.QLabel("Show routing defaults and overrides"), 0, 0, 1, 2)
    show_routing_group_layout.addWidget(show_routing_host)
    show_editor_layout.addWidget(show_routing_group)

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

    show_cues_table = QtWidgets.QTableWidget(0, 19)
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
            "Pan Follow",
            "Int Boost",
            "Origin",
            "Direction",
            "Color Bias",
            "Intensity Start",
            "Layers",
            "Target Groups",
            "Group Match",
            "Exclude Groups",
            "Untargeted",
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
        save_configuration_button=save_configuration_button,
        configuration_save_status_label=configuration_save_status_label,
        load_saved_show_button=load_saved_show_button,
        load_saved_track_button=load_saved_track_button,
        cue_track_button=cue_track_button,
        load_show_button=load_show_button,
        compile_show_button=compile_show_button,
        compile_all_tracks_button=compile_all_tracks_button,
        clear_show_button=clear_show_button,
        save_show_button=save_show_button,
        add_show_track_button=add_show_track_button,
        remove_show_track_button=remove_show_track_button,
        move_show_track_up_button=move_show_track_up_button,
        move_show_track_down_button=move_show_track_down_button,
        bake_show_button=bake_show_button,
        validate_baked_button=validate_baked_button,
        save_baked_as_button=save_baked_as_button,
        compile_seed_check=compile_seed_check,
        compile_seed_spin=compile_seed_spin,
        refresh_button=refresh_button,
        start_preview_button=start_preview_button,
        pause_playback_button=pause_playback_button,
        play_saved_show_button=play_saved_show_button,
        baked_playback_combo=baked_playback_combo,
        play_selected_cue_button=play_selected_cue_button,
        start_capture_button=start_capture_button,
        stop_capture_button=stop_capture_button,
        switch_pipeline_button=switch_pipeline_button,
        start_reactive_button=start_reactive_button,
        stop_output_button=stop_output_button,
        stop_preview_button=stop_preview_button,
        shuffle_button=shuffle_button,
        repeat_button=repeat_button,
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
        dark_mode_check=dark_mode_check,
        output_device_combo=output_device_combo,
        input_device_combo=input_device_combo,
        startup_live_mode_combo=startup_live_mode_combo,
        device_health_label=device_health_label,
        refresh_device_health_button=refresh_device_health_button,
        device_room_config_path_edit=device_room_config_path_edit,
        browse_device_room_config_button=browse_device_room_config_button,
        profile_directory_edit=profile_directory_edit,
        browse_profile_directory_button=browse_profile_directory_button,
        show_directory_edit=show_directory_edit,
        browse_show_directory_button=browse_show_directory_button,
        queue_directory_edit=queue_directory_edit,
        browse_queue_directory_button=browse_queue_directory_button,
        apply_file_locations_button=apply_file_locations_button,
        file_locations_status_label=file_locations_status_label,
        storage_cache_label=storage_cache_label,
        storage_capture_label=storage_capture_label,
        refresh_storage_button=refresh_storage_button,
        clear_all_cache_button=clear_all_cache_button,
        clear_selected_cache_button=clear_selected_cache_button,
        archive_captures_button=archive_captures_button,
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
        reactive_mirror_combo=reactive_mirror_combo,
        reactive_master_brightness_spin=reactive_master_brightness_spin,
        reactive_auto_cycle_check=reactive_auto_cycle_check,
        reactive_cycle_interval_spin=reactive_cycle_interval_spin,
        reactive_debug_mood_check=reactive_debug_mood_check,
        reactive_crossfade_check=reactive_crossfade_check,
        reactive_harmonic_structure_check=reactive_harmonic_structure_check,
        reactive_structure_sensitivity_spin=reactive_structure_sensitivity_spin,
        reactive_debug_harmonics_check=reactive_debug_harmonics_check,
        reactive_predictive_analysis_check=reactive_predictive_analysis_check,
        reactive_predictive_diagnostics_check=reactive_predictive_diagnostics_check,
        reactive_predictive_shadow_check=reactive_predictive_shadow_check,
        reactive_predictive_cues_check=reactive_predictive_cues_check,
        reactive_structure_phrase_actions_check=reactive_structure_phrase_actions_check,
        reactive_predictive_high_impact_check=reactive_predictive_high_impact_check,
        reactive_telemetry_dir_edit=reactive_telemetry_dir_edit,
        reactive_profile_strategy_combo=reactive_profile_strategy_combo,
        reactive_profile_override_label=reactive_profile_override_label,
        browse_reactive_profile_button=browse_reactive_profile_button,
        reactive_show_palette_set_combo=reactive_show_palette_set_combo,
        reactive_palette_rotation_label=reactive_palette_rotation_label,
        reactive_rotation_profiles_picker=reactive_rotation_profiles_picker,
        reactive_configuration_warning_label=reactive_configuration_warning_label,
        reactive_rotation_interval_spin=reactive_rotation_interval_spin,
        reactive_auto_palette_check=reactive_auto_palette_check,
        reactive_smart_rotation_check=reactive_smart_rotation_check,
        reactive_chain_blend_spin=reactive_chain_blend_spin,
        reactive_auto_palette_seed_check=reactive_auto_palette_seed_check,
        reactive_auto_palette_seed_spin=reactive_auto_palette_seed_spin,
        reactive_auto_palette_pool_size_spin=reactive_auto_palette_pool_size_spin,
        reactive_chain_dwell_range_check=reactive_chain_dwell_range_check,
        reactive_chain_min_dwell_spin=reactive_chain_min_dwell_spin,
        reactive_chain_max_dwell_spin=reactive_chain_max_dwell_spin,
        preview_profile_chain_button=preview_profile_chain_button,
        local_list=local_list,
        live_queue_toolbar=live_queue_toolbar,
        queue_mode_button=queue_mode_button,
        reactive_mode_button=reactive_mode_button,
        raw_visualizer_mode_button=raw_visualizer_mode_button,
        live_queue_group=local_group,
        live_reactive_group=reactive_live_group,
        reactive_mode_status_label=reactive_mode_status_label,
        reactive_profile_label=reactive_profile_label,
        reactive_active_palette_label=reactive_active_palette_label,
        reactive_active_palette_preview_label=reactive_active_palette_preview_label,
        reactive_palette_next_label=reactive_palette_next_label,
        reactive_palette_queue_label=reactive_palette_queue_label,
        reactive_live_look_group=reactive_live_look_group,
        reactive_live_color_profile_combo=reactive_live_color_profile_combo,
        reactive_live_active_effect_combo=reactive_live_active_effect_combo,
        reactive_live_effect_speed_combo=reactive_live_effect_speed_combo,
        reactive_live_effect_origin_combo=reactive_live_effect_origin_combo,
        reactive_live_effect_buttons=tuple(reactive_live_effect_buttons),
        reactive_live_look_status_label=reactive_live_look_status_label,
        reactive_listening_label=reactive_listening_label,
        reactive_beat_indicator_label=reactive_beat_indicator_label,
        reactive_bpm_label=reactive_bpm_label,
        reactive_effect_tempo_label=reactive_effect_tempo_label,
        reactive_cycle_tempo_label=reactive_cycle_tempo_label,
        reactive_cycle_tempo_half_button=(
            reactive_cycle_tempo_half_button
        ),
        reactive_cycle_tempo_normal_button=(
            reactive_cycle_tempo_normal_button
        ),
        reactive_cycle_tempo_double_button=(
            reactive_cycle_tempo_double_button
        ),
        reactive_effect_tempo_half_button=(
            reactive_effect_tempo_half_button
        ),
        reactive_effect_tempo_normal_button=(
            reactive_effect_tempo_normal_button
        ),
        reactive_effect_tempo_double_button=(
            reactive_effect_tempo_double_button
        ),
        reactive_downbeat_nudge_button=(
            reactive_downbeat_nudge_button
        ),
        reactive_chord_panel_check=reactive_chord_panel_check,
        reactive_waveform_panel_check=reactive_waveform_panel_check,
        reactive_harmonic_panel_check=reactive_harmonic_panel_check,
        reactive_diagnostics_splitter=reactive_diagnostics_splitter,
        reactive_chord_history_group=reactive_chord_history_group,
        reactive_chord_label=reactive_chord_label,
        reactive_chord_history_view=reactive_chord_history_view,
        reactive_chord_popout_button=reactive_chord_popout_button,
        reactive_chord_fullscreen_button=reactive_chord_fullscreen_button,
        reactive_waveform_panel=reactive_waveform_panel,
        reactive_active_effects_label=reactive_active_effects_label,
        reactive_cycle_label=reactive_cycle_label,
        reactive_waveform_view=reactive_waveform_view,
        reactive_harmonic_debug_group=reactive_harmonic_debug_group,
        reactive_harmonic_debug_view=reactive_harmonic_debug_view,
        reactive_harmonic_popout_button=reactive_harmonic_popout_button,
        reactive_harmonic_fullscreen_button=reactive_harmonic_fullscreen_button,
        stop_reactive_button=stop_reactive_button,
        spotify_group=spotify_group,
        recent_saved_list=recent_saved_list,
        show_tracks_list=show_tracks_list,
        show_name_edit=show_name_edit,
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
        show_meta_group=show_meta_group,
        show_meta_panel=show_meta_panel,
        show_routing_group=show_routing_group,
        show_routing_host=show_routing_host,
        show_meta_toggle_button=show_meta_toggle_button,
        show_columns_button=show_columns_button,
        show_cues_table=show_cues_table,
        show_timeline_view=show_timeline_view,
        show_simulation_group=show_simulation_group,
        show_simulation_content=show_simulation_content,
        show_simulation_view_combo=show_simulation_view_combo,
        show_simulation_background_combo=show_simulation_background_combo,
        show_simulation_strip_mode_combo=show_simulation_strip_mode_combo,
        show_simulation_popout_button=show_simulation_popout_button,
        show_simulation_fullscreen_button=show_simulation_fullscreen_button,
        show_simulation_host=show_simulation_host,
        show_simulation_layout=show_simulation_layout,
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
        baked_status_label=baked_status_label,
        playback_track_label=playback_track_label,
        playback_time_label=playback_time_label,
        device_status_label=device_status_label,
        audio_output_label=audio_output_label,
        input_device_status_label=input_device_status_label,
        simulation_view_combo=simulation_view_combo,
        simulation_background_combo=simulation_background_combo,
        simulation_strip_mode_combo=simulation_strip_mode_combo,
        simulation_freeze_check=simulation_freeze_check,
        simulation_frame_diagnostics_label=simulation_frame_diagnostics_label,
        simulation_popout_button=simulation_popout_button,
        simulation_fullscreen_button=simulation_fullscreen_button,
        simulation_host=simulation_host,
        simulation_layout=simulation_layout,
        selected_song_label=selected_song_label,
        selected_assignment_label=selected_assignment_label,
        selected_path_label=selected_path_label,
        live_show_source_label=live_show_source_label,
        live_palette_preview_label=live_palette_preview_label,
        live_palette_group=palette_group,
        cycle_palette_button=cycle_palette_button,
        recompile_palette_button=recompile_palette_button,
        live_palette_status_label=live_palette_status_label,
        spotify_refresh_button=spotify_refresh_button,
        spotify_skip_button=spotify_skip_button,
        spotify_shuffle_button=spotify_shuffle_button,
        spotify_uri_edit=spotify_uri_edit,
        spotify_add_button=spotify_add_button,
        live_loopback_check=live_loopback_check,
        patch_name_edit=patch_name_edit,
        patch_rules_edit=patch_rules_edit,
        patch_summary_label=patch_summary_label,
        save_patch_button=save_patch_button,
        clear_patch_button=clear_patch_button,
        show_override_group=patch_group,
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
        runtime_disabled_groups_edit=runtime_disabled_groups_edit,
        runtime_enabled_groups_edit=runtime_enabled_groups_edit,
        runtime_solo_groups_edit=runtime_solo_groups_edit,
        runtime_apply_button=runtime_apply_button,
        runtime_clear_button=runtime_clear_button,
        runtime_control_status_label=runtime_control_status_label,
        status_label=status_label,
    )
