"""Simple swatch strip widget."""

from __future__ import annotations


def build_palette_strip(qt_modules, colors: tuple[str, ...]):
    QtWidgets = qt_modules.QtWidgets
    container = QtWidgets.QWidget()
    layout = QtWidgets.QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    for color in colors:
        label = QtWidgets.QLabel(color)
        label.setMinimumHeight(28)
        label.setStyleSheet(f"background:{color}; color:#111; padding:4px;")
        layout.addWidget(label)
    layout.addStretch(1)
    return container
