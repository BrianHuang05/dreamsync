"""Validation for Spotify captures before canonical learned artifacts are published."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


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


_SIDECAR_FIELD_ALIASES = {
    "spotifyTrackId": "spotify_track_id",
    "spotifyUri": "spotify_uri",
    "songTitle": "song_title",
    "expectedDurationSeconds": "expected_duration_seconds",
    "observedStartProgressSeconds": "observed_start_progress_seconds",
    "observedEndProgressSeconds": "observed_end_progress_seconds",
    "segmentDurationSeconds": "segment_duration_seconds",
    "startFrame": "start_frame",
    "endFrame": "end_frame",
    "sampleRate": "sample_rate",
    "seekDetected": "seek_detected",
    "captureRestarts": "capture_restarts",
}


def normalize_capture_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Return capture metadata using the worker's snake-case field names.

    Capture callbacks already use snake case, while persisted sidecars use
    camel case.  Normalizing both through one function keeps reuse validation
    identical to validation of a newly finished capture.
    """

    normalized = dict(metadata)
    for persisted_name, runtime_name in _SIDECAR_FIELD_ALIASES.items():
        if runtime_name not in normalized and persisted_name in normalized:
            normalized[runtime_name] = normalized[persisted_name]
    return normalized


def candidate_from_capture_metadata(
    mp3_path: Path | str,
    metadata: dict[str, Any],
) -> SpotifyCaptureCandidate:
    """Build an eligibility candidate from a live callback or sidecar."""

    path = Path(mp3_path)
    metadata = normalize_capture_metadata(metadata)
    expected = float(metadata.get("expected_duration_seconds") or 0.0)
    captured = metadata.get("segment_duration_seconds")
    if captured is None:
        sample_rate = float(metadata.get("sample_rate") or 1.0)
        captured = (
            float(metadata.get("end_frame", 0))
            - float(metadata.get("start_frame", 0))
        ) / sample_rate
    observed_end = metadata.get("observed_end_progress_seconds")
    if observed_end is None:
        observed_end = expected
    gaps = metadata.get("gaps") or ()
    return SpotifyCaptureCandidate(
        track_id=str(metadata.get("spotify_track_id") or ""),
        uri=str(metadata.get("spotify_uri") or ""),
        title=str(metadata.get("song_title") or ""),
        artist=str(metadata.get("artist") or ""),
        album=str(metadata.get("album") or ""),
        expected_duration_seconds=expected,
        observed_start_progress_seconds=float(
            metadata.get("observed_start_progress_seconds") or 0.0
        ),
        observed_end_progress_seconds=float(observed_end or 0.0),
        captured_duration_seconds=float(captured or 0.0),
        seek_detected=bool(metadata.get("seek_detected", False)),
        paused=bool(metadata.get("paused", False)),
        skipped=bool(metadata.get("skipped", False)),
        capture_gaps=len(gaps),
        capture_restarts=int(metadata.get("capture_restarts") or 0),
        audio_readable=path.is_file(),
        nontrivial_audio=path.is_file() and path.stat().st_size > 1024,
    )
