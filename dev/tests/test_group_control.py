"""Tests for logical device and section group control."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dreamsync.groups.models import (
    ALL_GROUP_ID,
    GroupRuntimeState,
    GroupSelector,
    parse_group_definitions,
)
from dreamsync.groups.reactive_policy import (
    ReactiveGroupDescriptor,
    ReactiveGroupPolicy,
)
from dreamsync.director import EffectMode, LightingIntent
from dreamsync.output.auto_detect import load_device_config, save_device_config
from dreamsync.output.null_adapter import SimulationMultiAdapter
from dreamsync.render import RenderMode
from dreamsync.gui.controllers.spatial_controller import SpatialController
from dreamsync.gui.services.device_service import DeviceService
from dreamsync.show.runtime_control import (
    RuntimeControlBus,
    apply_runtime_control_to_intent_params,
)


class GroupModelTests(unittest.TestCase):
    def test_selector_supports_any_all_and_exclusions(self) -> None:
        memberships = {"all", "left", "strips"}
        self.assertTrue(
            GroupSelector(target_groups=("left", "top")).matches(memberships)
        )
        self.assertFalse(
            GroupSelector(
                target_groups=("left", "top"),
                target_match="all",
            ).matches(memberships)
        )
        self.assertFalse(
            GroupSelector(
                target_groups=("left",),
                exclude_groups=("strips",),
            ).matches(memberships)
        )

    def test_runtime_state_suppresses_only_targeted_contribution(self) -> None:
        state = GroupRuntimeState(disabled_groups=frozenset({"group-a"}))
        self.assertFalse(
            state.contribution_enabled(
                GroupSelector(target_groups=("group-a",))
            )
        )
        self.assertTrue(
            state.contribution_enabled(GroupSelector(target_groups=("left",)))
        )
        self.assertTrue(state.contribution_enabled(GroupSelector()))

    def test_reserved_all_definition_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "reserved"):
            parse_group_definitions([{"id": ALL_GROUP_ID, "name": "All"}])

    def test_runtime_control_bus_exposes_group_state_to_output_params(self) -> None:
        bus = RuntimeControlBus()
        state = bus.set_group_state(
            disabled_groups=("group-a",),
            enabled_groups=("group-b",),
            solo_groups=("left",),
        )
        intent = LightingIntent(
            mode=EffectMode.AMBIENT,
            intensity=1.0,
            speed=0.0,
            bpm=120.0,
            color="#ffffff",
        )
        _intent, params = apply_runtime_control_to_intent_params(
            intent,
            {},
            state,
        )
        assert params is not None
        runtime = params["runtime_control"]
        self.assertEqual(runtime["disabled_groups"], ("group-a",))
        self.assertEqual(runtime["enabled_groups"], ("group-b",))
        self.assertEqual(runtime["solo_groups"], ("left",))

    def test_runtime_state_can_force_enable_default_disabled_group(self) -> None:
        state = GroupRuntimeState.from_mapping(
            {"enabled_groups": ["group-a"]},
            default_disabled=("group-a",),
        )
        self.assertNotIn("group-a", state.disabled_groups)
        self.assertIn("group-a", state.enabled_groups)

    def test_reactive_policy_uses_frequency_bias_and_stays_stable(self) -> None:
        policy = ReactiveGroupPolicy(
            (
                ReactiveGroupDescriptor(
                    id="floor",
                    tags=("strip",),
                    centroid=(0.0, -0.8, 0.0),
                    node_count=4,
                ),
                ReactiveGroupDescriptor(
                    id="top",
                    tags=("accent",),
                    centroid=(0.0, 0.8, 0.0),
                    node_count=4,
                ),
            ),
            change_every_downbeats=4,
        )
        bass = policy.update(
            downbeat=True,
            routes=({"band": "bass"},),
        )
        self.assertEqual(bass.target_groups, ("floor",))
        stable = policy.update(
            downbeat=True,
            routes=({"band": "presence"},),
        )
        self.assertEqual(stable, bass)
        presence = policy.update(
            downbeat=False,
            routes=({"band": "presence"},),
            force_boundary=True,
        )
        self.assertEqual(presence.target_groups, ("top",))

    def test_reactive_policy_respects_disabled_and_solo_groups(self) -> None:
        policy = ReactiveGroupPolicy(
            (
                ReactiveGroupDescriptor("group-a", ("alternating",), (0, 0, 0), 2),
                ReactiveGroupDescriptor("group-b", ("alternating",), (1, 0, 0), 2),
            )
        )
        decision = policy.update(
            downbeat=True,
            runtime_state=GroupRuntimeState(
                disabled_groups=frozenset({"group-a"}),
                solo_groups=frozenset({"group-b"}),
            ),
        )
        self.assertEqual(decision.target_groups, ("group-b",))


class GroupConfigTests(unittest.TestCase):
    def _write(self, text: str) -> Path:
        directory = Path(tempfile.mkdtemp())
        path = directory / "devices.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_device_and_section_memberships_round_trip(self) -> None:
        path = self._write(
            """
groups:
  - id: left
    name: Left Group
    color: "#3366ff"
  - id: strips
    name: Strip Group
  - id: group-a
    name: Group A
devices:
  - name: Strip
    address: 10.0.0.2
    type: lan
    segments: 2
    groups: [strips, group-a]
    sections:
      - index: 0
        x: -0.5
        y: 0
        z: 0
        groups: [left]
      - index: 1
        x: 0.5
        y: 0
        z: 0
        exclude_groups: [group-a]
"""
        )
        configs = load_device_config(path)
        placement = configs[0].placement
        assert placement is not None
        self.assertEqual(placement.groups, ("strips", "group-a"))
        self.assertEqual(placement.sections[0].groups, ("left",))
        self.assertEqual(
            placement.sections[1].exclude_groups,
            ("group-a",),
        )

        saved = path.with_name("saved.yaml")
        save_device_config(saved, configs)
        reloaded = load_device_config(saved)
        self.assertEqual(reloaded, configs)
        self.assertIn("groups:", saved.read_text(encoding="utf-8"))

    def test_unknown_membership_is_rejected(self) -> None:
        path = self._write(
            """
groups:
  - id: left
    name: Left
devices:
  - address: 10.0.0.2
    groups: [missing]
"""
        )
        with self.assertRaisesRegex(ValueError, "unknown group"):
            load_device_config(path)

    def test_legacy_config_stays_group_free_when_saved(self) -> None:
        path = self._write(
            """
devices:
  - name: Bulb
    address: AA:BB
    type: ble
    protocol: bulb
    segments: 1
"""
        )
        configs = load_device_config(path)
        saved = path.with_name("legacy-saved.yaml")
        save_device_config(saved, configs)
        self.assertNotIn("\ngroups:", saved.read_text(encoding="utf-8"))

    def test_group_targeting_and_runtime_toggle_apply_per_section(self) -> None:
        path = self._write(
            """
groups:
  - id: group-a
    name: Group A
  - id: group-b
    name: Group B
devices:
  - name: Strip
    address: 10.0.0.2
    type: lan
    segments: 2
    sections:
      - index: 0
        x: -0.5
        y: 0
        z: 0
        groups: [group-a]
      - index: 1
        x: 0.5
        y: 0
        z: 0
        groups: [group-b]
"""
        )
        adapter = SimulationMultiAdapter.from_configs(
            load_device_config(path),
            render_mode=RenderMode.SOLID,
        )
        intent = LightingIntent(
            mode=EffectMode.AMBIENT,
            intensity=1.0,
            speed=0.0,
            bpm=120.0,
            color="#ff0000",
        )
        adapter.send_frame(
            0.0,
            intent,
            params={
                "target_groups": ["group-a"],
                "_render_mode": "solid",
            },
        )
        colors = adapter.preview_snapshot()["node_colors"]
        self.assertNotEqual(colors["10.0.0.2#section:0"], "#000000")
        self.assertEqual(colors["10.0.0.2#section:1"], "#000000")

        adapter.send_frame(
            0.1,
            intent,
            params={
                "target_groups": ["group-a"],
                "_render_mode": "solid",
                "runtime_control": {"disabled_groups": ["group-a"]},
            },
        )
        colors = adapter.preview_snapshot()["node_colors"]
        self.assertEqual(colors["10.0.0.2#section:0"], "#000000")

    def test_disabling_one_membership_keeps_overlapping_node_active(self) -> None:
        path = self._write(
            """
groups:
  - id: group-a
    name: Group A
  - id: left
    name: Left
devices:
  - address: 10.0.0.3
    segments: 1
    x: 0
    y: 0
    z: 0
    groups: [group-a, left]
"""
        )
        adapter = SimulationMultiAdapter.from_configs(
            load_device_config(path),
            render_mode=RenderMode.SOLID,
        )
        intent = LightingIntent(
            mode=EffectMode.AMBIENT,
            intensity=1.0,
            speed=0.0,
            bpm=120.0,
            color="#00ff00",
        )
        adapter.send_frame(
            0.0,
            intent,
            params={
                "_render_mode": "solid",
                "runtime_control": {"disabled_groups": ["group-a"]},
            },
        )
        self.assertNotEqual(
            adapter.preview_snapshot()["node_colors"]["10.0.0.3"],
            "#000000",
        )

    def test_layout_controller_edits_whole_strip_and_individual_section(self) -> None:
        path = self._write(
            """
groups:
  - id: strips
    name: Strips
devices:
  - name: Strip
    address: 10.0.0.4
    segments: 2
    groups: [strips]
    sections:
      - index: 0
        x: -0.05
        y: 0
        z: 0
      - index: 1
        x: 0
        y: 0
        z: 0
"""
        )
        controller = SpatialController(DeviceService())
        nodes = controller.load(path)
        group_a = controller.create_group("group-a", "Group A")
        controller.set_group_membership(
            nodes[0].key,
            group_a.id,
            True,
            whole_device=False,
        )
        controller.set_group_membership(
            nodes[1].key,
            "strips",
            False,
            whole_device=False,
        )
        controller.save()

        reloaded = SpatialController(DeviceService())
        reloaded_nodes = reloaded.load(path)
        self.assertIn("group-a", reloaded_nodes[0].groups)
        self.assertIn("strips", reloaded_nodes[1].exclude_groups)
        self.assertIn(
            "group-a",
            {definition.id for definition in reloaded.group_definitions},
        )

    def test_baked_frame_group_toggle_filters_sections(self) -> None:
        path = self._write(
            """
groups:
  - id: group-a
    name: Group A
  - id: group-b
    name: Group B
devices:
  - address: 10.0.0.5
    segments: 2
    sections:
      - index: 0
        x: -0.05
        y: 0
        z: 0
        groups: [group-a]
      - index: 1
        x: 0
        y: 0
        z: 0
        groups: [group-b]
"""
        )
        adapter = SimulationMultiAdapter.from_configs(load_device_config(path))
        adapter.send_baked_frame(
            0.0,
            {
                "10.0.0.5#section:0": "#ff0000",
                "10.0.0.5#section:1": "#00ff00",
            },
            params={
                "runtime_control": {
                    "disabled_groups": ["group-a"],
                }
            },
        )
        colors = adapter.preview_snapshot()["node_colors"]
        self.assertEqual(colors["10.0.0.5#section:0"], "#000000")
        self.assertEqual(colors["10.0.0.5#section:1"], "#00ff00")


if __name__ == "__main__":
    unittest.main()


def test_gui_exposes_group_membership_runtime_and_cue_controls(
    monkeypatch,
) -> None:
    import pytest

    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from dreamsync.gui.main_window import create_main_window
    from dreamsync.gui.qt import require_qt
    from dreamsync.gui.settings import GuiSettings

    directory = Path(tempfile.mkdtemp())
    path = directory / "devices.yaml"
    path.write_text(
        """
groups:
  - id: group-a
    name: Group A
  - id: group-b
    name: Group B
devices:
  - name: Strip
    address: strip-a
    segments: 2
    sections:
      - index: 0
        x: -0.05
        y: 0
        z: 0
      - index: 1
        x: 0
        y: 0
        z: 0
""",
        encoding="utf-8",
    )
    from dreamsync.show.models import Show, ShowCue, ShowTimeline, ShowTrack

    audio_path = directory / "picker-test.mp3"
    audio_path.touch()
    show_path = directory / "picker-test.show.json"
    timeline = ShowTimeline(
        song_path=str(audio_path),
        duration=1.0,
        bpm=120.0,
        time_signature=4,
        beat_times=(0.0,),
        downbeat_times=(0.0,),
        cues=(
            ShowCue(
                t=0.0,
                render_mode="solid",
                color_palette=("#123456",),
                intensity=1.0,
                speed=1.0,
                params={
                    "target_groups": ["group-a"],
                    "exclude_groups": ["group-b"],
                },
                transition="cut",
                transition_beats=0,
            ),
        ),
        metadata={},
    )
    Show(
        name="Picker Test",
        tracks=(
            ShowTrack(
                audio_path=str(audio_path),
                timeline=timeline,
            ),
        ),
        metadata={},
    ).to_json(show_path)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = create_main_window(require_qt(), GuiSettings(), config_path=path)
    membership = window.findChild(QtWidgets.QListWidget, "spatialMembershipList")
    group_list = window.findChild(QtWidgets.QListWidget, "spatialGroupList")
    disabled_groups = window.findChild(
        QtWidgets.QLineEdit,
        "runtimeDisabledGroupsEdit",
    )
    enabled_groups = window.findChild(
        QtWidgets.QLineEdit,
        "runtimeEnabledGroupsEdit",
    )
    solo_groups = window.findChild(
        QtWidgets.QLineEdit,
        "runtimeSoloGroupsEdit",
    )
    cue_table = window.findChild(QtWidgets.QTableWidget, "showCuesTable")
    assert membership is not None and membership.count() == 2
    assert group_list is not None and group_list.count() == 2
    assert disabled_groups is not None
    assert enabled_groups is not None
    assert solo_groups is not None
    assert cue_table is not None and cue_table.columnCount() == 31
    headers = [
        cue_table.horizontalHeaderItem(index).text()
        for index in range(cue_table.columnCount())
    ]
    assert headers[-4:] == [
        "Target Groups",
        "Group Match",
        "Exclude Groups",
        "Untargeted",
    ]
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getOpenFileName",
        lambda *args, **kwargs: (str(show_path), ""),
    )
    window.findChild(QtWidgets.QPushButton, "loadShowButton").click()
    app.processEvents()
    target_picker = window.findChild(
        QtWidgets.QToolButton,
        "showCueTargetGroupsPicker",
    )
    exclude_picker = window.findChild(
        QtWidgets.QToolButton,
        "showCueExcludeGroupsPicker",
    )
    assert target_picker is not None
    assert exclude_picker is not None
    assert target_picker.property("selectedGroupIds") == ["group-a"]
    assert exclude_picker.property("selectedGroupIds") == ["group-b"]
    target_options = {
        option.property("groupId"): option
        for option in target_picker.menu().findChildren(QtWidgets.QCheckBox)
    }
    assert set(target_options) == {"group-a", "group-b"}
    target_options["group-b"].setChecked(True)
    app.processEvents()
    assert target_picker.property("selectedGroupIds") == [
        "group-a",
        "group-b",
    ]
    assert target_picker.text() == "Group A, Group B"
    window.findChild(QtWidgets.QPushButton, "saveShowButton").click()
    app.processEvents()
    saved_show = Show.from_json(show_path)
    saved_params = saved_show.tracks[0].timeline.cues[0].params
    assert saved_params["target_groups"] == ["group-a", "group-b"]
    assert saved_params["exclude_groups"] == ["group-b"]
    window.close()
    app.processEvents()
