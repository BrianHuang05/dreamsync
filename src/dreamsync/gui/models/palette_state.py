"""Palette/profile editor state models."""

from __future__ import annotations

from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class PaletteEditorState:
    profile_name: str = ""
    profile_path: str = ""
    selected_palette: str = ""
    palettes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    selected_mood: str = ""
    moods: tuple[str, ...] = field(default_factory=tuple)
    palette_colors_text: str = ""
    mood_effects_text: str = "[]"
    mood_params_text: str = "{}"
    profile_eq_routes_text: str = "[]"
    mood_eq_routes_text: str = "[]"
    profile_instrument_routes_text: str = "[]"
    mood_instrument_routes_text: str = "[]"
    transitions_text: str = "[]"
    status_message: str = ""
    unsaved_changes: bool = False

    def with_palette(self, name: str, colors: tuple[str, ...]) -> "PaletteEditorState":
        new_palettes = dict(self.palettes)
        new_palettes[name] = colors
        return replace(
            self,
            palettes=new_palettes,
            selected_palette=name,
            palette_colors_text=", ".join(colors),
            unsaved_changes=True,
        )

    def with_palette_selection(self, name: str) -> "PaletteEditorState":
        colors = self.palettes.get(name, ())
        return replace(
            self,
            selected_palette=name,
            palette_colors_text=", ".join(colors),
        )

    def with_mood_selection(
        self,
        name: str,
        *,
        mood_effects_text: str,
        mood_params_text: str,
        mood_eq_routes_text: str,
        mood_instrument_routes_text: str,
    ) -> "PaletteEditorState":
        return replace(
            self,
            selected_mood=name,
            mood_effects_text=mood_effects_text,
            mood_params_text=mood_params_text,
            mood_eq_routes_text=mood_eq_routes_text,
            mood_instrument_routes_text=mood_instrument_routes_text,
        )
