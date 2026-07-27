"""Bounded variable-order song-local functional progression model."""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass


@dataclass(frozen=True)
class FunctionalToken:
    function: str
    bar_position: int | None
    duration_beats: float | None

    def compact(self) -> str:
        position = "?" if self.bar_position is None else str(self.bar_position)
        duration = "?" if self.duration_beats is None else str(
            max(1, int(round(self.duration_beats)))
        )
        return f"{self.function}@bar{position}:{duration}beats"


@dataclass(frozen=True)
class SequencePrediction:
    distribution: tuple[tuple[str, float], ...]
    context_length: int
    support: float
    source: str = "song_local_sequence"


class VariableOrderProgressionModel:
    """An online n-gram with bounded contexts and deterministic backoff."""

    def __init__(
        self,
        *,
        max_order: int = 64,
        max_nodes: int = 4096,
        max_history: int = 256,
        minimum_long_context_confidence: float = 0.45,
    ) -> None:
        if max_order < 1 or max_nodes < 8:
            raise ValueError("invalid sequence model bounds")
        self.max_order = int(max_order)
        self.max_nodes = int(max_nodes)
        self.max_history = max(self.max_order + 1, int(max_history))
        self.minimum_long_context_confidence = float(
            minimum_long_context_confidence
        )
        self.reset()

    def reset(self) -> None:
        self._history: deque[str] = deque(maxlen=self.max_history)
        self._nodes: OrderedDict[
            tuple[str, ...], dict[str, float]
        ] = OrderedDict()
        self._observations = 0

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def observations(self) -> int:
        return self._observations

    def observe(
        self,
        token: FunctionalToken,
        *,
        probability: float = 1.0,
    ) -> None:
        confidence = max(0.0, min(1.0, float(probability)))
        value = token.compact()
        history = tuple(self._history)
        max_context = min(self.max_order, len(history))
        for order in range(max_context + 1):
            if order > 3 and confidence < self.minimum_long_context_confidence:
                continue
            context = history[-order:] if order else ()
            distribution = self._nodes.setdefault(context, {})
            distribution[value] = distribution.get(value, 0.0) + confidence
            self._nodes.move_to_end(context)
        self._history.append(value)
        self._observations += 1
        self._prune()

    def predict(
        self,
        context: tuple[FunctionalToken, ...] | None = None,
    ) -> SequencePrediction | None:
        compact = (
            tuple(token.compact() for token in context)
            if context is not None
            else tuple(self._history)
        )
        max_order = min(self.max_order, len(compact))
        for order in range(max_order, -1, -1):
            key = compact[-order:] if order else ()
            counts = self._nodes.get(key)
            if not counts:
                continue
            total = sum(counts.values())
            # One weak root count is not useful enough to call a prediction.
            if total < (1.5 if order == 0 else 0.8):
                continue
            ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
            distribution = tuple((value, count / total) for value, count in ranked)
            return SequencePrediction(distribution, order, total)
        return None

    def _prune(self) -> None:
        while len(self._nodes) > self.max_nodes:
            self._nodes.popitem(last=False)


def token_function(compact: str) -> str:
    return compact.split("@", 1)[0]
