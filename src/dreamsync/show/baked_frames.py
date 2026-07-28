"""Baked frame artifact models and validation helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
ARTIFACT_KIND = "dreamsync_baked_frames"
SPATIAL_ENGINE_VERSION = 2
RENDERER_VERSION = 1


@dataclass(frozen=True)
class BakeSettings:
    fps: int = 30
    sample_mode: str = "nearest"
    include_sections: bool = True
    color_space: str = "srgb8"

    def to_dict(self) -> dict[str, Any]:
        return {
            "fps": int(self.fps),
            "sample_mode": self.sample_mode,
            "include_sections": bool(self.include_sections),
            "color_space": self.color_space,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BakeSettings":
        return cls(
            fps=int(data.get("fps", 30)),
            sample_mode=str(data.get("sample_mode", "nearest")),
            include_sections=bool(data.get("include_sections", True)),
            color_space=str(data.get("color_space", "srgb8")),
        )


@dataclass(frozen=True)
class BakedFrameNode:
    key: str
    device_address: str
    section_index: int | None = None
    kind: str = "device"

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "key": self.key,
            "device_address": self.device_address,
            "kind": self.kind,
        }
        if self.section_index is not None:
            data["section_index"] = self.section_index
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BakedFrameNode":
        section_index = data.get("section_index")
        return cls(
            key=str(data["key"]),
            device_address=str(data["device_address"]),
            section_index=int(section_index) if section_index is not None else None,
            kind=str(data.get("kind", "device")),
        )


@dataclass(frozen=True)
class BakedFrame:
    t: float
    colors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"t": round(float(self.t), 4), "colors": list(self.colors)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BakedFrame":
        return cls(t=float(data["t"]), colors=tuple(str(c) for c in data["colors"]))


@dataclass(frozen=True)
class BakeValidation:
    valid: bool
    reason: str = ""
    stale_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class BakedFrameArtifact:
    source_show_path: str
    source_show_hash: str
    device_config_path: str
    device_config_hash: str
    settings: BakeSettings
    duration: float
    nodes: tuple[BakedFrameNode, ...]
    frames: tuple[BakedFrame, ...]
    engine: dict[str, Any]
    summary: dict[str, Any]
    schema_version: int = SCHEMA_VERSION
    kind: str = ARTIFACT_KIND

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "source_show_path": self.source_show_path,
            "source_show_hash": self.source_show_hash,
            "device_config_path": self.device_config_path,
            "device_config_hash": self.device_config_hash,
            "engine": dict(self.engine),
            "settings": self.settings.to_dict(),
            "duration": round(float(self.duration), 4),
            "nodes": [node.to_dict() for node in self.nodes],
            "frames": [frame.to_dict() for frame in self.frames],
            "summary": dict(self.summary),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BakedFrameArtifact":
        schema_version = int(data.get("schema_version", 0))
        if schema_version != SCHEMA_VERSION:
            raise ValueError(f"Unsupported baked frame schema_version: {schema_version}")
        kind = str(data.get("kind", ""))
        if kind != ARTIFACT_KIND:
            raise ValueError(f"Unsupported baked frame kind: {kind}")
        nodes = tuple(BakedFrameNode.from_dict(node) for node in data.get("nodes", ()))
        frames = tuple(BakedFrame.from_dict(frame) for frame in data.get("frames", ()))
        for frame in frames:
            if len(frame.colors) != len(nodes):
                raise ValueError("Baked frame color count must match node count")
        return cls(
            source_show_path=str(data["source_show_path"]),
            source_show_hash=str(data["source_show_hash"]),
            device_config_path=str(data["device_config_path"]),
            device_config_hash=str(data["device_config_hash"]),
            engine=dict(data.get("engine", {})),
            settings=BakeSettings.from_dict(data.get("settings", {})),
            duration=float(data["duration"]),
            nodes=nodes,
            frames=frames,
            summary=dict(data.get("summary", {})),
            schema_version=schema_version,
            kind=kind,
        )

    def to_json(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, path: Path) -> "BakedFrameArtifact":
        with open(path, "r", encoding="utf-8") as handle:
            return cls.from_dict(json.load(handle))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def default_baked_frame_path(show_path: Path) -> Path:
    path = Path(show_path)
    if path.name.endswith(".show.json"):
        return path.with_name(path.name.removesuffix(".show.json") + ".show.frames.json")
    return path.with_suffix(path.suffix + ".frames.json")


def default_engine_metadata() -> dict[str, Any]:
    try:
        from dreamsync import __version__
    except Exception:
        __version__ = "local"
    return {
        "dreamsync_version": __version__ or "local",
        "spatial_engine_version": SPATIAL_ENGINE_VERSION,
        "renderer_version": RENDERER_VERSION,
    }


def validate_baked_frame_artifact(
    artifact: BakedFrameArtifact | None,
    *,
    source_show_hash: str,
    device_config_hash: str,
    settings: BakeSettings,
    engine: dict[str, Any] | None = None,
) -> BakeValidation:
    if artifact is None:
        return BakeValidation(False, "missing_artifact", ("artifact",))
    if artifact.schema_version != SCHEMA_VERSION:
        return BakeValidation(False, "schema_version_unsupported", ("schema_version",))
    if artifact.kind != ARTIFACT_KIND:
        return BakeValidation(False, "kind_unsupported", ("kind",))

    expected_engine = engine or default_engine_metadata()
    stale: list[str] = []
    if artifact.source_show_hash != source_show_hash:
        stale.append("source_show_hash")
    if artifact.device_config_hash != device_config_hash:
        stale.append("device_config_hash")
    if artifact.settings.fps != settings.fps:
        stale.append("fps")
    if artifact.settings.include_sections != settings.include_sections:
        stale.append("include_sections")
    if artifact.settings.sample_mode != settings.sample_mode:
        stale.append("sample_mode")
    if artifact.settings.color_space != settings.color_space:
        stale.append("color_space")
    for key, value in expected_engine.items():
        if artifact.engine.get(key) != value:
            stale.append(key)

    if stale:
        reason = f"{stale[0]}_changed"
        if stale[0] == "spatial_engine_version" or stale[0] == "renderer_version":
            reason = "engine_version_changed"
        if stale[0] == "fps":
            reason = "fps_changed"
        return BakeValidation(False, reason, tuple(stale))
    return BakeValidation(True)


def validate_baked_frame_file(
    artifact_path: Path,
    *,
    source_show_path: Path,
    device_config_path: Path,
    settings: BakeSettings,
    engine: dict[str, Any] | None = None,
) -> BakeValidation:
    path = Path(artifact_path)
    if not path.exists():
        return BakeValidation(False, "missing_artifact", ("artifact",))
    try:
        artifact = BakedFrameArtifact.from_json(path)
    except ValueError as exc:
        if "schema_version" in str(exc):
            return BakeValidation(False, "schema_version_unsupported", ("schema_version",))
        return BakeValidation(False, "invalid_artifact", ("artifact",))
    return validate_baked_frame_artifact(
        artifact,
        source_show_hash=sha256_file(source_show_path),
        device_config_hash=sha256_file(device_config_path),
        settings=settings,
        engine=engine,
    )
