"""Scalable current-and-previous chord history for Reactive Live."""

from __future__ import annotations


def build_reactive_chord_history_view(
    qt_modules,
    *,
    object_name: str = "reactiveChordHistoryView",
):
    """Build a four-card, past-only chord history surface."""

    QtCore = qt_modules.QtCore
    QtGui = qt_modules.QtGui
    QtWidgets = qt_modules.QtWidgets

    class ReactiveChordHistoryView(QtWidgets.QWidget):
        def __init__(self) -> None:
            super().__init__()
            self._current = ""
            self._previous: tuple[str, ...] = ()
            self._status = "Waiting for live input"
            self.setObjectName(object_name)
            self.setMinimumHeight(56)
            self.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Expanding,
                QtWidgets.QSizePolicy.Policy.Expanding,
            )
            self.setToolTip(
                "Confirmed chords for the previous three completed bars. "
                "Each slot locks at the next downbeat, so repeated adjacent "
                "chords remain visible as separate bars."
            )
            self._publish_state()

        def set_chord_history(
            self,
            current: str = "",
            previous=(),
            *,
            status: str = "",
        ) -> None:
            next_current = str(current or "").strip()
            next_previous = tuple(
                str(chord).strip()
                for chord in (previous or ())
            )[-3:]
            next_status = str(status or "").strip()
            if (
                next_current == self._current
                and next_previous == self._previous
                and next_status == self._status
            ):
                return
            self._current = next_current
            self._previous = next_previous
            self._status = next_status
            self._publish_state()
            self.update()

        def _publish_state(self) -> None:
            """Expose compact state for accessibility and GUI regression tests."""
            self.setProperty("currentChord", self._current)
            self.setProperty("previousChords", list(self._previous))
            self.setAccessibleName("Reactive chord history")
            summary = (
                f"Current chord {self._current}; previous completed bars "
                f"{', '.join(self._previous)}"
                if self._current
                else self._status or "No chord detected"
            )
            self.setAccessibleDescription(summary)

        def paintEvent(self, _event) -> None:  # pragma: no cover - Qt only
            painter = QtGui.QPainter(self)
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
            outer = self.rect()
            painter.fillRect(outer, QtGui.QColor("#10141b"))
            rect = QtCore.QRectF(outer.adjusted(8, 7, -8, -7))
            if rect.width() <= 0 or rect.height() <= 0:
                return
            painter.fillRect(rect, QtGui.QColor("#161d27"))
            painter.setPen(QtGui.QPen(QtGui.QColor("#334155"), 1))
            painter.drawRoundedRect(rect, 7, 7)

            scale = max(0.8, min(2.8, min(rect.width() / 720.0, rect.height() / 180.0)))
            header_height = max(18.0, 20.0 * scale)
            header_font = QtGui.QFont("Segoe UI", max(8, round(9 * scale)))
            painter.setFont(header_font)
            painter.setPen(QtGui.QColor("#94a3b8"))
            painter.drawText(
                QtCore.QRectF(
                    rect.left() + 10,
                    rect.top() + 2,
                    rect.width() - 20,
                    header_height,
                ),
                int(
                    QtCore.Qt.AlignmentFlag.AlignLeft
                    | QtCore.Qt.AlignmentFlag.AlignVCenter
                ),
                "Confirmed prior bars · downbeat locked",
            )

            cards = QtCore.QRectF(
                rect.left() + 8,
                rect.top() + header_height + 3,
                rect.width() - 16,
                max(1.0, rect.height() - header_height - 11),
            )
            gap = max(4.0, 8.0 * scale)
            card_width = max(1.0, (cards.width() - (gap * 3)) / 4.0)
            padded_previous = ("",) * (3 - len(self._previous)) + self._previous
            chord_values = padded_previous + (
                self._current or self._status or "—",
            )
            time_labels = (
                "BAR −3",
                "BAR −2",
                "BAR −1",
                "NOW",
            )

            for index, (time_label, chord) in enumerate(
                zip(time_labels, chord_values)
            ):
                card = QtCore.QRectF(
                    cards.left() + index * (card_width + gap),
                    cards.top(),
                    card_width,
                    cards.height(),
                )
                is_current = index == 3
                painter.setPen(
                    QtGui.QPen(
                        QtGui.QColor("#c084fc" if is_current else "#475569"),
                        max(1.0, 2.0 * scale) if is_current else 1.0,
                    )
                )
                painter.setBrush(
                    QtGui.QColor("#2b1f3a" if is_current else "#111827")
                )
                painter.drawRoundedRect(card, 6, 6)

                label_font = QtGui.QFont(
                    "Segoe UI",
                    max(7, round(8 * scale)),
                )
                label_font.setBold(is_current)
                painter.setFont(label_font)
                painter.setPen(
                    QtGui.QColor("#e9d5ff" if is_current else "#64748b")
                )
                painter.drawText(
                    QtCore.QRectF(
                        card.left() + 4,
                        card.top() + 2,
                        card.width() - 8,
                        max(14.0, 16.0 * scale),
                    ),
                    int(QtCore.Qt.AlignmentFlag.AlignCenter),
                    time_label,
                )

                chord_font = QtGui.QFont(
                    "Segoe UI",
                    max(10, round((18 if is_current else 15) * scale)),
                )
                chord_font.setBold(True)
                painter.setFont(chord_font)
                painter.setPen(
                    QtGui.QColor(
                        "#f8fafc"
                        if chord
                        else "#475569"
                    )
                )
                display_chord = chord or "—"
                painter.drawText(
                    QtCore.QRectF(
                        card.left() + 4,
                        card.top() + max(17.0, 19.0 * scale),
                        card.width() - 8,
                        max(1.0, card.height() - max(19.0, 21.0 * scale)),
                    ),
                    int(
                        QtCore.Qt.AlignmentFlag.AlignCenter
                        | QtCore.Qt.TextFlag.TextWordWrap
                    ),
                    display_chord,
                )

    return ReactiveChordHistoryView()
