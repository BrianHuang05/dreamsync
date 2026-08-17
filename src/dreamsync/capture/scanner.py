"""Capture directory scanner — reads MP3 + sidecar pairs from a capture directory."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from dreamsync.capture.eligibility import (
    CaptureEligibilityValidator,
    candidate_from_capture_metadata,
    normalize_capture_metadata,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CaptureTrack:
    """One captured song file with optional sidecar metadata."""

    mp3_path: Path
    sidecar_path: Path | None
    segment_index: int
    song_title: str | None
    artist: str | None
    album: str | None
    duration_seconds: float
    spotify_track_id: str | None = None
    spotify_uri: str | None = None
    expected_duration_seconds: float | None = None
    metadata: dict = field(default_factory=dict)


class CaptureDirectoryScanner:
    """Scan a capture output directory for MP3 + JSON sidecar pairs."""

    def __init__(self, directory: Path | str) -> None:
        self._directory = Path(directory).resolve()
        if not self._directory.is_dir():
            raise FileNotFoundError(f"Capture directory not found: {self._directory}")

    def scan(self) -> list[CaptureTrack]:
        """Scan directory for MP3 files and pair with JSON sidecars.

        Returns CaptureTrack list sorted by segment_index, then filename.
        """
        mp3_files = sorted(
            self._directory.rglob("*.mp3"),
            key=lambda path: str(path.relative_to(self._directory)).lower(),
        )
        if not mp3_files:
            return []

        tracks: list[CaptureTrack] = []
        for fallback_index, mp3_path in enumerate(mp3_files):
            sidecar_path = mp3_path.with_suffix(".json")
            sidecar_data = self._read_sidecar(sidecar_path)

            if sidecar_data is not None:
                track = CaptureTrack(
                    mp3_path=mp3_path,
                    sidecar_path=sidecar_path,
                    segment_index=sidecar_data.get("segmentIndex", fallback_index),
                    song_title=sidecar_data.get("songTitle"),
                    artist=sidecar_data.get("artist"),
                    album=sidecar_data.get("album"),
                    duration_seconds=sidecar_data.get("segmentDurationSeconds", 0.0),
                    spotify_track_id=sidecar_data.get("spotifyTrackId"),
                    spotify_uri=sidecar_data.get("spotifyUri"),
                    expected_duration_seconds=sidecar_data.get("expectedDurationSeconds"),
                    metadata=sidecar_data,
                )
            else:
                track = CaptureTrack(
                    mp3_path=mp3_path,
                    sidecar_path=None,
                    segment_index=fallback_index,
                    song_title=None,
                    artist=None,
                    album=None,
                    duration_seconds=0.0,
                    metadata={},
                )
            tracks.append(track)

        tracks.sort(key=lambda t: (t.segment_index, t.mp3_path.name.lower()))
        return tracks

    def find_reusable_capture(
        self,
        spotify_track_id: str,
        *,
        validator: CaptureEligibilityValidator | None = None,
    ) -> tuple[CaptureTrack, dict] | None:
        """Return the newest complete, eligible MP3 for a Spotify track."""

        validator = validator or CaptureEligibilityValidator()
        matches: list[tuple[CaptureTrack, dict]] = []
        for track in self.scan():
            if track.sidecar_path is None or track.spotify_track_id != spotify_track_id:
                continue
            metadata = normalize_capture_metadata(track.metadata)
            candidate = candidate_from_capture_metadata(track.mp3_path, metadata)
            if validator.validate(candidate).eligible:
                matches.append((track, metadata))
        if not matches:
            return None
        return max(matches, key=lambda match: match[0].mp3_path.stat().st_mtime)

    def _read_sidecar(self, path: Path) -> dict | None:
        """Read and parse a JSON sidecar file. Returns None on missing/corrupt."""
        if not path.is_file():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                logger.warning("Sidecar is not a JSON object: %s", path)
                return None
            return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Corrupt sidecar, treating as missing: %s (%s)", path, exc)
            return None
