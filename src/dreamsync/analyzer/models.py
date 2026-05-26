"""SongStructure model — the top-level data model for offline song analysis."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from dreamsync.analyzer.bpm import BeatGrid, TempoRegion
from dreamsync.analyzer.instruments import InstrumentProxy
from dreamsync.analyzer.phrases import InstrumentEvent, Phrase
from dreamsync.analyzer.sections import Section
from dreamsync.live import EQ_BAND_NAMES

_ZERO_EQ_BANDS: tuple[float, ...] = (0.0,) * len(EQ_BAND_NAMES)


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
    phrases: tuple[Phrase, ...] = ()    # sub-section phrases
    instrument_events: tuple[InstrumentEvent, ...] = ()  # instrument entrance/exit
    instrument_proxies: tuple[InstrumentProxy, ...] = ()  # heuristic phrase-aligned proxies

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
            "phrases": [
                {
                    "start_t": round(p.start_t, 4),
                    "end_t": round(p.end_t, 4),
                    "parent_section_index": p.parent_section_index,
                    "phrase_type": p.phrase_type,
                    "energy_delta": round(p.energy_delta, 4),
                    "has_kick": p.has_kick,
                    "band_energies": [round(v, 4) for v in p.band_energies],
                    "band_ratios": [round(v, 4) for v in p.band_ratios],
                    "band_fluxes": [round(v, 4) for v in p.band_fluxes],
                    "dominant_band": p.dominant_band,
                }
                for p in self.phrases
            ],
            "instrument_events": [
                {
                    "t": round(e.t, 4),
                    "event_type": e.event_type,
                    "confidence": round(e.confidence, 4),
                    "band": e.band,
                }
                for e in self.instrument_events
            ],
            "instrument_proxies": [
                {
                    "start_t": round(proxy.start_t, 4),
                    "end_t": round(proxy.end_t, 4),
                    "parent_section_index": proxy.parent_section_index,
                    "dominant_proxy": proxy.dominant_proxy,
                    "secondary_proxy": proxy.secondary_proxy,
                    "drums": round(proxy.drums, 4),
                    "bass": round(proxy.bass, 4),
                    "vocals": round(proxy.vocals, 4),
                    "harmonic": round(proxy.harmonic, 4),
                    "percussive": round(proxy.percussive, 4),
                    "active_proxies": list(proxy.active_proxies),
                    "pan_center": round(proxy.pan_center, 4),
                    "pan_width": round(proxy.pan_width, 4),
                    "band_pan_centers": [round(value, 4) for value in proxy.band_pan_centers],
                }
                for proxy in self.instrument_proxies
            ],
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
        phrases = tuple(
            Phrase(
                start_t=p["start_t"],
                end_t=p["end_t"],
                parent_section_index=p["parent_section_index"],
                phrase_type=p["phrase_type"],
                energy_delta=p["energy_delta"],
                has_kick=p["has_kick"],
                band_energies=_normalize_band_values(p.get("band_energies")),
                band_ratios=_normalize_band_values(p.get("band_ratios")),
                band_fluxes=_normalize_band_values(p.get("band_fluxes")),
                dominant_band=p.get("dominant_band", ""),
            )
            for p in data.get("phrases", [])
        )
        instrument_events = tuple(
            InstrumentEvent(
                t=e["t"],
                event_type=e["event_type"],
                confidence=e["confidence"],
                band=e.get("band"),
            )
            for e in data.get("instrument_events", [])
        )
        instrument_proxies = tuple(
            InstrumentProxy(
                start_t=proxy["start_t"],
                end_t=proxy["end_t"],
                parent_section_index=proxy["parent_section_index"],
                dominant_proxy=proxy.get("dominant_proxy", ""),
                secondary_proxy=proxy.get("secondary_proxy", ""),
                drums=proxy.get("drums", 0.0),
                bass=proxy.get("bass", 0.0),
                vocals=proxy.get("vocals", 0.0),
                harmonic=proxy.get("harmonic", 0.0),
                percussive=proxy.get("percussive", 0.0),
                active_proxies=tuple(str(name) for name in proxy.get("active_proxies", ()) if str(name)),
                pan_center=proxy.get("pan_center", 0.0),
                pan_width=proxy.get("pan_width", 0.0),
                band_pan_centers=_normalize_band_values(proxy.get("band_pan_centers")),
            )
            for proxy in data.get("instrument_proxies", [])
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
            phrases=phrases,
            instrument_events=instrument_events,
            instrument_proxies=instrument_proxies,
        )


def _normalize_band_values(values: list[float] | tuple[float, ...] | None) -> tuple[float, ...]:
    if not values:
        return _ZERO_EQ_BANDS
    normalized = [float(v) for v in values[: len(EQ_BAND_NAMES)]]
    if len(normalized) < len(EQ_BAND_NAMES):
        normalized.extend([0.0] * (len(EQ_BAND_NAMES) - len(normalized)))
    return tuple(normalized)
