from __future__ import annotations

from pathlib import Path

from dreamsync.gui.services.song_palette_store import SongPaletteStore
from dreamsync.profile_overrides import SongPaletteAssignment, track_key_for_path


def test_song_palette_store_round_trip_and_clear(tmp_path: Path):
    song = tmp_path / "song.mp3"
    song.touch()
    track_key = track_key_for_path(song)
    store = SongPaletteStore(tmp_path / "assignments.json")

    assignment = SongPaletteAssignment(
        track_key=track_key,
        palette_name="calm",
        colors=("#111111", "#222222", "#333333"),
        source_label="aurora",
        source_profile_path="aurora.yaml",
        generated_seed=12,
    )

    saved = store.upsert(assignment)
    assert saved[track_key].summary_label == "aurora / calm"

    loaded = store.load()
    assert loaded[track_key].colors[1] == "#222222"
    assert store.assignment_for_path(song) == assignment

    cleared = store.clear(track_key)
    assert track_key not in cleared
    assert store.assignment_for_path(song) is None
