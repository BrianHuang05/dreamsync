"""Aggregate bounded song-local memories with one reset boundary."""

from __future__ import annotations

from dataclasses import dataclass

from .online_sequence import VariableOrderProgressionModel
from .recurrence import PhraseRecurrenceMemory


@dataclass(frozen=True)
class SongMemoryStats:
    sequence_nodes: int
    sequence_observations: int
    phrases: int


class SongLocalMemory:
    def __init__(
        self,
        *,
        max_sequence_nodes: int = 4096,
        max_phrases: int = 64,
    ) -> None:
        self.sequence = VariableOrderProgressionModel(
            max_nodes=max_sequence_nodes
        )
        self.phrases = PhraseRecurrenceMemory(max_phrases=max_phrases)

    def reset(self) -> None:
        self.sequence.reset()
        self.phrases.reset()

    def stats(self) -> SongMemoryStats:
        return SongMemoryStats(
            sequence_nodes=self.sequence.node_count,
            sequence_observations=self.sequence.observations,
            phrases=len(self.phrases.phrases),
        )
