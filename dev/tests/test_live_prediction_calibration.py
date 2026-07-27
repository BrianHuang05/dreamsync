from dreamsync.prediction.calibration import (
    CalibrationParameters,
    PredictionCalibrator,
    report_calibration,
)
from dreamsync.prediction.models import (
    PredictedMusicalEvent,
    PredictionAlternative,
    PredictionEvidence,
)


def _event(probability: float) -> PredictedMusicalEvent:
    return PredictedMusicalEvent(
        prediction_id="p1",
        created_t=0.0,
        target_t=1.0,
        timing_sigma=0.1,
        event_type="chord_change",
        target_function="I",
        target_section=None,
        direction=None,
        probability=probability,
        alternatives=(
            PredictionAlternative("I", probability),
            PredictionAlternative("vi", 1.0 - probability),
        ),
        evidence=(PredictionEvidence("test", probability),),
        model_version="test",
    )


def test_calibration_normalizes_alternatives_and_abstains() -> None:
    calibrator = PredictionCalibrator(
        {"chord_change": CalibrationParameters(1.0, 0.0, 0.6)}
    )
    assert calibrator.calibrate(_event(0.4)) is None
    accepted = calibrator.calibrate(_event(0.8))
    assert accepted is not None
    assert abs(sum(item.probability for item in accepted.alternatives) - 1.0) < 1e-9
    assert accepted.raw_probability == 0.8


def test_calibration_report_is_separate_by_event_type() -> None:
    report = report_calibration(
        (
            ("chord_change", 0.8, True),
            ("chord_change", 0.2, False),
            ("phrase_boundary", 0.6, True),
        )
    )
    assert [item.event_type for item in report] == [
        "chord_change",
        "phrase_boundary",
    ]
