from __future__ import annotations

from pathlib import Path

import pytest

from dreamsync.show.baked_frames import (
    BakeSettings,
    BakedFrame,
    BakedFrameArtifact,
    BakedFrameNode,
    default_engine_metadata,
    default_baked_frame_path,
    sha256_file,
)
from dreamsync.show.baked_runtime import BakedFramePlaybackRuntime
from dreamsync.show.models import ShowCue, ShowTimeline
from dreamsync.show.playback_selector import choose_show_playback_runtime
from dreamsync.show.runtime import ShowPlaybackRuntime


def _timeline(tmp_path: Path) -> ShowTimeline:
    return ShowTimeline(
        song_path=str(tmp_path / "song.mp3"),
        duration=1.0,
        bpm=120.0,
        time_signature=4,
        beat_times=(0.0, 0.5),
        downbeat_times=(0.0,),
        cues=(ShowCue(0.0, "solid", ("#112233",), 1.0, 0.0, {}, "cut", 0),),
        metadata={},
    )


def _write_inputs(tmp_path: Path) -> tuple[ShowTimeline, Path, Path]:
    timeline = _timeline(tmp_path)
    show_path = tmp_path / "song.show.json"
    config_path = tmp_path / "devices.yaml"
    timeline.to_json(show_path)
    config_path.write_text("devices: []\n", encoding="utf-8")
    return timeline, show_path, config_path


def _write_artifact(show_path: Path, config_path: Path) -> Path:
    artifact_path = default_baked_frame_path(show_path)
    artifact = BakedFrameArtifact(
        source_show_path=str(show_path),
        source_show_hash=sha256_file(show_path),
        device_config_path=str(config_path),
        device_config_hash=sha256_file(config_path),
        engine=default_engine_metadata(),
        settings=BakeSettings(),
        duration=1.0,
        nodes=(BakedFrameNode("10.0.0.2", "10.0.0.2"),),
        frames=(BakedFrame(0.0, ("#112233",)),),
        summary={"frame_count": 1, "node_count": 1, "cue_count": 1},
    )
    artifact.to_json(artifact_path)
    return artifact_path


def test_auto_uses_valid_baked_artifact(tmp_path: Path):
    timeline, show_path, config_path = _write_inputs(tmp_path)
    artifact_path = _write_artifact(show_path, config_path)

    choice = choose_show_playback_runtime(
        timeline,
        object(),
        show_path=show_path,
        device_config_path=config_path,
        baked_playback_mode="auto",
    )

    assert isinstance(choice.runtime, BakedFramePlaybackRuntime)
    assert choice.playback_mode_used == "baked"
    assert choice.baked_artifact_path == artifact_path
    assert choice.baked_validation.valid


def test_auto_falls_back_to_live_when_artifact_missing(tmp_path: Path):
    timeline, show_path, config_path = _write_inputs(tmp_path)

    choice = choose_show_playback_runtime(
        timeline,
        object(),
        show_path=show_path,
        device_config_path=config_path,
        baked_playback_mode="auto",
    )

    assert isinstance(choice.runtime, ShowPlaybackRuntime)
    assert choice.playback_mode_used == "live"
    assert choice.baked_validation.reason == "missing_artifact"


def test_require_raises_when_artifact_is_stale(tmp_path: Path):
    timeline, show_path, config_path = _write_inputs(tmp_path)
    _write_artifact(show_path, config_path)
    show_path.write_text(show_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="source_show_hash_changed"):
        choose_show_playback_runtime(
            timeline,
            object(),
            show_path=show_path,
            device_config_path=config_path,
            baked_playback_mode="require",
        )


def test_off_always_uses_live_runtime(tmp_path: Path):
    timeline, show_path, config_path = _write_inputs(tmp_path)
    _write_artifact(show_path, config_path)

    choice = choose_show_playback_runtime(
        timeline,
        object(),
        show_path=show_path,
        device_config_path=config_path,
        baked_playback_mode="off",
    )

    assert isinstance(choice.runtime, ShowPlaybackRuntime)
    assert choice.playback_mode_used == "live"
    assert choice.baked_validation.reason == "baked_playback_off"
