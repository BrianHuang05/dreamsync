from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from dreamsync.gui.controllers.spatial_controller import SpatialController
from dreamsync.gui.main_window import create_main_window
from dreamsync.gui.models.spatial_scene import (
    SceneNode,
    chain_center,
    is_cardinal_step,
    validate_chain,
)
from dreamsync.gui.qt import require_qt
from dreamsync.gui.services.device_service import DeviceService
from dreamsync.gui.settings import GuiSettings


def _chain(count: int, *, address: str = "strip") -> list[SceneNode]:
    return [
        SceneNode(
            key=f"{address}#section:{index}",
            label=f"Strip [{index + 1}/{count}]",
            x=0.0,
            y=0.0,
            z=0.0,
            address=address,
            physical_name="Strip",
            section_index=index,
            section_count=count,
            is_section=True,
        )
        for index in range(count)
    ]


def test_validate_chain_accepts_cardinal_wrap_and_rejects_stretched_link():
    nodes = _chain(4)
    nodes[1] = SceneNode(**{**nodes[1].__dict__, "x": 0.05})
    nodes[2] = SceneNode(**{**nodes[2].__dict__, "x": 0.05, "y": 0.05})
    nodes[3] = SceneNode(**{**nodes[3].__dict__, "x": 0.10, "y": 0.05})

    assert validate_chain(nodes).valid

    nodes[3] = SceneNode(**{**nodes[3].__dict__, "x": 0.50})
    validation = validate_chain(nodes)
    assert not validation.valid
    assert validation.invalid_links == ((nodes[2].key, nodes[3].key),)


def test_even_count_orientation_centers_chain_and_preserves_adjacency():
    controller = SpatialController(DeviceService())
    controller.nodes = {node.key: node for node in _chain(4)}

    oriented = controller.orient_chain("strip", "y+", center=(0.25, -0.10, 0.40))

    assert chain_center(oriented) == pytest.approx((0.25, -0.10, 0.40))
    assert [node.y for node in oriented] == pytest.approx((-0.175, -0.125, -0.075, -0.025))
    assert all(is_cardinal_step(first, second) for first, second in zip(oriented, oriented[1:]))


def test_move_chain_preserves_links_and_rejects_out_of_bounds_move():
    controller = SpatialController(DeviceService())
    controller.nodes = {node.key: node for node in _chain(3)}
    controller.orient_chain("strip", "x+")

    moved = controller.move_chain("strip", 0.25, 0.10, -0.20)

    assert validate_chain(moved).valid
    with pytest.raises(ValueError, match="outside the room bounds"):
        controller.move_chain("strip", 1.0, 0.0, 0.0)


def test_gui_blocks_invalid_strip_save_then_saves_oriented_line(tmp_path: Path):
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    path = tmp_path / "devices.yaml"
    original = (
        textwrap.dedent(
            """
            devices:
              - name: Test Strip
                address: 10.0.0.20
                protocol: govee_lan
                segments: 3
            """
        ).strip()
        + "\n"
    )
    path.write_text(original, encoding="utf-8")

    window = create_main_window(require_qt(), GuiSettings(), config_path=path)
    save_button = next(
        button
        for button in window.findChildren(QtWidgets.QPushButton)
        if button.text() == "Save Layout"
    )
    apply_button = window.findChild(QtWidgets.QPushButton, "applySpatialLineButton")
    validation_label = window.findChild(QtWidgets.QLabel, "spatialValidationLabel")
    status_label = window.findChild(QtWidgets.QLabel, "spatialStatusLabel")

    assert apply_button is not None
    assert validation_label is not None
    assert status_label is not None
    assert "Layout invalid" in validation_label.text()

    save_button.click()
    app.processEvents()
    assert "Save blocked" in status_label.text()
    assert path.read_text(encoding="utf-8") == original

    apply_button.click()
    app.processEvents()
    assert "Layout valid" in validation_label.text()

    save_button.click()
    app.processEvents()
    saved_nodes = DeviceService().load_scene(path)
    assert len(saved_nodes) == 3
    assert all(
        is_cardinal_step(first, second)
        for first, second in zip(saved_nodes, saved_nodes[1:])
    )

    window.close()


def test_device_service_preserves_explicit_sections_without_segments(tmp_path: Path):
    path = tmp_path / "devices.yaml"
    path.write_text(
        textwrap.dedent(
            """
            devices:
              - name: Legacy Strip
                address: legacy
                protocol: segment
                sections:
                  - index: 0
                    x: 0.0
                    y: 0.0
                  - index: 1
                    x: 0.05
                    y: 0.0
                  - index: 2
                    x: 0.10
                    y: 0.0
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    service = DeviceService()

    nodes = service.load_scene(path)
    service.save_scene(path, {node.key: (node.x, node.y, node.z) for node in nodes})
    reloaded = service.load_scene(path)

    assert len(nodes) == 3
    assert len(reloaded) == 3
    assert all(node.section_count == 3 for node in reloaded)


def test_strip_keyboard_workflow_groups_tabs_and_toggles_fine_tune(tmp_path: Path):
    QtCore = pytest.importorskip("PySide6.QtCore")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    QtTest = pytest.importorskip("PySide6.QtTest")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    path = tmp_path / "devices.yaml"
    path.write_text(
        textwrap.dedent(
            """
            devices:
              - name: Strip A
                address: strip-a
                protocol: segment
                segments: 3
              - name: Strip B
                address: strip-b
                protocol: segment
                segments: 3
              - name: Bulb
                address: bulb
                protocol: bulb
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    window = create_main_window(require_qt(), GuiSettings(), config_path=path)
    window.show()
    app.processEvents()
    canvas = window.findChild(QtWidgets.QWidget, "spatialCanvas")
    node_list = window.findChild(QtWidgets.QListWidget, "spatialNodeList")
    mode_combo = window.findChild(QtWidgets.QComboBox, "spatialEditModeCombo")
    direction_combo = window.findChild(QtWidgets.QComboBox, "spatialDirectionCombo")
    direction_widget = window.findChild(QtWidgets.QWidget, "spatialDirectionWidget")
    selected_label = window.findChild(QtWidgets.QLabel, "selectedSpatialLabel")
    status_label = window.findChild(QtWidgets.QLabel, "spatialStatusLabel")

    assert canvas is not None
    assert node_list is not None
    assert mode_combo is not None
    assert direction_combo is not None
    assert direction_widget is not None
    assert selected_label is not None
    assert status_label is not None
    assert node_list.count() == 3
    assert node_list.item(0).text() == "Strip A [3 sections]"
    assert not direction_widget.isVisible()

    canvas.setFocus()
    QtTest.QTest.keyClick(canvas, QtCore.Qt.Key.Key_M)
    app.processEvents()
    assert mode_combo.currentData() == "orient_strip"
    assert direction_widget.isVisible()

    initial_direction = direction_combo.currentData()
    QtTest.QTest.keyClick(canvas, QtCore.Qt.Key.Key_D)
    app.processEvents()
    assert direction_combo.currentData() != initial_direction

    QtTest.QTest.keyClick(canvas, QtCore.Qt.Key.Key_L)
    app.processEvents()
    QtTest.QTest.keyClick(canvas, QtCore.Qt.Key.Key_Tab)
    app.processEvents()
    assert "Strip B" in selected_label.text()

    QtTest.QTest.keyClick(canvas, QtCore.Qt.Key.Key_L)
    app.processEvents()
    QtTest.QTest.keyClick(
        canvas,
        QtCore.Qt.Key.Key_S,
        QtCore.Qt.KeyboardModifier.ControlModifier,
    )
    app.processEvents()
    assert "Saved spatial layout" in status_label.text()

    QtTest.QTest.keyClick(canvas, QtCore.Qt.Key.Key_Return)
    app.processEvents()
    assert mode_combo.currentData() == "fine_tune"
    assert node_list.count() == 7
    assert not direction_widget.isVisible()

    QtTest.QTest.keyClick(canvas, QtCore.Qt.Key.Key_Tab)
    app.processEvents()
    assert "[2/3]" in selected_label.text()

    QtTest.QTest.keyClick(canvas, QtCore.Qt.Key.Key_Return)
    app.processEvents()
    assert mode_combo.currentData() == "orient_strip"
    assert node_list.count() == 3
    assert direction_widget.isVisible()

    QtTest.QTest.keyClick(
        canvas,
        QtCore.Qt.Key.Key_R,
        QtCore.Qt.KeyboardModifier.ControlModifier,
    )
    app.processEvents()
    assert "reloaded from disk" in status_label.text()
    window.close()


def test_grouped_spatial_list_includes_counter_strip():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = create_main_window(
        require_qt(),
        GuiSettings(),
        config_path=Path("dev/devices.yaml"),
    )
    node_list = window.findChild(QtWidgets.QListWidget, "spatialNodeList")

    assert node_list is not None
    labels = [node_list.item(index).text() for index in range(node_list.count())]
    assert "Counter strip (H617A) [15 sections]" in labels

    window.close()


def test_ctrl_tab_cycles_main_tabs_without_stepping_spatial_selection(tmp_path: Path):
    QtCore = pytest.importorskip("PySide6.QtCore")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    QtTest = pytest.importorskip("PySide6.QtTest")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    path = tmp_path / "devices.yaml"
    path.write_text(
        textwrap.dedent(
            """
            devices:
              - name: Strip A
                address: strip-a
                protocol: segment
                segments: 3
              - name: Strip B
                address: strip-b
                protocol: segment
                segments: 3
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    window = create_main_window(require_qt(), GuiSettings(), config_path=path)
    window.show()
    app.processEvents()
    tabs = window.centralWidget()
    canvas = window.findChild(QtWidgets.QWidget, "spatialCanvas")
    selected_label = window.findChild(QtWidgets.QLabel, "selectedSpatialLabel")

    assert tabs.currentIndex() == 0
    assert "Strip A" in selected_label.text()
    canvas.setFocus()
    QtTest.QTest.keyClick(
        canvas,
        QtCore.Qt.Key.Key_Tab,
        QtCore.Qt.KeyboardModifier.ControlModifier,
    )
    app.processEvents()
    assert tabs.currentIndex() == 1
    assert tabs.tabText(tabs.currentIndex()) == "Devices"
    assert "Strip A" in selected_label.text()

    QtTest.QTest.keyClick(
        window,
        QtCore.Qt.Key.Key_Tab,
        QtCore.Qt.KeyboardModifier.ControlModifier
        | QtCore.Qt.KeyboardModifier.ShiftModifier,
    )
    app.processEvents()
    assert tabs.currentIndex() == 0
    assert tabs.tabText(tabs.currentIndex()) == "Devices / Spatial"
    window.close()
