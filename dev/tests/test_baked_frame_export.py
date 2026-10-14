from __future__ import annotations

from pathlib import Path

from dreamsync.show.bake import bake_show_frames, export_baked_show_frames
from dreamsync.show.baked_frames import BakeSettings, BakedFrameArtifact
from dreamsync.show.models import ShowCue, ShowTimeline


def _write_show(path: Path, *, duration: float = 1.0) -> None:
    timeline = ShowTimeline(
        song_path="song.mp3",
        duration=duration,
        bpm=120.0,
        time_signature=4,
        beat_times=tuple(i * 0.5 for i in range(4)),
        downbeat_times=(0.0,),
        cues=(
            ShowCue(
                t=0.0,
                render_mode="solid",
                color_palette=("#ff0000",),
                intensity=1.0,
                speed=0.0,
                params={},
                transition="cut",
                transition_beats=0,
            ),
        ),
        metadata={"track_name": "Bake Test"},
    )
    timeline.to_json(path)


def _write_device_config(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "devices:",
                "  - name: Test Strip",
                "    address: 10.0.0.2",
                "    type: lan",
                "    segments: 3",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_bake_show_frames_captures_section_node_colors(tmp_path: Path):
    show_path = tmp_path / "song.show.json"
    config_path = tmp_path / "devices.yaml"
    _write_show(show_path)
    _write_device_config(config_path)

    result = bake_show_frames(show_path, config_path, settings=BakeSettings(fps=2))
    artifact = result.artifact

    assert artifact.summary["frame_count"] == 3
    assert artifact.summary["node_count"] == 3
    assert artifact.summary["bake_duration_seconds"] >= 0.0
    assert artifact.summary["bake_frames_per_second"] >= 0.0
    assert [node.key for node in artifact.nodes] == [
        "10.0.0.2#section:0",
        "10.0.0.2#section:1",
        "10.0.0.2#section:2",
    ]
    # The default strip role applies its 40% brightness scale before both
    # preview capture and hardware payload generation.
    assert artifact.frames[0].colors == ("#660000", "#660000", "#660000")
    assert artifact.frames[-1].t == 1.0


def test_export_baked_show_frames_writes_artifact(tmp_path: Path):
    show_path = tmp_path / "song.show.json"
    config_path = tmp_path / "devices.yaml"
    output_path = tmp_path / "song.show.frames.json"
    _write_show(show_path)
    _write_device_config(config_path)

    result = export_baked_show_frames(
        show_path,
        config_path,
        output_path,
        settings=BakeSettings(fps=1),
    )

    assert result.output_path == output_path
    loaded = BakedFrameArtifact.from_json(output_path)
    assert loaded.summary["frame_count"] == 2
    assert loaded.source_show_hash == result.artifact.source_show_hash


def test_baking_keeps_dynamic_colors_under_disabled_segments(tmp_path: Path):
    show_path = tmp_path / "song.show.json"
    config_path = tmp_path / "devices.yaml"
    _write_show(show_path)
    _write_device_config(config_path)
    with config_path.open("a", encoding="utf-8") as handle:
        handle.write("    sections:\n      - {index: 1, x: 0, y: 0, z: 0, enabled: false}\n")
    from dreamsync.output.auto_detect import load_device_config
    from dreamsync.output.null_adapter import SimulationMultiAdapter

    artifact = bake_show_frames(show_path, config_path, settings=BakeSettings(fps=1)).artifact
    node_colors = dict(zip((node.key for node in artifact.nodes), artifact.frames[0].colors))
    assert node_colors["10.0.0.2#section:1"] != "#ffffff"
    assert node_colors["10.0.0.2#section:1"] != "#000000"
    adapter = SimulationMultiAdapter.from_configs(load_device_config(config_path))
    adapter.send_baked_frame(0, node_colors)
    assert adapter.preview_snapshot()["node_colors"]["10.0.0.2#section:1"] == "#ffffff"
    adapter.send_baked_frame(0, node_colors, params={
        "runtime_control": {"segment_enabled": (("10.0.0.2#section:1", True),)},
    })
    assert adapter.preview_snapshot()["node_colors"]["10.0.0.2#section:1"] == node_colors["10.0.0.2#section:1"]


def test_baking_keeps_animation_under_static_bulbs(tmp_path):
    from dreamsync.output.auto_detect import load_device_config
    from dreamsync.output.null_adapter import SimulationMultiAdapter
    show_path = tmp_path / "song.show.json"
    config_path = tmp_path / "bulbs.yaml"
    _write_show(show_path)
    config_path.write_text("devices:\n- {name: Bulb, address: bulb, type: lan, segments: 1, enabled: false}\n")
    artifact = bake_show_frames(show_path, config_path, settings=BakeSettings(fps=1)).artifact
    colors = dict(zip((node.key for node in artifact.nodes), artifact.frames[0].colors))
    assert colors["bulb"] == "#ff0000"
    adapter = SimulationMultiAdapter.from_configs(load_device_config(config_path))
    adapter.send_baked_frame(0, colors)
    assert adapter.preview_snapshot()["node_colors"]["bulb"] == "#ffffff"
    adapter.send_baked_frame(0, colors, params={"runtime_control": {"segment_enabled": (("bulb", True),)}})
    assert adapter.preview_snapshot()["node_colors"]["bulb"] == "#ff0000"
