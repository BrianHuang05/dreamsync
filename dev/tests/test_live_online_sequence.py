from __future__ import annotations

import pytest

from dreamsync.prediction.online_sequence import (
    FunctionalToken,
    VariableOrderProgressionModel,
    token_function,
)


def _token(function: str, position: int) -> FunctionalToken:
    return FunctionalToken(function, position, 4.0)


@pytest.mark.parametrize(
    "progression",
    (
        ("I", "vi", "IV", "V", "I", "ii", "V", "I"),
        ("I", "I", "IV", "IV", "I", "I", "V", "IV", "I", "V", "IV", "I"),
    ),
)
def test_long_progressions_become_predictable_after_repetition(
    progression: tuple[str, ...],
) -> None:
    model = VariableOrderProgressionModel(max_order=64)
    tokens = tuple(_token(function, index + 1) for index, function in enumerate(progression))
    for _ in range(2):
        for token in tokens:
            model.observe(token)
    context = tokens[:-1]
    prediction = model.predict(context)
    assert prediction is not None
    assert token_function(prediction.distribution[0][0]) == progression[-1]
    assert prediction.context_length >= len(progression) - 1


def test_long_context_backs_off_after_mismatch() -> None:
    model = VariableOrderProgressionModel(max_order=16)
    sequence = tuple(_token(value, index) for index, value in enumerate(("I", "IV", "V", "I")))
    for _ in range(3):
        for token in sequence:
            model.observe(token)
    mismatched = sequence[:-1] + (_token("ii", 99),)
    prediction = model.predict(mismatched)
    assert prediction is not None
    assert prediction.context_length < len(mismatched)


def test_low_confidence_error_does_not_create_long_context_and_memory_is_bounded() -> None:
    model = VariableOrderProgressionModel(max_nodes=20, max_order=12)
    for index in range(80):
        model.observe(_token(f"X{index}", index), probability=0.2 if index == 20 else 1.0)
    assert model.node_count <= 20
