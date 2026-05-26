"""Local queue and Spotify queue orchestration services."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dreamsync.local_session import LocalPlaylistSession
from dreamsync.playlist import PlaylistManager
from dreamsync.profile_overrides import track_key_for_path
from dreamsync.spotify.client import SpotifyClient
from dreamsync.spotify.queue_watcher import SpotifyQueueWatcher


class QueueService:
    """Provide structured queue actions for the GUI."""

    def build_playlist(self, path: Path, *, shuffle: bool = False, repeat: bool = False) -> PlaylistManager:
        return PlaylistManager.from_path(path, shuffle=shuffle, repeat=repeat)

    def playlist_snapshot(self, playlist: PlaylistManager) -> dict[str, Any]:
        tracks = playlist.snapshot()
        current_index = playlist.current_index
        return {
            "current_index": current_index,
            "tracks": tuple(str(track) for track in tracks),
            "track_rows": tuple(
                self._track_row(track, index=index, current_index=current_index)
                for index, track in enumerate(tracks)
            ),
        }

    def local_queue_snapshot(self, session: LocalPlaylistSession) -> dict[str, Any]:
        snapshot = session.queue_snapshot()
        tracks = tuple(Path(track) for track in snapshot["tracks"])
        return {
            "current_index": int(snapshot["current_index"]),
            "tracks": tuple(str(track) for track in tracks),
            "track_rows": tuple(
                self._track_row(track, index=index, current_index=int(snapshot["current_index"]))
                for index, track in enumerate(tracks)
            ),
        }

    def move_local_track(self, session: LocalPlaylistSession, from_index: int, to_index: int) -> dict[str, Any]:
        session.move_track(from_index, to_index)
        return self.local_queue_snapshot(session)

    def move_playlist_track(self, playlist: PlaylistManager, from_index: int, to_index: int) -> dict[str, Any]:
        playlist.move(from_index, to_index)
        return self.playlist_snapshot(playlist)

    def remove_local_track(self, session: LocalPlaylistSession, index: int) -> dict[str, Any]:
        session.remove_track(index)
        return self.local_queue_snapshot(session)

    def remove_playlist_track(self, playlist: PlaylistManager, index: int) -> dict[str, Any]:
        playlist.remove(index)
        return self.playlist_snapshot(playlist)

    def play_local_now(self, session: LocalPlaylistSession, index: int) -> dict[str, Any]:
        session.play_now(index)
        return self.local_queue_snapshot(session)

    def play_playlist_now(self, playlist: PlaylistManager, index: int) -> dict[str, Any]:
        playlist.jump_to(index)
        return self.playlist_snapshot(playlist)

    def append_local_track(self, session: LocalPlaylistSession, path: Path | str) -> dict[str, Any]:
        session.append_track(path)
        return self.local_queue_snapshot(session)

    def insert_local_track(self, session: LocalPlaylistSession, index: int, path: Path | str) -> dict[str, Any]:
        session.insert_track(index, path)
        return self.local_queue_snapshot(session)

    def skip_local_track(self, session: LocalPlaylistSession) -> dict[str, Any]:
        session.skip_current()
        return session.session_snapshot()

    def shuffle_playlist(self, playlist: PlaylistManager) -> dict[str, Any]:
        playlist.shuffle_upcoming()
        return self.playlist_snapshot(playlist)

    def shuffle_local(self, session: LocalPlaylistSession) -> dict[str, Any]:
        session.shuffle_queue()
        return self.local_queue_snapshot(session)

    def spotify_queue_snapshot(
        self,
        client: SpotifyClient | None = None,
        watcher: SpotifyQueueWatcher | None = None,
    ) -> dict[str, Any]:
        if watcher is not None:
            snapshot = watcher.snapshot()
            queue = snapshot["queue"]
            playback_state = snapshot["playback_state"]
        elif client is not None:
            queue = client.get_queue()
            playback_state = client.get_playback_state()
        else:
            raise ValueError("Either a Spotify client or watcher must be provided")
        return {
            "current_track": getattr(getattr(playback_state, "track", None), "name", ""),
            "queue": tuple(track.name for track in getattr(queue, "queue", ())),
            "shuffle": bool(getattr(playback_state, "shuffle", False)) if playback_state else False,
        }

    def spotify_add_to_queue(self, client: SpotifyClient, uri: str) -> None:
        client.add_to_queue(uri)

    def spotify_skip(self, client: SpotifyClient) -> None:
        client.skip_to_next()

    def spotify_set_shuffle(self, client: SpotifyClient, enabled: bool) -> None:
        client.set_shuffle(enabled)

    @staticmethod
    def _track_row(track: Path | str, *, index: int, current_index: int) -> dict[str, Any]:
        path = Path(track)
        return {
            "track_key": track_key_for_path(path),
            "display_name": path.name,
            "path": str(path),
            "is_current": index == current_index,
        }
