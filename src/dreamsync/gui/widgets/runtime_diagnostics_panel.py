"""Structured runtime diagnostics panel."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeDiagnosticsWidgets:
    widget: object
    status_box: object
    metrics_box: object
    events_box: object
    warnings_box: object


def build_runtime_diagnostics_panel(qt_modules):
    QtWidgets = qt_modules.QtWidgets

    widget = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(widget)
    splitter = QtWidgets.QSplitter()
    layout.addWidget(splitter, 1)

    status_group = QtWidgets.QGroupBox("Status")
    status_layout = QtWidgets.QVBoxLayout(status_group)
    status_box = QtWidgets.QPlainTextEdit()
    status_box.setReadOnly(True)
    status_box.setObjectName("runtimeDiagnosticsStatusBox")
    status_layout.addWidget(status_box)
    splitter.addWidget(status_group)

    metrics_group = QtWidgets.QGroupBox("Metrics")
    metrics_layout = QtWidgets.QVBoxLayout(metrics_group)
    metrics_box = QtWidgets.QPlainTextEdit()
    metrics_box.setReadOnly(True)
    metrics_box.setObjectName("runtimeDiagnosticsMetricsBox")
    metrics_layout.addWidget(metrics_box)
    splitter.addWidget(metrics_group)

    events_group = QtWidgets.QGroupBox("Recent Events")
    events_layout = QtWidgets.QVBoxLayout(events_group)
    events_box = QtWidgets.QPlainTextEdit()
    events_box.setReadOnly(True)
    events_box.setObjectName("runtimeDiagnosticsEventsBox")
    events_layout.addWidget(events_box)
    splitter.addWidget(events_group)

    warnings_group = QtWidgets.QGroupBox("Errors / Warnings")
    warnings_layout = QtWidgets.QVBoxLayout(warnings_group)
    warnings_box = QtWidgets.QPlainTextEdit()
    warnings_box.setReadOnly(True)
    warnings_box.setObjectName("runtimeDiagnosticsWarningsBox")
    warnings_layout.addWidget(warnings_box)
    splitter.addWidget(warnings_group)
    splitter.setStretchFactor(0, 2)
    splitter.setStretchFactor(1, 2)
    splitter.setStretchFactor(2, 2)
    splitter.setStretchFactor(3, 2)

    return RuntimeDiagnosticsWidgets(
        widget=widget,
        status_box=status_box,
        metrics_box=metrics_box,
        events_box=events_box,
        warnings_box=warnings_box,
    )
