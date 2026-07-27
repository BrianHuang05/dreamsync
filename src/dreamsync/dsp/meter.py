"""Causal, confidence-gated meter inference for reactive live mode."""

from __future__ import annotations

import math
import statistics
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class LiveMeterState:
    """Latest inferred metrical role of a live beat."""

    t: float = 0.0
    beat: bool = False
    downbeat: bool = False
    bar_phase: int | None = None
    relative_phase: int | None = None
    phase_confidence: float = 0.0
    meter_confident: bool = False
    inferred_missing_beats: int = 0
    evidence: float = 0.0


@dataclass(frozen=True)
class ManualBeatMarker:
    t: float
    kind: str
    beat_index: int


class ManualBeatRegistration:
    """Collect snapped D/S markers and infer meter between downbeats."""

    def __init__(self, *, beats_per_bar: int = 4, max_markers: int = 128):
        self.beats_per_bar = int(beats_per_bar)
        self._markers: deque[ManualBeatMarker] = deque(maxlen=max_markers)
        self._last_downbeat_index: int | None = None
        self._beat_indices_since_downbeat: set[int] = set()
        self.count = 0

    @property
    def markers(self) -> tuple[ManualBeatMarker, ...]:
        return tuple(self._markers)

    def register(
        self,
        *,
        t: float,
        kind: str,
        beat_index: int,
    ) -> int | None:
        normalized = "downbeat" if kind == "downbeat" else "beat"
        index = int(beat_index)
        inferred: int | None = None
        if normalized == "downbeat":
            if self._last_downbeat_index is not None:
                candidate = (
                    len(self._beat_indices_since_downbeat) + 1
                    if self._beat_indices_since_downbeat
                    else index - self._last_downbeat_index
                )
                if 2 <= candidate <= 12:
                    inferred = candidate
                    self.beats_per_bar = candidate
            self._last_downbeat_index = index
            self._beat_indices_since_downbeat.clear()
        elif self._last_downbeat_index is not None:
            self._beat_indices_since_downbeat.add(index)
        retained = [
            marker
            for marker in self._markers
            if marker.beat_index != index
        ]
        self._markers.clear()
        self._markers.extend(retained)
        self._markers.append(
            ManualBeatMarker(float(t), normalized, index)
        )
        self.count += 1
        return inferred

    def prune_before(self, cutoff_t: float) -> None:
        while self._markers and self._markers[0].t < float(cutoff_t):
            self._markers.popleft()

    def reset(self, *, beats_per_bar: int | None = None) -> None:
        if beats_per_bar is not None:
            self.beats_per_bar = int(beats_per_bar)
        self._markers.clear()
        self._last_downbeat_index = None
        self._beat_indices_since_downbeat.clear()
        self.count = 0


class LiveMeterTracker:
    """Infer a fixed meter's downbeat phase from recurring beat accents.

    The tracker keeps one hypothesis for every possible bar phase. It abstains
    until at least ``warmup_bars`` have been observed and the best hypothesis
    is separated from the runner-up by ``min_confidence``.
    """

    def __init__(
        self,
        *,
        beats_per_bar: int = 4,
        warmup_bars: int = 2,
        min_confidence: float = 0.22,
        score_decay: float = 0.97,
        history_beats: int = 32,
    ) -> None:
        if beats_per_bar < 2:
            raise ValueError("beats_per_bar must be at least 2")
        if warmup_bars < 1:
            raise ValueError("warmup_bars must be at least 1")
        if not 0.0 < min_confidence < 1.0:
            raise ValueError("min_confidence must be between 0 and 1")
        if not 0.0 < score_decay <= 1.0:
            raise ValueError("score_decay must be in (0, 1]")

        self.beats_per_bar = int(beats_per_bar)
        self._warmup_bars = int(warmup_bars)
        self.warmup_beats = int(warmup_bars * beats_per_bar)
        self.min_confidence = float(min_confidence)
        self.score_decay = float(score_decay)
        self._phase_scores = [0.0] * self.beats_per_bar
        self._low_history: deque[float] = deque(maxlen=history_beats)
        self._onset_history: deque[float] = deque(maxlen=history_beats)
        self._energy_history: deque[float] = deque(maxlen=history_beats)
        self._harmonic_history: deque[float] = deque(maxlen=history_beats)
        self._beat_index = -1
        self._observed_beats = 0
        self._last_beat_t: float | None = None
        self._last_period = 0.5
        self._manual_anchor: int | None = None
        self._state = LiveMeterState()

    @property
    def state(self) -> LiveMeterState:
        return self._state

    @property
    def beat_index(self) -> int:
        return self._beat_index

    def set_beats_per_bar(self, beats_per_bar: int) -> LiveMeterState:
        """Change meter while preserving the current beat as the bar anchor."""

        value = int(beats_per_bar)
        if value < 2:
            raise ValueError("beats_per_bar must be at least 2")
        self.beats_per_bar = value
        self.warmup_beats = self._warmup_bars * value
        self._phase_scores = [-2.0] * value
        if self._beat_index < 0:
            self._manual_anchor = None
            self._state = LiveMeterState()
            return self._state
        self._manual_anchor = self._beat_index % value
        self._phase_scores[self._manual_anchor] = 4.0
        return self.nudge_downbeat(t=self._state.t)

    def reset(self) -> None:
        self._phase_scores = [0.0] * self.beats_per_bar
        self._low_history.clear()
        self._onset_history.clear()
        self._energy_history.clear()
        self._harmonic_history.clear()
        self._beat_index = -1
        self._observed_beats = 0
        self._last_beat_t = None
        self._last_period = 0.5
        self._manual_anchor = None
        self._state = LiveMeterState()

    def nudge_downbeat(self, *, t: float | None = None) -> LiveMeterState:
        """Make the most recently observed beat the persistent bar anchor."""

        if self._beat_index < 0:
            raise RuntimeError("cannot nudge downbeat before observing a beat")
        anchor = self._beat_index % self.beats_per_bar
        self._manual_anchor = anchor
        self._observed_beats = max(self._observed_beats, self.warmup_beats)
        self._phase_scores = [-2.0] * self.beats_per_bar
        self._phase_scores[anchor] = 4.0
        state = LiveMeterState(
            t=float(self._state.t if t is None else t),
            beat=True,
            downbeat=True,
            bar_phase=0,
            relative_phase=self._beat_index % self.beats_per_bar,
            phase_confidence=1.0,
            meter_confident=True,
            inferred_missing_beats=self._state.inferred_missing_beats,
            evidence=self._state.evidence,
        )
        self._state = state
        return state

    def observe_beat(
        self,
        *,
        t: float,
        bpm: float,
        low_frequency: float,
        onset_strength: float,
        energy: float,
        harmonic_novelty: float = 0.0,
        harmonic_confidence: float = 0.0,
    ) -> LiveMeterState:
        """Observe one detected beat and return the updated meter state."""

        now = float(t)
        period = 60.0 / float(bpm) if bpm > 0.0 else self._last_period
        if not math.isfinite(period) or period <= 0.0:
            period = self._last_period

        steps = 1
        inferred_missing = 0
        if self._last_beat_t is not None:
            elapsed = max(0.0, now - self._last_beat_t)
            if period > 1e-6:
                steps = max(1, min(self.beats_per_bar * 2, int(round(elapsed / period))))
                inferred_missing = max(0, steps - 1)
        self._beat_index += steps
        self._observed_beats += 1
        self._last_beat_t = now
        self._last_period = period

        low_score = self._accent_score(low_frequency, self._low_history)
        onset_score = self._accent_score(onset_strength, self._onset_history)
        energy_score = self._accent_score(energy, self._energy_history)
        components = [
            (low_score, 0.40),
            (onset_score, 0.35),
            (energy_score, 0.25),
        ]
        harmonic_weight = 0.35 * max(0.0, min(1.0, float(harmonic_confidence)))
        if harmonic_weight > 0.0:
            harmonic_score = self._accent_score(
                harmonic_novelty,
                self._harmonic_history,
            )
            components.append((harmonic_score, harmonic_weight))
        elif harmonic_novelty != 0.0:
            self._harmonic_history.append(float(harmonic_novelty))

        total_weight = sum(weight for _score, weight in components)
        evidence = sum(score * weight for score, weight in components) / total_weight
        centered = evidence - 0.5

        if inferred_missing:
            confidence_decay = 0.82 ** inferred_missing
            self._phase_scores = [
                score * confidence_decay for score in self._phase_scores
            ]

        offbeat_scale = 1.0 / max(1, self.beats_per_bar - 1)
        for anchor in range(self.beats_per_bar):
            is_downbeat_phase = (self._beat_index - anchor) % self.beats_per_bar == 0
            contribution = centered if is_downbeat_phase else -centered * offbeat_scale
            self._phase_scores[anchor] = (
                self._phase_scores[anchor] * self.score_decay
            ) + contribution

        probabilities = self._phase_probabilities()
        ranked = sorted(
            range(self.beats_per_bar),
            key=lambda anchor: probabilities[anchor],
            reverse=True,
        )
        if self._manual_anchor is not None:
            best_anchor = self._manual_anchor
            confidence = 1.0
            meter_confident = True
        else:
            best_anchor = ranked[0]
            best_probability = probabilities[best_anchor]
            next_probability = probabilities[ranked[1]]
            confidence = max(
                0.0,
                min(1.0, best_probability - next_probability),
            )
            meter_confident = (
                self._observed_beats >= self.warmup_beats
                and confidence >= self.min_confidence
            )
        inferred_phase = (self._beat_index - best_anchor) % self.beats_per_bar
        relative_phase = self._beat_index % self.beats_per_bar
        state = LiveMeterState(
            t=now,
            beat=True,
            downbeat=bool(meter_confident and inferred_phase == 0),
            bar_phase=inferred_phase if meter_confident else None,
            relative_phase=relative_phase,
            phase_confidence=round(confidence, 6),
            meter_confident=meter_confident,
            inferred_missing_beats=inferred_missing,
            evidence=round(evidence, 6),
        )
        self._state = state
        return state

    @staticmethod
    def _accent_score(value: float, history: deque[float]) -> float:
        current = max(0.0, float(value))
        if len(history) < 4:
            score = 0.5
        else:
            median = statistics.median(history)
            deviations = [abs(item - median) for item in history]
            mad = statistics.median(deviations)
            scale = max(1e-9, 1.4826 * mad, abs(median) * 0.05)
            z_score = max(-4.0, min(4.0, (current - median) / scale))
            score = 1.0 / (1.0 + math.exp(-z_score))
        history.append(current)
        return score

    def _phase_probabilities(self) -> list[float]:
        maximum = max(self._phase_scores)
        weights = [math.exp((score - maximum) * 2.0) for score in self._phase_scores]
        total = sum(weights) or 1.0
        return [weight / total for weight in weights]
