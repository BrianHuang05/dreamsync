"""Convert absolute major/minor chords to probabilistic musical functions."""

from __future__ import annotations

from dreamsync.dsp.tonality import PITCH_NAMES, parse_chord

from .models import FunctionalChordHypothesis, KeyHypothesis


_MAJOR = {
    0: (1, "I", "major"),
    2: (2, "ii", "minor"),
    4: (3, "iii", "minor"),
    5: (4, "IV", "major"),
    7: (5, "V", "major"),
    9: (6, "vi", "minor"),
    11: (7, "vii°", "diminished"),
}
_MINOR = {
    0: (1, "i", "minor"),
    2: (2, "ii°", "diminished"),
    3: (3, "III", "major"),
    5: (4, "iv", "minor"),
    7: (5, "V", "major"),
    8: (6, "VI", "major"),
    10: (7, "VII", "major"),
    11: (7, "vii°", "diminished"),
}
_CHROMATIC = {
    1: (2, "bII"),
    3: (3, "bIII"),
    6: (4, "#IV"),
    8: (6, "bVI"),
    10: (7, "bVII"),
}


def functional_hypotheses(
    chord: str | None,
    keys: tuple[KeyHypothesis, ...],
    *,
    chord_confidence: float,
    tonal_confidence: float,
    max_hypotheses: int = 4,
) -> tuple[FunctionalChordHypothesis, ...]:
    parsed = parse_chord(chord)
    if (
        parsed is None
        or chord_confidence < 0.25
        or tonal_confidence < 0.2
        or not keys
    ):
        return ()
    root, quality = parsed
    candidates: list[FunctionalChordHypothesis] = []
    for key in keys:
        interval = (root - key.tonic_pc) % 12
        mapping = _MAJOR if key.mode == "major" else _MINOR
        borrowed = interval not in mapping
        if interval in mapping:
            degree, numeral, expected = mapping[interval]
            compatibility = 1.0 if quality == expected else 0.42
            if expected == "diminished" and quality in {"major", "minor"}:
                compatibility = 0.28
        elif interval in _CHROMATIC:
            degree, numeral = _CHROMATIC[interval]
            numeral = numeral.upper() if quality == "major" else numeral.lower()
            compatibility = 0.28
        else:
            continue
        probability = (
            key.probability
            * max(0.0, min(1.0, chord_confidence))
            * max(0.0, min(1.0, tonal_confidence))
            * compatibility
        )
        candidates.append(
            FunctionalChordHypothesis(
                key=key,
                degree=degree,
                numeral=numeral,
                quality=quality,
                inversion=None,
                borrowed=borrowed or compatibility < 0.5,
                applied_target=None,
                probability=probability,
            )
        )
    candidates.sort(key=lambda item: item.probability, reverse=True)
    selected = candidates[: max(1, int(max_hypotheses))]
    total = sum(item.probability for item in selected)
    if total < 1e-9:
        return ()
    return tuple(
        FunctionalChordHypothesis(
            key=item.key,
            degree=item.degree,
            numeral=item.numeral,
            quality=item.quality,
            inversion=item.inversion,
            borrowed=item.borrowed,
            applied_target=item.applied_target,
            probability=item.probability / total,
        )
        for item in selected
    )


def absolute_chord_for_function(function: str, key: KeyHypothesis) -> str | None:
    mapping = _MAJOR if key.mode == "major" else _MINOR
    for interval, (_degree, numeral, quality) in mapping.items():
        if numeral == function:
            suffix = "m" if quality == "minor" else ""
            return f"{PITCH_NAMES[(key.tonic_pc + interval) % 12]}{suffix}"
    return None
