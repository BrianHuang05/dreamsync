"""Composite song-boundary detector merging Spotify, silence, and crossfade signals."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable

from dreamsync.live import CrossfadeBoundaryDetector, CrossfadeConfig, SongBoundaryDetector

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BoundaryEvent:
    timestamp: float              # seconds into the stream
    source: str                   # "spotify" | "silence" | "crossfade"
    track_name: str | None        # from Spotify, if available
    artist: str | None            # from Spotify, if available
    confidence: float             # 0.0–1.0


class CompositeBoundaryDetector:
    """Wraps silence and crossfade detectors, plus Spotify track-change signals.

    Fires at most one boundary per ``min_song_seconds`` cooldown.  When a
    Spotify signal arrives, audio-based boundaries within ±``debounce_seconds``
    are suppressed to avoid double-fires.
    """

    def __init__(
        self,
        hop_size: int = 512,
        sample_rate: int = 44100,
        min_song_seconds: float = 30.0,
        debounce_seconds: float = 3.0,
        on_boundary: Callable[[BoundaryEvent], None] | None = None,
    ) -> None:
        self._hop_size = hop_size
        self._sample_rate = sample_rate
        self._min_song_seconds = min_song_seconds
        self._debounce_seconds = debounce_seconds
        self._on_boundary = on_boundary

        self._silence_det = SongBoundaryDetector(
            hop_size=hop_size,
            sample_rate=sample_rate,
            min_song_seconds=min_song_seconds,
            cooldown_seconds=0.0,  # we manage cooldown at composite level
        )
        self._crossfade_det = CrossfadeBoundaryDetector(
            config=CrossfadeConfig(min_song_seconds=min_song_seconds),
            hop_size=hop_size,
            sample_rate=sample_rate,
        )

        self._last_boundary_t: float = -1e9
        self._pending_spotify: BoundaryEvent | None = None
        self._lock = threading.Lock()  # protects _pending_spotify
        self._boundary_count: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def notify_track_changed(self, new_track, old_track) -> None:
        """Called by SpotifyQueueWatcher (possibly from another thread).

        Sets a pending Spotify boundary that will be fired on the next
        ``update()`` call.
        """
        with self._lock:
            self._pending_spotify = BoundaryEvent(
                timestamp=0.0,  # will be replaced with actual stream time
                source="spotify",
                track_name=getattr(new_track, "name", None),
                artist=getattr(new_track, "artist", None),
                confidence=1.0,
            )

    def update(self, features: dict, t: float) -> BoundaryEvent | None:
        """Feed audio features for one frame.  Returns BoundaryEvent if fired.

        ``features`` should contain at least ``rms``; for crossfade detection
        it should also contain ``bpm``, ``centroid``, ``bass_ratio``,
        ``energy``, ``onset_strength``, ``beat``.
        """
        cooldown_ok = (t - self._last_boundary_t) >= self._min_song_seconds

        # 1. Check for pending Spotify signal (highest priority)
        with self._lock:
            pending = self._pending_spotify
            self._pending_spotify = None

        if pending is not None and cooldown_ok:
            event = BoundaryEvent(
                timestamp=t,
                source="spotify",
                track_name=pending.track_name,
                artist=pending.artist,
                confidence=1.0,
            )
            self._fire(event)
            return event

        # 2. Silence detector
        rms = features.get("rms", 0.0)
        silence_fired = self._silence_det.update(rms)
        if silence_fired and cooldown_ok and not self._near_spotify(t):
            event = BoundaryEvent(
                timestamp=t,
                source="silence",
                track_name=None,
                artist=None,
                confidence=0.8,
            )
            self._fire(event)
            return event

        # 3. Crossfade detector
        crossfade_fired = self._crossfade_det.update(
            bpm=features.get("bpm", 0.0),
            centroid=features.get("centroid", 0.0),
            bass_ratio=features.get("bass_ratio", 0.0),
            energy=features.get("energy", 0.0),
            onset_strength=features.get("onset_strength", 0.0),
            beat=features.get("beat", False),
            t=t,
        )
        if crossfade_fired and cooldown_ok and not self._near_spotify(t):
            event = BoundaryEvent(
                timestamp=t,
                source="crossfade",
                track_name=None,
                artist=None,
                confidence=0.6,
            )
            self._fire(event)
            return event

        return None

    def reset(self) -> None:
        """Reset all sub-detectors and internal state."""
        self._silence_det = SongBoundaryDetector(
            hop_size=self._hop_size,
            sample_rate=self._sample_rate,
            min_song_seconds=self._min_song_seconds,
            cooldown_seconds=0.0,
        )
        self._crossfade_det.reset()
        self._last_boundary_t = -1e9
        with self._lock:
            self._pending_spotify = None
        self._boundary_count = 0

    @property
    def boundary_count(self) -> int:
        return self._boundary_count

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fire(self, event: BoundaryEvent) -> None:
        self._last_boundary_t = event.timestamp
        self._boundary_count += 1
        # Reset sub-detectors after boundary so they start fresh
        self._silence_det = SongBoundaryDetector(
            hop_size=self._hop_size,
            sample_rate=self._sample_rate,
            min_song_seconds=self._min_song_seconds,
            cooldown_seconds=0.0,
        )
        self._crossfade_det.reset()
        logger.info(
            "Boundary #%d fired: source=%s track=%s artist=%s t=%.1f",
            self._boundary_count,
            event.source,
            event.track_name,
            event.artist,
            event.timestamp,
        )
        if self._on_boundary:
            self._on_boundary(event)

    def _near_spotify(self, t: float) -> bool:
        """True if a Spotify boundary fired within ±debounce window of *t*."""
        return abs(t - self._last_boundary_t) < self._debounce_seconds
