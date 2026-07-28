from __future__ import annotations

from pathlib import Path

from dreamsync.gui.services.device_discovery_service import (
    DeviceDiscoveryService,
    DeviceTestSpec,
    DiscoveredDeviceEntry,
)
from dreamsync.output.auto_detect import DeviceConfig, load_device_config, save_device_config
from dreamsync.output.discovery import GoveeDevice
from dreamsync.output.govee_ble import BleProtocol, GoveeBleDevice
from dreamsync.output.govee_lan import TransportMode
from dreamsync.spatial.models import DevicePlacement


class FakeLanAdapter:
    instances: list["FakeLanAdapter"] = []

    def __init__(self, config):
        self.config = config
        self.frames: list[list[tuple[int, int, int]]] = []
        FakeLanAdapter.instances.append(self)

    def send_frame(self, colors):
        self.frames.append(list(colors))
        return True


class FakeBleAdapter:
    instances: list["FakeBleAdapter"] = []

    def __init__(self, config):
        self.config = config
        self.started = False
        self.stopped = False
        self.colors: list[tuple[int, int, int, int]] = []
        FakeBleAdapter.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def send_color(self, r, g, b, brightness=100):
        self.colors.append((r, g, b, brightness))

    def send_segment_colors(self, colors, brightness=100):
        self.colors.append((tuple(colors), brightness))


def test_scan_lan_maps_discovered_devices() -> None:
    service = DeviceDiscoveryService(
        lan_scan=lambda timeout: [
            GoveeDevice(ip="192.168.1.44", sku="H617A", device_id="dev-1", raw={"ip": "192.168.1.44"})
        ]
    )

    entries = service.scan_lan(timeout=0.1)

    assert entries == [
        DiscoveredDeviceEntry(
            key="lan:192.168.1.44",
            source="lan",
            name="H617A",
            address="192.168.1.44",
            sku="H617A",
            device_id="dev-1",
        )
    ]


def test_scan_ble_sorts_unassigned_devices_by_rssi_descending() -> None:
    service = DeviceDiscoveryService(
        ble_scan=lambda timeout: [
            GoveeBleDevice(name="Far", address="aa", rssi=-82),
            GoveeBleDevice(name="Near", address="bb", rssi=-43),
        ]
    )

    entries = service.scan_ble(timeout=0.1)

    assert [entry.name for entry in entries] == ["Near", "Far"]
    assert [entry.rssi for entry in entries] == [-43, -82]


def test_merge_with_config_marks_existing_assignments(tmp_path: Path) -> None:
    config_path = tmp_path / "devices.yaml"
    save_device_config(
        config_path,
        [
            DeviceConfig(
                name="Left wall",
                address="192.168.1.50",
                type="lan",
                placement=DevicePlacement(x=-0.8, y=0.2, z=0.1),
            )
        ],
    )
    service = DeviceDiscoveryService()

    entries = service.merge_with_config(
        [
            DiscoveredDeviceEntry(
                key="lan:192.168.1.50",
                source="lan",
                name="H617A",
                address="192.168.1.50",
            )
        ],
        config_path,
    )

    assert entries[0].assigned is True
    assert entries[0].existing_name == "Left wall"


def test_merge_with_config_retains_configured_devices_not_seen_by_scan(tmp_path: Path) -> None:
    config_path = tmp_path / "devices.yaml"
    save_device_config(
        config_path,
        [
            DeviceConfig(
                name="Back light",
                address="192.168.1.60",
                type="lan",
                placement=DevicePlacement(x=0.0, y=0.5, z=-0.8),
            )
        ],
    )
    service = DeviceDiscoveryService()

    entries = service.merge_with_config([], config_path)

    assert len(entries) == 1
    assert entries[0].name == "Back light"
    assert entries[0].address == "192.168.1.60"
    assert entries[0].source == "lan"
    assert entries[0].assigned is True
    assert entries[0].connected is False


def test_assignment_to_config_defaults_and_spatial_fields() -> None:
    service = DeviceDiscoveryService()
    entry = DiscoveredDeviceEntry(
        key="ble:aa:bb",
        source="ble",
        name="H6006",
        address="aa:bb",
        rssi=-51,
    )

    config = service.assignment_to_config(
        entry,
        name="Ceiling lamp",
        segments=1,
        role="accent",
        brightness_scale=0.7,
        x=0.0,
        y=0.9,
        z=-0.2,
    )

    assert config.name == "Ceiling lamp"
    assert config.address == "aa:bb"
    assert config.type == "ble"
    assert config.protocol == BleProtocol.SEGMENT.value
    assert config.transport is None
    assert config.role == "accent"
    assert config.brightness_scale == 0.7
    assert config.placement == DevicePlacement(x=0.0, y=0.9, z=-0.2)


def test_assignment_to_config_suppresses_protocol_for_lan() -> None:
    service = DeviceDiscoveryService()
    entry = DiscoveredDeviceEntry(
        key="lan:192.168.1.2",
        source="lan",
        name="Strip",
        address="192.168.1.2",
    )

    config = service.assignment_to_config(
        entry,
        device_type="lan",
        transport="ptreal",
        protocol="segment",
    )

    assert config.transport == "ptreal"
    assert config.protocol is None


def test_upsert_device_config_creates_and_updates_by_address(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "devices.yaml"
    service = DeviceDiscoveryService()

    service.upsert_device_config(
        path,
        DeviceConfig(name="Original", address="192.168.1.2", type="lan", transport="ptreal"),
    )
    service.upsert_device_config(
        path,
        DeviceConfig(name="Renamed", address="192.168.1.2", type="lan", transport="colorwc"),
    )
    service.upsert_device_config(
        path,
        DeviceConfig(name="Other", address="aa:bb", type="ble", protocol="segment"),
    )

    configs = load_device_config(path)
    assert [(config.name, config.address) for config in configs] == [
        ("Renamed", "192.168.1.2"),
        ("Other", "aa:bb"),
    ]
    assert configs[0].transport == "colorwc"


def test_identify_lan_flashes_blue_then_off() -> None:
    FakeLanAdapter.instances = []
    service = DeviceDiscoveryService(
        lan_adapter_factory=FakeLanAdapter,
        sleep_fn=lambda seconds: None,
    )

    service.identify(
        DiscoveredDeviceEntry(key="lan:192.168.1.2", source="lan", name="Strip", address="192.168.1.2"),
        seconds=0.2,
        segments=2,
        transport="ptreal",
    )

    adapter = FakeLanAdapter.instances[0]
    assert adapter.config.transport == TransportMode.PTREAL
    assert adapter.frames[0] == [(0, 0, 255), (0, 0, 255)]
    assert adapter.frames[-1] == [(0, 0, 0), (0, 0, 0)]


def test_identify_lan_hard_flashes_at_full_brightness_for_at_most_five_seconds() -> None:
    FakeLanAdapter.instances = []
    sleeps: list[float] = []
    service = DeviceDiscoveryService(
        lan_adapter_factory=FakeLanAdapter,
        sleep_fn=sleeps.append,
    )

    service.identify(
        DiscoveredDeviceEntry(key="lan:192.168.1.2", source="lan", name="Strip", address="192.168.1.2"),
        seconds=10.0,
        segments=1,
        transport="ptreal",
    )

    adapter = FakeLanAdapter.instances[0]
    assert adapter.config.brightness == 1.0
    assert len(adapter.frames) == 11  # five on/off flashes plus final forced-off frame
    assert sleeps == [0.5] * 10


def test_identify_ble_starts_flashes_blue_and_stops() -> None:
    FakeBleAdapter.instances = []
    service = DeviceDiscoveryService(
        ble_adapter_factory=FakeBleAdapter,
        sleep_fn=lambda seconds: None,
    )

    service.identify(
        DiscoveredDeviceEntry(key="ble:aa:bb", source="ble", name="Lamp", address="aa:bb"),
        seconds=0.2,
        segments=1,
        protocol="bulb",
    )

    adapter = FakeBleAdapter.instances[0]
    assert adapter.config.protocol == BleProtocol.BULB
    assert adapter.started is True
    assert adapter.stopped is True
    assert adapter.colors[0] == (0, 0, 255, 100)
    assert adapter.colors[-1] == (0, 0, 0, 100)


def test_advanced_lan_walk_test_is_bounded_scaled_and_forces_off() -> None:
    FakeLanAdapter.instances = []
    service = DeviceDiscoveryService(
        lan_adapter_factory=FakeLanAdapter,
        sleep_fn=lambda seconds: None,
    )
    entry = DiscoveredDeviceEntry(
        key="lan:192.168.1.2",
        source="lan",
        name="Strip",
        address="192.168.1.2",
    )

    result = service.test_device(
        entry,
        DeviceTestSpec(
            pattern="walk",
            duration_seconds=0.6,
            brightness=0.2,
            segments=3,
            transport="ptreal",
        ),
    )

    adapter = FakeLanAdapter.instances[0]
    assert adapter.config.brightness == 0.2
    assert result.frames_sent == 2
    assert adapter.frames[0] == [(255, 0, 0), (0, 0, 0), (0, 0, 0)]
    assert adapter.frames[-1] == [(0, 0, 0)] * 3


def test_advanced_ble_bulb_rejects_segment_patterns_before_adapter_creation() -> None:
    FakeBleAdapter.instances = []
    service = DeviceDiscoveryService(
        ble_adapter_factory=FakeBleAdapter,
        sleep_fn=lambda seconds: None,
    )
    entry = DiscoveredDeviceEntry(key="ble:aa", source="ble", name="Bulb", address="aa")

    try:
        service.test_device(
            entry,
            DeviceTestSpec(pattern="walk", protocol="bulb", duration_seconds=1.0),
        )
    except ValueError as exc:
        assert "solid pattern" in str(exc)
    else:
        raise AssertionError("Expected BLE bulb walk test to be rejected")

    assert FakeBleAdapter.instances == []
