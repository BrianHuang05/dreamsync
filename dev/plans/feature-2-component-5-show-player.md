# Feature 2, Component 5 — Show Playback Runtime

**Status**: Code complete (65 unit tests)

---

## Overview

The Show Playback Runtime takes an mp3 audio file and a compiled show file (timestamped lighting cues with a beat grid), plays the audio through the system's default speaker output, and simultaneously dispatches lighting commands to Govee devices in real-time sync.

This is the pre-sequenced counterpart to `run_live_to_govee()`. Where v2 captures live audio → DSP → Director → LightingIntent every 12ms, Component 5 reads a pre-compiled timeline of cues and a pre-computed beat grid, plays back the audio file, and emits the appropriate LightingIntent at each position. The rendering and device output layers (`SegmentRenderer`, `MultiGoveeLanAdapter`, `GoveeBleAdapter`) are reused unchanged.

The core insight is that the main loop structure already exists in `run_live_to_govee()` (lines 1438–1698 of `live.py`). The v2 loop does: capture audio → extract features → Director decides intent → render → send. The v3 loop does: track audio position → look up cue in timeline → build intent from cue → render → send. Everything from "render → send" downward is identical.

---

## Architecture

```
                     ┌──────────────────────────────┐
                     │        mp3 audio file          │
                     └──────────────┬───────────────┘
                                    │
                                    ▼
                     ┌──────────────────────────────┐
                     │        AudioPlayer             │
                     │  Mp3Decoder → PCM (float32)    │
                     │  sounddevice OutputStream      │
                     │  → system speakers              │
                     │  Exposes: position_seconds,     │
                     │  play(), pause(), seek()         │
                     └──────────────┬───────────────┘
                                    │ current position (seconds)
                                    ▼
┌────────────────┐   ┌──────────────────────────────┐
│  Show file     │──▶│    ShowPlaybackRuntime         │
│  (JSON)        │   │  1. Read audio position        │
│  - beat grid   │   │  2. Find active ShowCue        │
│  - cues        │   │  3. Build LightingIntent       │
│  - transitions │   │  4. Check beat grid → beat flag │
└────────────────┘   │  5. Render + send               │
                     └──────────────┬───────────────┘
                                    │ LightingIntent + beat flag
                                    ▼
                     ┌──────────────────────────────┐
                     │   MultiGoveeLanAdapter         │
                     │   (unchanged from v2)           │
                     │   SegmentRenderer.render()      │
                     │   → GoveeLanAdapter.send_frame()│
                     │   → GoveeBleAdapter followers   │
                     └──────────────────────────────┘


Orchestrator:  run_show_playback()
               - Loads show file + decodes mp3
               - Creates AudioPlayer + ShowPlaybackRuntime
               - Main loop: position → cue → intent → render → send
               - Also exposed as CLI subcommand
```

### File Layout

```
src/dreamsync/
├── show/
│   ├── __init__.py
│   ├── models.py              # ShowTimeline, ShowCue (D5.1)
│   ├── player.py              # AudioPlayer (D5.2)
│   └── runtime.py             # ShowPlaybackRuntime + run_show_playback() (D5.3)
```

### Integration Points

- **Component 4 (Analyzer)** — `SongStructure` provides the beat grid and section data that the Show Compiler uses to produce the show file.
- **Feature 3 (Show Compiler)** — Produces the `ShowTimeline` JSON file that Component 5 consumes. The format defined here in D5.1 is the contract.
- **cli.py** — `play` subcommand for standalone show playback.
- **session.py** — Future: session-integrated playback where shows are triggered by Spotify track changes.
- **MultiGoveeLanAdapter** — Render + send layer, reused unchanged.
- **SegmentRenderer** — Per-device rendering, reused unchanged.

---

## D5.1: Show Timeline Model — `ShowTimeline` + `ShowCue`

### Design

Defines the file format that Component 5 expects as input. This is the contract between the Show Compiler (Feature 3) and the player. The format is designed to be self-contained: everything needed to play a show is in the file, including the beat grid (so the player can fire beat flags on the correct frames).

```python
@dataclass(frozen=True)
class ShowCue:
    t: float                          # absolute start time in seconds
    render_mode: str                  # "scroll" | "pulse" | "breathe" | "wave" | "solid" | "gradient"
    color_palette: tuple[str, ...]    # hex colors to cycle through
    intensity: float                  # 0.0–1.0 (maps to LightingIntent.intensity)
    speed: float                      # effect speed (maps to LightingIntent.speed)
    params: dict                      # renderer params: pulse_decay, breathe_rate_mult, etc.
    transition: str                   # "cut" | "fade"
    transition_beats: int             # beats to crossfade over (0 for hard cut)

@dataclass(frozen=True)
class ShowTimeline:
    song_path: str                    # original mp3 path (informational)
    duration: float                   # song duration in seconds
    bpm: float                        # global BPM
    time_signature: int               # beats per bar (4 or 3)
    beat_times: tuple[float, ...]     # absolute beat times (from BeatGrid)
    downbeat_times: tuple[float, ...] # bar boundaries (from BeatGrid)
    cues: tuple[ShowCue, ...]         # ordered by t, covering 0..duration
    metadata: dict                    # track_name, artist, etc.

    def to_dict(self) -> dict:
        """Serialise to a JSON-compatible dict."""

    def to_json(self, path: Path) -> None:
        """Write to a JSON file."""

    @classmethod
    def from_json(cls, path: Path) -> ShowTimeline:
        """Load from a JSON file."""

    @classmethod
    def from_dict(cls, data: dict) -> ShowTimeline:
        """Reconstruct from a dict."""

    def cue_at(self, t: float) -> ShowCue | None:
        """Find the active cue at time t (binary search)."""

    def is_beat(self, t: float, tolerance: float = 0.025) -> bool:
        """Check if time t is within tolerance of a beat."""

    def is_downbeat(self, t: float, tolerance: float = 0.025) -> bool:
        """Check if time t is within tolerance of a downbeat."""
```

**JSON format** (output of `to_json`):

```json
{
  "song_path": "captured_songs/20260303_142015.mp3",
  "duration": 237.5,
  "bpm": 128.0,
  "time_signature": 4,
  "beat_times": [0.12, 0.59, 1.06, 1.53, 2.0, ...],
  "downbeat_times": [0.12, 1.99, 3.87, 5.75, ...],
  "cues": [
    {
      "t": 0.0,
      "render_mode": "breathe",
      "color_palette": ["#ff8800", "#ffcc00", "#ff6600"],
      "intensity": 0.15,
      "speed": 0.22,
      "params": {"breathe_rate_mult": 0.5},
      "transition": "cut",
      "transition_beats": 0
    },
    {
      "t": 16.2,
      "render_mode": "scroll",
      "color_palette": ["#ff6600", "#ff4400", "#ff8800"],
      "intensity": 0.35,
      "speed": 0.4,
      "params": {"scroll_inject_width": 0.2},
      "transition": "fade",
      "transition_beats": 4
    },
    {
      "t": 48.5,
      "render_mode": "pulse",
      "color_palette": ["#ff0000", "#ff4400", "#ff8800"],
      "intensity": 0.72,
      "speed": 0.6,
      "params": {"pulse_decay": 6.0},
      "transition": "fade",
      "transition_beats": 2
    }
  ],
  "metadata": {
    "track_name": "Around the World",
    "artist": "Daft Punk"
  }
}
```

**Design decisions:**

- **Beat grid embedded in the show file**, not just in the SongStructure. The player should not need the SongStructure at runtime — the show file is self-contained.
- **Cues are sparse** — one cue per section change, not one per frame. The player holds the current cue until the next one activates. A 4-minute song might have 8–12 cues.
- **Color palette, not single color** — the player cycles through the palette on each beat (same behavior as the v2 Director with `set_colors()`).
- **Transition support** — `fade` transitions interpolate intensity/speed between the outgoing and incoming cue over N beats. `cut` transitions switch instantly. This is all the player needs to handle; the Show Compiler decides *what* transitions to use.

### Implementation Steps

1. Create `src/dreamsync/show/__init__.py` (empty).
2. Create `src/dreamsync/show/models.py`:
   - `ShowCue` frozen dataclass with t, render_mode, color_palette, intensity, speed, params, transition, transition_beats.
   - `ShowTimeline` frozen dataclass with song_path, duration, bpm, time_signature, beat_times, downbeat_times, cues, metadata.
   - `to_dict()` — serialize all tuples to lists, round floats to 4 decimal places.
   - `to_json(path)` — write to JSON file via `json.dump`.
   - `from_json(path)` / `from_dict(data)` — reconstruct frozen dataclasses.
   - `cue_at(t)` — binary search through `cues` to find the active cue at time t. Returns the cue whose `t` is ≤ the query time and whose successor's `t` is > the query time (or None if before first cue).
   - `is_beat(t, tolerance)` — binary search `beat_times` to check if `t` is within `tolerance` seconds of any beat.
   - `is_downbeat(t, tolerance)` — same for `downbeat_times`.

### Done When

- [ ] `ShowTimeline` and `ShowCue` are frozen dataclasses with all required fields
- [ ] JSON serialization round-trips: `ShowTimeline.from_json(tl.to_json(path))` equals original
- [ ] `cue_at(t)` correctly returns the active cue at any time position (tested at boundaries, mid-cue, before first cue, after last cue)
- [ ] `is_beat(t)` and `is_downbeat(t)` return correct results with configurable tolerance
- [ ] Binary search is efficient: O(log n) for `cue_at`, `is_beat`, `is_downbeat`
- [ ] Invalid show files (missing fields, unsorted cues, overlapping cues) raise clear errors on load
- [ ] 12 unit tests covering serialization, round-trip, cue lookup, beat detection, validation, edge cases

---

## D5.2: Audio Playback Engine — `AudioPlayer`

### Design

Plays an mp3 file through the system's default audio output device using `sounddevice` (already a project dependency for audio input). The mp3 is decoded to PCM via the existing `Mp3Decoder` (Component 4), then streamed through a `sounddevice.OutputStream` callback.

The key capability is **accurate position tracking**: the player knows exactly which sample is being output at any moment, allowing the ShowPlaybackRuntime to sync lighting cues to the audio within ±10ms.

```python
class AudioPlayer:
    def __init__(
        self,
        audio_path: Path,
        sample_rate: int = 44100,
        blocksize: int = 1024,
        device: int | None = None,    # None = system default output
    ) -> None:
        """Decode the mp3 and prepare for playback."""

    def play(self) -> None:
        """Start or resume playback from current position."""

    def pause(self) -> None:
        """Pause playback (audio stops, position freezes)."""

    def seek(self, t: float) -> None:
        """Jump to time t (seconds). Clamps to [0, duration]."""

    def stop(self) -> None:
        """Stop playback and release the audio stream."""

    @property
    def position_seconds(self) -> float:
        """Current playback position in seconds (sample-accurate)."""

    @property
    def duration(self) -> float:
        """Total duration in seconds."""

    @property
    def playing(self) -> bool:
        """True if audio is actively playing."""

    @property
    def finished(self) -> bool:
        """True if playback reached the end of the file."""
```

**Playback architecture:**

```
Mp3Decoder.decode_mp3(path)
    → AudioData(signal=float32[], sample_rate=44100)
        → sounddevice.OutputStream(callback=_audio_callback)
            → system default speakers

_audio_callback(outdata, frames, time_info, status):
    # Copy next `frames` samples from signal to outdata
    # Advance position counter (atomic int)
    # If past end of signal, zero-fill and set finished flag
```

**Key design decisions:**

- **Decode entire file to memory** — the full PCM signal is decoded once upfront, stored as a numpy array. A 4-minute song at 44.1kHz mono float32 ≈ 42 MB — easily fits in memory. This avoids streaming complexity and enables instant seek.
- **Mono output** — `Mp3Decoder` already outputs mono float32. `sounddevice` handles routing mono to both speaker channels. Audio quality is sufficient for this use case (the real listening source is the speaker system fed by the audio streaming service; this playback is for development/testing/standalone mode).
- **Position tracking via sample counter** — the output callback increments a sample counter atomically. `position_seconds` = `_position / sample_rate`. This is accurate to within one blocksize (~23ms at blocksize=1024, 44.1kHz).
- **No new dependencies** — `sounddevice` is already installed for audio input. `Mp3Decoder` is already built (Component 4). `ffmpeg` is already required.

### Implementation Steps

1. Create `src/dreamsync/show/player.py`:
   - `AudioPlayer`:
     - `__init__(audio_path, sample_rate, blocksize, device)`:
       1. Call `decode_mp3(audio_path, target_sr=sample_rate)` → `AudioData`.
       2. Store `self._signal = audio_data.signal` (mono float32 numpy array).
       3. Store sample_rate, blocksize, device.
       4. Initialize `self._position = 0` (int, current sample index).
       5. Initialize `self._playing = False`, `self._finished = False`.
       6. Do NOT create the sounddevice stream yet (created in `play()`).
     - `_audio_callback(outdata, frames, time_info, status)`:
       1. If not `self._playing`, fill `outdata` with zeros, return.
       2. Compute `end = self._position + frames`.
       3. If `end > len(self._signal)`: copy remaining samples, zero-fill rest, set `self._finished = True`.
       4. Otherwise: copy `self._signal[self._position:end]` into `outdata[:, 0]`.
       5. Update `self._position = min(end, len(self._signal))`.
     - `play()`:
       1. If stream not created, create `sd.OutputStream(samplerate, blocksize, device, channels=1, dtype='float32', callback=_audio_callback)` and start it.
       2. Set `self._playing = True`.
     - `pause()`: set `self._playing = False`. Stream stays open (callback outputs zeros).
     - `seek(t)`: set `self._position = int(t * self._sr)`, clamped to `[0, len(signal)]`. Reset `self._finished = False` if seeking backward.
     - `stop()`: set `self._playing = False`, close and release the stream.
     - Properties: `position_seconds`, `duration`, `playing`, `finished`.

### Done When

- [ ] `AudioPlayer` plays an mp3 file through system default speakers
- [ ] Audio quality is acceptable (no crackling, glitches, or dropouts)
- [ ] `position_seconds` is accurate to within ±25ms of actual playback position
- [ ] `pause()` stops audio output, `play()` resumes from the paused position
- [ ] `seek(t)` jumps to the specified position and audio continues from there
- [ ] `finished` flag is set when playback reaches end of file
- [ ] `stop()` cleanly releases the audio stream and system resources
- [ ] Works with mp3, wav, flac (any format `Mp3Decoder` supports)
- [ ] Handles missing/corrupt files gracefully (raises `DecodeError`)
- [ ] 10 unit tests covering playback lifecycle, seek, pause/resume, position tracking, edge cases (empty file, very short file, file not found)

---

## D5.3: Show Playback Runtime — `ShowPlaybackRuntime` + `run_show_playback()`

### Design

The main playback loop that synchronizes lighting with audio. This is structurally similar to `run_live_to_govee()` but dramatically simpler: no DSP pipeline, no Director, no BPM estimation. The loop reads the current audio position, looks up the active cue, builds a `LightingIntent`, checks the beat grid, and dispatches to the device adapters.

```python
class ShowPlaybackRuntime:
    def __init__(
        self,
        timeline: ShowTimeline,
        multi_adapter: MultiGoveeLanAdapter,
    ) -> None:
        """Prepare the runtime with a show timeline and device adapter."""

    def tick(self, t: float) -> bool:
        """Advance the show to time t.

        Finds the active cue, builds a LightingIntent, checks beats,
        renders, and sends to all devices.

        Returns True if a frame was sent, False if rate-limited.
        """

    @property
    def current_cue(self) -> ShowCue | None:
        """The currently active ShowCue."""

    @property
    def current_beat_index(self) -> int:
        """Index into the beat grid at the current position."""


def run_show_playback(
    mp3_path: Path,
    show_path: Path,
    multi_adapter: MultiGoveeLanAdapter,
    *,
    sample_rate: int = 44100,
    audio_device: int | None = None,
    stop_event: threading.Event | None = None,
    debug: bool = False,
) -> dict[str, Any]:
    """Play a show: mp3 audio + lighting timeline → speakers + Govee devices.

    This is the top-level orchestrator, analogous to run_live_to_govee().
    Returns a summary dict when playback finishes or is interrupted.
    """
```

**Main loop structure** (inside `run_show_playback`):

```python
def run_show_playback(...):
    # 1. Load show timeline
    timeline = ShowTimeline.from_json(show_path)

    # 2. Create audio player
    player = AudioPlayer(mp3_path, sample_rate=sample_rate, device=audio_device)

    # 3. Create runtime
    runtime = ShowPlaybackRuntime(timeline, multi_adapter)

    # 4. Activate devices
    multi_adapter.activate(brightness=100)

    # 5. Start audio playback
    player.play()

    # 6. Main loop (mirrors run_live_to_govee structure)
    while not player.finished:
        if stop_event is not None and stop_event.is_set():
            break
        t = player.position_seconds
        runtime.tick(t)
        time.sleep(0.005)   # ~200Hz tick, same as v2

    # 7. Cleanup
    player.stop()
    multi_adapter.deactivate()

    return summary
```

**ShowPlaybackRuntime.tick() logic:**

```python
def tick(self, t: float) -> bool:
    # 1. Find active cue
    cue = self._timeline.cue_at(t)
    if cue is None:
        return False

    # 2. Handle cue transitions
    if cue != self._current_cue:
        self._on_cue_change(cue, t)

    # 3. Build LightingIntent from cue
    #    - mode: map cue.render_mode to EffectMode
    #    - intensity: cue.intensity (or interpolated during fade)
    #    - speed: cue.speed (or interpolated during fade)
    #    - bpm: self._timeline.bpm
    #    - color: cycle through cue.color_palette on each beat
    intent = self._build_intent(cue, t)

    # 4. Check beat grid
    beat = self._timeline.is_beat(t, tolerance=self._beat_tolerance)

    # 5. Advance color on beat
    if beat and not self._beat_fired:
        self._color_index = (self._color_index + 1) % len(cue.color_palette)
        self._beat_fired = True
    elif not beat:
        self._beat_fired = False

    # 6. Render + send to all devices
    return self._multi_adapter.send_frame(t, intent, beat=beat, params=cue.params)
```

**Cue transitions (fade handling):**

When transitioning between cues with `transition="fade"`, the runtime interpolates intensity and speed over the specified number of beats:

```
outgoing_cue.intensity ──────┐
                              ├── linear blend over N beats ──▶ incoming_cue.intensity
incoming_cue.intensity ──────┘
```

The transition period starts at the incoming cue's `t` and lasts `transition_beats * (60 / bpm)` seconds. During this window:
- `intensity = lerp(old_intensity, new_intensity, progress)`
- `speed = lerp(old_speed, new_speed, progress)`
- The render mode switches immediately to the incoming cue's mode (gradual mode blending is not supported — effects like scroll and pulse have incompatible internal state).

**EffectMode mapping:**

The show cue uses `render_mode` (a `RenderMode` string) while `LightingIntent` uses `EffectMode`. The mapping:

| ShowCue.render_mode | EffectMode | RenderMode set on renderer |
|---------------------|------------|----------------------------|
| "solid" | AMBIENT | SOLID |
| "breathe" | AMBIENT | BREATHE |
| "scroll" | MOTION | SCROLL |
| "pulse" | PULSE | PULSE |
| "wave" | MOTION | WAVE |
| "gradient" | AMBIENT | GRADIENT |

The `RenderMode` is set directly on each device's `SegmentRenderer.mode` when a cue changes (same as the v2 effect cycler does). The `EffectMode` in the `LightingIntent` determines how the Director would classify the mode, but since we bypass the Director entirely, it's used only for intent construction.

### Implementation Steps

1. Create `src/dreamsync/show/runtime.py`:
   - `ShowPlaybackRuntime`:
     - `__init__(timeline, multi_adapter)`:
       1. Store timeline and multi_adapter.
       2. Initialize `_current_cue = None`, `_color_index = 0`, `_beat_fired = False`.
       3. Initialize fade state: `_fade_start_t = 0.0`, `_fade_end_t = 0.0`, `_fade_from_cue = None`.
       4. Pre-compute `_beat_tolerance = 0.5 * (60.0 / timeline.bpm) * 0.5` — half a beat-interval's worth of tolerance, capped at 25ms.
     - `tick(t)`:
       1. Call `timeline.cue_at(t)` to find active cue.
       2. If cue changed from `_current_cue`, handle transition:
          a. If incoming cue has `transition="fade"` and `transition_beats > 0`:
             - Store `_fade_from_cue = _current_cue`.
             - Compute `_fade_end_t = t + transition_beats * 60.0 / timeline.bpm`.
             - Set `_fade_start_t = t`.
          b. Switch render mode on all renderers: `renderer.mode = RenderMode(cue.render_mode)`.
          c. Update `_current_cue = cue`.
       3. Build intent via `_build_intent(cue, t)`:
          a. Compute intensity and speed (interpolated if in fade window).
          b. Determine color from palette using `_color_index`.
          c. Construct `LightingIntent(mode=..., intensity=..., speed=..., bpm=timeline.bpm, color=...)`.
       4. Check `timeline.is_beat(t)` for beat flag.
       5. On beat (with debounce): advance `_color_index`, set `_beat_fired`.
       6. Call `multi_adapter.send_frame(t, intent, beat=beat, params=cue.params)`.
     - `_build_intent(cue, t)`:
       1. If in fade window (`_fade_start_t <= t < _fade_end_t` and `_fade_from_cue` exists):
          - `progress = (t - _fade_start_t) / (_fade_end_t - _fade_start_t)`.
          - `intensity = lerp(_fade_from_cue.intensity, cue.intensity, progress)`.
          - `speed = lerp(_fade_from_cue.speed, cue.speed, progress)`.
       2. Otherwise: use cue.intensity, cue.speed directly.
       3. Map render_mode to EffectMode.
       4. Get color from `cue.color_palette[_color_index % len(palette)]`.
       5. Return `LightingIntent(...)`.
   - `run_show_playback(mp3_path, show_path, multi_adapter, *, ...)`:
     1. Load `ShowTimeline.from_json(show_path)`.
     2. Create `AudioPlayer(mp3_path, sample_rate, device=audio_device)`.
     3. Create `ShowPlaybackRuntime(timeline, multi_adapter)`.
     4. Activate devices: `multi_adapter.activate(brightness=100)`.
     5. Start playback: `player.play()`.
     6. Main loop:
        ```
        while not player.finished:
            if stop_event and stop_event.is_set(): break
            t = player.position_seconds
            sent = runtime.tick(t)
            time.sleep(0.005)
        ```
     7. Cleanup: `player.stop()`, `multi_adapter.deactivate()`.
     8. Return summary dict: duration, frames_sent, cues_played, beats_hit.

2. Wire into CLI (D5.4 below).

### Done When

- [ ] `ShowPlaybackRuntime.tick(t)` correctly maps position → cue → intent → render → send
- [ ] Cue changes switch render mode on all device renderers instantly
- [ ] Color palette cycling advances on each beat (using beat grid, not live detection)
- [ ] Fade transitions interpolate intensity and speed over the configured beat count
- [ ] Beat flags fire within ±25ms of the beat grid positions
- [ ] `run_show_playback()` plays audio and lights simultaneously from start to finish
- [ ] Audio and light output are synchronized within ±50ms (perceptually aligned)
- [ ] Playback handles clean shutdown on SIGINT/SIGTERM (stop_event)
- [ ] Playback handles reaching end-of-file gracefully (devices deactivated, summary returned)
- [ ] Debug mode prints cue changes, beat counts, and position to stdout
- [ ] 12 unit tests covering tick logic, cue lookup, fade interpolation, beat detection, intent construction, and end-to-end playback flow

---

## D5.4: CLI + Integration

### CLI Subcommand

```
dreamsync play song.mp3 --show show.json --config devices.yaml [options]
```

**Arguments:**

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `mp3_path` | positional | required | Path to the mp3 audio file |
| `--show` | Path | required | Path to the show timeline JSON file |
| `--config` | Path | required | Path to YAML device config (same as `session`) |
| `--sample-rate` | int | 44100 | Audio sample rate |
| `--audio-device` | int | None | Output audio device ID (None = system default) |
| `--fps` | int | 30 | Device frame rate |
| `--brightness` | float | 1.0 | Global brightness (0–1) |
| `--mirror` / `--no-mirror` | flag | True | Center-outward vs left-to-right rendering |
| `--debug` | flag | False | Print cue changes, beat counts, position |

**Example usage:**

```bash
# Play a show
dreamsync play song.mp3 --show song_show.json --config devices.yaml

# With debug output
dreamsync play song.mp3 --show song_show.json --config devices.yaml --debug

# Specific audio output device
dreamsync play song.mp3 --show song_show.json --config devices.yaml --audio-device 3
```

### Implementation Steps

1. Add `play` subcommand to `build_parser()` in `cli.py`:
   - Positional arg: `mp3_path` (Path).
   - Required: `--show` (Path), `--config` (Path).
   - Optional: `--sample-rate`, `--audio-device`, `--fps`, `--brightness`, `--mirror`/`--no-mirror`, `--debug`.

2. Add command handler in `main()`:
   - Load device config via `load_device_config(config_path)`.
   - Probe devices via `detect_all_devices(configs)`.
   - Build adapter via `build_multi_adapter(detected, ...)`.
   - Call `run_show_playback(mp3_path, show_path, multi_adapter, ...)`.
   - Print summary as JSON.

3. Signal handling:
   - Register SIGINT/SIGTERM → `stop_event.set()` (same pattern as `session`).
   - Clean shutdown: audio stops, devices deactivated.

### Done When

- [ ] `dreamsync play song.mp3 --show show.json --config devices.yaml` works end-to-end
- [ ] Device probing, activation, and deactivation work correctly
- [ ] Signal handling (Ctrl+C) stops playback cleanly
- [ ] Debug output shows cue transitions, beat counts, playback position
- [ ] Error messages are clear for missing files, invalid JSON, device probe failures
- [ ] 6 unit tests covering CLI argument parsing, command wiring, error handling

---

## Tests

All tests in `dev/tests/test_show_*.py`. Target: **40 tests** across 4 files.

| File | Area | Count |
|------|------|-------|
| `dev/tests/test_show_models.py` | ShowTimeline + ShowCue: serialization, round-trip, cue_at, is_beat, validation | 12 |
| `dev/tests/test_show_player.py` | AudioPlayer: playback lifecycle, seek, pause/resume, position tracking, edge cases | 10 |
| `dev/tests/test_show_runtime.py` | ShowPlaybackRuntime: tick, cue switching, fade, beat detection, intent construction | 12 |
| `dev/tests/test_show_cli.py` | CLI: argument parsing, command dispatch, error handling | 6 |

### Test Strategy

- **Model tests**: Pure dataclass tests — no audio, no devices. Construct ShowTimeline programmatically, test serialization round-trip, cue lookup at various timestamps, beat detection with known beat grids.
- **Player tests**: Mock `sounddevice` to avoid requiring audio hardware in CI. Verify that `AudioPlayer` calls `decode_mp3` correctly, tracks position, handles pause/resume/seek. One integration test (marked `@pytest.mark.slow`) that actually plays audio if sounddevice is available.
- **Runtime tests**: Mock `MultiGoveeLanAdapter.send_frame()` to capture calls. Build a ShowTimeline with known cues and beats, call `tick()` at specific times, verify that the correct LightingIntent is sent with the correct beat flag and params. Test fade transitions by ticking through the transition window and checking interpolated values.
- **CLI tests**: Test argument parsing with `build_parser().parse_args()`. Mock `run_show_playback` to verify it's called with correct arguments.

---

## Parameters

| Parameter | Default | Rationale |
|-----------|---------|-----------|
| `player.sample_rate` | 44100 | Matches existing DSP pipeline and decode output |
| `player.blocksize` | 1024 | Matches existing audio callback blocksize |
| `runtime.beat_tolerance` | 0.025 | 25ms — half the v2 frame interval; tight enough for perceptual sync |
| `runtime.tick_interval` | 0.005 | 5ms sleep = ~200Hz tick rate, same as `run_live_to_govee()` |
| `runtime.max_fade_beats` | 16 | Cap fade transitions at 16 beats to prevent overly slow crossfades |

---

## Build Order

Steps are sequential within each deliverable. D5.1 must come first (the model is used by everything). D5.2 and D5.3 are semi-independent but D5.3 depends on D5.2's position tracking interface.

| Phase | Step | Deliverable | Files Created/Modified |
|-------|------|-------------|----------------------|
| 1 | Create `show/` package | Setup | `src/dreamsync/show/__init__.py` |
| 2 | Implement `ShowTimeline` + `ShowCue` models | D5.1 | `src/dreamsync/show/models.py` |
| 3 | Write model tests | Tests | `dev/tests/test_show_models.py` |
| 4 | Implement `AudioPlayer` | D5.2 | `src/dreamsync/show/player.py` |
| 5 | Write player tests | Tests | `dev/tests/test_show_player.py` |
| 6 | Implement `ShowPlaybackRuntime` + `run_show_playback()` | D5.3 | `src/dreamsync/show/runtime.py` |
| 7 | Write runtime tests | Tests | `dev/tests/test_show_runtime.py` |
| 8 | Wire into CLI | D5.4 | `src/dreamsync/cli.py` |
| 9 | Write CLI tests | Tests | `dev/tests/test_show_cli.py` |
| 10 | Manual validation: play 3+ shows on real devices | Validation | — |

---

## Non-Goals

- **Show compilation / generation**: Component 5 is the *player*, not the compiler. Generating the `ShowTimeline` from a `SongStructure` is the responsibility of the Show Compiler (Feature 3). Component 5 only needs to *consume* the show file.
- **Spotify position sync**: In standalone `play` mode, timing comes from the local `AudioPlayer`. Spotify-synced playback (where the audio comes from Spotify and the player only dispatches lights) is a future session-mode feature.
- **Stereo / multichannel audio output**: Audio is decoded to mono and played mono. The source audio for actual listening comes from the audio streaming service (Spotify) through the speaker system. This playback channel is for development, testing, and standalone mode.
- **Gapless / crossfaded multi-song playback**: `play` handles one song at a time. Continuous multi-song playback with inter-song transitions is a session-mode feature.
- **Audio effects / equalization**: No processing is applied to the audio output. Pass-through only.
- **Live fallback within a show**: If a show file is loaded, the entire song plays from the show timeline. Falling back to v2 Director mid-song is not supported. Fallback is handled at the session level (Feature 6: v2 Fallback Switch).

---

## Dependencies

| Dependency | Type | Notes |
|------------|------|-------|
| `numpy` | Existing | PCM signal storage |
| `sounddevice` | Existing | Audio output (OutputStream) — already used for input |
| `json` | Stdlib | ShowTimeline serialization |
| `threading` | Stdlib | stop_event for clean shutdown |
| `bisect` | Stdlib | Binary search for cue_at and is_beat |
| `Mp3Decoder` | Existing code | `src/dreamsync/analyzer/decode.py` (Component 4) |
| `MultiGoveeLanAdapter` | Existing code | `src/dreamsync/output/govee_lan.py` |
| `SegmentRenderer` | Existing code | `src/dreamsync/render.py` |
| `LightingIntent`, `EffectMode` | Existing code | `src/dreamsync/director.py` |
| `RenderMode` | Existing code | `src/dreamsync/render.py` |
| `load_device_config`, `detect_all_devices`, `build_multi_adapter` | Existing code | `src/dreamsync/output/auto_detect.py` |
| `ffmpeg` | System binary | Required by Mp3Decoder for mp3 → PCM |

No new pip dependencies required.
