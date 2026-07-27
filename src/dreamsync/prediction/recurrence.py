"""Bounded phrase fingerprints and partial recurrence matching."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

from .models import PhraseFingerprint
from .similarity import MultiFeatureSimilarityMemory
from .structure_models import (
    CompactBarDescriptor,
    LiveBarFingerprint,
    StructurePhraseFingerprint,
)


@dataclass(frozen=True)
class PhraseMatch:
    memory_index: int
    score: float
    matched_tokens: int
    continuation: str | None
    fingerprint: PhraseFingerprint


class PhraseRecurrenceMemory:
    def __init__(self, max_phrases: int = 64) -> None:
        if max_phrases < 1:
            raise ValueError("max_phrases must be positive")
        self.max_phrases = int(max_phrases)
        self._phrases: deque[PhraseFingerprint] = deque(maxlen=self.max_phrases)

    def reset(self) -> None:
        self._phrases.clear()

    @property
    def phrases(self) -> tuple[PhraseFingerprint, ...]:
        return tuple(self._phrases)

    def remember(self, fingerprint: PhraseFingerprint) -> None:
        self._phrases.append(fingerprint)

    def match_partial(
        self,
        numerals: tuple[str, ...],
        *,
        duration_pattern: tuple[int, ...] = (),
        energy_shape: tuple[int, ...] = (),
        maximum_substitutions: int = 1,
    ) -> tuple[PhraseMatch, ...]:
        if not numerals:
            return ()
        matches: list[PhraseMatch] = []
        for index, fingerprint in enumerate(self._phrases):
            comparable = min(len(numerals), len(fingerprint.numerals))
            if comparable == 0:
                continue
            substitutions = sum(
                left != right
                for left, right in zip(
                    numerals[-comparable:],
                    fingerprint.numerals[:comparable],
                )
            )
            if substitutions > maximum_substitutions:
                continue
            harmonic = 1.0 - (substitutions / comparable)
            duration = _shape_similarity(
                duration_pattern[-comparable:],
                fingerprint.chord_duration_pattern[:comparable],
            )
            energy = _shape_similarity(
                energy_shape,
                fingerprint.energy_shape[: len(energy_shape)],
            )
            completion = comparable / max(1, len(fingerprint.numerals))
            score = (
                (0.62 * harmonic)
                + (0.18 * duration)
                + (0.08 * energy)
                + (0.12 * completion)
            )
            continuation = (
                fingerprint.numerals[comparable]
                if comparable < len(fingerprint.numerals)
                else None
            )
            matches.append(
                PhraseMatch(
                    memory_index=index,
                    score=max(0.0, min(1.0, score)),
                    matched_tokens=comparable,
                    continuation=continuation,
                    fingerprint=fingerprint,
                )
            )
        return tuple(sorted(matches, key=lambda item: item.score, reverse=True))


def _shape_similarity(left: tuple[int, ...], right: tuple[int, ...]) -> float:
    if not left or not right:
        return 0.5
    size = min(len(left), len(right))
    difference = sum(
        min(4, abs(a - b)) / 4.0
        for a, b in zip(left[:size], right[:size])
    ) / size
    return 1.0 - difference


@dataclass(frozen=True)
class StructurePhraseMatch:
    memory_index: int
    score: float
    matched_bars: int
    pitch_shift: int
    fingerprint: StructurePhraseFingerprint


class StructurePhraseRecurrenceMemory:
    """Bounded phrase recurrence whose primary identity is multi-feature audio."""

    def __init__(self, max_phrases: int = 64) -> None:
        if max_phrases < 1:
            raise ValueError("max_phrases must be positive")
        self.max_phrases = int(max_phrases)
        self._phrases: deque[
            tuple[StructurePhraseFingerprint, tuple[LiveBarFingerprint, ...]]
        ] = deque(maxlen=self.max_phrases)
        self._similarity = MultiFeatureSimilarityMemory(max_bars=256)

    def reset(self) -> None:
        self._phrases.clear()

    @property
    def phrases(self) -> tuple[StructurePhraseFingerprint, ...]:
        return tuple(item[0] for item in self._phrases)

    def remember(
        self,
        bars: tuple[LiveBarFingerprint, ...],
    ) -> StructurePhraseFingerprint:
        if not bars:
            raise ValueError("a structure phrase must contain at least one bar")
        fingerprint = structure_phrase_fingerprint(bars)
        self._phrases.append((fingerprint, bars))
        return fingerprint

    def match_prefix(
        self,
        bars: tuple[LiveBarFingerprint, ...],
    ) -> tuple[StructurePhraseMatch, ...]:
        if not bars:
            return ()
        matches: list[StructurePhraseMatch] = []
        for index, (fingerprint, remembered) in enumerate(self._phrases):
            comparable = min(len(bars), len(remembered))
            result = self._similarity.sequence_similarity(
                bars[-comparable:],
                remembered[:comparable],
            )
            if result is None:
                continue
            completion = comparable / max(1, len(remembered))
            score = result.combined * (0.55 + (0.45 * completion))
            matches.append(
                StructurePhraseMatch(
                    memory_index=index,
                    score=max(0.0, min(1.0, score)),
                    matched_bars=comparable,
                    pitch_shift=result.pitch_shift,
                    fingerprint=fingerprint,
                )
            )
        return tuple(
            sorted(matches, key=lambda item: (-item.score, item.memory_index))
        )


def structure_phrase_fingerprint(
    bars: tuple[LiveBarFingerprint, ...],
) -> StructurePhraseFingerprint:
    if not bars:
        raise ValueError("bars must not be empty")
    descriptors = tuple(_compact_descriptor(bar) for bar in bars)
    signatures = np.asarray(
        [descriptor.signature for descriptor in descriptors],
        dtype=np.float64,
    )
    combined = tuple(float(value) for value in np.mean(signatures, axis=0))
    return StructurePhraseFingerprint(
        bars=len(bars),
        bar_descriptors=descriptors,
        combined_signature=combined,
        entrance_signature=descriptors[0].signature,
        exit_signature=descriptors[-1].signature,
        harmonic_shift_tolerance=True,
        energy_shape=tuple(
            int(round(4.0 * float(np.mean(bar.energy_shape))))
            for bar in bars
        ),
        onset_shape=tuple(
            int(round(4.0 * float(np.mean(bar.onset_shape))))
            for bar in bars
        ),
        tonal_diagnostics=tuple(
            bar.tonal_label for bar in bars if bar.tonal_label is not None
        ),
    )


def _compact_descriptor(bar: LiveBarFingerprint) -> CompactBarDescriptor:
    signature = (
        tuple(bar.chroma_profile)
        + tuple(bar.mfcc_mean[:6])
        + tuple(bar.band_profile)
        + (
            float(np.mean(bar.onset_shape)) if bar.onset_shape else 0.0,
            float(np.mean(bar.energy_shape)) if bar.energy_shape else 0.0,
            float(np.mean(bar.bass_shape)) if bar.bass_shape else 0.0,
        )
    )
    return CompactBarDescriptor(
        bar_index=bar.bar_index,
        signature=signature,
        reliability=bar.reliability,
    )
