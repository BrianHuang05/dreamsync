"""Export show timelines to baked frame artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import time
from typing import Callable

from dreamsync.output.auto_detect import load_device_config
from dreamsync.output.null_adapter import SimulationMultiAdapter
from dreamsync.show.baked_frames import (
    BakeSettings,
    BakedFrame,
    BakedFrameArtifact,
    BakedFrameNode,
    default_engine_metadata,
    sha256_file,
)
from dreamsync.show.models import ShowTimeline
from dreamsync.show.runtime import ShowPlaybackRuntime

ProgressCallback = Callable[[int, int, float], None]


@dataclass(frozen=True)
class BakeFrameExportResult:
    artifact: BakedFrameArtifact
    output_path: Path | None = None


def bake_show_frames(
    show_path: Path,
    device_config_path: Path,
    *,
    settings: BakeSettings | None = None,
    progress: ProgressCallback | None = None,
) -> BakeFrameExportResult:
    settings = settings or BakeSettings()
    started_at = time.perf_counter()
    show_path = Path(show_path)
    device_config_path = Path(device_config_path)
    timeline = ShowTimeline.from_json(show_path)
    configs = load_device_config(device_config_path)
    adapter = SimulationMultiAdapter.from_configs(configs)
    runtime = ShowPlaybackRuntime(timeline, adapter)
    runtime.prepare_spatial_playback()

    times = _sample_times(timeline.duration, settings.fps)
    frames: list[BakedFrame] = []
    nodes: tuple[BakedFrameNode, ...] | None = None

    total = len(times)
    for index, t in enumerate(times, start=1):
        runtime.tick(t)
        snapshot = adapter.preview_snapshot()
        node_colors = dict(snapshot.get("node_colors", {}))
        if nodes is None:
            nodes = _nodes_from_snapshot(node_colors)
        colors = tuple(node_colors.get(node.key, "#000000") for node in nodes)
        frames.append(BakedFrame(t=t, colors=colors))
        if progress is not None:
            progress(index, total, t)

    if nodes is None:
        nodes = ()

    bake_duration_seconds = time.perf_counter() - started_at
    frame_count = len(frames)
    artifact = BakedFrameArtifact(
        source_show_path=str(show_path),
        source_show_hash=sha256_file(show_path),
        device_config_path=str(device_config_path),
        device_config_hash=sha256_file(device_config_path),
        engine=default_engine_metadata(),
        settings=settings,
        duration=timeline.duration,
        nodes=nodes,
        frames=tuple(frames),
        summary={
            "frame_count": frame_count,
            "node_count": len(nodes),
            "cue_count": len(timeline.cues),
            "bake_duration_seconds": round(bake_duration_seconds, 4),
            "bake_frames_per_second": round(frame_count / bake_duration_seconds, 2) if bake_duration_seconds > 0 else 0.0,
            "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        },
    )
    return BakeFrameExportResult(artifact=artifact)


def export_baked_show_frames(
    show_path: Path,
    device_config_path: Path,
    output_path: Path,
    *,
    settings: BakeSettings | None = None,
    progress: ProgressCallback | None = None,
) -> BakeFrameExportResult:
    result = bake_show_frames(
        show_path,
        device_config_path,
        settings=settings,
        progress=progress,
    )
    output_path = Path(output_path)
    result.artifact.to_json(output_path)
    return BakeFrameExportResult(artifact=result.artifact, output_path=output_path)


def _sample_times(duration: float, fps: int) -> tuple[float, ...]:
    if fps <= 0:
        raise ValueError(f"fps must be positive, got {fps}")
    frame_count = int(duration * fps) + 1
    return tuple(round(index / fps, 4) for index in range(frame_count + 1) if (index / fps) <= duration)


def _nodes_from_snapshot(node_colors: dict[str, str]) -> tuple[BakedFrameNode, ...]:
    return tuple(_node_from_key(key) for key in sorted(node_colors))


def _node_from_key(key: str) -> BakedFrameNode:
    marker = "#section:"
    if marker in key:
        address, raw_index = key.split(marker, 1)
        return BakedFrameNode(
            key=key,
            device_address=address,
            section_index=int(raw_index),
            kind="section",
        )
    return BakedFrameNode(key=key, device_address=key, kind="device")
