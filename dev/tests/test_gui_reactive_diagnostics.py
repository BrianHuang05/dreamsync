from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dreamsync.gui.main_window import (
    _format_active_live_effects,
    _format_reactive_bpm_heading,
    _format_reactive_cycle,
    _format_structure_similarity,
    _reactive_display_chord,
    create_main_window,
)
from dreamsync.gui.qt import require_qt
from dreamsync.gui.settings import GuiSettings
from dreamsync.gui.widgets.reactive_chord_history_view import (
    build_reactive_chord_history_view,
)
from dreamsync.gui.widgets.reactive_harmonic_debug_view import (
    build_reactive_harmonic_debug_view,
)
from dreamsync.gui.widgets.reactive_waveform_view import (
    _format_effect_cue_label,
    build_reactive_waveform_view,
)


def _application():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_live_chord_estimate_precedes_beat_aligned_commit_in_ui():
    displayed, confirming = _reactive_display_chord(
        {
            "harmonic_chord": "F",
            "detected_chord": "C",
        }
    )

    assert displayed == "F"
    assert confirming


def test_cycle_readout_formats_bar_beat_and_meter_position():
    text = _format_reactive_cycle(
        {
            "available": True,
            "current_bar": 3,
            "current_beat": 12,
            "cycle_bars": 4,
            "cycle_beats": 16,
            "bars_to_next_cycle": 1,
            "beats_to_next_cycle": 4,
            "beats_since_cycle_start": 11,
            "time_signature": (4, 4),
        }
    )

    assert "Current Bar: 3 / 4" in text
    assert "Current Beat: 12 / 16" in text
    assert "Cycle Length: 4 bars (16 beats)" in text
    assert "Bars to next Cycle: 1" in text
    assert "Beats to next Cycle: 4" in text
    assert "Detected Time Signature: 4/4" in text


def test_beat_detector_heading_reflects_overridden_cycle_bpm():
    assert _format_reactive_bpm_heading(60.0) == (
        "Beat detector: 60.0 BPM"
    )
    assert _format_reactive_bpm_heading(240.0) == (
        "Beat detector: 240.0 BPM"
    )


def test_upcoming_effect_label_distinguishes_bar_and_phrase_cues():
    assert _format_effect_cue_label(
        "small_color_move",
        "bar_marker",
        "armed",
        0.64,
    ) == "BAR · small_color_move · armed 64%"
    assert _format_effect_cue_label(
        "wave_drift",
        "phrase_reset",
        "scheduled",
        0.72,
    ) == "PHRASE · wave_drift · scheduled 72%"


def test_active_effect_readout_includes_renderer_and_decay() -> None:
    text = _format_active_live_effects(
        (
            {
                "effect": "wave_drift",
                "render_mode": "pulse",
                "native_render_mode": "wave",
                "override_source": "bass enter route",
                "source": "bass enter route",
                "decay_seconds": 0.5,
                "remaining_seconds": 0.25,
                "effect_speed_beats": 1,
                "effect_origin": "center",
            },
            {
                "effect": "wave_drift",
                "render_mode": "wave",
                "source": "effect bank",
                "decay_seconds": None,
                "remaining_seconds": None,
            },
        )
    )

    assert "Preset wave_drift (native renderer=wave)" in text
    assert "active renderer=pulse (flash)" in text
    assert "overridden by bass enter route" in text
    assert "0.25s remaining / 0.50s decay" in text
    assert "speed=1 beat" in text
    assert "origin=center" in text
    assert "Preset wave_drift → active renderer=wave (wave)" in text


def test_preview_exposes_frame_freeze_and_output_provenance_readout():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    app = _application()
    window = create_main_window(
        require_qt(),
        GuiSettings(),
        config_path=Path("dev/devices-dummy.yaml"),
    )

    freeze = window.findChild(
        QtWidgets.QCheckBox,
        "simulationFreezeFrameCheck",
    )
    diagnostics = window.findChild(
        QtWidgets.QLabel,
        "simulationFrameDiagnosticsLabel",
    )

    assert freeze is not None
    assert "last valid per-device RGB" in freeze.toolTip()
    assert diagnostics is not None
    assert diagnostics.text() == "Frame: waiting for output"
    freeze.setChecked(True)
    assert freeze.isChecked()
    window.close()
    app.processEvents()


def test_waveform_widget_renders_centered_upcoming_beats_and_effects():
    app = _application()
    view = build_reactive_waveform_view(require_qt())
    view.set_diagnostic_data(
        ((9.0, 0.2), (9.5, 0.8), (10.0, 0.4)),
        (9.0, 9.5, 10.0),
        downbeats=(9.0,),
        manual_beats=(
            {"t": 9.5, "kind": "beat"},
            {"t": 10.0, "kind": "downbeat"},
        ),
        predicted_beats=(
            {"t": 10.5, "downbeat": False, "beat_in_bar": 2},
            {"t": 11.0, "downbeat": True, "beat_in_bar": 1},
        ),
        upcoming_effects=(
            {
                "t": 11.0,
                "effect": "ripple",
                "cue_class": "boundary",
                "state": "scheduled",
                "confidence": 0.8,
            },
        ),
        effect_triggers=(
            {
                "t": 9.5,
                "effect": "drop_blast",
                "render_mode": "pulse",
                "source": "structural action",
            },
        ),
        now_t=10.0,
        window_seconds=2.0,
        bpm=120.0,
    )
    view.resize(900, 260)
    view.show()
    app.processEvents()

    assert not view.grab().isNull()
    assert view.property("timelineNow") == 10.0
    assert view.property("predictedBeats")[1] == {
        "t": 11.0,
        "downbeat": True,
        "beat_in_bar": 1,
    }
    assert view.property("upcomingEffects")[0]["effect"] == "ripple"
    assert view.property("effectTriggers")[0]["effect"] == "drop_blast"
    assert view.property("pastEffectLineStyle") == "solid"
    assert view.property("upcomingEffectLineStyle") == "dashed"
    view.deleteLater()


def test_structure_similarity_readout_exposes_causal_evidence_and_action():
    text = _format_structure_similarity(
        {
            "structure_configured_meter": (4, 4),
            "structure_current_bar": 12,
            "structure_phrase_hypotheses": (
                {
                    "bars": 8,
                    "probability": 0.82,
                    "source": "song_local_recurrence",
                },
            ),
            "structure_section_id": "B",
            "structure_top_matches": (
                {"right_bar": 4, "combined": 0.91},
            ),
            "structure_upcoming_boundaries": (
                {"target_bar": 16, "predicted_probability": 0.78},
            ),
            "structure_action_history": (
                {
                    "outcome": "applied",
                    "requested_effect": "pulse",
                    "applied_effect": "pulse",
                },
            ),
        }
    )

    assert "Configured Meter: 4/4" in text
    assert "8 bars (0.82, song_local_recurrence)" in text
    assert "Section: B" in text
    assert "bar 4 (0.91)" in text
    assert "bar 16 (0.78)" in text
    assert "applied: pulse -> pulse" in text


def test_chord_history_widget_preserves_duplicate_and_unknown_bar_slots():
    _application()
    view = build_reactive_chord_history_view(require_qt())

    view.set_chord_history("G", ("C", "", "C"))

    assert view.property("previousChords") == ["C", "", "C"]
    view.deleteLater()


def test_chord_history_widget_exposes_current_and_previous_chords():
    _application()
    view = build_reactive_chord_history_view(require_qt())

    view.set_chord_history("Dm", ("F", "C", "Am"))

    assert view.property("currentChord") == "Dm"
    assert view.property("previousChords") == ["F", "C", "Am"]
    assert "Current chord Dm" in view.accessibleDescription()
    view.deleteLater()


def test_harmonic_widget_renders_octave_folded_note_evidence():
    app = _application()
    view = build_reactive_harmonic_debug_view(require_qt())
    note_evidence = (
        ("C", 1.0),
        ("C#", 0.04),
        ("D", 0.02),
        ("D#", 0.03),
        ("E", 0.82),
        ("F", 0.05),
        ("F#", 0.01),
        ("G", 0.76),
        ("G#", 0.02),
        ("A", 0.03),
        ("A#", 0.01),
        ("B", 0.04),
    )
    view.set_debug_data(
        note_evidence,
        tuple(value for _note, value in note_evidence),
        chord="C",
        chord_confidence=0.88,
        chord_tones=("C", "E", "G"),
        root_note="C",
        non_chord_tones=("D",),
        predicted_chord="F",
        prediction_seconds=1.5,
        prediction_confidence=0.75,
    )
    view.resize(900, 320)
    view.show()
    app.processEvents()

    assert not view.grab().isNull()
    assert view.property("rootNote") == "C"
    assert view.property("chordTones") == ["C", "E", "G"]
    assert view.property("nonChordTones") == ["D"]
    assert view.property("predictedChord") == "F"
    assert view.property("predictionMismatch") is False
    assert view.property("toneRoleColors") == {
        "root": "#ef4444",
        "chord": "#f59e0b",
        "non_chord": "#2563eb",
    }
    view.set_debug_data(
        note_evidence,
        tuple(value for _note, value in note_evidence),
        chord="G",
        chord_confidence=0.82,
        chord_tones=("G", "B", "D"),
        root_note="G",
        predicted_chord="F",
        prediction_mismatch={
            "predicted_chord": "F",
            "actual_chord": "G",
            "timing_error": 0.35,
        },
    )
    assert view.property("predictionMismatch") is True
    view.close()


def test_reactive_diagnostics_resize_pop_out_and_return_from_fullscreen():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    QtCore = pytest.importorskip("PySide6.QtCore")
    QtTest = pytest.importorskip("PySide6.QtTest")
    app = _application()
    window = create_main_window(
        require_qt(),
        GuiSettings(),
        config_path=Path("dev/devices-dummy.yaml"),
    )
    window.show()
    app.processEvents()
    window.findChild(
        QtWidgets.QPushButton,
        "reactiveLiveModeButton",
    ).click()
    app.processEvents()

    splitter = window.findChild(
        QtWidgets.QSplitter,
        "reactiveDiagnosticsSplitter",
    )
    chord_group = window.findChild(
        QtWidgets.QGroupBox,
        "reactiveChordHistoryGroup",
    )
    harmonic_group = window.findChild(
        QtWidgets.QGroupBox,
        "reactiveHarmonicDebugGroup",
    )
    chord_button = window.findChild(
        QtWidgets.QPushButton,
        "reactiveChordFullscreenButton",
    )
    chord_popout_button = window.findChild(
        QtWidgets.QPushButton,
        "reactiveChordPopoutButton",
    )
    harmonic_button = window.findChild(
        QtWidgets.QPushButton,
        "reactiveHarmonicFullscreenButton",
    )
    harmonic_popout_button = window.findChild(
        QtWidgets.QPushButton,
        "reactiveHarmonicPopoutButton",
    )

    assert splitter is not None
    assert splitter.orientation() == QtCore.Qt.Orientation.Vertical
    assert splitter.count() == 3
    assert splitter.childrenCollapsible() is False
    assert splitter.opaqueResize() is True
    assert chord_group is not None
    assert harmonic_group is not None
    assert chord_button is not None
    assert chord_popout_button is not None
    assert harmonic_button is not None
    assert harmonic_popout_button is not None

    harmonic_group.setVisible(True)
    splitter.setSizes([160, 240, 320])
    app.processEvents()
    first_sizes = splitter.sizes()
    assert all(size > 0 for size in first_sizes)
    splitter.setSizes([260, 180, 280])
    app.processEvents()
    second_sizes = splitter.sizes()
    assert all(size > 0 for size in second_sizes)
    assert second_sizes != first_sizes

    chord_popout_button.click()
    app.processEvents()
    chord_state = window._dreamsync_reactive_diagnostic_windows["chord"]
    chord_window = chord_state["window"]
    assert chord_window is not None
    assert chord_window.isVisible()
    assert not chord_window.isFullScreen()
    assert chord_window.property("diagnosticMode") == "popout"
    assert chord_state["view"] is not None
    assert chord_state["view"] is not window.findChild(
        QtWidgets.QWidget,
        "reactiveChordHistoryView",
    )
    exit_button = chord_state["exit_button"]
    assert exit_button is not None
    assert exit_button.text() == "Exit Pop-out Mode"
    assert exit_button.isVisible()
    old_size = chord_window.size()
    chord_window.resize(old_size.width() + 120, old_size.height() + 80)
    app.processEvents()
    assert chord_window.size().width() > old_size.width()
    assert chord_window.size().height() > old_size.height()

    chord_state["view"].setFocus()
    QtTest.QTest.keyClick(
        chord_state["view"],
        QtCore.Qt.Key.Key_Escape,
    )
    app.processEvents()
    app.processEvents()
    assert chord_state["window"] is None
    assert chord_state["view"] is None
    assert window.centralWidget().tabText(
        window.centralWidget().currentIndex()
    ) == "Live"

    chord_button.click()
    app.processEvents()
    chord_window = chord_state["window"]
    assert chord_window is not None
    assert chord_window.isFullScreen()
    assert chord_window.property("diagnosticMode") == "fullscreen"
    assert chord_state["exit_button"].isVisible()

    chord_state["view"].setFocus()
    QtTest.QTest.keyClick(
        chord_state["view"],
        QtCore.Qt.Key.Key_Escape,
    )
    app.processEvents()
    app.processEvents()
    assert chord_state["window"] is None
    assert chord_state["view"] is None
    assert window.centralWidget().tabText(
        window.centralWidget().currentIndex()
    ) == "Live"
    assert chord_group.isVisible()

    harmonic_popout_button.click()
    app.processEvents()
    harmonic_state = window._dreamsync_reactive_diagnostic_windows[
        "harmonic"
    ]
    harmonic_window = harmonic_state["window"]
    assert harmonic_window is not None
    assert not harmonic_window.isFullScreen()
    assert harmonic_state["mode"] == "popout"
    assert harmonic_state["view"].objectName() == (
        "reactiveHarmonicPopoutView"
    )
    assert harmonic_state["exit_button"].text() == "Exit Pop-out Mode"

    harmonic_button.click()
    app.processEvents()
    assert harmonic_window.isFullScreen()
    assert harmonic_state["mode"] == "fullscreen"
    harmonic_state["exit_button"].click()
    window.close()
    app.processEvents()
