"""Show Cache — file-based cache for compiled ShowTimelines.

Provides:
- profile_fingerprint(): deterministic hex fingerprint from ProfileConfig content
- ShowCache: file-based KV store keyed by (track_id, profile_fingerprint)
- cached_compile_show(): cache-aware wrapper around compile_show()
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from dreamsync.profile import ProfileConfig
from dreamsync.show.models import ShowTimeline

if TYPE_CHECKING:
    from dreamsync.analyzer.models import SongStructure

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# D4.1 — Profile Fingerprint
# ---------------------------------------------------------------------------

_NONE_FINGERPRINT = "00000000"


def profile_fingerprint(profile: ProfileConfig | None) -> str:
    """Compute an 8-char hex fingerprint of a profile's compilation-relevant content.

    Only fields that affect compile_show() output are included:
    name, palettes, moods (palettes/effects/params), and transitions.

    Returns "00000000" for None (built-in defaults).
    """
    if profile is None:
        return _NONE_FINGERPRINT

    canonical = {
        "name": profile.name,
        "palettes": _canonical_palettes(profile.palettes),
        "moods": _canonical_moods(profile.moods),
        "eq_routes": _canonical_eq_routes(profile.eq_routes),
        "instrument_routes": _canonical_instrument_routes(profile.instrument_routes),
        "transitions": _canonical_transitions(profile.transitions),
    }
    json_str = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(json_str.encode("utf-8")).hexdigest()[:8]


def _canonical_palettes(palettes: dict[str, tuple[str, ...]]) -> list:
    """Sort palettes by name; preserve color order within each palette."""
    return sorted(
        [[name, list(colors)] for name, colors in palettes.items()]
    )


def _canonical_moods(moods: dict) -> list:
    """Sort moods by name; sort each mood's palettes, effects, and params."""
    result = []
    for mood_name in sorted(moods):
        mood = moods[mood_name]
        result.append([
            mood_name,
            sorted(mood.palettes),
            sorted([[e.name, e.weight] for e in mood.effects]),
            sorted(mood.params.items()),
            _canonical_eq_routes(mood.eq_routes),
            _canonical_instrument_routes(mood.instrument_routes),
        ])
    return result


def _canonical_eq_routes(routes: tuple) -> list:
    return sorted([
        [
            route.band,
            route.when,
            route.color_bias,
            route.render_mode,
            route.spatial_preset,
            route.intensity_boost,
        ]
        for route in routes
    ])


def _canonical_instrument_routes(routes: tuple) -> list:
    return sorted([
        [
            route.instrument,
            route.when,
            route.color_bias,
            route.render_mode,
            route.spatial_preset,
            route.spatial_zone,
            route.pan_follow,
            route.width_scale,
            route.confidence_min,
            route.intensity_boost,
        ]
        for route in routes
    ])


def _canonical_transitions(transitions: tuple) -> list:
    """Sort transitions by (from_mood, to_mood, palette) for determinism."""
    return sorted(
        [[r.from_mood, r.to_mood, r.palette] for r in transitions]
    )


# ---------------------------------------------------------------------------
# D4.2 — ShowCache Core Class
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CacheEntry:
    """Metadata about one cached show (for listing/display)."""
    track_id: str
    profile_fingerprint: str
    profile_name: str
    track_name: str
    artist: str
    duration: float
    compiled_at: str
    file_path: Path
    file_size: int


@dataclass(frozen=True)
class CacheStats:
    """Aggregate cache statistics."""
    entry_count: int
    track_count: int
    total_bytes: int
    cache_dir: Path


class ShowCache:
    """File-based cache for compiled ShowTimelines.

    On-disk layout:
        {cache_dir}/{track_id}/{profile_fingerprint}.show.json
    """

    def __init__(self, cache_dir: Path | str = "~/.dreamsync/cache") -> None:
        self._cache_dir = Path(cache_dir).expanduser().resolve()
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir

    def get(self, track_id: str, profile: ProfileConfig | None = None) -> ShowTimeline | None:
        fp = profile_fingerprint(profile)
        path = self._entry_path(track_id, fp)
        if not path.exists():
            return None
        return self._read_timeline(path)

    def put(self, track_id: str, timeline: ShowTimeline, profile: ProfileConfig | None = None) -> Path:
        fp = profile_fingerprint(profile)
        path = self._entry_path(track_id, fp)
        self._write_timeline(path, timeline, profile)
        return path

    def has(self, track_id: str, profile: ProfileConfig | None = None) -> bool:
        fp = profile_fingerprint(profile)
        return self._entry_path(track_id, fp).exists()

    def entry_path(self, track_id: str, profile: ProfileConfig | None = None) -> Path:
        """Return the on-disk path used for a track/profile cache entry."""

        return self._entry_path(track_id, profile_fingerprint(profile))

    def invalidate(self, track_id: str) -> int:
        track_dir = self._track_dir(track_id)
        if not track_dir.exists():
            return 0
        count = 0
        for entry in track_dir.iterdir():
            if entry.name.endswith(".show.json"):
                entry.unlink()
                count += 1
        try:
            track_dir.rmdir()
        except OSError:
            pass
        return count

    def clear(self) -> int:
        count = 0
        if not self._cache_dir.exists():
            return 0
        for track_dir in self._cache_dir.iterdir():
            if track_dir.is_dir():
                for entry in track_dir.iterdir():
                    if entry.name.endswith(".show.json"):
                        entry.unlink()
                        count += 1
                try:
                    track_dir.rmdir()
                except OSError:
                    pass
        return count

    def list_entries(self) -> list[CacheEntry]:
        entries = []
        if not self._cache_dir.exists():
            return entries
        for track_dir in self._cache_dir.iterdir():
            if not track_dir.is_dir():
                continue
            track_id = track_dir.name
            for show_file in track_dir.iterdir():
                if not show_file.name.endswith(".show.json"):
                    continue
                fp = show_file.stem.replace(".show", "")
                timeline = self._read_timeline(show_file)
                if timeline is None:
                    continue
                meta = timeline.metadata
                entries.append(CacheEntry(
                    track_id=track_id,
                    profile_fingerprint=fp,
                    profile_name=meta.get("_cache_profile_name", "unknown"),
                    track_name=meta.get("track_name", "unknown"),
                    artist=meta.get("artist", "unknown"),
                    duration=timeline.duration,
                    compiled_at=meta.get("_cache_compiled_at", "unknown"),
                    file_path=show_file.resolve(),
                    file_size=show_file.stat().st_size,
                ))
        entries.sort(key=lambda e: e.track_name.lower())
        return entries

    def stats(self) -> CacheStats:
        entries = self.list_entries()
        track_ids = {e.track_id for e in entries}
        total_bytes = sum(e.file_size for e in entries)
        return CacheStats(
            entry_count=len(entries),
            track_count=len(track_ids),
            total_bytes=total_bytes,
            cache_dir=self._cache_dir,
        )

    # -- Internal helpers --

    def _entry_path(self, track_id: str, fp: str) -> Path:
        return self._cache_dir / track_id / f"{fp}.show.json"

    def _track_dir(self, track_id: str) -> Path:
        return self._cache_dir / track_id

    def _read_timeline(self, path: Path) -> ShowTimeline | None:
        try:
            return ShowTimeline.from_json(path)
        except Exception as exc:
            logger.warning("Corrupt cache file, removing: %s (%s)", path, exc)
            path.unlink(missing_ok=True)
            return None

    def _write_timeline(self, path: Path, timeline: ShowTimeline, profile: ProfileConfig | None) -> None:
        data = timeline.to_dict()
        data["metadata"] = dict(data.get("metadata", {}))
        data["metadata"]["_cache_profile_name"] = profile.name if profile else "(built-in defaults)"
        data["metadata"]["_cache_profile_fp"] = profile_fingerprint(profile)
        data["metadata"]["_cache_compiled_at"] = datetime.now(timezone.utc).isoformat()

        path.parent.mkdir(parents=True, exist_ok=True)

        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp_path, path)


# ---------------------------------------------------------------------------
# D4.3 — cached_compile_show + path_based_track_id
# ---------------------------------------------------------------------------

def _sanitize_track_id(track_id: str) -> str:
    """Ensure track_id is safe for use as a directory name."""
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", track_id)
    sanitized = sanitized[:128]
    if not sanitized or sanitized == "_" * len(sanitized):
        raise ValueError(f"Invalid track_id: {track_id!r} (empty after sanitization)")
    return sanitized


def sidecar_track_id(song_title: str, artist: str) -> str:
    """Cache-stable ID from sidecar metadata. Same song -> same ID across captures."""
    title = song_title.strip().lower()
    art = artist.strip().lower()
    if not title and not art:
        raise ValueError("Both song_title and artist are empty/whitespace")
    key = f"{art}:{title}"
    return f"sidecar_{hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]}"


def track_id_for_capture(capture_track) -> str:
    """Compute a cache track ID for a CaptureTrack.

    Uses sidecar metadata (title+artist) when available for cross-capture
    cache stability. Falls back to path-based ID otherwise.
    """
    if capture_track.song_title and capture_track.artist:
        return sidecar_track_id(capture_track.song_title, capture_track.artist)
    return path_based_track_id(capture_track.mp3_path)


def path_based_track_id(file_path: Path | str) -> str:
    """Generate a cache-safe track ID from a file path.

    Uses the filename stem (sanitized) + a short hash of the absolute path.
    Example: 'my_song_a1b2c3d4'
    """
    path = Path(file_path).resolve()
    stem = re.sub(r"[^a-zA-Z0-9_-]", "_", path.stem)
    if not stem or stem == "_":
        stem = "unknown"
    path_hash = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:8]
    return f"{stem}_{path_hash}"


def cached_compile_show(
    structure: SongStructure,
    profile: ProfileConfig | None = None,
    *,
    cache: ShowCache,
    track_id: str,
    **compile_kwargs,
) -> tuple[ShowTimeline, bool]:
    """Compile a show, using the cache when possible.

    Returns:
        (timeline, from_cache) — the compiled show and whether it was a cache hit.
    """
    track_id = _sanitize_track_id(track_id)

    # 1. Check cache
    timeline = cache.get(track_id, profile)
    if timeline is not None:
        logger.info("Cache HIT for track=%s profile_fp=%s",
                     track_id, profile_fingerprint(profile))
        return (timeline, True)

    # 2. Cache miss — compile
    logger.info("Cache MISS for track=%s profile_fp=%s — compiling",
                 track_id, profile_fingerprint(profile))
    from dreamsync.compiler.compile import compile_show
    timeline = compile_show(structure, profile, **compile_kwargs)

    # 3. Store in cache
    cache.put(track_id, timeline, profile)

    return (timeline, False)
