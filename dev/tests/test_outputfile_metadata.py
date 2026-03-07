"""Tests for outputFile showing the renamed path in segment metadata."""

import tempfile

import pytest

from dreamsync.capture.orchestrator import CaptureOrchestrator, OrchestratorConfig


class FakeEncoder:
    """Minimal encoder mock."""

    def __init__(self, output_path=""):
        self._output_path = output_path

    def write(self, data):
        pass

    def close(self):
        pass

    def wait(self, timeout=30.0):
        return 0

    @property
    def output_path(self):
        return self._output_path

    @property
    def stderr_output(self):
        return []


class TestOutputFileMetadata:
    """_build_segment_metadata should use explicitly passed output_file."""

    def test_explicit_output_file_overrides_encoder(self):
        """When output_file is passed, it should be used instead of enc.output_path."""
        config = OrchestratorConfig(output_dir=tempfile.mkdtemp())
        orch = CaptureOrchestrator(config=config)

        # Encoder has the temp path
        orch._encoders[0] = FakeEncoder("temp_segment_001.mp3")

        seg_meta = orch._build_segment_metadata(
            segment_index=0,
            start_frame=0,
            end_frame=300_000,
            boundary_meta={"song_title": "Test Song", "artist": "Test Artist"},
            output_file="Artist_-_Song.mp3",
        )

        assert seg_meta.output_file == "Artist_-_Song.mp3"

    def test_fallback_to_encoder_path_when_no_output_file(self):
        """When output_file is None, should fall back to enc.output_path."""
        config = OrchestratorConfig(output_dir=tempfile.mkdtemp())
        orch = CaptureOrchestrator(config=config)

        orch._encoders[0] = FakeEncoder("temp_segment_001.mp3")

        seg_meta = orch._build_segment_metadata(
            segment_index=0,
            start_frame=0,
            end_frame=300_000,
            boundary_meta={"song_title": "Test Song"},
        )

        assert seg_meta.output_file == "temp_segment_001.mp3"

    def test_no_encoder_no_output_file(self):
        """When no encoder and no output_file, output_file should be None."""
        config = OrchestratorConfig(output_dir=tempfile.mkdtemp())
        orch = CaptureOrchestrator(config=config)

        seg_meta = orch._build_segment_metadata(
            segment_index=99,
            start_frame=0,
            end_frame=300_000,
            boundary_meta={"song_title": "Test"},
        )

        assert seg_meta.output_file is None

    def test_empty_string_output_file_used(self):
        """An empty string output_file should be used as-is (truthy check: '' is falsy,
        but since we check 'is None', empty string should be passed through)."""
        config = OrchestratorConfig(output_dir=tempfile.mkdtemp())
        orch = CaptureOrchestrator(config=config)

        orch._encoders[0] = FakeEncoder("temp_segment_001.mp3")

        seg_meta = orch._build_segment_metadata(
            segment_index=0,
            start_frame=0,
            end_frame=300_000,
            boundary_meta={"song_title": "Test"},
            output_file="",
        )

        # Empty string was explicitly passed, so it should be used
        assert seg_meta.output_file == ""

    def test_metadata_fields_populated(self):
        """Metadata fields from boundary_meta should be in the result."""
        config = OrchestratorConfig(output_dir=tempfile.mkdtemp())
        orch = CaptureOrchestrator(config=config)

        seg_meta = orch._build_segment_metadata(
            segment_index=0,
            start_frame=1000,
            end_frame=500_000,
            boundary_meta={
                "song_title": "My Song",
                "artist": "My Artist",
                "album": "My Album",
            },
            output_file="/path/to/My Artist_-_My Song.mp3",
        )

        assert seg_meta.song_title == "My Song"
        assert seg_meta.artist == "My Artist"
        assert seg_meta.album == "My Album"
        assert seg_meta.output_file == "/path/to/My Artist_-_My Song.mp3"
        assert seg_meta.start_frame == 1000
        assert seg_meta.end_frame == 500_000
