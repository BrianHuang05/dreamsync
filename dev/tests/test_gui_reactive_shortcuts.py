from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from dreamsync.gui.main_window import create_main_window
from dreamsync.gui.qt import require_qt
from dreamsync.gui.settings import GuiSettings, GuiSettingsStore
from dreamsync.profile import BUILTIN_PROFILES_DIR


class ReactiveShortcutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.settings_path = Path(self.temp_dir.name) / "gui-settings.json"
        self.window = create_main_window(
            require_qt(),
            GuiSettings(),
            config_path=Path("dev/devices-dummy.yaml"),
            settings_store=GuiSettingsStore(self.settings_path),
        )
        self.window.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.window.close()
        self.app.processEvents()
        self.temp_dir.cleanup()

    def _tab(self, name: str):
        tabs = self.window.centralWidget()
        for index in range(tabs.count()):
            if tabs.tabText(index) == name:
                tabs.setCurrentIndex(index)
                self.app.processEvents()
                return tabs.widget(index)
        self.fail(f"Missing tab: {name}")

    def test_invalid_override_warns_inline_and_on_config_save_shortcut(self) -> None:
        self._tab("Live")
        self.window.findChild(
            QtWidgets.QPushButton,
            "reactiveLiveModeButton",
        ).click()
        strategy = self.window.findChild(
            QtWidgets.QComboBox,
            "reactiveProfileStrategyCombo",
        )
        profile_label = self.window.findChild(
            QtWidgets.QLabel,
            "reactiveProfileOverrideLabel",
        )
        warning = self.window.findChild(
            QtWidgets.QLabel,
            "reactiveConfigurationWarningLabel",
        )
        save_button = self.window.findChild(
            QtWidgets.QPushButton,
            "saveConfigurationButton",
        )

        strategy.setCurrentIndex(strategy.findData("override_profile"))
        self.assertEqual(profile_label.property("profilePath"), "")
        self.app.processEvents()

        self.assertTrue(warning.isVisible())
        self.assertIn("Choose a reactive profile override", warning.text())
        self.assertEqual(save_button.text(), "Save Configuration")

        config_widget = self._tab("Config")
        config_widget.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        config_widget.setFocus()
        self.app.processEvents()
        with patch.object(QtWidgets.QMessageBox, "warning") as warning_dialog:
            QtTest.QTest.keyClick(
                config_widget,
                QtCore.Qt.Key.Key_S,
                QtCore.Qt.KeyboardModifier.ControlModifier,
            )
            self.app.processEvents()

        warning_dialog.assert_called_once()
        self.assertFalse(self.settings_path.exists())

    def test_override_profile_uses_file_picker_from_profile_directory(self) -> None:
        self._tab("Live")
        self.window.findChild(
            QtWidgets.QPushButton,
            "reactiveLiveModeButton",
        ).click()
        strategy = self.window.findChild(
            QtWidgets.QComboBox,
            "reactiveProfileStrategyCombo",
        )
        profile_directory_edit = self.window.findChild(
            QtWidgets.QLineEdit,
            "profileDirectoryEdit",
        )
        browse_button = self.window.findChild(
            QtWidgets.QPushButton,
            "browseReactiveProfileButton",
        )
        profile_label = self.window.findChild(
            QtWidgets.QLabel,
            "reactiveProfileOverrideLabel",
        )
        profile_directory = Path("src/dreamsync/profiles").resolve()
        selected_profile = profile_directory / "aurora.yaml"
        profile_directory_edit.setText(str(profile_directory))
        strategy.setCurrentIndex(strategy.findData("override_profile"))
        self.app.processEvents()

        with patch.object(
            QtWidgets.QFileDialog,
            "getOpenFileName",
            return_value=(str(selected_profile), "YAML Profile Files (*.yaml *.yml)"),
        ) as file_dialog:
            browse_button.click()
            self.app.processEvents()

        self.assertEqual(file_dialog.call_args.args[2], str(profile_directory))
        self.assertEqual(profile_label.property("profilePath"), str(selected_profile))
        self.assertEqual(profile_label.text(), str(selected_profile))

    def test_override_profile_picker_falls_back_to_builtin_directory(self) -> None:
        self._tab("Live")
        self.window.findChild(
            QtWidgets.QPushButton,
            "reactiveLiveModeButton",
        ).click()
        strategy = self.window.findChild(
            QtWidgets.QComboBox,
            "reactiveProfileStrategyCombo",
        )
        profile_directory_edit = self.window.findChild(
            QtWidgets.QLineEdit,
            "profileDirectoryEdit",
        )
        browse_button = self.window.findChild(
            QtWidgets.QPushButton,
            "browseReactiveProfileButton",
        )
        profile_directory_edit.clear()
        strategy.setCurrentIndex(strategy.findData("override_profile"))
        self.app.processEvents()

        with patch.object(
            QtWidgets.QFileDialog,
            "getOpenFileName",
            return_value=("", ""),
        ) as file_dialog:
            browse_button.click()

        self.assertEqual(file_dialog.call_args.args[2], str(BUILTIN_PROFILES_DIR))

    def test_space_reaches_reactive_page_without_button_focus(self) -> None:
        live_widget = self._tab("Live")
        strategy = self.window.findChild(
            QtWidgets.QComboBox,
            "reactiveProfileStrategyCombo",
        )
        strategy.setCurrentIndex(strategy.findData("override_profile"))
        self.app.processEvents()

        reactive_mode_button = self.window.findChild(
            QtWidgets.QPushButton,
            "reactiveLiveModeButton",
        )
        reactive_mode_button.click()
        live_widget.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        live_widget.setFocus()
        self.app.processEvents()

        with patch.object(QtWidgets.QMessageBox, "warning") as warning_dialog:
            QtTest.QTest.keyClick(live_widget, QtCore.Qt.Key.Key_Space)
            self.app.processEvents()

        warning_dialog.assert_called_once()

    def test_live_effect_tempo_buttons_and_hotkeys_select_bpm_multiple(
        self,
    ) -> None:
        live_widget = self._tab("Live")
        self.window.findChild(
            QtWidgets.QPushButton,
            "reactiveLiveModeButton",
        ).click()
        label = self.window.findChild(
            QtWidgets.QLabel,
            "reactiveEffectTempoLabel",
        )
        half_button = self.window.findChild(
            QtWidgets.QPushButton,
            "reactiveEffectTempoHalfButton",
        )
        self.assertIsNotNone(label)
        self.assertIsNotNone(half_button)

        half_button.click()
        self.assertIn("0.5× cycle BPM", label.text())

        live_widget.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        live_widget.setFocus()
        QtTest.QTest.keyClick(
            live_widget,
            QtCore.Qt.Key.Key_BracketRight,
        )
        self.app.processEvents()
        self.assertIn("2× cycle BPM", label.text())

        QtTest.QTest.keyClick(
            live_widget,
            QtCore.Qt.Key.Key_Backslash,
        )
        self.app.processEvents()
        self.assertIn("1× cycle BPM", label.text())

        cycle_label = self.window.findChild(
            QtWidgets.QLabel,
            "reactiveCycleTempoLabel",
        )
        cycle_half = self.window.findChild(
            QtWidgets.QPushButton,
            "reactiveCycleTempoHalfButton",
        )
        cycle_normal = self.window.findChild(
            QtWidgets.QPushButton,
            "reactiveCycleTempoNormalButton",
        )
        cycle_double = self.window.findChild(
            QtWidgets.QPushButton,
            "reactiveCycleTempoDoubleButton",
        )
        self.assertIsNotNone(cycle_half)
        self.assertIsNotNone(cycle_normal)
        self.assertIsNotNone(cycle_double)

        cycle_half.click()
        self.assertIn("0.5× detector", cycle_label.text())
        cycle_normal.click()
        self.assertIn("1× detector", cycle_label.text())
        cycle_double.click()
        self.assertIn("2× detector", cycle_label.text())
        cycle_normal.click()
        self.assertIn("1× detector", cycle_label.text())

        QtTest.QTest.keyClick(
            live_widget,
            QtCore.Qt.Key.Key_BracketLeft,
            QtCore.Qt.KeyboardModifier.ShiftModifier,
        )
        self.app.processEvents()
        self.assertIn("0.5× detector", cycle_label.text())

        QtTest.QTest.keyClick(
            live_widget,
            QtCore.Qt.Key.Key_BracketRight,
            QtCore.Qt.KeyboardModifier.ShiftModifier,
        )
        self.app.processEvents()
        self.assertIn("1× detector", cycle_label.text())

        QtTest.QTest.keyClick(
            live_widget,
            QtCore.Qt.Key.Key_BracketRight,
            QtCore.Qt.KeyboardModifier.ShiftModifier,
        )
        self.app.processEvents()
        self.assertIn("2× detector", cycle_label.text())

    def test_downbeat_nudge_control_and_live_hotkey_are_exposed(self) -> None:
        live_widget = self._tab("Live")
        self.window.findChild(
            QtWidgets.QPushButton,
            "reactiveLiveModeButton",
        ).click()
        button = self.window.findChild(
            QtWidgets.QPushButton,
            "reactiveDownbeatNudgeButton",
        )

        self.assertIsNotNone(button)
        self.assertIn("Downbeat nearest", button.text())
        self.assertIn("nearest the keypress", button.toolTip())
        self.assertFalse(button.isEnabled())
        manual_shortcuts = {
            shortcut.key().toString(): shortcut
            for shortcut in self.window._dreamsync_live_shortcuts
            if shortcut.key().toString() in {"D", "S", "N"}
        }
        self.assertEqual(set(manual_shortcuts), {"D", "S", "N"})
        self.assertTrue(
            all(
                not shortcut.autoRepeat()
                for shortcut in manual_shortcuts.values()
            )
        )

        live_widget.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        live_widget.setFocus()
        QtTest.QTest.keyClick(live_widget, QtCore.Qt.Key.Key_D)
        self.app.processEvents()

        self.assertTrue(
            any(
                "Start Reactive listening before registering manual beats"
                in label.text()
                for label in self.window.findChildren(QtWidgets.QLabel)
            )
        )

        QtTest.QTest.keyClick(live_widget, QtCore.Qt.Key.Key_S)
        self.app.processEvents()
        self.assertTrue(
            any(
                "Start Reactive listening before registering manual beats"
                in label.text()
                for label in self.window.findChildren(QtWidgets.QLabel)
            )
        )

        QtTest.QTest.keyClick(live_widget, QtCore.Qt.Key.Key_N)
        self.app.processEvents()
        self.assertTrue(
            any(
                "Start Reactive listening before resetting beat detection"
                in label.text()
                for label in self.window.findChildren(QtWidgets.QLabel)
            )
        )

    def test_waveform_accepts_snapped_manual_beat_overlay(self) -> None:
        self._tab("Live")
        view = self.window.findChild(
            QtWidgets.QWidget,
            "reactiveWaveformView",
        )

        view.set_diagnostic_data(
            ((0.0, 0.1), (1.0, 0.2)),
            (0.5, 1.0),
            downbeats=(0.5,),
            manual_beats=(
                {"t": 0.5, "kind": "downbeat", "beat_index": 8},
                {"t": 1.0, "kind": "beat", "beat_index": 9},
            ),
        )

        self.assertEqual(
            view._manual_beats,
            ((0.5, "downbeat"), (1.0, "beat")),
        )
        self.assertEqual(view._downbeats, (0.5,))
        self.assertIn("Automatic beats are orange", view.toolTip())
        self.assertIn("automatic downbeats are red", view.toolTip())
        self.assertIn("Manual S beats are blue", view.toolTip())
        self.assertIn("manual D downbeats are green", view.toolTip())

    def test_valid_configuration_ctrl_s_persists_settings(self) -> None:
        config_widget = self._tab("Config")
        config_widget.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        config_widget.setFocus()

        QtTest.QTest.keyClick(
            config_widget,
            QtCore.Qt.Key.Key_S,
            QtCore.Qt.KeyboardModifier.ControlModifier,
        )
        self.app.processEvents()

        self.assertTrue(self.settings_path.exists())
        status = self.window.findChild(
            QtWidgets.QLabel,
            "configurationSaveStatusLabel",
        )
        self.assertEqual(status.text(), "Configuration saved.")
        toast = self.window.findChild(
            QtWidgets.QFrame,
            "dreamsyncSuccessToast",
        )
        self.assertIsNotNone(toast)
        self.assertTrue(toast.isVisible())
        self.assertTrue(self.window._dreamsync_success_toast_timer.isActive())

    def test_dark_mode_applies_immediately_and_persists_with_configuration(self) -> None:
        config_widget = self._tab("Config")
        dark_mode = self.window.findChild(QtWidgets.QCheckBox, "darkModeCheck")
        self.assertIsNotNone(dark_mode)

        dark_mode.setChecked(True)
        self.app.processEvents()
        self.assertEqual(
            self.app.palette().color(QtGui.QPalette.ColorRole.Window).name(),
            "#121212",
        )
        config_widget.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        config_widget.setFocus()
        QtTest.QTest.keyClick(
            config_widget,
            QtCore.Qt.Key.Key_S,
            QtCore.Qt.KeyboardModifier.ControlModifier,
        )
        self.app.processEvents()

        self.assertTrue(GuiSettingsStore(self.settings_path).load().dark_mode)

    def test_reactive_live_look_and_panel_preferences_persist_on_close(
        self,
    ) -> None:
        self._tab("Live")
        self.window.findChild(
            QtWidgets.QPushButton,
            "reactiveLiveModeButton",
        ).click()
        color_profile = self.window.findChild(
            QtWidgets.QComboBox,
            "reactiveLiveColorProfileCombo",
        )
        active_effect = self.window.findChild(
            QtWidgets.QComboBox,
            "reactiveLiveActiveEffectCombo",
        )
        chord_panel = self.window.findChild(
            QtWidgets.QCheckBox,
            "reactiveChordPanelVisibleCheck",
        )
        dark_mode = self.window.findChild(
            QtWidgets.QCheckBox,
            "darkModeCheck",
        )
        ripple = self.window.findChild(
            QtWidgets.QToolButton,
            "reactiveLiveEffectRippleButton",
        )
        spatial_control = self.window.findChild(
            QtWidgets.QGroupBox,
            "reactiveLiveLookGroup",
        ).findChild(
            QtWidgets.QWidget,
            "runtimeSpatialPresetEdit",
        )

        color_profile.setCurrentIndex(color_profile.findData("neon"))
        active_effect.setCurrentIndex(active_effect.findData("ripple"))
        chord_panel.setChecked(False)
        dark_mode.setChecked(True)
        for button in self.window.findChild(
            QtWidgets.QGroupBox,
            "reactiveLiveLookGroup",
        ).findChildren(QtWidgets.QToolButton):
            button.setChecked(button is ripple)
        self.app.processEvents()
        palette_preview = self.window.findChild(
            QtWidgets.QLabel,
            "reactiveActivePalettePreviewLabel",
        )
        self.assertIsNotNone(palette_preview)
        self.assertIn("#ff00ff", palette_preview.toolTip().lower())
        self.assertIn("●", palette_preview.text())
        self.window.close()
        self.app.processEvents()

        saved = GuiSettingsStore(self.settings_path).load()
        self.assertEqual(saved.reactive_live_color_profile, "neon")
        self.assertEqual(saved.reactive_live_active_effect, "ripple")
        self.assertEqual(saved.reactive_live_effect_bank, ("ripple",))
        self.assertFalse(saved.reactive_chord_panel_visible)
        self.assertTrue(saved.dark_mode)
        self.assertTrue(saved.window_geometry)
        self.assertEqual(
            len(saved.reactive_diagnostics_splitter_sizes),
            3,
        )
        self.assertIsNone(spatial_control)

    def test_ctrl_o_opens_the_saved_show_picker_from_shows(self) -> None:
        shows_widget = self._tab("Shows")
        shows_widget.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        shows_widget.setFocus()
        self.app.processEvents()

        with patch.object(
            QtWidgets.QFileDialog,
            "getOpenFileName",
            return_value=("", ""),
        ) as file_dialog:
            QtTest.QTest.keyClick(
                shows_widget,
                QtCore.Qt.Key.Key_O,
                QtCore.Qt.KeyboardModifier.ControlModifier,
            )
            self.app.processEvents()

        self.assertTrue(file_dialog.called)
        self.assertEqual(file_dialog.call_args.args[1], "Load Saved Show")
        open_show_button = self.window.findChild(QtWidgets.QPushButton, "loadShowButton")
        self.assertIn("Ctrl+O", open_show_button.toolTip())

    def test_show_routing_group_collapses_its_defaults_and_overrides(self) -> None:
        self._tab("Shows")
        routing_group = self.window.findChild(QtWidgets.QGroupBox, "showRoutingGroup")
        routing_host = self.window.findChild(QtWidgets.QWidget, "showRoutingHost")
        self.assertIsNotNone(routing_group)
        self.assertIsNotNone(routing_host)
        self.assertTrue(routing_host.isVisible())

        routing_group.setChecked(False)
        self.app.processEvents()

        self.assertFalse(routing_host.isVisible())

    def test_escape_from_file_location_applies_and_saves_then_defocuses(self) -> None:
        self._tab("Config")
        profile_directory = self.window.findChild(
            QtWidgets.QLineEdit,
            "profileDirectoryEdit",
        )
        self.assertIsNotNone(profile_directory)
        target_directory = Path(self.temp_dir.name) / "profiles"
        profile_directory.setText(str(target_directory))
        profile_directory.setFocus()
        self.app.processEvents()

        QtTest.QTest.keyClick(profile_directory, QtCore.Qt.Key.Key_Escape)
        self.app.processEvents()

        self.assertEqual(profile_directory.text(), str(target_directory))
        self.assertFalse(profile_directory.hasFocus())
        self.assertTrue(self.settings_path.exists())
        saved = GuiSettingsStore(self.settings_path).load()
        self.assertEqual(saved.profile_directory, str(target_directory))

    def test_escape_defocuses_show_text_and_space_stays_text_input_safe(self) -> None:
        self._tab("Shows")
        title_edit = self.window.findChild(QtWidgets.QLineEdit, "showTitleEdit")
        self.assertIsNotNone(title_edit)
        title_edit.setText("Draft")
        title_edit.setFocus()
        self.app.processEvents()

        QtTest.QTest.keyClick(title_edit, QtCore.Qt.Key.Key_Space)
        self.app.processEvents()
        self.assertEqual(title_edit.text(), "Draft ")

        QtTest.QTest.keyClick(title_edit, QtCore.Qt.Key.Key_Escape)
        self.app.processEvents()
        self.assertEqual(title_edit.text(), "Draft ")
        self.assertFalse(title_edit.hasFocus())


if __name__ == "__main__":
    unittest.main()
