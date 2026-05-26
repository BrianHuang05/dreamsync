from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from dreamsync.gui.main_window import _format_spatial_details, _format_spatial_summary, create_main_window
from dreamsync.gui.controllers.spatial_controller import SpatialController
from dreamsync.gui.models.spatial_scene import SceneNode
from dreamsync.gui.models.spatial_scene import ProjectionConfig, project_point
from dreamsync.gui.services.device_service import DeviceService
from dreamsync.gui.settings import GuiSettings
from dreamsync.gui.qt import require_qt
from dreamsync.gui.widgets.spatial_canvas import _cycle_axis_name, _cycle_view_mode, _project_node_for_view, _spatial_hint_text, _visible_axes_for_view


def test_projection_is_deterministic():
    config = ProjectionConfig(origin_x=100.0, origin_y=200.0, scale_x=50.0, scale_y=40.0, depth_x=10.0, depth_y=5.0)

    assert project_point(1.0, 1.0, 1.0, config=config) == (160.0, 165.0)


def test_plane_projection_modes_are_deterministic():
    config = ProjectionConfig(origin_x=100.0, origin_y=200.0, scale_x=50.0, scale_y=40.0, depth_x=10.0, depth_y=5.0)
    node = SceneNode(key="node", label="Node", x=0.5, y=0.25, z=-0.75)

    assert _project_node_for_view(node, view_mode="xy", config=config) == (125.0, 190.0)
    assert _project_node_for_view(node, view_mode="xz", config=config) == (125.0, 230.0)
    assert _project_node_for_view(node, view_mode="yz", config=config) == (62.5, 190.0)


def test_visible_axes_for_view_modes():
    assert _visible_axes_for_view("xy") == ("x", "y")
    assert _visible_axes_for_view("xz") == ("x", "z")
    assert _visible_axes_for_view("yz") == ("z", "y")
    assert _visible_axes_for_view("room") is None


def test_cycle_axis_name_advances_in_xyz_order():
    assert _cycle_axis_name("x") == "y"
    assert _cycle_axis_name("y") == "z"
    assert _cycle_axis_name("z") == "x"
    assert _cycle_axis_name("invalid") == "x"


def test_cycle_view_mode_advances_in_room_plane_order():
    assert _cycle_view_mode("room") == "xy"
    assert _cycle_view_mode("xy") == "xz"
    assert _cycle_view_mode("xz") == "yz"
    assert _cycle_view_mode("yz") == "room"
    assert _cycle_view_mode("invalid") == "room"


def test_spatial_controller_round_trip(tmp_path: Path):
    path = tmp_path / "devices.yaml"
    path.write_text(
        textwrap.dedent(
            """
            devices:
              - name: Desk Left
                address: 10.0.0.10
                x: -0.5
                y: 0.25
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    controller = SpatialController(DeviceService())
    nodes = controller.load(path)
    assert nodes[0].x == -0.5

    controller.update_position("10.0.0.10", x=0.2, y=-0.4, z=0.8)
    controller.save()

    reloaded = controller.load(path)
    assert reloaded[0].x == 0.2
    assert reloaded[0].y == -0.4
    assert reloaded[0].z == 0.8


def test_spatial_summary_reports_default_origin(tmp_path: Path):
    path = tmp_path / "devices.yaml"
    path.write_text(
        textwrap.dedent(
            """
            devices:
              - name: Desk Left
                address: 10.0.0.10
                segments: 24
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    scene_entries = DeviceService().load_scene(path)

    summary = _format_spatial_summary(scene_entries, path)
    details = _format_spatial_details(scene_entries)

    assert "Loaded 1 device(s)" in summary
    assert "default origin" in summary
    assert "[default placement]" in details


def test_spatial_hint_text_warns_about_origin_overlap():
    nodes = [SceneNode(key="dummy", label="Dummy Strip", x=0.0, y=0.0, z=0.0)]
    assert "default origin" in _spatial_hint_text(nodes)


def test_spatial_tab_shows_loaded_dummy_device_details():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    window = create_main_window(
        require_qt(),
        GuiSettings(),
        config_path=Path("dev/devices-dummy.yaml"),
    )

    summary = window.findChild(QtWidgets.QLabel, "spatialSummaryLabel")
    details = window.findChild(QtWidgets.QPlainTextEdit, "spatialDetailsBox")
    view_combo = window.findChild(QtWidgets.QComboBox, "spatialViewCombo")

    assert summary is not None
    assert details is not None
    assert "Loaded 1 physical device(s)" in summary.text()
    assert "Dummy Strip" in details.toPlainText()
    assert "default placement" in details.toPlainText()
    assert view_combo is not None
    assert view_combo.currentData() == "room"

    if app is not None and QtWidgets.QApplication.instance() is app:
        window.close()


def test_spatial_tab_expands_real_strip_sections():
    scene_entries = DeviceService().load_scene(Path("dev/devices.yaml"))

    assert len(scene_entries) > 10
    assert any(entry.is_section for entry in scene_entries)
    assert any(entry.physical_name.startswith("Couch strip") for entry in scene_entries)


def test_spatial_canvas_shortcuts_cycle_axis_step_selection_nudge_and_change_view(tmp_path: Path):
    QtCore = pytest.importorskip("PySide6.QtCore")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    QtTest = pytest.importorskip("PySide6.QtTest")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    path = tmp_path / "devices.yaml"
    path.write_text(
        textwrap.dedent(
            """
            devices:
              - name: Left
                address: 10.0.0.10
                x: -0.5
                y: 0.0
                z: 0.0
              - name: Right
                address: 10.0.0.11
                x: 0.5
                y: 0.0
                z: 0.0
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    window = create_main_window(require_qt(), GuiSettings(), config_path=path)
    window.show()
    app.processEvents()

    spatial_canvas = window.findChild(QtWidgets.QWidget, "spatialCanvas")
    selected_label = window.findChild(QtWidgets.QLabel, "selectedSpatialLabel")
    spatial_node_list = window.findChild(QtWidgets.QListWidget, "spatialNodeList")
    spatial_view_combo = window.findChild(QtWidgets.QComboBox, "spatialViewCombo")
    spatial_x_spin = window.findChild(QtWidgets.QDoubleSpinBox, "spatialXSpin")
    spatial_z_spin = window.findChild(QtWidgets.QDoubleSpinBox, "spatialZSpin")
    axis_buttons = {
        button.text().lower(): button
        for button in window.findChildren(QtWidgets.QRadioButton)
        if button.text() in {"X", "Y", "Z"}
    }

    assert spatial_canvas is not None
    assert selected_label is not None
    assert spatial_node_list is not None
    assert spatial_view_combo is not None
    assert spatial_x_spin is not None
    assert spatial_z_spin is not None
    assert axis_buttons["x"].isChecked()
    assert "Left" in selected_label.text()
    assert spatial_node_list.selectedItems()
    assert spatial_node_list.selectedItems()[0].text() == "Left"

    left_x, left_y = project_point(-0.5, 0.0, 0.0)
    QtTest.QTest.mouseClick(
        spatial_canvas,
        QtCore.Qt.MouseButton.RightButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
        QtCore.QPoint(int(left_x), int(left_y)),
    )
    app.processEvents()

    assert axis_buttons["y"].isChecked()
    assert "Left" in selected_label.text()

    spatial_canvas.setFocus()
    QtTest.QTest.keyClick(
        spatial_canvas,
        QtCore.Qt.Key.Key_Tab,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )
    app.processEvents()

    assert "Right" in selected_label.text()
    assert spatial_node_list.selectedItems()
    assert spatial_node_list.selectedItems()[0].text() == "Right"

    QtTest.QTest.keyClick(
        spatial_canvas,
        QtCore.Qt.Key.Key_Tab,
        QtCore.Qt.KeyboardModifier.ShiftModifier,
    )
    app.processEvents()

    assert "Left" in selected_label.text()
    assert spatial_node_list.selectedItems()
    assert spatial_node_list.selectedItems()[0].text() == "Left"

    QtTest.QTest.keyClick(
        spatial_canvas,
        QtCore.Qt.Key.Key_A,
        QtCore.Qt.KeyboardModifier.ControlModifier,
    )
    app.processEvents()

    assert axis_buttons["z"].isChecked()
    assert "Left" in selected_label.text()

    QtTest.QTest.keyClick(
        spatial_canvas,
        QtCore.Qt.Key.Key_Right,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )
    app.processEvents()

    assert spatial_x_spin.value() == pytest.approx(-0.5)
    assert "Left" in selected_label.text()
    assert spatial_z_spin.value() == pytest.approx(0.05)

    QtTest.QTest.keyClick(
        spatial_canvas,
        QtCore.Qt.Key.Key_Left,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )
    app.processEvents()

    assert spatial_z_spin.value() == pytest.approx(0.0)

    for expected_view in ("xy", "xz", "yz", "room"):
        QtTest.QTest.keyClick(
            spatial_canvas,
            QtCore.Qt.Key.Key_V,
            QtCore.Qt.KeyboardModifier.ControlModifier,
        )
        app.processEvents()
        assert spatial_view_combo.currentData() == expected_view

    if app is not None and QtWidgets.QApplication.instance() is app:
        window.close()
