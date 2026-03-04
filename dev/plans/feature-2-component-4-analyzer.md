# Feature 2, Component 4 — Song Structure Analyzer

**Status**: **CODE COMPLETE** (80 unit tests)

---

## Overview

The Analyzer takes an mp3 file (produced by Component 3 or supplied directly) and produces a `SongStructure` — a complete structural map of the song including stable BPM, beat grid, section boundaries with labels, and per-section energy/mood profiles. This is the input to the Show Compiler (Feature 3).

The core insight is that most of the per-frame analysis already exists in the codebase (`LiveBpmEstimator`, `CrossfadeBoundaryDetector`, spectral features, noise floor subtraction, percussive onset tracking). Component 4 adapts this live pipeline to run offline over a complete file, then adds the new capabilities that only work with full-song context: global BPM consensus, section segmentation via self-similarity, and section labelling.

---

## Architecture

```
                     ┌──────────────────────────────┐
                     │         mp3 file              │
                     └──────────────┬───────────────┘
                                    │
                                    ▼
                     ┌──────────────────────────────┐
                     │       Mp3Decoder              │
                     │  ffmpeg → PCM (numpy float32) │
                     └──────────────┬───────────────┘
                                    │ mono PCM signal
                                    ▼
                     ┌──────────────────────────────┐
                     │   OfflineFeaturePipeline       │
                     │  Runs the full live-quality    │
                     │  feature chain frame-by-frame: │
                     │  RMS, ZCR, centroid, bass,     │
                     │  spectral flux, kick flux,     │
                     │  whitened flux, percussive      │
                     │  onset, BPM, beat detection     │
                     │  → list[FeatureRow]             │
                     └──────────────┬───────────────┘
                                    │ per-frame features
                                    ▼
                     ┌──────────────────────────────┐
                     │    GlobalBpmEstimator          │
                     │  Consensus BPM from full song  │
                     │  + tempo-change detection       │
                     └──────────────┬───────────────┘
                                    │ stable BPM, beat grid
                                    ▼
                     ┌──────────────────────────────┐
                     │    SectionSegmenter            │
                     │  Self-similarity matrix →      │
                     │  novelty curve → boundaries    │
                     │  → section labelling            │
                     └──────────────┬───────────────┘
                                    │ labelled sections
                                    ▼
                     ┌──────────────────────────────┐
                     │    SongStructure               │
                     │  (frozen dataclass / JSON)     │
                     │  beats, sections, bpm, energy, │
                     │  mood per section              │
                     └──────────────────────────────┘


Orchestrator:  analyze_song(mp3_path) → SongStructure
               - Wires decoder → features → bpm → sections → output
               - Single function entry point
               - Also exposed as CLI subcommand
```

### File Layout

```
src/dreamsync/
├── analyzer/
│   ├── __init__.py
│   ├── decode.py               # Mp3Decoder (mp3 → PCM via ffmpeg)
│   ├── features.py             # OfflineFeaturePipeline (full feature chain)
│   ├── bpm.py                  # GlobalBpmEstimator (consensus + tempo changes)
│   ├── sections.py             # SectionSegmenter (boundaries + labels)
│   ├── models.py               # SongStructure, Section, BeatGrid dataclasses
│   └── analyze.py              # analyze_song() orchestrator
```

### Integration Points

- **Component 3 (mp3 Storage)** — `on_song_saved(path, metadata)` callback triggers `analyze_song(path)`.
- **Feature 3 (Show Compiler)** — Consumes `SongStructure` to produce a lighting timeline.
- **cli.py** — `analyze` subcommand for one-off file analysis.
- **session.py** — Optional: auto-analyze captured songs during a session.

---

## D4.1: mp3 Decoding — `Mp3Decoder`

### Design

Decodes mp3 (or any ffmpeg-supported format) to mono float32 PCM using ffmpeg as a subprocess. Mirrors the encoding pattern from `capture/writer.py` but in reverse.

```python
@dataclass(frozen=True)
class AudioData:
    signal: np.ndarray    # mono float32, [-1.0, 1.0]
    sample_rate: int
    duration: float       # seconds
    channels: int         # original channel count (before downmix)

def decode_mp3(path: Path, target_sr: int = 44100) -> AudioData:
    """Decode an mp3 file to mono float32 PCM via ffmpeg.

    Resamples to target_sr and downmixes to mono.
    Raises DecodeError on failure.
    """

class DecodeError(Exception):
    """Raised when ffmpeg decoding fails."""
```

**ffmpeg command:**
```bash
ffmpeg -i input.mp3 -f f32le -acodec pcm_f32le -ar 44100 -ac 1 pipe:1
```

This outputs raw float32 little-endian PCM to stdout, which is read directly into a numpy array. No temp files.

### Implementation Steps

1. Create `src/dreamsync/analyzer/__init__.py` (empty).
2. Create `src/dreamsync/analyzer/decode.py`:
   - `AudioData` frozen dataclass with signal, sample_rate, duration, channels.
   - `decode_mp3(path, target_sr)`:
     1. Verify file exists and is non-empty.
     2. Probe the file with `ffprobe -show_streams -of json` to get original sample_rate, channels, duration.
     3. Spawn ffmpeg subprocess with stdout=PIPE, stderr=PIPE.
     4. Read stdout into bytes, convert to `np.ndarray(dtype=np.float32)`.
     5. Compute duration from `len(signal) / target_sr`.
     6. Return `AudioData`.
   - `DecodeError(Exception)` — raised on ffmpeg failures, missing files.
   - Reuse `check_ffmpeg()` from `capture/writer.py` (import it, don't duplicate).

### Done When

- [x] `decode_mp3("song.mp3")` returns an `AudioData` with correct signal, sample_rate, duration
- [x] Works with mp3, wav, flac, ogg (any ffmpeg-supported format)
- [x] Resamples to target sample rate (verified: 48kHz input → 44.1kHz output)
- [x] Downmixes stereo to mono
- [x] Raises `DecodeError` with actionable message on corrupt/missing files
- [x] Duration matches ffprobe-reported duration ±0.5s
- [x] No temp files — streams through pipe
- [x] 13 unit tests covering normal decode, resample, missing file, corrupt file, various formats

---

## D4.2: Offline Feature Pipeline — `OfflineFeaturePipeline`

### Design

Runs the full live-quality feature extraction chain on a decoded PCM signal. This reuses the exact same analysis classes from `live.py` — `LiveBpmEstimator`, `NoiseFloorEstimator`, `PercussiveOnsetTracker`, `SpectralBeatTemplate` — but feeds them an entire file's worth of frames in a loop rather than from a real-time audio callback.

The output is a list of per-frame feature dicts, identical in format to what `run_live_to_govee()` produces internally. This means the Director, MoodClassifier, and all downstream code can consume it without modification.

```python
@dataclass(frozen=True)
class FeatureRow:
    t: float
    rms: float
    zcr: float
    centroid: float
    bass_ratio: float
    spectral_flux: float
    kick_spectral_flux: float
    onset_strength: float
    energy: float           # Director's composite energy
    bpm: float              # frame-level BPM estimate
    beat: bool
    mood: str               # "chill" | "groove" | "hype" | "drop"

class OfflineFeaturePipeline:
    def __init__(
        self,
        sample_rate: int = 44100,
        frame_size: int = 2048,
        hop_size: int = 512,
    ) -> None: ...

    def extract(self, signal: np.ndarray) -> list[FeatureRow]:
        """Run the full analysis pipeline on a PCM signal.

        Instantiates LiveBpmEstimator, NoiseFloorEstimator,
        PercussiveOnsetTracker, SpectralBeatTemplate, Director,
        and MoodClassifier — then feeds frames sequentially.
        """
```

### Implementation Steps

1. Create `src/dreamsync/analyzer/features.py`:
   - `FeatureRow` frozen dataclass with all per-frame fields.
   - `OfflineFeaturePipeline`:
     - `__init__` — store sample_rate, frame_size, hop_size.
     - `extract(signal)`:
       1. Pre-compute window, bass/kick masks, frequency bins (same as `run_live_to_govee`).
       2. Instantiate fresh: `LiveBpmEstimator`, `NoiseFloorEstimator`, `PercussiveOnsetTracker`, `SpectralBeatTemplate`, `Director`, `MoodClassifier`.
       3. Frame the signal: iterate `buffer[0:frame_size]`, advance by `hop_size`.
       4. Per frame, compute the same feature chain as `run_live_to_govee()` lines 1458–1538:
          - RMS, spectral features (`_spectral_features`), noise floor update, whitened flux, percussive onset, BPM/beat via estimator, Director update, mood update.
       5. Append a `FeatureRow` per frame.
       6. Return the full list.

2. Extract `_spectral_features()`, `_compute_whitened_flux()`, `_prepare_bass_window()`, and `_feature_row_from_frame()` from `live.py` into importable module-level functions (or use them directly if they're already importable). If they are currently nested/private in `live.py`, refactor to make them importable without breaking existing code.

### Done When

- [x] `OfflineFeaturePipeline.extract(signal)` produces per-frame features identical to live analysis
- [x] Reuses `LiveBpmEstimator`, `NoiseFloorEstimator`, `PercussiveOnsetTracker`, `SpectralBeatTemplate`, `Director`, `MoodClassifier` from existing code (no reimplementation)
- [x] Output `FeatureRow` list has frame-accurate timestamps (t = frame_index * hop_size / sample_rate)
- [x] BPM, beat, energy, mood fields are populated per frame
- [x] Processes a 4-minute song in < 10 seconds on the development machine
- [x] Produces the same BPM estimate as the live pipeline for the same audio (verified on 3+ test files)
- [x] 15 unit tests covering feature extraction, BPM accuracy, beat detection, edge cases (silence, very short files)

---

## D4.3: Global BPM Estimation — `GlobalBpmEstimator`

### Design

The live `LiveBpmEstimator` tracks BPM frame-by-frame with inertia and confirmation, which is ideal for real-time stability but means it takes several seconds to lock. For offline analysis, we have the entire song available and can compute a more accurate global BPM.

The `GlobalBpmEstimator` takes the per-frame BPM estimates from D4.2 and produces:
1. A **primary BPM** — the dominant stable tempo of the song.
2. **Tempo regions** — contiguous sections with consistent BPM (handles songs with tempo changes).
3. A **refined beat grid** — evenly-spaced beat positions at the global BPM, phase-aligned to the strongest detected beats.

```python
@dataclass(frozen=True)
class TempoRegion:
    start_t: float
    end_t: float
    bpm: float
    confidence: float   # 0.0–1.0

@dataclass(frozen=True)
class BeatGrid:
    bpm: float
    beat_times: tuple[float, ...]       # absolute time of each beat
    downbeat_times: tuple[float, ...]   # bar boundaries (every 4th beat for 4/4)
    time_signature: int                 # beats per bar (4 for 4/4, 3 for 3/4)

class GlobalBpmEstimator:
    def estimate(
        self,
        features: list[FeatureRow],
        sample_rate: int = 44100,
        hop_size: int = 512,
    ) -> tuple[float, list[TempoRegion], BeatGrid]:
        """Compute global BPM, tempo regions, and a refined beat grid."""
```

**Algorithm:**

1. **BPM histogram**: Collect frame-level BPMs (excluding 0.0 and first ~2s warmup), build histogram with 0.5 BPM bins, find the dominant peak.
2. **Harmonic aliasing**: Check ×0.5 and ×2.0 of the peak — if one has a significant secondary peak, prefer the musically correct range (80–160 BPM sweet spot).
3. **Tempo regions**: Scan frame-level BPM in windows of ~8 seconds. If median BPM deviates from global by > 8 BPM for a sustained window, create a new tempo region.
4. **Beat grid construction**: Using the global BPM, generate evenly-spaced beat times. Phase-align by finding the phase offset that maximises alignment with detected beats (from FeatureRow.beat == True). This gives metronomically precise beat positions.
5. **Time signature estimation**: Count how many detected beats fall on even vs. odd grid positions. If 3-beat grouping scores higher than 4, mark as 3/4; otherwise default 4/4.

### Implementation Steps

1. Create `src/dreamsync/analyzer/bpm.py`:
   - `TempoRegion` frozen dataclass.
   - `BeatGrid` frozen dataclass.
   - `GlobalBpmEstimator`:
     - `estimate(features, sample_rate, hop_size)`:
       1. Build histogram from `[f.bpm for f in features if f.bpm > 0 and f.t > 2.0]`.
       2. Smooth histogram, find peak. Resolve harmonic alias.
       3. Scan for tempo regions by windowed median.
       4. Build beat grid at global BPM: `beat_times = [offset + n * 60/bpm for n in range(N)]`.
       5. Optimize phase offset: try 100 offsets in [0, 60/bpm), pick the one maximising beat alignment score.
       6. Estimate time signature from beat grouping patterns.
       7. Return (global_bpm, regions, beat_grid).

### Done When

- [x] Global BPM is within ±2 BPM of ground truth on 10+ test files across genres
- [x] Harmonic aliasing resolved: doesn't report 60 BPM when song is 120 BPM (or vice versa)
- [x] Tempo changes detected: songs with known tempo shifts produce multiple `TempoRegion`s
- [x] Beat grid phase-aligned to detected beats (mean alignment error < 30ms)
- [x] Time signature detection: correctly identifies 4/4 on 8/10 test songs, correctly identifies 3/4 when applicable
- [x] Single-tempo songs produce exactly one `TempoRegion`
- [x] Handles edge cases: very slow (<70 BPM), very fast (>170 BPM), silence sections
- [x] 18 unit tests covering BPM accuracy, harmonic alias, tempo changes, beat grid, time signature

---

## D4.4: Section Segmentation — `SectionSegmenter`

### Design

Detects structural section boundaries and labels them. This is the most novel part of Component 4 — the existing codebase detects song-to-song boundaries but not within-song structure.

**Approach: Spectral Self-Similarity + Novelty Curve**

This is the standard MIR technique (Foote 2000, McFee & Ellis 2014). The idea:
1. Compute a feature vector per frame (chroma, MFCC, or spectral centroid/bass/energy).
2. Build a self-similarity matrix: `S[i,j] = cosine_sim(feature[i], feature[j])`.
3. Derive a novelty curve by convolving S with a checkerboard kernel along the diagonal — peaks correspond to section boundaries.
4. Peak-pick the novelty curve to get boundary timestamps.
5. Label each section by clustering: sections with similar feature profiles get the same label (A, B, C...), then map to musical names (intro, verse, chorus, etc.) based on energy/position heuristics.

```python
@dataclass(frozen=True)
class Section:
    start_t: float
    end_t: float
    label: str              # "intro" | "verse" | "chorus" | "bridge" | "drop" | "outro" | "breakdown"
    energy_mean: float      # average Director energy in this section
    mood: str               # dominant mood: "chill" | "groove" | "hype" | "drop"
    bpm: float              # BPM in this section (from TempoRegion)
    section_id: str         # structural label: "A", "B", "C"... (repeated sections share ID)

class SectionSegmenter:
    def __init__(
        self,
        kernel_size: int = 64,       # checkerboard kernel size in frames
        min_section_seconds: float = 8.0,
        peak_threshold: float = 0.3,  # novelty peak threshold (relative)
    ) -> None: ...

    def segment(
        self,
        features: list[FeatureRow],
        beat_grid: BeatGrid,
        tempo_regions: list[TempoRegion],
    ) -> list[Section]:
        """Detect section boundaries and label them."""
```

**Section labelling heuristics:**

| Criterion | Label |
|-----------|-------|
| First section, low energy, < 30s | `intro` |
| Last section, energy decreasing | `outro` |
| Highest energy section(s) | `chorus` (or `drop` if energy spike + BPM > 120) |
| Sections preceding highest energy | `verse` (if moderate energy) or `pre-chorus` (if energy rising) |
| Low-energy section between high-energy sections | `bridge` or `breakdown` |
| Repeating structure IDs get consistent labels | If A=verse once, A=verse everywhere |

**Structural ID assignment:**
- After boundary detection, compute a representative feature vector per section (mean chroma/MFCC/energy).
- Hierarchical clustering with a similarity threshold — sections closer than threshold get the same letter (A, B, C...).
- This captures verse-chorus-verse-chorus as A-B-A-B.

### Implementation Steps

1. Create `src/dreamsync/analyzer/sections.py`:
   - `Section` frozen dataclass.
   - `SectionSegmenter`:
     - `__init__` — store kernel_size, min_section_seconds, peak_threshold.
     - `segment(features, beat_grid, tempo_regions)`:
       1. **Build feature matrix**: For each frame, compute a feature vector from FeatureRow fields: [rms, centroid, bass_ratio, spectral_flux, energy]. Normalize each dimension to [0, 1] over the song. Shape: (n_frames, n_features).
       2. **Self-similarity matrix**: Compute pairwise cosine similarity. Optionally downsample to every 4th frame for efficiency (a 4-min song at 512 hop = ~5,000 frames; 5000×5000 is fine).
       3. **Novelty curve**: Convolve diagonal of S with a checkerboard kernel. The kernel is +1 in top-left and bottom-right quadrants, -1 in off-diagonals. Size = kernel_size frames.
       4. **Peak picking**: Find peaks in the novelty curve above `peak_threshold * max(novelty)`. Enforce `min_section_seconds` between peaks.
       5. **Snap to beat grid**: Move each boundary to the nearest downbeat in `beat_grid.downbeat_times`.
       6. **Structural IDs**: Compute mean feature vector per section. Agglomerative clustering → letter assignments.
       7. **Label assignment**: Apply heuristics (position, energy, repetition) to assign musical names.
       8. **Per-section stats**: Compute energy_mean, dominant mood (mode of frame moods), BPM from tempo_regions.
       9. Return sorted list of `Section`s.

2. The feature vector for similarity should emphasize timbral/harmonic content (centroid, bass_ratio, spectral_flux) over dynamics (rms, energy) to detect structural changes rather than just volume changes. Weights: centroid=0.3, bass_ratio=0.25, spectral_flux=0.25, rms=0.1, onset_strength=0.1.

### Done When

- [x] Section boundaries detected within ±4 seconds of manual annotation on 7/10 test songs
- [x] At minimum, intro/verse/chorus/outro are distinguished on songs with clear structure
- [x] EDM-style songs detect buildup/drop/breakdown patterns
- [x] Repeating sections assigned same structural ID (A-B-A-B pattern)
- [x] Boundary timestamps snapped to nearest downbeat
- [x] Section labels are reasonable on 7/10 test songs (no obviously wrong labels)
- [x] Handles songs with no clear structure (ambient, free-form) gracefully — produces at least 1 section
- [x] `min_section_seconds` prevents over-segmentation
- [x] Analysis completes in < 20 seconds per song (4-min track)
- [x] 20 unit tests covering boundary detection, labelling, snapping, clustering, edge cases

---

## D4.5: Song Structure Model & Orchestrator — `SongStructure` + `analyze_song()`

### Design

The top-level data model and orchestrator that wires D4.1–D4.4 together.

```python
@dataclass(frozen=True)
class SongStructure:
    path: str                           # source file path
    duration: float                     # total duration in seconds
    bpm: float                          # primary global BPM
    time_signature: int                 # beats per bar (4 for 4/4)
    beat_grid: BeatGrid                 # beat + downbeat positions
    tempo_regions: tuple[TempoRegion, ...]  # BPM over time
    sections: tuple[Section, ...]       # structural sections in order
    metadata: dict                      # track_name, artist (if available from mp3 tags or Spotify)

    def to_dict(self) -> dict:
        """Serialise to a JSON-compatible dict."""

    def to_json(self, path: Path) -> None:
        """Write structure to a JSON file."""

    @classmethod
    def from_json(cls, path: Path) -> SongStructure:
        """Load structure from a JSON file."""

def analyze_song(
    mp3_path: Path,
    *,
    sample_rate: int = 44100,
    frame_size: int = 2048,
    hop_size: int = 512,
    metadata: dict | None = None,
) -> SongStructure:
    """Full analysis pipeline: mp3 → SongStructure.

    Steps:
    1. Decode mp3 → PCM
    2. Run offline feature pipeline
    3. Estimate global BPM + beat grid
    4. Segment into sections
    5. Return SongStructure
    """
```

**JSON format** (output of `to_json`):

```json
{
  "path": "captured_songs/20260303_142015.mp3",
  "duration": 237.5,
  "bpm": 128.0,
  "time_signature": 4,
  "beat_grid": {
    "bpm": 128.0,
    "beat_times": [0.12, 0.59, 1.06, ...],
    "downbeat_times": [0.12, 1.99, 3.87, ...],
    "time_signature": 4
  },
  "tempo_regions": [
    {"start_t": 0.0, "end_t": 237.5, "bpm": 128.0, "confidence": 0.95}
  ],
  "sections": [
    {
      "start_t": 0.0, "end_t": 16.2, "label": "intro",
      "energy_mean": 0.15, "mood": "chill", "bpm": 128.0, "section_id": "A"
    },
    {
      "start_t": 16.2, "end_t": 48.5, "label": "verse",
      "energy_mean": 0.35, "mood": "groove", "bpm": 128.0, "section_id": "B"
    },
    ...
  ],
  "metadata": {
    "track_name": "Around the World",
    "artist": "Daft Punk"
  }
}
```

### Implementation Steps

1. Create `src/dreamsync/analyzer/models.py`:
   - `SongStructure` frozen dataclass. Contains `to_dict()`, `to_json()`, `from_json()`.
   - Import `Section`, `BeatGrid`, `TempoRegion` from other modules.
   - JSON serialisation: convert all tuples to lists, floats to 4 decimal places.
   - JSON deserialisation: reconstruct frozen dataclasses from dicts.

2. Create `src/dreamsync/analyzer/analyze.py`:
   - `analyze_song(mp3_path, *, sample_rate, frame_size, hop_size, metadata)`:
     1. `decode_mp3(mp3_path, target_sr=sample_rate)` → `AudioData`.
     2. `OfflineFeaturePipeline(sample_rate, frame_size, hop_size).extract(audio.signal)` → `list[FeatureRow]`.
     3. `GlobalBpmEstimator().estimate(features, sample_rate, hop_size)` → `(bpm, regions, beat_grid)`.
     4. `SectionSegmenter().segment(features, beat_grid, regions)` → `list[Section]`.
     5. Assemble and return `SongStructure`.

3. Add CLI subcommand to `cli.py`:
   - `analyze` subcommand:
     - `dreamsync analyze song.mp3` — prints JSON to stdout.
     - `dreamsync analyze song.mp3 --output structure.json` — writes to file.
     - `dreamsync analyze song.mp3 --summary` — prints human-readable summary (BPM, sections, duration).
   - Batch mode: `dreamsync analyze-dir captured_songs/ --output-dir analysis/`.

4. Wire into `session.py` (optional auto-analysis):
   - If `--capture` and `--analyze` are both passed, hook `on_song_saved` to call `analyze_song()` after each capture.
   - Print analysis summary to stdout.

### Done When

- [x] `analyze_song(mp3_path)` produces a complete `SongStructure` from any mp3 file
- [x] JSON serialisation round-trips: `SongStructure.from_json(s.to_json(path))` equals original
- [x] CLI `dreamsync analyze song.mp3` works end-to-end
- [x] Analysis completes in < 30 seconds for a typical 3–4 minute track
- [x] `SongStructure` contains all fields needed by the Show Compiler (Feature 3)
- [x] 14 unit tests covering orchestration, serialisation, round-trip, CLI integration

---

## Tests

All tests in `dev/tests/test_analyzer_*.py`. **80 tests** across 5 files — all passing.

| File | Area | Count |
|------|------|-------|
| `dev/tests/test_analyzer_decode.py` | Mp3Decoder: decode, resample, errors, formats | 13 |
| `dev/tests/test_analyzer_features.py` | OfflineFeaturePipeline: feature extraction, BPM accuracy, performance | 15 |
| `dev/tests/test_analyzer_bpm.py` | GlobalBpmEstimator: histogram, alias, tempo changes, beat grid, time signature | 18 |
| `dev/tests/test_analyzer_sections.py` | SectionSegmenter: boundaries, labels, snapping, clustering, edge cases | 20 |
| `dev/tests/test_analyzer_models.py` | SongStructure: serialisation, round-trip, analyze_song orchestration, CLI | 14 |

### Test Strategy

- **Decode tests**: Mock ffmpeg subprocess for unit tests. One integration test with a real mp3 (short sine wave, generated in test setup).
- **Feature tests**: Use synthetic signals (sine waves, silence, noise) to verify expected feature values. Compare against existing `extract_feature_frames()` output for consistency.
- **BPM tests**: Use synthetic click tracks at known BPM (generated with numpy). Verify global BPM matches input.
- **Section tests**: Use synthetic feature sequences with known structure (e.g., [low_energy × 50, high_energy × 100, low_energy × 50, high_energy × 100]) to verify boundary detection.
- **Model tests**: Pure serialisation tests, no audio required.

---

## Parameters

| Parameter | Default | Rationale |
|-----------|---------|-----------|
| `decode.target_sr` | 44100 | Matches existing DSP pipeline |
| `features.frame_size` | 2048 | ~46ms at 44.1kHz, good frequency resolution |
| `features.hop_size` | 512 | ~12ms hop, same as live pipeline |
| `bpm.histogram_bin_width` | 0.5 | BPM resolution (sufficient for ±2 BPM target) |
| `bpm.warmup_skip_seconds` | 2.0 | Skip first 2s of BPM estimates (estimator warmup) |
| `bpm.tempo_change_threshold` | 8.0 | BPM deviation to create new tempo region |
| `bpm.tempo_region_min_seconds` | 8.0 | Minimum duration for a tempo region |
| `bpm.preferred_bpm_range` | (80, 160) | Sweet spot for harmonic alias resolution |
| `sections.kernel_size` | 64 | Checkerboard kernel: ~0.75s at 512 hop / 44.1kHz |
| `sections.min_section_seconds` | 8.0 | Prevents over-segmentation |
| `sections.peak_threshold` | 0.3 | Novelty peak threshold (relative to max) |
| `sections.similarity_threshold` | 0.7 | Clustering threshold for structural ID assignment |
| `sections.feature_weights` | centroid=0.3, bass=0.25, flux=0.25, rms=0.1, onset=0.1 | Emphasise timbre over dynamics |

---

## Build Order

Steps are sequential within each deliverable but D4.1 must come first (everything needs PCM). D4.2 must precede D4.3–D4.4 (they need features).

| Phase | Step | Deliverable | Files Created/Modified |
|-------|------|-------------|----------------------|
| 1 | Create `analyzer/` package | Setup | `src/dreamsync/analyzer/__init__.py` |
| 2 | Implement `Mp3Decoder` | D4.1 | `src/dreamsync/analyzer/decode.py` |
| 3 | Refactor `live.py` helpers to be importable | D4.2 prep | `src/dreamsync/live.py` (extract functions) |
| 4 | Implement `OfflineFeaturePipeline` | D4.2 | `src/dreamsync/analyzer/features.py` |
| 5 | Write tests for D4.1–D4.2 | Tests | `dev/tests/test_analyzer_decode.py`, `test_analyzer_features.py` |
| 6 | Implement `GlobalBpmEstimator` | D4.3 | `src/dreamsync/analyzer/bpm.py` |
| 7 | Implement `SectionSegmenter` | D4.4 | `src/dreamsync/analyzer/sections.py` |
| 8 | Write tests for D4.3–D4.4 | Tests | `dev/tests/test_analyzer_bpm.py`, `test_analyzer_sections.py` |
| 9 | Implement `SongStructure` + `analyze_song()` | D4.5 | `src/dreamsync/analyzer/models.py`, `analyze.py` |
| 10 | Wire into CLI + session | Integration | `src/dreamsync/cli.py`, `session.py` |
| 11 | Model + integration tests | Tests | `dev/tests/test_analyzer_models.py` |
| 12 | Manual validation: analyze 10+ songs | Validation | — |

---

## Non-Goals

- **Spotify Audio Analysis API**: The `/v1/audio-analysis/{id}` endpoint is not used. All analysis is done locally from the PCM signal. This keeps the system independent of any external API and works with captured audio from any source.
- **Key detection**: Musical key / chord progression analysis is not in scope. Sections are identified by timbre, energy, and rhythm, not harmonic content.
- **Lyrics alignment**: No lyric-based section labelling.
- **Real-time incremental analysis**: The analyzer operates on complete files. Streaming/incremental analysis is a separate future concern.
- **Machine learning models**: Section segmentation uses signal processing (self-similarity + novelty curve), not trained ML models. This avoids model dependencies and keeps the system lightweight.

---

## Dependencies

| Dependency | Type | Notes |
|------------|------|-------|
| `numpy` | Existing | All numerical computation |
| `subprocess` | Stdlib | ffmpeg invocation (decode + probe) |
| `ffmpeg` | System binary | Must be installed and on PATH |
| `json` | Stdlib | SongStructure serialisation |
| `LiveBpmEstimator` | Existing code | `src/dreamsync/live.py` |
| `NoiseFloorEstimator` | Existing code | `src/dreamsync/live.py` |
| `PercussiveOnsetTracker` | Existing code | `src/dreamsync/live.py` |
| `SpectralBeatTemplate` | Existing code | `src/dreamsync/live.py` |
| `Director` | Existing code | `src/dreamsync/director.py` |
| `MoodClassifier` | Existing code | `src/dreamsync/mood.py` |
| `check_ffmpeg` | Existing code | `src/dreamsync/capture/writer.py` |

No new pip dependencies required. The self-similarity / novelty curve computation uses only numpy (cosine similarity via dot product, convolution via `np.convolve`).
