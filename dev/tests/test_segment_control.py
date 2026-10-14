from types import SimpleNamespace

import pytest

from dreamsync.director import EffectMode, LightingIntent
from dreamsync.gui.controllers.spatial_controller import SpatialController
from dreamsync.gui.services.device_service import DeviceService
from dreamsync.output.auto_detect import load_device_config, save_device_config
from dreamsync.output.govee_lan import MultiGoveeLanAdapter
from dreamsync.output.null_adapter import SimulationMultiAdapter
from dreamsync.output.roles import DeviceRole
from dreamsync.output.segment_control import apply_segment_control
from dreamsync.render import RenderMode, SegmentRenderer
from dreamsync.show.runtime_control import RuntimeControlBus, runtime_control_to_dict
from dreamsync.spatial.mapper import SpatialMapper
from dreamsync.spatial.models import DevicePlacement, GridCell, SectionPlacement, SpatialCellState


PLACEMENT = DevicePlacement(0, 0, sections=(
    SectionPlacement(0, 0, 0, 0), SectionPlacement(1, 0.05, 0, 0, enabled=False),
))
INTENT = LightingIntent(mode=EffectMode.AMBIENT, intensity=0.4, speed=0.3, bpm=120, color="#ff0000")


def test_disabled_segments_share_background_and_can_rejoin_current_frame():
    colors = [(100, 0, 0), (0, 100, 0)]
    assert apply_segment_control(colors, "strip", PLACEMENT, None, "#ffffff")[0] == [colors[0], (255, 255, 255)]
    bus = RuntimeControlBus()
    state = bus.update(segment_enabled=(("strip#section:0", False), ("strip#section:1", True)), background_color="#123456")
    params = {"runtime_control": runtime_control_to_dict(state)}
    assert apply_segment_control(colors, "strip", PLACEMENT, params, "#ffffff")[0] == [(18, 52, 86), colors[1]]
    assert apply_segment_control(colors, "other", PLACEMENT, params, "#ffffff")[0] == [colors[0], (18, 52, 86)]


@pytest.mark.parametrize("path", ["ordinary", "continuous", "grid", "baked"])
@pytest.mark.parametrize("ble", [False, True])
def test_all_output_paths_apply_fallback_and_hot_reenable(path, ble):
    frames = []
    target = SimpleNamespace(
        config=SimpleNamespace(device_ip="strip", address="strip"),
        send_frame=lambda colors: frames.append((colors, 100)) or True,
        send_segment_colors=lambda colors, brightness: frames.append((colors, brightness)),
    )
    renderer = SegmentRenderer(segments=2, mode=RenderMode.SOLID)
    device = (target, renderer, DeviceRole.PRIMARY, 1.0, PLACEMENT)
    follower = (target, DeviceRole.PRIMARY, 1.0, PLACEMENT, renderer)
    adapter = MultiGoveeLanAdapter(
        [] if ble else [device], ble_followers=[follower] if ble else [],
        spatial_mapper=SpatialMapper(enabled=True) if path == "continuous" else None,
    )

    def send(params):
        if path == "baked":
            adapter.send_baked_frame(0, {"strip": "#ff0000"}, params=params)
        elif path == "grid":
            adapter.send_spatial_scene(0, {GridCell.CENTER: SpatialCellState(INTENT, 1.0, params=params)})
        else:
            adapter.send_frame(0, INTENT, params=params)

    send(None)
    assert frames[-1][0][1] == (255, 255, 255)
    assert frames[-1][1] == 100
    send({"runtime_control": {"segment_enabled": (("strip#section:1", True),)}})
    assert frames[-1][0][1] != (255, 255, 255)
    assert frames[-1][0][1][0] > 0


def test_ble_background_is_steady_without_brightening_dynamic_segments():
    colors = [(100, 80, 60), (200, 0, 0)]
    for brightness in (30, 70):
        output, hardware = apply_segment_control(colors, "strip", PLACEMENT, None, "#ffffff", brightness=brightness)
        assert hardware == 100
        assert output[1] == (255, 255, 255)
        assert output[0] == tuple(int(c * brightness / 100) for c in colors[0])


def write_config(path):
    path.write_text("""background_color: '#123456'
devices:
  - address: strip
    type: ble
    protocol: segment
    segments: 2
    sections:
      - {index: 0, x: 0, y: 0, z: 0}
      - {index: 1, x: 0.05, y: 0, z: 0, enabled: false}
""", encoding="utf-8")


def test_editor_and_device_config_roundtrip_and_preview(tmp_path):
    path = tmp_path / "devices.yaml"
    write_config(path)
    controller = SpatialController(DeviceService())
    controller.load(path)
    assert controller.background_color == "#123456"
    assert not controller.nodes["strip#section:1"].enabled
    controller.set_segment_enabled("strip#section:0", False)
    controller.set_segment_enabled("strip#section:1", True)
    controller.background_color = "#abcdef"
    controller.save()
    configs = load_device_config(path)
    save_device_config(path, configs)
    controller.load(path)
    assert controller.background_color == "#abcdef"
    assert [n.enabled for n in controller.snapshot()] == [False, True]
    adapter = SimulationMultiAdapter.from_configs(load_device_config(path))
    adapter.send_baked_frame(0, {"strip": "#ff0000"})
    assert adapter.preview_snapshot()["node_colors"] == {"strip#section:0": "#abcdef", "strip#section:1": "#ff0000"}


def test_gui_segment_controls_apply_live_and_save(tmp_path, monkeypatch):
    QtCore = pytest.importorskip("PySide6.QtCore")
    QtGui = pytest.importorskip("PySide6.QtGui")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from dreamsync.gui.main_window import create_main_window
    from dreamsync.gui.qt import require_qt
    from dreamsync.gui.settings import GuiSettings
    from dreamsync.gui.services.runtime_supervisor import RuntimeSupervisor

    updates = []
    monkeypatch.setattr(RuntimeSupervisor, "update_runtime_control", lambda self, **kw: updates.append(kw) or kw)
    monkeypatch.setattr(QtWidgets.QColorDialog, "getColor", lambda *args: QtGui.QColor("#aabbcc"))
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    path = tmp_path / "devices.yaml"
    write_config(path)
    window = create_main_window(require_qt(), GuiSettings(), config_path=path)
    try:
        listing = window.findChild(QtWidgets.QListWidget, "segmentEnabledList")
        assert listing.count() == 2
        listing.item(0).setCheckState(QtCore.Qt.CheckState.Unchecked)
        assert dict(updates[-1]["segment_enabled"])["strip#section:0"] is False
        combo = window.findChild(QtWidgets.QComboBox, "segmentPresetCombo")
        combo.setCurrentIndex(combo.findData("even"))
        window.findChild(QtWidgets.QPushButton, "applySegmentPresetButton").click()
        assert dict(updates[-1]["segment_enabled"]) == {"strip#section:0": False, "strip#section:1": True}
        window.findChild(QtWidgets.QPushButton, "backgroundSetButton").click()
        assert updates[-1]["room_background_color"] == "#aabbcc"
        save = next(b for b in window.findChildren(QtWidgets.QPushButton) if b.text() == "Save Layout")
        save.click()
        app.processEvents()
        configs = load_device_config(path)
        assert configs[0].background_color == "#aabbcc"
        assert [s.enabled for s in configs[0].placement.sections] == [False, True]
    finally:
        window.close()


def test_invalid_background_and_boolean_rejected(tmp_path):
    path = tmp_path / "devices.yaml"
    write_config(path)
    path.write_text(path.read_text().replace("enabled: false", "enabled: 'false'"))
    with pytest.raises(ValueError, match="boolean"):
        load_device_config(path)
    with pytest.raises(ValueError, match="#RRGGBB"):
        RuntimeControlBus().update(background_color="white")
