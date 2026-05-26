"""Analyze/compile helper services for the GUI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from dreamsync.analyzer.decode import decode_mp3
from dreamsync.analyzer.analyze import analyze_song
from dreamsync.analyzer.models import SongStructure
from dreamsync.compiler import compile_show
from dreamsync.show.control_patch import ShowControlPatch, apply_show_control_patch
from dreamsync.show.models import ShowTimeline
from dreamsync.gui.widgets.show_timeline_view import TimelineSectionMarker


@dataclass(frozen=True)
class ShowEditorTimelineContext:
    duration: float
    waveform: tuple[float, ...] = ()
    sections: tuple[TimelineSectionMarker, ...] = ()


class ShowService:
    """Provide structured song analysis and show compilation helpers."""

    def analyze(self, path: Path) -> Any:
        return analyze_song(path)

    def compile(
        self,
        path: Path,
        *,
        profile=None,
        patch: ShowControlPatch | None = None,
    ):
        structure = analyze_song(path)
        timeline = compile_show(structure, profile)
        return apply_show_control_patch(timeline, patch)

    def compile_with_analysis(
        self,
        path: Path,
        *,
        profile=None,
        patch: ShowControlPatch | None = None,
    ) -> tuple[SongStructure, ShowTimeline]:
        structure = analyze_song(path)
        timeline = apply_show_control_patch(compile_show(structure, profile), patch)
        return structure, timeline

    def apply_patch(self, timeline, patch: ShowControlPatch | None):
        return apply_show_control_patch(timeline, patch)

    def load_timeline(self, path: Path) -> ShowTimeline:
        return ShowTimeline.from_json(path)

    def save_timeline(self, timeline: ShowTimeline, path: Path) -> Path:
        timeline.to_json(path)
        return path

    def build_timeline_context(
        self,
        *,
        audio_path: Path | None,
        timeline: ShowTimeline | None,
        structure: SongStructure | None = None,
    ) -> ShowEditorTimelineContext:
        duration = float(timeline.duration) if timeline is not None else 0.0
        waveform: tuple[float, ...] = ()
        sections: tuple[TimelineSectionMarker, ...] = ()
        resolved_audio_path = Path(audio_path) if audio_path is not None else None
        resolved_structure = structure or self._load_or_analyze_structure(resolved_audio_path)
        if resolved_structure is not None:
            duration = max(duration, float(resolved_structure.duration))
            sections = self._sections_from_structure(resolved_structure)
        if timeline is not None:
            editor_sections = self._sections_from_metadata(timeline.metadata)
            if editor_sections:
                sections = editor_sections
        if resolved_audio_path is not None and resolved_audio_path.exists():
            waveform = self._build_waveform(resolved_audio_path)
        return ShowEditorTimelineContext(duration=duration, waveform=waveform, sections=sections)

    def _sections_from_structure(self, structure: SongStructure) -> tuple[TimelineSectionMarker, ...]:
        return tuple(
            TimelineSectionMarker(
                start_t=float(section.start_t),
                end_t=float(section.end_t),
                label=str(section.label),
            )
            for section in structure.sections
        )

    def _sections_from_metadata(self, metadata: dict[str, Any] | None) -> tuple[TimelineSectionMarker, ...]:
        if not isinstance(metadata, dict):
            return ()
        raw_sections = metadata.get("editor_sections")
        if not isinstance(raw_sections, list):
            return ()
        sections: list[TimelineSectionMarker] = []
        for entry in raw_sections:
            if not isinstance(entry, dict):
                continue
            try:
                start_t = float(entry.get("start_t", 0.0))
                end_t = float(entry.get("end_t", start_t))
            except (TypeError, ValueError):
                continue
            label = str(entry.get("label", "Section")).strip() or "Section"
            sections.append(TimelineSectionMarker(start_t=start_t, end_t=end_t, label=label))
        return tuple(sections)

    def _load_or_analyze_structure(self, audio_path: Path | None) -> SongStructure | None:
        if audio_path is None or not audio_path.exists():
            return None
        sidecar = audio_path.with_suffix(".analysis.json")
        if sidecar.exists():
            try:
                return SongStructure.from_json(sidecar)
            except Exception:
                pass
        try:
            return analyze_song(audio_path)
        except Exception:
            return None

    def _build_waveform(self, audio_path: Path, *, points: int = 720) -> tuple[float, ...]:
        try:
            audio = decode_mp3(audio_path, target_sr=8000)
        except Exception:
            return ()
        signal = np.asarray(audio.signal, dtype=np.float32)
        if signal.size == 0:
            return ()
        chunks = np.array_split(np.abs(signal), min(points, signal.size))
        values = np.asarray(
            [float(chunk.max()) if chunk.size else 0.0 for chunk in chunks],
            dtype=np.float32,
        )
        peak = float(values.max()) if values.size else 0.0
        if peak <= 0.0:
            return tuple(0.0 for _ in values)
        return tuple(float(value / peak) for value in values)
