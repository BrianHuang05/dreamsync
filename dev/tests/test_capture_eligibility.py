from dataclasses import replace

from dreamsync.capture.eligibility import (
    CaptureEligibilityValidator,
    SpotifyCaptureCandidate,
)


def complete_candidate() -> SpotifyCaptureCandidate:
    return SpotifyCaptureCandidate(
        track_id="abc",
        expected_duration_seconds=200.0,
        observed_start_progress_seconds=0.4,
        observed_end_progress_seconds=199.5,
        captured_duration_seconds=199.5,
    )


def test_complete_capture_is_eligible():
    assert CaptureEligibilityValidator().validate(complete_candidate()).eligible


def test_ineligible_reasons_are_deterministic():
    result = CaptureEligibilityValidator().validate(
        replace(
            complete_candidate(),
            observed_start_progress_seconds=4.0,
            seek_detected=True,
            capture_gaps=1,
            capture_restarts=1,
        )
    )
    assert result.reasons == (
        "started_mid_track", "seek_detected", "capture_gap", "capture_restart"
    )


def test_duration_tolerance_accepts_boundary_and_rejects_both_sides():
    validator = CaptureEligibilityValidator()
    assert validator.validate(replace(complete_candidate(), captured_duration_seconds=198.0)).eligible
    assert "duration_mismatch" in validator.validate(
        replace(complete_candidate(), captured_duration_seconds=197.99)
    ).reasons
    assert "duration_mismatch" in validator.validate(
        replace(complete_candidate(), captured_duration_seconds=202.01)
    ).reasons


def test_pause_invalidates_capture():
    result = CaptureEligibilityValidator().validate(
        replace(complete_candidate(), paused=True)
    )
    assert result.reasons == ("paused",)
