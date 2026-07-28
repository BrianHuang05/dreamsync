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
        request_precompile = getattr(session, "request_precompile_upcoming", None)
        if callable(request_precompile):
            request_precompile()
        return self.local_queue_snapshot(session)

    def append_playlist_track(self, playlist: PlaylistManager, path: Path | str) -> dict[str, Any]:
        playlist.append(path)
        return self.playlist_snapshot(playlist)

    def insert_playlist_track(
        self,
        playlist: PlaylistManager,
        index: int,
        path: Path | str,
    ) -> dict[str, Any]:
        playlist.insert(index, path)
        return self.playlist_snapshot(playlist)

    def skip_local_track(self, session: LocalPlaylistSession) -> dict[str, Any]:
        session.skip_current()
        return session.session_snapshot()

    def shuffle_playlist(self, playlist: PlaylistManager) -> dict[str, Any]:
        playlist.shuffle_upcoming()
        return self.playlist_snapshot(playlist)

    def shuffle_local(self, session: LocalPlaylistSession) -> dict[str, Any]:
        session.shuffle_queue()
        return self.local_queue_snapshot(session)

    def set_playlist_repeat(self, playlist: PlaylistManager, enabled: bool) -> dict[str, Any]:
        playlist.set_repeat(enabled)
        return self.playlist_snapshot(playlist)

    def set_local_repeat(self, session: LocalPlaylistSession, enabled: bool) -> dict[str, Any]:
        session.set_repeat(enabled)
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
    def spotify_track_change_timing(new_track, old_track=None) -> dict[str, Any]:
        """Build capture timing data for an immediate Spotify track change."""
        current_song = {
            "song_title": new_track.name,
            "artist": new_track.artist,
            "album": new_track.album,
        }
        timing_data: dict[str, Any] = {
            "song_durations": [new_track.duration_ms / 1000.0],
            "current_playback_time": 0.0,
            "current_song": current_song,
            "songs": [current_song],
        }
        if old_track is not None:
            timing_data["previous_song"] = {
                "song_title": old_track.name,
                "artist": old_track.artist,
                "album": old_track.album,
            }
        return timing_data

    @staticmethod
    def spotify_capture_timing_snapshot(watcher: SpotifyQueueWatcher) -> dict[str, Any] | None:
        """Build the periodic capture timing snapshot from watcher state."""
        snapshot = watcher.snapshot()
        queue = snapshot.get("queue")
        playback_state = snapshot.get("playback_state")
        current = getattr(playback_state, "track", None)
        if current is None:
            current = getattr(queue, "currently_playing", None)
        if current is None:
            return None

        queued_tracks = tuple(
            track
            for track in (getattr(queue, "queue", ()) or ())
            if track.track_id != current.track_id
        )
        tracks = (current, *queued_tracks)
        songs = [
            {
                "song_title": track.name,
                "artist": track.artist,
                "album": track.album,
            }
            for track in tracks
        ]
        return {
            "song_durations": [track.duration_ms / 1000.0 for track in tracks],
            "current_playback_time": (
                getattr(playback_state, "progress_ms", 0) / 1000.0
                if playback_state is not None
                else 0.0
            ),
            "current_song": songs[0],
            "songs": songs,
        }

    @staticmethod
    def _track_row(track: Path | str, *, index: int, current_index: int) -> dict[str, Any]:
        path = Path(track)
        return {
            "index": index,
            "track_key": track_key_for_path(path),
            "display_name": path.name,
            "path": str(path),
            "is_current": index == current_index,
        }
