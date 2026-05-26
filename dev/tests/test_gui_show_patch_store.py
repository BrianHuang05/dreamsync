from __future__ import annotations

from pathlib import Path

from dreamsync.gui.controllers.show_patch_controller import ShowPatchController
from dreamsync.gui.services.show_patch_store import ShowPatchStore
from dreamsync.profile_overrides import track_key_for_path


def test_show_patch_store_and_controller_round_trip(tmp_path: Path):
    song = tmp_path / "song.mp3"
    song.touch()
    track_key = track_key_for_path(song)
    store = ShowPatchStore(tmp_path / "patches.json")
    controller = ShowPatchController(store)

    controller.bind_track(track_key, "song.mp3", str(song))
    controller.set_patch_name("Vocal Push")
    controller.set_patch_rules_text(
        '[{"match": {"has_instrument": "vocals"}, "color_bias": "#ff88aa", "render_mode": "gradient"}]'
    )
    saved = controller.save_selected_patch()

    assert saved.unsaved_changes is False
    assert "1 rule" in saved.patch_summary

    patch = store.patch_for_path(song)
    assert patch is not None
    assert patch.name == "Vocal Push"
    assert patch.rules[0].match.has_instrument == "vocals"
    assert patch.rules[0].render_mode == "gradient"

    cleared = controller.clear_selected_patch()
    assert cleared.patch_summary == "No show override saved."
    assert store.patch_for_path(song) is None
