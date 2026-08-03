"""Validation for Spotify captures before canonical learned artifacts are published."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpotifyCaptureCandidate:
    track_id: str
    uri: str = ""
    title: str = ""
    artist: str = ""
    album: str = ""
    expected_duration_seconds: float = 0.0
    observed_start_progress_seconds: float = 0.0
    observed_end_progress_seconds: float = 0.0
    captured_duration_seconds: float = 0.0
    seek_detected: bool = False
    paused: bool = False
    skipped: bool = False
    capture_gaps: int = 0
    capture_restarts: int = 0
    audio_readable: bool = True
    nontrivial_audio: bool = True


@dataclass(frozen=True)
class CaptureEligibility:
    eligible: bool
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class CaptureEligibilityConfig:
    max_start_offset_seconds: float = 2.0
    minimum_audio_seconds: float = 1.0
    duration_tolerance_seconds: float = 2.0
    duration_tolerance_ratio: float = 0.01
    substantial_end_margin_seconds: float = 2.0


class CaptureEligibilityValidator:
    def __init__(self, config: CaptureEligibilityConfig | None = None) -> None:
        self.config = config or CaptureEligibilityConfig()

    def validate(self, candidate: SpotifyCaptureCandidate) -> CaptureEligibility:
        reasons: list[str] = []
        if not candidate.track_id.strip():
            reasons.append("missing_track_id")
        if candidate.observed_start_progress_seconds > self.config.max_start_offset_seconds:
            reasons.append("started_mid_track")
        if candidate.seek_detected:
            reasons.append("seek_detected")
        if candidate.paused:
            reasons.append("paused")
        if candidate.skipped or (
            candidate.expected_duration_seconds > 0
            and candidate.observed_end_progress_seconds
            < candidate.expected_duration_seconds - self.config.substantial_end_margin_seconds
        ):
            reasons.append("skipped")
        if candidate.capture_gaps:
            reasons.append("capture_gap")
        if candidate.capture_restarts:
            reasons.append("capture_restart")
        tolerance = max(
            self.config.duration_tolerance_seconds,
            candidate.expected_duration_seconds * self.config.duration_tolerance_ratio,
        )
        if (
            candidate.expected_duration_seconds <= 0
            or abs(candidate.captured_duration_seconds - candidate.expected_duration_seconds) > tolerance
        ):
            reasons.append("duration_mismatch")
        if not candidate.audio_readable:
            reasons.append("unreadable_audio")
        elif (
            not candidate.nontrivial_audio
            or candidate.captured_duration_seconds < self.config.minimum_audio_seconds
        ):
            reasons.append("trivial_audio")
        return CaptureEligibility(not reasons, tuple(dict.fromkeys(reasons)))
