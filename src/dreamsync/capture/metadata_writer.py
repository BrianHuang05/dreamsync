"""Write JSON sidecar files alongside output MP3 segments."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class SegmentMetadata:
    """All metadata for a single output segment."""

    start_frame: int = 0
    end_frame: int = 0
    sample_rate: int = 48000
    channels: int = 2
    bitrate: str = "192k"
    segment_index: int = 0
    song_title: str | None = None
    artist: str | None = None
    album: str | None = None
    planned_start_time: str | None = None
    planned_end_time: str | None = None
    source_timing_data: dict | None = None
    output_file: str | None = None
    capture_session_id: str | None = None
    gaps: list[dict] = field(default_factory=list)

    @property
    def segment_duration_frames(self) -> int:
        return self.end_frame - self.start_frame

    @property
    def segment_duration_seconds(self) -> float:
        return self.segment_duration_frames / self.sample_rate


class MetadataWriter:
    """Write JSON sidecar files for output MP3 segments.

    Files are written atomically (temp + rename) to prevent partial
    writes on crash.

    Parameters
    ----------
    output_dir:
        Directory where sidecar files are written.  Defaults to the
        same directory as the MP3.
    """

    def __init__(self, output_dir: str | None = None) -> None:
        self._output_dir = Path(output_dir) if output_dir else None

    def write_sidecar(self, mp3_path: str, metadata: SegmentMetadata) -> str:
        """Write a JSON sidecar file.  Returns the sidecar path."""
        mp3 = Path(mp3_path)
        out_dir = self._output_dir or mp3.parent
        out_dir.mkdir(parents=True, exist_ok=True)

        sidecar_path = out_dir / f"{mp3.stem}.json"
        payload = self._build_payload(metadata)

        # Atomic write: temp file → rename.
        fd, tmp_path = tempfile.mkstemp(
            suffix=".json.tmp", dir=str(out_dir),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, default=str)
            # On Windows, target must not exist for os.replace.
            if sidecar_path.exists():
                sidecar_path.unlink()
            os.replace(tmp_path, str(sidecar_path))
        except BaseException:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

        return str(sidecar_path)

    @staticmethod
    def _build_payload(meta: SegmentMetadata) -> dict:
        return {
            "startFrame": meta.start_frame,
            "endFrame": meta.end_frame,
            "segmentDurationFrames": meta.segment_duration_frames,
            "segmentDurationSeconds": meta.segment_duration_seconds,
            "plannedStartTime": meta.planned_start_time,
            "plannedEndTime": meta.planned_end_time,
            "songTitle": meta.song_title,
            "artist": meta.artist,
            "album": meta.album,
            "segmentIndex": meta.segment_index,
            "sampleRate": meta.sample_rate,
            "channels": meta.channels,
            "bitrate": meta.bitrate,
            "sourceTimingData": meta.source_timing_data,
            "outputFile": meta.output_file,
            "captureSessionId": meta.capture_session_id,
            "gaps": meta.gaps if meta.gaps else None,
        }
