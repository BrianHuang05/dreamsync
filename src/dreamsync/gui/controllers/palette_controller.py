"""Palette editor controller."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dreamsync.color_utils import hsv_to_hex, rgb_to_hex
from dreamsync.gui.models.palette_state import PaletteEditorState
from dreamsync.gui.services.profile_service import ProfileService


@dataclass
class PaletteController:
    service: ProfileService
    state: PaletteEditorState = PaletteEditorState()
    _default_palette_colors: tuple[str, ...] = ("#ff6b6b", "#ffd166", "#06d6a0", "#118ab2")

    def load(self, path: Path) -> PaletteEditorState:
        profile = self.service.load_profile(path)
        raw = self.service.load_profile_document(path)
        first_palette = next(iter(profile.palettes), "")
        moods = tuple(profile.moods.keys())
        first_mood = moods[0] if moods else ""
        self.state = PaletteEditorState(
            profile_name=profile.name,
            profile_path=str(path),
            selected_palette=first_palette,
            palettes=dict(profile.palettes),
            selected_mood=first_mood,
            moods=moods,
            palette_colors_text=", ".join(profile.palettes.get(first_palette, ())),
            mood_effects_text=self._json_text(self._mood_document(raw, first_mood).get("effects", [])),
            mood_params_text=self._json_text(self._mood_document(raw, first_mood).get("params", {})),
            profile_eq_routes_text=self._json_text(raw.get("eq_routes", [])),
            mood_eq_routes_text=self._json_text(self._mood_document(raw, first_mood).get("eq_routes", [])),
            profile_instrument_routes_text=self._json_text(raw.get("instrument_routes", [])),
            mood_instrument_routes_text=self._json_text(
                self._mood_document(raw, first_mood).get("instrument_routes", [])
            ),
            transitions_text=self._json_text(raw.get("transitions", [])),
            status_message=f"Loaded profile: {profile.name}",
            unsaved_changes=False,
        )
        return self.state

    def select_palette(self, palette_name: str) -> PaletteEditorState:
        if palette_name in self.state.palettes:
            self.state = self.state.with_palette_selection(palette_name)
        return self.state

    def select_mood(self, mood: str) -> PaletteEditorState:
        profile_path = Path(self.state.profile_path)
        raw = self.service.load_profile_document(profile_path)
        mood_doc = self._mood_document(raw, mood)
        self.state = self.state.with_mood_selection(
            mood,
            mood_effects_text=self._json_text(mood_doc.get("effects", [])),
            mood_params_text=self._json_text(mood_doc.get("params", {})),
            mood_eq_routes_text=self._json_text(mood_doc.get("eq_routes", [])),
            mood_instrument_routes_text=self._json_text(mood_doc.get("instrument_routes", [])),
        )
        return self.state

    def update_hex(self, palette_name: str, color_index: int, color: str) -> PaletteEditorState:
        colors = list(self.state.palettes[palette_name])
        colors[color_index] = color
        self.state = self.state.with_palette(palette_name, tuple(colors))
        return self.state

    def create_palette(
        self,
        *,
        name: str | None = None,
        colors: tuple[str, ...] | None = None,
    ) -> PaletteEditorState:
        palette_name = name or self._next_palette_name()
        palette_colors = colors or self.state.palettes.get(self.state.selected_palette) or self._default_palette_colors
        self.state = self.state.with_palette(palette_name, tuple(palette_colors))
        return self.state

    def append_palette_color(self, color: str = "#ffffff") -> PaletteEditorState:
        selected = self.state.selected_palette
        colors = list(self.state.palettes.get(selected, self._default_palette_colors))
        if len(colors) >= 8:
            raise ValueError("Palettes can have at most 8 colors.")
        colors.append(color)
        self.state = self.state.with_palette(selected, tuple(colors))
        return self.state

    def remove_palette_color(self) -> PaletteEditorState:
        selected = self.state.selected_palette
        colors = list(self.state.palettes.get(selected, ()))
        if len(colors) <= 3:
            raise ValueError("Palettes must keep at least 3 colors.")
        colors.pop()
        self.state = self.state.with_palette(selected, tuple(colors))
        return self.state

    def update_rgb(self, palette_name: str, color_index: int, r: int, g: int, b: int) -> PaletteEditorState:
        return self.update_hex(palette_name, color_index, rgb_to_hex(r, g, b))

    def update_hsv(
        self, palette_name: str, color_index: int, h: float, s: float, v: float,
    ) -> PaletteEditorState:
        return self.update_hex(palette_name, color_index, hsv_to_hex(h, s, v))

    def set_palette_colors_text(self, text: str) -> PaletteEditorState:
        colors = self._parse_palette_colors(text)
        self.state = self.state.with_palette(self.state.selected_palette, colors)
        return self.state

    def set_palette_colors(self, colors: tuple[str, ...]) -> PaletteEditorState:
        if not colors:
            raise ValueError("Palette colors cannot be empty.")
        self.state = self.state.with_palette(self.state.selected_palette, tuple(colors))
        return self.state

    def set_mood_effects_text(self, text: str) -> PaletteEditorState:
        self.state = self._replace_text(mood_effects_text=text)
        return self.state

    def set_mood_params_text(self, text: str) -> PaletteEditorState:
        self.state = self._replace_text(mood_params_text=text)
        return self.state

    def set_profile_eq_routes_text(self, text: str) -> PaletteEditorState:
        self.state = self._replace_text(profile_eq_routes_text=text)
        return self.state

    def set_mood_eq_routes_text(self, text: str) -> PaletteEditorState:
        self.state = self._replace_text(mood_eq_routes_text=text)
        return self.state

    def set_profile_instrument_routes_text(self, text: str) -> PaletteEditorState:
        self.state = self._replace_text(profile_instrument_routes_text=text)
        return self.state

    def set_mood_instrument_routes_text(self, text: str) -> PaletteEditorState:
        self.state = self._replace_text(mood_instrument_routes_text=text)
        return self.state

    def set_transitions_text(self, text: str) -> PaletteEditorState:
        self.state = self._replace_text(transitions_text=text)
        return self.state

    def save(self) -> PaletteEditorState:
        profile_path = Path(self.state.profile_path)
        selected = self.state.selected_palette
        updated = self.service.update_palette(profile_path, selected, self.state.palettes[selected])
        self.load(profile_path)
        self.state = self._replace_text(
            selected_palette=selected,
            status_message=f"Saved palette '{selected}'.",
            unsaved_changes=False,
        )
        return self.select_palette(selected)

    def save_sections(self) -> PaletteEditorState:
        profile_path = Path(self.state.profile_path)
        mood = self.state.selected_mood
        effects = self._parse_json(self.state.mood_effects_text, expected_type=list, field_name="mood effects")
        params = self._parse_json(self.state.mood_params_text, expected_type=dict, field_name="mood params")
        profile_eq_routes = self._parse_json(
            self.state.profile_eq_routes_text,
            expected_type=list,
            field_name="profile EQ routes",
        )
        mood_eq_routes = self._parse_json(
            self.state.mood_eq_routes_text,
            expected_type=list,
            field_name="mood EQ routes",
        )
        profile_instrument_routes = self._parse_json(
            self.state.profile_instrument_routes_text,
            expected_type=list,
            field_name="profile instrument routes",
        )
        mood_instrument_routes = self._parse_json(
            self.state.mood_instrument_routes_text,
            expected_type=list,
            field_name="mood instrument routes",
        )
        transitions = self._parse_json(
            self.state.transitions_text,
            expected_type=list,
            field_name="transitions",
        )

        self.service.update_mood_effects(profile_path, mood, effects)
        self.service.update_mood_params(profile_path, mood, params)
        self.service.update_eq_routes(profile_path, profile_eq_routes)
        self.service.update_eq_routes(profile_path, mood_eq_routes, mood=mood)
        self.service.update_instrument_routes(profile_path, profile_instrument_routes)
        self.service.update_instrument_routes(profile_path, mood_instrument_routes, mood=mood)
        self.service.update_transitions(profile_path, transitions)

        self.load(profile_path)
        self.state = self._replace_text(
            status_message=f"Saved profile sections for mood '{mood}'.",
            unsaved_changes=False,
        )
        return self.select_mood(mood)

    def save_all(self) -> PaletteEditorState:
        self.save()
        return self.save_sections()

    def _replace_text(self, **changes: Any) -> PaletteEditorState:
        return PaletteEditorState(
            profile_name=str(changes.get("profile_name", self.state.profile_name)),
            profile_path=str(changes.get("profile_path", self.state.profile_path)),
            selected_palette=str(changes.get("selected_palette", self.state.selected_palette)),
            palettes=dict(changes.get("palettes", self.state.palettes)),
            selected_mood=str(changes.get("selected_mood", self.state.selected_mood)),
            moods=tuple(changes.get("moods", self.state.moods)),
            palette_colors_text=str(changes.get("palette_colors_text", self.state.palette_colors_text)),
            mood_effects_text=str(changes.get("mood_effects_text", self.state.mood_effects_text)),
            mood_params_text=str(changes.get("mood_params_text", self.state.mood_params_text)),
            profile_eq_routes_text=str(
                changes.get("profile_eq_routes_text", self.state.profile_eq_routes_text)
            ),
            mood_eq_routes_text=str(changes.get("mood_eq_routes_text", self.state.mood_eq_routes_text)),
            profile_instrument_routes_text=str(
                changes.get(
                    "profile_instrument_routes_text",
                    self.state.profile_instrument_routes_text,
                )
            ),
            mood_instrument_routes_text=str(
                changes.get(
                    "mood_instrument_routes_text",
                    self.state.mood_instrument_routes_text,
                )
            ),
            transitions_text=str(changes.get("transitions_text", self.state.transitions_text)),
            status_message=str(changes.get("status_message", self.state.status_message)),
            unsaved_changes=bool(changes.get("unsaved_changes", True)),
        )

    def _next_palette_name(self) -> str:
        existing = {name.casefold() for name in self.state.palettes}
        index = 1
        while True:
            candidate = f"new palette {index}"
            if candidate.casefold() not in existing:
                return candidate
            index += 1

    @staticmethod
    def _parse_palette_colors(text: str) -> tuple[str, ...]:
        colors = tuple(part.strip() for part in text.split(",") if part.strip())
        if not colors:
            raise ValueError("Palette colors cannot be empty.")
        return colors

    @staticmethod
    def _parse_json(text: str, *, expected_type: type, field_name: str):
        try:
            value = json.loads(text.strip() or ("[]" if expected_type is list else "{}"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON for {field_name}: {exc.msg}") from exc
        if not isinstance(value, expected_type):
            label = "array" if expected_type is list else "object"
            raise ValueError(f"{field_name.capitalize()} must be a JSON {label}.")
        return value

    @staticmethod
    def _json_text(value: object) -> str:
        return json.dumps(value, indent=2, sort_keys=True)

    @staticmethod
    def _mood_document(raw: dict[str, object], mood: str) -> dict[str, Any]:
        moods = raw.get("moods", {})
        if isinstance(moods, dict):
            mood_doc = moods.get(mood, {})
            if isinstance(mood_doc, dict):
                return mood_doc
        return {}
