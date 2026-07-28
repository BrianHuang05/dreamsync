"""Analyze/compile helper services for the GUI."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
from typing import Any

import numpy as np

from dreamsync.analyzer.decode import decode_mp3
from dreamsync.analyzer.analyze import analyze_song
from dreamsync.analyzer.models import SongStructure
from dreamsync.compiler import compile_show
from dreamsync.show.control_patch import ShowControlPatch, apply_show_control_patch
from dreamsync.show.baked_frames import (
    BakeSettings,
    BakedFrameArtifact,
    BakeValidation,
    validate_baked_frame_file,
)
from dreamsync.show.models import Show, ShowTimeline, ShowTrack
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
        seed: int | None = None,
    ):
        structure = analyze_song(path)
        timeline = self._compile_timeline(structure, profile, seed=seed)
        return apply_show_control_patch(timeline, patch)

    def compile_with_analysis(
        self,
        path: Path,
        *,
        profile=None,
        patch: ShowControlPatch | None = None,
        seed: int | None = None,
    ) -> tuple[SongStructure, ShowTimeline]:
        structure = analyze_song(path)
        timeline = apply_show_control_patch(
            self._compile_timeline(structure, profile, seed=seed),
            patch,
        )
        return structure, timeline

    @staticmethod
    def _compile_timeline(structure, profile, *, seed: int | None) -> ShowTimeline:
        timeline = compile_show(structure, profile, seed=seed)
        if seed is None:
            return timeline
        return replace(
            timeline,
            metadata={**dict(timeline.metadata), "compile_seed": int(seed)},
        )

    def apply_patch(self, timeline, patch: ShowControlPatch | None):
        return apply_show_control_patch(timeline, patch)

    @staticmethod
    def retint_timeline(timeline: ShowTimeline, colors: tuple[str, ...] | list[str]) -> ShowTimeline:
        """Replace a compiled show's generated palette without recompiling its cues.

        This intentionally preserves cue timing, effects, routing, intensity, and
        any cue that was explicitly given its own palette in the Show editor.  It
        also updates generated gradient parameters that mirror a cue's palette.
        """

        palette = tuple(str(color).strip() for color in colors if str(color).strip())
        if not palette:
            raise ValueError("A replacement palette requires at least one color.")

        raw_show_palette = timeline.metadata.get("show_palette")
        previous_show_palette = (
            tuple(str(color) for color in raw_show_palette)
            if isinstance(raw_show_palette, (list, tuple))
            else ()
        )
        retinted_cues: list = []
        for cue in timeline.cues:
            params = dict(cue.params)
            if bool(params.get("palette_override")):
                retinted_cues.append(cue)
                continue

            previous_cue_palette = tuple(cue.color_palette)
            gradient_colors = params.get("gradient_colors")
            if isinstance(gradient_colors, (list, tuple)) and tuple(gradient_colors) in {
                previous_cue_palette,
                previous_show_palette,
            }:
                params["gradient_colors"] = tuple(palette)
            retinted_cues.append(replace(cue, color_palette=palette, params=params))

        metadata = {
            **dict(timeline.metadata),
            "show_palette": list(palette),
            "palette_retinted": True,
        }
        return replace(timeline, cues=tuple(retinted_cues), metadata=metadata)

    def load_timeline(self, path: Path) -> ShowTimeline:
        return ShowTimeline.from_json(path)

    def save_timeline(self, timeline: ShowTimeline, path: Path) -> Path:
        timeline.to_json(path)
        return path

    @staticmethod
    def default_show_directory() -> Path:
        """The one canonical location for saved multi-track Show manifests."""
        project_root = Path(__file__).resolve().parents[4]
        if (project_root / "src" / "dreamsync").is_dir():
            return project_root / "out" / "shows"
        return Path.cwd() / "out" / "shows"

    def new_show(self, name: str = "Untitled Show") -> Show:
        return Show(name=name, tracks=(), metadata={})

    def load_show(self, path: Path) -> Show:
        """Load a multi-track Show or upgrade a legacy one-track timeline."""
        return Show.from_json(path)

    def load_show_manifest(self, path: Path) -> Show:
        """Load an explicit saved Show manifest, rejecting legacy track files."""
        payload = self._load_show_payload(path)
        if payload.get("format") != Show.FORMAT:
            raise ValueError(
                "This is a precompiled single-track lightshow. Use Cue Compiled Track instead."
            )
        return Show.from_dict(payload, legacy_name=Path(path).stem.removesuffix(".show"))

    def load_precompiled_track_show(self, path: Path) -> Show:
        """Load one compiled track timeline without resolving it by audio path.

        Legacy ``.show.json`` timelines are the normal format for individual
        lightshows.  A one-track v2 Show is also accepted, which lets users
        retain a deliberately named single-track Show variant.
        """
        path = Path(path)
        payload = self._load_show_payload(path)
        if payload.get("format") != Show.FORMAT:
            timeline = ShowTimeline.from_dict(payload)
            return Show(
                name=path.name.removesuffix(".show.json"),
                tracks=(
                    ShowTrack(
                        audio_path=timeline.song_path,
                        timeline=timeline,
                        metadata={"title": str(timeline.metadata.get("track_name", ""))},
                    ),
                ),
                metadata={
                    "imported_legacy_timeline": True,
                    "source_show_path": str(path),
                },
            )

        show = Show.from_dict(payload, legacy_name=path.stem.removesuffix(".show"))
        if len(show.tracks) != 1:
            raise ValueError(
                "This saved Show has multiple tracks. Use Load Saved Show instead."
            )
        track = show.tracks[0]
        if not track.is_compiled:
            raise ValueError("The selected track has no compiled lightshow timeline.")
        return show

    def save_show(self, show: Show, path: Path) -> Path:
        show.to_json(path)
        return path

    def validate_baked_artifact(
        self,
        artifact_path: Path,
        *,
        source_show_path: Path,
        device_config_path: Path,
        settings: BakeSettings | None = None,
    ) -> BakeValidation:
        return validate_baked_frame_file(
            artifact_path,
            source_show_path=source_show_path,
            device_config_path=device_config_path,
            settings=settings or BakeSettings(),
        )

    def export_baked_artifact(self, source_path: Path, target_path: Path) -> Path:
        """Export a structurally valid baked artifact without mutating its source."""
        source_path = Path(source_path)
        target_path = Path(target_path)
        artifact = BakedFrameArtifact.from_json(source_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = target_path.with_name(f".{target_path.name}.dreamsync-tmp")
        try:
            artifact.to_json(temporary)
            BakedFrameArtifact.from_json(temporary)
            temporary.replace(target_path)
        finally:
            temporary.unlink(missing_ok=True)
        return target_path

    @staticmethod
    def _load_show_payload(path: Path) -> dict[str, Any]:
        with Path(path).open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError("Show file must contain a JSON object.")
        return payload

    def compile_track(
        self,
        track: ShowTrack,
        *,
        profile=None,
        patch: ShowControlPatch | None = None,
        seed: int | None = None,
    ) -> tuple[SongStructure, ShowTrack]:
        """Compile one pending Track while retaining its display metadata."""
        structure, timeline = self.compile_with_analysis(
            Path(track.audio_path),
            profile=profile,
            patch=patch,
            seed=seed,
        )
        return structure, ShowTrack(
            audio_path=track.audio_path,
            timeline=timeline,
            metadata=dict(track.metadata or {}),
        )

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
