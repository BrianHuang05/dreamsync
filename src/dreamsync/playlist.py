"""Playlist management for local audio file playback."""

from __future__ import annotations

import hashlib
import logging
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
        if not self._tracks or self._index >= len(self._tracks):
            return None
        return self._tracks[self._index]

    @property
    def current_index(self) -> int:
        return self._index

    def next(self) -> Path | None:
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
        if not self._tracks:
            return None

        if self._index > 0:
            self._index -= 1
        # At index 0, stay at 0 (no wrap-around for prev)

        return self.current

    def peek_next(self, count: int = 1) -> list[Path]:
        result = []
        for i in range(1, count + 1):
            idx = self._index + i
            if idx < len(self._tracks):
                result.append(self._tracks[idx])
            elif self._repeat and self._tracks:
                idx = idx % len(self._tracks)
                result.append(self._tracks[idx])
        return result

    def __len__(self) -> int:
        return len(self._tracks)

    def __iter__(self):
        return iter(self._tracks)


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
