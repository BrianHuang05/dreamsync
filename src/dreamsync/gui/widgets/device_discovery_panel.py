"""Device discovery and assignment panel widget builder."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceDiscoveryWidgets:
    widget: object
    status_label: object
    scan_lan_devices_button: object
    scan_ble_devices_button: object
    scan_all_devices_button: object
    identify_device_button: object
    advanced_test_device_button: object
    assign_discovered_device_button: object
    save_discovered_config_button: object
    devices_table: object
    name_edit: object
    type_combo: object
    segments_spin: object
    transport_combo: object
    protocol_combo: object
    role_combo: object
    brightness_spin: object
    x_spin: object
    y_spin: object
    z_spin: object


def _build_devices_table(QtWidgets):
    headers = (
        "Status",
        "Source",
        "Name",
        "Address",
        "Latency",
        "RSSI",
    )
    table = QtWidgets.QTableWidget(0, len(headers))
    table.setObjectName("discoveredDevicesTable")
    table.setHorizontalHeaderLabels(list(headers))
    table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setStretchLastSection(True)
    table.setAlternatingRowColors(True)
    table.setMinimumWidth(520)
    return table


def _add_labeled_field(QtWidgets, layout, row: int, label: str, editor: object):
    layout.addWidget(QtWidgets.QLabel(label), row, 0)
    layout.addWidget(editor, row, 1)


def _build_combo(QtWidgets, object_name: str, items: tuple[tuple[str, str], ...]):
    combo = QtWidgets.QComboBox()
    combo.setObjectName(object_name)
    for label, value in items:
        combo.addItem(label, value)
    return combo


def _build_position_spin(QtWidgets, object_name: str):
    spin = QtWidgets.QDoubleSpinBox()
    spin.setObjectName(object_name)
    spin.setRange(-1000.0, 1000.0)
    spin.setSingleStep(0.1)
    spin.setDecimals(2)
    return spin


def build_device_discovery_panel(qt_modules):
    QtWidgets = qt_modules.QtWidgets

    widget = QtWidgets.QWidget()
    widget.setObjectName("deviceDiscoveryPanel")
    root_layout = QtWidgets.QVBoxLayout(widget)

    content = QtWidgets.QSplitter()
    root_layout.addWidget(content, 1)

    discovery_group = QtWidgets.QGroupBox("Discovered Devices")
    discovery_layout = QtWidgets.QVBoxLayout(discovery_group)

    scan_actions = QtWidgets.QHBoxLayout()
    scan_lan_devices_button = QtWidgets.QPushButton("Scan LAN")
    scan_lan_devices_button.setObjectName("scanLanDevicesButton")
    scan_lan_devices_button.setToolTip("Scan the local network for devices (L)")
    scan_ble_devices_button = QtWidgets.QPushButton("Scan BLE")
    scan_ble_devices_button.setObjectName("scanBleDevicesButton")
    scan_ble_devices_button.setToolTip("Scan Bluetooth Low Energy for devices (B)")
    scan_all_devices_button = QtWidgets.QPushButton("Scan All")
    scan_all_devices_button.setObjectName("scanAllDevicesButton")
    scan_all_devices_button.setToolTip("Scan both LAN and Bluetooth Low Energy (S)")
    scan_actions.addWidget(scan_lan_devices_button)
    scan_actions.addWidget(scan_ble_devices_button)
    scan_actions.addWidget(scan_all_devices_button)
    scan_actions.addStretch(1)
    discovery_layout.addLayout(scan_actions)

    devices_table = _build_devices_table(QtWidgets)
    discovery_layout.addWidget(devices_table, 1)
    content.addWidget(discovery_group)

    assignment_group = QtWidgets.QGroupBox("Assignment")
    assignment_layout = QtWidgets.QVBoxLayout(assignment_group)
    form_layout = QtWidgets.QGridLayout()
    assignment_layout.addLayout(form_layout)

    name_edit = QtWidgets.QLineEdit()
    name_edit.setObjectName("discoveredDeviceNameEdit")
    name_edit.setPlaceholderText("Device name")
    _add_labeled_field(QtWidgets, form_layout, 0, "Name", name_edit)

    type_combo = _build_combo(
        QtWidgets,
        "discoveredDeviceTypeCombo",
        (("Auto", "auto"), ("LAN", "lan"), ("BLE", "ble")),
    )
    _add_labeled_field(QtWidgets, form_layout, 1, "Type", type_combo)

    segments_spin = QtWidgets.QSpinBox()
    segments_spin.setObjectName("discoveredDeviceSegmentsSpin")
    segments_spin.setRange(1, 2048)
    segments_spin.setValue(10)
    _add_labeled_field(QtWidgets, form_layout, 2, "Segments", segments_spin)

    transport_combo = _build_combo(
        QtWidgets,
        "discoveredDeviceTransportCombo",
        (("ptreal", "ptreal"), ("razer", "razer"), ("colorwc", "colorwc")),
    )
    _add_labeled_field(QtWidgets, form_layout, 3, "Transport", transport_combo)

    protocol_combo = _build_combo(
        QtWidgets,
        "discoveredDeviceProtocolCombo",
        (("segment", "segment"), ("bulb", "bulb")),
    )
    _add_labeled_field(QtWidgets, form_layout, 4, "BLE protocol", protocol_combo)

    role_combo = _build_combo(
        QtWidgets,
        "discoveredDeviceRoleCombo",
        (("", ""), ("primary", "primary"), ("accent", "accent")),
    )
    _add_labeled_field(QtWidgets, form_layout, 5, "Role", role_combo)

    brightness_spin = QtWidgets.QDoubleSpinBox()
    brightness_spin.setObjectName("discoveredDeviceBrightnessSpin")
    brightness_spin.setRange(0.0, 1.0)
    brightness_spin.setSingleStep(0.05)
    brightness_spin.setDecimals(2)
    brightness_spin.setValue(1.0)
    _add_labeled_field(QtWidgets, form_layout, 6, "Brightness", brightness_spin)

    x_spin = _build_position_spin(QtWidgets, "discoveredDeviceXSpin")
    y_spin = _build_position_spin(QtWidgets, "discoveredDeviceYSpin")
    z_spin = _build_position_spin(QtWidgets, "discoveredDeviceZSpin")
    _add_labeled_field(QtWidgets, form_layout, 7, "X", x_spin)
    _add_labeled_field(QtWidgets, form_layout, 8, "Y", y_spin)
    _add_labeled_field(QtWidgets, form_layout, 9, "Z", z_spin)

    assignment_actions = QtWidgets.QHBoxLayout()
    identify_device_button = QtWidgets.QPushButton("Identify")
    identify_device_button.setObjectName("identifyDeviceButton")
    identify_device_button.setToolTip("Flash the selected device blue (I)")
    advanced_test_device_button = QtWidgets.QPushButton("Advanced Test…")
    advanced_test_device_button.setObjectName("advancedTestDeviceButton")
    advanced_test_device_button.setToolTip("Run a bounded color or segment-pattern test")
    assign_discovered_device_button = QtWidgets.QPushButton("Add / Update Assignment")
    assign_discovered_device_button.setObjectName("assignDiscoveredDeviceButton")
    assign_discovered_device_button.setToolTip("Add or update the selected device assignment (A)")
    save_discovered_config_button = QtWidgets.QPushButton("Save Config")
    save_discovered_config_button.setObjectName("saveDiscoveredConfigButton")
    save_discovered_config_button.setToolTip("Save the selected device assignment (Ctrl+S)")
    assignment_actions.addWidget(identify_device_button)
    assignment_actions.addWidget(advanced_test_device_button)
    assignment_actions.addWidget(assign_discovered_device_button)
    assignment_actions.addWidget(save_discovered_config_button)
    assignment_layout.addLayout(assignment_actions)
    assignment_layout.addStretch(1)
    content.addWidget(assignment_group)

    content.setStretchFactor(0, 3)
    content.setStretchFactor(1, 2)

    status_label = QtWidgets.QLabel("Ready")
    status_label.setWordWrap(True)
    status_label.setObjectName("deviceDiscoveryStatusLabel")
    root_layout.addWidget(status_label)

    return DeviceDiscoveryWidgets(
        widget=widget,
        status_label=status_label,
        scan_lan_devices_button=scan_lan_devices_button,
        scan_ble_devices_button=scan_ble_devices_button,
        scan_all_devices_button=scan_all_devices_button,
        identify_device_button=identify_device_button,
        advanced_test_device_button=advanced_test_device_button,
        assign_discovered_device_button=assign_discovered_device_button,
        save_discovered_config_button=save_discovered_config_button,
        devices_table=devices_table,
        name_edit=name_edit,
        type_combo=type_combo,
        segments_spin=segments_spin,
        transport_combo=transport_combo,
        protocol_combo=protocol_combo,
        role_combo=role_combo,
        brightness_spin=brightness_spin,
        x_spin=x_spin,
        y_spin=y_spin,
        z_spin=z_spin,
    )
