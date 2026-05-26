"""Session control and diagnostics panel."""

from __future__ import annotations


def build_session_panel(qt_modules):
    QtWidgets = qt_modules.QtWidgets
    widget = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(widget)
    layout.addWidget(QtWidgets.QLabel("Session / Playback"))
    buttons = QtWidgets.QHBoxLayout()
    start_button = QtWidgets.QPushButton("Start Local Preview")
    stop_button = QtWidgets.QPushButton("Stop")
    buttons.addWidget(start_button)
    buttons.addWidget(stop_button)
    layout.addLayout(buttons)
    summary = QtWidgets.QPlainTextEdit()
    summary.setReadOnly(True)
    layout.addWidget(summary)
    return widget, start_button, stop_button, summary
