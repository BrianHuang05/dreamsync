from __future__ import annotations

from pathlib import Path

import pytest

from dreamsync.gui.main_window import create_main_window
from dreamsync.gui.qt import require_qt
from dreamsync.gui.services.device_discovery_service import (
    DeviceDiscoveryService,
    DiscoveredDeviceEntry,
)
from dreamsync.gui.settings import GuiSettings
from dreamsync.output.auto_detect import load_device_config


class _FakeDiscoveryService(DeviceDiscoveryService):
    def __init__(self) -> None:
        super().__init__()
        self.scan_calls: list[str] = []
        self.identified: list[DiscoveredDeviceEntry] = []
        self.identify_attempts: list[tuple[str, str | None, str | None]] = []

    def scan_lan(self, timeout: float = 5.0) -> list[DiscoveredDeviceEntry]:
        self.scan_calls.append("lan")
        return [
            DiscoveredDeviceEntry(
                key="lan:192.168.1.77",
                source="lan",
                name="H6097 LAN",
                address="192.168.1.77",
                sku="H6097",
            )
        ]

    def scan_ble(self, timeout: float = 10.0) -> list[DiscoveredDeviceEntry]:
        self.scan_calls.append("ble")
        return [
            DiscoveredDeviceEntry(
                key="ble:D7:01:86:46:44:59",
                source="ble",
                name="H6097 BLE",
                address="D7:01:86:46:44:59",
                rssi=-42,
            )
        ]

    def measure_lan_latency(
        self,
        entries: list[DiscoveredDeviceEntry],
    ) -> list[DiscoveredDeviceEntry]:
        return list(entries)

    def identify(self, entry: DiscoveredDeviceEntry, **kwargs) -> None:
        self.identified.append(entry)
        self.identify_attempts.append(
            (
                entry.source,
                kwargs.get("transport"),
                kwargs.get("protocol"),
            )
        )


def _wait_until(QtTest, predicate, *, attempts: int = 100) -> None:
    for _ in range(attempts):
        if predicate():
            return
        QtTest.QTest.qWait(20)
    raise AssertionError("Timed out waiting for the GUI discovery worker.")


@pytest.mark.parametrize("source,address", [("lan", "192.168.1.77"), ("ble", "D7:01:86:46:44:59")])
def test_configured_device_missing_from_scan_can_identify_and_open_advanced_test(tmp_path, source, address):
    """A missing discovery reply must not prevent direct device diagnostics."""
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    QtCore = pytest.importorskip("PySide6.QtCore")
    QtTest = pytest.importorskip("PySide6.QtTest")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    path = tmp_path / "devices.yaml"
    path.write_text(
        f"devices:\n  - name: Test Light\n    type: {source}\n    address: '{address}'\n"
        "    segments: 3\n    lighting_advance_ms: 70\n", encoding="utf-8",
    )

    class Service(_FakeDiscoveryService):
        def scan_lan(self, timeout=5.0):
            return []

        def scan_ble(self, timeout=10.0):
            return []

        def probe_configured_lan(self, entries):
            return list(entries)

        def test_device(self, entry, spec, *, stop_event, spec_provider):
            tested.append((entry.address, spec.segments))
            stop_event.wait(3)

    tested = []
    service = Service()
    window = create_main_window(require_qt(), GuiSettings(), config_path=path, discovery_service=service)
    window.show()
    app.processEvents()
    table = window.findChild(QtWidgets.QTableWidget, "discoveredDevicesTable")
    scan = window.findChild(QtWidgets.QPushButton, "scanAllDevicesButton")
    identify = window.findChild(QtWidgets.QPushButton, "identifyDeviceButton")
    advanced = window.findChild(QtWidgets.QPushButton, "advancedTestDeviceButton")
    timer = QtCore.QTimer(window)
    watchdog = QtCore.QTimer(window)
    watchdog.setSingleShot(True)
    watchdog.timeout.connect(
        lambda: app.activeModalWidget().reject() if app.activeModalWidget() else None
    )
    errors, opened = [], []

    def exercise_dialog():
        dialog = app.activeModalWidget()
        if dialog is None:
            return
        try:
            assert dialog.objectName() == "advancedDeviceTestDialog"
            if not opened:
                opened.append(True)
                assert dialog.findChild(QtWidgets.QDoubleSpinBox, "deviceLightingAdvanceSpin").value() == 70
                dialog.findChild(QtWidgets.QPushButton, "deviceTestStartButton").click()
            elif tested:
                dialog.reject()
        except BaseException as exc:
            errors.append(exc)
            dialog.reject()

    try:
        scan.click()
        _wait_until(QtTest, lambda: scan.isEnabled() and table.rowCount() == 1)
        table.selectRow(0)
        app.processEvents()
        assert "Not found" in table.item(0, 0).text()
        assert identify.isEnabled(), "Configured devices must support a direct identify attempt"
        assert advanced.isEnabled(), "Advanced Test must open without a discovery reply"
        identify.click()
        _wait_until(QtTest, lambda: bool(service.identified) and identify.isEnabled())
        assert service.identified[-1].address == address
        window.findChild(QtWidgets.QPushButton, "confirmIdentifyProtocolButton").click()
        timer.timeout.connect(exercise_dialog)
        timer.start(20)
        watchdog.start(3000)
        advanced.click()
        assert opened, "Clicking Advanced Test must open the real modal dialog"
        assert not errors, errors
        assert tested == [(address, 3)]
    finally:
        timer.stop()
        watchdog.stop()
        window.close()
        window.deleteLater()
        QtTest.QTest.qWait(50)


@pytest.mark.parametrize(
    ("source", "address"),
    [
        ("lan", "192.168.1.77"),
        ("ble", "D7:01:86:46:44:59"),
    ],
)
def test_add_device_scans_identifies_and_adds_lan_and_ble(
    tmp_path: Path,
    source: str,
    address: str,
) -> None:
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    QtCore = pytest.importorskip("PySide6.QtCore")
    QtTest = pytest.importorskip("PySide6.QtTest")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    config_path = tmp_path / f"{source}-devices.yaml"
    config_path.write_text("devices: []\n", encoding="utf-8")
    service = _FakeDiscoveryService()
    window = create_main_window(
        require_qt(),
        GuiSettings(),
        config_path=config_path,
        discovery_service=service,
    )
    window.show()
    app.processEvents()
    tabs = window.centralWidget()
    tabs.setCurrentIndex(0)
    app.processEvents()

    add_button = window.findChild(QtWidgets.QPushButton, "addDeviceButton")
    table = window.findChild(QtWidgets.QTableWidget, "discoveredDevicesTable")
    identify_button = window.findChild(QtWidgets.QPushButton, "identifyDeviceButton")
    add_to_config_button = window.findChild(
        QtWidgets.QPushButton,
        "assignDiscoveredDeviceButton",
    )
    save_changes_button = window.findChild(
        QtWidgets.QPushButton,
        "saveDiscoveredConfigButton",
    )
    confirm_protocol_button = window.findChild(
        QtWidgets.QPushButton,
        "confirmIdentifyProtocolButton",
    )
    try_next_protocol_button = window.findChild(
        QtWidgets.QPushButton,
        "tryNextIdentifyProtocolButton",
    )
    name_edit = window.findChild(QtWidgets.QLineEdit, "discoveredDeviceNameEdit")
    type_combo = window.findChild(QtWidgets.QComboBox, "discoveredDeviceTypeCombo")
    transport_combo = window.findChild(
        QtWidgets.QComboBox,
        "discoveredDeviceTransportCombo",
    )
    protocol_combo = window.findChild(
        QtWidgets.QComboBox,
        "discoveredDeviceProtocolCombo",
    )
    assert add_button is not None
    assert table is not None
    assert identify_button is not None
    assert add_to_config_button is not None
    assert save_changes_button is not None
    assert confirm_protocol_button is not None
    assert try_next_protocol_button is not None
    assert name_edit is not None
    assert type_combo is not None
    assert transport_combo is not None
    assert protocol_combo is not None

    add_button.click()
    _wait_until(
        QtTest,
        lambda: add_button.isEnabled() and table.rowCount() == 2,
    )
    QtTest.QTest.qWait(50)
    assert service.scan_calls == ["lan", "ble"]
    assert {
        table.item(row, 1).text().lower()
        for row in range(table.rowCount())
    } == {"lan", "ble"}

    row = next(
        row
        for row in range(table.rowCount())
        if table.item(row, 1).text().lower() == source
    )
    table.selectRow(row)
    app.processEvents()
    assert add_to_config_button.text() == "Add to Config"
    assert type_combo.currentData() == source
    assert not type_combo.isEnabled()
    assert transport_combo.isEnabled() is (source == "lan")
    assert protocol_combo.isEnabled() is (source == "ble")

    identify_button.click()
    _wait_until(
        QtTest,
        lambda: identify_button.isEnabled() and confirm_protocol_button.isVisible(),
    )
    QtTest.QTest.qWait(50)
    assert service.identified[-1].source == source
    assert service.identified[-1].address == address

    try_next_protocol_button.click()
    _wait_until(
        QtTest,
        lambda: len(service.identify_attempts) == 2
        and confirm_protocol_button.isVisible(),
    )
    QtTest.QTest.qWait(50)
    expected_choice = "razer" if source == "lan" else "bulb"
    assert (
        service.identify_attempts[-1][1]
        if source == "lan"
        else service.identify_attempts[-1][2]
    ) == expected_choice
    confirm_protocol_button.click()
    assert not confirm_protocol_button.isVisible()

    name_edit.setText(f"Confirmed {source.upper()}")
    if source == "lan":
        save_changes_button.click()
    else:
        name_edit.setFocus()
        QtTest.QTest.keyClick(
            name_edit,
            QtCore.Qt.Key.Key_S,
            QtCore.Qt.KeyboardModifier.ControlModifier,
        )
    app.processEvents()
    saved = load_device_config(config_path)
    assert [(config.type, config.address) for config in saved] == [(source, address)]
    assert saved[0].name == f"Confirmed {source.upper()}"
    assert (
        saved[0].transport if source == "lan" else saved[0].protocol
    ) == expected_choice
    window.close()
    window.deleteLater()
    QtTest.QTest.qWait(20)


def test_scan_shortcuts_work_with_tab_bar_focus_and_ignore_text_and_modals(tmp_path):
    QtWidgets = pytest.importorskip('PySide6.QtWidgets')
    QtCore = pytest.importorskip('PySide6.QtCore')
    QtTest = pytest.importorskip('PySide6.QtTest')
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    path = tmp_path / 'devices.yaml'
    path.write_text('devices: []\n')
    service = _FakeDiscoveryService()
    window = create_main_window(require_qt(), GuiSettings(), config_path=path, discovery_service=service)
    window.show()
    window.activateWindow()
    tabs = window.centralWidget()
    tabs.setCurrentIndex(1)
    app.processEvents()
    tabs.setCurrentIndex(0)
    bar = tabs.tabBar()
    bar.setFocus()
    app.processEvents()
    shortcut_filter = window._dreamsync_reliable_page_shortcut_filter
    for key in ('B', 'L', 'S'):
        assert shortcut_filter._handler_for(key) is not None
    # Exercise the actual key event without involving scan threads in this focus regression.
    handled = []
    original = shortcut_filter._handler_for
    shortcut_filter._handler_for = lambda sequence: (
        (lambda: handled.append(sequence)) if original(sequence) is not None else None
    )
    for key in (QtCore.Qt.Key.Key_B, QtCore.Qt.Key.Key_L, QtCore.Qt.Key.Key_S):
        QtTest.QTest.keyClick(bar, key)
    assert handled == ['B', 'L', 'S']
    edit = window.findChild(QtWidgets.QLineEdit, 'discoveredDeviceNameEdit')
    assert edit is not None
    edit.setEnabled(True)
    edit.setFocus()
    app.processEvents()
    assert original('B') is None
    dialog = QtWidgets.QDialog(window)
    dialog.setModal(True)
    dialog.show()
    app.processEvents()
    assert original('L') is None
    dialog.close()
    window.close()
