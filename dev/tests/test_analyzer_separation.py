from __future__ import annotations

from pathlib import Path

import numpy as np

from dreamsync.analyzer.decode import AudioData
from dreamsync.analyzer.separation import (
    NoOpStemSeparationBackend,
    StemArtifact,
    StemBackendRegistry,
    stem_artifact_metadata,
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


def test_stem_artifact_metadata_preserves_mixed_source_hints():
    artifact = StemArtifact(
        name="vocals_hint",
        sample_rate=44100,
        kind="enhancement",
        confidence=0.72,
        proxy_envelope=(0.1, 0.4, 0.8),
        pan_hint=0.22,
        width_hint=0.31,
        metadata={"source": "mixed"},
    )

    data = stem_artifact_metadata(artifact)
    assert data["name"] == "vocals_hint"
    assert data["kind"] == "enhancement"
    assert data["confidence"] == 0.72
    assert data["proxy_envelope"] == [0.1, 0.4, 0.8]
    assert data["pan_hint"] == 0.22
    assert data["width_hint"] == 0.31
    assert data["metadata"]["source"] == "mixed"
