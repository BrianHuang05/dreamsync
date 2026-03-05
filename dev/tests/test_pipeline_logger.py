"""Tests for dreamsync.capture.pipeline_logger — structured JSON logging."""

import json
import os

import pytest

from dreamsync.capture.pipeline_logger import PipelineLogger


@pytest.fixture
def log_dir(tmp_path):
    return str(tmp_path / "logs")


@pytest.fixture
def logger(log_dir):
    return PipelineLogger(
        log_dir=log_dir,
        console_level="WARNING",  # suppress console noise during tests
        file_level="DEBUG",
    )


class TestLogCreation:
    def test_creates_log_directory(self, log_dir, logger):
        assert os.path.isdir(log_dir)

    def test_creates_log_file(self, log_dir, logger):
        logger.log("INFO", "test", "hello")
        assert os.path.exists(os.path.join(log_dir, "pipeline.jsonl"))


class TestJsonFormat:
    def test_log_entry_is_valid_json(self, log_dir, logger):
        logger.log("INFO", "capture", "process_started", {"pid": 123}, frame_position=0)

        log_file = os.path.join(log_dir, "pipeline.jsonl")
        with open(log_file) as f:
            lines = f.readlines()

        assert len(lines) >= 1
        entry = json.loads(lines[-1])
        assert entry["level"] == "INFO"
        assert entry["category"] == "capture"
        assert entry["event"] == "process_started"
        assert entry["data"] == {"pid": 123}
        assert entry["frame_position"] == 0
        assert "timestamp" in entry

    def test_multiple_entries(self, log_dir, logger):
        logger.log("INFO", "capture", "start")
        logger.log("WARNING", "timing", "drift")
        logger.log("ERROR", "recovery", "crash")

        log_file = os.path.join(log_dir, "pipeline.jsonl")
        with open(log_file) as f:
            lines = f.readlines()

        assert len(lines) == 3
        for line in lines:
            entry = json.loads(line)
            assert "timestamp" in entry
            assert "level" in entry


class TestConvenienceMethods:
    def test_capture_event(self, log_dir, logger):
        logger.capture_event("started", frame_position=100, pid=42)
        log_file = os.path.join(log_dir, "pipeline.jsonl")
        with open(log_file) as f:
            entry = json.loads(f.readline())
        assert entry["category"] == "capture"
        assert entry["event"] == "started"
        assert entry["frame_position"] == 100

    def test_encoder_event(self, log_dir, logger):
        logger.encoder_event("segment_complete", segment=3)
        log_file = os.path.join(log_dir, "pipeline.jsonl")
        with open(log_file) as f:
            entry = json.loads(f.readline())
        assert entry["category"] == "encoder"

    def test_split_event(self, log_dir, logger):
        logger.split_event("boundary_reached", frame_position=48000)
        log_file = os.path.join(log_dir, "pipeline.jsonl")
        with open(log_file) as f:
            entry = json.loads(f.readline())
        assert entry["category"] == "split"

    def test_boundary_event(self, log_dir, logger):
        logger.boundary_event("added", frame_position=0)
        log_file = os.path.join(log_dir, "pipeline.jsonl")
        with open(log_file) as f:
            entry = json.loads(f.readline())
        assert entry["category"] == "boundary"

    def test_timing_event(self, log_dir, logger):
        logger.timing_event("refresh")
        log_file = os.path.join(log_dir, "pipeline.jsonl")
        with open(log_file) as f:
            entry = json.loads(f.readline())
        assert entry["category"] == "timing"

    def test_recovery_event(self, log_dir, logger):
        logger.recovery_event("capture_restarted", attempt=2)
        log_file = os.path.join(log_dir, "pipeline.jsonl")
        with open(log_file) as f:
            entry = json.loads(f.readline())
        assert entry["category"] == "recovery"
        assert entry["level"] == "WARNING"


class TestLogLevels:
    def test_debug_written_to_file(self, log_dir, logger):
        logger.log("DEBUG", "pipeline", "frame_processed", frame_position=42)
        log_file = os.path.join(log_dir, "pipeline.jsonl")
        with open(log_file) as f:
            entry = json.loads(f.readline())
        assert entry["level"] == "DEBUG"

    def test_frame_position_included(self, log_dir, logger):
        logger.log("INFO", "split", "segment_started", frame_position=999)
        log_file = os.path.join(log_dir, "pipeline.jsonl")
        with open(log_file) as f:
            entry = json.loads(f.readline())
        assert entry["frame_position"] == 999
