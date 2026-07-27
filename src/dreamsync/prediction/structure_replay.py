"""Deterministic causal replay harness for structure-first validation."""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from .bar_features import LiveBarFingerprintBuilder
from .boundary import StructurePredictionEngine
from .models import PredictedMusicalEvent
from .similarity import MultiFeatureSimilarityMemory
from .structure_models import LiveBarFingerprint, LiveBeatStructureObservation


@dataclass(frozen=True)
class StructureReplayResult:
    fingerprints: tuple[LiveBarFingerprint, ...]
    events: tuple[PredictedMusicalEvent, ...]
    completed_bar_update_ms: tuple[float, ...]
    memory_bars: int

    @property
    def p95_bar_update_ms(self) -> float:
        return (
            float(np.percentile(self.completed_bar_update_ms, 95))
            if self.completed_bar_update_ms
            else 0.0
        )

    @property
    def p99_bar_update_ms(self) -> float:
        return (
            float(np.percentile(self.completed_bar_update_ms, 99))
            if self.completed_bar_update_ms
            else 0.0
        )

    def deterministic_signature(self) -> tuple[object, ...]:
        return (
            tuple(
                (
                    bar.bar_index,
                    tuple(round(value, 8) for value in bar.chroma_profile),
                    round(bar.reliability, 8),
                )
                for bar in self.fingerprints
            ),
            tuple(
                (
                    event.event_type,
                    event.target.target_bar_index if event.target else None,
                    event.target.target_beat_index if event.target else None,
                    round(event.probability, 8),
                    event.cold_start,
                )
                for event in self.events
            ),
        )


def replay_structure_observations(
    observations: tuple[LiveBeatStructureObservation, ...],
    *,
    beats_per_bar: int = 4,
    memory_bars: int = 256,
) -> StructureReplayResult:
    """Replay only causal observations in their original publication order."""

    builder = LiveBarFingerprintBuilder(beats_per_bar=beats_per_bar)
    memory = MultiFeatureSimilarityMemory(max_bars=memory_bars)
    engine = StructurePredictionEngine(beats_per_bar=beats_per_bar)
    fingerprints: list[LiveBarFingerprint] = []
    events: list[PredictedMusicalEvent] = []
    timings: list[float] = []
    previous_t = -float("inf")
    previous_beat = -1
    for observation in observations:
        if observation.t < previous_t or observation.beat_index <= previous_beat:
            raise ValueError("replay observations must be strictly causal and ordered")
        previous_t = observation.t
        previous_beat = observation.beat_index
        completed = builder.observe(observation)
        if completed is None:
            continue
        started = time.perf_counter()
        row = memory.add(completed)
        sequences = tuple(
            match
            for horizon in memory.horizons
            if horizon > 1
            for match in memory.top_matches(horizon=horizon)
        )
        produced = engine.observe(
            completed,
            similarities=row,
            sequence_matches=sequences,
            current_beat_index=observation.beat_index,
            current_bar_index=int(
                observation.bar_index
                if observation.bar_index is not None
                else completed.bar_index + 1
            ),
            downbeat_t=observation.t,
            beat_period=float(observation.beat_period or 0.5),
        )
        timings.append((time.perf_counter() - started) * 1000.0)
        for event in produced:
            if event.created_t > observation.t:
                raise AssertionError("a replay prediction used a future timestamp")
            if event.matched_earlier_bar is not None and (
                event.matched_earlier_bar >= completed.bar_index
            ):
                raise AssertionError("a replay prediction exposed a future bar")
        fingerprints.append(completed)
        events.extend(produced)
    return StructureReplayResult(
        fingerprints=tuple(fingerprints),
        events=tuple(events),
        completed_bar_update_ms=tuple(timings),
        memory_bars=len(memory.fingerprints),
    )
