from pathlib import Path
from types import SimpleNamespace

import pytest

from dreamsync.gui.controllers.spatial_controller import SpatialController
from dreamsync.gui.services.device_service import DeviceService
from dreamsync.gui.services.show_service import ShowService
from dreamsync.output.auto_detect import load_device_config, save_device_config
from dreamsync.output.govee_ble import GoveeBleAdapter, GoveeBleConfig
from dreamsync.output.govee_lan import MultiGoveeLanAdapter
from dreamsync.output.null_adapter import SimulationMultiAdapter
from dreamsync.output.roles import DeviceRole
from dreamsync.render import RenderMode, SegmentRenderer
from dreamsync.show.models import Show, ShowCue, ShowTimeline, ShowTrack
from dreamsync.show.runtime import ShowPlaybackRuntime
from dreamsync.show.runtime_control import RuntimeControlBus
from dreamsync.spatial.mapper import SpatialMapper
from dreamsync.spatial.models import DevicePlacement, GridCell, SpatialCellState
from dreamsync.director import EffectMode, LightingIntent


def timeline():
    return ShowTimeline("song.mp3", 10, 120, 4, (0, .5), (0,), (
        ShowCue(0, "solid", ("#ff0000",), .5, 1, {}, "cut", 0),
    ), {})


@pytest.mark.parametrize("path", ["ordinary", "continuous", "grid", "baked"])
@pytest.mark.parametrize("ble", [False, True])
def test_bulbs_hot_switch_between_static_and_dynamic(path, ble):
    frames = []
    target = GoveeBleAdapter(GoveeBleConfig(address="bulb", segments=1)) if ble else SimpleNamespace(
        config=SimpleNamespace(device_ip="bulb"),
        send_frame=lambda colors: frames.append(tuple(colors)) or True,
    )
    placement = DevicePlacement(0, 0, enabled=False)
    renderer = SegmentRenderer(segments=1, mode=RenderMode.SOLID)
    output = MultiGoveeLanAdapter(
        [] if ble else [(target, renderer, DeviceRole.PRIMARY, 1, placement)],
        ble_followers=[(target, DeviceRole.PRIMARY, 1, placement)] if ble else [],
        spatial_mapper=SpatialMapper(enabled=True) if path == "continuous" else None,
        background_color="#123456",
    )

    def send(params, intensity=.5):
        intent = LightingIntent(EffectMode.AMBIENT, intensity, 1, 120, "#ff0000")
        if path == "baked":
            output.send_baked_frame(0, {"bulb": "#ff0000"}, params=params)
        elif path == "grid":
            output.send_spatial_scene(0, {GridCell.CENTER: SpatialCellState(intent, 1, params=params)})
        else:
            output.send_frame(0, intent, params=params)
        return output.final_frame_snapshot()["device_rgb"]["bulb"][0]

    assert send(None) == (18, 52, 86)
    if ble:
        assert target._queue.get_nowait() == (18, 52, 86, 100)
    assert send({"show_background_color": "#aabbcc"}, .1) == (170, 187, 204)
    assert send({"show_background_color": "#aabbcc", "runtime_control": {
        "background_color": "#445566", "room_background_color": "#ffffff",
    }}, .9) == (68, 85, 102)
    # A saved static bulb can rejoin the current dynamic frame, including spatial output.
    dynamic = send({"runtime_control": {"segment_enabled": (("bulb", True),)}})
    assert dynamic[0] > 0 and dynamic[1:] == (0, 0)
    assert send({"runtime_control": {"segment_enabled": (("bulb", False),)}}) == (18, 52, 86)


def test_bulb_static_mode_is_session_only_and_simulation_matches(tmp_path):
    path = tmp_path / "devices.yaml"
    path.write_text("devices:\n- {name: Lamp, address: bulb, type: ble, protocol: bulb, segments: 1, x: 0, y: 0}\n")
    controller = SpatialController(DeviceService())
    controller.load(path)
    controller.set_segment_enabled("bulb", False)
    controller.background_color = "#123456"
    controller.save()
    assert "enabled" not in path.read_text(encoding="utf-8")
    save_device_config(path, load_device_config(path))
    assert load_device_config(path)[0].placement.enabled is True
    controller.load(path)
    assert controller.nodes["bulb"].enabled
    output = SimulationMultiAdapter.from_configs(load_device_config(path))
    output.send_baked_frame(0, {"bulb": "#ff0000"}, params={"runtime_control": {"segment_enabled": (("bulb", False),)}})
    assert output.preview_snapshot()["node_colors"]["bulb"] == "#123456"


def test_show_color_persists_for_all_tracks_and_live_reset_restores_it(tmp_path):
    source = timeline()
    show = Show("Set", (ShowTrack("one.mp3", source), ShowTrack("two.mp3", source)), {})
    service = ShowService()
    show = service.set_background_color(show, "#AABBCC")
    path = tmp_path / "set.show.json"
    service.save_show(show, path)
    loaded = service.load_show(path)
    assert loaded.metadata["background_color"] == "#aabbcc"
    assert all(loaded.timeline_for_track(i).metadata["background_color"] == "#aabbcc" for i in range(2))
    assert "background_color" not in source.metadata
    frames = []
    adapter = SimpleNamespace(send_frame=lambda t, intent, **kw: frames.append(kw["params"]) or True)
    bus = RuntimeControlBus()
    runtime = ShowPlaybackRuntime(loaded.timeline_for_track(0), adapter, control_state_getter=bus.snapshot)
    runtime.tick(0)
    assert frames[-1]["show_background_color"] == "#aabbcc"
    bus.update(background_color="#112233")
    runtime.tick(.1)
    assert frames[-1]["runtime_control"]["background_color"] == "#112233"
    bus.update(background_color="")
    runtime.tick(.2)
    assert frames[-1]["show_background_color"] == "#aabbcc"
    assert loaded.metadata["background_color"] == "#aabbcc"
    assert "background_color" not in service.set_background_color(loaded).metadata


def test_dummy_room_has_valid_bent_strips_and_single_bulbs():
    path = Path(__file__).resolve().parents[1] / "devices-dummy.yaml"
    controller = SpatialController(DeviceService())
    nodes = controller.load(path)
    layouts = controller.validate_layout()
    assert len(layouts) == 4
    assert all(result.valid for result in layouts), layouts
    assert len([node for node in nodes if not node.is_section]) == 6
    bent = 0
    for result in layouts:
        chain = controller.chain_for_address(result.chain_key)
        axes = {next(i for i, (a, b) in enumerate(zip((first.x, first.y, first.z), (second.x, second.y, second.z))) if a != b)
                for first, second in zip(chain, chain[1:])}
        bent += len(axes) > 1
    assert bent >= 2


def test_gui_bulb_toggle_show_save_and_live_override(tmp_path, monkeypatch):
    QtCore = pytest.importorskip("PySide6.QtCore")
    QtGui = pytest.importorskip("PySide6.QtGui")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from dreamsync.gui.main_window import create_main_window
    from dreamsync.gui.qt import require_qt
    from dreamsync.gui.settings import GuiSettings
    from dreamsync.gui.services.runtime_supervisor import RuntimeSupervisor

    updates = []
    monkeypatch.setattr(RuntimeSupervisor, "update_runtime_control", lambda self, **kw: updates.append(kw) or kw)
    chosen_color = ["#123456"]
    monkeypatch.setattr(QtWidgets.QColorDialog, "getColor", lambda *args: QtGui.QColor(chosen_color[0]))
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    config = tmp_path / "bulbs.yaml"
    config.write_text("devices:\n- {name: Lamp, address: bulb, type: ble, protocol: bulb, segments: 1, x: 0, y: 0}\n")
    original_config = config.read_bytes()
    show_path = tmp_path / "set.show.json"
    Show("Set", (ShowTrack(str(tmp_path / "pending.mp3")),), {}).to_json(show_path)
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", lambda *args, **kwargs: (str(show_path), ""))
    window = create_main_window(require_qt(), GuiSettings(), config_path=config)
    try:
        listing = window.findChild(QtWidgets.QListWidget, "segmentEnabledList")
        assert listing.count() == 1
        assert listing.item(0).text() == "Lamp"
        listing.item(0).setCheckState(QtCore.Qt.CheckState.Unchecked)
        assert dict(updates[-1]["segment_enabled"])["bulb"] is False
        listing.item(0).setCheckState(QtCore.Qt.CheckState.Checked)
        assert dict(updates[-1]["segment_enabled"])["bulb"] is True

        window.findChild(QtWidgets.QPushButton, "loadShowButton").click()
        app.processEvents()
        show_button = window.findChild(QtWidgets.QPushButton, "showBackgroundColorButton")
        show_button.click()
        window.findChild(QtWidgets.QPushButton, "saveShowButton").click()
        assert Show.from_json(show_path).metadata["background_color"] == "#123456"
        saved_show = show_path.read_bytes()
        chosen_color[0] = "#abcdef"
        live_button = window.findChild(QtWidgets.QPushButton, "liveBackgroundColorButton")
        assert window.findChild(QtWidgets.QWidget, "liveTabWidget").isAncestorOf(live_button)
        live_button.click()
        assert updates[-1] == {"background_color": "#abcdef"}
        window.findChild(QtWidgets.QPushButton, "resetLiveBackgroundColorButton").click()
        assert updates[-1] == {"background_color": ""}
        assert config.read_bytes() == original_config
        assert show_path.read_bytes() == saved_show
        window.findChild(QtWidgets.QPushButton, "loadShowButton").click()
        assert show_button.text() == "Static Color: #123456"
        window.findChild(QtWidgets.QPushButton, "resetShowBackgroundColorButton").click()
        window.findChild(QtWidgets.QPushButton, "saveShowButton").click()
        assert "background_color" not in Show.from_json(show_path).metadata
    finally:
        window.close()
