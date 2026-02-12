from __future__ import annotations

from typing import Protocol

from dreamsync.director import LightingIntent


class OutputAdapter(Protocol):
    def emit(self, t: float, intent: LightingIntent) -> None:
        """Emit a lighting intent at logical time t."""
