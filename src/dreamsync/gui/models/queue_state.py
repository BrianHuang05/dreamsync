"""Queue panel state models."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class QueueTrackState:
    track_key: str = ""
    display_name: str = ""
    path: str = ""
    is_current: bool = False
    assignment_label: str = ""
    assignment_colors: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class QueueState:
    current_index: int = 0
    local_tracks: tuple[QueueTrackState, ...] = field(default_factory=tuple)
    selected_track_key: str = ""
    playlist_source_path: str = ""
    preview_source_label: str = ""
    preview_palette_name: str = ""
    preview_colors: tuple[str, ...] = field(default_factory=tuple)
    preview_source_path: str = ""
    preview_generated_seed: int | None = None
    status_message: str = ""
    spotify_current: str = ""
    spotify_upcoming: tuple[str, ...] = field(default_factory=tuple)

    @property
    def tracks(self) -> tuple[str, ...]:
        return tuple(track.display_name for track in self.local_tracks)

    @property
    def selected_track(self) -> QueueTrackState | None:
        for track in self.local_tracks:
            if track.track_key == self.selected_track_key:
                return track
        return None
