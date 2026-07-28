from __future__ import annotations

import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtTest import QTest

from dreamsync.gui.widgets.show_timeline_view import build_show_timeline_view


class ShowTimelineViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _view(self):
        view = build_show_timeline_view(
            SimpleNamespace(QtCore=QtCore, QtGui=QtGui, QtWidgets=QtWidgets)
        )
        view.set_timeline_data(
            duration=10.0,
            waveform=(),
            beats=(2.5, 5.0),
            downbeats=(0.0, 7.5),
            sections=(),
            cue_times=(4.0,),
            selected_cue_index=0,
        )
        return view

    def test_delete_selected_beat_emits_requested_index(self) -> None:
        view = self._view()
        deleted: list[int] = []
        view.beatDeleteRequested.connect(deleted.append)
        view._selected_marker = ("beat", 1, None)

        self.assertTrue(view.delete_selected_item())
        self.assertEqual(deleted, [1])

    def test_delete_selected_cue_emits_requested_index(self) -> None:
        view = self._view()
        deleted: list[int] = []
        view.cueDeleteRequested.connect(deleted.append)

        self.assertTrue(view.delete_selected_item())
        self.assertEqual(deleted, [0])

    def test_tab_advances_between_orange_editable_markers_only(self) -> None:
        view = self._view()
        view.resize(600, 200)
        view.show()
        view.setFocus()
        self.app.processEvents()
        view._selected_marker = ("downbeat", 0, None)
        view._selected_cue_index = None

        QTest.keyClick(view, QtCore.Qt.Key.Key_Tab)
        self.app.processEvents()

        self.assertEqual(view._selected_marker, ("downbeat", 1, None))
        self.assertTrue(view.hasFocus())
        self.assertTrue(view.focusNextPrevChild(True))
        view.hide()

    def test_alt_tab_switches_from_marker_to_nearest_cue(self) -> None:
        view = self._view()
        selected: list[int] = []
        view.cueSelected.connect(selected.append)
        view._selected_marker = ("beat", 0, None)

        view.keyPressEvent(
            QtGui.QKeyEvent(
                QtCore.QEvent.Type.KeyPress,
                QtCore.Qt.Key.Key_Tab,
                QtCore.Qt.KeyboardModifier.AltModifier,
            )
        )

        self.assertEqual(selected, [0])

    def test_ctrl_tab_is_the_local_switching_fallback(self) -> None:
        view = self._view()
        selected: list[int] = []
        view.cueSelected.connect(selected.append)
        view._selected_marker = ("beat", 0, None)

        view.keyPressEvent(
            QtGui.QKeyEvent(
                QtCore.QEvent.Type.KeyPress,
                QtCore.Qt.Key.Key_Tab,
                QtCore.Qt.KeyboardModifier.ControlModifier,
            )
        )

        self.assertEqual(selected, [0])

    def test_shift_up_switches_selection_kind(self) -> None:
        view = self._view()
        selected: list[int] = []
        view.cueSelected.connect(selected.append)
        view._selected_marker = ("beat", 0, None)

        view.keyPressEvent(
            QtGui.QKeyEvent(
                QtCore.QEvent.Type.KeyPress,
                QtCore.Qt.Key.Key_Up,
                QtCore.Qt.KeyboardModifier.ShiftModifier,
            )
        )

        self.assertEqual(selected, [0])

    def test_switch_from_cue_targets_orange_marker_and_clears_cue_selection(self) -> None:
        view = self._view()

        view.switch_selection_kind()

        self.assertEqual(view._selected_marker, ("downbeat", 1, None))
        self.assertIsNone(view._selected_cue_index)

        view.set_timeline_data(
            duration=10.0,
            waveform=(),
            beats=(2.5, 5.0),
            downbeats=(0.0, 7.5),
            sections=(),
            cue_times=(4.0,),
            selected_cue_index=0,
        )
        self.assertEqual(view._selected_marker, ("downbeat", 1, None))
        self.assertIsNone(view._selected_cue_index)

    def test_small_and_large_arrow_nudges_apply_to_selected_cue(self) -> None:
        view = self._view()
        view.resize(1000, 200)
        moved: list[tuple[int, float]] = []
        view.cueMoved.connect(lambda index, at_time: moved.append((index, at_time)))

        view.keyPressEvent(
            QtGui.QKeyEvent(
                QtCore.QEvent.Type.KeyPress,
                QtCore.Qt.Key.Key_Right,
                QtCore.Qt.KeyboardModifier.NoModifier,
            )
        )
        view.keyPressEvent(
            QtGui.QKeyEvent(
                QtCore.QEvent.Type.KeyPress,
                QtCore.Qt.Key.Key_Right,
                QtCore.Qt.KeyboardModifier.ShiftModifier,
            )
        )

        self.assertEqual(moved[0][0], 0)
        self.assertEqual(moved[1][0], 0)
        self.assertGreater(moved[1][1] - 4.0, moved[0][1] - 4.0)

    def test_repeated_tab_keeps_timeline_focus_and_only_changes_cue_selection(self) -> None:
        host = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(host)
        view = build_show_timeline_view(
            SimpleNamespace(QtCore=QtCore, QtGui=QtGui, QtWidgets=QtWidgets)
        )
        focus_thief = QtWidgets.QDoubleSpinBox()
        layout.addWidget(view)
        layout.addWidget(focus_thief)
        view.set_timeline_data(
            duration=10.0,
            waveform=(),
            beats=(2.5, 5.0),
            downbeats=(0.0, 7.5),
            sections=(),
            cue_times=(1.0, 4.0, 8.0),
            selected_cue_index=0,
        )
        selected: list[int] = []
        moved: list[tuple[int, float]] = []

        def _mirror_table_selection(index: int) -> None:
            selected.append(index)
            focus_thief.setFocus()

        view.cueSelected.connect(_mirror_table_selection)
        view.cueMoved.connect(lambda index, at_time: moved.append((index, at_time)))
        host.show()
        view.setFocus()
        self.app.processEvents()

        QTest.keyClick(view, QtCore.Qt.Key.Key_Tab)
        self.app.processEvents()
        self.assertTrue(view.hasFocus())
        QTest.keyClick(view, QtCore.Qt.Key.Key_Tab)
        self.app.processEvents()
        QTest.keyClick(
            view,
            QtCore.Qt.Key.Key_Tab,
            QtCore.Qt.KeyboardModifier.ShiftModifier,
        )
        self.app.processEvents()

        self.assertEqual(selected, [1, 2, 1])
        self.assertEqual(moved, [])
        self.assertTrue(view.hasFocus())
        host.hide()

    def test_space_emits_local_play_pause_request(self) -> None:
        view = self._view()
        requests: list[bool] = []
        view.playPauseRequested.connect(lambda: requests.append(True))

        view.keyPressEvent(
            QtGui.QKeyEvent(
                QtCore.QEvent.Type.KeyPress,
                QtCore.Qt.Key.Key_Space,
                QtCore.Qt.KeyboardModifier.NoModifier,
            )
        )

        self.assertEqual(requests, [True])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
