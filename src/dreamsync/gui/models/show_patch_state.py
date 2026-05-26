"""GUI state for per-song/per-show control patches."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ShowPatchEditorState:
    selected_track_key: str = ""
    selected_track_label: str = ""
    selected_track_path: str = ""
    patch_name: str = ""
    patch_rules_text: str = "[]"
    patch_summary: str = "No show override saved."
    status_message: str = ""
    unsaved_changes: bool = False
