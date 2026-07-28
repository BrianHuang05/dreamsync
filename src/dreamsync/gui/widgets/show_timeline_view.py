"""Compact waveform + timing viewer for saved-show editing."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TimelineSectionMarker:
    start_t: float
    end_t: float
    label: str


def build_show_timeline_view(qt_modules):
    QtCore = qt_modules.QtCore
    QtGui = qt_modules.QtGui
    QtWidgets = qt_modules.QtWidgets

    class ShowTimelineView(QtWidgets.QWidget):
        cueSelected = QtCore.Signal(int)
        cueMoved = QtCore.Signal(int, float)
        beatMoved = QtCore.Signal(int, float)
        downbeatMoved = QtCore.Signal(int, float)
        cueDeleteRequested = QtCore.Signal(int)
        beatDeleteRequested = QtCore.Signal(int)
        downbeatDeleteRequested = QtCore.Signal(int)
        addCueRequested = QtCore.Signal(float)
        addBeatRequested = QtCore.Signal(float)
        playPauseRequested = QtCore.Signal()
        sectionBoundaryMoved = QtCore.Signal(int, str, float)
        zoomChanged = QtCore.Signal(float)
        viewWindowChanged = QtCore.Signal(float, float, float)

        def __init__(self) -> None:
            super().__init__()
            self._duration = 1.0
            self._waveform: tuple[float, ...] = ()
            self._beats: tuple[float, ...] = ()
            self._downbeats: tuple[float, ...] = ()
            self._sections: tuple[TimelineSectionMarker, ...] = ()
            self._cue_times: tuple[float, ...] = ()
            self._selected_cue_index: int | None = None
            self._active_cue_index: int | None = None
            self._playhead_seconds: float | None = None
            self._zoom_factor: float = 1.0
            self._view_start_t: float = 0.0
            self._drag_kind: str | None = None
            self._drag_index: int | None = None
            self._drag_edge: str | None = None
            self._drag_anchor_x: float = 0.0
            self._drag_anchor_start_t: float = 0.0
            self._selected_marker: tuple[str, int, str | None] | None = None
            self.setMinimumHeight(170)
            self.setObjectName("showTimelineView")
            self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
            self.setToolTip(
                "Waveform controls: Space plays or pauses; Tab / Shift+Tab select the next / previous item of the same type; "
                "Ctrl+Tab or Shift+Up/Down switches between cues and beat markers; "
                "Left / Right make a small nudge and Shift+Left / Right make a large nudge; "
                "Delete removes the selected cue or beat line; "
                "right-click to add a cue or beat line."
            )

        def focusNextPrevChild(self, _next: bool) -> bool:  # pragma: no cover - Qt only
            # Tab is an editor command here, not focus traversal.
            return True

        def event(self, event) -> bool:  # pragma: no cover - Qt only
            if event.type() == QtCore.QEvent.Type.ShortcutOverride:
                if event.key() in {
                    QtCore.Qt.Key.Key_Space,
                    QtCore.Qt.Key.Key_Tab,
                    QtCore.Qt.Key.Key_Backtab,
                    QtCore.Qt.Key.Key_Left,
                    QtCore.Qt.Key.Key_Right,
                    QtCore.Qt.Key.Key_Up,
                    QtCore.Qt.Key.Key_Down,
                    QtCore.Qt.Key.Key_Delete,
                }:
                    event.accept()
                    return True
            if event.type() == QtCore.QEvent.Type.KeyPress and self._handle_editor_key(event):
                return True
            return super().event(event)

        def set_timeline_data(
            self,
            *,
            duration: float,
            waveform: tuple[float, ...],
            beats: tuple[float, ...],
            downbeats: tuple[float, ...],
            sections: tuple[TimelineSectionMarker, ...],
            cue_times: tuple[float, ...],
            selected_cue_index: int | None = None,
            active_cue_index: int | None = None,
            playhead_seconds: float | None = None,
        ) -> None:
            previous_cue_index = self._selected_cue_index
            self._duration = max(0.001, float(duration))
            self._waveform = tuple(float(value) for value in waveform)
            self._beats = tuple(float(value) for value in beats)
            self._downbeats = tuple(float(value) for value in downbeats)
            self._sections = tuple(sections)
            self._cue_times = tuple(float(value) for value in cue_times)
            if self._selected_marker is None:
                self._selected_cue_index = selected_cue_index
            else:
                # Routine playback refreshes mirror the selected table row, but
                # must not steal selection back from an editable orange marker.
                self._selected_cue_index = None
            if selected_cue_index != previous_cue_index and self._selected_marker is None:
                self._selected_marker = None
            self._active_cue_index = active_cue_index
            self._playhead_seconds = playhead_seconds
            if self._selected_marker is not None:
                kind, index, edge = self._selected_marker
                marker_count = {
                    "beat": len(self._beats),
                    "downbeat": len(self._downbeats),
                    "section": len(self._sections),
                }.get(kind, 0)
                if not 0 <= index < marker_count:
                    self._selected_marker = None
            self._clamp_view_window()
            self._emit_view_window_changed()
            self.update()

        def set_zoom_factor(self, factor: float) -> None:
            next_factor = max(1.0, min(float(factor), 30.0))
            if abs(next_factor - self._zoom_factor) < 1e-6:
                return
            anchor = self._playhead_seconds
            if anchor is None and self._selected_cue_index is not None and 0 <= self._selected_cue_index < len(self._cue_times):
                anchor = self._cue_times[self._selected_cue_index]
            if anchor is None:
                anchor = self._view_start_t + (self._visible_duration() / 2.0)
            self._zoom_factor = next_factor
            visible = self._visible_duration()
            self._view_start_t = max(0.0, anchor - (visible / 2.0))
            self._clamp_view_window()
            self.zoomChanged.emit(self._zoom_factor)
            self._emit_view_window_changed()
            self.update()

        def fit_to_duration(self) -> None:
            self._zoom_factor = 1.0
            self._view_start_t = 0.0
            self.zoomChanged.emit(self._zoom_factor)
            self._emit_view_window_changed()
            self.update()

        def zoom_factor(self) -> float:
            return self._zoom_factor

        def visible_duration_seconds(self) -> float:
            return float(self._visible_duration())

        def view_start_seconds(self) -> float:
            return float(self._view_start_t)

        def max_view_start_seconds(self) -> float:
            return max(0.0, self._duration - self._visible_duration())

        def set_view_start_seconds(self, start_seconds: float) -> None:
            next_start = max(0.0, min(float(start_seconds), self.max_view_start_seconds()))
            if abs(next_start - self._view_start_t) < 1e-6:
                return
            self._view_start_t = next_start
            self._emit_view_window_changed()
            self.update()

        def _visible_duration(self) -> float:
            return max(0.001, self._duration / self._zoom_factor)

        def _clamp_view_window(self) -> None:
            visible = self._visible_duration()
            max_start = max(0.0, self._duration - visible)
            self._view_start_t = max(0.0, min(self._view_start_t, max_start))

        def _emit_view_window_changed(self) -> None:
            self.viewWindowChanged.emit(
                float(self._view_start_t),
                float(self._visible_duration()),
                float(self.max_view_start_seconds()),
            )

        def _x_for_time(self, rect, t: float) -> float:
            visible = self._visible_duration()
            clamped = max(self._view_start_t, min(float(t), self._view_start_t + visible))
            return rect.left() + ((clamped - self._view_start_t) / visible) * rect.width()

        def _time_for_x(self, rect, x: float) -> float:
            visible = self._visible_duration()
            fraction = 0.0 if rect.width() <= 0 else max(0.0, min((x - rect.left()) / rect.width(), 1.0))
            return self._view_start_t + (fraction * visible)

        def _cue_hit_index(self, rect, x: float, y: float) -> int | None:
            cue_band_y = rect.bottom() - 18
            if y < cue_band_y - 6:
                return None
            nearest_index = None
            nearest_distance = 14.0
            for index, cue_t in enumerate(self._cue_times):
                cue_x = self._x_for_time(rect, cue_t)
                distance = abs(x - cue_x)
                if distance <= nearest_distance:
                    nearest_index = index
                    nearest_distance = distance
            return nearest_index

        def _marker_hit(self, rect, x: float) -> tuple[str, int, str | None] | None:
            threshold = 8.0
            for index, t in enumerate(self._downbeats):
                marker_x = self._x_for_time(rect, t)
                if abs(x - marker_x) <= threshold:
                    return ("downbeat", index, None)
            for index, t in enumerate(self._beats):
                marker_x = self._x_for_time(rect, t)
                if abs(x - marker_x) <= threshold:
                    return ("beat", index, None)
            for index, section in enumerate(self._sections):
                start_x = self._x_for_time(rect, section.start_t)
                end_x = self._x_for_time(rect, section.end_t)
                if abs(x - start_x) <= threshold:
                    return ("section", index, "start")
                if abs(x - end_x) <= threshold:
                    return ("section", index, "end")
            return None

        def _ordered_beat_markers(self) -> tuple[tuple[str, int, str | None, float], ...]:
            # Gray beat lines are the fixed detected grid. The orange downbeat
            # lines are the editable "beat markers" exposed by keyboard editing.
            markers = [
                ("downbeat", index, None, marker_time)
                for index, marker_time in enumerate(self._downbeats)
            ]
            return tuple(sorted(markers, key=lambda marker: marker[3]))

        def _ensure_time_visible(self, marker_time: float) -> None:
            visible = self._visible_duration()
            if marker_time < self._view_start_t or marker_time > self._view_start_t + visible:
                self.set_view_start_seconds(marker_time - (visible / 2.0))

        def _select_relative_marker(self, direction: int) -> None:
            markers = self._ordered_beat_markers()
            if not markers:
                return
            current = self._selected_marker
            current_index = next(
                (
                    index
                    for index, marker in enumerate(markers)
                    if marker[:3] == current
                ),
                -1 if direction > 0 else len(markers),
            )
            next_index = max(0, min(len(markers) - 1, current_index + direction))
            kind, index, edge, marker_time = markers[next_index]
            self._selected_marker = (kind, index, edge)
            self._selected_cue_index = None
            self._ensure_time_visible(marker_time)
            self.update()

        def _select_relative_cue(self, direction: int) -> None:
            if not self._cue_times:
                return
            current_index = self._selected_cue_index
            if current_index is None or not 0 <= current_index < len(self._cue_times):
                current_index = -1 if direction > 0 else len(self._cue_times)
            next_index = max(0, min(len(self._cue_times) - 1, current_index + direction))
            self._selected_marker = None
            self._selected_cue_index = next_index
            self._ensure_time_visible(self._cue_times[next_index])
            self.cueSelected.emit(next_index)
            # Selecting the matching table row is a synchronous signal handler
            # and may transfer focus into its beat/time editors. Timeline Tab
            # navigation must retain focus so the next Tab selects another cue.
            self.setFocus(QtCore.Qt.FocusReason.ShortcutFocusReason)
            self.update()

        def _selected_marker_time(self) -> float | None:
            if self._selected_marker is None:
                return None
            kind, index, edge = self._selected_marker
            if kind == "beat" and 0 <= index < len(self._beats):
                return self._beats[index]
            if kind == "downbeat" and 0 <= index < len(self._downbeats):
                return self._downbeats[index]
            if kind == "section" and 0 <= index < len(self._sections) and edge is not None:
                return float(getattr(self._sections[index], f"{edge}_t"))
            return None

        def _switch_selection_kind(self) -> None:
            marker_time = self._selected_marker_time()
            if marker_time is not None and self._cue_times:
                cue_index = min(
                    range(len(self._cue_times)),
                    key=lambda index: abs(self._cue_times[index] - marker_time),
                )
                self._selected_marker = None
                self._selected_cue_index = cue_index
                self._ensure_time_visible(self._cue_times[cue_index])
                self.cueSelected.emit(cue_index)
                self.setFocus(QtCore.Qt.FocusReason.ShortcutFocusReason)
                self.update()
                return
            if self._selected_cue_index is not None:
                if not self._cue_times:
                    return
                cue_time = self._cue_times[self._selected_cue_index]
                markers = self._ordered_beat_markers()
                if not markers:
                    return
                kind, index, edge, next_time = min(
                    markers,
                    key=lambda marker: abs(marker[3] - cue_time),
                )
                self._selected_marker = (kind, index, edge)
                self._selected_cue_index = None
                self._ensure_time_visible(next_time)
                self.update()
                return
            if self._cue_times:
                self._select_relative_cue(1)
            else:
                self._select_relative_marker(1)

        def switch_selection_kind(self) -> None:
            """Switch the focused editor selection between a cue and beat marker."""
            self._switch_selection_kind()

        def _move_selected_marker(self, direction: int, *, large: bool = False) -> None:
            step_multiplier = 10.0 if large else 1.0
            if self._selected_cue_index is not None and 0 <= self._selected_cue_index < len(self._cue_times):
                step = max(0.01, self._visible_duration() / max(self.width(), 1.0))
                next_time = max(
                    0.0,
                    min(
                        self._duration,
                        float(self._cue_times[self._selected_cue_index])
                        + (step * step_multiplier * direction),
                    ),
                )
                self.cueMoved.emit(self._selected_cue_index, next_time)
                return
            if self._selected_marker is None:
                self._select_relative_marker(direction)
                return
            kind, index, edge = self._selected_marker
            marker_time = None
            if kind == "beat" and 0 <= index < len(self._beats):
                marker_time = self._beats[index]
            elif kind == "downbeat" and 0 <= index < len(self._downbeats):
                marker_time = self._downbeats[index]
            elif kind == "section" and 0 <= index < len(self._sections) and edge is not None:
                marker_time = getattr(self._sections[index], f"{edge}_t")
            if marker_time is None:
                return
            step = max(0.01, self._visible_duration() / max(self.width(), 1.0))
            next_time = float(marker_time) + (step * step_multiplier * direction)
            if kind == "beat":
                self.beatMoved.emit(index, next_time)
            elif kind == "downbeat":
                self.downbeatMoved.emit(index, next_time)
            elif kind == "section" and edge is not None:
                self.sectionBoundaryMoved.emit(index, edge, next_time)

        def delete_selected_item(self) -> bool:
            """Request deletion of the current cue or timing marker, if any."""
            if self._selected_marker is not None:
                kind, index, _edge = self._selected_marker
                if kind == "beat":
                    self.beatDeleteRequested.emit(index)
                    return True
                if kind == "downbeat":
                    self.downbeatDeleteRequested.emit(index)
                    return True
            if self._selected_cue_index is not None:
                self.cueDeleteRequested.emit(self._selected_cue_index)
                return True
            return False

        def clear_marker_selection(self) -> None:
            self._selected_marker = None
            self.update()

        def _handle_editor_key(self, event) -> bool:
            key = event.key()
            modifiers = event.modifiers()
            if key == QtCore.Qt.Key.Key_Space:
                self.playPauseRequested.emit()
                event.accept()
                return True
            if key in (QtCore.Qt.Key.Key_Tab, QtCore.Qt.Key.Key_Backtab):
                if bool(
                    modifiers
                    & (
                        QtCore.Qt.KeyboardModifier.AltModifier
                        | QtCore.Qt.KeyboardModifier.ControlModifier
                    )
                ):
                    self._switch_selection_kind()
                    event.accept()
                    return True
                backwards = key == QtCore.Qt.Key.Key_Backtab or bool(
                    modifiers & QtCore.Qt.KeyboardModifier.ShiftModifier
                )
                direction = -1 if backwards else 1
                if self._selected_marker is not None:
                    self._select_relative_marker(direction)
                elif self._selected_cue_index is not None or self._cue_times:
                    self._select_relative_cue(direction)
                else:
                    self._select_relative_marker(direction)
                event.accept()
                return True
            if (
                key in (QtCore.Qt.Key.Key_Up, QtCore.Qt.Key.Key_Down)
                and bool(modifiers & QtCore.Qt.KeyboardModifier.ShiftModifier)
            ):
                self.switch_selection_kind()
                event.accept()
                return True
            if key == QtCore.Qt.Key.Key_Left:
                self._move_selected_marker(
                    -1,
                    large=bool(modifiers & QtCore.Qt.KeyboardModifier.ShiftModifier),
                )
                event.accept()
                return True
            if key == QtCore.Qt.Key.Key_Right:
                self._move_selected_marker(
                    1,
                    large=bool(modifiers & QtCore.Qt.KeyboardModifier.ShiftModifier),
                )
                event.accept()
                return True
            if key == QtCore.Qt.Key.Key_Delete and self.delete_selected_item():
                event.accept()
                return True
            return False

        def keyPressEvent(self, event) -> None:  # pragma: no cover - Qt only
            if self._handle_editor_key(event):
                return
            super().keyPressEvent(event)

        def mousePressEvent(self, event) -> None:  # pragma: no cover - Qt only
            self.setFocus()
            if event.button() != QtCore.Qt.MouseButton.LeftButton:
                return super().mousePressEvent(event)
            rect = self.rect().adjusted(12, 12, -12, -12)
            if rect.width() <= 0:
                return super().mousePressEvent(event)
            cue_index = self._cue_hit_index(rect, event.position().x(), event.position().y())
            if cue_index is not None:
                self._selected_marker = None
                self._selected_cue_index = cue_index
                self.update()
                self.cueSelected.emit(cue_index)
                self.setFocus(QtCore.Qt.FocusReason.MouseFocusReason)
                event.accept()
                return
            marker = self._marker_hit(rect, event.position().x())
            if marker is not None:
                self._drag_kind, self._drag_index, self._drag_edge = marker
                self._selected_marker = marker
                self._selected_cue_index = None
                self.update()
                event.accept()
                return
            if self._zoom_factor > 1.0:
                self._drag_kind = "pan"
                self._drag_anchor_x = float(event.position().x())
                self._drag_anchor_start_t = self._view_start_t
                event.accept()
                return
            super().mousePressEvent(event)

        def contextMenuEvent(self, event) -> None:  # pragma: no cover - Qt only
            rect = self.rect().adjusted(12, 12, -12, -12)
            if rect.width() <= 0 or not rect.contains(event.pos()):
                return super().contextMenuEvent(event)
            self.setFocus()
            at_time = self._time_for_x(rect, float(event.pos().x()))
            menu = QtWidgets.QMenu(self)
            add_cue = menu.addAction("Add Cue Here")
            add_beat = menu.addAction("Add Beat Line Here")
            selected = menu.exec(event.globalPos())
            if selected is add_cue:
                self.addCueRequested.emit(at_time)
            elif selected is add_beat:
                self.addBeatRequested.emit(at_time)
            event.accept()

        def mouseMoveEvent(self, event) -> None:  # pragma: no cover - Qt only
            if self._drag_kind is None:
                return super().mouseMoveEvent(event)
            rect = self.rect().adjusted(12, 12, -12, -12)
            if rect.width() <= 0:
                return
            next_time = self._time_for_x(rect, event.position().x())
            if self._drag_kind == "beat" and self._drag_index is not None:
                self.beatMoved.emit(self._drag_index, next_time)
            elif self._drag_kind == "downbeat" and self._drag_index is not None:
                self.downbeatMoved.emit(self._drag_index, next_time)
            elif self._drag_kind == "section" and self._drag_index is not None and self._drag_edge is not None:
                self.sectionBoundaryMoved.emit(self._drag_index, self._drag_edge, next_time)
            elif self._drag_kind == "pan":
                visible = self._visible_duration()
                delta_fraction = (event.position().x() - self._drag_anchor_x) / max(rect.width(), 1.0)
                self._view_start_t = self._drag_anchor_start_t - (delta_fraction * visible)
                self._clamp_view_window()
                self._emit_view_window_changed()
                self.update()

        def mouseReleaseEvent(self, event) -> None:  # pragma: no cover - Qt only
            self._drag_kind = None
            self._drag_index = None
            self._drag_edge = None
            super().mouseReleaseEvent(event)

        def wheelEvent(self, event) -> None:  # pragma: no cover - Qt only
            delta = event.angleDelta().y()
            if bool(event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier):
                step = self._visible_duration() * 0.12
                direction = -1.0 if delta > 0 else 1.0
                self.set_view_start_seconds(self._view_start_t + (direction * step))
                event.accept()
                return
            if delta == 0:
                return super().wheelEvent(event)
            factor = 1.12 if delta > 0 else (1 / 1.12)
            self.set_zoom_factor(self._zoom_factor * factor)
            event.accept()

        def paintEvent(self, _event) -> None:  # pragma: no cover - Qt only
            painter = QtGui.QPainter(self)
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)

            outer = self.rect()
            painter.fillRect(outer, QtGui.QColor("#10141b"))
            rect = outer.adjusted(12, 12, -12, -12)
            if rect.width() <= 0 or rect.height() <= 0:
                return

            painter.fillRect(rect, QtGui.QColor("#161d27"))
            painter.setPen(QtGui.QPen(QtGui.QColor("#273240"), 1))
            painter.drawRoundedRect(rect, 8, 8)

            top_band = QtCore.QRectF(rect.left(), rect.top(), rect.width(), 22)
            wave_band = QtCore.QRectF(rect.left(), rect.top() + 24, rect.width(), rect.height() - 52)
            cue_band_y = rect.bottom() - 18

            section_colors = ("#1f3b33", "#2e2748", "#3f3021", "#1d3550")
            painter.save()
            painter.setClipRect(top_band)
            for index, section in enumerate(self._sections):
                start_x = self._x_for_time(rect, section.start_t)
                end_x = self._x_for_time(rect, section.end_t)
                band = QtCore.QRectF(start_x, top_band.top(), max(1.0, end_x - start_x), top_band.height())
                painter.fillRect(band, QtGui.QColor(section_colors[index % len(section_colors)]))
                painter.setPen(QtGui.QColor("#d8dee8"))
                label_rect = band.adjusted(4, 2, -4, -2)
                painter.drawText(label_rect, int(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter), section.label)
            painter.restore()

            if self._waveform:
                center_y = wave_band.center().y()
                half_height = max(8.0, wave_band.height() / 2.2)
                painter.setPen(QtGui.QPen(QtGui.QColor("#7dcfff"), 1.1))
                count = len(self._waveform)
                visible_end = self._view_start_t + self._visible_duration()
                start_index = max(
                    0,
                    int(((self._view_start_t / self._duration) * max(count - 1, 1))) - 1,
                )
                end_index = min(
                    count,
                    int(((visible_end / self._duration) * max(count - 1, 1))) + 2,
                )
                painter.save()
                painter.setClipRect(wave_band)
                for index in range(start_index, end_index):
                    value = self._waveform[index]
                    sample_t = (index / max(count - 1, 1)) * self._duration
                    if sample_t < self._view_start_t - 0.01 or sample_t > visible_end + 0.01:
                        continue
                    x = self._x_for_time(rect, sample_t)
                    amplitude = max(0.0, min(1.0, value)) * half_height
                    painter.drawLine(
                        QtCore.QPointF(x, center_y - amplitude),
                        QtCore.QPointF(x, center_y + amplitude),
                    )
                painter.restore()
            else:
                painter.setPen(QtGui.QColor("#7a8796"))
                painter.drawText(
                    wave_band,
                    int(QtCore.Qt.AlignmentFlag.AlignCenter),
                    "Waveform unavailable",
                )

            for index, beat_t in enumerate(self._beats):
                x = self._x_for_time(rect, beat_t)
                selected = self._selected_marker == ("beat", index, None)
                color = "#89b4fa" if selected else "#3a4556"
                painter.setPen(QtGui.QPen(QtGui.QColor(color), 2.5 if selected else 1))
                painter.drawLine(QtCore.QPointF(x, wave_band.top()), QtCore.QPointF(x, rect.bottom()))
            for index, downbeat_t in enumerate(self._downbeats):
                x = self._x_for_time(rect, downbeat_t)
                selected = self._selected_marker == ("downbeat", index, None)
                color = "#ffd166" if selected else "#f6c177"
                painter.setPen(QtGui.QPen(QtGui.QColor(color), 3 if selected else 1.5))
                painter.drawLine(QtCore.QPointF(x, top_band.top()), QtCore.QPointF(x, rect.bottom()))

            for index, cue_t in enumerate(self._cue_times):
                x = self._x_for_time(rect, cue_t)
                active = index == self._active_cue_index
                selected = index == self._selected_cue_index
                color = "#ff6b6b" if active else "#f5c2e7" if selected else "#89b4fa"
                painter.setPen(QtGui.QPen(QtGui.QColor(color), 2 if active else 1.4))
                painter.drawLine(QtCore.QPointF(x, wave_band.top()), QtCore.QPointF(x, cue_band_y))
                triangle = QtGui.QPolygonF(
                    [
                        QtCore.QPointF(x, cue_band_y),
                        QtCore.QPointF(x - 5, cue_band_y + 10),
                        QtCore.QPointF(x + 5, cue_band_y + 10),
                    ]
                )
                painter.setBrush(QtGui.QColor(color))
                painter.drawPolygon(triangle)

            if self._playhead_seconds is not None:
                x = self._x_for_time(rect, self._playhead_seconds)
                painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 2))
                painter.drawLine(QtCore.QPointF(x, rect.top()), QtCore.QPointF(x, rect.bottom()))

    return ShowTimelineView()
