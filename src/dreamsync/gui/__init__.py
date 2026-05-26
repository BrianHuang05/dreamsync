"""Desktop GUI support for DreamSync."""

__all__ = ["GuiDependencyError", "launch_gui"]


def __getattr__(name: str):
    if name in __all__:
        from .app import GuiDependencyError, launch_gui

        exports = {
            "GuiDependencyError": GuiDependencyError,
            "launch_gui": launch_gui,
        }
        return exports[name]
    raise AttributeError(name)
