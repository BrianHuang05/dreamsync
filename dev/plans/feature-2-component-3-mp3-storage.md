# Feature 2, Component 3 — mp3 Storage

**Status**: COMPLETE

---

## Overview

The mp3 Storage component sits between the audio source (virtual device, system input, or wav file) and the Analyzer (Component 4). It ingests a continuous audio stream, detects song boundaries using a composite strategy (Spotify track-change events, silence gaps, and crossfade detection), and compiles each song into an individual mp3 file on disk. Component 3 is designed to be source-agnostic: it accepts PCM audio from any provider, enabling isolated development and testing without Components 1–2.

---

## Architecture

```
                         ┌──────────────────────────────────┐
                         │         Audio Source              │
                         │  (system_input / wav / virtual)   │
                         └──────────────┬───────────────────┘
                                        │ PCM chunks (numpy arrays)
                                        ▼
┌────────────────┐      ┌──────────────────────────────────┐
│ SpotifyQueue   │      │       AudioStreamBuffer          │
│ Watcher        │─────▶│  (thread-safe ring buffer)       │
│ (track change) │ cb   │  stores raw PCM for current song │
└────────────────┘      └──────────────┬───────────────────┘
                                       │ PCM frames
                                       ▼
                        ┌──────────────────────────────────┐
                        │    CompositeBoundaryDetector      │
                        │  ┌───────────┬──────────────┐    │
                        │  │ Spotify   │ Silence      │    │
                        │  │ signal    │ detector     │    │
                        │  ├───────────┼──────────────┤    │
                        │  │ Crossfade │ (future:     │    │
                        │  │ detector  │  metadata)   │    │
                        │  └───────────┴──────────────┘    │
                        └──────────────┬───────────────────┘
                                       │ boundary event
                                       ▼
                        ┌──────────────────────────────────┐
                        │        SongFileWriter             │
                        │  PCM accumulator → mp3 encoder    │
                        │  writes one .mp3 per song         │
                        └──────────────┬───────────────────┘
                                       │
                                       ▼
                              output_dir/{song}.mp3


Orchestrator:  StreamCapturePipeline
               - Wires source → buffer → detector → writer
               - Daemon-thread pattern (start/stop)
               - Fires on_song_saved(path, metadata) callback
```

### File Layout

```
src/dreamsync/
├── capture/
│   ├── __init__.py
│   ├── buffer.py              # AudioStreamBuffer
│   ├── boundary.py            # CompositeBoundaryDetector
│   ├── writer.py              # SongFileWriter (PCM → mp3)
│   └── pipeline.py            # StreamCapturePipeline orchestrator
```

### Integration Points

- **session.py** — Pipeline is started/stopped alongside existing watchers. `--capture` flag enables it. When Spotify watcher is also active, its `on_track_changed` callback is forwarded to the boundary detector.
- **cli.py** — `--capture`, `--capture-dir`, `--capture-format` flags on `session` subcommand.
- **Feature 1 (Queue Watcher)** — `on_track_changed(new, old)` provides the highest-confidence boundary signal and song metadata (title, artist, album).
- **Component 4 (Analyzer)** — Consumes the saved mp3 files. The `on_song_saved` callback is the handoff point.

---

## D3.1: Stream Ingestion — `AudioStreamBuffer`

### Design

A thread-safe buffer that accumulates PCM audio for the current song. Unlike a fixed-size ring buffer that overwrites old data, this uses a growable buffer (list of numpy chunks) since we need all PCM data for the current song to write to disk. A configurable max duration prevents unbounded memory growth.

```python
@dataclass(frozen=True)
class BufferConfig:
    sample_rate: int = 44100
    channels: int = 1
    max_song_seconds: float = 900.0   # 15 min cap
    chunk_size: int = 1024            # frames per chunk

class AudioStreamBuffer:
    def __init__(self, config: BufferConfig = BufferConfig()):
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._total_frames: int = 0

    def write(self, pcm: np.ndarray) -> None:
        """Append a PCM chunk. Thread-safe. Drops oldest if max exceeded."""

    def read_all(self) -> np.ndarray:
        """Return all accumulated PCM as a single contiguous array."""

    def clear(self) -> None:
        """Discard all accumulated data (called after song boundary)."""

    @property
    def duration_seconds(self) -> float:
        """Current accumulated duration."""

    @property
    def frame_count(self) -> int:
        """Total frames accumulated."""
```

### Implementation Steps

1. Create `src/dreamsync/capture/__init__.py` (empty).
2. Create `src/dreamsync/capture/buffer.py`:
   - `BufferConfig` frozen dataclass with sample_rate, channels, max_song_seconds, chunk_size.
   - `AudioStreamBuffer` class:
     - `__init__(config)` — initializes empty chunk list, threading lock, frame counter.
     - `write(pcm)` — appends numpy array to chunk list under lock. If `_total_frames` exceeds `max_song_seconds * sample_rate`, log a warning and drop the oldest chunk (FIFO shed).
     - `read_all()` — concatenates all chunks into single `np.ndarray` under lock. Returns empty array if no data.
     - `clear()` — empties chunk list and resets frame counter under lock.
     - `duration_seconds` property — `_total_frames / config.sample_rate`.
     - `frame_count` property.
3. Ensure `write()` accepts both 1-D (mono) and 2-D (channels × frames) arrays, normalizing to the configured channel count.

### Done When

- [ ] `AudioStreamBuffer` accepts numpy PCM chunks via `write()` and returns them via `read_all()`
- [ ] Thread-safe: concurrent `write()` and `read_all()` from different threads do not corrupt data (verified with threading test)
- [ ] `clear()` resets the buffer to empty state
- [ ] Memory cap: buffer sheds oldest chunks when `max_song_seconds` is exceeded
- [ ] `duration_seconds` is accurate within 1 frame of expected value
- [ ] Handles mono and stereo input without error
- [ ] 10+ unit tests covering normal operation, edge cases, thread safety

---

## D3.2: Song Boundary Detection — `CompositeBoundaryDetector`

### Design

A composite detector that merges multiple boundary signals. Each signal source has a priority and confidence level. The detector fires at most once per `min_song_seconds` cooldown.

**Signal sources (priority order):**

| # | Signal | Source | Confidence | Notes |
|---|--------|--------|------------|-------|
| 1 | Spotify track change | `SpotifyQueueWatcher.on_track_changed` | Highest | Authoritative; includes metadata. Optional (only when Spotify is connected). |
| 2 | Silence gap | Existing `SongBoundaryDetector` | High | Already built and tested. Works for tracks with gaps. |
| 3 | Crossfade detection | Existing `CrossfadeBoundaryDetector` | Medium | Already built and tested. Works for gapless/crossfade playlists. |

The composite detector does not re-implement the individual detectors — it wraps and coordinates them.

```python
@dataclass(frozen=True)
class BoundaryEvent:
    timestamp: float             # seconds into the stream
    source: str                  # "spotify" | "silence" | "crossfade"
    track_name: str | None       # from Spotify, if available
    artist: str | None           # from Spotify, if available
    confidence: float            # 0.0–1.0

class CompositeBoundaryDetector:
    def __init__(
        self,
        hop_size: int = 512,
        sample_rate: int = 44100,
        min_song_seconds: float = 30.0,
        on_boundary: Callable[[BoundaryEvent], None] | None = None,
    ):
        self._silence_det = SongBoundaryDetector(...)
        self._crossfade_det = CrossfadeBoundaryDetector(...)
        self._on_boundary = on_boundary
        self._last_boundary_t: float = -1e9
        self._pending_spotify: BoundaryEvent | None = None

    def update(self, features: dict, t: float) -> BoundaryEvent | None:
        """Feed audio features for one frame. Returns BoundaryEvent if fired."""

    def notify_track_changed(self, new_track, old_track) -> None:
        """Called by SpotifyQueueWatcher. Sets pending Spotify boundary."""

    def reset(self) -> None:
        """Reset all sub-detectors."""

    @property
    def boundary_count(self) -> int: ...
```

**Boundary firing logic:**

1. If a Spotify track-change notification is pending, fire immediately (highest confidence). The Spotify signal is debounced: if the audio-based detectors fire within ±3 seconds of the Spotify signal, they are suppressed (same boundary).
2. If the silence detector fires (`SongBoundaryDetector.update()` returns `True`), fire with source="silence".
3. If the crossfade detector fires (`CrossfadeBoundaryDetector.update()` returns `True`), fire with source="crossfade".
4. All signals are subject to `min_song_seconds` cooldown from the last boundary.

### Implementation Steps

1. Create `src/dreamsync/capture/boundary.py`:
   - `BoundaryEvent` frozen dataclass with timestamp, source, track_name, artist, confidence.
   - `CompositeBoundaryDetector`:
     - `__init__` — instantiate `SongBoundaryDetector` and `CrossfadeBoundaryDetector` with configured params. Store callback.
     - `notify_track_changed(new_track, old_track)` — sets `_pending_spotify` with metadata from `SpotifyTrack`. Thread-safe (called from watcher thread).
     - `update(features, t)`:
       1. Check Spotify pending first. If set and cooldown elapsed, fire and clear pending.
       2. Feed `features["rms"]` to silence detector. If fires and no recent Spotify boundary, emit silence event.
       3. Feed full features to crossfade detector. If fires and no recent boundary (Spotify or silence), emit crossfade event.
       4. If any event emitted, call `_on_boundary(event)` and update `_last_boundary_t`.
     - `reset()` — reset both sub-detectors, clear pending, reset counters.
   - Debounce window: if Spotify signal arrived within ±`_debounce_seconds` (default 3.0) of an audio-detected boundary, suppress the audio signal.

2. The `features` dict passed to `update()` should contain the same keys already computed by `LiveBpmEstimator` and `SpectralBeatTemplate`: `rms`, `bpm`, `centroid`, `bass_ratio`, `energy`, `onset_strength`, `beat`. This keeps the interface compatible with the existing DSP pipeline.

### Done When

- [ ] `CompositeBoundaryDetector` wraps `SongBoundaryDetector` and `CrossfadeBoundaryDetector` without duplicating their logic
- [ ] Spotify track-change events produce immediate boundary events with metadata
- [ ] Silence-based boundaries fire when Spotify is not connected
- [ ] Crossfade-based boundaries fire for gapless transitions
- [ ] Debounce: audio boundaries within ±3s of a Spotify boundary are suppressed (no double-fire)
- [ ] `min_song_seconds` cooldown is respected across all signal sources
- [ ] `on_boundary` callback fires exactly once per detected boundary
- [ ] Thread-safe: `notify_track_changed` can be called from a different thread than `update`
- [ ] 15+ unit tests covering each signal source, debounce, cooldown, thread safety, and reset

---

## D3.3: File Compilation — `SongFileWriter`

### Design

Accumulates PCM audio for the current song and encodes it to mp3 when a boundary is detected. Uses `subprocess` to invoke `ffmpeg` for encoding (avoids adding a pip dependency; ffmpeg is widely available and already likely installed for audio work).

```python
@dataclass(frozen=True)
class WriterConfig:
    output_dir: str = "captured_songs"
    bitrate: str = "192k"
    sample_rate: int = 44100
    channels: int = 1
    min_duration_seconds: float = 15.0  # discard fragments shorter than this
    naming: str = "timestamp"           # "timestamp" | "metadata"

class SongFileWriter:
    def __init__(self, config: WriterConfig = WriterConfig()):
        ...

    def finalize(
        self,
        pcm: np.ndarray,
        metadata: dict | None = None,
    ) -> Path | None:
        """Encode PCM to mp3 and write to disk. Returns path or None if too short."""

    def _build_filename(self, metadata: dict | None) -> str:
        """Generate filename from metadata or timestamp."""

    def _encode_mp3(self, pcm: np.ndarray, output_path: Path) -> None:
        """Pipe raw PCM to ffmpeg subprocess, produce mp3 file."""
```

**Filename scheme:**

| Mode | Pattern | Example |
|------|---------|---------|
| `timestamp` | `{YYYYMMDD}_{HHMMSS}.mp3` | `20260303_142015.mp3` |
| `metadata` | `{artist} - {title}.mp3` | `Daft Punk - Around the World.mp3` |

When `metadata` mode is selected but metadata is unavailable (no Spotify), falls back to `timestamp`.

**Encoding strategy:**

```
pcm (numpy float32) → raw PCM bytes (int16 LE) → stdin pipe → ffmpeg → output.mp3
```

ffmpeg command:
```bash
ffmpeg -f s16le -ar 44100 -ac 1 -i pipe:0 -b:a 192k -y output.mp3
```

This avoids temp wav files and streams directly through a pipe.

### Implementation Steps

1. Create `src/dreamsync/capture/writer.py`:
   - `WriterConfig` frozen dataclass with output_dir, bitrate, sample_rate, channels, min_duration_seconds, naming.
   - `SongFileWriter`:
     - `__init__(config)` — store config, create output directory if it doesn't exist.
     - `finalize(pcm, metadata)`:
       1. Compute duration from `len(pcm) / config.sample_rate`.
       2. If duration < `min_duration_seconds`, log skip and return `None`.
       3. Build filename via `_build_filename(metadata)`.
       4. Call `_encode_mp3(pcm, output_path)`.
       5. Verify output file exists and size > 0.
       6. Return `Path` to the written file.
     - `_build_filename(metadata)`:
       1. If `config.naming == "metadata"` and metadata has `track_name` and `artist`: sanitize and format.
       2. Otherwise, use `datetime.now().strftime("%Y%m%d_%H%M%S")`.
       3. Handle filename collisions by appending `_2`, `_3`, etc.
     - `_encode_mp3(pcm, output_path)`:
       1. Convert float32 → int16: `(pcm * 32767).astype(np.int16)`.
       2. Spawn `ffmpeg` subprocess with stdin=PIPE, stdout=DEVNULL, stderr=PIPE.
       3. Write PCM bytes to stdin, close stdin, wait for process.
       4. If returncode != 0, raise `EncodeError` with stderr output.
   - `EncodeError(Exception)` — raised on ffmpeg failures.

2. Add ffmpeg availability check:
   - `check_ffmpeg() -> bool` — runs `ffmpeg -version`, returns True if available.
   - Called once at pipeline startup. Raises clear error message if ffmpeg is not installed.

### Done When

- [ ] `SongFileWriter.finalize(pcm, metadata)` produces a valid, playable mp3 file
- [ ] Files are written to the configured output directory with correct naming
- [ ] Fragments shorter than `min_duration_seconds` are discarded (returns `None`)
- [ ] `metadata` naming mode produces sanitized filenames (no illegal path chars)
- [ ] `timestamp` naming mode produces unique filenames (no collisions in rapid succession)
- [ ] Encoding uses ffmpeg subprocess with piped stdin (no temp files)
- [ ] `EncodeError` is raised with useful diagnostics on ffmpeg failure
- [ ] `check_ffmpeg()` detects missing ffmpeg and raises an actionable error
- [ ] Output mp3 files are verified playable (duration matches input PCM ±0.5s)
- [ ] 12+ unit tests covering encoding, naming, min duration, error handling, collision avoidance

---

## Integration: `StreamCapturePipeline`

The orchestrator that wires the three deliverables together into a single start/stop lifecycle.

### Design

```python
@dataclass(frozen=True)
class CaptureConfig:
    buffer: BufferConfig = BufferConfig()
    writer: WriterConfig = WriterConfig()
    hop_size: int = 512
    sample_rate: int = 44100
    min_song_seconds: float = 30.0

class StreamCapturePipeline:
    def __init__(
        self,
        config: CaptureConfig = CaptureConfig(),
        on_song_saved: Callable[[Path, dict], None] | None = None,
    ):
        self._buffer = AudioStreamBuffer(config.buffer)
        self._detector = CompositeBoundaryDetector(
            hop_size=config.hop_size,
            sample_rate=config.sample_rate,
            min_song_seconds=config.min_song_seconds,
            on_boundary=self._handle_boundary,
        )
        self._writer = SongFileWriter(config.writer)
        self._on_song_saved = on_song_saved

    def feed(self, pcm: np.ndarray, features: dict, t: float) -> None:
        """Called per audio frame. Buffers audio and checks for boundaries."""
        self._buffer.write(pcm)
        event = self._detector.update(features, t)
        # boundary handling is done in _handle_boundary callback

    def notify_track_changed(self, new_track, old_track) -> None:
        """Forward Spotify track change to boundary detector."""
        self._detector.notify_track_changed(new_track, old_track)

    def _handle_boundary(self, event: BoundaryEvent) -> None:
        """Finalize current song, start new accumulation."""
        pcm = self._buffer.read_all()
        self._buffer.clear()
        metadata = {
            "track_name": event.track_name,
            "artist": event.artist,
            "source": event.source,
            "boundary_time": event.timestamp,
        }
        path = self._writer.finalize(pcm, metadata)
        if path and self._on_song_saved:
            self._on_song_saved(path, metadata)

    def flush(self) -> Path | None:
        """Finalize whatever is in the buffer (e.g., on session end)."""

    def reset(self) -> None:
        """Clear all state."""
```

### Implementation Steps

1. Create `src/dreamsync/capture/pipeline.py`:
   - `CaptureConfig` frozen dataclass composing BufferConfig, WriterConfig, and detector params.
   - `StreamCapturePipeline` as above.
   - `feed()` is called from the main audio loop in `live.py` — one call per hop frame.
   - `flush()` handles session shutdown: finalizes the last song if buffer has enough data.

2. Wire into `session.py`:
   - If `--capture` is passed, instantiate `StreamCapturePipeline`.
   - In the main audio loop (where `LiveBpmEstimator.update()` is called), also call `pipeline.feed(pcm, features, t)`.
   - If Spotify watcher is active, connect `watcher.on_track_changed → pipeline.notify_track_changed`.
   - On session shutdown, call `pipeline.flush()`.

3. Add CLI flags to `cli.py`:
   - `--capture` (flag) — enable song capture.
   - `--capture-dir PATH` — output directory (default: `captured_songs`).
   - `--capture-naming {timestamp,metadata}` — filename scheme (default: `timestamp`).

### Done When

- [ ] `StreamCapturePipeline` wires buffer → detector → writer into a single `feed()` call
- [ ] `on_song_saved(path, metadata)` fires after each song is written
- [ ] `flush()` finalizes the last partial song on shutdown
- [ ] Integration with `session.py`: `--capture` flag enables the pipeline
- [ ] Integration with Spotify watcher: track changes forward to boundary detector
- [ ] End-to-end: continuous audio input → individual mp3 files on disk, one per song
- [ ] 8+ integration tests covering the full pipeline, flush, and session wiring

---

## Tests

All tests in `dev/tests/test_capture_*.py`. Target: **45+ tests** across 4 files.

| File | Area | Count |
|------|------|-------|
| `dev/tests/test_capture_buffer.py` | AudioStreamBuffer: write, read_all, clear, duration, thread safety, max cap, mono/stereo | 10+ |
| `dev/tests/test_capture_boundary.py` | CompositeBoundaryDetector: Spotify signal, silence signal, crossfade signal, debounce, cooldown, reset, thread safety | 15+ |
| `dev/tests/test_capture_writer.py` | SongFileWriter: encode, naming modes, min duration skip, collisions, sanitize, errors, ffmpeg check | 12+ |
| `dev/tests/test_capture_pipeline.py` | StreamCapturePipeline: end-to-end feed→save, flush, Spotify integration, callback firing | 8+ |

### Test Strategy

- **Buffer tests**: Pure unit tests with numpy arrays. Thread safety tested with `concurrent.futures.ThreadPoolExecutor`.
- **Boundary tests**: Mock the existing `SongBoundaryDetector` and `CrossfadeBoundaryDetector` to control when they fire. Test composite logic in isolation.
- **Writer tests**: Mock `subprocess.run` / `subprocess.Popen` to avoid requiring ffmpeg in CI. One integration test (marked `@pytest.mark.slow`) that actually encodes if ffmpeg is available.
- **Pipeline tests**: Mock the writer's `finalize()` to capture calls. Verify correct PCM data is passed and callbacks fire.

---

## Parameters

| Parameter | Default | Rationale |
|-----------|---------|-----------|
| `buffer.sample_rate` | 44100 | Standard CD quality, matches existing DSP pipeline |
| `buffer.channels` | 1 | Mono, consistent with `capture_mono_audio()` |
| `buffer.max_song_seconds` | 900.0 | 15 min cap prevents unbounded memory (~75 MB at 44.1kHz mono int16) |
| `buffer.chunk_size` | 1024 | Frames per write, matches sounddevice blocksize |
| `detector.min_song_seconds` | 30.0 | Minimum time between boundaries; prevents false splits on intros/breakdowns |
| `detector.debounce_seconds` | 3.0 | Window for suppressing audio boundaries near a Spotify signal |
| `writer.output_dir` | `captured_songs` | Relative to working dir; configurable via CLI |
| `writer.bitrate` | `192k` | Good quality-to-size ratio for analysis source material |
| `writer.min_duration_seconds` | 15.0 | Discard fragments shorter than this (noise, partial captures) |
| `writer.naming` | `timestamp` | Default to timestamp; metadata mode requires Spotify |
| `pipeline.hop_size` | 512 | Matches existing DSP pipeline hop size |

---

## Build Order

Steps are sequential within each deliverable but D3.1–D3.3 can be built in parallel since they have minimal coupling:

| Phase | Step | Deliverable | Files Created/Modified |
|-------|------|-------------|----------------------|
| 1 | Create `capture/` package | Setup | `src/dreamsync/capture/__init__.py` |
| 2a | Implement `AudioStreamBuffer` | D3.1 | `src/dreamsync/capture/buffer.py` |
| 2b | Implement `CompositeBoundaryDetector` | D3.2 | `src/dreamsync/capture/boundary.py` |
| 2c | Implement `SongFileWriter` | D3.3 | `src/dreamsync/capture/writer.py` |
| 3 | Write unit tests for D3.1–D3.3 | Tests | `dev/tests/test_capture_*.py` |
| 4 | Implement `StreamCapturePipeline` | Integration | `src/dreamsync/capture/pipeline.py` |
| 5 | Wire into session + CLI | Integration | `src/dreamsync/session.py`, `src/dreamsync/cli.py` |
| 6 | End-to-end integration tests | Tests | `dev/tests/test_capture_pipeline.py` |
| 7 | Manual validation: capture 5+ songs | Validation | — |

---

## Non-Goals

- **mp3 decoding / re-encoding of input**: Input is PCM, not compressed mp3. If Component 2 provides an mp3 stream in the future, a decoder adapter will be added at that time.
- **Real-time mp3 streaming output**: Files are written only after a boundary is detected, not streamed incrementally.
- **Automatic ffmpeg installation**: The user is expected to have ffmpeg installed. `check_ffmpeg()` provides a clear error if missing.
- **ID3 tagging**: mp3 files are written without ID3 metadata tags. Metadata is tracked in the `on_song_saved` callback dict. ID3 tagging can be added later if needed.
- **Gapless playback of output files**: Output files are for analysis input (Component 4), not for listening. Minor boundary artifacts at start/end are acceptable.

---

## Dependencies

| Dependency | Type | Notes |
|------------|------|-------|
| `numpy` | Existing | PCM buffer storage and manipulation |
| `threading` | Stdlib | Thread-safe buffer access |
| `subprocess` | Stdlib | ffmpeg invocation |
| `ffmpeg` | System binary | Must be installed and on PATH |
| `SongBoundaryDetector` | Existing code | `src/dreamsync/live.py` |
| `CrossfadeBoundaryDetector` | Existing code | `src/dreamsync/live.py` |
| `SpotifyQueueWatcher` | Existing code (Feature 1) | `src/dreamsync/spotify/queue_watcher.py` — optional |

No new pip dependencies required.
