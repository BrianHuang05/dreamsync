"""Controller for per-song GUI show override patches."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from dreamsync.gui.models.show_patch_state import ShowPatchEditorState
from dreamsync.gui.services.show_patch_store import ShowPatchStore
from dreamsync.profile_overrides import track_key_for_path
from dreamsync.show.control_patch import CueOverrideRule, ShowControlPatch, rule_from_data, rule_to_data


@dataclass
class ShowPatchController:
    store: ShowPatchStore
    state: ShowPatchEditorState = ShowPatchEditorState()

    def bind_track(self, track_key: str, display_name: str, track_path: str) -> ShowPatchEditorState:
        patch = self.store.load().get(track_key)
        self.state = ShowPatchEditorState(
            selected_track_key=track_key,
            selected_track_label=display_name,
            selected_track_path=track_path,
            patch_name=patch.name if patch is not None else "",
            patch_rules_text=self._rules_to_text(patch.rules if patch is not None else ()),
            patch_summary=self._summary_for_patch(patch),
            status_message="",
            unsaved_changes=False,
        )
        return self.state

    def set_patch_name(self, value: str) -> ShowPatchEditorState:
        self.state = replace(self.state, patch_name=value, unsaved_changes=True)
        return self.state

    def set_patch_rules_text(self, value: str) -> ShowPatchEditorState:
        self.state = replace(self.state, patch_rules_text=value, unsaved_changes=True)
        return self.state

    def save_selected_patch(self) -> ShowPatchEditorState:
        track_key = self.state.selected_track_key
        if not track_key:
            raise ValueError("Select a song before saving a show override.")
        patch = ShowControlPatch(
            name=self.state.patch_name.strip() or "GUI Patch",
            rules=self._parse_rules_text(self.state.patch_rules_text),
        )
        self.store.upsert(track_key, patch)
        self.state = replace(
            self.state,
            patch_name=patch.name,
            patch_rules_text=self._rules_to_text(patch.rules),
            patch_summary=self._summary_for_patch(patch),
            status_message="Show override saved.",
            unsaved_changes=False,
        )
        return self.state

    def clear_selected_patch(self) -> ShowPatchEditorState:
        track_key = self.state.selected_track_key
        if not track_key:
            raise ValueError("Select a song before clearing a show override.")
        self.store.clear(track_key)
        self.state = replace(
            self.state,
            patch_name="",
            patch_rules_text="[]",
            patch_summary="No show override saved.",
            status_message="Show override cleared.",
            unsaved_changes=False,
        )
        return self.state

    def patch_for_path(self, track_path: Path | str) -> ShowControlPatch | None:
        return self.store.patch_for_path(track_path)

    def patch_for_track_key(self, track_key: str) -> ShowControlPatch | None:
        return self.store.load().get(track_key)

    @staticmethod
    def track_key_for_path(track_path: Path | str) -> str:
        return track_key_for_path(track_path)

    @staticmethod
    def _rules_to_text(rules: tuple[CueOverrideRule, ...]) -> str:
        return json.dumps([rule_to_data(rule) for rule in rules], indent=2, sort_keys=True)

    @staticmethod
    def _parse_rules_text(text: str) -> tuple[CueOverrideRule, ...]:
        try:
            raw = json.loads(text.strip() or "[]")
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON for show override rules: {exc.msg}") from exc
        if not isinstance(raw, list):
            raise ValueError("Show override rules must be a JSON array.")
        return tuple(rule_from_data(value) for value in raw if isinstance(value, dict))

    @staticmethod
    def _summary_for_patch(patch: ShowControlPatch | None) -> str:
        if patch is None or not patch.rules:
            return "No show override saved."
        return f"{patch.name or 'Patch'} ({len(patch.rules)} rule(s))"
