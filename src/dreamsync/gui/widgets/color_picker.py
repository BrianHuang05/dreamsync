"""Simple color picker surface for palette editing."""

from __future__ import annotations


def build_color_picker(qt_modules, initial_hex: str = "#ffffff"):
    QtWidgets = qt_modules.QtWidgets
    widget = QtWidgets.QWidget()
    layout = QtWidgets.QFormLayout(widget)
    hex_edit = QtWidgets.QLineEdit(initial_hex)
    preview = QtWidgets.QLabel("Preview")
    preview.setStyleSheet(f"background:{initial_hex}; color:#111; padding:8px;")
    layout.addRow("Hex", hex_edit)
    layout.addRow("Color", preview)
    return widget, hex_edit, preview
