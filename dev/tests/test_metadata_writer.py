"""Tests for dreamsync.capture.metadata_writer — JSON sidecar files."""

import json
import os

import pytest

from dreamsync.capture.metadata_writer import MetadataWriter, SegmentMetadata


@pytest.fixture
def writer(tmp_path):
    return MetadataWriter(output_dir=str(tmp_path))


@pytest.fixture
def meta():
    return SegmentMetadata(
        start_frame=0,
        end_frame=720000,
        sample_rate=48000,
        channels=2,
        bitrate="192k",
        segment_index=0,
        song_title="Time",
        artist="Pink Floyd",
        album="The Dark Side of the Moon",
        planned_start_time="2026-03-04T14:30:00.000Z",
        planned_end_time="2026-03-04T14:30:15.000Z",
        output_file="segment_000001.mp3",
        capture_session_id="abc123",
    )


class TestSegmentMetadata:
    def test_duration_frames(self, meta):
        assert meta.segment_duration_frames == 720000

    def test_duration_seconds(self, meta):
        assert meta.segment_duration_seconds == 15.0

    def test_zero_duration(self):
        m = SegmentMetadata(start_frame=100, end_frame=100)
        assert m.segment_duration_frames == 0
        assert m.segment_duration_seconds == 0.0


class TestWriteSidecar:
    def test_creates_json_file(self, writer, meta, tmp_path):
        mp3_path = str(tmp_path / "segment_000001.mp3")
        result = writer.write_sidecar(mp3_path, meta)
        assert result.endswith(".json")
        assert os.path.exists(result)

    def test_json_is_valid(self, writer, meta, tmp_path):
        mp3_path = str(tmp_path / "segment_000001.mp3")
        result = writer.write_sidecar(mp3_path, meta)
        with open(result) as f:
            data = json.load(f)
        assert isinstance(data, dict)

    def test_required_fields(self, writer, meta, tmp_path):
        mp3_path = str(tmp_path / "segment_000001.mp3")
        result = writer.write_sidecar(mp3_path, meta)
        with open(result) as f:
            data = json.load(f)

        assert data["startFrame"] == 0
        assert data["endFrame"] == 720000
        assert data["segmentDurationFrames"] == 720000
        assert data["segmentDurationSeconds"] == 15.0
        assert data["songTitle"] == "Time"
        assert data["artist"] == "Pink Floyd"
        assert data["album"] == "The Dark Side of the Moon"
        assert data["sampleRate"] == 48000
        assert data["channels"] == 2
        assert data["bitrate"] == "192k"
        assert data["segmentIndex"] == 0
        assert data["captureSessionId"] == "abc123"

    def test_duration_matches(self, writer, tmp_path):
        meta = SegmentMetadata(start_frame=100000, end_frame=580000, sample_rate=48000)
        mp3_path = str(tmp_path / "test.mp3")
        result = writer.write_sidecar(mp3_path, meta)
        with open(result) as f:
            data = json.load(f)
        assert data["segmentDurationFrames"] == 480000
        assert data["segmentDurationSeconds"] == pytest.approx(10.0)

    def test_null_metadata_fields(self, writer, tmp_path):
        meta = SegmentMetadata(start_frame=0, end_frame=100)
        mp3_path = str(tmp_path / "test.mp3")
        result = writer.write_sidecar(mp3_path, meta)
        with open(result) as f:
            data = json.load(f)
        assert data["songTitle"] is None
        assert data["artist"] is None

    def test_sidecar_filename_matches_mp3(self, writer, meta, tmp_path):
        mp3_path = str(tmp_path / "my_song.mp3")
        result = writer.write_sidecar(mp3_path, meta)
        assert os.path.basename(result) == "my_song.json"

    def test_overwrite_existing(self, writer, meta, tmp_path):
        mp3_path = str(tmp_path / "seg.mp3")
        writer.write_sidecar(mp3_path, meta)
        # Write again — should overwrite without error
        result = writer.write_sidecar(mp3_path, meta)
        assert os.path.exists(result)

    def test_creates_output_dir(self, tmp_path, meta):
        new_dir = str(tmp_path / "nested" / "dir")
        w = MetadataWriter(output_dir=new_dir)
        mp3_path = str(tmp_path / "test.mp3")
        result = w.write_sidecar(mp3_path, meta)
        assert os.path.exists(result)

    def test_gaps_field(self, writer, tmp_path):
        meta = SegmentMetadata(
            start_frame=0, end_frame=1000,
            gaps=[{"start": 100, "end": 200}],
        )
        mp3_path = str(tmp_path / "test.mp3")
        result = writer.write_sidecar(mp3_path, meta)
        with open(result) as f:
            data = json.load(f)
        assert data["gaps"] == [{"start": 100, "end": 200}]
