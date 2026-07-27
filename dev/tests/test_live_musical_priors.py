from dreamsync.prediction.priors import (
    PRIOR_SOURCE,
    PRIOR_VERSION,
    cadence_distribution,
    next_function_distribution,
    phrase_length_distribution,
)


def test_priors_are_versioned_distributions_with_unknown() -> None:
    assert PRIOR_VERSION and PRIOR_SOURCE
    for distribution in (
        phrase_length_distribution((4, 4)),
        next_function_distribution(("vi", "IV", "V")).as_dict(),
        cadence_distribution(("V", "I")).as_dict(),
    ):
        assert abs(sum(distribution.values()) - 1.0) < 1e-9
        assert None in distribution or "unknown" in distribution


def test_cadence_retains_deceptive_alternative() -> None:
    predicted = next_function_distribution(("V",)).as_dict()
    assert predicted["I"] > predicted["vi"] > 0.0
