from __future__ import annotations

import textwrap
from pathlib import Path

from dreamsync.gui.controllers.palette_controller import PaletteController
from dreamsync.gui.services.profile_service import ProfileService


def test_palette_controller_updates_and_saves(tmp_path: Path):
    path = tmp_path / "profile.yaml"
    path.write_text(
        textwrap.dedent(
            """
            name: Palette Test
            version: 1
            palettes:
              warm: ["#110000", "#220000", "#330000"]
            moods:
              chill: {palettes: [warm]}
              groove: {palettes: [warm]}
              hype: {palettes: [warm]}
              drop: {palettes: [warm]}
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    controller = PaletteController(ProfileService())
    state = controller.load(path)
    assert state.selected_palette == "warm"

    controller.update_rgb("warm", 1, 12, 34, 56)
    controller.update_hsv("warm", 2, 120.0, 1.0, 0.5)
    saved = controller.save()

    assert saved.unsaved_changes is False
    assert saved.palettes["warm"][1] == "#0c2238"
    assert saved.palettes["warm"][2].startswith("#")


def test_palette_controller_saves_profile_sections(tmp_path: Path):
    path = tmp_path / "profile.yaml"
    path.write_text(
        textwrap.dedent(
            """
            name: Palette Test
            version: 1
            palettes:
              warm: ["#110000", "#220000", "#330000"]
            moods:
              chill: {palettes: [warm]}
              groove: {palettes: [warm]}
              hype: {palettes: [warm]}
              drop: {palettes: [warm]}
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    controller = PaletteController(ProfileService())
    controller.load(path)
    controller.set_mood_effects_text('[{"name": "gradient_flow", "weight": 1.5}]')
    controller.set_mood_params_text('{"wave_rate_mult": 0.25}')
    controller.set_profile_eq_routes_text('[{"band": "bass", "when": "dominant"}]')
    controller.set_mood_eq_routes_text('[{"band": "presence", "when": "lift"}]')
    controller.set_profile_instrument_routes_text('[{"instrument": "vocals", "when": "dominant"}]')
    controller.set_mood_instrument_routes_text('[{"instrument": "bass", "when": "present"}]')
    controller.set_transitions_text('[{"from": "chill", "to": "groove", "palette": "warm"}]')

    saved = controller.save_sections()

    assert saved.unsaved_changes is False
    assert "Saved profile sections" in saved.status_message
    profile = ProfileService().load_profile(path)
    assert profile.eq_routes[0].band == "bass"
    assert profile.moods["chill"].eq_routes[0].band == "presence"
    assert profile.instrument_routes[0].instrument == "vocals"
    assert profile.moods["chill"].instrument_routes[0].instrument == "bass"
    assert profile.transitions[0].to_mood == "groove"


def test_palette_controller_creates_default_named_palette(tmp_path: Path):
    path = tmp_path / "profile.yaml"
    path.write_text(
        textwrap.dedent(
            """
            name: Palette Test
            version: 1
            palettes:
              warm: ["#110000", "#220000", "#330000"]
            moods:
              chill: {palettes: [warm]}
              groove: {palettes: [warm]}
              hype: {palettes: [warm]}
              drop: {palettes: [warm]}
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    controller = PaletteController(ProfileService())
    controller.load(path)
    state = controller.create_palette()

    assert state.selected_palette == "new palette 1"
    assert state.palettes["new palette 1"] == ("#110000", "#220000", "#330000")


def test_profile_service_generates_gradient_palette_from_one_seed():
    service = ProfileService()

    palette = service.generate_palette_from_seed_colors(["#7fffd4"], scheme="gradient")

    assert len(palette) == 6
    assert all(color.startswith("#") for color in palette)
    assert len(set(palette)) > 1


def test_profile_service_generates_complementary_palette_from_two_seeds():
    service = ProfileService()

    palette = service.generate_palette_from_seed_colors(
        ["#ff8800", "#8000ff"],
        scheme="complementary",
    )

    assert len(palette) == 6
    assert all(color.startswith("#") for color in palette)


def test_profile_service_generates_quickshow_document_without_spatial_defaults():
    service = ProfileService()

    document = service.generate_quickshow_profile_document(
        name="Test Quickshow",
        seed_colors=["#ff8800", "#8000ff"],
        scheme="complementary",
        energy_modifier=1.15,
        rng_seed=7,
    )

    assert document["name"] == "Test Quickshow"
    assert "palettes" in document
    assert "moods" in document
    assert document["instrument_routes"]
    assert document["eq_routes"]
    assert all("spatial_preset" not in route for route in document["eq_routes"])
    assert all("spatial_preset" not in route for route in document["instrument_routes"])


def test_profile_service_saves_generated_quickshow_profile(tmp_path: Path):
    service = ProfileService()

    path = service.save_generated_quickshow_profile(
        tmp_path,
        name="My Quickshow",
        seed_colors=["#7fffd4"],
        scheme="gradient",
        energy_modifier=0.95,
        rng_seed=3,
    )

    profile = service.load_profile(path)

    assert path.exists()
    assert profile.name == "My Quickshow"
