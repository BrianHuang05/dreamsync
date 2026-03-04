"""Show Timeline model — self-contained show file format for pre-sequenced playback."""

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


@dataclass(frozen=True)
class ShowTimeline:
    """Self-contained show file: beat grid + lighting cues for one song."""

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

    def is_beat(self, t: float, tolerance: float = 0.025) -> bool:
        """Check if time *t* is within *tolerance* of a beat."""
        return _within_tolerance(self.beat_times, t, tolerance)

    def is_downbeat(self, t: float, tolerance: float = 0.025) -> bool:
        """Check if time *t* is within *tolerance* of a downbeat."""
        return _within_tolerance(self.downbeat_times, t, tolerance)


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
