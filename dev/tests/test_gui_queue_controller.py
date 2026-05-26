from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from dreamsync.gui.controllers.queue_controller import QueueController
from dreamsync.gui.services.queue_service import QueueService
from dreamsync.local_session import LocalPlaylistSession
from dreamsync.playlist import PlaylistManager
from dreamsync.profile_overrides import SongPaletteAssignment, track_key_for_path
from dreamsync.spotify.models import PlaybackState, QueueSnapshot, SpotifyTrack


def test_queue_controller_local_mutations(tmp_path: Path):
    for name in ("a.mp3", "b.mp3", "c.mp3"):
        (tmp_path / name).touch()

    playlist = PlaylistManager.from_directory(tmp_path)
    session = LocalPlaylistSession(MagicMock(), playlist, cache=MagicMock(), profile=None)
    controller = QueueController(QueueService())

    state = controller.bind_local_session(session)
    assert state.tracks == ("a.mp3", "b.mp3", "c.mp3")

    state = controller.remove_local(session, 1)
    assert state.tracks == ("a.mp3", "c.mp3")

    extra = tmp_path / "d.mp3"
    extra.touch()
    state = controller.append_local(session, extra)
    assert state.tracks == ("a.mp3", "c.mp3", "d.mp3")

    state = controller.play_local_now(session, 2)
    assert state.current_index == 0

    state = controller.shuffle_local(session)
    assert state.tracks[0] == "a.mp3"


def test_queue_controller_playlist_mutations_without_session(tmp_path: Path):
    for name in ("a.mp3", "b.mp3", "c.mp3", "d.mp3"):
        (tmp_path / name).touch()

    playlist = PlaylistManager.from_directory(tmp_path)
    controller = QueueController(QueueService())

    state = controller.bind_playlist(playlist, source_path=tmp_path)
    assert state.tracks == ("a.mp3", "b.mp3", "c.mp3", "d.mp3")

    state = controller.move_playlist(playlist, 3, 1)
    assert state.tracks == ("a.mp3", "d.mp3", "b.mp3", "c.mp3")

    state = controller.remove_playlist(playlist, 2)
    assert state.tracks == ("a.mp3", "d.mp3", "c.mp3")

    state = controller.play_playlist_now(playlist, 1)
    assert state.current_index == 1

    state = controller.shuffle_playlist(playlist)
    assert state.tracks[1] == "d.mp3"


def test_queue_controller_spotify_refresh():
    controller = QueueController(QueueService())
    track = SpotifyTrack(
        track_id="1",
        name="Current",
        artist="Artist",
        album="Album",
        duration_ms=200000,
        uri="spotify:track:1",
    )
    queue_track = SpotifyTrack(
        track_id="2",
        name="Next",
        artist="Artist",
        album="Album",
        duration_ms=180000,
        uri="spotify:track:2",
    )
    client = MagicMock()
    client.get_playback_state.return_value = PlaybackState(
        is_playing=True,
        track=track,
        progress_ms=0,
        timestamp=0.0,
        device_name="desk",
        shuffle=True,
        repeat="off",
    )
    client.get_queue.return_value = QueueSnapshot(
        currently_playing=track,
        queue=(queue_track,),
        fetched_at=0.0,
    )

    state = controller.refresh_spotify(client=client)

    assert state.spotify_current == "Current"
    assert state.spotify_upcoming == ("Next",)


def test_queue_controller_tracks_selection_and_assignment(tmp_path: Path):
    for name in ("a.mp3", "b.mp3"):
        (tmp_path / name).touch()

    playlist = PlaylistManager.from_directory(tmp_path)
    controller = QueueController(QueueService())

    track_key = track_key_for_path(tmp_path / "b.mp3")
    assignments = {
        track_key: SongPaletteAssignment(
            track_key=track_key,
            palette_name="energy",
            colors=("#112233", "#445566", "#778899"),
            source_label="generated_90",
        )
    }

    state = controller.bind_playlist(
        playlist,
        assignments=assignments,
        source_path=tmp_path,
    )
    state = controller.select_local_track(track_key)

    assert state.playlist_source_path == str(tmp_path)
    assert state.selected_track is not None
    assert state.selected_track.display_name == "b.mp3"
    assert state.selected_track.assignment_label == "generated_90 / energy"


def test_queue_controller_preview_palette_state():
    controller = QueueController(QueueService())

    state = controller.set_preview_palette(
        source_label="aurora",
        palette_name="calm",
        colors=("#123456", "#abcdef", "#654321"),
        source_path="profile.yaml",
        generated_seed=42,
    )

    assert state.preview_source_label == "aurora"
    assert state.preview_palette_name == "calm"
    assert state.preview_colors[0] == "#123456"
    assert state.preview_generated_seed == 42
