"""Optional Qt import helpers for the desktop GUI."""

from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType


class GuiDependencyError(RuntimeError):
    """Raised when the optional GUI dependency set is unavailable."""


@dataclass(frozen=True)
class QtModules:
    QtCore: ModuleType
    QtGui: ModuleType
    QtWidgets: ModuleType


def require_qt() -> QtModules:
    """Import and return the Qt modules required by the GUI."""
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
    except ImportError as exc:  # pragma: no cover - depends on local install
        raise GuiDependencyError(
            "PySide6 is required for the desktop GUI. "
            "Install it with: pip install dreamsync-music-sync[gui]"
        ) from exc
    return QtModules(QtCore=QtCore, QtGui=QtGui, QtWidgets=QtWidgets)
