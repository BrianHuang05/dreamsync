from __future__ import annotations

from pathlib import Path

import numpy as np

from dreamsync.analyzer.decode import AudioData
from dreamsync.analyzer.separation import (
    NoOpStemSeparationBackend,
    StemArtifact,
    StemBackendRegistry,
)


class DummyStemBackend:
    @property
    def backend_name(self) -> str:
        return "dummy"

    def separate(self, audio_path: Path, audio: AudioData) -> dict[str, StemArtifact]:
        return {
            "vocals": StemArtifact(
                name="vocals",
                sample_rate=audio.sample_rate,
                path=str(audio_path.with_suffix(".vocals.wav")),
            ),
        }


def test_noop_backend_returns_no_stems():
    backend = NoOpStemSeparationBackend()
    audio = AudioData(
        signal=np.zeros(64, dtype=np.float32),
        sample_rate=44100,
        duration=64 / 44100,
        channels=2,
    )
    assert backend.backend_name == "none"
    assert backend.separate(Path("song.mp3"), audio) == {}


def test_registry_resolves_registered_backend():
    registry = StemBackendRegistry()
    backend = DummyStemBackend()
    registry.register(backend)

    resolved = registry.resolve("dummy")
    assert resolved is backend
    assert registry.names() == ("dummy",)
