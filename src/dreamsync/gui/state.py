"""Top-level GUI state models."""

from __future__ import annotations

from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class StatusBarState:
    current_profile: str = ""
    session_state: str = "idle"
    output_mode: str = "none"
    device_summary: str = "0 devices"


@dataclass(frozen=True)
class AppState:
    selected_tab: str = "Devices / Spatial"
    config_path: str = ""
    profile_path: str = ""
    diagnostics: tuple[str, ...] = field(default_factory=tuple)
    status_bar: StatusBarState = field(default_factory=StatusBarState)

    def with_tab(self, name: str) -> "AppState":
        return replace(self, selected_tab=name)

    def with_diagnostic(self, message: str) -> "AppState":
        return replace(self, diagnostics=(*self.diagnostics, message))
