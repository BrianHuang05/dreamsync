"""Tests for passive configured-device health snapshots."""

from pathlib import Path

from dreamsync.gui.services.device_health_service import DeviceHealthService
from dreamsync.output.auto_detect import DeviceConfig, LatencyStats


def test_passive_health_classifies_lan_and_leaves_ble_unknown(tmp_path: Path):
    configs = [
        DeviceConfig(name="Fast LAN", address="192.0.2.10", type="lan"),
        DeviceConfig(name="Slow LAN", address="192.0.2.11", type="lan"),
        DeviceConfig(name="Missing LAN", address="192.0.2.12", type="lan"),
        DeviceConfig(name="BLE Strip", address="AA:BB:CC:DD:EE:FF", type="ble"),
    ]

    def probe(address: str, **_kwargs):
        if address.endswith(".10"):
            return LatencyStats(samples=[12.0, 14.0, 16.0])
        if address.endswith(".11"):
            return LatencyStats(samples=[250.0, 260.0, 270.0])
        return LatencyStats(samples=[])

    service = DeviceHealthService(
        config_loader=lambda _path: configs,
        lan_probe=probe,
    )
    snapshot = service.refresh_once(tmp_path / "devices.yaml")

    assert [entry.status for entry in snapshot.entries] == [
        "online",
        "degraded",
        "offline",
        "unknown",
    ]
    assert snapshot.counts() == {
        "online": 1,
        "degraded": 1,
        "offline": 1,
        "unknown": 1,
    }


def test_passive_health_reports_config_errors_without_raising(tmp_path: Path):
    def fail_load(_path):
        raise ValueError("invalid config")

    service = DeviceHealthService(config_loader=fail_load)
    snapshot = service.refresh_once(tmp_path / "devices.yaml")

    assert snapshot.entries == ()
    assert snapshot.error == "invalid config"


def test_runtime_ble_health_overrides_passive_unknown(tmp_path: Path):
    config = DeviceConfig(name="BLE Strip", address="AA:BB:CC:DD:EE:FF", type="ble")
    service = DeviceHealthService(
        config_loader=lambda _path: [config],
        runtime_health_provider=lambda: {
            "AA:BB:CC:DD:EE:FF": {
                "status": "degraded",
                "error": "BLE reconnecting (attempt 2).",
            }
        },
    )

    snapshot = service.refresh_once(tmp_path / "devices.yaml")

    assert snapshot.entries[0].status == "degraded"
    assert "reconnecting" in snapshot.entries[0].error


def test_passive_health_turns_probe_errors_into_offline_entries(tmp_path: Path):
    config = DeviceConfig(name="LAN", address="192.0.2.20", type="lan")

    def fail_probe(_address: str, **_kwargs):
        raise OSError("unreachable")

    service = DeviceHealthService(
        config_loader=lambda _path: [config],
        lan_probe=fail_probe,
    )
    snapshot = service.refresh_once(tmp_path / "devices.yaml")

    assert snapshot.entries[0].status == "offline"
    assert snapshot.entries[0].error == "unreachable"
