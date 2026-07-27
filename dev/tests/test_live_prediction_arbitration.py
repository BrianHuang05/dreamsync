from dataclasses import replace

from dreamsync.prediction.arbitration import arbitrate_predictions
from dreamsync.prediction.models import (
    PredictedMusicalEvent,
    PredictionAlternative,
    PredictionEvidence,
)


def _event(event_type: str, probability: float, function: str = "I"):
    return PredictedMusicalEvent(
        prediction_id=f"{event_type}-{probability}",
        created_t=0.0,
        target_t=1.0,
        timing_sigma=0.1,
        event_type=event_type,
        target_function=function,
        target_section=None,
        direction=None,
        probability=probability,
        alternatives=(PredictionAlternative(function, 1.0),),
        evidence=(PredictionEvidence("test", 1.0),),
        model_version="test",
    )


def test_conflicting_same_time_predictions_are_deterministic() -> None:
    low = _event("chord_change", 0.6, "vi")
    high = _event("harmonic_resolution", 0.8, "I")
    assert arbitrate_predictions((low, high)) == (high,)
    assert arbitrate_predictions((high, low)) == (high,)


def test_missing_evidence_abstains() -> None:
    event = replace(_event("phrase_boundary", 0.8), evidence=())
    assert arbitrate_predictions((event,)) == ()
