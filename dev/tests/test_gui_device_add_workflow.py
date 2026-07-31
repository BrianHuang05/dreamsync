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
