"""SongStructure model — the top-level data model for offline song analysis."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from dreamsync.analyzer.bpm import BeatGrid, TempoRegion
from dreamsync.analyzer.sections import Section


@dataclass(frozen=True)
class SongStructure:
    path: str                           # source file path
    duration: float                     # total duration in seconds
    bpm: float                          # primary global BPM
    time_signature: int                 # beats per bar (4 for 4/4)
    beat_grid: BeatGrid                 # beat + downbeat positions
    tempo_regions: tuple[TempoRegion, ...]  # BPM over time
    sections: tuple[Section, ...]       # structural sections in order
    metadata: dict                      # track_name, artist (if available)

    def to_dict(self) -> dict:
        """Serialise to a JSON-compatible dict."""
        return {
            "path": self.path,
            "duration": round(self.duration, 4),
            "bpm": round(self.bpm, 2),
            "time_signature": self.time_signature,
            "beat_grid": {
                "bpm": round(self.beat_grid.bpm, 2),
                "beat_times": [round(t, 4) for t in self.beat_grid.beat_times],
                "downbeat_times": [round(t, 4) for t in self.beat_grid.downbeat_times],
                "time_signature": self.beat_grid.time_signature,
            },
            "tempo_regions": [
                {
                    "start_t": round(r.start_t, 4),
                    "end_t": round(r.end_t, 4),
                    "bpm": round(r.bpm, 2),
                    "confidence": round(r.confidence, 4),
                }
                for r in self.tempo_regions
            ],
            "sections": [
                {
                    "start_t": round(s.start_t, 4),
                    "end_t": round(s.end_t, 4),
                    "label": s.label,
                    "energy_mean": round(s.energy_mean, 4),
                    "mood": s.mood,
                    "bpm": round(s.bpm, 2),
                    "section_id": s.section_id,
                }
                for s in self.sections
            ],
            "metadata": self.metadata,
        }

    def to_json(self, path: Path) -> None:
        """Write structure to a JSON file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, path: Path) -> SongStructure:
        """Load structure from a JSON file."""
        path = Path(path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> SongStructure:
        """Reconstruct SongStructure from a dict."""
        bg = data["beat_grid"]
        beat_grid = BeatGrid(
            bpm=bg["bpm"],
            beat_times=tuple(bg["beat_times"]),
            downbeat_times=tuple(bg["downbeat_times"]),
            time_signature=bg["time_signature"],
        )
        tempo_regions = tuple(
            TempoRegion(
                start_t=r["start_t"],
                end_t=r["end_t"],
                bpm=r["bpm"],
                confidence=r["confidence"],
            )
            for r in data["tempo_regions"]
        )
        sections = tuple(
            Section(
                start_t=s["start_t"],
                end_t=s["end_t"],
                label=s["label"],
                energy_mean=s["energy_mean"],
                mood=s["mood"],
                bpm=s["bpm"],
                section_id=s["section_id"],
            )
            for s in data["sections"]
        )
        return cls(
            path=data["path"],
            duration=data["duration"],
            bpm=data["bpm"],
            time_signature=data["time_signature"],
            beat_grid=beat_grid,
            tempo_regions=tempo_regions,
            sections=sections,
            metadata=data.get("metadata", {}),
        )
