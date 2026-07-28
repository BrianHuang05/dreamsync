"""Queue manager controller."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from dreamsync.gui.models.queue_state import QueueState, QueueTrackState
from dreamsync.gui.services.queue_service import QueueService
from dreamsync.profile_overrides import SongPaletteAssignment


@dataclass
class QueueController:
    service: QueueService
    state: QueueState = QueueState()

    def bind_playlist(
        self,
        playlist: Any,
        *,
        assignments: Mapping[str, SongPaletteAssignment] | None = None,
        source_path: Path | str | None = None,
    ) -> QueueState:
        snapshot = self.service.playlist_snapshot(playlist)
        return self._bind_local_snapshot(
            snapshot,
            assignments=assignments,
            source_path=str(source_path or self.state.playlist_source_path),
            lock_next=False,
            protect_current=False,
        )

    def bind_local_session(
        self,
        session: Any,
        *,
        assignments: Mapping[str, SongPaletteAssignment] | None = None,
    ) -> QueueState:
        snapshot = self.service.local_queue_snapshot(session)
        session_snapshot = session.session_snapshot() if hasattr(session, "session_snapshot") else {}
        position = float(session_snapshot.get("position_seconds", 0.0) or 0.0)
        duration = float(session_snapshot.get("duration_seconds", 0.0) or 0.0)
        current_index = int(snapshot.get("current_index", -1))
        lock_next = bool(
            current_index >= 0
            and duration > 0.0
            and max(0.0, duration - position) <= 10.0
        )
        return self._bind_local_snapshot(
            snapshot,
            assignments=assignments,
            source_path=self.state.playlist_source_path,
            lock_next=lock_next,
            protect_current=True,
        )

    def select_local_track(self, track_key: str) -> QueueState:
        if any(track.track_key == track_key for track in self.state.local_tracks):
            self.state = replace(self.state, selected_track_key=track_key)
        return self.state

    def move_local(self, session: Any, from_index: int, to_index: int) -> QueueState:
        self.service.move_local_track(session, from_index, to_index)
        return self.bind_local_session(session)

    def move_playlist(self, playlist: Any, from_index: int, to_index: int) -> QueueState:
        self.service.move_playlist_track(playlist, from_index, to_index)
        return self.bind_playlist(playlist, source_path=self.state.playlist_source_path)

    def remove_local(self, session: Any, index: int) -> QueueState:
        self.service.remove_local_track(session, index)
        return self.bind_local_session(session)

    def remove_playlist(self, playlist: Any, index: int) -> QueueState:
        self.service.remove_playlist_track(playlist, index)
        return self.bind_playlist(playlist, source_path=self.state.playlist_source_path)

    def skip_current(self, session: Any) -> QueueState:
        self.service.skip_local_track(session)
        return self.bind_local_session(session)

    def play_local_now(self, session: Any, index: int) -> QueueState:
        self.service.play_local_now(session, index)
        return self.bind_local_session(session)

    def play_playlist_now(self, playlist: Any, index: int) -> QueueState:
        self.service.play_playlist_now(playlist, index)
        return self.bind_playlist(playlist, source_path=self.state.playlist_source_path)

    def append_local(self, session: Any, path: Path | str) -> QueueState:
        self.service.append_local_track(session, path)
        return self.bind_local_session(session)

    def insert_local(self, session: Any, index: int, path: Path | str) -> QueueState:
        self.service.insert_local_track(session, index, path)
        return self.bind_local_session(session)

    def append_playlist(self, playlist: Any, path: Path | str) -> QueueState:
        self.service.append_playlist_track(playlist, path)
        return self.bind_playlist(playlist, source_path=self.state.playlist_source_path)

    def insert_playlist(self, playlist: Any, index: int, path: Path | str) -> QueueState:
        self.service.insert_playlist_track(playlist, index, path)
        return self.bind_playlist(playlist, source_path=self.state.playlist_source_path)

    def shuffle_local(self, session: Any) -> QueueState:
        self.service.shuffle_local(session)
        return self.bind_local_session(session)

    def shuffle_playlist(self, playlist: Any) -> QueueState:
        self.service.shuffle_playlist(playlist)
        return self.bind_playlist(playlist, source_path=self.state.playlist_source_path)

    def refresh_spotify(self, *, client=None, watcher=None) -> QueueState:
        snapshot = self.service.spotify_queue_snapshot(client=client, watcher=watcher)
        self.state = replace(
            self.state,
            spotify_current=str(snapshot["current_track"]),
            spotify_upcoming=tuple(str(item) for item in snapshot["queue"]),
        )
        return self.state

    def set_preview_palette(
        self,
        *,
        source_label: str,
        palette_name: str,
        colors: tuple[str, ...],
        source_path: str = "",
        generated_seed: int | None = None,
    ) -> QueueState:
        self.state = replace(
            self.state,
            preview_source_label=source_label,
            preview_palette_name=palette_name,
            preview_colors=tuple(colors),
            preview_source_path=source_path,
            preview_generated_seed=generated_seed,
        )
        return self.state

    def set_status(self, message: str) -> QueueState:
        self.state = replace(self.state, status_message=message)
        return self.state

    def _bind_local_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        assignments: Mapping[str, SongPaletteAssignment] | None,
        source_path: str,
        lock_next: bool,
        protect_current: bool,
    ) -> QueueState:
        current_index = int(snapshot.get("current_index", -1))
        local_tracks = tuple(
            self._track_state_from_row(
                row,
                assignments,
                current_index=current_index,
                lock_next=lock_next,
                protect_current=protect_current,
            )
            for row in snapshot.get("track_rows", ())
        )
        selected_track_key = self.state.selected_track_key
        available_keys = {track.track_key for track in local_tracks}
        if selected_track_key not in available_keys:
            selected_index = current_index if 0 <= current_index < len(local_tracks) else 0
            selected_track_key = local_tracks[selected_index].track_key if local_tracks else ""
        self.state = QueueState(
            current_index=current_index,
            local_tracks=local_tracks,
            selected_track_key=selected_track_key,
            playlist_source_path=source_path,
            preview_source_label=self.state.preview_source_label,
            preview_palette_name=self.state.preview_palette_name,
            preview_colors=self.state.preview_colors,
            preview_source_path=self.state.preview_source_path,
            preview_generated_seed=self.state.preview_generated_seed,
            status_message=self.state.status_message,
            spotify_current=self.state.spotify_current,
            spotify_upcoming=self.state.spotify_upcoming,
        )
        return self.state

    @staticmethod
    def _track_state_from_row(
        row: dict[str, Any],
        assignments: Mapping[str, SongPaletteAssignment] | None,
        *,
        current_index: int,
        lock_next: bool,
        protect_current: bool,
    ) -> QueueTrackState:
        assignment = assignments.get(str(row["track_key"])) if assignments else None
        index = int(row.get("index", -1))
        is_current = bool(row["is_current"]) and protect_current
        is_next = protect_current and index == current_index + 1
        if is_current:
            show_editable = False
            edit_lock_reason = "Currently playing"
        elif is_next and lock_next:
            show_editable = False
            edit_lock_reason = "Locked for the upcoming transition"
        else:
            show_editable = True
            edit_lock_reason = ""
        return QueueTrackState(
            queue_index=index,
            track_key=str(row["track_key"]),
            display_name=str(row["display_name"]),
            path=str(row["path"]),
            is_current=is_current,
            is_next=is_next,
            show_editable=show_editable,
            edit_lock_reason=edit_lock_reason,
            assignment_label=assignment.summary_label if assignment is not None else "",
            assignment_colors=assignment.colors if assignment is not None else (),
        )
