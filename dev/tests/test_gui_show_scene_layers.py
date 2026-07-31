from __future__ import annotations

import json
from pathlib import Path

import pytest

from dreamsync.gui.main_window import create_main_window
from dreamsync.gui.qt import require_qt
from dreamsync.gui.settings import GuiSettings, GuiSettingsStore
from dreamsync.show.models import Show, ShowCue, ShowTimeline, ShowTrack


def _write_show(path: Path, audio_path: Path) -> None:
    timeline = ShowTimeline(
        song_path=str(audio_path),
        duration=10.0,
        bpm=120.0,
        time_signature=4,
        beat_times=(0.0, 0.5, 1.0),
        downbeat_times=(0.0,),
        cues=(
            ShowCue(
                t=0.0,
                render_mode="solid",
                color_palette=("#123456",),
                intensity=1.0,
                speed=1.0,
                params={
                    "spatial_preset": "ripple_right_to_left",
                    "wave_rate_mult": 2.0,
                    "active_instrument_routes": [
                        {
                            "instrument": "vocals",
                            "confidence_min": 0.8,
                        }
                    ],
                    "scene_layers": [
                        {
                            "layer_category": "slice",
                            "spatial_preset": "ripple_left_to_right",
                            "spatial_direction": "x+",
                            "target_groups": ["odd"],
                            "exclude_groups": ["even"],
                            "duration_s": 2.0,
                            "layer_weight": 0.8,
                            "color_bias": "#ff0000",
                            "time_offset_s": 0.5,
                            "trigger_mode": "oneshot",
                            "layer_blend": "mix",
                            "layer_priority": 20,
                        },
                        {
                            "layer_category": "slice",
                            "spatial_preset": "ripple_right_to_left",
                            "spatial_direction": "x-",
                            "target_groups": ["even"],
                            "duration_s": 3.0,
                            "layer_weight": 0.7,
                            "color_bias": "#0000ff",
                        },
                    ],
                },
                transition="cut",
                transition_beats=0,
            ),
        ),
        metadata={},
    )
    Show(
        name="Layer Test",
        tracks=(ShowTrack(audio_path=str(audio_path), timeline=timeline),),
        metadata={},
    ).to_json(path)


def test_scene_layer_editor_exposes_simplified_controls_and_saves_layers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    QtCore = pytest.importorskip("PySide6.QtCore")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    audio_path = tmp_path / "layers.mp3"
    audio_path.touch()
    show_path = tmp_path / "layers.show.json"
    _write_show(show_path, audio_path)
    window = create_main_window(require_qt(), GuiSettings())
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getOpenFileName",
        lambda *args, **kwargs: (str(show_path), ""),
    )
    window.findChild(QtWidgets.QPushButton, "loadShowButton").click()
    app.processEvents()

    layers_button = window.findChild(QtWidgets.QPushButton, "showCueLayersButton")
    assert layers_button is not None
    assert layers_button.text() == "2 layers"
    assert len(json.loads(str(layers_button.property("sceneLayersJson")))) == 2
    cue_table = window.findChild(QtWidgets.QTableWidget, "showCuesTable")
    assert cue_table is not None
    assert cue_table.cellWidget(0, 10).currentData() == "center"
    assert cue_table.cellWidget(0, 11).currentData() == "x-"
    observed: dict[str, object] = {}

    def inspect_dialog() -> None:
        dialog = app.activeModalWidget()
        assert dialog is not None
        table = dialog.findChild(QtWidgets.QTableWidget, "showSceneLayersTable")
        assert table is not None
        observed["headers"] = [
            table.horizontalHeaderItem(index).text()
            for index in range(table.columnCount())
        ]
        observed["intensity_hidden"] = table.isColumnHidden(6)
        table.cellWidget(0, 5).setValue(1.25)
        table.cellWidget(0, 6).setValue(0.55)
        dialog.accept()

    QtCore.QTimer.singleShot(0, inspect_dialog)
    layers_button.click()
    assert observed["headers"] == [
        "Effect",
        "Origin",
        "Direction",
        "Target groups",
        "Exclude groups",
        "Duration (s)",
        "Intensity",
        "Color bias",
    ]
    assert observed["intensity_hidden"] is True
    assert window.findChild(QtWidgets.QComboBox, "showCueLayerTriggerCombo") is None

    window.findChild(QtWidgets.QPushButton, "saveShowButton").click()
    app.processEvents()
    saved = Show.from_json(show_path)
    layers = saved.tracks[0].timeline.cues[0].params["scene_layers"]
    saved_params = saved.tracks[0].timeline.cues[0].params
    assert len(layers) == 2
    assert layers[0]["duration_s"] == pytest.approx(1.25)
    assert layers[0]["layer_weight"] == pytest.approx(0.55)
    assert layers[0]["trigger_mode"] == "continuous"
    assert layers[0]["layer_blend"] == "max"
    assert layers[0]["layer_priority"] == 0
    assert "time_offset_s" not in layers[0]
    assert layers[1]["layer_priority"] == 1
    assert saved_params["spatial_origin"] == {"x": 0.0, "y": 0.0, "z": 0.0}
    assert saved_params["spatial_direction"] == "x-"
    assert "spatial_preset" not in saved_params
    assert "wave_rate_mult" not in saved_params
    assert saved_params["active_instrument_routes"][0]["instrument"] == "vocals"
    window.close()
    app.processEvents()


def test_cue_column_visibility_persists_between_windows(tmp_path: Path) -> None:
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    store = GuiSettingsStore(tmp_path / "gui-settings.json")
    window = create_main_window(
        require_qt(),
        GuiSettings(show_editor_hidden_columns=(4, 14)),
        settings_store=store,
    )
    table = window.findChild(QtWidgets.QTableWidget, "showCuesTable")
    columns_button = window.findChild(QtWidgets.QToolButton, "showColumnsButton")
    assert table is not None
    assert columns_button is not None
    assert table.isColumnHidden(4)
    assert table.isColumnHidden(14)

    speed_action = next(
        action
        for action in columns_button.menu().actions()
        if action.text() == "Speed"
    )
    speed_action.setChecked(False)
    assert table.isColumnHidden(5)
    window.close()
    app.processEvents()

    loaded = store.load()
    assert set(loaded.show_editor_hidden_columns) == {4, 5, 14}
    reopened = create_main_window(
        require_qt(),
        loaded,
        settings_store=store,
    )
    reopened_table = reopened.findChild(QtWidgets.QTableWidget, "showCuesTable")
    assert reopened_table is not None
    assert reopened_table.isColumnHidden(4)
    assert reopened_table.isColumnHidden(5)
    assert reopened_table.isColumnHidden(14)
    reopened.close()
    app.processEvents()
