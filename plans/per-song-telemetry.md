# Per-Song Telemetry — Implementation Plan

## Goal

Log per-frame audio/mood/energy data to a separate file for each song (split on song boundaries). Enables post-session analysis like "song 2 was 128 BPM, mostly HYPE, high energy."

---

## Output Structure

```
out/session-20260225-214500/
  song-001.jsonl      # frames from session start → first boundary
  song-002.jsonl      # frames from boundary #1 → boundary #2
  song-003.jsonl      # ...
  session-summary.json  # per-song summaries + session totals
```

- Directory named with session start timestamp
- CLI flag: `--telemetry-dir <path>` (default: `out/`)
- Each `.jsonl` file is one JSON object per line (matches existing `--jsonl` pattern)

---

## Per-Frame Row (written every tick to current song file)

Extend the existing `log_row` dict (live.py:695-713) with fields already available in the loop:

```json
{
  "t": 142.31,
  "bpm": 127.3,
  "beat": true,
  "rms": 0.04812,
  "energy": 0.3812,
  "stability": 0.054,
  "mood": "groove",
  "effect": "beat_pulse",
  "palette": "vivid",
  "render_mode": "pulse",
  "bass_ratio": 0.38,
  "spectral_flux": 12.4,
  "onset_strength": 0.032
}
```

All values are already computed each frame — no new DSP work.

---

## Per-Song Summary (written as last line of each song file + into session-summary.json)

Accumulated in-memory during the song, flushed at boundary or session end:

```json
{
  "kind": "song_summary",
  "song_index": 2,
  "start_t": 183.0,
  "end_t": 305.0,
  "duration_seconds": 122.0,
  "frames": 10640,
  "beats": 259,
  "bpm_median": 127.3,
  "bpm_mean": 126.8,
  "bpm_std": 1.2,
  "energy_mean": 0.41,
  "energy_max": 0.78,
  "stability_mean": 0.06,
  "mood_distribution": {"chill": 0.08, "groove": 0.52, "hype": 0.35, "drop": 0.05},
  "effect_distribution": {"beat_pulse": 0.6, "fast_scroll": 0.4},
  "palette_distribution": {"vivid": 0.5, "neon": 0.3, "fire": 0.2},
  "dominant_mood": "groove",
  "dominant_effect": "beat_pulse"
}
```

---

## Code Changes

### 1. New file: `src/dreamsync/telemetry.py`

Small helper class (~80 lines) to manage per-song file rotation:

```python
class SongTelemetryWriter:
    def __init__(self, output_dir: Path):
        # Create timestamped session directory
        # Open song-001.jsonl
        # Initialize accumulators for summary stats

    def write_frame(self, row: dict) -> None:
        # Write JSON line to current song file
        # Accumulate stats (bpm list, energy sum, mood counts, etc.)

    def on_boundary(self, boundary_index: int, t: float) -> None:
        # Flush song summary as last line of current file
        # Close current file
        # Open next song file (song-002.jsonl, etc.)
        # Reset accumulators

    def close(self) -> dict:
        # Flush final song
        # Write session-summary.json (list of all song summaries)
        # Return session summary dict
```

Accumulators (reset each song):
- `bpm_values: list[float]` — for median/mean/std (only when bpm > 0)
- `energy_sum: float`, `energy_max: float`, `frame_count: int`
- `stability_sum: float`
- `mood_counts: dict[str, int]` — frame count per mood
- `effect_counts: dict[str, int]` — frame count per effect
- `palette_counts: dict[str, int]` — frame count per palette
- `beat_count: int`

### 2. Modify: `src/dreamsync/live.py` — `run_live_to_govee()`

Three integration points, all minimal:

**a) Initialization (after line ~595, near other setup):**
```python
telemetry = SongTelemetryWriter(telemetry_dir) if telemetry_dir else None
```

**b) Song boundary block (line 618-634, after existing resets):**
```python
if song_detector.update(rms):
    # ... existing resets ...
    if telemetry:
        telemetry.on_boundary(song_detector.boundary_count, stream_t)
```

**c) Frame logging block (line 695-713, extend existing log_row):**
```python
if telemetry and mood_classifier is not None:
    telemetry.write_frame({
        "t": round(stream_t, 4),
        "bpm": round(float(bpm_estimator.last_bpm), 2),
        "beat": bool(beat_this_tick),
        "rms": round(float(last_features["rms"]), 5) if last_features else 0.0,
        "energy": round(director.energy, 4),
        "stability": round(director.stability, 4),
        "mood": mood_classifier.mood.value,
        "effect": effect_cycler.current_effect,
        "palette": effect_cycler.current_palette,
        "render_mode": preset.render_mode.value if preset else None,
        "bass_ratio": round(float(last_features["bass_ratio"]), 4) if last_features else 0.0,
        "spectral_flux": round(float(last_features["spectral_flux"]), 4) if last_features else 0.0,
        "onset_strength": round(float(last_features.get("onset_strength", 0)), 4) if last_features else 0.0,
    })
```

**d) Session teardown (line ~745, before return):**
```python
if telemetry:
    telemetry.close()
```

### 3. Modify: `src/dreamsync/cli.py`

Add `--telemetry-dir` flag to `govee-live` and `session` subcommands:

```python
parser.add_argument("--telemetry-dir", type=Path, default=None,
                    help="Write per-song telemetry files to this directory")
```

Pass through to `run_live_to_govee()` as `telemetry_dir` parameter.

### 4. Tests: `tests/test_telemetry.py`

- `test_write_frame_creates_file` — write frames, verify JSONL output
- `test_on_boundary_rotates_file` — trigger boundary, verify song-001 closed and song-002 opened
- `test_song_summary_stats` — verify median BPM, mood distribution math
- `test_close_writes_session_summary` — verify session-summary.json contains all songs
- `test_no_telemetry_when_disabled` — verify None telemetry_dir is a no-op

---

## Not In Scope

- Real-time dashboard / live streaming of telemetry
- Retroactive analysis of existing bar-test log files (could add later as a script)
- Changes to `--debug-mood` stdout output
- Changes to existing `--jsonl` output format
