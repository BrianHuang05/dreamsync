from types import SimpleNamespace

import pytest

from dreamsync.director import EffectMode, LightingIntent
from dreamsync.gui.services.session_service import SessionService
from dreamsync.gui.services.device_health_service import DeviceHealthService
from dreamsync.output.auto_detect import DeviceConfig
from dreamsync.output.govee_ble import GoveeBleAdapter, GoveeBleConfig
from dreamsync.output.govee_lan import MultiGoveeLanAdapter
from dreamsync.output.roles import DeviceRole
from dreamsync.render import SegmentRenderer
from dreamsync.spatial.mapper import SpatialMapper
from dreamsync.spatial.models import GridCell, SpatialCellState


class RecordingLight:
    def __init__(self, address):
        self.config = SimpleNamespace(device_ip=address)
        self.frames = []

    def send_frame(self, colors):
        self.frames.append(tuple(colors))
        return True


@pytest.mark.parametrize("mode", ["regular", "scene", "continuous", "baked"])
def test_hot_toggle_preserves_devices_and_other_output(mode):
    lights = [RecordingLight("one"), RecordingLight("two")]
    ble = GoveeBleAdapter(GoveeBleConfig(address="ble"))
    output = MultiGoveeLanAdapter(
        [(light, SegmentRenderer(segments=2), DeviceRole.PRIMARY) for light in lights],
        ble_followers=[ble],
        spatial_mapper=SpatialMapper(enabled=True) if mode == "continuous" else None,
    )
    devices = list(output.devices)
    intent = LightingIntent(EffectMode.AMBIENT, 1.0, 1.0, 120.0, "#ff0000")

    def send(t):
        if mode == "baked":
            return output.send_baked_frame(t, {"one": "#ff0000", "two": "#00ff00", "ble": "#ffffff"})
        if mode == "scene":
            return output.send_spatial_scene(t, {GridCell.CENTER: SpatialCellState(intent=intent, emphasis=1.0)})
        return output.send_frame(t, intent)

    assert send(0)
    assert not ble._queue.empty()
    output.set_device_enabled("one", False)
    output.set_device_enabled("ble", False)
    assert ble._queue.empty()
    assert send(1)
    assert [len(light.frames) for light in lights] == [1, 2]
    assert ble._queue.empty()
    assert output.devices == devices
    assert output._ble_followers == [ble]
    output.set_device_enabled("one", True)
    output.set_device_enabled("ble", True)
    assert send(2)
    assert [len(light.frames) for light in lights] == [2, 3]
    assert not ble._queue.empty()
    for address in ("one", "two", "ble"):
        output.set_device_enabled(address, False)
    assert not send(3)
    output.set_device_enabled("two", True)
    assert send(4)
    assert [len(light.frames) for light in lights] == [2, 4]


def test_service_toggle_survives_output_rebuild_without_config_edits(tmp_path):
    path = tmp_path / "devices.yaml"
    original = b"devices:\n  - address: 192.168.1.2\n    type: lan\n    name: Desk\n    segments: 2\n"
    path.write_bytes(original)
    service = SessionService()
    first = service.build_output_adapter(path)
    service.set_device_enabled("192.168.1.2", False)
    second = service.build_output_adapter(path)
    assert not first.device_enabled("192.168.1.2")
    assert not second.device_enabled("192.168.1.2")
    service.set_device_enabled("192.168.1.2", True)
    assert first.device_enabled("192.168.1.2")
    assert second.device_enabled("192.168.1.2")
    assert path.read_bytes() == original
    service.set_device_enabled("192.168.1.2", False)
    assert SessionService().build_output_adapter(path).device_enabled("192.168.1.2")


def test_ble_toggle_discards_pending_frames_and_allows_same_color_again():
    adapter = GoveeBleAdapter(GoveeBleConfig(address="ble"))
    adapter.send_color(10, 20, 30)
    adapter.set_output_enabled(False)
    adapter.send_color(40, 50, 60)
    adapter.send_segment_colors([(70, 80, 90)])
    assert adapter._queue.empty()
    adapter.set_output_enabled(True)
    adapter.send_color(10, 20, 30)
    assert adapter._queue.get_nowait() == (10, 20, 30, 100)


def test_disabled_device_health_does_not_probe_or_report_failure(tmp_path):
    session = SessionService()
    session.set_device_enabled("desk", False)

    def unexpected_probe(*args, **kwargs):
        pytest.fail("Disabled light should not be probed")

    health = DeviceHealthService(
        config_loader=lambda path: [DeviceConfig(name="Desk", address="desk", type="lan")],
        lan_probe=unexpected_probe,
        runtime_health_provider=session.hardware_health_snapshot,
    )
    snapshot = health.refresh_once(tmp_path / "unused.yaml")
    assert snapshot.entries[0].status == "disabled"
    assert snapshot.counts()["offline"] == 0
