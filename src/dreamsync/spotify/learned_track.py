"""Transactional durable storage for Spotify learned-live artifacts."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from dreamsync.analyzer.models import SongStructure
from dreamsync.cache import ShowCache, profile_fingerprint, spotify_track_cache_id
from dreamsync.capture.eligibility import SpotifyCaptureCandidate

MANIFEST_SCHEMA_VERSION = 1
ANALYSIS_VERSION = 1
COMPILER_VERSION = 1


class Mp3RetentionPolicy(str, Enum):
    KEEP_ALL = "keep_all"
    KEEP_RECENT = "keep_recent"
    DELETE_AFTER_VERIFIED_COMPILE = "delete_after_verified_compile"


@dataclass(frozen=True)
class TrackManifest:
    schema_version: int = MANIFEST_SCHEMA_VERSION
    provider: str = "spotify"
    provider_track_id: str = ""
    provider_uri: str = ""
    title: str = ""
    artist: str = ""
    album: str = ""
    duration_ms: int = 0
    captured_at: str = ""
    capture_complete: bool = False
    capture_validation: dict[str, Any] = field(default_factory=dict)
    analysis_version: int = ANALYSIS_VERSION
    compiler_version: int = COMPILER_VERSION
    profile_fingerprints: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return {
            "schemaVersion": data["schema_version"],
            "provider": data["provider"],
            "providerTrackId": data["provider_track_id"],
            "providerUri": data["provider_uri"],
            "title": data["title"],
            "artist": data["artist"],
            "album": data["album"],
            "durationMs": data["duration_ms"],
            "capturedAt": data["captured_at"],
            "captureComplete": data["capture_complete"],
            "captureValidation": data["capture_validation"],
            "analysisVersion": data["analysis_version"],
            "compilerVersion": data["compiler_version"],
            "profileFingerprints": list(data["profile_fingerprints"]),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TrackManifest":
        return cls(
            schema_version=int(data.get("schemaVersion", 0)),
            provider=str(data.get("provider", "")),
            provider_track_id=str(data.get("providerTrackId", "")),
            provider_uri=str(data.get("providerUri", "")),
            title=str(data.get("title", "")),
            artist=str(data.get("artist", "")),
            album=str(data.get("album", "")),
            duration_ms=int(data.get("durationMs", 0)),
            captured_at=str(data.get("capturedAt", "")),
            capture_complete=bool(data.get("captureComplete", False)),
            capture_validation=dict(data.get("captureValidation") or {}),
            analysis_version=int(data.get("analysisVersion", 0)),
            compiler_version=int(data.get("compilerVersion", 0)),
            profile_fingerprints=tuple(str(v) for v in data.get("profileFingerprints", ())),
        )


class LearnedTrackStore:
    def __init__(self, cache: ShowCache | Path | str = "~/.dreamsync/cache") -> None:
        self.cache = cache if isinstance(cache, ShowCache) else ShowCache(cache)

    def track_dir(self, track_id: str) -> Path:
        return self.cache.cache_dir / spotify_track_cache_id(track_id)

    def manifest_path(self, track_id: str) -> Path:
        return self.track_dir(track_id) / "manifest.json"

    def analysis_path(self, track_id: str) -> Path:
        return self.track_dir(track_id) / "analysis.json"

    def read_manifest(self, track_id: str) -> TrackManifest | None:
        try:
            data = json.loads(self.manifest_path(track_id).read_text(encoding="utf-8"))
            manifest = TrackManifest.from_dict(data)
            if manifest.schema_version != MANIFEST_SCHEMA_VERSION:
                return None
            return manifest
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def read_analysis(self, track_id: str) -> SongStructure | None:
        try:
            data = json.loads(self.analysis_path(track_id).read_text(encoding="utf-8"))
            return SongStructure.from_dict(data)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return None

    def write_analysis(self, track_id: str, structure: SongStructure) -> Path:
        path = self.analysis_path(track_id)
        self._atomic_json(path, structure.to_dict())
        if self.read_analysis(track_id) is None:
            raise RuntimeError("Persisted learned analysis failed reload validation")
        return path

    def publish_manifest(self, candidate: SpotifyCaptureCandidate, profile=None) -> Path:
        key = spotify_track_cache_id(candidate.track_id)
        if self.read_analysis(candidate.track_id) is None:
            raise RuntimeError("Cannot publish learned manifest without valid analysis")
        timeline = self.cache.get(key, profile)
        if timeline is None:
            raise RuntimeError("Cannot publish learned manifest without reloadable show")
        validation = {
            "startOffsetSeconds": candidate.observed_start_progress_seconds,
            "durationDeltaSeconds": candidate.captured_duration_seconds - candidate.expected_duration_seconds,
            "seekDetected": candidate.seek_detected,
            "skipped": candidate.skipped,
            "captureGaps": candidate.capture_gaps,
            "captureRestarts": candidate.capture_restarts,
        }
        manifest = TrackManifest(
            provider_track_id=candidate.track_id,
            provider_uri=candidate.uri,
            title=candidate.title,
            artist=candidate.artist,
            album=candidate.album,
            duration_ms=round(candidate.expected_duration_seconds * 1000),
            captured_at=datetime.now(timezone.utc).isoformat(),
            capture_complete=True,
            capture_validation=validation,
            profile_fingerprints=(profile_fingerprint(profile),),
        )
        path = self.manifest_path(candidate.track_id)
        self._atomic_json(path, manifest.to_dict())
        if not self.is_complete(candidate.track_id, profile):
            raise RuntimeError("Published learned track failed completeness validation")
        return path

    def is_complete(self, track_id: str, profile=None) -> bool:
        manifest = self.read_manifest(track_id)
        if manifest is None or not manifest.capture_complete or manifest.provider_track_id != track_id:
            return False
        if self.read_analysis(track_id) is None:
            return False
        return self.cache.get(spotify_track_cache_id(track_id), profile) is not None

    def lookup(self, track_id: str, profile=None):
        if not self.is_complete(track_id, profile):
            return None
        return self.cache.get(spotify_track_cache_id(track_id), profile)

    def list_manifests(self) -> tuple[TrackManifest, ...]:
        manifests = []
        for path in self.cache.cache_dir.glob("spotify_*/manifest.json"):
            try:
                manifest = TrackManifest.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
            if manifest.capture_complete:
                manifests.append(manifest)
        return tuple(sorted(manifests, key=lambda item: item.captured_at, reverse=True))

    def recovery_items(self) -> tuple[Path, ...]:
        """Return incomplete transaction evidence without guessing provider identity."""
        items: list[Path] = []
        for directory in self.cache.cache_dir.glob("spotify_*"):
            if not directory.is_dir():
                continue
            manifest = directory / "manifest.json"
            if not manifest.exists() or self.read_manifest(directory.name.removeprefix("spotify_")) is None:
                items.append(directory)
        return tuple(items)

    @staticmethod
    def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        try:
            temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

