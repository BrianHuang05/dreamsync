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


def test_queue_controller_inserts_cued_track_after_current_item(tmp_path: Path):
    for name in ("a.mp3", "b.mp3", "c.mp3"):
        (tmp_path / name).touch()

    playlist = PlaylistManager.from_tracks([tmp_path / "a.mp3", tmp_path / "c.mp3"])
    controller = QueueController(QueueService())
    controller.bind_playlist(playlist, source_path=tmp_path)

    state = controller.insert_playlist(playlist, 1, tmp_path / "b.mp3")

    assert state.tracks == ("a.mp3", "b.mp3", "c.mp3")
    assert state.current_index == 0


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


def test_queue_controller_locks_current_and_next_show_at_transition(tmp_path: Path):
    for name in ("a.mp3", "b.mp3", "c.mp3"):
        (tmp_path / name).touch()

    playlist = PlaylistManager.from_directory(tmp_path)
    session = LocalPlaylistSession(MagicMock(), playlist, cache=MagicMock(), profile=None)
    session.session_snapshot = MagicMock(
        return_value={"position_seconds": 91.0, "duration_seconds": 100.0}
    )
    controller = QueueController(QueueService())

    state = controller.bind_local_session(session)

    assert state.local_tracks[0].is_current is True
    assert state.local_tracks[0].show_editable is False
    assert state.local_tracks[0].edit_lock_reason == "Currently playing"
    assert state.local_tracks[1].is_next is True
    assert state.local_tracks[1].show_editable is False
    assert state.local_tracks[1].edit_lock_reason == "Locked for the upcoming transition"
    assert state.local_tracks[2].show_editable is True


def test_queue_controller_keeps_unstarted_playlist_tracks_editable(tmp_path: Path):
    for name in ("a.mp3", "b.mp3"):
        (tmp_path / name).touch()

    controller = QueueController(QueueService())
    state = controller.bind_playlist(PlaylistManager.from_directory(tmp_path), source_path=tmp_path)

    assert all(track.show_editable for track in state.local_tracks)
    assert not any(track.is_current for track in state.local_tracks)


def test_local_playlist_session_pause_and_resume_delegate_to_current_player(tmp_path: Path):
    (tmp_path / "a.mp3").touch()
    session = LocalPlaylistSession(
        MagicMock(),
        PlaylistManager.from_directory(tmp_path),
        cache=MagicMock(),
        profile=None,
    )

    class Player:
        playing = True
        finished = False
        position_seconds = 0.0

        def pause(self):
            self.playing = False

        def play(self):
            self.playing = True

    session._current_player = Player()

    assert session.toggle_pause() == "paused"
    assert session.session_snapshot()["playback_state"] == "paused"
    assert session.toggle_pause() == "playing"
    assert session.session_snapshot()["playback_state"] == "playing"


def test_local_playlist_skip_survives_track_preparation(tmp_path: Path):
    """A skip pressed during compilation must not be cleared by the next track."""

    for name in ("a.mp3", "b.mp3"):
        (tmp_path / name).touch()
    playlist = PlaylistManager.from_directory(tmp_path)
    session = LocalPlaylistSession(MagicMock(), playlist, cache=MagicMock(), profile=None)
    session._session.load_track = MagicMock(return_value=MagicMock())

    session.signal_next()

    assert session._play_track(playlist.current, stop_event=MagicMock()) == "next"
    assert session._tracks_skipped == 1
    assert not session._signal_next.is_set()
