from __future__ import annotations

import json
from pathlib import Path

import pytest

from dreamsync.show.baked_frames import (
    BakeSettings,
    BakedFrame,
    BakedFrameArtifact,
    BakedFrameNode,
    default_engine_metadata,
    sha256_file,
    validate_baked_frame_artifact,
)


def _artifact(tmp_path: Path, *, settings: BakeSettings | None = None) -> BakedFrameArtifact:
    show = tmp_path / "song.show.json"
    config = tmp_path / "devices.yaml"
    show.write_text('{"show": true}', encoding="utf-8")
    config.write_text("devices: []\n", encoding="utf-8")
    return BakedFrameArtifact(
        source_show_path=str(show),
        source_show_hash=sha256_file(show),
        device_config_path=str(config),
        device_config_hash=sha256_file(config),
        engine=default_engine_metadata(),
        settings=settings or BakeSettings(fps=30),
        duration=1.0,
        nodes=(BakedFrameNode("10.0.0.2#section:0", "10.0.0.2", 0, "section"),),
        frames=(BakedFrame(0.0, ("#ff0000",)),),
        summary={"frame_count": 1, "node_count": 1, "cue_count": 1},
    )


def test_baked_frame_artifact_round_trips_json(tmp_path: Path):
    artifact = _artifact(tmp_path)
    path = tmp_path / "song.show.frames.json"

    artifact.to_json(path)
    loaded = BakedFrameArtifact.from_json(path)

    assert loaded == artifact
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["kind"] == "dreamsync_baked_frames"
    assert raw["nodes"][0]["section_index"] == 0


def test_baked_frame_artifact_rejects_schema_mismatch(tmp_path: Path):
    artifact = _artifact(tmp_path)
    data = artifact.to_dict()
    data["schema_version"] = 999

    with pytest.raises(ValueError, match="schema_version"):
        BakedFrameArtifact.from_dict(data)


def test_validation_detects_changed_show_hash(tmp_path: Path):
    artifact = _artifact(tmp_path)
    validation = validate_baked_frame_artifact(
        artifact,
        source_show_hash="changed",
        device_config_hash=artifact.device_config_hash,
        settings=artifact.settings,
        engine=artifact.engine,
    )

    assert not validation.valid
    assert validation.reason == "source_show_hash_changed"
    assert validation.stale_fields == ("source_show_hash",)


def test_validation_detects_changed_device_hash(tmp_path: Path):
    artifact = _artifact(tmp_path)
    validation = validate_baked_frame_artifact(
        artifact,
        source_show_hash=artifact.source_show_hash,
        device_config_hash="changed",
        settings=artifact.settings,
        engine=artifact.engine,
    )

    assert not validation.valid
    assert validation.reason == "device_config_hash_changed"


def test_validation_detects_changed_fps(tmp_path: Path):
    artifact = _artifact(tmp_path)
    validation = validate_baked_frame_artifact(
        artifact,
        source_show_hash=artifact.source_show_hash,
        device_config_hash=artifact.device_config_hash,
        settings=BakeSettings(fps=60),
        engine=artifact.engine,
    )

    assert not validation.valid
    assert validation.reason == "fps_changed"


def test_validation_detects_engine_version_change(tmp_path: Path):
    artifact = _artifact(tmp_path)
    engine = dict(artifact.engine)
    engine["renderer_version"] = 2
    validation = validate_baked_frame_artifact(
        artifact,
        source_show_hash=artifact.source_show_hash,
        device_config_hash=artifact.device_config_hash,
        settings=artifact.settings,
        engine=engine,
    )

    assert not validation.valid
    assert validation.reason == "engine_version_changed"
