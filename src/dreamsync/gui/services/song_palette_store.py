"""Persistence for GUI song-to-palette assignments."""

from __future__ import annotations

import json
import os
from pathlib import Path

from dreamsync.profile_overrides import SongPaletteAssignment, track_key_for_path


def default_song_palette_store_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "DreamSync" / "song-palette-assignments.json"
    return Path.home() / ".dreamsync" / "song-palette-assignments.json"


class SongPaletteStore:
    """Load and save persisted song palette assignments."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or default_song_palette_store_path()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, SongPaletteAssignment]:
        if not self._path.exists():
            return {}
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        assignments: dict[str, SongPaletteAssignment] = {}
        for track_key, value in raw.items():
            if not isinstance(value, dict):
                continue
            assignment = SongPaletteAssignment.from_data(value)
            if assignment.track_key:
                assignments[str(track_key)] = assignment
        return assignments

    def save_all(self, assignments: dict[str, SongPaletteAssignment]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            track_key: assignment.to_data()
            for track_key, assignment in assignments.items()
        }
        self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def upsert(self, assignment: SongPaletteAssignment) -> dict[str, SongPaletteAssignment]:
        assignments = self.load()
        assignments[assignment.track_key] = assignment
        self.save_all(assignments)
        return assignments

    def clear(self, track_key: str) -> dict[str, SongPaletteAssignment]:
        assignments = self.load()
        assignments.pop(track_key, None)
        self.save_all(assignments)
        return assignments

    def assignment_for_path(self, track_path: Path | str) -> SongPaletteAssignment | None:
        return self.load().get(track_key_for_path(track_path))
