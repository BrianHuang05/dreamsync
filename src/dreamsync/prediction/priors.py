"""Small inspectable musical priors used before song-local evidence is strong.

Version/source: ``dreamsync-hand-authored-v1``.  Values are deliberately
conservative product priors, not claims about a universal music corpus.
"""

from __future__ import annotations

from dataclasses import dataclass


PRIOR_VERSION = "dreamsync-hand-authored-v1"
PRIOR_SOURCE = "DreamSync implementation plan; hand-authored, 2026-07"

PHRASE_LENGTHS_BY_METER: dict[tuple[int, int] | None, dict[int | None, float]] = {
    (4, 4): {2: 0.08, 4: 0.34, 8: 0.29, 12: 0.10, 16: 0.13, None: 0.06},
    (3, 4): {2: 0.10, 4: 0.31, 8: 0.27, 12: 0.12, 16: 0.10, None: 0.10},
    None: {2: 0.08, 4: 0.25, 8: 0.23, 12: 0.10, 16: 0.12, None: 0.22},
}

PROGRESSION_PRIORS: dict[tuple[str, ...], dict[str, float]] = {
    ("vi", "IV", "V"): {"I": 0.62, "vi": 0.16, "V": 0.12, "unknown": 0.10},
    ("ii", "V"): {"I": 0.72, "vi": 0.12, "V": 0.08, "unknown": 0.08},
    ("V",): {"I": 0.55, "vi": 0.20, "V": 0.15, "unknown": 0.10},
    ("IV",): {"I": 0.35, "V": 0.30, "vi": 0.15, "unknown": 0.20},
    ("I", "V", "vi"): {"IV": 0.58, "I": 0.16, "V": 0.14, "unknown": 0.12},
}

CADENCE_PRIORS: dict[tuple[str, ...], dict[str, float]] = {
    ("V", "I"): {"authentic": 0.82, "continuation": 0.12, "unknown": 0.06},
    ("IV", "I"): {"plagal": 0.58, "continuation": 0.26, "unknown": 0.16},
    ("ii", "V", "I"): {"authentic": 0.88, "continuation": 0.08, "unknown": 0.04},
    ("V", "vi"): {"deceptive": 0.76, "continuation": 0.16, "unknown": 0.08},
}

SECTION_TRANSITIONS: dict[str, dict[str, float]] = {
    "A": {"B": 0.52, "A": 0.20, "C": 0.10, "unknown": 0.18},
    "B": {"A": 0.42, "B": 0.18, "C": 0.22, "unknown": 0.18},
    "C": {"B": 0.42, "A": 0.25, "C": 0.10, "unknown": 0.23},
}


@dataclass(frozen=True)
class PriorDistribution:
    values: tuple[tuple[str, float], ...]
    version: str = PRIOR_VERSION
    source: str = PRIOR_SOURCE

    def as_dict(self) -> dict[str, float]:
        return dict(self.values)


def phrase_length_distribution(
    meter: tuple[int, int] | None,
) -> dict[int | None, float]:
    return dict(PHRASE_LENGTHS_BY_METER.get(meter, PHRASE_LENGTHS_BY_METER[None]))


def next_function_distribution(context: tuple[str, ...]) -> PriorDistribution:
    for size in range(min(3, len(context)), 0, -1):
        suffix = context[-size:]
        if suffix in PROGRESSION_PRIORS:
            return PriorDistribution(tuple(PROGRESSION_PRIORS[suffix].items()))
    return PriorDistribution((("unknown", 1.0),))


def cadence_distribution(context: tuple[str, ...]) -> PriorDistribution:
    for size in range(min(3, len(context)), 1, -1):
        suffix = context[-size:]
        if suffix in CADENCE_PRIORS:
            return PriorDistribution(tuple(CADENCE_PRIORS[suffix].items()))
    return PriorDistribution((("continuation", 0.55), ("unknown", 0.45)))
