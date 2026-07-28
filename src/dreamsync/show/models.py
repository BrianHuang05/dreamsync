"""Track timeline and multi-track Show models for pre-sequenced playback."""

from __future__ import annotations

import bisect
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ShowCue:
    """A single lighting cue in a show timeline."""

    t: float                          # absolute start time in seconds
    render_mode: str                  # "scroll" | "pulse" | "breathe" | "wave" | "solid" | "gradient"
    color_palette: tuple[str, ...]    # hex colors to cycle through
    intensity: float                  # 0.0–1.0
    speed: float                      # effect speed
    params: dict                      # renderer params: pulse_decay, breathe_rate_mult, etc.
    transition: str                   # "cut" | "fade"
    transition_beats: int             # beats to crossfade over (0 for hard cut)
    intensity_start: float | None = None  # if set, intensity ramps from start to intensity


@dataclass(frozen=True)
class ShowTimeline:
    """Compiled lighting timeline for one audio Track.

    ``ShowTimeline`` is retained as the public type for backwards-compatible
    reading of the original single-track ``.show.json`` files.  New saved
    Shows compose one or more of these timelines through :class:`ShowTrack`
    and :class:`Show` below.
    """

    song_path: str                    # original mp3 path (informational)
    duration: float                   # song duration in seconds
    bpm: float                        # global BPM
    time_signature: int               # beats per bar (4 or 3)
    beat_times: tuple[float, ...]     # absolute beat times
    downbeat_times: tuple[float, ...] # bar boundaries
    cues: tuple[ShowCue, ...]         # ordered by t, covering 0..duration
    metadata: dict                    # track_name, artist, etc.

    def __post_init__(self) -> None:
        """Validate the timeline on construction."""
        if self.duration <= 0:
            raise ValueError(f"duration must be positive, got {self.duration}")
        if self.bpm <= 0:
            raise ValueError(f"bpm must be positive, got {self.bpm}")
        if self.time_signature not in (3, 4):
            raise ValueError(f"time_signature must be 3 or 4, got {self.time_signature}")
        if not self.cues:
            raise ValueError("cues must not be empty")
        # Verify cues are sorted by t
        for i in range(1, len(self.cues)):
            if self.cues[i].t < self.cues[i - 1].t:
                raise ValueError(
                    f"cues must be sorted by t: cue[{i}].t={self.cues[i].t} "
                    f"< cue[{i-1}].t={self.cues[i-1].t}"
                )

    def to_dict(self) -> dict:
        """Serialise to a JSON-compatible dict."""
        return {
            "song_path": self.song_path,
            "duration": round(self.duration, 4),
            "bpm": round(self.bpm, 2),
            "time_signature": self.time_signature,
            "beat_times": [round(t, 4) for t in self.beat_times],
            "downbeat_times": [round(t, 4) for t in self.downbeat_times],
            "cues": [
                {
                    "t": round(c.t, 4),
                    "render_mode": c.render_mode,
                    "color_palette": list(c.color_palette),
                    "intensity": round(c.intensity, 4),
                    "speed": round(c.speed, 4),
                    "params": c.params,
                    "transition": c.transition,
                    "transition_beats": c.transition_beats,
                    **({"intensity_start": round(c.intensity_start, 4)} if c.intensity_start is not None else {}),
                }
                for c in self.cues
            ],
            "metadata": self.metadata,
        }

    def to_json(self, path: Path) -> None:
        """Write to a JSON file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, path: Path) -> ShowTimeline:
        """Load from a JSON file."""
        path = Path(path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> ShowTimeline:
        """Reconstruct from a dict."""
        cues = tuple(
            ShowCue(
                t=c["t"],
                render_mode=c["render_mode"],
                color_palette=tuple(c["color_palette"]),
                intensity=c["intensity"],
                speed=c["speed"],
                params=c.get("params", {}),
                transition=c.get("transition", "cut"),
                transition_beats=c.get("transition_beats", 0),
                intensity_start=c.get("intensity_start"),
            )
            for c in data["cues"]
        )
        return cls(
            song_path=data["song_path"],
            duration=data["duration"],
            bpm=data["bpm"],
            time_signature=data["time_signature"],
            beat_times=tuple(data["beat_times"]),
            downbeat_times=tuple(data["downbeat_times"]),
            cues=cues,
            metadata=data.get("metadata", {}),
        )

    def cue_at(self, t: float) -> ShowCue | None:
        """Find the active cue at time *t* (binary search, O(log n)).

        Returns the cue whose ``t`` is <= the query time and whose
        successor's ``t`` is > the query time.  Returns ``None`` if
        *t* is before the first cue.
        """
        if not self.cues:
            return None
        # bisect_right on the cue start times
        cue_times = [c.t for c in self.cues]
        idx = bisect.bisect_right(cue_times, t) - 1
        if idx < 0:
            return None
        return self.cues[idx]

    def is_beat(self, t: float, tolerance: float = 0.040) -> bool:
        """Check if time *t* is within *tolerance* of a beat."""
        return _within_tolerance(self.beat_times, t, tolerance)

    def is_downbeat(self, t: float, tolerance: float = 0.040) -> bool:
        """Check if time *t* is within *tolerance* of a downbeat."""
        return _within_tolerance(self.downbeat_times, t, tolerance)


@dataclass(frozen=True)
class ShowTrack:
    """One ordered audio Track inside a saved :class:`Show`.

    A Track may be uncompiled while it is being arranged.  Once compiled, its
    timeline is embedded in the saved Show so playback is deterministic and
    does not depend on the local compilation cache.
    """

    audio_path: str
    timeline: ShowTimeline | None = None
    metadata: dict | None = None

    def __post_init__(self) -> None:
        if not str(self.audio_path).strip():
            raise ValueError("ShowTrack.audio_path must not be empty")

    @property
    def is_compiled(self) -> bool:
        return self.timeline is not None

    @property
    def display_name(self) -> str:
        title = str((self.metadata or {}).get("title", "")).strip()
        return title or Path(self.audio_path).name

    def to_dict(self) -> dict:
        return {
            "audio_path": self.audio_path,
            "timeline": self.timeline.to_dict() if self.timeline is not None else None,
            "metadata": dict(self.metadata or {}),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ShowTrack":
        raw_timeline = data.get("timeline")
        return cls(
            audio_path=str(data["audio_path"]),
            timeline=(ShowTimeline.from_dict(raw_timeline) if isinstance(raw_timeline, dict) else None),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class Show:
    """A named, ordered playlist of compiled or pending Tracks."""

    name: str
    tracks: tuple[ShowTrack, ...]
    metadata: dict

    FORMAT = "dreamsync.show/v2"

    def __post_init__(self) -> None:
        if not str(self.name).strip():
            raise ValueError("Show.name must not be empty")

    @property
    def is_fully_compiled(self) -> bool:
        return bool(self.tracks) and all(track.is_compiled for track in self.tracks)

    @property
    def duration(self) -> float:
        return sum(track.timeline.duration for track in self.tracks if track.timeline is not None)

    def to_dict(self) -> dict:
        return {
            "format": self.FORMAT,
            "name": self.name,
            "tracks": [track.to_dict() for track in self.tracks],
            "metadata": dict(self.metadata),
        }

    def to_json(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, indent=2, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict, *, legacy_name: str = "Imported Show") -> "Show":
        if data.get("format") == cls.FORMAT:
            return cls(
                name=str(data.get("name") or legacy_name),
                tracks=tuple(ShowTrack.from_dict(entry) for entry in data.get("tracks", ())),
                metadata=dict(data.get("metadata") or {}),
            )

        # The original single-track file remains importable and becomes a
        # one-Track Show.  This keeps existing generated files useful after
        # the terminology migration.
        timeline = ShowTimeline.from_dict(data)
        return cls(
            name=str(timeline.metadata.get("track_name") or legacy_name),
            tracks=(ShowTrack(audio_path=timeline.song_path, timeline=timeline),),
            metadata={"imported_legacy_timeline": True},
        )

    @classmethod
    def from_json(cls, path: Path) -> "Show":
        path = Path(path)
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return cls.from_dict(data, legacy_name=path.stem.removesuffix(".show"))

def _within_tolerance(
    times: tuple[float, ...], t: float, tolerance: float,
) -> bool:
    """Binary-search *times* to check if *t* is within *tolerance* of any entry."""
    if not times:
        return False
    idx = bisect.bisect_left(times, t)
    # Check the closest entries on either side
    for candidate_idx in (idx - 1, idx):
        if 0 <= candidate_idx < len(times):
            if abs(times[candidate_idx] - t) <= tolerance:
                return True
    return False
