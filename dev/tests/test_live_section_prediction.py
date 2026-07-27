from dreamsync.prediction.models import PhraseFingerprint
from dreamsync.prediction.section import SectionOrderMemory


def _phrase(values: tuple[str, ...]) -> PhraseFingerprint:
    return PhraseFingerprint(
        bars=len(values),
        numerals=values,
        chord_duration_pattern=(4,) * len(values),
        cadence_class="authentic",
        harmonic_rhythm_pattern=(1,) * len(values),
        energy_shape=(0, 1, 2, 2)[: len(values)],
        onset_shape=(0,) * len(values),
        key_path=("0:major",),
    )


def test_anonymous_section_recurrence_works_before_semantic_label() -> None:
    memory = SectionOrderMemory()
    first = memory.observe_completed(
        phrases=(_phrase(("I", "V", "vi", "IV")),),
        duration_bars=4,
        entrance_function="I",
        exit_function="IV",
        energy_envelope=(0, 1, 2, 2),
        onset_envelope=(0, 1, 1, 2),
    )
    matches = memory.match_prefix(("I", "V"))
    assert matches and matches[0].section_id == first.section_id
    assert matches[0].semantic_role is None


def test_transition_graph_predicts_song_local_successor() -> None:
    memory = SectionOrderMemory()
    kwargs_a = dict(
        phrases=(_phrase(("I", "V", "vi", "IV")),),
        duration_bars=4,
        entrance_function="I",
        exit_function="IV",
        energy_envelope=(0, 0, 1, 1),
        onset_envelope=(0, 0, 1, 1),
    )
    kwargs_b = dict(
        phrases=(_phrase(("IV", "V", "I", "I")),),
        duration_bars=4,
        entrance_function="IV",
        exit_function="I",
        energy_envelope=(1, 2, 3, 3),
        onset_envelope=(1, 2, 3, 3),
    )
    a = memory.observe_completed(**kwargs_a)
    b = memory.observe_completed(**kwargs_b)
    memory.observe_completed(**kwargs_a)
    predicted = memory.predict_successor()
    assert predicted and predicted[0].section_id == b.section_id
    assert memory.order[-1] == a.section_id
