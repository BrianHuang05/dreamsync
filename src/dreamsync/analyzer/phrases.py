"""Phrase Segmenter — subdivide sections into 4-bar phrases with instrument event detection."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean as _mean

from dreamsync.analyzer.bpm import BeatGrid
from dreamsync.analyzer.features import FeatureRow
from dreamsync.analyzer.sections import Section
from dreamsync.live import EQ_BAND_NAMES

_ZERO_EQ_BANDS: tuple[float, ...] = (0.0,) * len(EQ_BAND_NAMES)
_EQ_BAND_INDEX = {name: idx for idx, name in enumerate(EQ_BAND_NAMES)}


@dataclass(frozen=True)
class Phrase:
    start_t: float
    end_t: float
    parent_section_index: int
    phrase_type: str       # "steady" | "build" | "drop" | "breakdown"
    energy_delta: float
    has_kick: bool
    band_energies: tuple[float, ...] = _ZERO_EQ_BANDS
    band_ratios: tuple[float, ...] = _ZERO_EQ_BANDS
    band_fluxes: tuple[float, ...] = _ZERO_EQ_BANDS
    dominant_band: str = ""


@dataclass(frozen=True)
class InstrumentEvent:
    t: float
    event_type: str        # "kick_enter" | "kick_exit" | "bass_enter" | "bass_drop"
    confidence: float
    band: str | None = None


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
                phrases.append(self._build_phrase(
                    start_t=section.start_t,
                    end_t=section.end_t,
                    parent_section_index=idx,
                    phrase_features=phrase_features,
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
                phrases.append(self._build_phrase(
                    start_t=phrase_start,
                    end_t=phrase_end,
                    parent_section_index=idx,
                    phrase_features=phrase_features,
                ))

        return phrases

    def _build_phrase(
        self,
        *,
        start_t: float,
        end_t: float,
        parent_section_index: int,
        phrase_features: list[FeatureRow],
    ) -> Phrase:
        energy_delta = self._compute_energy_delta(phrase_features)
        has_kick = self._detect_kick_presence(phrase_features)
        phrase_type = self._classify_phrase(energy_delta, has_kick, phrase_features)
        band_energies = self._summarize_band_vectors(phrase_features, "band_energies")
        band_ratios = self._summarize_band_vectors(phrase_features, "band_ratios")
        band_fluxes = self._summarize_band_vectors(phrase_features, "band_fluxes")
        dominant_band = self._dominant_band(band_ratios)
        return Phrase(
            start_t=round(start_t, 4),
            end_t=round(end_t, 4),
            parent_section_index=parent_section_index,
            phrase_type=phrase_type,
            energy_delta=round(energy_delta, 4),
            has_kick=has_kick,
            band_energies=band_energies,
            band_ratios=band_ratios,
            band_fluxes=band_fluxes,
            dominant_band=dominant_band,
        )

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

    def _summarize_band_vectors(
        self,
        features: list[FeatureRow],
        attr_name: str,
    ) -> tuple[float, ...]:
        if not features:
            return _ZERO_EQ_BANDS
        totals = [0.0] * len(EQ_BAND_NAMES)
        for feature in features:
            values = getattr(feature, attr_name, _ZERO_EQ_BANDS)
            for idx, value in enumerate(values[: len(EQ_BAND_NAMES)]):
                totals[idx] += float(value)
        count = float(len(features))
        return tuple(round(total / count, 4) for total in totals)

    def _dominant_band(self, band_ratios: tuple[float, ...]) -> str:
        if not band_ratios or max(band_ratios) <= 0.0:
            return ""
        max_idx = max(range(len(band_ratios)), key=band_ratios.__getitem__)
        return EQ_BAND_NAMES[max_idx]


class InstrumentEventDetector:
    """Detect instrument entrance/exit events at phrase boundaries."""

    def __init__(
        self,
        kick_enter_threshold: float = 0.25,
        kick_exit_threshold: float = 0.10,
        bass_enter_ratio: float = 0.20,
        bass_drop_ratio: float = 0.45,
        sub_enter_ratio: float = 0.10,
        presence_lift_delta: float = 0.08,
        presence_floor: float = 0.12,
        air_swell_delta: float = 0.06,
        air_floor: float = 0.08,
    ) -> None:
        self.kick_enter_threshold = kick_enter_threshold
        self.kick_exit_threshold = kick_exit_threshold
        self.bass_enter_ratio = bass_enter_ratio
        self.bass_drop_ratio = bass_drop_ratio
        self.sub_enter_ratio = sub_enter_ratio
        self.presence_lift_delta = presence_lift_delta
        self.presence_floor = presence_floor
        self.air_swell_delta = air_swell_delta
        self.air_floor = air_floor

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
            prev_sub_ratio = self._phrase_band_value(prev, prev_features, "band_ratios", "sub")
            curr_sub_ratio = self._phrase_band_value(curr, curr_features, "band_ratios", "sub")
            prev_presence_ratio = self._phrase_band_value(prev, prev_features, "band_ratios", "presence")
            curr_presence_ratio = self._phrase_band_value(curr, curr_features, "band_ratios", "presence")
            prev_air_ratio = self._phrase_band_value(prev, prev_features, "band_ratios", "air")
            curr_air_ratio = self._phrase_band_value(curr, curr_features, "band_ratios", "air")

            # Kick entrance
            if prev_kick_mean < self.kick_exit_threshold and curr_kick_mean > self.kick_enter_threshold:
                confidence = min(1.0, (curr_kick_mean - prev_kick_mean) / self.kick_enter_threshold)
                events.append(InstrumentEvent(curr.start_t, "kick_enter", round(confidence, 3), "kick"))

            # Kick exit
            if prev_kick_mean > self.kick_enter_threshold and curr_kick_mean < self.kick_exit_threshold:
                confidence = min(1.0, (prev_kick_mean - curr_kick_mean) / self.kick_enter_threshold)
                events.append(InstrumentEvent(curr.start_t, "kick_exit", round(confidence, 3), "kick"))

            # Bass entrance
            if prev_bass_mean < self.bass_enter_ratio and curr_bass_mean > self.bass_enter_ratio:
                confidence = min(1.0, (curr_bass_mean - prev_bass_mean) / self.bass_enter_ratio)
                events.append(InstrumentEvent(curr.start_t, "bass_enter", round(confidence, 3), "bass"))

            # Bass drop
            if curr_bass_mean > self.bass_drop_ratio and prev_bass_mean < self.bass_drop_ratio:
                confidence = min(1.0, curr_bass_mean / self.bass_drop_ratio - 1.0)
                events.append(InstrumentEvent(curr.start_t, "bass_drop", round(confidence, 3), "bass"))

            # Sub entrance
            if prev_sub_ratio < self.sub_enter_ratio and curr_sub_ratio > self.sub_enter_ratio:
                confidence = min(1.0, (curr_sub_ratio - prev_sub_ratio) / self.sub_enter_ratio)
                events.append(InstrumentEvent(curr.start_t, "sub_enter", round(confidence, 3), "sub"))

            # Presence lift
            presence_delta = curr_presence_ratio - prev_presence_ratio
            if curr_presence_ratio > self.presence_floor and presence_delta > self.presence_lift_delta:
                confidence = min(1.0, presence_delta / self.presence_lift_delta)
                events.append(InstrumentEvent(curr.start_t, "presence_lift", round(confidence, 3), "presence"))

            # Air swell
            air_delta = curr_air_ratio - prev_air_ratio
            if curr_air_ratio > self.air_floor and air_delta > self.air_swell_delta:
                confidence = min(1.0, air_delta / self.air_swell_delta)
                events.append(InstrumentEvent(curr.start_t, "air_swell", round(confidence, 3), "air"))

        return events

    def _phrase_band_value(
        self,
        phrase: Phrase,
        phrase_features: list[FeatureRow],
        attr_name: str,
        band_name: str,
    ) -> float:
        idx = _EQ_BAND_INDEX[band_name]
        values = getattr(phrase, attr_name, _ZERO_EQ_BANDS)
        if any(value > 0.0 for value in values):
            return float(values[idx])
        if not phrase_features:
            return 0.0
        feature_values = [getattr(f, attr_name, _ZERO_EQ_BANDS)[idx] for f in phrase_features]
        return float(_mean(feature_values))
