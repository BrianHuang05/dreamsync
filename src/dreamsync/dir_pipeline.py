"""Directory pipeline — scan capture dir -> analyze -> compile -> play."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from dreamsync.cache import (
    ShowCache,
    cached_compile_show,
    track_id_for_capture,
)
from dreamsync.analyzer.analyze import analyze_song
from dreamsync.capture.scanner import CaptureDirectoryScanner, CaptureTrack

if TYPE_CHECKING:
    from dreamsync.analyzer.models import SongStructure
    from dreamsync.profile import ProfileConfig
    from dreamsync.show.models import ShowTimeline

logger = logging.getLogger(__name__)


@dataclass
class TrackResult:
    track: CaptureTrack
    structure: SongStructure | None = None
    timeline: ShowTimeline | None = None
    error: str | None = None
    from_cache: bool = False


@dataclass
class PipelineResult:
    tracks: list[TrackResult] = field(default_factory=list)
    analyzed: int = 0
    compiled: int = 0
    cache_hits: int = 0
    errors: int = 0


class DirectoryPipeline:
    """Scan a capture directory, analyze songs, compile shows, prepare for playback."""

    def __init__(
        self,
        capture_dir: Path | str,
        *,
        cache: ShowCache,
        profile: ProfileConfig | None = None,
        sample_rate: int = 44100,
        on_progress: Callable[[str, int, int, CaptureTrack], None] | None = None,
    ) -> None:
        self._capture_dir = Path(capture_dir)
        self._cache = cache
        self._profile = profile
        self._sample_rate = sample_rate
        self._on_progress = on_progress
        self._result: PipelineResult | None = None

    def prepare(self) -> PipelineResult:
        """Scan, analyze, and compile all tracks in the capture directory."""
        scanner = CaptureDirectoryScanner(self._capture_dir)
        capture_tracks = scanner.scan()

        result = PipelineResult()

        for i, ct in enumerate(capture_tracks):
            tr = self._process_track(ct)
            result.tracks.append(tr)

            if tr.error:
                result.errors += 1
            elif tr.from_cache:
                result.cache_hits += 1
            else:
                if tr.structure is not None:
                    result.analyzed += 1
                if tr.timeline is not None:
                    result.compiled += 1

            if self._on_progress:
                step = "error" if tr.error else ("cache_hit" if tr.from_cache else "compiled")
                self._on_progress(step, i, len(capture_tracks), ct)

        self._result = result
        return result

    def playable_tracks(self) -> list[Path]:
        """Return MP3 paths for tracks with valid timelines, in scan order."""
        if self._result is None:
            return []
        return [
            tr.track.mp3_path
            for tr in self._result.tracks
            if tr.timeline is not None
        ]

    def _process_track(self, ct: CaptureTrack) -> TrackResult:
        """Analyze and compile a single track."""
        track_id = track_id_for_capture(ct)

        # Check cache first
        if self._cache.has(track_id, self._profile):
            timeline = self._cache.get(track_id, self._profile)
            if timeline is not None:
                logger.info("Cache hit for %s", ct.mp3_path.name)
                return TrackResult(track=ct, timeline=timeline, from_cache=True)

        # Analyze
        try:
            metadata = {}
            if ct.song_title:
                metadata["track_name"] = ct.song_title
            if ct.artist:
                metadata["artist"] = ct.artist
            if ct.album:
                metadata["album"] = ct.album

            structure = analyze_song(
                ct.mp3_path,
                sample_rate=self._sample_rate,
                metadata=metadata or None,
            )
        except Exception as exc:
            logger.warning("Analysis failed for %s: %s", ct.mp3_path.name, exc)
            return TrackResult(track=ct, error=str(exc))

        # Write .analysis.json alongside MP3
        analysis_path = ct.mp3_path.with_suffix(".analysis.json")
        try:
            analysis_path.write_text(
                json.dumps(structure.to_dict(), indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("Failed to write analysis file: %s", exc)

        # Compile
        try:
            timeline, from_cache = cached_compile_show(
                structure,
                self._profile,
                cache=self._cache,
                track_id=track_id,
            )
        except Exception as exc:
            logger.warning("Compilation failed for %s: %s", ct.mp3_path.name, exc)
            return TrackResult(track=ct, structure=structure, error=str(exc))

        return TrackResult(
            track=ct,
            structure=structure,
            timeline=timeline,
            from_cache=from_cache,
        )
