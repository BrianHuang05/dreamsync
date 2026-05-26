"""Optional stem-separation backend interfaces.

The default analyzer path remains heuristic and dependency-light. This module
provides a narrow seam for future offline backends such as Demucs-style stem
separation without coupling the core analysis pipeline to any one tool.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np

from dreamsync.analyzer.decode import AudioData


@dataclass(frozen=True)
class StemArtifact:
    """One separated or externally produced stem."""

    name: str
    sample_rate: int
    path: str | None = None
    signal: np.ndarray | None = None
    kind: str = "stem"
    confidence: float = 0.0
    mask_path: str | None = None
    proxy_envelope: tuple[float, ...] = ()
    pan_hint: float | None = None
    width_hint: float | None = None
    metadata: dict[str, object] = field(default_factory=dict)


class StemSeparationBackend(Protocol):
    """Protocol for optional offline stem-separation backends."""

    @property
    def backend_name(self) -> str:
        ...

    def separate(self, audio_path: Path, audio: AudioData) -> dict[str, StemArtifact]:
        ...


class NoOpStemSeparationBackend:
    """Default backend that intentionally produces no stems."""

    @property
    def backend_name(self) -> str:
        return "none"

    def separate(self, audio_path: Path, audio: AudioData) -> dict[str, StemArtifact]:
        return {}


class StemBackendRegistry:
    """Simple registry for optional separation backends."""

    def __init__(self) -> None:
        self._backends: dict[str, StemSeparationBackend] = {}

    def register(self, backend: StemSeparationBackend) -> None:
        self._backends[backend.backend_name.strip().lower()] = backend

    def resolve(self, name: str) -> StemSeparationBackend | None:
        return self._backends.get(name.strip().lower())

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._backends))


def stem_artifact_metadata(artifact: StemArtifact) -> dict[str, object]:
    data: dict[str, object] = {
        "name": artifact.name,
        "sample_rate": int(artifact.sample_rate),
        "kind": str(artifact.kind or "stem"),
        "confidence": round(float(artifact.confidence), 4),
    }
    if artifact.path is not None:
        data["path"] = artifact.path
    if artifact.mask_path is not None:
        data["mask_path"] = artifact.mask_path
    if artifact.proxy_envelope:
        data["proxy_envelope"] = [round(float(value), 6) for value in artifact.proxy_envelope]
    if artifact.pan_hint is not None:
        data["pan_hint"] = round(float(artifact.pan_hint), 4)
    if artifact.width_hint is not None:
        data["width_hint"] = round(float(artifact.width_hint), 4)
    if artifact.signal is not None:
        data["signal_samples"] = int(getattr(artifact.signal, "shape", [0])[0])
    if artifact.metadata:
        data["metadata"] = dict(artifact.metadata)
    return data
