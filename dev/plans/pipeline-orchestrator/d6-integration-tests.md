# D6 — Integration Tests

## Prerequisites

- D5 (CLI Integration) — full pipeline wired and accessible via CLI

## Overview

End-to-end integration tests that validate the complete `CaptureOrchestrator` pipeline using synthetic audio. These tests run without real audio devices or FFmpeg by mocking the capture process and verifying the full data flow from PCM input to MP3 output + JSON sidecars.

---

## Implementation Items

### 6.1 Synthetic audio test harness

Create a test helper that generates known PCM data and feeds it through the pipeline:

```python
# dev/tests/helpers/synthetic_audio.py

def generate_sine_pcm(
    frequency: float,
    duration_seconds: float,
    sample_rate: int = 48000,
    channels: int = 2,
) -> bytes:
    """Generate raw PCM s16le bytes of a sine wave."""
    import struct, math
    n_frames = int(duration_seconds * sample_rate)
    samples = []
    for i in range(n_frames):
        val = int(32767 * math.sin(2 * math.pi * frequency * i / sample_rate))
        for _ in range(channels):
            samples.append(val)
    return struct.pack(f"<{len(samples)}h", *samples)


def generate_multi_song_pcm(
    song_durations: list[float],
    frequencies: list[float] | None = None,
    sample_rate: int = 48000,
    channels: int = 2,
) -> bytes:
    """Generate PCM with distinct frequencies per song for split verification."""
    if frequencies is None:
        frequencies = [220 + i * 110 for i in range(len(song_durations))]
    parts = []
    for dur, freq in zip(song_durations, frequencies):
        parts.append(generate_sine_pcm(freq, dur, sample_rate, channels))
    return b"".join(parts)
```

### 6.2 Mock capture process

Replace `CaptureProcessManager` with a mock that yields synthetic PCM from a `BytesIO` stream:

```python
class MockCaptureProcess:
    """Drop-in replacement for CaptureProcessManager that reads from a BytesIO."""

    def __init__(self, pcm_data: bytes) -> None:
        self._data = pcm_data
        self._stream = None

    @property
    def stdout(self):
        return self._stream

    def start(self, device_name=None):
        from io import BytesIO
        self._stream = BytesIO(self._data)

    def stop(self):
        if self._stream:
            self._stream.close()
        return 0

    def is_alive(self):
        return self._stream is not None and not self._stream.closed

    def restart(self, device_name=None):
        self.stop()
        self.start(device_name)
```

### 6.3 Mock encoder process

Replace `EncoderProcess` with a mock that writes raw PCM to a file (no FFmpeg needed):

```python
class MockEncoderProcess:
    """Drop-in replacement for EncoderProcess that writes raw bytes to disk."""

    def __init__(self, output_path: str, **kwargs) -> None:
        self._output_path = output_path
        self._file = None
        self._bytes_written = 0

    @property
    def output_path(self):
        return self._output_path

    def start(self):
        self._file = open(self._output_path, "wb")

    def write(self, data: bytes):
        self._file.write(data)
        self._bytes_written += len(data)

    def close(self):
        if self._file:
            self._file.close()

    def wait(self, timeout=30.0):
        return 0  # success

    def is_alive(self):
        return self._file is not None and not self._file.closed

    @property
    def stderr_output(self):
        return []
```

---

## Test Cases

### E2E: Basic split accuracy

```python
# dev/tests/test_capture_integration.py

class TestBasicSplitAccuracy:
    """Verify that multi-song PCM is split into correct per-song files."""

    def test_three_songs_produce_three_files(self, tmp_path):
        """3 songs of 5s each → 3 output files."""
        durations = [5.0, 5.0, 5.0]
        pcm = generate_multi_song_pcm(durations)
        # ... wire mock capture + mock encoders + orchestrator ...
        # Assert: 3 files created, each ~5s of PCM data

    def test_split_frame_accuracy(self, tmp_path):
        """Verify split happens at exact frame boundary."""
        durations = [3.0, 4.0]
        # Boundary at frame 144000 (3.0s * 48000)
        # Assert: file 0 has exactly 144000 * 4 bytes
        # Assert: file 1 has exactly 192000 * 4 bytes

    def test_uneven_songs(self, tmp_path):
        """Songs of different lengths split correctly."""
        durations = [2.5, 7.0, 1.5, 4.0]
        # Assert: 4 files, each with correct byte count ±1 frame
```

### E2E: Metadata sidecars

```python
class TestMetadataSidecars:

    def test_sidecar_written_per_segment(self, tmp_path):
        """Each MP3 file has a corresponding .json sidecar."""
        # Assert: for each .mp3, a .json file exists
        # Assert: JSON contains startFrame, endFrame, segmentIndex

    def test_sidecar_contains_song_metadata(self, tmp_path):
        """Sidecar includes song title and artist from timing data."""
        # Push timing_data with song info before capture
        # Assert: JSON contains songTitle, artist, album

    def test_sidecar_frame_positions_contiguous(self, tmp_path):
        """endFrame of segment N == startFrame of segment N+1."""
        # Assert: no gaps or overlaps in frame ranges
```

### E2E: Dynamic boundary updates

```python
class TestDynamicBoundaries:

    def test_boundary_update_mid_capture(self, tmp_path):
        """Updating timing data mid-capture changes split points."""
        # Start capture with initial boundaries at [5s, 10s]
        # After 3s of PCM, update boundaries to [4s, 8s]
        # Assert: first split happens at ~4s, not 5s

    def test_track_change_triggers_immediate_split(self, tmp_path):
        """on_track_change() causes an immediate boundary."""
        # Assert: segment boundary created at current frame position

    def test_locked_boundary_not_modified(self, tmp_path):
        """Boundaries within safety margin are not affected by updates."""
        # Assert: boundary within 0.5s of current frame survives replace_future()
```

### E2E: Drift detection

```python
class TestDriftDetection:

    def test_no_correction_under_threshold(self, tmp_path):
        """Drift < 0.1s produces no correction."""
        # Assert: drift_corrections == 0

    def test_correction_applied_over_threshold(self, tmp_path):
        """Drift > 0.5s triggers auto-correction of future boundaries."""
        # Simulate slow capture (fewer frames than wall time expects)
        # Assert: drift_corrections >= 1
        # Assert: future boundaries adjusted
```

### E2E: Failure injection

```python
class TestFailureInjection:

    def test_capture_crash_mid_stream(self, tmp_path):
        """FFmpeg crash mid-capture triggers restart and gap recording."""
        # Mock capture that raises after N chunks
        # Assert: recovery happens, gap recorded
        # Assert: pipeline continues producing output after recovery

    def test_capture_retries_exhausted(self, tmp_path):
        """5 consecutive failures → pipeline drains gracefully."""
        # Mock capture that always raises
        # Assert: pipeline stops without crash
        # Assert: any partial segments are finalized

    def test_encoder_crash_produces_raw_fallback(self, tmp_path):
        """Encoder failure → retry → raw PCM fallback."""
        # Mock encoder that returns exit code 1
        # Assert: .raw file written as fallback

    def test_encoder_skip_on_total_failure(self, tmp_path):
        """Encoder + fallback both fail → segment skipped."""
        # Assert: no output file for that segment
        # Assert: subsequent segments still produced
```

### E2E: Logging verification

```python
class TestLoggingIntegration:

    def test_jsonl_log_written(self, tmp_path):
        """Pipeline run produces a pipeline.jsonl file."""
        # Assert: logs/pipeline.jsonl exists
        # Assert: each line is valid JSON

    def test_key_events_logged(self, tmp_path):
        """Start, segment_complete, and stop events all appear in log."""
        events = [json.loads(l) for l in (tmp_path / "logs/pipeline.jsonl").read_text().splitlines()]
        event_names = [e["event"] for e in events]
        assert "started" in event_names
        assert "segment_complete" in event_names
        assert "stopped" in event_names

    def test_summary_stats_in_stop_event(self, tmp_path):
        """Stop event includes segment count, elapsed time, etc."""
        stop_event = [e for e in events if e["event"] == "stopped"][0]
        assert "segments_completed" in stop_event["data"]
```

### E2E: File naming

```python
class TestFileNaming:

    def test_timestamp_naming(self, tmp_path):
        """Files named YYYY-MM-DD_HH-MM-SS_segment_NNNNNN.mp3."""
        import re
        for f in tmp_path.glob("*.mp3"):
            assert re.match(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_segment_\d{6}\.mp3", f.name)

    def test_metadata_naming(self, tmp_path):
        """Files named YYYY-MM-DD_HH-MM-SS_Artist_-_Title.mp3."""
        # Push timing with artist/title
        # Assert: filename contains sanitized artist and title
```

---

## Test Organization

```
dev/tests/
├── helpers/
│   └── synthetic_audio.py          # PCM generation + mock classes
├── test_orchestrator.py             # Unit tests from D1-D4
└── test_capture_integration.py      # E2E integration tests (D6)
```

Total expected new tests: ~30 integration tests + ~25 unit tests from D1-D4 = ~55 new tests.

---

## Running Tests

```bash
# Unit tests only (fast, no mocking)
python -m pytest dev/tests/test_orchestrator.py -v

# Integration tests (uses mock capture/encoder, no real FFmpeg)
python -m pytest dev/tests/test_capture_integration.py -v

# All capture tests (existing 185 + new ~55)
python -m pytest dev/tests/test_capture*.py dev/tests/test_pcm*.py dev/tests/test_split*.py dev/tests/test_encoder*.py dev/tests/test_boundary*.py dev/tests/test_metadata*.py dev/tests/test_drift*.py dev/tests/test_recovery*.py dev/tests/test_pipeline_logger.py dev/tests/test_timing*.py dev/tests/test_file_namer.py dev/tests/test_segment*.py dev/tests/test_orchestrator.py dev/tests/test_capture_integration.py -v
```

---

## Passing Criteria

- [x] Synthetic 3-song PCM produces exactly 3 output files with correct byte counts
- [x] Split frame accuracy: boundary at frame N means file 0 has exactly N frames of data
- [x] JSON sidecars have contiguous frame ranges (no gaps, no overlaps)
- [x] Sidecar metadata includes song title, artist, album from timing data
- [x] Dynamic boundary updates change split points mid-capture
- [x] Safety-margin-locked boundaries are not modified by updates
- [x] Drift correction adjusts future boundaries when drift > 0.5s
- [x] Capture crash triggers restart with gap recording, pipeline continues
- [x] Encoder crash triggers retry → raw fallback → skip cascade
- [x] Pipeline JSONL log contains start, segment_complete, and stop events
- [x] All 30 integration tests pass
- [x] Existing unit tests still pass
