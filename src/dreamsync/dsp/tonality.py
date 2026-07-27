"""Causal probabilistic key-center tracking for reactive live prediction."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from dreamsync.prediction.models import KeyHypothesis


PITCH_NAMES = (
    "C",
    "C#",
    "D",
    "D#",
    "E",
    "F",
    "F#",
    "G",
    "G#",
    "A",
    "A#",
    "B",
)
MAJOR_PROFILE = np.asarray(
    (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88),
    dtype=np.float64,
)
MINOR_PROFILE = np.asarray(
    (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17),
    dtype=np.float64,
)


@dataclass(frozen=True)
class KeyTrackerConfig:
    top_k: int = 3
    smoothing: float = 0.22
    chord_weight: float = 0.35
    transition_penalty: float = 0.35
    minimum_residence_beats: int = 8
    change_margin: float = 0.08


def parse_chord(chord: str | None) -> tuple[int, str] | None:
    label = str(chord or "").strip()
    for name in sorted(PITCH_NAMES, key=len, reverse=True):
        if label.startswith(name):
            suffix = label[len(name) :]
            if suffix in {"", "maj"}:
                return PITCH_NAMES.index(name), "major"
            if suffix in {"m", "min"}:
                return PITCH_NAMES.index(name), "minor"
    return None


class CausalKeyTracker:
    """A deterministic 24-state key filter with residence hysteresis."""

    def __init__(self, config: KeyTrackerConfig | None = None) -> None:
        self.config = config or KeyTrackerConfig()
        if not 1 <= self.config.top_k <= 24:
            raise ValueError("top_k must be between 1 and 24")
        self._profiles = self._make_profiles()
        self.reset()

    def reset(self) -> None:
        self._scores = np.zeros(24, dtype=np.float64)
        self._leader: int | None = None
        self._leader_age = 0
        self._ages = np.zeros(24, dtype=np.int64)
        self._last = ()

    @property
    def hypotheses(self) -> tuple[KeyHypothesis, ...]:
        return self._last

    def observe(
        self,
        *,
        chroma: tuple[float, ...],
        chord: str | None,
        chroma_confidence: float,
        chord_confidence: float,
        duration_beats: float = 1.0,
    ) -> tuple[KeyHypothesis, ...]:
        values = np.maximum(0.0, np.asarray(chroma, dtype=np.float64))
        if values.shape != (12,):
            raise ValueError("chroma must contain 12 pitch classes")
        total = float(values.sum())
        chroma_weight = _clamp01(chroma_confidence)
        chord_weight = _clamp01(chord_confidence) * self.config.chord_weight
        parsed = parse_chord(chord)
        if total <= 1e-12 and parsed is None:
            self._age_leader()
            return self._last
        if total > 0.0:
            values /= total

        chroma_scores = self._profiles @ values if total > 0.0 else np.zeros(24)
        chord_scores = np.zeros(24, dtype=np.float64)
        if parsed is not None:
            root, quality = parsed
            for index in range(24):
                tonic = index % 12
                mode = "major" if index < 12 else "minor"
                chord_scores[index] = _chord_key_compatibility(
                    root, quality, tonic, mode
                )
        evidence = (chroma_weight * chroma_scores) + (chord_weight * chord_scores)
        alpha = 1.0 - ((1.0 - self.config.smoothing) ** max(0.25, duration_beats))
        self._scores = ((1.0 - alpha) * self._scores) + (alpha * evidence)

        proposed = int(np.argmax(self._scores))
        if self._leader is None:
            self._leader = proposed
            self._leader_age = 1
        elif proposed == self._leader:
            self._leader_age += 1
        else:
            margin = float(self._scores[proposed] - self._scores[self._leader])
            required_age = self.config.minimum_residence_beats
            if self._leader_age >= required_age and margin >= self.config.change_margin:
                self._leader = proposed
                self._leader_age = 1
            else:
                self._scores[self._leader] += self.config.transition_penalty
                self._leader_age += 1
        self._ages += 1
        if self._leader is not None:
            self._ages[self._leader] = self._leader_age

        probabilities = _softmax(self._scores * 5.0)
        ranked = np.argsort(probabilities)[::-1][: self.config.top_k]
        hypotheses: list[KeyHypothesis] = []
        for index in ranked:
            index = int(index)
            tonic = index % 12
            mode = "major" if index < 12 else "minor"
            hypotheses.append(
                KeyHypothesis(
                    tonic_pc=tonic,
                    mode=mode,
                    probability=float(probabilities[index]),
                    stability=min(
                        1.0,
                        float(self._ages[index])
                        / max(1, self.config.minimum_residence_beats),
                    ),
                    age_beats=int(self._ages[index]),
                    chroma_evidence=float(chroma_scores[index] * chroma_weight),
                    chord_evidence=float(chord_scores[index] * chord_weight),
                    transition_evidence=(
                        0.0
                        if index == self._leader
                        else -self.config.transition_penalty
                    ),
                )
            )
        retained_total = sum(item.probability for item in hypotheses) or 1.0
        self._last = tuple(
            KeyHypothesis(
                **{
                    **hypothesis.__dict__,
                    "probability": hypothesis.probability / retained_total,
                }
            )
            for hypothesis in hypotheses
        )
        return self._last

    def _age_leader(self) -> None:
        if self._leader is not None:
            self._leader_age += 1
            self._ages += 1
            self._ages[self._leader] = self._leader_age

    @staticmethod
    def _make_profiles() -> np.ndarray:
        rows = []
        for profile in (MAJOR_PROFILE, MINOR_PROFILE):
            normalized = profile / float(profile.sum())
            for tonic in range(12):
                rows.append(np.roll(normalized, tonic))
        return np.asarray(rows)


def _chord_key_compatibility(
    root: int, quality: str, tonic: int, mode: str
) -> float:
    interval = (root - tonic) % 12
    if mode == "major":
        qualities = {
            0: "major",
            2: "minor",
            4: "minor",
            5: "major",
            7: "major",
            9: "minor",
        }
    else:
        qualities = {
            0: "minor",
            2: "minor",
            3: "major",
            5: "minor",
            7: "major",
            8: "major",
            10: "major",
        }
    if interval not in qualities:
        return -0.25
    return 1.0 if qualities[interval] == quality else 0.25


def _softmax(values: np.ndarray) -> np.ndarray:
    shifted = values - float(np.max(values))
    weights = np.exp(np.clip(shifted, -50.0, 50.0))
    return weights / (float(weights.sum()) or 1.0)


def _clamp01(value: float) -> float:
    if not math.isfinite(float(value)):
        return 0.0
    return max(0.0, min(1.0, float(value)))
