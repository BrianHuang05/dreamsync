"""Log viewer panel."""

from __future__ import annotations


def build_log_panel(qt_modules):
    QtWidgets = qt_modules.QtWidgets
    widget = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(widget)
    layout.addWidget(QtWidgets.QLabel("Logs / Diagnostics"))
    log_box = QtWidgets.QPlainTextEdit()
    log_box.setReadOnly(True)
    layout.addWidget(log_box)
    return widget, log_box
