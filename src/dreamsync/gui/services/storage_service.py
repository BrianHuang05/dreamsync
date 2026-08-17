"""Bounded cache statistics and transactional capture archiving for the GUI."""

from __future__ import annotations

import os
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from dreamsync.cache import ShowCache, path_based_track_id
from dreamsync.spotify.learned_track import LearnedTrackStore


@dataclass(frozen=True)
class DirectoryStats:
    path: Path
    file_count: int
    total_bytes: int


@dataclass(frozen=True)
class StorageSnapshot:
    cache: DirectoryStats
    captures: DirectoryStats
    learned: DirectoryStats = field(
        default_factory=lambda: DirectoryStats(Path("."), 0, 0)
    )


@dataclass(frozen=True)
class CaptureArchivePreview:
    directory: Path
    files: tuple[Path, ...]
    total_bytes: int


class StorageService:
    """Expose product-safe storage operations without arbitrary deletion."""

    def __init__(self, cache_dir: Path | str = "~/.dreamsync/cache") -> None:
        self._cache_dir = self._safe_root(cache_dir, label="cache")

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir

    def snapshot(self, capture_dir: Path | str) -> StorageSnapshot:
        cache = ShowCache(self._cache_dir).stats()
        captures = self.capture_preview(capture_dir)
        learned_files = tuple(
            path for directory in self._cache_dir.rglob("spotify_*")
            if directory.is_dir() for path in directory.iterdir() if path.is_file()
        )
        return StorageSnapshot(
            cache=DirectoryStats(
                path=cache.cache_dir,
                file_count=cache.entry_count,
                total_bytes=cache.total_bytes,
            ),
            captures=DirectoryStats(
                path=captures.directory,
                file_count=len(captures.files),
                total_bytes=captures.total_bytes,
            ),
            learned=DirectoryStats(
                path=self._cache_dir,
                file_count=len(learned_files),
                total_bytes=sum(path.stat().st_size for path in learned_files),
            ),
        )

    def learned_recovery_items(self) -> tuple[Path, ...]:
        return LearnedTrackStore(self._cache_dir).recovery_items()

    def delete_retained_source(self, source: Path | str, capture_dir: Path | str) -> None:
        source_path = Path(source).expanduser().resolve()
        root = self._safe_root(capture_dir, label="capture")
        try:
            source_path.relative_to(root)
        except ValueError:
            raise ValueError("Retained source is outside the configured capture directory.")
        if source_path.suffix.lower() != ".mp3":
            raise ValueError("Only retained MP3 source files can be deleted.")
        source_path.unlink(missing_ok=True)
        source_path.with_suffix(".json").unlink(missing_ok=True)

    def delete_learned_entry(self, track_id: str, profile=None) -> int:
        from dreamsync.cache import spotify_track_cache_id

        key = spotify_track_cache_id(track_id)
        self._safe_track_id(key)
        cache = ShowCache(self._cache_dir)
        if profile is not None:
            path = cache.entry_path(key, profile)
            if path.exists():
                path.unlink()
                return 1
            return 0
        directories = tuple(
            directory for directory in self._cache_dir.rglob(key)
            if directory.is_dir() and directory.name == key
        )
        if not directories:
            return 0
        count = 0
        for directory in directories:
            for path in directory.iterdir():
                if path.is_file():
                    path.unlink()
                    count += 1
            directory.rmdir()
        return count

    def clear_all_cache(self) -> int:
        self._safe_root(self._cache_dir, label="cache")
        return ShowCache(self._cache_dir).clear()

    def clear_track_cache(self, track_path: Path | str) -> int:
        track_id = path_based_track_id(Path(track_path))
        self._safe_track_id(track_id)
        return ShowCache(self._cache_dir).invalidate(track_id)

    def capture_preview(self, capture_dir: Path | str) -> CaptureArchivePreview:
        directory = Path(capture_dir).expanduser().resolve()
        files = (
            tuple(sorted(path for path in directory.rglob("*.mp3") if path.is_file()))
            if directory.is_dir() else ()
        )
        return CaptureArchivePreview(
            directory=directory,
            files=files,
            total_bytes=sum(path.stat().st_size for path in files),
        )

    def default_archive_path(self, capture_dir: Path | str) -> Path:
        directory = Path(capture_dir).expanduser().resolve()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return directory / f"archived_{stamp}.zip"

    def archive_captures(
        self,
        capture_dir: Path | str,
        destination: Path | str,
        *,
        keep_originals: bool = True,
    ) -> Path:
        preview = self.capture_preview(capture_dir)
        if not preview.files:
            raise ValueError("No MP3 capture files are available to archive.")
        destination = Path(destination).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.dreamsync-tmp")
        try:
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
                for source in preview.files:
                    archive.write(source, source.relative_to(preview.directory))
            with zipfile.ZipFile(temporary, "r") as archive:
                if archive.testzip() is not None or len(archive.namelist()) != len(preview.files):
                    raise RuntimeError("Capture archive verification failed.")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        if not keep_originals:
            for source in preview.files:
                source.unlink()
        return destination

    @staticmethod
    def _safe_root(path: Path | str, *, label: str) -> Path:
        resolved = Path(path).expanduser().resolve()
        if resolved == Path(resolved.anchor):
            raise ValueError(f"Refusing to use a filesystem root as the {label} directory.")
        return resolved

    def _safe_track_id(self, track_id: str) -> None:
        if not track_id or track_id in {".", ".."} or any(char in track_id for char in ("/", "\\")):
            raise ValueError("Invalid cache track identifier.")
        candidate = (self._cache_dir / track_id).resolve()
        if candidate.parent != self._cache_dir:
            raise ValueError("Cache track identifier escapes the cache root.")
