"""Persistence for GUI-authored show control patches."""

from __future__ import annotations

import json
import os
from pathlib import Path

from dreamsync.profile_overrides import track_key_for_path
from dreamsync.show.control_patch import ShowControlPatch


def default_show_patch_store_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "DreamSync" / "show-control-patches.json"
    return Path.home() / ".dreamsync" / "show-control-patches.json"


class ShowPatchStore:
    """Load and save persisted show control patches keyed by track key."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or default_show_patch_store_path()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, ShowControlPatch]:
        if not self._path.exists():
            return {}
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        patches: dict[str, ShowControlPatch] = {}
        for track_key, value in raw.items():
            if isinstance(value, dict):
                patches[str(track_key)] = ShowControlPatch.from_data(value)
        return patches

    def save_all(self, patches: dict[str, ShowControlPatch]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            track_key: patch.to_data()
            for track_key, patch in patches.items()
        }
        self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def upsert(self, track_key: str, patch: ShowControlPatch) -> dict[str, ShowControlPatch]:
        patches = self.load()
        patches[str(track_key)] = patch
        self.save_all(patches)
        return patches

    def clear(self, track_key: str) -> dict[str, ShowControlPatch]:
        patches = self.load()
        patches.pop(str(track_key), None)
        self.save_all(patches)
        return patches

    def patch_for_path(self, track_path: Path | str) -> ShowControlPatch | None:
        return self.load().get(track_key_for_path(track_path))
