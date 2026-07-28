"""Bounded cache statistics and transactional capture archiving for the GUI."""

from __future__ import annotations

import os
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from dreamsync.cache import ShowCache, path_based_track_id


@dataclass(frozen=True)
class DirectoryStats:
    path: Path
    file_count: int
    total_bytes: int


@dataclass(frozen=True)
class StorageSnapshot:
    cache: DirectoryStats
    captures: DirectoryStats


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
        )

    def clear_all_cache(self) -> int:
        self._safe_root(self._cache_dir, label="cache")
        return ShowCache(self._cache_dir).clear()

    def clear_track_cache(self, track_path: Path | str) -> int:
        track_id = path_based_track_id(Path(track_path))
        self._safe_track_id(track_id)
        return ShowCache(self._cache_dir).invalidate(track_id)

    def capture_preview(self, capture_dir: Path | str) -> CaptureArchivePreview:
        directory = Path(capture_dir).expanduser().resolve()
        files = tuple(sorted(path for path in directory.glob("*.mp3") if path.is_file())) if directory.is_dir() else ()
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
                    archive.write(source, source.name)
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
