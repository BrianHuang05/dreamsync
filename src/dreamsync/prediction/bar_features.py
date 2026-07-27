"""Beat-aligned construction of bounded, multi-feature bar fingerprints."""

from __future__ import annotations

import math
from collections import Counter, deque

import numpy as np

from .structure_models import LiveBarFingerprint, LiveBeatStructureObservation


class LiveBarFingerprintBuilder:
    """Close the preceding bar exactly once when a confident downbeat arrives."""

    def __init__(
        self,
        *,
        beats_per_bar: int = 4,
        minimum_meter_confidence: float = 0.22,
        normalization_history_bars: int = 64,
        nyquist_hz: float = 22050.0,
    ) -> None:
        if beats_per_bar < 1:
            raise ValueError("beats_per_bar must be positive")
        if not 0.0 <= minimum_meter_confidence <= 1.0:
            raise ValueError("minimum_meter_confidence must be between 0 and 1")
        self.beats_per_bar = int(beats_per_bar)
        self.minimum_meter_confidence = float(minimum_meter_confidence)
        self.normalization_history_bars = max(4, int(normalization_history_bars))
        self.nyquist_hz = max(1.0, float(nyquist_hz))
        self.reset()

    def reset(self) -> None:
        self._current_bar: int | None = None
        self._observations: list[LiveBeatStructureObservation] = []
        self._last_beat_index = -1
        self._last_closed_bar: int | None = None
        self._mfcc_history: deque[tuple[float, ...]] = deque(
            maxlen=self.normalization_history_bars
        )
        self._flux_history: deque[float] = deque(
            maxlen=self.normalization_history_bars * self.beats_per_bar
        )

    @property
    def current_bar(self) -> int | None:
        return self._current_bar

    @property
    def pending_beats(self) -> int:
        return len(self._observations)

    def observe(
        self,
        observation: LiveBeatStructureObservation,
    ) -> LiveBarFingerprint | None:
        """Consume one new beat and optionally publish the preceding bar."""

        if observation.beat_index <= self._last_beat_index:
            return None
        self._last_beat_index = observation.beat_index
        completed: LiveBarFingerprint | None = None
        confident_downbeat = (
            observation.downbeat
            and observation.bar_index is not None
            and observation.meter_confidence >= self.minimum_meter_confidence
        )
        if confident_downbeat:
            if (
                self._current_bar is not None
                and observation.bar_index != self._current_bar
                and self._current_bar != self._last_closed_bar
                and self._observations
            ):
                completed = self._close(end_t=observation.t)
                self._last_closed_bar = self._current_bar
            if observation.bar_index != self._current_bar:
                self._current_bar = observation.bar_index
                self._observations = []
        elif self._current_bar is None and observation.bar_index is not None:
            self._current_bar = observation.bar_index

        if self._current_bar is not None:
            self._observations.append(observation)
        return completed

    def _close(self, *, end_t: float) -> LiveBarFingerprint:
        rows = tuple(self._observations)
        expected_beats = _expected_beats(rows, self.beats_per_bar)
        ordered = _unique_beat_rows(rows)
        observed_positions = {
            row.beat_in_bar for row in ordered if row.beat_in_bar is not None
        }
        observed_count = len(observed_positions) or len(ordered)
        completeness = min(1.0, observed_count / max(1, expected_beats))
        meter_confidence = float(np.mean([row.meter_confidence for row in ordered]))

        beat_chroma = tuple(_l1(row.chroma) for row in ordered)
        chroma_profile = _l1(tuple(np.mean(np.asarray(beat_chroma), axis=0)))
        chroma_deltas = tuple(
            1.0 - _cosine(left, right)
            for left, right in zip(beat_chroma, beat_chroma[1:])
        )
        chroma_confidence = float(
            np.mean([row.chroma_confidence for row in ordered])
        )

        raw_mfcc = np.asarray([row.mfcc for row in ordered], dtype=np.float64)
        standardized_mfcc = self._standardize_mfcc(raw_mfcc)
        mfcc_mean = tuple(float(value) for value in np.mean(standardized_mfcc, axis=0))
        mfcc_std = tuple(float(value) for value in np.std(standardized_mfcc, axis=0))
        self._mfcc_history.append(
            tuple(float(value) for value in np.mean(raw_mfcc, axis=0))
        )

        bands = np.asarray([row.band_ratios for row in ordered], dtype=np.float64)
        band_profile = (
            _l1(tuple(float(value) for value in np.mean(bands, axis=0)))
            if bands.shape[1]
            else ()
        )
        raw_flux = np.asarray(
            [
                math.log1p(max(0.0, float(np.mean(row.band_fluxes))))
                if row.band_fluxes
                else 0.0
                for row in ordered
            ],
            dtype=np.float64,
        )
        band_flux_shape = tuple(float(value) for value in self._normalize_flux(raw_flux))
        self._flux_history.extend(float(value) for value in raw_flux)

        onset_shape = _positive_shape(row.onset_strength for row in ordered)
        energy_shape = _relative_shape(
            (row.energy for row in ordered),
            logarithmic=True,
        )
        bass_shape = _positive_shape(row.bass_ratio for row in ordered)
        centroid_shape = _relative_shape(
            (row.spectral_centroid / self.nyquist_hz for row in ordered),
            logarithmic=False,
        )
        labels = Counter(
            row.chord_label for row in ordered if row.chord_label is not None
        )
        tonal_label = labels.most_common(1)[0][0] if labels else None
        meter = next((row.meter for row in ordered if row.meter is not None), None)
        return LiveBarFingerprint(
            bar_index=int(self._current_bar or 0),
            start_t=float(ordered[0].t),
            end_t=float(end_t),
            beats=expected_beats,
            meter=meter,
            meter_confidence=max(0.0, min(1.0, meter_confidence)),
            completeness=completeness,
            beat_chroma=beat_chroma,
            chroma_profile=chroma_profile,
            chroma_delta_shape=chroma_deltas,
            chroma_confidence=max(0.0, min(1.0, chroma_confidence)),
            mfcc_mean=mfcc_mean,
            mfcc_std=mfcc_std,
            band_profile=band_profile,
            band_flux_shape=band_flux_shape,
            onset_shape=onset_shape,
            energy_shape=energy_shape,
            bass_shape=bass_shape,
            centroid_shape=centroid_shape,
            tonal_label=tonal_label,
        )

    def _standardize_mfcc(self, values: np.ndarray) -> np.ndarray:
        if self._mfcc_history:
            history = np.asarray(self._mfcc_history, dtype=np.float64)
            median = np.median(history, axis=0)
            mad = np.median(np.abs(history - median), axis=0)
            scale = np.maximum(1e-3, 1.4826 * mad)
        else:
            median = np.zeros(values.shape[1], dtype=np.float64)
            scale = np.maximum(1.0, np.abs(np.median(values, axis=0)))
        return np.clip((values - median) / scale, -6.0, 6.0)

    def _normalize_flux(self, values: np.ndarray) -> np.ndarray:
        if self._flux_history:
            history = np.asarray(self._flux_history, dtype=np.float64)
            median = float(np.median(history))
            mad = float(np.median(np.abs(history - median)))
            scale = max(1e-4, 1.4826 * mad)
            return np.clip((values - median) / scale, -4.0, 4.0)
        maximum = max(1e-8, float(np.max(values)))
        return values / maximum


def _expected_beats(
    rows: tuple[LiveBeatStructureObservation, ...],
    fallback: int,
) -> int:
    for row in rows:
        if row.meter is not None:
            return max(1, int(row.meter[0]))
    return max(1, int(fallback))


def _unique_beat_rows(
    rows: tuple[LiveBeatStructureObservation, ...],
) -> tuple[LiveBeatStructureObservation, ...]:
    by_position: dict[int, LiveBeatStructureObservation] = {}
    unpositioned: list[LiveBeatStructureObservation] = []
    for row in rows:
        if row.beat_in_bar is None:
            unpositioned.append(row)
        else:
            by_position.setdefault(int(row.beat_in_bar), row)
    positioned = tuple(by_position[key] for key in sorted(by_position))
    return positioned + tuple(unpositioned)


def _l1(values: tuple[float, ...]) -> tuple[float, ...]:
    array = np.maximum(0.0, np.asarray(values, dtype=np.float64))
    total = float(array.sum())
    if total <= 1e-12:
        return tuple(0.0 for _ in values)
    return tuple(float(value) for value in array / total)


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    a = np.asarray(left, dtype=np.float64)
    b = np.asarray(right, dtype=np.float64)
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denominator <= 1e-12:
        return 0.0
    return max(0.0, min(1.0, float(np.dot(a, b) / denominator)))


def _positive_shape(values: object) -> tuple[float, ...]:
    array = np.log1p(
        np.maximum(0.0, np.fromiter((float(value) for value in values), dtype=np.float64))
    )
    maximum = float(np.max(array)) if array.size else 0.0
    if maximum <= 1e-12:
        return tuple(0.0 for _ in array)
    return tuple(float(value) for value in array / maximum)


def _relative_shape(
    values: object,
    *,
    logarithmic: bool,
) -> tuple[float, ...]:
    array = np.fromiter((max(0.0, float(value)) for value in values), dtype=np.float64)
    if logarithmic:
        array = np.log(np.maximum(1e-8, array))
    if not array.size:
        return ()
    centered = array - float(np.mean(array))
    scale = max(1e-8, float(np.max(np.abs(centered))))
    return tuple(float(value) for value in centered / scale)
