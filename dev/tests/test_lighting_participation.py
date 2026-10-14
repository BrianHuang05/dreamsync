"""Session-scoped static/dynamic participation for lights and groups."""

from pathlib import Path

import pytest
import yaml

from dreamsync.gui.controllers.lighting_participation import LightingParticipation
from dreamsync.gui.services.runtime_supervisor import RuntimeSupervisor
from dreamsync.output.auto_detect import load_device_config
from dreamsync.show.models import Show, ShowTrack


MEMBERS = {
    "lamp-a": frozenset({"bulbs"}),
    "lamp-b": frozenset({"bulbs"}),
    "strip#section:0": frozenset({"odd"}),
    "strip#section:1": frozenset(),
}


def test_everything_starts_dynamic_and_newest_change_wins_in_both_directions():
    state = LightingParticipation()
    assert all(state.resolve(MEMBERS).values())

    state.set_group("bulbs", False)
    assert state.resolve(MEMBERS) == {
        "lamp-a": False, "lamp-b": False, "strip#section:0": True, "strip#section:1": True,
    }
    assert state.group_state("bulbs", MEMBERS) == "static"

    # A later single-light change beats the earlier group change.
    state.set_node("lamp-a", True)
    resolved = state.resolve(MEMBERS)
    assert resolved["lamp-a"] is True and resolved["lamp-b"] is False
    assert state.group_state("bulbs", MEMBERS) == "mixed"

    # A later group change beats the earlier single-light change.
    state.set_group("bulbs", True)
    assert state.resolve(MEMBERS)["lamp-a"] is True
    assert state.group_state("bulbs", MEMBERS) == "dynamic"

    state.set_node("strip#section:1", False)
    state.set_group("all", False)
    assert not any(state.resolve(MEMBERS).values())
    state.set_group("odd", True)
    assert state.resolve(MEMBERS)["strip#section:0"] is True
    assert state.resolve(MEMBERS)["lamp-a"] is False
    assert state.group_state("all", MEMBERS) == "mixed"
    assert state.group_state("missing", MEMBERS) == "empty"


def test_log_compacts_per_target_and_follows_current_membership():
    state = LightingParticipation()
    state.set_group("bulbs", False)
    state.set_node("lamp-a", True)
    state.set_group("bulbs", False)  # re-issued: now newer than the lamp-a change
    assert [(c.scope, c.target) for c in state.changes] == [("node", "lamp-a"), ("group", "bulbs")]
    assert state.resolve(MEMBERS)["lamp-a"] is False
    # A light added to the group later inherits the group's latest setting.
    assert state.resolve({**MEMBERS, "lamp-c": frozenset({"bulbs"})})["lamp-c"] is False
    state.clear()
    assert all(state.resolve(MEMBERS).values())


class _Session:
    def __init__(self) -> None:
        self.control: dict = {}

    def update_runtime_control(self, **changes):
        self.control.update(changes)
        return dict(self.control)

    def runtime_control_snapshot(self):
        return dict(self.control)


class _Handle:
    def __init__(self, session) -> None:
        self.session_ref = [session]
        self.mode = "saved_show"
        self.running = True
        self.summary = None
        self.error = None

    def stop(self) -> None:
        self.running = False


class _SessionService:
    def __init__(self) -> None:
        self.sessions: list[_Session] = []

    def start_compiled_show_session(self, show, **kwargs):
        session = _Session()
        self.sessions.append(session)
        return _Handle(session)

    def start_reactive_live_session(self, **kwargs):
        return self.start_compiled_show_session(None)


def test_supervisor_reapplies_participation_to_every_new_session_but_not_live_overrides(tmp_path):
    service = _SessionService()
    supervisor = RuntimeSupervisor(session_service=service, pipeline_factory=lambda: None)
    show = Show("Set", (ShowTrack(str(tmp_path / "one.mp3")),), {})
    participation = {"segment_enabled": (("lamp-a", False),), "room_background_color": "#123456"}

    assert supervisor.set_persistent_runtime_control(**participation) == {}
    supervisor.start_compiled_show(show)
    first = service.sessions[-1]
    assert first.control == participation

    assert supervisor.update_runtime_control(background_color="#abcdef")["background_color"] == "#abcdef"
    supervisor.set_persistent_runtime_control(segment_enabled=(("lamp-a", True),))
    assert first.control["segment_enabled"] == (("lamp-a", True),)

    supervisor.start_compiled_show(show)
    second = service.sessions[-1]
    assert second is not first
    assert second.control == {"segment_enabled": (("lamp-a", True),), "room_background_color": "#123456"}
    assert "background_color" not in second.control


def test_gui_group_toggles_resolve_newest_first_and_persist_nowhere(tmp_path, monkeypatch):
    QtCore = pytest.importorskip("PySide6.QtCore")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from dreamsync.gui.main_window import create_main_window
    from dreamsync.gui.qt import require_qt
    from dreamsync.gui.settings import GuiSettings

    updates = []
    monkeypatch.setattr(RuntimeSupervisor, "update_runtime_control", lambda self, **kw: updates.append(kw) or kw)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    config = tmp_path / "room.yaml"
    config.write_text(
        "groups:\n- {id: bulbs, name: Bulbs}\n"
        "devices:\n"
        "- {name: Lamp A, address: lamp-a, type: ble, protocol: bulb, segments: 1, x: 0, y: 0, groups: [bulbs]}\n"
        "- {name: Lamp B, address: lamp-b, type: ble, protocol: bulb, segments: 1, x: 0.1, y: 0, groups: [bulbs], enabled: false}\n"
        "- address: strip\n  type: ble\n  protocol: segment\n  segments: 2\n"
        "  sections:\n  - {index: 0, x: 0.2, y: 0, z: 0}\n  - {index: 1, x: 0.25, y: 0, z: 0}\n",
        encoding="utf-8",
    )
    show_path = tmp_path / "set.show.json"
    Show("Set", (ShowTrack(str(tmp_path / "pending.mp3")),), {}).to_json(show_path)
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", lambda *args, **kwargs: (str(show_path), ""))
    window = create_main_window(require_qt(), GuiSettings(), config_path=config)
    try:
        all_keys = {"lamp-a", "lamp-b", "strip#section:0", "strip#section:1"}
        # A fresh app session ignores the config's enabled flag and starts fully dynamic.
        assert dict(updates[-1]["segment_enabled"]) == dict.fromkeys(all_keys, True)

        groups = window.findChild(QtWidgets.QListWidget, "groupEnabledList")
        # Lighting toggles live in their own panel, apart from group membership editing.
        assert window.findChild(QtWidgets.QGroupBox, "groupLightingBox").isAncestorOf(groups)
        assert not window.findChild(QtWidgets.QGroupBox, "spatialGroupBox").isAncestorOf(groups)
        membership = window.findChild(QtWidgets.QListWidget, "spatialMembershipList")
        assert window.findChild(QtWidgets.QGroupBox, "spatialGroupBox").isAncestorOf(membership)
        rows = {groups.item(i).data(QtCore.Qt.ItemDataRole.UserRole): groups.item(i) for i in range(groups.count())}
        assert set(rows) == {"all", "bulbs"}
        rows["bulbs"].setCheckState(QtCore.Qt.CheckState.Unchecked)
        assert dict(updates[-1]["segment_enabled"]) == {
            "lamp-a": False, "lamp-b": False, "strip#section:0": True, "strip#section:1": True,
        }

        # The selected light is Lamp A; re-enabling it alone overrides the older group change.
        lights = window.findChild(QtWidgets.QListWidget, "segmentEnabledList")
        assert lights.item(0).text() == "Lamp A"
        lights.item(0).setCheckState(QtCore.Qt.CheckState.Checked)
        assert dict(updates[-1]["segment_enabled"])["lamp-a"] is True
        assert dict(updates[-1]["segment_enabled"])["lamp-b"] is False
        rows = {groups.item(i).data(QtCore.Qt.ItemDataRole.UserRole): groups.item(i) for i in range(groups.count())}
        assert rows["bulbs"].checkState() == QtCore.Qt.CheckState.PartiallyChecked
        assert rows["all"].checkState() == QtCore.Qt.CheckState.PartiallyChecked

        # A newer group change overrides the single-light change.
        rows["all"].setCheckState(QtCore.Qt.CheckState.Unchecked)
        assert dict(updates[-1]["segment_enabled"]) == dict.fromkeys(all_keys, False)
        rows = {groups.item(i).data(QtCore.Qt.ItemDataRole.UserRole): groups.item(i) for i in range(groups.count())}
        rows["bulbs"].setCheckState(QtCore.Qt.CheckState.Checked)
        assert dict(updates[-1]["segment_enabled"]) == {
            "lamp-a": True, "lamp-b": True, "strip#section:0": False, "strip#section:1": False,
        }

        # Nothing above reaches the device config or a saved Show.
        save = next(b for b in window.findChildren(QtWidgets.QPushButton) if b.text() == "Save Layout")
        save.click()
        app.processEvents()
        saved = load_device_config(config)
        assert [cfg.placement.enabled for cfg in saved if not cfg.placement.sections] == [True, False]
        entries = yaml.safe_load(config.read_text(encoding="utf-8"))["devices"]
        assert [entry.get("enabled") for entry in entries] == [None, False, None]
        assert all("enabled" not in section for section in entries[2]["sections"])
        window.findChild(QtWidgets.QPushButton, "loadShowButton").click()
        app.processEvents()
        window.findChild(QtWidgets.QPushButton, "saveShowButton").click()
        assert "segment" not in show_path.read_text(encoding="utf-8")
        assert Show.from_json(show_path).metadata == {}
    finally:
        window.close()


def test_controls_reach_the_bus_before_the_session_object_exists(tmp_path):
    """Sessions are built on their worker thread, so the handle's bus must be the target."""
    from dreamsync.gui.services.session_service import SessionService
    from dreamsync.show.runtime_control import RuntimeControlBus

    class _LateHandle(_Handle):
        def __init__(self, bus) -> None:
            super().__init__(None)
            self.runtime_control = bus

    class _LateService(SessionService):
        def __init__(self) -> None:
            super().__init__()
            self.handles = []

        def start_compiled_show_session(self, show, **kwargs):
            handle = _LateHandle(self._new_runtime_control_bus())
            self.handles.append(handle)
            return handle

    service = _LateService()
    supervisor = RuntimeSupervisor(session_service=service, pipeline_factory=lambda: None)
    show = Show("Set", (ShowTrack(str(tmp_path / "one.mp3")),), {})

    supervisor.set_persistent_runtime_control(segment_enabled=(("bulb", False),))
    assert service.runtime_control_defaults == {"segment_enabled": (("bulb", False),)}
    supervisor.start_compiled_show(show)
    bus = service.handles[-1].runtime_control
    assert bus.snapshot().segment_enabled == (("bulb", False),)
    assert supervisor.runtime_control_snapshot()["segment_enabled"] == (("bulb", False),)

    # A toggle made while the session object is still absent is not lost.
    assert supervisor.active_session() is None
    result = supervisor.update_runtime_control(segment_enabled=(("bulb", True),))
    assert result["segment_enabled"] == (("bulb", True),)
    assert bus.snapshot().segment_enabled == (("bulb", True),)

    # Once the worker publishes the session, it shares the same bus.
    service.handles[-1].session_ref[0] = _Session()
    supervisor.update_runtime_control(background_color="#010203")
    assert bus.snapshot().background_color == "#010203"
    assert isinstance(service._new_runtime_control_bus(), RuntimeControlBus)
