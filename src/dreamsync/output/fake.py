from __future__ import annotations

from dataclasses import dataclass

from dreamsync.director import LightingIntent


@dataclass(frozen=True)
class RecordedIntent:
    t: float
    mode: str
    intensity: float
    speed: float
    bpm: float


class FakeOutputAdapter:
    def __init__(self) -> None:
        self.records: list[RecordedIntent] = []

    def emit(self, t: float, intent: LightingIntent) -> None:
        self.records.append(
            RecordedIntent(
                t=t,
                mode=str(intent.mode.value),
                intensity=float(intent.intensity),
                speed=float(intent.speed),
                bpm=float(intent.bpm),
            )
        )
