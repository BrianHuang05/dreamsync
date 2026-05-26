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
_DOMINANCE_PRIORITY: dict[str, int] = {
    "vocals": 5,
    "bass": 4,
    "harmonic": 3,
    "drums": 2,
    "percussive": 1,
}
_SPECIFIC_PROXY_NAMES: tuple[str, ...] = ("vocals", "bass", "harmonic")
_GENERIC_PROXY_NAMES: tuple[str, ...] = ("drums", "percussive")


@dataclass(frozen=True)
class InstrumentProxy:
    start_t: float
    end_t: float
    parent_section_index: int
    dominant_proxy: str
    secondary_proxy: str = ""
    drums: float = 0.0
    bass: float = 0.0
    vocals: float = 0.0
    harmonic: float = 0.0
    percussive: float = 0.0
    active_proxies: tuple[str, ...] = ()
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
        flux_total = sum(max(0.0, float(value)) for value in band_fluxes) + 1e-8
        flux_ratios = tuple(max(0.0, float(value)) / flux_total for value in band_fluxes)
        bass_ratio = self._mean_attr(phrase_features, "bass_ratio")
        kick_flux = self._compress_feature(
            self._mean_attr(phrase_features, "kick_spectral_flux"),
            scale=20.0,
        )
        onset_strength = self._compress_feature(
            self._mean_attr(phrase_features, "onset_strength"),
            scale=0.35,
        )
        spectral_flux = self._compress_feature(
            self._mean_attr(phrase_features, "spectral_flux"),
            scale=35.0,
        )
        chroma_stability = self._chroma_stability(phrase_features)
        tonal_confidence = chroma_stability * self._chroma_presence(phrase_features)
        onset_density = self._fraction_above(
            phrase_features,
            "onset_strength",
            self.onset_active_threshold,
        )
        bass_sustain = self._bass_sustain(phrase_features)
        pan_center = self._mean_attr(phrase_features, "pan_center")
        pan_width = max(0.0, min(1.0, self._mean_attr(phrase_features, "pan_width")))
        band_pan_centers = self._mean_band_values(phrase_features, "band_pan_centers")
        center_bias = 1.0 - min(1.0, abs(pan_center))

        drums = _clamp01(
            (0.34 * kick_flux)
            + (0.28 * onset_strength)
            + (0.20 * spectral_flux)
            + (0.18 * flux_ratios[_EQ_BAND_INDEX["kick"]])
        )
        bass = _clamp01(
            (0.30 * bass_ratio)
            + (0.30 * band_ratios[_EQ_BAND_INDEX["bass"]])
            + (0.20 * band_ratios[_EQ_BAND_INDEX["sub"]])
            + (0.20 * bass_sustain)
        )
        percussive = _clamp01(
            (0.28 * onset_strength)
            + (0.24 * spectral_flux)
            + (0.24 * flux_ratios[_EQ_BAND_INDEX["kick"]])
            + (0.14 * onset_density)
            + (0.10 * kick_flux)
        )
        harmonic = _clamp01(
            (0.26 * band_ratios[_EQ_BAND_INDEX["mid"]])
            + (0.24 * band_ratios[_EQ_BAND_INDEX["low_mid"]])
            + (0.12 * band_ratios[_EQ_BAND_INDEX["presence"]])
            + (0.24 * tonal_confidence)
            + (0.08 * pan_width)
            + (0.06 * (1.0 - percussive))
        )
        vocals = _clamp01(
            (0.32 * band_ratios[_EQ_BAND_INDEX["presence"]])
            + (0.24 * band_ratios[_EQ_BAND_INDEX["mid"]])
            + (0.18 * tonal_confidence)
            + (0.10 * flux_ratios[_EQ_BAND_INDEX["presence"]])
            + (0.09 * center_bias)
            + (0.07 * (1.0 - pan_width))
        )

        scores = {
            "drums": drums,
            "bass": bass,
            "vocals": vocals,
            "harmonic": harmonic,
            "percussive": percussive,
        }
        dominant_proxy, secondary_proxy, active_proxies = self._select_proxy_roles(scores)

        return InstrumentProxy(
            start_t=round(phrase.start_t, 4),
            end_t=round(phrase.end_t, 4),
            parent_section_index=phrase.parent_section_index,
            dominant_proxy=dominant_proxy,
            secondary_proxy=secondary_proxy,
            drums=round(drums, 4),
            bass=round(bass, 4),
            vocals=round(vocals, 4),
            harmonic=round(harmonic, 4),
            percussive=round(percussive, 4),
            active_proxies=active_proxies,
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

    def _chroma_presence(self, features: list[FeatureRow]) -> float:
        chroma_vectors = [
            tuple(float(value) for value in getattr(feature, "chroma", ()))
            for feature in features
            if getattr(feature, "chroma", ())
        ]
        if not chroma_vectors:
            return 0.0
        maxima = [max(vector, default=0.0) for vector in chroma_vectors]
        if not maxima:
            return 0.0
        return _clamp01(sum(maxima) / float(len(maxima)))

    @staticmethod
    def _compress_feature(value: float, *, scale: float) -> float:
        value = max(0.0, float(value))
        if scale <= 1e-8:
            return _clamp01(value)
        return value / (value + scale)

    def _select_proxy_roles(
        self,
        scores: dict[str, float],
    ) -> tuple[str, str, tuple[str, ...]]:
        raw_scores = {name: _clamp01(score) for name, score in scores.items()}
        selection_scores = self._dominance_scores(raw_scores)
        ranked_names = sorted(
            raw_scores,
            key=lambda name: (selection_scores.get(name, 0.0), _DOMINANCE_PRIORITY.get(name, 0)),
            reverse=True,
        )
        if not ranked_names or selection_scores.get(ranked_names[0], 0.0) <= 0.0:
            return "", "", ()

        dominant_name = ranked_names[0]
        dominant_score = raw_scores.get(dominant_name, 0.0)
        best_specific_name, _ = self._best_proxy_score(selection_scores, _SPECIFIC_PROXY_NAMES)
        best_specific_score = raw_scores.get(best_specific_name, 0.0)
        best_generic_name, _ = self._best_proxy_score(selection_scores, _GENERIC_PROXY_NAMES)
        best_generic_score = raw_scores.get(best_generic_name, 0.0)

        if dominant_score < 0.38:
            dominant_name = ""
        elif dominant_name == "percussive":
            if best_specific_score >= max(0.34, dominant_score - 0.14):
                dominant_name = best_specific_name
                dominant_score = best_specific_score
            elif best_generic_name == "drums" and best_generic_score >= (dominant_score - 0.06):
                dominant_name = best_generic_name
                dominant_score = best_generic_score
        elif dominant_name == "drums":
            if best_specific_score >= max(0.36, dominant_score - 0.10):
                dominant_name = best_specific_name
                dominant_score = best_specific_score

        active_threshold = max(0.34, dominant_score - 0.12) if dominant_name else 0.38
        secondary_name = self._select_secondary_proxy(
            ranked_names,
            raw_scores,
            dominant_name=dominant_name,
            dominant_score=dominant_score,
            active_threshold=active_threshold,
        )
        active_candidates = [
            name
            for name in ranked_names
            if raw_scores.get(name, 0.0) >= active_threshold and name not in {dominant_name, secondary_name}
        ]
        ordered_active = [name for name in (dominant_name, secondary_name) if name]
        ordered_active.extend(active_candidates)
        active_proxies = tuple(ordered_active[:3])

        return dominant_name, secondary_name, active_proxies

    @staticmethod
    def _best_proxy_score(
        scores: dict[str, float],
        names: tuple[str, ...],
    ) -> tuple[str, float]:
        best_name = ""
        best_score = 0.0
        for name in names:
            score = _clamp01(scores.get(name, 0.0))
            if score > best_score or (
                abs(score - best_score) <= 1e-8
                and _DOMINANCE_PRIORITY.get(name, 0) > _DOMINANCE_PRIORITY.get(best_name, 0)
            ):
                best_name = name
                best_score = score
        return best_name, best_score

    def _select_secondary_proxy(
        self,
        ranked_names: list[str],
        raw_scores: dict[str, float],
        *,
        dominant_name: str,
        dominant_score: float,
        active_threshold: float,
    ) -> str:
        remaining = [name for name in ranked_names if name != dominant_name]
        if not remaining or dominant_score <= 0.0:
            return ""

        candidate_name = remaining[0]
        candidate_score = raw_scores.get(candidate_name, 0.0)
        if candidate_score < active_threshold:
            return ""

        best_remaining_specific = self._best_from_ranked(
            remaining,
            raw_scores,
            _SPECIFIC_PROXY_NAMES,
            exclude={dominant_name},
        )
        best_remaining_generic = self._best_from_ranked(
            remaining,
            raw_scores,
            _GENERIC_PROXY_NAMES,
            exclude={dominant_name},
        )

        if candidate_name == "percussive":
            drums_name, drums_score = self._best_from_ranked(
                remaining,
                raw_scores,
                ("drums",),
                exclude={dominant_name},
            )
            if dominant_name in _SPECIFIC_PROXY_NAMES and drums_name == "drums" and drums_score >= active_threshold:
                return drums_name
            if drums_name == "drums" and drums_score >= (candidate_score - 0.06):
                return drums_name
            specific_name, specific_score = best_remaining_specific
            if specific_name and specific_score >= max(active_threshold, candidate_score - 0.10):
                return specific_name
        elif candidate_name == "drums":
            specific_name, specific_score = best_remaining_specific
            if specific_name and specific_score >= max(active_threshold, candidate_score - 0.08):
                return specific_name

        return candidate_name

    @staticmethod
    def _best_from_ranked(
        ranked_names: list[str],
        scores: dict[str, float],
        names: tuple[str, ...],
        *,
        exclude: set[str],
    ) -> tuple[str, float]:
        best_name = ""
        best_score = 0.0
        name_set = set(names)
        for name in ranked_names:
            if name in exclude or name not in name_set:
                continue
            score = _clamp01(scores.get(name, 0.0))
            if score > best_score or (
                abs(score - best_score) <= 1e-8
                and _DOMINANCE_PRIORITY.get(name, 0) > _DOMINANCE_PRIORITY.get(best_name, 0)
            ):
                best_name = name
                best_score = score
        return best_name, best_score

    @staticmethod
    def _dominance_scores(scores: dict[str, float]) -> dict[str, float]:
        vocals = _clamp01(scores.get("vocals", 0.0))
        bass = _clamp01(scores.get("bass", 0.0))
        harmonic = _clamp01(scores.get("harmonic", 0.0))
        drums = _clamp01(scores.get("drums", 0.0))
        percussive = _clamp01(scores.get("percussive", 0.0))
        strongest_specific = max(vocals, bass, harmonic)

        return {
            "vocals": _clamp01(vocals + (0.08 * max(0.0, vocals - percussive + 0.08))),
            "bass": _clamp01(bass + (0.08 * max(0.0, bass - percussive + 0.08))),
            "harmonic": _clamp01(harmonic + (0.05 * max(0.0, harmonic - percussive + 0.05))),
            "drums": _clamp01(max(0.0, drums - (0.48 * strongest_specific) - (0.14 * percussive))),
            "percussive": _clamp01(max(0.0, percussive - (0.32 * drums) - (0.24 * strongest_specific))),
        }


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
