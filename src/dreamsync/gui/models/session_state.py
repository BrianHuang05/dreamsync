"""Session lifecycle and diagnostics state."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SessionState:
    mode: str = "idle"
    running: bool = False
    current_track: str = ""
    last_summary: dict[str, object] = field(default_factory=dict)
    log_lines: tuple[str, ...] = field(default_factory=tuple)
