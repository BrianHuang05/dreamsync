"""Qt application bootstrap for DreamSync desktop."""

from __future__ import annotations

from pathlib import Path

from .main_window import create_main_window
from .qt import GuiDependencyError, require_qt
from .settings import GuiSettingsStore


def launch_gui(
    *,
    config_path: Path | None = None,
    profile_path: Path | None = None,
    settings_path: Path | None = None,
    close_after_ms: int | None = None,
) -> int:
    """Launch the desktop GUI and block until it closes."""
    qt_modules = require_qt()
    QtWidgets = qt_modules.QtWidgets
    QtCore = qt_modules.QtCore

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    store = GuiSettingsStore(settings_path)
    settings = store.load()
    window = create_main_window(
        qt_modules,
        settings,
        config_path=config_path,
        profile_path=profile_path,
    )
    window.show()

    if close_after_ms is not None:
        QtCore.QTimer.singleShot(close_after_ms, window.close)

    result = int(app.exec())
    snapshot_fn = getattr(window, "_dreamsync_settings_snapshot", None)
    if callable(snapshot_fn):
        store.save(snapshot_fn())
    else:
        current_tab = window.centralWidget().tabText(window.centralWidget().currentIndex())
        store.save(
            type(settings)(
                last_config_path=str(config_path or settings.last_config_path),
                last_profile_path=str(profile_path or settings.last_profile_path),
                last_tab=current_tab,
                window_geometry=settings.window_geometry,
                splitter_sizes=settings.splitter_sizes,
            )
        )
    return result


__all__ = ["GuiDependencyError", "launch_gui"]
