"""Heuristic instrument-like analysis built from existing band/onset features.

This module intentionally does not attempt true source separation. It derives
phrase-aligned proxy scores that can support future routing decisions without
adding heavyweight DSP or ML dependencies to the default install.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt

from dreamsync.analyzer.features import FeatureRow
from dreamsync.analyzer.phrases import Phrase
from dreamsync.live import EQ_BAND_NAMES

_ZERO_EQ_BANDS: tuple[float, ...] = (0.0,) * len(EQ_BAND_NAMES)
_EQ_BAND_INDEX = {name: idx for idx, name in enumerate(EQ_BAND_NAMES)}
INSTRUMENT_PROXY_NAMES: tuple[str, ...] = (
    "drums",
    "bass",
    "vocals",
    "harmonic",
    "percussive",
)


@dataclass(frozen=True)
class InstrumentProxy:
    start_t: float
    end_t: float
    parent_section_index: int
    dominant_proxy: str
    drums: float = 0.0
    bass: float = 0.0
    vocals: float = 0.0
    harmonic: float = 0.0
    percussive: float = 0.0
    pan_center: float = 0.0
    pan_width: float = 0.0
    band_pan_centers: tuple[float, ...] = _ZERO_EQ_BANDS


class InstrumentHeuristicAnalyzer:
    """Build phrase-aligned instrument-like proxy scores.

    Proxy names reflect likely musical roles, not isolated stems:
    - drums: transient, kick-heavy, onset-driven activity
    - bass: sustained low-frequency weight
    - vocals: mid/presence-forward, more tonal than percussive
    - harmonic: stable pitched support
    - percussive: broad rhythmic/transient energy regardless of instrument
    """

    def __init__(
        self,
        *,
        bass_sustain_threshold: float = 0.18,
        onset_active_threshold: float = 0.18,
    ) -> None:
        self.bass_sustain_threshold = bass_sustain_threshold
        self.onset_active_threshold = onset_active_threshold

    def analyze(
        self,
        phrases: list[Phrase] | tuple[Phrase, ...],
        features: list[FeatureRow] | tuple[FeatureRow, ...],
    ) -> list[InstrumentProxy]:
        proxies: list[InstrumentProxy] = []
        for phrase in phrases:
            phrase_features = [
                feature
                for feature in features
                if phrase.start_t <= feature.t < phrase.end_t
            ]
            proxies.append(self._build_proxy(phrase, phrase_features))
        return proxies

    def _build_proxy(
        self,
        phrase: Phrase,
        phrase_features: list[FeatureRow],
    ) -> InstrumentProxy:
        band_ratios = self._phrase_band_values(phrase, phrase_features, "band_ratios")
        band_fluxes = self._phrase_band_values(phrase, phrase_features, "band_fluxes")
        bass_ratio = self._mean_attr(phrase_features, "bass_ratio")
        kick_flux = self._mean_attr(phrase_features, "kick_spectral_flux")
        onset_strength = self._mean_attr(phrase_features, "onset_strength")
        spectral_flux = self._mean_attr(phrase_features, "spectral_flux")
        chroma_stability = self._chroma_stability(phrase_features)
        onset_density = self._fraction_above(
            phrase_features,
            "onset_strength",
            self.onset_active_threshold,
        )
        bass_sustain = self._bass_sustain(phrase_features)
        pan_center = self._mean_attr(phrase_features, "pan_center")
        pan_width = self._mean_attr(phrase_features, "pan_width")
        band_pan_centers = self._mean_band_values(phrase_features, "band_pan_centers")

        drums = _clamp01(
            (0.40 * kick_flux)
            + (0.35 * onset_strength)
            + (0.25 * spectral_flux)
        )
        bass = _clamp01(
            (0.30 * bass_ratio)
            + (0.30 * band_ratios[_EQ_BAND_INDEX["bass"]])
            + (0.20 * band_ratios[_EQ_BAND_INDEX["sub"]])
            + (0.20 * bass_sustain)
        )
        percussive = _clamp01(
            (0.40 * onset_strength)
            + (0.25 * spectral_flux)
            + (0.20 * band_fluxes[_EQ_BAND_INDEX["kick"]])
            + (0.15 * onset_density)
        )
        harmonic = _clamp01(
            (0.30 * band_ratios[_EQ_BAND_INDEX["mid"]])
            + (0.25 * band_ratios[_EQ_BAND_INDEX["low_mid"]])
            + (0.15 * band_ratios[_EQ_BAND_INDEX["presence"]])
            + (0.30 * chroma_stability)
        )
        vocals = _clamp01(
            (0.30 * band_ratios[_EQ_BAND_INDEX["presence"]])
            + (0.25 * band_ratios[_EQ_BAND_INDEX["mid"]])
            + (0.20 * chroma_stability)
            + (0.10 * band_fluxes[_EQ_BAND_INDEX["presence"]])
            + (0.15 * (1.0 - percussive))
        )

        scores = {
            "drums": drums,
            "bass": bass,
            "vocals": vocals,
            "harmonic": harmonic,
            "percussive": percussive,
        }
        dominant_proxy = ""
        if max(scores.values(), default=0.0) > 0.0:
            dominant_proxy = max(scores, key=scores.get)

        return InstrumentProxy(
            start_t=round(phrase.start_t, 4),
            end_t=round(phrase.end_t, 4),
            parent_section_index=phrase.parent_section_index,
            dominant_proxy=dominant_proxy,
            drums=round(drums, 4),
            bass=round(bass, 4),
            vocals=round(vocals, 4),
            harmonic=round(harmonic, 4),
            percussive=round(percussive, 4),
            pan_center=round(pan_center, 4),
            pan_width=round(pan_width, 4),
            band_pan_centers=tuple(round(value, 4) for value in band_pan_centers),
        )

    def _phrase_band_values(
        self,
        phrase: Phrase,
        phrase_features: list[FeatureRow],
        attr_name: str,
    ) -> tuple[float, ...]:
        values = getattr(phrase, attr_name, _ZERO_EQ_BANDS)
        if any(value > 0.0 for value in values):
            return values
        if not phrase_features:
            return _ZERO_EQ_BANDS
        totals = [0.0] * len(EQ_BAND_NAMES)
        for feature in phrase_features:
            feature_values = getattr(feature, attr_name, _ZERO_EQ_BANDS)
            for idx, value in enumerate(feature_values[: len(EQ_BAND_NAMES)]):
                totals[idx] += float(value)
        count = float(len(phrase_features))
        return tuple(total / count for total in totals)

    def _mean_attr(self, features: list[FeatureRow], attr_name: str) -> float:
        if not features:
            return 0.0
        total = sum(float(getattr(feature, attr_name, 0.0)) for feature in features)
        return total / float(len(features))

    def _mean_band_values(
        self,
        features: list[FeatureRow],
        attr_name: str,
    ) -> tuple[float, ...]:
        if not features:
            return _ZERO_EQ_BANDS
        totals = [0.0] * len(EQ_BAND_NAMES)
        for feature in features:
            feature_values = getattr(feature, attr_name, _ZERO_EQ_BANDS)
            for idx, value in enumerate(feature_values[: len(EQ_BAND_NAMES)]):
                totals[idx] += float(value)
        count = float(len(features))
        return tuple(total / count for total in totals)

    def _fraction_above(
        self,
        features: list[FeatureRow],
        attr_name: str,
        threshold: float,
    ) -> float:
        if not features:
            return 0.0
        hits = sum(
            1 for feature in features
            if float(getattr(feature, attr_name, 0.0)) >= threshold
        )
        return hits / float(len(features))

    def _bass_sustain(self, features: list[FeatureRow]) -> float:
        if not features:
            return 0.0
        band_idx = _EQ_BAND_INDEX["bass"]
        hits = 0
        for feature in features:
            band_ratios = getattr(feature, "band_ratios", _ZERO_EQ_BANDS)
            band_value = float(band_ratios[band_idx]) if band_idx < len(band_ratios) else 0.0
            if max(float(getattr(feature, "bass_ratio", 0.0)), band_value) >= self.bass_sustain_threshold:
                hits += 1
        return hits / float(len(features))

    def _chroma_stability(self, features: list[FeatureRow]) -> float:
        chroma_vectors = [
            tuple(float(value) for value in getattr(feature, "chroma", ()))
            for feature in features
            if getattr(feature, "chroma", ())
        ]
        if not chroma_vectors:
            return 0.0
        if len(chroma_vectors) == 1:
            return 1.0 if max(chroma_vectors[0], default=0.0) > 0.0 else 0.0

        similarities: list[float] = []
        for prev, curr in zip(chroma_vectors, chroma_vectors[1:]):
            prev_norm = sqrt(sum(value * value for value in prev))
            curr_norm = sqrt(sum(value * value for value in curr))
            if prev_norm <= 1e-8 or curr_norm <= 1e-8:
                continue
            dot = sum(a * b for a, b in zip(prev, curr))
            similarities.append(max(0.0, min(1.0, dot / (prev_norm * curr_norm))))
        if not similarities:
            return 0.0
        return sum(similarities) / float(len(similarities))


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
