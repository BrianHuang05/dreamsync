from __future__ import annotations

from pathlib import Path

from dreamsync.show.baked_frames import (
    BakeSettings,
    BakedFrame,
    BakedFrameArtifact,
    BakedFrameNode,
    default_engine_metadata,
)
from dreamsync.show.baked_runtime import BakedFramePlaybackRuntime


class _Adapter:
    def __init__(self):
        self.calls: list[tuple[float, dict[str, str]]] = []

    def send_baked_frame(self, t: float, node_colors: dict[str, str], *, fallback_color="#000000"):
        self.calls.append((t, dict(node_colors)))
        return True


class _NoSpatialAdapter(_Adapter):
    def send_frame(self, *args, **kwargs):
        raise AssertionError("baked playback must not send live frames")

    def prepare_spatial_cue(self, *args, **kwargs):
        raise AssertionError("baked playback must not prepare spatial cues")

    def clear_prepared_spatial_cues(self, *args, **kwargs):
        raise AssertionError("baked playback must not clear spatial cue cache")


def _artifact() -> BakedFrameArtifact:
    return BakedFrameArtifact(
        source_show_path=str(Path("song.show.json")),
        source_show_hash="show-hash",
        device_config_path=str(Path("devices.yaml")),
        device_config_hash="device-hash",
        engine=default_engine_metadata(),
        settings=BakeSettings(fps=2),
        duration=1.0,
        nodes=(
            BakedFrameNode("10.0.0.2#section:0", "10.0.0.2", 0, "section"),
            BakedFrameNode("10.0.0.2#section:1", "10.0.0.2", 1, "section"),
        ),
        frames=(
            BakedFrame(0.0, ("#000000", "#111111")),
            BakedFrame(0.5, ("#222222", "#333333")),
            BakedFrame(1.0, ("#444444", "#555555")),
        ),
        summary={"frame_count": 3, "node_count": 2, "cue_count": 1},
    )


def test_frame_at_uses_nearest_frame():
    runtime = BakedFramePlaybackRuntime(_artifact(), _Adapter())

    assert runtime.frame_at(-1.0).t == 0.0
    assert runtime.frame_at(0.24).t == 0.0
    assert runtime.frame_at(0.26).t == 0.5
    assert runtime.frame_at(10.0).t == 1.0


def test_tick_sends_node_color_map_to_adapter():
    adapter = _Adapter()
    runtime = BakedFramePlaybackRuntime(_artifact(), adapter)

    assert runtime.tick(0.51)

    assert adapter.calls == [(
        0.51,
        {
            "10.0.0.2#section:0": "#222222",
            "10.0.0.2#section:1": "#333333",
        },
    )]
    assert runtime.stats["frames_sent"] == 1
    assert runtime.stats["frame_lookup_count"] == 1
    assert runtime.stats["frame_lookup_avg_ms"] >= 0.0
    assert runtime.stats["frame_lookup_max_ms"] >= 0.0


def test_tick_avoids_live_spatial_resolution_path():
    adapter = _NoSpatialAdapter()
    runtime = BakedFramePlaybackRuntime(_artifact(), adapter)

    for t in (0.0, 0.25, 0.75):
        assert runtime.tick(t)

    assert len(adapter.calls) == 3
    assert runtime.stats["frames_sent"] == 3
    assert runtime.stats["frame_lookup_count"] == 3


def test_empty_artifact_tick_returns_false():
    artifact = _artifact()
    empty = BakedFrameArtifact(
        source_show_path=artifact.source_show_path,
        source_show_hash=artifact.source_show_hash,
        device_config_path=artifact.device_config_path,
        device_config_hash=artifact.device_config_hash,
        engine=artifact.engine,
        settings=artifact.settings,
        duration=artifact.duration,
        nodes=artifact.nodes,
        frames=(),
        summary={},
    )

    assert not BakedFramePlaybackRuntime(empty, _Adapter()).tick(0.0)
