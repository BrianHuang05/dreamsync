from __future__ import annotations

from dreamsync.prediction.function import (
    absolute_chord_for_function,
    functional_hypotheses,
)
from dreamsync.prediction.models import KeyHypothesis


def _key(tonic: int, mode: str = "major") -> KeyHypothesis:
    return KeyHypothesis(tonic, mode, 1.0, 1.0, 16)


def test_transposed_progressions_share_functional_sequence() -> None:
    progressions = (("Am", "F", "G", "C"), ("F#m", "D", "E", "A"))
    keys = (_key(0), _key(9))
    sequences = []
    for progression, key in zip(progressions, keys):
        sequences.append(
            tuple(
                functional_hypotheses(
                    chord,
                    (key,),
                    chord_confidence=1.0,
                    tonal_confidence=1.0,
                )[0].numeral
                for chord in progression
            )
        )
    assert sequences[0] == sequences[1] == ("vi", "IV", "V", "I")


def test_low_confidence_harmony_abstains() -> None:
    assert not functional_hypotheses(
        "G", (_key(0),), chord_confidence=0.1, tonal_confidence=1.0
    )


def test_absolute_mapping_supports_visual_diagnostics() -> None:
    assert absolute_chord_for_function("V", _key(0)) == "G"
    assert absolute_chord_for_function("vi", _key(0)) == "Am"
