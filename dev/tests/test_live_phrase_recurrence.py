from dreamsync.prediction.models import PhraseFingerprint
from dreamsync.prediction.recurrence import PhraseRecurrenceMemory


def _fingerprint(numerals: tuple[str, ...]) -> PhraseFingerprint:
    return PhraseFingerprint(
        bars=len(numerals),
        numerals=numerals,
        chord_duration_pattern=(4,) * len(numerals),
        cadence_class="authentic",
        harmonic_rhythm_pattern=(1,) * len(numerals),
        energy_shape=(0, 1, 1, 2)[: len(numerals)],
        onset_shape=(0,) * len(numerals),
        key_path=("major",),
    )


def test_partial_match_predicts_continuation_with_one_substitution() -> None:
    memory = PhraseRecurrenceMemory()
    memory.remember(_fingerprint(("vi", "IV", "V", "I")))
    matches = memory.match_partial(
        ("vi", "ii", "V"),
        duration_pattern=(4, 4, 4),
        maximum_substitutions=1,
    )
    assert matches
    assert matches[0].continuation == "I"
    assert matches[0].score > 0.7


def test_phrase_memory_is_bounded() -> None:
    memory = PhraseRecurrenceMemory(max_phrases=3)
    for index in range(10):
        memory.remember(_fingerprint((f"X{index}",)))
    assert len(memory.phrases) == 3
