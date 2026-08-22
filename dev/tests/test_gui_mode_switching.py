from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from dreamsync.gui.main_window import create_main_window
from dreamsync.gui.qt import require_qt
from dreamsync.gui.settings import GuiSettings


def test_help_action_opens_searchable_how_to_guide():
    QtCore = pytest.importorskip("PySide6.QtCore")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = create_main_window(
        require_qt(),
        GuiSettings(),
        config_path=Path("dev/devices-dummy.yaml"),
    )
    action = window.findChild(QtCore.QObject, "dreamSyncHelpGuideAction")
    assert action is not None

    observed: dict[str, object] = {}

    def inspect_and_close() -> None:
        dialog = window.findChild(QtWidgets.QDialog, "dreamSyncHelpGuideDialog")
        assert dialog is not None
        sections = dialog.findChild(QtWidgets.QListWidget, "dreamSyncHelpGuideSections")
        search = dialog.findChild(QtWidgets.QLineEdit, "dreamSyncHelpGuideSearch")
        browser = dialog.findChild(QtWidgets.QTextBrowser, "dreamSyncHelpGuideText")
        assert sections is not None
        assert search is not None
        assert browser is not None
        observed["content"] = browser.toPlainText()
        search.setText("VB-Cable")
        observed["vb_cable_visible"] = not sections.item(1).isHidden()
        observed["vb_cable_content"] = browser.toPlainText()
        dialog.accept()

    QtCore.QTimer.singleShot(0, inspect_and_close)
    action.trigger()
    app.processEvents()
    assert "DreamSync how-to guide" in str(observed["content"])
    assert observed["vb_cable_visible"] is True
    assert "Windows system-audio loopback" in str(observed["vb_cable_content"])
    window.close()


def test_startup_with_live_loopback_enabled_renders_before_first_runtime_poll():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    token_store = type("TokenStore", (), {"access_token": ""})()
    with patch("dreamsync.spotify.auth.TokenStore", return_value=token_store):
        window = create_main_window(
            require_qt(),
            GuiSettings(live_loopback_enabled=True),
            config_path=Path("dev/devices-dummy.yaml"),
        )

    checkbox = window.findChild(QtWidgets.QCheckBox, "liveLoopbackCheck")
    assert checkbox is not None
    assert checkbox.isChecked()
    window.close()


def test_learning_mode_has_button_and_participates_in_m_cycle():
    QtCore = pytest.importorskip("PySide6.QtCore")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    QtTest = pytest.importorskip("PySide6.QtTest")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    window = create_main_window(
        require_qt(),
        GuiSettings(),
        config_path=Path("dev/devices-dummy.yaml"),
    )
    live_tab = window.findChild(QtWidgets.QWidget, "liveTabWidget")
    queue_button = window.findChild(QtWidgets.QPushButton, "queueLiveModeButton")
    reactive_button = window.findChild(
        QtWidgets.QPushButton,
        "reactiveLiveModeButton",
    )
    raw_button = window.findChild(
        QtWidgets.QPushButton,
        "rawVisualizerLiveModeButton",
    )
    learning_button = window.findChild(
        QtWidgets.QPushButton,
        "spotifyLearnedLiveModeButton",
    )
    reactive_toolbar = window.findChild(QtWidgets.QWidget, "liveReactiveToolbar")
    learning_toolbar = window.findChild(QtWidgets.QWidget, "liveLearningToolbar")
    start_reactive = window.findChild(QtWidgets.QPushButton, "startReactiveButton")
    start_raw = window.findChild(QtWidgets.QPushButton, "startRawVisualizerButton")
    start_learning = window.findChild(
        QtWidgets.QPushButton,
        "spotifyLearnedLiveStartButton",
    )
    learning_status = window.findChild(
        QtWidgets.QLabel,
        "spotifyLearnedLiveStatusLabel",
    )

    assert learning_button is not None
    assert reactive_toolbar.isAncestorOf(start_reactive)
    assert reactive_toolbar.isAncestorOf(start_raw)
    assert learning_toolbar.isAncestorOf(start_learning)
    learning_button.click()
    app.processEvents()
    assert learning_button.isChecked()
    assert not learning_toolbar.isHidden()
    assert reactive_toolbar.isHidden()
    assert learning_status.text() == "Learning: stopped"

    raw_button.click()
    app.processEvents()
    assert not reactive_toolbar.isHidden()
    assert start_reactive.isHidden()
    assert not start_raw.isHidden()

    queue_button.click()
    live_tab.setFocus()
    for expected in (
        reactive_button,
        raw_button,
        learning_button,
        queue_button,
    ):
        QtTest.QTest.keyClick(live_tab, QtCore.Qt.Key.Key_M)
        app.processEvents()
        assert expected.isChecked()

    window.close()


def test_space_starts_and_stops_raw_visualizer_mode():
    QtCore = pytest.importorskip("PySide6.QtCore")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    QtTest = pytest.importorskip("PySide6.QtTest")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    handles = []

    class _FakeHandle:
        mode = "reactive_live"
        error = None
        summary = None

        def __init__(self):
            self.running = True
            self.stop_calls = 0
            self.session_ref = [SimpleNamespace(session_snapshot=lambda: {})]

        def stop(self):
            self.stop_calls += 1
            self.running = False

    def _start_fake_raw(supervisor, **kwargs):
        handle = _FakeHandle()
        supervisor._reactive_effect_mode = kwargs["effect_mode"]
        supervisor._output_handle = handle
        handles.append(handle)
        return handle

    with patch(
        "dreamsync.gui.services.runtime_supervisor.RuntimeSupervisor.start_reactive_live",
        new=_start_fake_raw,
    ):
        window = create_main_window(
            require_qt(),
            GuiSettings(),
            config_path=Path("dev/devices-dummy.yaml"),
        )
        live_tab = window.findChild(QtWidgets.QWidget, "liveTabWidget")
        raw_button = window.findChild(
            QtWidgets.QPushButton,
            "rawVisualizerLiveModeButton",
        )
        raw_button.click()
        live_tab.setFocus()

        QtTest.QTest.keyClick(live_tab, QtCore.Qt.Key.Key_Space)
        app.processEvents()
        assert len(handles) == 1
        assert handles[0].running

        QtTest.QTest.keyClick(live_tab, QtCore.Qt.Key.Key_Space)
        app.processEvents()
        assert handles[0].stop_calls == 1
        assert not handles[0].running
        window.close()


def test_learning_start_updates_status_and_polls_live_preview():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    QtTest = pytest.importorskip("PySide6.QtTest")
    from dreamsync.spotify.learned_live_session import LearnedLiveSnapshot

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    class _FakeWatcher:
        def __init__(self, *_args, **_kwargs):
            self.running = False

        def start(self):
            self.running = True

        def stop(self):
            self.running = False

        def snapshot(self):
            return {}

    class _FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def close(self):
            pass

    class _FakeHandle:
        error = None
        summary = None

        def __init__(self, mode, session=None):
            self.mode = mode
            self.running = True
            self.session_ref = [session] if session is not None else []

        def stop(self):
            self.running = False

    class _FakeReactiveSession:
        def __init__(self):
            self.preview_calls = 0

        def session_snapshot(self):
            return {"playback_state": "playing"}

        def preview_frame_snapshot(self):
            self.preview_calls += 1
            return {"node_colors": {"preview-node": "#ff0000"}}

    reactive_session = _FakeReactiveSession()

    def _start_fake_learning(supervisor, *_args, **_kwargs):
        learned_session = SimpleNamespace(
            snapshot=lambda: LearnedLiveSnapshot(
                active_strategy="reactive",
                active_learning_state="capturing",
            )
        )
        supervisor._learned_live_session = learned_session
        supervisor._learned_live_handle = _FakeHandle("spotify_learned_live")
        supervisor._reactive_effect_mode = "reactive"
        supervisor._output_handle = _FakeHandle(
            "reactive_live",
            reactive_session,
        )
        return supervisor._learned_live_handle

    def _stop_fake_learning(supervisor):
        if supervisor._learned_live_handle is not None:
            supervisor._learned_live_handle.stop()
        supervisor._learned_live_handle = None
        supervisor._learned_live_session = None
        supervisor._stop_output_handle()

    token_store = SimpleNamespace(access_token="test-token")
    with (
        patch("dreamsync.spotify.auth.TokenStore", return_value=token_store),
        patch("dreamsync.spotify.client.SpotifyClient", _FakeClient),
        patch("dreamsync.spotify.queue_watcher.SpotifyQueueWatcher", _FakeWatcher),
        patch(
            "dreamsync.gui.services.runtime_supervisor.RuntimeSupervisor.start_spotify_learned_live",
            new=_start_fake_learning,
        ),
        patch(
            "dreamsync.gui.services.runtime_supervisor.RuntimeSupervisor.stop_spotify_learned_live",
            new=_stop_fake_learning,
        ),
    ):
        window = create_main_window(
            require_qt(),
            GuiSettings(),
            config_path=Path("dev/devices-dummy.yaml"),
        )
        mode_button = window.findChild(
            QtWidgets.QPushButton,
            "spotifyLearnedLiveModeButton",
        )
        start_button = window.findChild(
            QtWidgets.QPushButton,
            "spotifyLearnedLiveStartButton",
        )
        stop_button = window.findChild(
            QtWidgets.QPushButton,
            "spotifyLearnedLiveStopButton",
        )
        status = window.findChild(
            QtWidgets.QLabel,
            "spotifyLearnedLiveStatusLabel",
        )

        mode_button.click()
        start_button.click()
        QtTest.QTest.qWait(80)
        app.processEvents()

        assert status.text() == "Learning: running"
        assert reactive_session.preview_calls > 0
        label_texts = [label.text() for label in window.findChildren(QtWidgets.QLabel)]
        assert "Spotify Live — Learning started." in label_texts
        assert not any("Start when Spotify is playing" in text for text in label_texts)

        stop_button.click()
        app.processEvents()
        assert status.text() == "Learning: stopped"
        assert "Spotify Live — Learning stopped." in [
            label.text() for label in window.findChildren(QtWidgets.QLabel)
        ]
        window.close()


def test_raw_visualizer_mode_hides_reactive_live_controls():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    window = create_main_window(
        require_qt(),
        GuiSettings(),
        config_path=Path("dev/devices-dummy.yaml"),
    )
    raw_mode_button = window.findChild(
        QtWidgets.QPushButton,
        "rawVisualizerLiveModeButton",
    )
    queue_group = window.findChild(
        QtWidgets.QGroupBox,
        "liveQueueGroup",
    )
    reactive_group = window.findChild(
        QtWidgets.QGroupBox,
        "reactiveLiveGroup",
    )
    raw_group = window.findChild(
        QtWidgets.QGroupBox,
        "rawVisualizerLiveGroup",
    )
    point_count = window.findChild(
        QtWidgets.QSpinBox,
        "rawVisualizerPointCountSpin",
    )
    fourth_frequency = window.findChild(
        QtWidgets.QSpinBox,
        "rawVisualizerFrequency4Spin",
    )

    raw_mode_button.click()
    app.processEvents()

    assert queue_group.isHidden()
    assert reactive_group.isHidden()
    assert not raw_group.isHidden()
    assert point_count.value() == 3
    assert fourth_frequency.isHidden()
    origin_table = window.findChild(
        QtWidgets.QTableWidget,
        "rawVisualizerOriginTable",
    )
    assert origin_table is not None
    assert origin_table.rowCount() > 0
    assert window.findChild(
        QtWidgets.QSlider,
        "rawVisualizerNoiseThresholdSlider",
    ) is not None
    raw_palette_combo = window.findChild(
        QtWidgets.QComboBox,
        "rawVisualizerPaletteCombo",
    )
    assert raw_palette_combo is not None
    assert raw_palette_combo.count() > 1
    assert window.findChild(
        QtWidgets.QComboBox,
        "rawVisualizerPaletteSetCombo",
    ) is not None
    raw_palette_pool = window.findChild(
        QtWidgets.QListWidget,
        "rawVisualizerPalettePoolList",
    )
    assert raw_palette_pool is not None
    assert raw_palette_pool.count() > 0
    assert window.findChild(
        QtWidgets.QCheckBox,
        "rawVisualizerAutoPaletteCheck",
    ) is not None
    assert window.findChild(
        QtWidgets.QLabel,
        "rawVisualizerPalettePreviewLabel",
    ) is not None
    assert window.findChild(
        QtWidgets.QPushButton,
        "rawVisualizerSavePaletteProfileButton",
    ) is not None
    assert window.findChild(
        QtWidgets.QPushButton,
        "rawVisualizerSavePaletteButton",
    ) is not None
    assert window.findChild(
        QtWidgets.QPushButton,
        "rawVisualizerSavePaletteSetButton",
    ) is not None
    window.close()


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
    show_preview_strip_mode = window.findChild(
        QtWidgets.QComboBox,
        "showSimulationStripModeCombo",
    )
    live_preview_strip_mode = window.findChild(
        QtWidgets.QComboBox,
        "simulationStripModeCombo",
    )
    routing_status = window.findChild(QtWidgets.QLabel, "routingStatusLabel")
    ready_list = window.findChild(QtWidgets.QListWidget, "capturedReadyList")
    queue_runtime_group = window.findChild(
        QtWidgets.QGroupBox, "queueRuntimeGroup"
    )
    replay_queue_group = window.findChild(
        QtWidgets.QGroupBox, "compiledCaptureReplayQueueGroup"
    )
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
    stop_capture_button = window.findChild(QtWidgets.QPushButton, "stopCaptureButton")
    switch_pipeline_button = window.findChild(QtWidgets.QPushButton, "switchPipelineButton")
    stop_output_button = window.findChild(QtWidgets.QPushButton, "stopOutputButton")
    start_reactive_button = window.findChild(QtWidgets.QPushButton, "startReactiveButton")
    output_target_combo = window.findChild(QtWidgets.QComboBox, "outputTargetCombo")
    output_device_combo = window.findChild(QtWidgets.QComboBox, "outputAudioDeviceCombo")
    input_device_combo = window.findChild(QtWidgets.QComboBox, "liveInputDeviceCombo")
    runtime_group = window.findChild(QtWidgets.QGroupBox, "runtimeRoutingGroup")
    runtime_override_group = window.findChild(QtWidgets.QGroupBox, "runtimeOverrideGroup")
    capture_group = window.findChild(QtWidgets.QGroupBox, "captureSettingsGroup")
    capture_source_combo = window.findChild(
        QtWidgets.QComboBox, "captureSourceCombo"
    )
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
    add_device_button = window.findChild(QtWidgets.QPushButton, "addDeviceButton")
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
        "Devices",
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
    assert show_preview_strip_mode is not None
    assert live_preview_strip_mode is not None
    assert show_preview_strip_mode.currentData() == "segments"
    assert live_preview_strip_mode.currentData() == "segments"
    assert show_preview_canvas.strip_render_mode() == "segments"
    show_preview_strip_mode.setCurrentIndex(
        show_preview_strip_mode.findData("bounds")
    )
    app.processEvents()
    assert live_preview_strip_mode.currentData() == "bounds"
    assert show_preview_canvas.strip_render_mode() == "bounds"
    assert routing_status is not None
    assert ready_list is not None
    assert queue_runtime_group is not None
    assert replay_queue_group is not None
    assert not queue_runtime_group.isHidden()
    assert not replay_queue_group.isHidden()
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
    assert show_cues_table.columnCount() == 19
    assert {
        index
        for index in range(show_cues_table.columnCount())
        if show_cues_table.isColumnHidden(index)
    } == {8, 9, 13, 18}
    assert [
        show_cues_table.horizontalHeaderItem(index).text()
        for index in range(8, show_cues_table.columnCount())
    ] == [
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
    assert capture_source_combo is not None
    assert not capture_source_combo.isEditable()
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
    for queue_control in (
        start_capture_button,
        stop_capture_button,
        switch_pipeline_button,
        stop_output_button,
        ready_list,
    ):
        assert is_descendant(queue_control, queue_runtime_group) or is_descendant(
            queue_control, replay_queue_group
        )
        assert not is_descendant(queue_control, tabs.widget(5))
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
    assert add_device_button is not None
    assert add_device_button.text() == "Add Device"
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
        "Ctrl+Enter",
        "Ctrl+N",
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


def test_room_layout_group_controls_scroll_into_view_and_add_group(monkeypatch):
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    QtCore = pytest.importorskip("PySide6.QtCore")
    qt_modules = require_qt()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = create_main_window(
        qt_modules,
        GuiSettings(),
        config_path=Path("dev/devices-dummy.yaml"),
    )
    window.resize(900, 600)
    window.show()
    tabs = window.centralWidget()
    tabs.setCurrentIndex(
        next(
            index
            for index in range(tabs.count())
            if tabs.tabText(index) == "Room Layout"
        )
    )
    app.processEvents()

    scroll = window.findChild(
        QtWidgets.QScrollArea,
        "spatialEditorScrollArea",
    )
    add_button = window.findChild(
        QtWidgets.QPushButton,
        "addSpatialGroupButton",
    )
    assert scroll is not None
    assert add_button is not None
    assert scroll.verticalScrollBar().maximum() > 0

    scroll.ensureWidgetVisible(add_button, 12, 12)
    app.processEvents()
    button_rect = QtCore.QRect(
        add_button.mapTo(scroll.viewport(), QtCore.QPoint(0, 0)),
        add_button.size(),
    )
    assert scroll.viewport().rect().intersects(button_rect)
    assert add_button.isEnabled()

    group_list = window.findChild(
        QtWidgets.QListWidget,
        "spatialGroupList",
    )
    status_label = window.findChild(
        QtWidgets.QLabel,
        "spatialStatusLabel",
    )
    initial_count = group_list.count()
    answers = iter((("Test Group", True), ("test-group", True)))
    monkeypatch.setattr(
        QtWidgets.QInputDialog,
        "getText",
        lambda *args, **kwargs: next(answers),
    )
    monkeypatch.setattr(
        QtWidgets.QColorDialog,
        "getColor",
        lambda *args, **kwargs: qt_modules.QtGui.QColor("#123456"),
    )
    add_button.click()
    app.processEvents()

    assert group_list.count() == initial_count + 1
    assert any(
        group_list.item(index).data(QtCore.Qt.ItemDataRole.UserRole)
        == "test-group"
        for index in range(group_list.count())
    )
    assert "Created group Test Group" in status_label.text()
    window.close()
