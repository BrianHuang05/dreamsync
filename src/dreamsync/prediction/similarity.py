"""Bounded song-local multi-feature bar and sequence similarity."""

from __future__ import annotations

from collections import Counter, OrderedDict
from dataclasses import dataclass
from math import sqrt
from statistics import median

import numpy as np

from .structure_models import BarSimilarity, LiveBarFingerprint, SequenceMatch


DEFAULT_GROUP_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("harmonic_absolute", 0.20),
    ("harmonic_transposed", 0.20),
    ("timbre", 0.25),
    ("rhythm", 0.20),
    ("dynamics", 0.15),
)
DEFAULT_HORIZONS = (1, 2, 4, 8, 12, 16)
_ROLL_INDICES = np.asarray(
    [
        [(pitch - shift) % 12 for pitch in range(12)]
        for shift in range(12)
    ],
    dtype=np.intp,
)


@dataclass(frozen=True)
class SimilarityDiagnostics:
    latest_bar: int | None
    top_bar_matches: tuple[BarSimilarity, ...]
    top_sequence_matches: tuple[SequenceMatch, ...]
    memory_bars: int


@dataclass(frozen=True)
class _HarmonicArrays:
    profile: np.ndarray
    shifted_profiles: np.ndarray
    profile_norm: float
    beats: np.ndarray
    shifted_beats: np.ndarray
    beat_norms: np.ndarray


class MultiFeatureSimilarityMemory:
    """Retain bounded fingerprints, recent rows, and top recurrence matches."""

    def __init__(
        self,
        *,
        max_bars: int = 256,
        horizons: tuple[int, ...] = DEFAULT_HORIZONS,
        group_weights: tuple[tuple[str, float], ...] = DEFAULT_GROUP_WEIGHTS,
        top_matches_per_horizon: int = 3,
    ) -> None:
        if max_bars < 2:
            raise ValueError("max_bars must be at least 2")
        self.max_bars = int(max_bars)
        self.horizons = tuple(
            sorted({int(value) for value in horizons if int(value) > 0})
        )
        self.group_weights = group_weights
        self.top_matches_per_horizon = max(1, int(top_matches_per_horizon))
        self.reset()

    def reset(self) -> None:
        self._bars: OrderedDict[int, LiveBarFingerprint] = OrderedDict()
        self._rows: OrderedDict[int, tuple[BarSimilarity, ...]] = OrderedDict()
        self._row_maps: OrderedDict[int, dict[int, BarSimilarity]] = OrderedDict()
        self._signatures: OrderedDict[int, np.ndarray] = OrderedDict()
        self._harmonics: OrderedDict[int, _HarmonicArrays] = OrderedDict()
        self._sequence_matches: dict[int, tuple[SequenceMatch, ...]] = {}

    @property
    def fingerprints(self) -> tuple[LiveBarFingerprint, ...]:
        return tuple(self._bars.values())

    @property
    def bar_ids(self) -> tuple[int, ...]:
        return tuple(self._bars)

    def add(self, fingerprint: LiveBarFingerprint) -> tuple[BarSimilarity, ...]:
        if fingerprint.bar_index in self._bars:
            return self._rows.get(fingerprint.bar_index, ())
        candidates = self._prefilter_candidates(fingerprint)
        harmonic = _prepare_harmonic(fingerprint)
        row = tuple(
            sorted(
                (
                    _compare_bars_prepared(
                        fingerprint,
                        earlier,
                        left_harmonic=harmonic,
                        right_harmonic=self._harmonics[earlier.bar_index],
                        group_weights=self.group_weights,
                    )
                    for earlier in candidates
                ),
                key=lambda item: (-item.combined, -item.reliability, item.right_bar),
            )
        )
        self._bars[fingerprint.bar_index] = fingerprint
        self._signatures[fingerprint.bar_index] = _search_signature(fingerprint)
        self._harmonics[fingerprint.bar_index] = harmonic
        self._rows[fingerprint.bar_index] = row[: min(self.max_bars, 32)]
        self._row_maps[fingerprint.bar_index] = {
            item.right_bar: item
            for item in self._rows[fingerprint.bar_index]
        }
        while len(self._bars) > self.max_bars:
            evicted, _ = self._bars.popitem(last=False)
            self._rows.pop(evicted, None)
            self._row_maps.pop(evicted, None)
            self._signatures.pop(evicted, None)
            self._harmonics.pop(evicted, None)
        while len(self._rows) > self.max_bars:
            evicted, _ = self._rows.popitem(last=False)
            self._row_maps.pop(evicted, None)
        self._refresh_sequence_matches()
        return self._rows[fingerprint.bar_index]

    def _prefilter_candidates(
        self,
        fingerprint: LiveBarFingerprint,
    ) -> tuple[LiveBarFingerprint, ...]:
        if not self._bars:
            return ()
        ids = tuple(self._bars)
        matrix = np.stack(tuple(self._signatures.values()))
        signature = _search_signature(fingerprint)
        distances = np.linalg.norm(matrix - signature[None, :], axis=1)
        # Exact-score the best content-addressed candidate plus every required
        # recurrence lag. The fixed horizon candidates preserve sequence
        # recall; keeping the generic prefilter to one avoids spending the
        # completed-bar budget on near-duplicate shortlist entries.
        selected = {int(np.argmin(distances))}
        for horizon in self.horizons:
            aligned = len(ids) - horizon
            if aligned >= 0:
                selected.add(aligned)
        return tuple(
            self._bars[ids[index]]
            for index in sorted(selected)
        )

    def similarities_for(self, bar_index: int) -> tuple[BarSimilarity, ...]:
        return self._rows.get(int(bar_index), ())

    def top_matches(
        self,
        *,
        horizon: int = 1,
        limit: int | None = None,
    ) -> tuple[BarSimilarity | SequenceMatch, ...]:
        count = self.top_matches_per_horizon if limit is None else max(0, int(limit))
        if horizon == 1:
            if not self._rows:
                return ()
            return tuple(next(reversed(self._rows.values()))[:count])
        return tuple(self._sequence_matches.get(int(horizon), ())[:count])

    def sequence_similarity(
        self,
        current: tuple[LiveBarFingerprint, ...],
        earlier: tuple[LiveBarFingerprint, ...],
    ) -> SequenceMatch | None:
        if not current or len(current) != len(earlier):
            return None
        return _compare_sequences(
            current,
            earlier,
            group_weights=self.group_weights,
        )

    def diagnostics(self) -> SimilarityDiagnostics:
        latest = next(reversed(self._bars), None) if self._bars else None
        bar_matches = (
            self._rows.get(latest, ())[:3] if latest is not None else ()
        )
        sequences = tuple(
            match
            for horizon in self.horizons
            if horizon > 1
            for match in self._sequence_matches.get(horizon, ())[:1]
        )
        return SimilarityDiagnostics(
            latest_bar=latest,
            top_bar_matches=bar_matches,
            top_sequence_matches=sequences,
            memory_bars=len(self._bars),
        )

    def _refresh_sequence_matches(self) -> None:
        bars = self.fingerprints
        self._sequence_matches.clear()
        if not bars:
            return
        positions = {
            fingerprint.bar_index: index
            for index, fingerprint in enumerate(bars)
        }
        latest_row = self._rows.get(bars[-1].bar_index, ())
        for horizon in self.horizons:
            if horizon <= 1 or len(bars) < horizon * 2:
                continue
            current = bars[-horizon:]
            candidates: list[SequenceMatch] = []
            latest_start = len(bars) - horizon
            candidate_starts: list[int] = []
            for match in latest_row[:3]:
                end = positions.get(match.right_bar)
                if end is None:
                    continue
                start = end - horizon + 1
                if 0 <= start and start + horizon <= latest_start:
                    candidate_starts.append(start)
            fallback = latest_start - horizon
            if fallback >= 0:
                candidate_starts.append(fallback)
            for start in dict.fromkeys(candidate_starts):
                earlier = bars[start : start + horizon]
                match = self._compare_cached_sequence(current, earlier)
                if match is not None:
                    candidates.append(match)
            self._sequence_matches[horizon] = tuple(
                sorted(
                    candidates,
                    key=lambda item: (-item.combined, -item.reliability),
                )[: self.top_matches_per_horizon]
            )

    def _compare_cached_sequence(
        self,
        current: tuple[LiveBarFingerprint, ...],
        earlier: tuple[LiveBarFingerprint, ...],
    ) -> SequenceMatch | None:
        pairs: list[BarSimilarity | None] = []
        missing = 0
        for left, right in zip(current, earlier):
            match = self._row_maps.get(left.bar_index, {}).get(
                right.bar_index
            )
            if match is None:
                missing += 1
                pairs.append(None)
                continue
            pairs.append(match)
        available = tuple(item for item in pairs if item is not None)
        if not available or missing > 1:
            return None
        shifts = Counter(item.best_pitch_shift for item in available)
        consistent_shift = min(
            (
                (-count, shift)
                for shift, count in shifts.items()
            )
        )[1]
        scores: list[float] = []
        for pair in pairs:
            if pair is None:
                scores.append(0.0)
                continue
            score = pair.combined
            if pair.best_pitch_shift != consistent_shift:
                transposed_weight = dict(pair.active_weights).get(
                    "harmonic_transposed",
                    0.0,
                )
                score -= transposed_weight * max(
                    0.0,
                    pair.harmonic_transposed - pair.harmonic_absolute,
                )
            scores.append(_clamp(score))
        substituted = int(missing > 0)
        retained = scores
        if len(scores) >= 4:
            weakest = min(range(len(scores)), key=scores.__getitem__)
            median_score = float(median(scores))
            retained = [
                score for index, score in enumerate(scores) if index != weakest
            ]
            substituted = max(
                substituted,
                int(
                    scores[weakest] < 0.58
                    or scores[weakest] < median_score - 0.10
                ),
            )
        combined = sum(retained) / len(retained) if retained else 0.0
        if substituted:
            combined *= 0.96
        return SequenceMatch(
            horizon=len(current),
            current_start_bar=current[0].bar_index,
            earlier_start_bar=earlier[0].bar_index,
            combined=_clamp(combined),
            reliability=_clamp(
                sum(item.reliability for item in available) / len(available)
                * (0.94 if substituted else 1.0)
            ),
            pitch_shift=consistent_shift,
            substituted_bars=substituted,
        )


def compare_bars(
    left: LiveBarFingerprint,
    right: LiveBarFingerprint,
    *,
    group_weights: tuple[tuple[str, float], ...] = DEFAULT_GROUP_WEIGHTS,
) -> BarSimilarity:
    return _compare_bars_prepared(
        left,
        right,
        left_harmonic=_prepare_harmonic(left),
        right_harmonic=_prepare_harmonic(right),
        group_weights=group_weights,
    )


def _compare_bars_prepared(
    left: LiveBarFingerprint,
    right: LiveBarFingerprint,
    *,
    left_harmonic: _HarmonicArrays,
    right_harmonic: _HarmonicArrays,
    group_weights: tuple[tuple[str, float], ...],
) -> BarSimilarity:
    shift_scores = _harmonic_shift_scores_prepared(
        left,
        right,
        left_harmonic,
        right_harmonic,
    )
    absolute = float(shift_scores[0])
    ranked_shifts = sorted(
        enumerate(shift_scores),
        key=lambda item: (-item[1], item[0]),
    )
    best_shift, transposed = ranked_shifts[0]
    runner_up = ranked_shifts[1][1] if len(ranked_shifts) > 1 else 0.0
    shift_gap = max(0.0, transposed - runner_up)
    timbre = _timbre_similarity(left, right)
    rhythm = _rhythm_similarity(left, right)
    dynamics = _dynamics_similarity(left, right)
    components = {
        "harmonic_absolute": absolute,
        "harmonic_transposed": transposed,
        "timbre": timbre,
        "rhythm": rhythm,
        "dynamics": dynamics,
    }
    active_weights = _active_weights(left, right, group_weights)
    combined = sum(
        components[name] * weight for name, weight in active_weights
    )
    meter_factor = (
        1.0
        if left.meter == right.meter and left.meter is not None
        else 0.82
        if left.meter is None or right.meter is None
        else 0.72
    )
    ambiguity_factor = 0.88 + (0.12 * min(1.0, shift_gap * 5.0))
    reliability = (
        min(left.reliability, right.reliability)
        * meter_factor
        * ambiguity_factor
    )
    return BarSimilarity(
        left_bar=left.bar_index,
        right_bar=right.bar_index,
        combined=_clamp(combined),
        harmonic_absolute=_clamp(absolute),
        harmonic_transposed=_clamp(transposed),
        best_pitch_shift=int(best_shift),
        timbre=_clamp(timbre),
        rhythm=_clamp(rhythm),
        dynamics=_clamp(dynamics),
        reliability=_clamp(reliability),
        pitch_shift_gap=_clamp(shift_gap),
        active_weights=active_weights,
    )


def _compare_sequences(
    current: tuple[LiveBarFingerprint, ...],
    earlier: tuple[LiveBarFingerprint, ...],
    *,
    group_weights: tuple[tuple[str, float], ...],
) -> SequenceMatch | None:
    if not current or len(current) != len(earlier):
        return None
    harmonic_rows = tuple(
        _harmonic_shift_scores(left, right)
        for left, right in zip(current, earlier)
    )
    sequence_shift_scores = np.mean(np.stack(harmonic_rows), axis=0)
    best_shift = int(np.argmax(sequence_shift_scores))
    pair_scores: list[float] = []
    reliabilities: list[float] = []
    for pair_index, (left, right) in enumerate(zip(current, earlier)):
        components = {
            "harmonic_absolute": float(harmonic_rows[pair_index][0]),
            "harmonic_transposed": float(
                harmonic_rows[pair_index][best_shift]
            ),
            "timbre": _timbre_similarity(left, right),
            "rhythm": _rhythm_similarity(left, right),
            "dynamics": _dynamics_similarity(left, right),
        }
        active = _active_weights(left, right, group_weights)
        pair_scores.append(
            sum(components[name] * weight for name, weight in active)
        )
        reliabilities.append(min(left.reliability, right.reliability))
    substituted = 0
    retained = pair_scores
    if len(pair_scores) >= 4:
        weakest = min(
            range(len(pair_scores)),
            key=pair_scores.__getitem__,
        )
        median_score = float(median(pair_scores))
        retained = [
            score for index, score in enumerate(pair_scores) if index != weakest
        ]
        substituted = int(
            pair_scores[weakest] < 0.58
            or pair_scores[weakest] < median_score - 0.10
        )
    score = sum(retained) / len(retained) if retained else 0.0
    if substituted:
        score *= 0.96
    return SequenceMatch(
        horizon=len(current),
        current_start_bar=current[0].bar_index,
        earlier_start_bar=earlier[0].bar_index,
        combined=_clamp(score),
        reliability=_clamp(
            (sum(reliabilities) / len(reliabilities))
            * (0.94 if substituted else 1.0)
        ),
        pitch_shift=int(best_shift),
        substituted_bars=substituted,
    )


def _harmonic_similarity(
    left: LiveBarFingerprint,
    right: LiveBarFingerprint,
    *,
    shift: int,
) -> float:
    return float(_harmonic_shift_scores(left, right)[shift % 12])


def _harmonic_shift_scores(
    left: LiveBarFingerprint,
    right: LiveBarFingerprint,
) -> np.ndarray:
    """Return all 12 consistent pitch shifts with one vectorized comparison."""

    return _harmonic_shift_scores_prepared(
        left,
        right,
        _prepare_harmonic(left),
        _prepare_harmonic(right),
    )


def _prepare_harmonic(
    fingerprint: LiveBarFingerprint,
) -> _HarmonicArrays:
    profile = np.asarray(fingerprint.chroma_profile, dtype=np.float64)
    beats = np.asarray(fingerprint.beat_chroma, dtype=np.float64)
    if beats.size == 0:
        beats = np.zeros((0, 12), dtype=np.float64)
    return _HarmonicArrays(
        profile=profile,
        shifted_profiles=profile[_ROLL_INDICES],
        profile_norm=float(np.linalg.norm(profile)),
        beats=beats,
        shifted_beats=np.transpose(
            beats[:, _ROLL_INDICES],
            (1, 0, 2),
        ),
        beat_norms=np.linalg.norm(beats, axis=1),
    )


def _harmonic_shift_scores_prepared(
    left: LiveBarFingerprint,
    right: LiveBarFingerprint,
    left_arrays: _HarmonicArrays,
    right_arrays: _HarmonicArrays,
) -> np.ndarray:
    profile_denominator = (
        left_arrays.profile_norm * right_arrays.profile_norm
    )
    profile_scores = np.divide(
        right_arrays.shifted_profiles @ left_arrays.profile,
        profile_denominator,
        out=np.zeros(12, dtype=np.float64),
        where=np.full(12, profile_denominator > 1e-12),
    )
    size = max(len(left.beat_chroma), len(right.beat_chroma), 1)
    if len(left_arrays.beats) == size:
        left_beats = left_arrays.beats
        left_norms = left_arrays.beat_norms
    else:
        left_beats = _resample_matrix(left.beat_chroma, size, width=12)
        left_norms = np.linalg.norm(left_beats, axis=1)
    if len(right_arrays.beats) == size:
        shifted_beats = right_arrays.shifted_beats
        right_norms = right_arrays.beat_norms
    else:
        right_beats = _resample_matrix(right.beat_chroma, size, width=12)
        shifted_beats = np.transpose(
            right_beats[:, _ROLL_INDICES],
            (1, 0, 2),
        )
        right_norms = np.linalg.norm(right_beats, axis=1)
    numerators = np.einsum("hij,ij->hi", shifted_beats, left_beats)
    denominators = left_norms[None, :] * right_norms[None, :]
    beat_scores = np.divide(
        numerators,
        denominators,
        out=np.zeros_like(numerators),
        where=denominators > 1e-12,
    ).mean(axis=1)
    delta = _shape_similarity(
        left.chroma_delta_shape,
        right.chroma_delta_shape,
    )
    return np.clip(
        (0.35 * profile_scores) + (0.55 * beat_scores) + (0.10 * delta),
        0.0,
        1.0,
    )


def _harmonic_similarity_legacy(
    left: LiveBarFingerprint,
    right: LiveBarFingerprint,
    *,
    shift: int,
) -> float:
    profile = _cosine01(
        np.asarray(left.chroma_profile),
        np.roll(np.asarray(right.chroma_profile), shift),
    )
    size = max(len(left.beat_chroma), len(right.beat_chroma), 1)
    left_beats = _resample_matrix(left.beat_chroma, size, width=12)
    right_beats = np.roll(
        _resample_matrix(right.beat_chroma, size, width=12),
        shift,
        axis=1,
    )
    beat_score = float(
        np.mean(
            [
                _cosine01(left_beats[index], right_beats[index])
                for index in range(size)
            ]
        )
    )
    delta = _shape_similarity(
        left.chroma_delta_shape,
        right.chroma_delta_shape,
    )
    return (0.35 * profile) + (0.55 * beat_score) + (0.10 * delta)


def _timbre_similarity(
    left: LiveBarFingerprint,
    right: LiveBarFingerprint,
) -> float:
    return float(
        (0.60 * _vector_similarity(left.mfcc_mean, right.mfcc_mean))
        + (0.15 * _vector_similarity(left.mfcc_std, right.mfcc_std))
        + (0.25 * _vector_similarity(left.band_profile, right.band_profile))
    )


def _rhythm_similarity(
    left: LiveBarFingerprint,
    right: LiveBarFingerprint,
) -> float:
    return (
        _shape_similarity(left.onset_shape, right.onset_shape)
        + _shape_similarity(left.band_flux_shape, right.band_flux_shape)
        + _shape_similarity(left.bass_shape, right.bass_shape)
        + _shape_similarity(
            left.chroma_delta_shape,
            right.chroma_delta_shape,
        )
    ) / 4.0


def _dynamics_similarity(
    left: LiveBarFingerprint,
    right: LiveBarFingerprint,
) -> float:
    return (
        _shape_similarity(left.energy_shape, right.energy_shape)
        + _shape_similarity(left.bass_shape, right.bass_shape)
        + _shape_similarity(left.centroid_shape, right.centroid_shape)
        + _slope_similarity(left.energy_shape, right.energy_shape)
    ) / 4.0


def _active_weights(
    left: LiveBarFingerprint,
    right: LiveBarFingerprint,
    weights: tuple[tuple[str, float], ...],
) -> tuple[tuple[str, float], ...]:
    harmonic_reliability = min(left.chroma_confidence, right.chroma_confidence)
    factors = {
        "harmonic_absolute": harmonic_reliability,
        "harmonic_transposed": harmonic_reliability,
        "timbre": float(bool(left.mfcc_mean and right.mfcc_mean)),
        "rhythm": float(bool(left.onset_shape and right.onset_shape)),
        "dynamics": float(bool(left.energy_shape and right.energy_shape)),
    }
    active = [
        (name, max(0.0, float(weight)) * factors.get(name, 0.0))
        for name, weight in weights
    ]
    total = sum(weight for _, weight in active)
    if total <= 1e-12:
        return (("dynamics", 1.0),)
    return tuple((name, weight / total) for name, weight in active if weight > 0.0)


def _resample_matrix(
    values: tuple[tuple[float, ...], ...],
    size: int,
    *,
    width: int,
) -> np.ndarray:
    if not values:
        return np.zeros((size, width), dtype=np.float64)
    matrix = np.asarray(values, dtype=np.float64)
    if len(matrix) == size:
        return matrix
    old = np.linspace(0.0, 1.0, len(matrix))
    new = np.linspace(0.0, 1.0, size)
    return np.stack(
        [np.interp(new, old, matrix[:, column]) for column in range(width)],
        axis=1,
    )


def _shape_similarity(
    left: tuple[float, ...],
    right: tuple[float, ...],
) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.5
    if len(left) == len(right):
        scale = max(
            1.0,
            max(abs(value) for value in left),
            max(abs(value) for value in right),
        )
        distance = sum(
            abs(left_value - right_value)
            for left_value, right_value in zip(left, right)
        ) / len(left)
        return _clamp(1.0 - distance / (2.0 * scale))
    size = max(len(left), len(right))
    a = _resample_vector(left, size)
    b = _resample_vector(right, size)
    scale = max(1.0, float(np.max(np.abs(np.concatenate((a, b))))))
    return _clamp(1.0 - float(np.mean(np.abs(a - b))) / (2.0 * scale))


def _slope_similarity(
    left: tuple[float, ...],
    right: tuple[float, ...],
) -> float:
    if len(left) < 2 or len(right) < 2:
        return 0.75
    left_slope = float(left[-1] - left[0])
    right_slope = float(right[-1] - right[0])
    return _clamp(1.0 - min(1.0, abs(left_slope - right_slope) / 2.0))


def _resample_vector(values: tuple[float, ...], size: int) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64)
    if len(array) == size:
        return array
    return np.interp(
        np.linspace(0.0, 1.0, size),
        np.linspace(0.0, 1.0, len(array)),
        array,
    )


def _vector_similarity(
    left: tuple[float, ...],
    right: tuple[float, ...],
) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.5
    size = min(len(left), len(right))
    left_values = left[:size]
    right_values = right[:size]
    denominator = max(
        1.0,
        sqrt(sum(value * value for value in left_values))
        + sqrt(sum(value * value for value in right_values)),
    )
    distance = sqrt(
        sum(
            (left_value - right_value) ** 2
            for left_value, right_value in zip(left_values, right_values)
        )
    )
    return _clamp(1.0 - distance / denominator)


def _cosine01(left: np.ndarray, right: np.ndarray) -> float:
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    if denominator <= 1e-12:
        return 1.0 if np.allclose(left, right) else 0.0
    return _clamp(float(np.dot(left, right) / denominator))


def _search_signature(fingerprint: LiveBarFingerprint) -> np.ndarray:
    """Cheap fixed-width signature used only to shortlist exact comparisons."""

    chroma = np.sort(np.asarray(fingerprint.chroma_profile, dtype=np.float64))
    mfcc = np.tanh(np.asarray(fingerprint.mfcc_mean, dtype=np.float64) / 3.0)
    bands = np.zeros(8, dtype=np.float64)
    band_values = np.asarray(fingerprint.band_profile[:8], dtype=np.float64)
    bands[: len(band_values)] = band_values
    groups = (
        chroma,
        mfcc,
        bands,
        _resample_vector(fingerprint.chroma_delta_shape or (0.0,), 4),
        _resample_vector(fingerprint.onset_shape or (0.0,), 4),
        _resample_vector(fingerprint.energy_shape or (0.0,), 4),
        _resample_vector(fingerprint.bass_shape or (0.0,), 4),
        _resample_vector(fingerprint.centroid_shape or (0.0,), 4),
    )
    return np.concatenate(groups)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
