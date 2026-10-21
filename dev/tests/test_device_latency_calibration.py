from types import SimpleNamespace

import pytest

from dreamsync.gui.services.device_discovery_service import DeviceDiscoveryService, DiscoveredDeviceEntry
from dreamsync.output.auto_detect import DeviceConfig, device_config_to_mapping, load_device_config, save_device_config
from dreamsync.show.baked_frames import BakeSettings, BakedFrame, BakedFrameArtifact, BakedFrameNode, default_engine_metadata
from dreamsync.show.baked_runtime import BakedFramePlaybackRuntime


def test_device_latency_advance_round_trips_config(tmp_path):
    path = tmp_path / "devices.yaml"
    path.write_text("devices:\n  - name: Strip\n    address: 192.0.2.1\n    segments: 4\n", encoding="utf-8")
    entry = DiscoveredDeviceEntry(key="lan:192.0.2.1", source="lan", name="Strip", address="192.0.2.1")
    DeviceDiscoveryService().update_device_lighting_advance(path, entry, 135)
    config = load_device_config(path)[0]
    assert config.lighting_advance_ms == 135
    assert device_config_to_mapping(config)["lighting_advance_ms"] == 135
    DeviceDiscoveryService().update_device_lighting_advance(path, entry, -45)
    assert load_device_config(path)[0].lighting_advance_ms == -45
    with pytest.raises(ValueError):
        DeviceDiscoveryService().update_device_lighting_advance(path, entry, 2500)


def test_baked_playback_selects_frames_at_each_device_advance():
    artifact = BakedFrameArtifact(
        source_show_path="show.json", source_show_hash="s", device_config_path="devices.yaml",
        device_config_hash="d", settings=BakeSettings(fps=2), duration=1.0,
        nodes=(BakedFrameNode(key="192.0.2.1", device_address="192.0.2.1"),),
        frames=(BakedFrame(0.0, ("#000000",)), BakedFrame(0.5, ("#ffffff",))),
        engine=default_engine_metadata(), summary={},
    )
    class Adapter:
        def __init__(self):
            self.config = SimpleNamespace(device_ip="192.0.2.1")
            self.lighting_advance_ms = 500
            self.calls = []
        def send_baked_frame(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return True
    adapter = Adapter()
    class Room:
        devices = [(adapter,)]
        def send_baked_frame(self, *args, **kwargs):
            adapter.send_baked_frame(*args, **kwargs)
            return True
    room = Room()
    runtime = BakedFramePlaybackRuntime(artifact, room)
    runtime.tick(0.0)
    kwargs = adapter.calls[0][1]
    assert kwargs["device_node_colors"]["192.0.2.1"]["192.0.2.1"] == "#ffffff"
    assert adapter.calls[0][0][1]["192.0.2.1"] == "#000000"
