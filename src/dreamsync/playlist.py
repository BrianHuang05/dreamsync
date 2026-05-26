"""Playlist management for local audio file playback."""

from __future__ import annotations

import hashlib
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

HASH_BYTES = 65536  # 64KB


class PlaylistManager:
    """Manage an ordered list of audio files for sequential playback.

    Sources:
    - Single file: PlaylistManager.from_file(path)
    - Directory: PlaylistManager.from_directory(path)
    - M3U file: PlaylistManager.from_m3u(path)
    - Auto-detect: PlaylistManager.from_path(path)
    """

    def __init__(
        self,
        tracks: list[Path],
        *,
        shuffle: bool = False,
        repeat: bool = False,
    ) -> None:
        self._tracks: list[Path] = [Path(t) for t in tracks]
        self._shuffle = shuffle
        self._repeat = repeat
        self._index: int = 0
        self._lock = threading.RLock()

        if shuffle and len(self._tracks) > 1:
            import random
            random.shuffle(self._tracks)

    @classmethod
    def from_file(cls, path: Path | str, **kwargs) -> PlaylistManager:
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"Audio file not found: {path}")
        return cls([path], **kwargs)

    @classmethod
    def from_directory(
        cls,
        directory: Path | str,
        *,
        extensions: tuple[str, ...] = (".mp3", ".wav", ".flac", ".ogg", ".aac"),
        **kwargs,
    ) -> PlaylistManager:
        directory = Path(directory)
        if not directory.is_dir():
            raise NotADirectoryError(f"Not a directory: {directory}")

        tracks = sorted(
            [f for f in directory.iterdir()
             if f.is_file() and f.suffix.lower() in extensions],
            key=lambda f: f.name.lower(),
        )

        if not tracks:
            logger.warning("No audio files found in '%s' with extensions %s", directory, extensions)

        return cls(tracks, **kwargs)

    @classmethod
    def from_m3u(cls, path: Path | str, **kwargs) -> PlaylistManager:
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"Playlist file not found: {path}")

        base_dir = path.parent
        tracks = []

        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                # Skip empty lines and comments/metadata
                if not line or line.startswith("#"):
                    continue

                track_path = Path(line)
                # Resolve relative paths against M3U file's directory
                if not track_path.is_absolute():
                    track_path = base_dir / track_path

                if track_path.is_file():
                    tracks.append(track_path.resolve())
                else:
                    logger.warning("Skipping non-existent file in playlist: %s", line)

        return cls(tracks, **kwargs)

    @classmethod
    def from_tracks(cls, tracks: list[Path], **kwargs) -> PlaylistManager:
        """Create a playlist from an explicit list of file paths.

        Validates all paths exist. Raises FileNotFoundError for missing files.
        """
        missing = [t for t in tracks if not Path(t).is_file()]
        if missing:
            raise FileNotFoundError(f"Audio files not found: {missing}")
        return cls([Path(t) for t in tracks], **kwargs)

    @classmethod
    def from_path(cls, path: Path | str, **kwargs) -> PlaylistManager:
        """Auto-detect source type and create playlist."""
        path = Path(path)

        if path.is_dir():
            return cls.from_directory(path, **kwargs)
        elif path.suffix.lower() in (".m3u", ".m3u8"):
            return cls.from_m3u(path, **kwargs)
        elif path.is_file():
            return cls.from_file(path, **kwargs)
        else:
            raise FileNotFoundError(f"Path not found: {path}")

    @property
    def current(self) -> Path | None:
        with self._lock:
            if not self._tracks or self._index >= len(self._tracks):
                return None
            return self._tracks[self._index]

    @property
    def current_index(self) -> int:
        with self._lock:
            return self._index

    def next(self) -> Path | None:
        with self._lock:
            if not self._tracks:
                return None

            self._index += 1

            if self._index >= len(self._tracks):
                if self._repeat:
                    self._index = 0
                    if self._shuffle:
                        import random
                        random.shuffle(self._tracks)
                else:
                    return None  # exhausted

            return self.current

    def prev(self) -> Path | None:
        with self._lock:
            if not self._tracks:
                return None

            if self._index > 0:
                self._index -= 1
            # At index 0, stay at 0 (no wrap-around for prev)

            return self.current

    def peek_next(self, count: int = 1) -> list[Path]:
        with self._lock:
            result = []
            for i in range(1, count + 1):
                idx = self._index + i
                if idx < len(self._tracks):
                    result.append(self._tracks[idx])
                elif self._repeat and self._tracks:
                    idx = idx % len(self._tracks)
                    result.append(self._tracks[idx])
            return result

    def snapshot(self) -> tuple[Path, ...]:
        """Return the full playlist order as an immutable snapshot."""
        with self._lock:
            return tuple(self._tracks)

    def jump_to(self, index: int) -> Path:
        """Make *index* the current track and return it."""
        with self._lock:
            self._validate_index(index)
            self._index = index
            return self._tracks[self._index]

    def remove(self, index: int) -> Path:
        """Remove a track by absolute index and return the removed path."""
        with self._lock:
            self._validate_index(index)
            removed = self._tracks.pop(index)

            if index < self._index:
                self._index -= 1
            elif self._index >= len(self._tracks):
                self._index = max(0, len(self._tracks) - 1)

            return removed

    def move(self, from_index: int, to_index: int) -> None:
        """Move a track to a new absolute index."""
        with self._lock:
            self._validate_index(from_index)
            self._validate_index(to_index)
            if from_index == to_index:
                return

            track = self._tracks.pop(from_index)
            self._tracks.insert(to_index, track)

            if self._index == from_index:
                self._index = to_index
            elif from_index < self._index <= to_index:
                self._index -= 1
            elif to_index <= self._index < from_index:
                self._index += 1

    def append(self, track: Path | str) -> Path:
        """Append a track to the end of the playlist."""
        path = Path(track)
        if not path.is_file():
            raise FileNotFoundError(f"Audio file not found: {path}")
        with self._lock:
            self._tracks.append(path)
        return path

    def insert(self, index: int, track: Path | str) -> Path:
        """Insert a track at an absolute index."""
        path = Path(track)
        if not path.is_file():
            raise FileNotFoundError(f"Audio file not found: {path}")
        with self._lock:
            if index < 0 or index > len(self._tracks):
                raise IndexError(f"Track index out of range: {index}")
            self._tracks.insert(index, path)
            if index <= self._index:
                self._index += 1
        return path

    def shuffle_upcoming(self) -> None:
        """Shuffle tracks after the current one, keeping the current track fixed."""
        with self._lock:
            if len(self._tracks) - self._index <= 2:
                return
            import random

            upcoming = self._tracks[self._index + 1:]
            random.shuffle(upcoming)
            self._tracks[self._index + 1:] = upcoming

    def __len__(self) -> int:
        with self._lock:
            return len(self._tracks)

    def __iter__(self):
        return iter(self.snapshot())

    def _validate_index(self, index: int) -> None:
        if index < 0 or index >= len(self._tracks):
            raise IndexError(f"Track index out of range: {index}")


def content_hash_track_id(file_path: Path | str) -> str:
    """Generate a cache-stable track ID from file content.

    Uses SHA256 of first 64KB + file size. Stable across renames,
    unique across different files.

    Returns: 'contenthash_<hex16>'
    """
    file_path = Path(file_path)
    file_size = file_path.stat().st_size

    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        h.update(f.read(HASH_BYTES))
    h.update(str(file_size).encode())

    return f"contenthash_{h.hexdigest()[:16]}"
