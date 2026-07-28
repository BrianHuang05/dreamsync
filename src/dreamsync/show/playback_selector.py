"""Choose live or baked show playback runtimes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from dreamsync.show.baked_frames import (
    BakeSettings,
    BakedFrameArtifact,
    BakeValidation,
    default_baked_frame_path,
    validate_baked_frame_file,
)
from dreamsync.show.baked_runtime import BakedFramePlaybackRuntime
from dreamsync.show.models import ShowTimeline
from dreamsync.show.runtime import ShowPlaybackRuntime

BakedPlaybackMode = Literal["off", "auto", "require"]


@dataclass(frozen=True)
class PlaybackRuntimeChoice:
    runtime: Any
    playback_mode_used: str
    baked_validation: BakeValidation
    baked_artifact_path: Path | None = None

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "playback_mode_used": self.playback_mode_used,
            "baked_validation_valid": self.baked_validation.valid,
            "baked_validation_reason": self.baked_validation.reason,
            "baked_stale_fields": self.baked_validation.stale_fields,
            "baked_artifact_path": str(self.baked_artifact_path) if self.baked_artifact_path else "",
        }


def choose_show_playback_runtime(
    timeline: ShowTimeline,
    multi_adapter,
    *,
    show_path: Path | None,
    device_config_path: Path | None,
    baked_playback_mode: str = "auto",
    bake_settings: BakeSettings | None = None,
    control_state_getter=None,
) -> PlaybackRuntimeChoice:
    mode = _normalize_mode(baked_playback_mode)
    settings = bake_settings or BakeSettings()
    live_runtime = lambda: ShowPlaybackRuntime(
        timeline,
        multi_adapter,
        control_state_getter=control_state_getter,
    )

    if mode == "off":
        return PlaybackRuntimeChoice(
            runtime=live_runtime(),
            playback_mode_used="live",
            baked_validation=BakeValidation(False, "baked_playback_off", ("mode",)),
        )

    artifact_path = default_baked_frame_path(show_path) if show_path is not None else None
    missing_fields: list[str] = []
    if show_path is None:
        missing_fields.append("source_show_path")
    if device_config_path is None:
        missing_fields.append("device_config_path")
    if artifact_path is None or not artifact_path.exists():
        missing_fields.append("artifact")
    if missing_fields:
        validation = BakeValidation(False, "missing_artifact", tuple(missing_fields))
        if mode == "require":
            raise RuntimeError(_format_required_error(validation, artifact_path))
        return PlaybackRuntimeChoice(
            runtime=live_runtime(),
            playback_mode_used="live",
            baked_validation=validation,
            baked_artifact_path=artifact_path,
        )

    validation = validate_baked_frame_file(
        artifact_path,
        source_show_path=show_path,
        device_config_path=device_config_path,
        settings=settings,
    )
    if not validation.valid:
        if mode == "require":
            raise RuntimeError(_format_required_error(validation, artifact_path))
        return PlaybackRuntimeChoice(
            runtime=live_runtime(),
            playback_mode_used="live",
            baked_validation=validation,
            baked_artifact_path=artifact_path,
        )

    artifact = BakedFrameArtifact.from_json(artifact_path)
    return PlaybackRuntimeChoice(
        runtime=BakedFramePlaybackRuntime(artifact, multi_adapter),
        playback_mode_used="baked",
        baked_validation=validation,
        baked_artifact_path=artifact_path,
    )


def _normalize_mode(mode: str) -> BakedPlaybackMode:
    normalized = str(mode or "auto").strip().lower()
    if normalized not in {"off", "auto", "require"}:
        raise ValueError(f"Unsupported baked playback mode: {mode}")
    return normalized  # type: ignore[return-value]


def _format_required_error(validation: BakeValidation, artifact_path: Path | None) -> str:
    path = str(artifact_path) if artifact_path is not None else "(unknown)"
    reason = validation.reason or "invalid_artifact"
    return f"Baked playback required but artifact is not valid: {reason} ({path})"
