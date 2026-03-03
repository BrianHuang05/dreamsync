"""Frozen dataclasses for Spotify API responses.

Only the fields DreamSync needs are extracted from the raw JSON.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpotifyTrack:
    """A single Spotify track."""

    track_id: str
    name: str
    artist: str
    album: str
    duration_ms: int
    uri: str


@dataclass(frozen=True)
class PlaybackState:
    """Snapshot of the current Spotify playback."""

    is_playing: bool
    track: SpotifyTrack | None
    progress_ms: int
    timestamp: float  # time.monotonic() when fetched
    device_name: str
    shuffle: bool
    repeat: str  # "off", "track", "context"


@dataclass(frozen=True)
class QueueSnapshot:
    """Snapshot of the Spotify play queue."""

    currently_playing: SpotifyTrack | None
    queue: tuple[SpotifyTrack, ...]
    fetched_at: float  # time.monotonic() when fetched


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def parse_track(data: dict) -> SpotifyTrack:
    """Parse a track dict from the Spotify Web API into a SpotifyTrack.

    Handles missing/null fields gracefully with sensible defaults.
    """
    artists = data.get("artists") or []
    primary_artist = artists[0].get("name", "") if artists else ""
    album_data = data.get("album") or {}

    return SpotifyTrack(
        track_id=data.get("id") or "",
        name=data.get("name") or "",
        artist=primary_artist,
        album=album_data.get("name") or "",
        duration_ms=data.get("duration_ms") or 0,
        uri=data.get("uri") or "",
    )
