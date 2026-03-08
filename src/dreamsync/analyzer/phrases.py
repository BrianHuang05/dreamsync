"""Phrase Segmenter — subdivide sections into 4-bar phrases with instrument event detection."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean as _mean

from dreamsync.analyzer.bpm import BeatGrid
from dreamsync.analyzer.features import FeatureRow
from dreamsync.analyzer.sections import Section


@dataclass(frozen=True)
class Phrase:
    start_t: float
    end_t: float
    parent_section_index: int
    phrase_type: str       # "steady" | "build" | "drop" | "breakdown"
    energy_delta: float
    has_kick: bool


@dataclass(frozen=True)
class InstrumentEvent:
    t: float
    event_type: str        # "kick_enter" | "kick_exit" | "bass_enter" | "bass_drop"
    confidence: float


class PhraseSegmenter:
    """Subdivide sections into N-bar phrases at downbeat boundaries."""

    def __init__(
        self,
        bars_per_phrase: int = 4,
        energy_change_threshold: float = 0.15,
        kick_presence_threshold: float = 0.3,
    ) -> None:
        self.bars_per_phrase = bars_per_phrase
        self.energy_change_threshold = energy_change_threshold
        self.kick_presence_threshold = kick_presence_threshold

    def segment(
        self,
        sections: list[Section],
        features: list[FeatureRow],
        beat_grid: BeatGrid,
    ) -> list[Phrase]:
        """Subdivide each section into phrases at downbeat boundaries."""
        phrases: list[Phrase] = []
        for idx, section in enumerate(sections):
            section_downbeats = [
                d for d in beat_grid.downbeat_times
                if section.start_t <= d < section.end_t
            ]

            if not section_downbeats:
                # Section too short for even one downbeat — make a single phrase
                phrase_features = [f for f in features if section.start_t <= f.t < section.end_t]
                energy_delta = self._compute_energy_delta(phrase_features)
                has_kick = self._detect_kick_presence(phrase_features)
                phrase_type = self._classify_phrase(energy_delta, has_kick, phrase_features)
                phrases.append(Phrase(
                    start_t=section.start_t,
                    end_t=section.end_t,
                    parent_section_index=idx,
                    phrase_type=phrase_type,
                    energy_delta=round(energy_delta, 4),
                    has_kick=has_kick,
                ))
                continue

            phrase_len = self.bars_per_phrase

            for i in range(0, len(section_downbeats), phrase_len):
                phrase_start = section_downbeats[i]
                if i + phrase_len < len(section_downbeats):
                    phrase_end = section_downbeats[i + phrase_len]
                else:
                    phrase_end = section.end_t

                phrase_features = [f for f in features if phrase_start <= f.t < phrase_end]
                energy_delta = self._compute_energy_delta(phrase_features)
                has_kick = self._detect_kick_presence(phrase_features)
                phrase_type = self._classify_phrase(energy_delta, has_kick, phrase_features)

                phrases.append(Phrase(
                    start_t=round(phrase_start, 4),
                    end_t=round(phrase_end, 4),
                    parent_section_index=idx,
                    phrase_type=phrase_type,
                    energy_delta=round(energy_delta, 4),
                    has_kick=has_kick,
                ))

        return phrases

    def _compute_energy_delta(self, features: list[FeatureRow]) -> float:
        """Energy change from first quarter to last quarter of the phrase."""
        if len(features) < 4:
            return 0.0
        quarter = max(1, len(features) // 4)
        start_energy = _mean([f.energy for f in features[:quarter]])
        end_energy = _mean([f.energy for f in features[-quarter:]])
        return end_energy - start_energy

    def _detect_kick_presence(self, features: list[FeatureRow]) -> bool:
        """Check if kick onsets are present above threshold."""
        if not features:
            return False
        avg_kick = _mean([f.kick_spectral_flux for f in features])
        return avg_kick >= self.kick_presence_threshold

    def _classify_phrase(
        self, energy_delta: float, has_kick: bool, features: list[FeatureRow],
    ) -> str:
        """Classify a phrase based on energy contour and kick presence."""
        if not features:
            return "steady"

        avg_energy = _mean([f.energy for f in features])

        if energy_delta > self.energy_change_threshold:
            return "build"
        if energy_delta < -self.energy_change_threshold and avg_energy > 0.3:
            return "drop"
        if avg_energy < 0.25 and not has_kick:
            return "breakdown"
        return "steady"


class InstrumentEventDetector:
    """Detect instrument entrance/exit events at phrase boundaries."""

    def __init__(
        self,
        kick_enter_threshold: float = 0.25,
        kick_exit_threshold: float = 0.10,
        bass_enter_ratio: float = 0.20,
        bass_drop_ratio: float = 0.45,
    ) -> None:
        self.kick_enter_threshold = kick_enter_threshold
        self.kick_exit_threshold = kick_exit_threshold
        self.bass_enter_ratio = bass_enter_ratio
        self.bass_drop_ratio = bass_drop_ratio

    def detect(
        self,
        phrases: list[Phrase],
        features: list[FeatureRow],
    ) -> list[InstrumentEvent]:
        """Detect instrument entrance/exit events at phrase boundaries."""
        if len(phrases) < 2:
            return []

        events: list[InstrumentEvent] = []
        for i in range(1, len(phrases)):
            prev = phrases[i - 1]
            curr = phrases[i]

            prev_features = [f for f in features if prev.start_t <= f.t < prev.end_t]
            curr_features = [f for f in features if curr.start_t <= f.t < curr.end_t]

            if not prev_features or not curr_features:
                continue

            prev_kick_mean = _mean([f.kick_spectral_flux for f in prev_features])
            curr_kick_mean = _mean([f.kick_spectral_flux for f in curr_features])
            prev_bass_mean = _mean([f.bass_ratio for f in prev_features])
            curr_bass_mean = _mean([f.bass_ratio for f in curr_features])

            # Kick entrance
            if prev_kick_mean < self.kick_exit_threshold and curr_kick_mean > self.kick_enter_threshold:
                confidence = min(1.0, (curr_kick_mean - prev_kick_mean) / self.kick_enter_threshold)
                events.append(InstrumentEvent(curr.start_t, "kick_enter", round(confidence, 3)))

            # Kick exit
            if prev_kick_mean > self.kick_enter_threshold and curr_kick_mean < self.kick_exit_threshold:
                confidence = min(1.0, (prev_kick_mean - curr_kick_mean) / self.kick_enter_threshold)
                events.append(InstrumentEvent(curr.start_t, "kick_exit", round(confidence, 3)))

            # Bass entrance
            if prev_bass_mean < self.bass_enter_ratio and curr_bass_mean > self.bass_enter_ratio:
                confidence = min(1.0, (curr_bass_mean - prev_bass_mean) / self.bass_enter_ratio)
                events.append(InstrumentEvent(curr.start_t, "bass_enter", round(confidence, 3)))

            # Bass drop
            if curr_bass_mean > self.bass_drop_ratio and prev_bass_mean < self.bass_drop_ratio:
                confidence = min(1.0, curr_bass_mean / self.bass_drop_ratio - 1.0)
                events.append(InstrumentEvent(curr.start_t, "bass_drop", round(confidence, 3)))

        return events
