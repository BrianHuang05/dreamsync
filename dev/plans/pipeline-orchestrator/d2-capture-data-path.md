# D2 — Capture Data Path

## Prerequisites

- D1 (Orchestrator Core) — `CaptureOrchestrator` shell with lifecycle methods

## Overview

Wire the PCM data pipeline inside `CaptureOrchestrator`:

```
CaptureProcessManager.stdout
        │
        ▼
  pcm_reader.read_chunks()     ← producer thread
        │
        ▼
  AudioBuffer.put(chunk)
        │
        ▼
  AudioBuffer.get()            ← consumer thread
        │
        ▼
  SplitProcessor.process_chunk(chunk)
        │
        ├──► EncoderProcess[N].write(pcm)
        │         │
        │         ▼
        │    song_N.mp3  (FileNamer provides path)
        │
        └──► MetadataWriter.write_sidecar()
```

---

## Implementation Items

### 2.1 Producer thread

Runs in `CaptureOrchestrator._producer_loop()`:

```python
def _producer_loop(self) -> None:
    """Read PCM from FFmpeg stdout and feed into AudioBuffer."""
    try:
        for chunk in read_chunks(self._capture.stdout, self._config.chunk_ms):
            self._buffer.put(chunk)
            self._recovery.reset_capture_failure_count()
    except Exception:
        # Capture failure — delegate to RecoveryManager (D4)
        ...
    finally:
        self._buffer.signal_eof()
```

Key behavior:
- Each chunk is exactly `chunk_bytes_for_ms(100)` = 19200 bytes (100ms at 48kHz stereo s16le)
- `AudioBuffer.put()` blocks if buffer is full (backpressure from consumer)
- On EOF (FFmpeg exits), signals the consumer via `signal_eof()`
- On exception, attempts recovery (wired in D4)

### 2.2 Consumer thread

Runs in `CaptureOrchestrator._consumer_loop()`:

```python
def _consumer_loop(self) -> None:
    """Drain AudioBuffer through SplitProcessor."""
    while True:
        chunk = self._buffer.get()
        if chunk is None:  # EOF sentinel
            break
        self._split.process_chunk(chunk)
    self._split.finish()
```

Key behavior:
- Blocks on `AudioBuffer.get()` until data is available
- Routes each chunk to the active `EncoderProcess` via `SplitProcessor`
- When `SplitProcessor` hits a boundary, it calls `_rotate_encoder()` which closes the current encoder and starts a new one
- On EOF, calls `finish()` to close the final encoder

### 2.3 Encoder factory (start_encoder callback)

`SplitProcessor` needs a `start_encoder(segment_index) -> encoder` callback. The orchestrator provides this:

```python
def _start_encoder(self, segment_index: int) -> EncoderProcess:
    """Create and start a new EncoderProcess for segment N."""
    # Get metadata from BoundaryQueue for this segment
    metadata = self._get_segment_metadata(segment_index)

    # Generate output path
    mp3_path = self._file_namer.next_filename(metadata)

    # Create and start encoder
    encoder = EncoderProcess(
        output_path=mp3_path,
        sample_rate=self._config.sample_rate,
        channels=self._config.channels,
        bitrate=self._config.bitrate,
    )
    encoder.start()

    self._logger.encoder_event(
        "encoder_started",
        frame_position=self._buffer.frames_processed,
        segment_index=segment_index,
        output_path=mp3_path,
    )
    return encoder
```

### 2.4 Segment completion callback (on_segment_complete)

`SplitProcessor` calls `on_segment_complete(segment_index, start_frame, end_frame)` when a segment boundary is crossed:

```python
def _on_segment_complete(
    self, segment_index: int, start_frame: int, end_frame: int
) -> None:
    """Finalize a completed segment: wait for encoder, write sidecar."""
    # Wait for encoder to finish writing MP3
    encoder = self._encoders[segment_index]
    rc = encoder.wait(timeout=30.0)

    if rc != 0:
        # Encoder failed — delegate to RecoveryManager (D4)
        ...
        return

    mp3_path = encoder.output_path

    # Write JSON sidecar
    metadata = self._build_segment_metadata(segment_index, start_frame, end_frame)
    sidecar_path = self._metadata_writer.write_sidecar(mp3_path, metadata)

    self._logger.split_event(
        "segment_complete",
        frame_position=end_frame,
        segment_index=segment_index,
        mp3_path=mp3_path,
        sidecar_path=sidecar_path,
        duration_frames=end_frame - start_frame,
    )

    # Fire user callback
    if self._on_segment_saved:
        self._on_segment_saved(mp3_path, asdict(metadata))
```

### 2.5 Encoder lifecycle tracking

The orchestrator maintains a dict of active/completed encoders:

```python
self._encoders: dict[int, EncoderProcess] = {}
```

The `_start_encoder` callback stores each encoder in this dict. The `_on_segment_complete` callback retrieves it to call `wait()`.

### 2.6 Build SegmentMetadata

```python
def _build_segment_metadata(
    self, segment_index: int, start_frame: int, end_frame: int
) -> SegmentMetadata:
    """Construct metadata for a completed segment."""
    boundary_meta = self._get_segment_metadata(segment_index)
    return SegmentMetadata(
        start_frame=start_frame,
        end_frame=end_frame,
        sample_rate=self._config.sample_rate,
        channels=self._config.channels,
        bitrate=self._config.bitrate,
        segment_index=segment_index,
        song_title=boundary_meta.get("song_title") if boundary_meta else None,
        artist=boundary_meta.get("artist") if boundary_meta else None,
        album=boundary_meta.get("album") if boundary_meta else None,
        output_file=self._encoders[segment_index].output_path,
        gaps=self._recovery.gaps,
    )
```

---

## Thread Safety

| Shared State | Protection | Access Pattern |
|-------------|-----------|----------------|
| `AudioBuffer` | Internal `queue.Queue` + `threading.Lock` | Producer puts, consumer gets |
| `_encoders` dict | Only written by consumer thread | Consumer-only access after creation |
| `FileNamer._counter` | Only called from consumer thread | Consumer-only |
| `MetadataWriter` | No shared state | Consumer-only |

No additional locking needed — the `AudioBuffer` queue is the single synchronization point between producer and consumer.

---

## File Modified

```
src/dreamsync/capture/orchestrator.py  (add producer/consumer loops, encoder factory)
```

## Tests

Added to `dev/tests/test_orchestrator.py`:

- [ ] `test_producer_reads_chunks_into_buffer` — mock FFmpeg stdout with known bytes, verify buffer receives them
- [ ] `test_consumer_routes_chunks_to_encoder` — push chunks into buffer, verify encoder.write() called
- [ ] `test_encoder_factory_creates_encoder` — verify `_start_encoder` returns a started EncoderProcess with correct path
- [ ] `test_segment_complete_writes_sidecar` — verify MetadataWriter called after encoder completes
- [ ] `test_segment_complete_fires_callback` — verify `on_segment_saved` called with path and metadata
- [ ] `test_eof_triggers_finish` — signal EOF on buffer, verify SplitProcessor.finish() called
- [ ] `test_file_namer_timestamp_pattern` — verify filenames match `YYYY-MM-DD_HH-MM-SS_segment_NNNNNN.mp3`
- [ ] `test_file_namer_metadata_pattern` — verify filenames match `YYYY-MM-DD_HH-MM-SS_Artist_-_Title.mp3`

---

## Passing Criteria

- [ ] Producer thread reads PCM from CaptureProcessManager.stdout and enqueues into AudioBuffer
- [ ] Consumer thread dequeues from AudioBuffer and routes through SplitProcessor
- [ ] SplitProcessor calls `_start_encoder` at each boundary to spawn per-segment EncoderProcess
- [ ] Each segment produces an MP3 file at the path returned by FileNamer
- [ ] Each segment produces a JSON sidecar via MetadataWriter with frame positions, timing, and song metadata
- [ ] `on_segment_saved` callback fires after each segment is finalized
- [ ] EOF from FFmpeg propagates through buffer → SplitProcessor.finish() → final encoder closed
- [ ] All D2 tests pass
