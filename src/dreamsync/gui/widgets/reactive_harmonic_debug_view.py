"""Live FFT, pitch-class, and chord-wheel diagnostic widget."""

from __future__ import annotations

import math


_PITCH_NAMES: tuple[str, ...] = (
    "C",
    "C#",
    "D",
    "D#",
    "E",
    "F",
    "F#",
    "G",
    "G#",
    "A",
    "A#",
    "B",
)


def build_reactive_harmonic_debug_view(
    qt_modules,
    *,
    object_name: str = "reactiveHarmonicDebugView",
):
    """Build the opt-in Reactive harmonic inspection surface."""

    QtCore = qt_modules.QtCore
    QtGui = qt_modules.QtGui
    QtWidgets = qt_modules.QtWidgets

    class ReactiveHarmonicDebugView(QtWidgets.QWidget):
        def __init__(self) -> None:
            super().__init__()
            self._spectrum: tuple[tuple[str, float], ...] = ()
            self._chroma: tuple[float, ...] = (0.0,) * 12
            self._chord = ""
            self._confidence = 0.0
            self._chord_tones: tuple[str, ...] = ()
            self._root_note = ""
            self._non_chord_tones: tuple[str, ...] = ()
            self._predicted_chord = ""
            self._prediction_seconds: float | None = None
            self._prediction_confidence = 0.0
            self._prediction_mismatch: dict[str, object] = {}
            self.setObjectName(object_name)
            self.setProperty(
                "toneRoleColors",
                {
                    "root": "#ef4444",
                    "chord": "#f59e0b",
                    "non_chord": "#2563eb",
                },
            )
            self.setMinimumHeight(80)
            self.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Expanding,
                QtWidgets.QSizePolicy.Policy.Expanding,
            )
            self.setToolTip(
                "Live-only diagnostic. Left: all 12 detected pitch classes "
                "and the assumed triad. Right: narrow tonal FFT peaks folded "
                "across octaves into 12 notes. Frequencies below 80 Hz, above "
                "2 kHz, and broadband noise are rejected."
            )

        def set_debug_data(
            self,
            spectrum,
            chroma,
            *,
            chord: str = "",
            chord_confidence: float = 0.0,
            chord_tones=(),
            root_note: str = "",
            non_chord_tones=(),
            predicted_chord: str = "",
            prediction_seconds: float | None = None,
            prediction_confidence: float = 0.0,
            prediction_mismatch=None,
        ) -> None:
            normalized_spectrum = tuple(
                (
                    str(note),
                    max(0.0, min(1.0, float(value))),
                )
                for note, value in (spectrum or ())
            )
            normalized_chroma = tuple(
                max(0.0, float(value)) for value in (chroma or ())
            )
            if len(normalized_chroma) != 12:
                normalized_chroma = (0.0,) * 12
            next_chord = str(chord or "")
            next_confidence = max(0.0, min(1.0, float(chord_confidence)))
            next_tones = tuple(str(tone) for tone in (chord_tones or ()))
            next_root = str(root_note or "")
            next_non_chord_tones = tuple(
                str(tone) for tone in (non_chord_tones or ())
            )
            next_predicted_chord = str(predicted_chord or "")
            next_prediction_seconds = (
                max(0.0, float(prediction_seconds))
                if prediction_seconds is not None
                else None
            )
            next_prediction_confidence = max(
                0.0,
                min(1.0, float(prediction_confidence)),
            )
            next_prediction_mismatch = dict(prediction_mismatch or {})
            if (
                normalized_spectrum == self._spectrum
                and normalized_chroma == self._chroma
                and next_chord == self._chord
                and next_confidence == self._confidence
                and next_tones == self._chord_tones
                and next_root == self._root_note
                and next_non_chord_tones == self._non_chord_tones
                and next_predicted_chord == self._predicted_chord
                and next_prediction_seconds == self._prediction_seconds
                and next_prediction_confidence
                == self._prediction_confidence
                and next_prediction_mismatch
                == self._prediction_mismatch
            ):
                return
            self._spectrum = normalized_spectrum
            self._chroma = normalized_chroma
            self._chord = next_chord
            self._confidence = next_confidence
            self._chord_tones = next_tones
            self._root_note = next_root
            self._non_chord_tones = next_non_chord_tones
            self._predicted_chord = next_predicted_chord
            self._prediction_seconds = next_prediction_seconds
            self._prediction_confidence = next_prediction_confidence
            self._prediction_mismatch = next_prediction_mismatch
            self.setProperty("rootNote", self._root_note)
            self.setProperty("chordTones", list(self._chord_tones))
            self.setProperty(
                "nonChordTones",
                list(self._non_chord_tones),
            )
            self.setProperty("predictedChord", self._predicted_chord)
            self.setProperty(
                "predictionMismatch",
                bool(self._prediction_mismatch),
            )
            self.update()

        def paintEvent(self, _event) -> None:  # pragma: no cover - Qt only
            painter = QtGui.QPainter(self)
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
            painter.setFont(QtGui.QFont("Segoe UI", 9))
            outer = self.rect()
            painter.fillRect(outer, QtGui.QColor("#10141b"))
            rect = QtCore.QRectF(outer.adjusted(8, 8, -8, -8))
            painter.fillRect(rect, QtGui.QColor("#161d27"))
            painter.setPen(
                QtGui.QPen(
                    QtGui.QColor(
                        "#ef4444"
                        if self._prediction_mismatch
                        else "#334155"
                    ),
                    3 if self._prediction_mismatch else 1,
                )
            )
            painter.drawRoundedRect(rect, 7, 7)

            title = (
                f"Live chord estimate: {self._chord or 'acquiring'}"
                f"  ·  confidence {self._confidence:.0%}"
            )
            painter.setPen(QtGui.QColor("#e9d5ff"))
            if self._prediction_mismatch:
                expected = str(
                    self._prediction_mismatch.get(
                        "predicted_chord",
                        "?",
                    )
                )
                actual = str(
                    self._prediction_mismatch.get(
                        "actual_chord",
                        self._chord or "?",
                    )
                )
                timing_error = float(
                    self._prediction_mismatch.get(
                        "timing_error",
                        0.0,
                    )
                    or 0.0
                )
                title += (
                    f"  |  PREDICTION MISS {expected} -> {actual}"
                    f" ({timing_error:+.2f}s)"
                )
                painter.setPen(QtGui.QColor("#fecaca"))
            elif self._predicted_chord:
                time_text = (
                    f" in {self._prediction_seconds:.1f}s"
                    if self._prediction_seconds is not None
                    else ""
                )
                title += (
                    f"  |  Next: {self._predicted_chord}{time_text}"
                    f" ({self._prediction_confidence:.0%})"
                )
            title_font = painter.font()
            title_font.setBold(True)
            painter.setFont(title_font)
            painter.drawText(
                QtCore.QRectF(
                    rect.left() + 10,
                    rect.top() + 4,
                    rect.width() - 20,
                    22,
                ),
                int(
                    QtCore.Qt.AlignmentFlag.AlignLeft
                    | QtCore.Qt.AlignmentFlag.AlignVCenter
                ),
                title,
            )
            title_font.setBold(False)
            painter.setFont(title_font)

            show_pitch_values = rect.height() >= 215
            footer_height = 32 if show_pitch_values else 0
            content = QtCore.QRectF(
                rect.left() + 8,
                rect.top() + 30,
                rect.width() - 16,
                rect.height() - 30 - footer_height,
            )
            wheel_size = min(
                content.height(),
                max(96.0, content.width() * 0.36),
            )
            wheel = QtCore.QRectF(
                content.left(),
                content.top(),
                wheel_size,
                content.height(),
            )
            spectrum = QtCore.QRectF(
                wheel.right() + 12,
                content.top(),
                max(1.0, content.right() - wheel.right() - 12),
                content.height(),
            )
            self._paint_wheel(painter, wheel)
            self._paint_spectrum(painter, spectrum)
            if show_pitch_values:
                self._paint_pitch_values(painter, rect)

        def _paint_wheel(self, painter, rect) -> None:
            center = rect.center()
            radius = max(24.0, min(rect.width(), rect.height()) * 0.34)
            painter.setPen(QtGui.QPen(QtGui.QColor("#475569"), 1))
            painter.setBrush(QtGui.QColor("#111827"))
            painter.drawEllipse(center, radius + 20.0, radius + 20.0)

            peak = max(1e-9, max(self._chroma, default=0.0))
            assumed = set(self._chord_tones)
            non_chord = set(self._non_chord_tones)
            root = (
                self._root_note
                or (
                    self._chord_tones[0]
                    if self._chord_tones
                    else ""
                )
            )
            for index, pitch in enumerate(_PITCH_NAMES):
                angle = math.radians(-90.0 + (index * 30.0))
                point = QtCore.QPointF(
                    center.x() + (math.cos(angle) * radius),
                    center.y() + (math.sin(angle) * radius),
                )
                strength = min(1.0, self._chroma[index] / peak)
                if pitch == root:
                    fill = QtGui.QColor("#ef4444")
                    border_color = "#fecaca"
                    border_width = 3.5
                elif pitch in assumed:
                    fill = QtGui.QColor("#f59e0b")
                    border_color = "#fde047"
                    border_width = 3.0
                elif pitch in non_chord:
                    fill = QtGui.QColor("#2563eb")
                    border_color = "#7dd3fc"
                    border_width = 2.5
                else:
                    fill = QtGui.QColor("#334155")
                    border_color = "#64748b"
                    border_width = 1.0
                fill.setAlpha(55 + int(200 * strength))
                painter.setPen(
                    QtGui.QPen(QtGui.QColor(border_color), border_width)
                )
                painter.setBrush(fill)
                node_radius = max(
                    9.0,
                    min(15.0, min(rect.width(), rect.height()) * 0.075),
                )
                painter.drawEllipse(point, node_radius, node_radius)
                painter.setPen(QtGui.QColor("#f8fafc"))
                painter.drawText(
                    QtCore.QRectF(
                        point.x() - node_radius,
                        point.y() - node_radius,
                        node_radius * 2,
                        node_radius * 2,
                    ),
                    int(QtCore.Qt.AlignmentFlag.AlignCenter),
                    pitch,
                )

            painter.setPen(QtGui.QColor("#f8fafc"))
            center_font = painter.font()
            center_font.setBold(True)
            center_font.setPointSize(max(11, center_font.pointSize() + 3))
            painter.setFont(center_font)
            painter.drawText(
                QtCore.QRectF(
                    center.x() - 48,
                    center.y() - 18,
                    96,
                    36,
                ),
                int(QtCore.Qt.AlignmentFlag.AlignCenter),
                self._chord or "—",
            )
            center_font.setBold(False)
            center_font.setPointSize(max(8, center_font.pointSize() - 3))
            painter.setFont(center_font)

        def _paint_spectrum(self, painter, rect) -> None:
            painter.setPen(QtGui.QColor("#cbd5e1"))
            painter.drawText(
                QtCore.QRectF(rect.left(), rect.top(), rect.width(), 18),
                int(
                    QtCore.Qt.AlignmentFlag.AlignLeft
                    | QtCore.Qt.AlignmentFlag.AlignVCenter
                ),
                "Octave-folded tonal peaks · 80 Hz–2 kHz",
            )
            painter.setPen(QtGui.QColor("#94a3b8"))
            painter.drawText(
                QtCore.QRectF(
                    rect.left(),
                    rect.top() + 14,
                    rect.width(),
                    14,
                ),
                int(QtCore.Qt.AlignmentFlag.AlignLeft),
                "Root red | chord tones yellow/orange | other tones blue",
            )
            plot = QtCore.QRectF(
                rect.left(),
                rect.top() + 30,
                rect.width(),
                max(1.0, rect.height() - 50),
            )
            painter.setPen(QtGui.QPen(QtGui.QColor("#334155"), 1))
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawRect(plot)
            if not self._spectrum:
                painter.setPen(QtGui.QColor("#7a8796"))
                painter.drawText(
                    plot,
                    int(QtCore.Qt.AlignmentFlag.AlignCenter),
                    "Waiting for narrow tonal peaks…",
                )
                return

            bar_width = max(1.0, plot.width() / len(self._spectrum))
            for index, (note, amplitude) in enumerate(self._spectrum):
                height = amplitude * max(1.0, plot.height() - 2.0)
                bar = QtCore.QRectF(
                    plot.left() + (index * bar_width),
                    plot.bottom() - height,
                    max(1.0, bar_width - 0.5),
                    height,
                )
                color = QtGui.QColor("#7dcfff")
                color.setAlpha(80 + int(175 * amplitude))
                painter.fillRect(bar, color)
                painter.setPen(QtGui.QColor("#94a3b8"))
                painter.drawText(
                    QtCore.QRectF(
                        plot.left() + (index * bar_width),
                        plot.bottom() + 2,
                        bar_width,
                        16,
                    ),
                    int(QtCore.Qt.AlignmentFlag.AlignCenter),
                    note,
                )

        def _paint_pitch_values(self, painter, rect) -> None:
            painter.setPen(QtGui.QColor("#94a3b8"))
            first = " · ".join(
                f"{pitch} {self._chroma[index]:.2f}"
                for index, pitch in enumerate(_PITCH_NAMES[:6])
            )
            second = " · ".join(
                f"{pitch} {self._chroma[index + 6]:.2f}"
                for index, pitch in enumerate(_PITCH_NAMES[6:])
            )
            bottom = rect.bottom() - 30
            painter.drawText(
                QtCore.QRectF(rect.left() + 10, bottom, rect.width() - 20, 14),
                int(QtCore.Qt.AlignmentFlag.AlignLeft),
                first,
            )
            painter.drawText(
                QtCore.QRectF(
                    rect.left() + 10,
                    bottom + 14,
                    rect.width() - 20,
                    14,
                ),
                int(QtCore.Qt.AlignmentFlag.AlignLeft),
                second,
            )

    return ReactiveHarmonicDebugView()
