"""Deterministic file naming for output MP3 segments."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize(name: str) -> str:
    """Remove characters that are invalid in file paths."""
    name = _ILLEGAL_CHARS.sub("_", name)
    name = name.strip(". ")
    return name or "untitled"


class FileNamer:
    """Generate deterministic output filenames for MP3 segments.

    Parameters
    ----------
    output_dir:
        Directory where MP3 files are written.  Created if it doesn't exist.
    pattern:
        ``"timestamp"`` (default) or ``"metadata"``.
    """

    def __init__(
        self,
        output_dir: str = "./output",
        pattern: str = "timestamp",
    ) -> None:
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._pattern = pattern
        self._counter: int = 0

    @property
    def output_dir(self) -> Path:
        return self._output_dir

    @property
    def counter(self) -> int:
        return self._counter

    def next_filename(self, metadata: dict | None = None) -> str:
        """Return the full path for the next segment file."""
        self._counter += 1
        base = self._build_base(metadata)
        return str(self._resolve_collision(base))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_base(self, metadata: dict | None) -> str:
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

        if (
            self._pattern == "metadata"
            and metadata
            and metadata.get("artist")
            and metadata.get("song_title")
        ):
            artist = sanitize(metadata["artist"])
            title = sanitize(metadata["song_title"])
            return f"{ts}_{artist}_-_{title}"

        return f"{ts}_segment_{self._counter:06d}"

    def _resolve_collision(self, base: str) -> Path:
        candidate = self._output_dir / f"{base}.mp3"
        if not candidate.exists():
            return candidate
        for i in range(2, 100_000):
            candidate = self._output_dir / f"{base}_{i}.mp3"
            if not candidate.exists():
                return candidate
        return candidate  # pragma: no cover
