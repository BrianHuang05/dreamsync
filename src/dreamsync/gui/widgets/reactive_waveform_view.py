"""Rolling audio and EQ diagnostic for the live Reactive detector."""

from __future__ import annotations


def build_reactive_waveform_view(qt_modules):
    """Build a ten-second envelope, EQ-flux, beat, and chord diagnostic."""
    QtCore = qt_modules.QtCore
    QtGui = qt_modules.QtGui
    QtWidgets = qt_modules.QtWidgets

    class ReactiveWaveformView(QtWidgets.QWidget):
        _BAND_ORDER = ("kick", "bass")

        def __init__(self) -> None:
            super().__init__()
            self._points: tuple[tuple[float, float], ...] = ()
            self._band_points: tuple[tuple[str, tuple[tuple[float, float], ...]], ...] = ()
            self._beats: tuple[float, ...] = ()
            self._downbeats: tuple[float, ...] = ()
            self._manual_beats: tuple[tuple[float, str], ...] = ()
            self._chord_changes: tuple[tuple[float, str], ...] = ()
            self._window_seconds = 10.0
            self._bpm = 0.0
            self.setObjectName("reactiveWaveformView")
            self.setMinimumHeight(80)
            self.setToolTip(
                "Top: rolling input envelope. Lower lanes: positive kick and bass flux, the two "
                "beat-priority EQ signals. The detector still evaluates the full EQ breakdown internally. "
                "Automatic beats are orange and automatic downbeats are red. "
                "Manual S beats are blue and manual D downbeats are green; "
                "manual registrations snap to detector lines. Purple bars are "
                "confirmed past chord changes."
            )

        def set_diagnostic_data(
            self,
            points: tuple[tuple[float, float], ...] | list[tuple[float, float]],
            beats: tuple[float, ...] | list[float],
            *,
            band_points: dict[str, tuple[tuple[float, float], ...] | list[tuple[float, float]]] | None = None,
            chord_changes: tuple[tuple[float, str], ...] | list[tuple[float, str]] = (),
            downbeats: tuple[float, ...] | list[float] = (),
            manual_beats: tuple[dict[str, object], ...] | list[dict[str, object]] = (),
            window_seconds: float = 10.0,
            bpm: float = 0.0,
        ) -> None:
            normalized_points = tuple(
                (float(timestamp), max(0.0, float(amplitude)))
                for timestamp, amplitude in points
            )
            normalized_bands = tuple(
                (
                    str(name),
                    tuple(
                        (float(timestamp), max(0.0, float(value)))
                        for timestamp, value in series
                    ),
                )
                for name, series in sorted((band_points or {}).items())
            )
            normalized_beats = tuple(float(timestamp) for timestamp in beats)
            normalized_downbeats = tuple(
                float(timestamp) for timestamp in downbeats
            )
            normalized_manual_beats = tuple(
                (
                    float(marker.get("t", 0.0) or 0.0),
                    str(marker.get("kind", "beat") or "beat"),
                )
                for marker in manual_beats
            )
            normalized_chord_changes = tuple(
                (float(timestamp), str(chord))
                for timestamp, chord in chord_changes
                if str(chord).strip()
            )
            next_window = max(1.0, float(window_seconds))
            next_bpm = max(0.0, float(bpm))
            if (
                normalized_points == self._points
                and normalized_bands == self._band_points
                and normalized_beats == self._beats
                and normalized_downbeats == self._downbeats
                and normalized_manual_beats == self._manual_beats
                and normalized_chord_changes == self._chord_changes
                and next_window == self._window_seconds
                and next_bpm == self._bpm
            ):
                return
            self._points = normalized_points
            self._band_points = normalized_bands
            self._beats = normalized_beats
            self._downbeats = normalized_downbeats
            self._manual_beats = normalized_manual_beats
            self._chord_changes = normalized_chord_changes
            self._window_seconds = next_window
            self._bpm = next_bpm
            self.update()

        def paintEvent(self, _event) -> None:  # pragma: no cover - Qt only
            painter = QtGui.QPainter(self)
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, False)
            outer = self.rect()
            painter.fillRect(outer, QtGui.QColor("#10141b"))
            rect = QtCore.QRectF(outer.adjusted(10, 8, -10, -8))
            if rect.width() <= 0 or rect.height() <= 0:
                return
            painter.fillRect(rect, QtGui.QColor("#161d27"))
            painter.setPen(QtGui.QPen(QtGui.QColor("#273240"), 1))
            painter.drawRoundedRect(rect, 6, 6)

            title_band = QtCore.QRectF(rect.left() + 8, rect.top() + 2, rect.width() - 16, 20)
            title = "Reactive input + beat/chord changes — last 10 seconds"
            if self._bpm > 0.0:
                title += f" · {self._bpm:.1f} BPM"
            painter.setPen(QtGui.QColor("#cbd5e1"))
            painter.drawText(
                title_band,
                int(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter),
                title,
            )

            plot = QtCore.QRectF(rect.left() + 8, rect.top() + 26, rect.width() - 16, rect.height() - 46)
            if plot.width() <= 1 or plot.height() <= 1:
                return
            if not self._points:
                painter.setPen(QtGui.QColor("#7a8796"))
                painter.drawText(
                    plot,
                    int(QtCore.Qt.AlignmentFlag.AlignCenter),
                    "Waiting for reactive input…",
                )
                return

            end_time = self._points[-1][0]
            start_time = end_time - self._window_seconds
            visible_points = tuple(point for point in self._points if point[0] >= start_time)
            band_data = dict(self._band_points)
            bands = tuple(name for name in self._BAND_ORDER if name in band_data)
            waveform_height = max(42.0, plot.height() * 0.36)
            waveform_plot = QtCore.QRectF(plot.left(), plot.top(), plot.width(), waveform_height)

            painter.save()
            painter.setClipRect(plot)
            for beat_time in self._beats:
                if not start_time <= beat_time <= end_time:
                    continue
                ratio = (beat_time - start_time) / self._window_seconds
                x = plot.left() + ratio * plot.width()
                painter.setPen(QtGui.QPen(QtGui.QColor("#f59e0b"), 1.25))
                painter.drawLine(QtCore.QPointF(x, plot.top()), QtCore.QPointF(x, plot.bottom()))
            for beat_time in self._downbeats:
                if not start_time <= beat_time <= end_time:
                    continue
                ratio = (beat_time - start_time) / self._window_seconds
                x = plot.left() + ratio * plot.width()
                painter.setPen(QtGui.QPen(QtGui.QColor("#ef4444"), 2.25))
                painter.drawLine(
                    QtCore.QPointF(x, plot.top()),
                    QtCore.QPointF(x, plot.bottom()),
                )
            for beat_time, kind in self._manual_beats:
                if not start_time <= beat_time <= end_time:
                    continue
                ratio = (beat_time - start_time) / self._window_seconds
                x = plot.left() + ratio * plot.width()
                width = 3.5 if kind == "downbeat" else 2.75
                color = "#22c55e" if kind == "downbeat" else "#3b82f6"
                painter.setPen(QtGui.QPen(QtGui.QColor(color), width))
                painter.drawLine(
                    QtCore.QPointF(x, plot.top()),
                    QtCore.QPointF(x, plot.bottom()),
                )
            for change_index, (change_time, chord) in enumerate(
                self._chord_changes
            ):
                if not start_time <= change_time <= end_time:
                    continue
                ratio = (change_time - start_time) / self._window_seconds
                x = plot.left() + ratio * plot.width()
                painter.setPen(QtGui.QPen(QtGui.QColor("#c084fc"), 2.25))
                painter.drawLine(
                    QtCore.QPointF(x, plot.top()),
                    QtCore.QPointF(x, plot.bottom()),
                )
                label_y = plot.top() + 2.0 + ((change_index % 2) * 14.0)
                painter.setPen(QtGui.QColor("#e9d5ff"))
                painter.drawText(
                    QtCore.QRectF(x + 3.0, label_y, 48.0, 14.0),
                    int(
                        QtCore.Qt.AlignmentFlag.AlignLeft
                        | QtCore.Qt.AlignmentFlag.AlignVCenter
                    ),
                    chord,
                )

            center_y = waveform_plot.center().y()
            painter.setPen(QtGui.QPen(QtGui.QColor("#334155"), 1))
            painter.drawLine(
                QtCore.QPointF(waveform_plot.left(), center_y),
                QtCore.QPointF(waveform_plot.right(), center_y),
            )
            peak = max(0.03, max((amplitude for _timestamp, amplitude in visible_points), default=0.0))
            half_height = max(4.0, waveform_plot.height() / 2.25)
            painter.setPen(QtGui.QPen(QtGui.QColor("#7dcfff"), 1))
            for timestamp, amplitude in visible_points:
                ratio = (timestamp - start_time) / self._window_seconds
                x = waveform_plot.left() + max(0.0, min(1.0, ratio)) * waveform_plot.width()
                height = min(1.0, amplitude / peak) * half_height
                painter.drawLine(
                    QtCore.QPointF(x, center_y - height),
                    QtCore.QPointF(x, center_y + height),
                )

            if bands:
                lane_top = waveform_plot.bottom() + 5.0
                lane_height = max(12.0, (plot.bottom() - lane_top) / len(bands))
                for index, band_name in enumerate(bands):
                    lane = QtCore.QRectF(
                        plot.left() + 42.0,
                        lane_top + index * lane_height,
                        max(1.0, plot.width() - 42.0),
                        max(8.0, lane_height - 2.0),
                    )
                    painter.setPen(QtGui.QColor("#94a3b8"))
                    painter.drawText(
                        QtCore.QRectF(plot.left(), lane.top(), 38.0, lane.height()),
                        int(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter),
                        band_name.replace("_", " "),
                    )
                    painter.setPen(QtGui.QPen(QtGui.QColor("#334155"), 1))
                    painter.drawLine(
                        QtCore.QPointF(lane.left(), lane.bottom()),
                        QtCore.QPointF(lane.right(), lane.bottom()),
                    )
                    series = tuple(point for point in band_data.get(band_name, ()) if point[0] >= start_time)
                    band_peak = max(1e-6, max((value for _timestamp, value in series), default=0.0))
                    color = "#f6c177" if band_name == "kick" else "#a6e3a1" if band_name == "bass" else "#64748b"
                    painter.setPen(QtGui.QPen(QtGui.QColor(color), 1))
                    for timestamp, value in series:
                        ratio = (timestamp - start_time) / self._window_seconds
                        x = lane.left() + max(0.0, min(1.0, ratio)) * lane.width()
                        height = min(1.0, value / band_peak) * max(1.0, lane.height() - 2.0)
                        painter.drawLine(
                            QtCore.QPointF(x, lane.bottom()),
                            QtCore.QPointF(x, lane.bottom() - height),
                        )
            painter.restore()

            painter.setPen(QtGui.QColor("#94a3b8"))
            painter.drawText(
                QtCore.QRectF(plot.left(), rect.bottom() - 18, 90, 16),
                int(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter),
                "−10 s",
            )
            painter.drawText(
                QtCore.QRectF(plot.right() - 350, rect.bottom() - 18, 350, 16),
                int(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter),
                "orange auto beat · red auto downbeat · blue S · green D",
            )

    return ReactiveWaveformView()
