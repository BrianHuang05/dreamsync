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
    assert artifact.frames[0].colors == ("#ff0000", "#ff0000", "#ff0000")
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
