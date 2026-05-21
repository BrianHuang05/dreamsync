"""Optional stem-separation backend interfaces.

The default analyzer path remains heuristic and dependency-light. This module
provides a narrow seam for future offline backends such as Demucs-style stem
separation without coupling the core analysis pipeline to any one tool.
"""

from __future__ import annotations

from dataclasses import dataclass
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
