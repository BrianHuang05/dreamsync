from __future__ import annotations

from pathlib import Path

from dreamsync.cli import build_parser, main
from dreamsync.show.baked_frames import BakedFrameArtifact
from dreamsync.show.models import ShowCue, ShowTimeline


def _write_show(path: Path) -> None:
    timeline = ShowTimeline(
        song_path="song.mp3",
        duration=1.0,
        bpm=120.0,
        time_signature=4,
        beat_times=(0.0, 0.5),
        downbeat_times=(0.0,),
        cues=(
            ShowCue(0.0, "solid", ("#00ff00",), 1.0, 0.0, {}, "cut", 0),
        ),
        metadata={},
    )
    timeline.to_json(path)


def _write_config(path: Path) -> None:
    path.write_text(
        "devices:\n"
        "  - name: Test Strip\n"
        "    address: 10.0.0.2\n"
        "    type: lan\n"
        "    segments: 2\n",
        encoding="utf-8",
    )


def test_bake_frames_parser():
    parser = build_parser()
    args = parser.parse_args([
        "bake-frames",
        "song.show.json",
        "--config",
        "devices.yaml",
        "--fps",
        "15",
        "--output",
        "song.show.frames.json",
    ])

    assert args.command == "bake-frames"
    assert args.show_path == Path("song.show.json")
    assert args.config == Path("devices.yaml")
    assert args.fps == 15
    assert args.output == Path("song.show.frames.json")


def test_reactive_live_parser_defaults_to_low_latency_harmonic_hop():
    parser = build_parser()

    args = parser.parse_args(["govee-live", "--duration", "1"])

    assert args.harmonic_hop_multiplier == 1


def test_bake_frames_cli_writes_artifact(tmp_path: Path, capsys):
    show_path = tmp_path / "song.show.json"
    config_path = tmp_path / "devices.yaml"
    output_path = tmp_path / "song.show.frames.json"
    _write_show(show_path)
    _write_config(config_path)

    ret = main([
        "bake-frames",
        str(show_path),
        "--config",
        str(config_path),
        "--output",
        str(output_path),
        "--fps",
        "2",
        "--summary",
    ])

    assert ret == 0
    assert "Baked 3 frame(s)" in capsys.readouterr().out
    artifact = BakedFrameArtifact.from_json(output_path)
    assert artifact.settings.fps == 2
    assert artifact.summary["node_count"] == 2


def test_bake_frames_validate_only_detects_valid_artifact(tmp_path: Path, capsys):
    show_path = tmp_path / "song.show.json"
    config_path = tmp_path / "devices.yaml"
    output_path = tmp_path / "song.show.frames.json"
    _write_show(show_path)
    _write_config(config_path)

    assert main([
        "bake-frames",
        str(show_path),
        "--config",
        str(config_path),
        "--output",
        str(output_path),
        "--fps",
        "1",
    ]) == 0

    ret = main([
        "bake-frames",
        str(show_path),
        "--config",
        str(config_path),
        "--output",
        str(output_path),
        "--fps",
        "1",
        "--validate-only",
    ])

    assert ret == 0
    assert "valid" in capsys.readouterr().out
