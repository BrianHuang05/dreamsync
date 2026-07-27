"""Beat-synchronous macro-structure inference for reactive live mode."""

from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass
from typing import Literal

import numpy as np

from dreamsync.dsp.harmonic import LiveHarmonicState
from dreamsync.dsp.meter import LiveMeterState


@dataclass(frozen=True)
class LiveStructureEvent:
    """A timestamped local or macro live-structure observation."""

    kind: Literal["harmonic_change", "macro_candidate", "macro_change"]
    t: float
    confidence: float
    harmonic_novelty: float
    phase_confidence: float
    bar_index: int | None
    phrase_index: int | None
    chord_before: str | None
    chord_after: str | None


@dataclass(frozen=True)
class _BeatSummary:
    chroma: tuple[float, ...]
    confidence: float
    energy: float
    centroid: float
    onset: float


@dataclass(frozen=True)
class _BarSummary:
    chroma: tuple[float, ...]
    confidence: float
    energy: float
    centroid: float
    onset: float


@dataclass
class _MacroCandidate:
    event: LiveStructureEvent
    reference_chroma: np.ndarray
    candidate_chroma: np.ndarray
    expires_at: float


class LiveStructureTracker:
    """Aggregate harmonic frames into beat/bar/phrase macro evidence.

    A macro candidate is possible only near a confidence-gated downbeat at an
    expected phrase cadence.  It becomes a macro change only after the new
    harmonic state persists for 100--250 ms.
    """

    def __init__(
        self,
        *,
        beats_per_bar: int = 4,
        bars_per_phrase: int = 4,
        sensitivity: float = 0.5,
        minimum_phase_confidence: float = 0.22,
        confirmation_seconds: float = 0.12,
        candidate_window_seconds: float = 0.30,
        downbeat_window_seconds: float = 0.35,
        cooldown_seconds: float = 4.0,
    ) -> None:
        if beats_per_bar < 2 or bars_per_phrase < 1:
            raise ValueError("invalid bar or phrase size")
        if not 0.0 <= sensitivity <= 1.0:
            raise ValueError("sensitivity must be between 0 and 1")
        if not 0.0 < confirmation_seconds <= candidate_window_seconds:
            raise ValueError("confirmation must fit inside the candidate window")

        self.beats_per_bar = int(beats_per_bar)
        self.bars_per_phrase = int(bars_per_phrase)
        self.sensitivity = float(sensitivity)
        self.minimum_phase_confidence = float(minimum_phase_confidence)
        self.confirmation_seconds = float(confirmation_seconds)
        self.candidate_window_seconds = float(candidate_window_seconds)
        self.downbeat_window_seconds = float(downbeat_window_seconds)
        self.cooldown_seconds = float(cooldown_seconds)
        self._harmonic_frames: deque[LiveHarmonicState] = deque(maxlen=64)
        self._beats: deque[_BeatSummary] = deque(maxlen=self.beats_per_bar)
        self._bars: deque[_BarSummary] = deque(
            maxlen=max(8, self.bars_per_phrase * 2)
        )
        self._score_history: deque[float] = deque(maxlen=64)
        self._completed_bars = 0
        self._last_downbeat_t: float | None = None
        self._last_phase_confidence = 0.0
        self._candidate: _MacroCandidate | None = None
        self._last_macro_t = -1e9
        self._last_chord: str | None = None
        self._last_event: LiveStructureEvent | None = None

    @property
    def last_event(self) -> LiveStructureEvent | None:
        return self._last_event

    @property
    def completed_bars(self) -> int:
        return self._completed_bars

    def reset(self) -> None:
        self._harmonic_frames.clear()
        self._beats.clear()
        self._bars.clear()
        self._score_history.clear()
        self._completed_bars = 0
        self._last_downbeat_t = None
        self._last_phase_confidence = 0.0
        self._candidate = None
        self._last_macro_t = -1e9
        self._last_chord = None
        self._last_event = None

    def observe_beat(
        self,
        meter: LiveMeterState,
        *,
        energy: float,
        centroid: float,
        onset: float,
    ) -> None:
        """Close a causal beat summary and, on downbeats, the previous bar."""

        if not meter.beat:
            return
        summary = self._summarize_beat(
            energy=float(energy),
            centroid=float(centroid),
            onset=float(onset),
        )
        if meter.downbeat and meter.meter_confident:
            if len(self._beats) >= self.beats_per_bar:
                self._bars.append(self._summarize_bar(tuple(self._beats)))
                self._completed_bars += 1
            self._beats.clear()
            self._last_downbeat_t = float(meter.t)
            self._last_phase_confidence = float(meter.phase_confidence)
        self._beats.append(summary)
        self._harmonic_frames.clear()

    def observe_harmonic(
        self,
        state: LiveHarmonicState,
        *,
        energy: float,
        centroid: float,
        onset: float,
    ) -> tuple[LiveStructureEvent, ...]:
        """Observe one harmonic frame and return any new structure events."""

        if state.tonal_confidence > 0.0:
            self._harmonic_frames.append(state)
        events: list[LiveStructureEvent] = []
        before = self._last_chord

        if state.harmonic_change:
            local_event = LiveStructureEvent(
                kind="harmonic_change",
                t=float(state.t),
                confidence=float(
                    min(state.tonal_confidence, state.chord_confidence)
                ),
                harmonic_novelty=float(state.novelty),
                phase_confidence=self._last_phase_confidence,
                bar_index=(
                    self._completed_bars if self._completed_bars else None
                ),
                phrase_index=self._phrase_index(),
                chord_before=before,
                chord_after=state.chord or None,
            )
            events.append(local_event)
            self._last_event = local_event

            candidate = self._maybe_start_candidate(
                state,
                energy=float(energy),
                centroid=float(centroid),
                onset=float(onset),
                chord_before=before,
            )
            if candidate is not None:
                events.append(candidate)
                self._last_event = candidate
            self._last_chord = state.chord or before

        confirmed = self._maybe_confirm_candidate(state)
        if confirmed is not None:
            events.append(confirmed)
            self._last_event = confirmed
        return tuple(events)

    def _maybe_start_candidate(
        self,
        state: LiveHarmonicState,
        *,
        energy: float,
        centroid: float,
        onset: float,
        chord_before: str | None,
    ) -> LiveStructureEvent | None:
        if (
            self._last_downbeat_t is None
            or state.t - self._last_downbeat_t < 0.0
            or state.t - self._last_downbeat_t > self.downbeat_window_seconds
            or self._last_phase_confidence < self.minimum_phase_confidence
            or self._completed_bars < self.bars_per_phrase
            or self._completed_bars % self.bars_per_phrase != 0
            or state.t - self._last_macro_t < self.cooldown_seconds
            or len(self._bars) < self.bars_per_phrase
        ):
            return None

        phrase = tuple(self._bars)[-self.bars_per_phrase :]
        reference_chroma = self._median_chroma([bar.chroma for bar in phrase])
        harmonic_distance = 0.5 * float(
            np.sum(np.abs(np.asarray(state.chroma) - reference_chroma))
        )
        energy_reference = statistics.median(bar.energy for bar in phrase)
        centroid_reference = statistics.median(bar.centroid for bar in phrase)
        onset_reference = statistics.median(bar.onset for bar in phrase)
        energy_contrast = self._relative_contrast(energy, energy_reference)
        centroid_contrast = self._relative_contrast(
            centroid,
            centroid_reference,
        )
        texture_contrast = max(energy_contrast, centroid_contrast)
        onset_contrast = self._relative_contrast(onset, onset_reference)
        cadence_support = 1.0
        novelty = max(float(state.novelty), harmonic_distance)
        score = (
            (0.50 * novelty)
            + (0.20 * texture_contrast)
            + (0.15 * onset_contrast)
            + (0.15 * cadence_support)
        )
        threshold = self._macro_threshold()
        self._score_history.append(score)
        if score < threshold:
            return None

        confidence = min(
            1.0,
            score * max(0.0, min(1.0, self._last_phase_confidence)),
        )
        event = LiveStructureEvent(
            kind="macro_candidate",
            t=float(state.t),
            confidence=round(confidence, 6),
            harmonic_novelty=round(novelty, 6),
            phase_confidence=round(self._last_phase_confidence, 6),
            bar_index=self._completed_bars,
            phrase_index=self._phrase_index(),
            chord_before=chord_before,
            chord_after=state.chord or None,
        )
        candidate_chroma = np.asarray(state.chroma, dtype=np.float32)
        self._candidate = _MacroCandidate(
            event=event,
            reference_chroma=reference_chroma,
            candidate_chroma=candidate_chroma,
            expires_at=float(state.t) + self.candidate_window_seconds,
        )
        return event

    def _maybe_confirm_candidate(
        self,
        state: LiveHarmonicState,
    ) -> LiveStructureEvent | None:
        candidate = self._candidate
        if candidate is None:
            return None
        elapsed = float(state.t) - candidate.event.t
        if elapsed > self.candidate_window_seconds or state.t > candidate.expires_at:
            self._candidate = None
            return None
        if elapsed < self.confirmation_seconds or state.tonal_confidence < 0.30:
            return None
        current = np.asarray(state.chroma, dtype=np.float32)
        reference_distance = 0.5 * float(
            np.sum(np.abs(current - candidate.reference_chroma))
        )
        candidate_distance = 0.5 * float(
            np.sum(np.abs(current - candidate.candidate_chroma))
        )
        required_distance = max(
            0.12,
            candidate.event.harmonic_novelty * 0.55,
        )
        if reference_distance < required_distance or candidate_distance > 0.22:
            return None

        event = LiveStructureEvent(
            kind="macro_change",
            t=float(state.t),
            confidence=candidate.event.confidence,
            harmonic_novelty=round(reference_distance, 6),
            phase_confidence=candidate.event.phase_confidence,
            bar_index=candidate.event.bar_index,
            phrase_index=candidate.event.phrase_index,
            chord_before=candidate.event.chord_before,
            chord_after=state.chord or candidate.event.chord_after,
        )
        self._candidate = None
        self._last_macro_t = float(state.t)
        return event

    def _summarize_beat(
        self,
        *,
        energy: float,
        centroid: float,
        onset: float,
    ) -> _BeatSummary:
        tonal = [
            state
            for state in self._harmonic_frames
            if state.tonal_confidence >= 0.20
        ]
        if tonal:
            chroma = self._median_chroma([state.chroma for state in tonal])
            confidence = statistics.median(
                state.tonal_confidence for state in tonal
            )
        elif self._bars:
            chroma = np.asarray(self._bars[-1].chroma, dtype=np.float32)
            confidence = 0.0
        else:
            chroma = np.zeros(12, dtype=np.float32)
            confidence = 0.0
        return _BeatSummary(
            chroma=tuple(float(value) for value in chroma),
            confidence=float(confidence),
            energy=energy,
            centroid=centroid,
            onset=onset,
        )

    def _summarize_bar(self, beats: tuple[_BeatSummary, ...]) -> _BarSummary:
        chroma = self._median_chroma([beat.chroma for beat in beats])
        return _BarSummary(
            chroma=tuple(float(value) for value in chroma),
            confidence=float(statistics.median(beat.confidence for beat in beats)),
            energy=float(statistics.median(beat.energy for beat in beats)),
            centroid=float(statistics.median(beat.centroid for beat in beats)),
            onset=float(statistics.median(beat.onset for beat in beats)),
        )

    def _macro_threshold(self) -> float:
        base = 0.50 - (0.12 * self.sensitivity)
        if len(self._score_history) < 8:
            return base
        median = statistics.median(self._score_history)
        mad = statistics.median(
            abs(score - median) for score in self._score_history
        )
        return max(base, min(0.75, median + (2.5 * 1.4826 * mad)))

    def _phrase_index(self) -> int | None:
        if self._completed_bars < self.bars_per_phrase:
            return None
        return self._completed_bars // self.bars_per_phrase

    @staticmethod
    def _median_chroma(chromas: list[tuple[float, ...]]) -> np.ndarray:
        if not chromas:
            return np.zeros(12, dtype=np.float32)
        median = np.median(np.asarray(chromas, dtype=np.float32), axis=0)
        total = float(np.sum(median))
        return median / total if total > 1e-9 else median

    @staticmethod
    def _relative_contrast(value: float, reference: float) -> float:
        denominator = max(0.05, abs(float(value)), abs(float(reference)))
        return max(0.0, min(1.0, abs(float(value) - float(reference)) / denominator))
