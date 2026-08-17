"""Library-root migration and maintenance helpers."""

from __future__ import annotations

import shutil
import json
import os
from dataclasses import dataclass
from pathlib import Path

from dreamsync.capture.eligibility import (
    CaptureEligibilityValidator,
    candidate_from_capture_metadata,
    normalize_capture_metadata,
)
from dreamsync.capture.scanner import CaptureDirectoryScanner


@dataclass(frozen=True)
class LibraryRoots:
    audio: Path
    analysis: Path
    shows: Path
    temp: Path

    @classmethod
    def from_values(
        cls,
        audio: Path | str,
        analysis: Path | str,
        shows: Path | str,
        temp: Path | str,
    ) -> "LibraryRoots":
        roots = cls(*(Path(value).expanduser().resolve() for value in (
            audio, analysis, shows, temp
        )))
        if len({str(path).casefold() for path in roots.paths}) != 4:
            raise ValueError("Library roots must use four different directories.")
        return roots

    @property
    def paths(self) -> tuple[Path, Path, Path, Path]:
        return self.audio, self.analysis, self.shows, self.temp

    def create(self) -> None:
        for path in self.paths:
            path.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class MigrationReport:
    promoted_audio_files: int = 0
    temporary_files: int = 0
    analysis_files: int = 0
    compiled_show_files: int = 0


def migrate_legacy_storage(
    *,
    legacy_capture_root: Path | str,
    legacy_cache_root: Path | str,
    roots: LibraryRoots,
) -> MigrationReport:
    """Move legacy captures/cache into the four-root library layout."""

    roots.create()
    capture_root = Path(legacy_capture_root).expanduser().resolve()
    cache_root = Path(legacy_cache_root).expanduser().resolve()
    promoted = temporary = analyses = shows = 0
    moved_capture_files: set[Path] = set()

    if capture_root.is_dir():
        validator = CaptureEligibilityValidator()
        for track in CaptureDirectoryScanner(capture_root).scan():
            source = track.mp3_path.resolve()
            sidecar = source.with_suffix(".json")
            metadata = normalize_capture_metadata(track.metadata)
            eligible = bool(
                track.sidecar_path is not None
                and validator.validate(
                    candidate_from_capture_metadata(source, metadata)
                ).eligible
            )
            destination_root = roots.audio if eligible else roots.temp / "legacy"
            moved_mp3 = _move_file(source, destination_root / source.name)
            moved_capture_files.add(source)
            promoted += int(eligible)
            temporary += int(not eligible)
            if sidecar.is_file():
                moved_sidecar = _move_file(sidecar, destination_root / sidecar.name)
                _rewrite_json_path(moved_sidecar, "outputFile", moved_mp3)
                moved_capture_files.add(sidecar.resolve())
        for source in sorted(path for path in capture_root.rglob("*") if path.is_file()):
            if source.resolve() in moved_capture_files:
                continue
            relative = source.relative_to(capture_root)
            _move_file(source, roots.temp / "legacy" / relative)
            temporary += 1
        _remove_empty_directories(capture_root)

    if cache_root.is_dir():
        for source in sorted(path for path in cache_root.rglob("*") if path.is_file()):
            relative = source.relative_to(cache_root)
            if source.name == "analysis.json" and source.parent.name.startswith("spotify_"):
                _move_file(source, roots.analysis / relative)
                analyses += 1
            else:
                _move_file(source, roots.shows / relative)
                shows += 1
        _remove_empty_directories(cache_root)

    repair_library_references(roots)
    return MigrationReport(promoted, temporary, analyses, shows)


def repair_library_references(roots: LibraryRoots) -> int:
    """Repair audio paths in migrated sidecars, analyses, and learned shows."""

    track_audio: dict[str, Path] = {}
    updated = 0
    for sidecar in roots.audio.rglob("*.json"):
        mp3 = sidecar.with_suffix(".mp3")
        if not mp3.is_file():
            continue
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        track_id = str(data.get("spotifyTrackId") or data.get("spotify_track_id") or "")
        if track_id:
            track_audio[track_id] = mp3.resolve()
        if data.get("outputFile") != str(mp3.resolve()):
            data["outputFile"] = str(mp3.resolve())
            _atomic_json(sidecar, data)
            updated += 1
    for track_id, audio_path in track_audio.items():
        directory_name = f"spotify_{track_id}"
        for directory in roots.analysis.rglob(directory_name):
            analysis = directory / "analysis.json"
            updated += int(_rewrite_json_path(analysis, "path", audio_path))
        for directory in roots.shows.rglob(directory_name):
            for show in directory.glob("*.show.json"):
                updated += int(_rewrite_json_path(show, "song_path", audio_path))
    return updated


def _move_file(source: Path, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    candidate = target
    suffix = 2
    while candidate.exists():
        candidate = target.with_name(f"{target.stem}_{suffix}{target.suffix}")
        suffix += 1
    shutil.move(str(source), str(candidate))
    return candidate


def _rewrite_json_path(path: Path, field: str, value: Path) -> bool:
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict) or data.get(field) == str(value.resolve()):
        return False
    data[field] = str(value.resolve())
    _atomic_json(path, data)
    return True


def _atomic_json(path: Path, data: dict) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _remove_empty_directories(root: Path) -> None:
    for directory in sorted(
        (path for path in root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        try:
            directory.rmdir()
        except OSError:
            pass
