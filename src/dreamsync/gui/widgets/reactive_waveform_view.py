"""Past/future beat-grid and effect-cue diagnostic for Reactive Live."""

from __future__ import annotations


_CUE_CLASS_LABELS = {
    "bar_marker": "BAR",
    "chord_accent": "CHORD",
    "phrase_reset": "PHRASE",
    "resolution_bloom": "RESOLUTION",
    "section_recall": "SECTION",
    "section_transition": "SECTION",
    "chorus_lift": "SECTION",
}


def _format_effect_cue_label(
    effect: str,
    cue_class: str,
    state: str,
    confidence: float,
) -> str:
    kind = _CUE_CLASS_LABELS.get(
        cue_class,
        cue_class.replace("_", " ").upper(),
    )
    subject = effect or "effect"
    prefix = f"{kind} · " if kind else ""
    return f"{prefix}{subject} · {state} {confidence:.0%}"


def build_reactive_waveform_view(qt_modules):
    """Build a centered timeline with past input and upcoming cue predictions."""

    QtCore = qt_modules.QtCore
    QtGui = qt_modules.QtGui
    QtWidgets = qt_modules.QtWidgets

    class ReactiveWaveformView(QtWidgets.QWidget):
        _BAND_ORDER = ("kick", "bass")

        def __init__(self) -> None:
            super().__init__()
            self._points: tuple[tuple[float, float], ...] = ()
            self._band_points: tuple[
                tuple[str, tuple[tuple[float, float], ...]], ...
            ] = ()
            self._beats: tuple[float, ...] = ()
            self._downbeats: tuple[float, ...] = ()
            self._manual_beats: tuple[tuple[float, str], ...] = ()
            self._predicted_beats: tuple[
                tuple[float, bool, int | None], ...
            ] = ()
            self._upcoming_effects: tuple[
                tuple[float, str, str, str, float], ...
            ] = ()
            self._effect_triggers: tuple[
                tuple[float, str, str, str, str, str], ...
            ] = ()
            self._chord_changes: tuple[tuple[float, str], ...] = ()
            self._window_seconds = 10.0
            self._bpm = 0.0
            self._now_t = 0.0
            self.setObjectName("reactiveWaveformView")
            self.setMinimumHeight(80)
            self.setToolTip(
                "NOW is centered. The left half is captured input history; "
                "the right half projects the corrected beat grid and labels "
                "armed/scheduled effects. Automatic beats are orange and "
                "automatic downbeats are red. Manual S beats are blue and "
                "manual D downbeats are green goalposts. "
                "Solid magenta lines are effects actually triggered. Dashed "
                "cyan lines are predicted effect triggers; dashed amber/red "
                "lines are upcoming beats/downbeats."
            )

        def set_diagnostic_data(
            self,
            points: tuple[tuple[float, float], ...]
            | list[tuple[float, float]],
            beats: tuple[float, ...] | list[float],
            *,
            band_points: dict[
                str,
                tuple[tuple[float, float], ...]
                | list[tuple[float, float]],
            ]
            | None = None,
            chord_changes: tuple[tuple[float, str], ...]
            | list[tuple[float, str]] = (),
            downbeats: tuple[float, ...] | list[float] = (),
            manual_beats: tuple[dict[str, object], ...]
            | list[dict[str, object]] = (),
            predicted_beats: tuple[dict[str, object], ...]
            | list[dict[str, object]] = (),
            upcoming_effects: tuple[dict[str, object], ...]
            | list[dict[str, object]] = (),
            effect_triggers: tuple[dict[str, object], ...]
            | list[dict[str, object]] = (),
            now_t: float | None = None,
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
            normalized_downbeats = tuple(float(timestamp) for timestamp in downbeats)
            normalized_manual_beats = tuple(
                (
                    float(marker.get("t", 0.0) or 0.0),
                    str(marker.get("kind", "beat") or "beat"),
                )
                for marker in manual_beats
            )
            normalized_predicted_beats = tuple(
                (
                    float(marker.get("t", 0.0) or 0.0),
                    bool(marker.get("downbeat", False)),
                    (
                        int(marker["beat_in_bar"])
                        if marker.get("beat_in_bar") is not None
                        else None
                    ),
                )
                for marker in predicted_beats
            )
            normalized_upcoming_effects = tuple(
                (
                    float(cue.get("t", 0.0) or 0.0),
                    str(cue.get("effect", "") or ""),
                    str(cue.get("cue_class", "") or ""),
                    str(cue.get("state", "") or ""),
                    max(0.0, min(1.0, float(cue.get("confidence", 0.0) or 0.0))),
                )
                for cue in upcoming_effects
            )
            normalized_effect_triggers = tuple(
                (
                    float(event.get("t", 0.0) or 0.0),
                    str(event.get("effect", "") or ""),
                    str(event.get("render_mode", "") or ""),
                    str(event.get("native_render_mode", "") or ""),
                    str(event.get("source", "") or ""),
                    str(event.get("override_source", "") or ""),
                )
                for event in effect_triggers
            )
            normalized_chord_changes = tuple(
                (float(timestamp), str(chord))
                for timestamp, chord in chord_changes
                if str(chord).strip()
            )
            next_window = max(1.0, float(window_seconds))
            next_bpm = max(0.0, float(bpm))
            next_now = float(
                now_t
                if now_t is not None
                else normalized_points[-1][0]
                if normalized_points
                else 0.0
            )
            if (
                normalized_points == self._points
                and normalized_bands == self._band_points
                and normalized_beats == self._beats
                and normalized_downbeats == self._downbeats
                and normalized_manual_beats == self._manual_beats
                and normalized_predicted_beats == self._predicted_beats
                and normalized_upcoming_effects == self._upcoming_effects
                and normalized_effect_triggers == self._effect_triggers
                and normalized_chord_changes == self._chord_changes
                and next_window == self._window_seconds
                and next_bpm == self._bpm
                and next_now == self._now_t
            ):
                return
            self._points = normalized_points
            self._band_points = normalized_bands
            self._beats = normalized_beats
            self._downbeats = normalized_downbeats
            self._manual_beats = normalized_manual_beats
            self._predicted_beats = normalized_predicted_beats
            self._upcoming_effects = normalized_upcoming_effects
            self._effect_triggers = normalized_effect_triggers
            self._chord_changes = normalized_chord_changes
            self._window_seconds = next_window
            self._bpm = next_bpm
            self._now_t = next_now
            self.setProperty(
                "predictedBeats",
                [
                    {
                        "t": timestamp,
                        "downbeat": downbeat,
                        "beat_in_bar": beat_in_bar,
                    }
                    for timestamp, downbeat, beat_in_bar
                    in normalized_predicted_beats
                ],
            )
            self.setProperty(
                "upcomingEffects",
                [
                    {
                        "t": timestamp,
                        "effect": effect,
                        "cue_class": cue_class,
                        "state": state,
                        "confidence": confidence,
                    }
                    for timestamp, effect, cue_class, state, confidence
                    in normalized_upcoming_effects
                ],
            )
            self.setProperty(
                "effectTriggers",
                [
                    {
                        "t": timestamp,
                        "effect": effect,
                        "render_mode": render_mode,
                        "native_render_mode": native_render_mode,
                        "source": source,
                        "override_source": override_source,
                    }
                    for (
                        timestamp,
                        effect,
                        render_mode,
                        native_render_mode,
                        source,
                        override_source,
                    )
                    in normalized_effect_triggers
                ],
            )
            self.setProperty("pastEffectLineStyle", "solid")
            self.setProperty("upcomingEffectLineStyle", "dashed")
            self.setProperty("timelineNow", next_now)
            self.update()

        def paintEvent(self, _event) -> None:  # pragma: no cover - Qt only
            painter = QtGui.QPainter(self)
            painter.setRenderHint(
                QtGui.QPainter.RenderHint.Antialiasing,
                False,
            )
            outer = self.rect()
            painter.fillRect(outer, QtGui.QColor("#10141b"))
            rect = QtCore.QRectF(outer.adjusted(10, 8, -10, -8))
            if rect.width() <= 0 or rect.height() <= 0:
                return
            painter.fillRect(rect, QtGui.QColor("#161d27"))
            painter.setPen(QtGui.QPen(QtGui.QColor("#273240"), 1))
            painter.drawRoundedRect(rect, 6, 6)

            title_band = QtCore.QRectF(
                rect.left() + 8,
                rect.top() + 2,
                rect.width() - 16,
                20,
            )
            title = "Past input  ←  |  NOW  |  Upcoming beats + cued effects  →"
            if self._bpm > 0.0:
                title += f"  ·  {self._bpm:.1f} BPM"
            painter.setPen(QtGui.QColor("#cbd5e1"))
            painter.drawText(
                title_band,
                int(
                    QtCore.Qt.AlignmentFlag.AlignLeft
                    | QtCore.Qt.AlignmentFlag.AlignVCenter
                ),
                title,
            )

            plot = QtCore.QRectF(
                rect.left() + 8,
                rect.top() + 26,
                rect.width() - 16,
                rect.height() - 46,
            )
            if plot.width() <= 1 or plot.height() <= 1:
                return
            now_t = self._now_t
            start_time = now_t - self._window_seconds
            end_time = now_t + self._window_seconds
            total_seconds = self._window_seconds * 2.0
            now_x = plot.center().x()
            past_rect = QtCore.QRectF(
                plot.left(),
                plot.top(),
                plot.width() / 2.0,
                plot.height(),
            )
            future_rect = QtCore.QRectF(
                now_x,
                plot.top(),
                plot.width() / 2.0,
                plot.height(),
            )
            painter.fillRect(past_rect, QtGui.QColor("#161d27"))
            painter.fillRect(future_rect, QtGui.QColor("#111827"))

            def x_for(timestamp: float) -> float:
                ratio = (float(timestamp) - start_time) / total_seconds
                return plot.left() + max(0.0, min(1.0, ratio)) * plot.width()

            visible_points = tuple(
                point
                for point in self._points
                if start_time <= point[0] <= now_t
            )
            band_data = dict(self._band_points)
            bands = tuple(
                name for name in self._BAND_ORDER if name in band_data
            )
            waveform_height = max(42.0, plot.height() * 0.36)
            waveform_plot = QtCore.QRectF(
                plot.left(),
                plot.top(),
                plot.width(),
                waveform_height,
            )

            painter.save()
            painter.setClipRect(plot)
            for beat_time in self._beats:
                if start_time <= beat_time <= now_t:
                    x = x_for(beat_time)
                    painter.setPen(
                        QtGui.QPen(QtGui.QColor("#f59e0b"), 1.25)
                    )
                    painter.drawLine(
                        QtCore.QPointF(x, plot.top()),
                        QtCore.QPointF(x, plot.bottom()),
                    )
            for beat_time in self._downbeats:
                if start_time <= beat_time <= now_t:
                    x = x_for(beat_time)
                    painter.setPen(
                        QtGui.QPen(QtGui.QColor("#ef4444"), 2.25)
                    )
                    painter.drawLine(
                        QtCore.QPointF(x, plot.top()),
                        QtCore.QPointF(x, plot.bottom()),
                    )
            for beat_time, kind in self._manual_beats:
                if start_time <= beat_time <= now_t:
                    x = x_for(beat_time)
                    color = "#22c55e" if kind == "downbeat" else "#3b82f6"
                    width = 3.5 if kind == "downbeat" else 2.75
                    painter.setPen(QtGui.QPen(QtGui.QColor(color), width))
                    painter.drawLine(
                        QtCore.QPointF(x, plot.top()),
                        QtCore.QPointF(x, plot.bottom()),
                    )
            for beat_time, downbeat, beat_in_bar in self._predicted_beats:
                if not now_t < beat_time <= end_time:
                    continue
                x = x_for(beat_time)
                color = "#fb7185" if downbeat else "#fbbf24"
                pen = QtGui.QPen(
                    QtGui.QColor(color),
                    2.0 if downbeat else 1.25,
                )
                pen.setStyle(QtCore.Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.drawLine(
                    QtCore.QPointF(x, plot.top()),
                    QtCore.QPointF(x, plot.bottom()),
                )
                if beat_in_bar is not None:
                    painter.setPen(QtGui.QColor(color))
                    painter.drawText(
                        QtCore.QRectF(x + 2, plot.bottom() - 15, 20, 13),
                        int(QtCore.Qt.AlignmentFlag.AlignLeft),
                        str(beat_in_bar),
                    )

            for change_index, (change_time, chord) in enumerate(
                self._chord_changes
            ):
                if not start_time <= change_time <= now_t:
                    continue
                x = x_for(change_time)
                painter.setPen(QtGui.QPen(QtGui.QColor("#c084fc"), 2.25))
                painter.drawLine(
                    QtCore.QPointF(x, plot.top()),
                    QtCore.QPointF(x, plot.bottom()),
                )
                painter.setPen(QtGui.QColor("#e9d5ff"))
                painter.drawText(
                    QtCore.QRectF(
                        x + 3,
                        plot.top() + 2 + (change_index % 2) * 14,
                        48,
                        14,
                    ),
                    int(QtCore.Qt.AlignmentFlag.AlignLeft),
                    chord,
                )

            center_y = waveform_plot.center().y()
            painter.setPen(QtGui.QPen(QtGui.QColor("#334155"), 1))
            painter.drawLine(
                QtCore.QPointF(waveform_plot.left(), center_y),
                QtCore.QPointF(waveform_plot.right(), center_y),
            )
            peak = max(
                0.03,
                max(
                    (amplitude for _timestamp, amplitude in visible_points),
                    default=0.0,
                ),
            )
            half_height = max(4.0, waveform_plot.height() / 2.25)
            painter.setPen(QtGui.QPen(QtGui.QColor("#7dcfff"), 1))
            for timestamp, amplitude in visible_points:
                x = x_for(timestamp)
                height = min(1.0, amplitude / peak) * half_height
                painter.drawLine(
                    QtCore.QPointF(x, center_y - height),
                    QtCore.QPointF(x, center_y + height),
                )

            visible_effect_triggers = tuple(
                event
                for event in self._effect_triggers
                if start_time <= event[0] <= now_t
            )
            labelled_effect_triggers = set(visible_effect_triggers[-6:])
            for trigger_index, (
                trigger_t,
                effect,
                render_mode,
                native_render_mode,
                _source,
                override_source,
            ) in enumerate(visible_effect_triggers):
                x = x_for(trigger_t)
                painter.setPen(
                    QtGui.QPen(QtGui.QColor("#e879f9"), 2.25)
                )
                painter.drawLine(
                    QtCore.QPointF(x, plot.top()),
                    QtCore.QPointF(x, plot.bottom()),
                )
                event = (
                    trigger_t,
                    effect,
                    render_mode,
                    native_render_mode,
                    _source,
                    override_source,
                )
                if event not in labelled_effect_triggers:
                    continue
                label = effect or render_mode or "effect"
                if (
                    native_render_mode
                    and render_mode
                    and native_render_mode != render_mode
                ):
                    label = (
                        f"{label}: {native_render_mode}→{render_mode}"
                        + (
                            f" · {override_source}"
                            if override_source
                            else ""
                        )
                    )
                elif render_mode and render_mode not in label:
                    label = f"{label} [{render_mode}]"
                label_width = min(
                    280.0,
                    max(70.0, 7.0 * len(label) + 12.0),
                )
                label_x = max(
                    plot.left() + 2.0,
                    min(x + 3.0, now_x - label_width - 2.0),
                )
                label_y = (
                    waveform_plot.bottom()
                    - 18.0
                    - (trigger_index % 3) * 18.0
                )
                pill = QtCore.QRectF(
                    label_x,
                    label_y,
                    label_width,
                    16.0,
                )
                painter.fillRect(pill, QtGui.QColor("#701a75"))
                painter.setPen(QtGui.QColor("#fae8ff"))
                painter.drawText(
                    pill.adjusted(4, 0, -2, 0),
                    int(
                        QtCore.Qt.AlignmentFlag.AlignLeft
                        | QtCore.Qt.AlignmentFlag.AlignVCenter
                    ),
                    label,
                )

            for cue_index, (
                cue_t,
                effect,
                cue_class,
                state,
                confidence,
            ) in enumerate(self._upcoming_effects):
                if not now_t <= cue_t <= end_time:
                    continue
                x = x_for(cue_t)
                cue_pen = QtGui.QPen(QtGui.QColor("#22d3ee"), 2.25)
                cue_pen.setStyle(QtCore.Qt.PenStyle.DashLine)
                painter.setPen(cue_pen)
                painter.drawLine(
                    QtCore.QPointF(x, plot.top()),
                    QtCore.QPointF(x, plot.bottom()),
                )
                label = _format_effect_cue_label(
                    effect,
                    cue_class,
                    state,
                    confidence,
                )
                label_width = min(
                    240.0,
                    max(84.0, 7.0 * len(label) + 12.0),
                )
                label_x = min(
                    x + 4.0,
                    plot.right() - label_width - 2.0,
                )
                label_y = plot.top() + 3.0 + (cue_index % 3) * 19.0
                pill = QtCore.QRectF(
                    label_x,
                    label_y,
                    label_width,
                    17.0,
                )
                painter.fillRect(pill, QtGui.QColor("#164e63"))
                painter.setPen(QtGui.QColor("#cffafe"))
                painter.drawText(
                    pill.adjusted(5, 0, -3, 0),
                    int(
                        QtCore.Qt.AlignmentFlag.AlignLeft
                        | QtCore.Qt.AlignmentFlag.AlignVCenter
                    ),
                    label,
                )

            if bands:
                lane_top = waveform_plot.bottom() + 5.0
                lane_height = max(
                    12.0,
                    (plot.bottom() - lane_top) / len(bands),
                )
                for index, band_name in enumerate(bands):
                    lane = QtCore.QRectF(
                        plot.left() + 42.0,
                        lane_top + index * lane_height,
                        max(1.0, plot.width() - 42.0),
                        max(8.0, lane_height - 2.0),
                    )
                    painter.setPen(QtGui.QColor("#94a3b8"))
                    painter.drawText(
                        QtCore.QRectF(
                            plot.left(),
                            lane.top(),
                            38.0,
                            lane.height(),
                        ),
                        int(
                            QtCore.Qt.AlignmentFlag.AlignRight
                            | QtCore.Qt.AlignmentFlag.AlignVCenter
                        ),
                        band_name,
                    )
                    painter.setPen(
                        QtGui.QPen(QtGui.QColor("#334155"), 1)
                    )
                    painter.drawLine(
                        QtCore.QPointF(lane.left(), lane.bottom()),
                        QtCore.QPointF(lane.right(), lane.bottom()),
                    )
                    series = tuple(
                        point
                        for point in band_data.get(band_name, ())
                        if start_time <= point[0] <= now_t
                    )
                    band_peak = max(
                        1e-6,
                        max(
                            (value for _timestamp, value in series),
                            default=0.0,
                        ),
                    )
                    color = (
                        "#f6c177" if band_name == "kick" else "#a6e3a1"
                    )
                    painter.setPen(QtGui.QPen(QtGui.QColor(color), 1))
                    for timestamp, value in series:
                        x = x_for(timestamp)
                        height = (
                            min(1.0, value / band_peak)
                            * max(1.0, lane.height() - 2.0)
                        )
                        painter.drawLine(
                            QtCore.QPointF(x, lane.bottom()),
                            QtCore.QPointF(x, lane.bottom() - height),
                        )
            painter.restore()

            now_pen = QtGui.QPen(QtGui.QColor("#e2e8f0"), 2.0)
            painter.setPen(now_pen)
            painter.drawLine(
                QtCore.QPointF(now_x, plot.top()),
                QtCore.QPointF(now_x, plot.bottom()),
            )
            painter.setPen(QtGui.QColor("#94a3b8"))
            painter.drawText(
                QtCore.QRectF(plot.left(), rect.bottom() - 18, 80, 16),
                int(QtCore.Qt.AlignmentFlag.AlignLeft),
                f"−{self._window_seconds:g} s",
            )
            painter.drawText(
                QtCore.QRectF(now_x - 30, rect.bottom() - 18, 60, 16),
                int(QtCore.Qt.AlignmentFlag.AlignCenter),
                "NOW",
            )
            painter.drawText(
                QtCore.QRectF(plot.right() - 80, rect.bottom() - 18, 80, 16),
                int(QtCore.Qt.AlignmentFlag.AlignRight),
                f"+{self._window_seconds:g} s",
            )

    return ReactiveWaveformView()
