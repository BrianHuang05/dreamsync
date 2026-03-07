# DreamSync — Audio-Reactive Govee LAN Control

Windows-first Python project for local audio-reactive lighting. Listens to system audio, detects beats and energy, and streams per-segment RGB frames directly to Govee devices over LAN UDP — no cloud, no LedFx dependency.

## Quick start

```bash
python -m venv .venv
. .venv/Scripts/activate
pip install -e .
```

## Device discovery

Find Govee devices on your network:

```bash
python -m dreamsync govee-scan
```

## Live music sync

Play music on your PC, then run `govee-live` to drive the lights in real time.

### Single device

```bash
python -m dreamsync govee-live \
    --device-ip 10.0.0.123 \
    --segments 15 \
    --duration 120 \
    --render-mode scroll \
    --brightness 0.8
```

### Multiple devices (different hardware)

Use `--device IP:SEGMENTS:ROLE:TRANSPORT` to target multiple strips. Each device can have its own segment count and transport protocol.

```bash
python -m dreamsync govee-live \
    --device 10.0.0.1:7:primary:ptreal \
    --device 10.0.0.2:25:primary:razer \
    --duration 120 \
    --render-mode scroll
```

### Key options

| Flag | Default | Description |
|------|---------|-------------|
| `--device-ip` | | Single device IP (use with `--segments`) |
| `--device` | | Repeatable device spec: `IP:SEGMENTS[:ROLE[:TRANSPORT]]` |
| `--segments` | 15 | Segment count (with `--device-ip`) |
| `--duration` | | Capture duration in seconds (required) |
| `--render-mode` | scroll | `solid`, `pulse`, `scroll`, or `breathe` |
| `--transport` | ptreal | `razer` (DreamView per-LED), `ptreal` (BLE-over-LAN per-segment), `colorwc` (whole-strip) |
| `--fps` | 30 | Frame rate |
| `--brightness` | 1.0 | Global brightness (0-1) |
| `--colors` | auto | Comma-separated hex colors to cycle on beats |
| `--mirror` / `--no-mirror` | mirror | Scroll from center outward vs left-to-right |
| `--half-time` | off | Halve detected BPM (fixes octave-doubled detection) |
| `--max-brightness` | off | Force all frames to full intensity |
| `--auto-cycle` / `--no-auto-cycle` | on | Mood-driven effect cycling |
| `--cycle-interval` | 16 | Seconds between effect changes within same mood |
| `--debug-mood` | off | Print mood, effect, BPM, and song boundary events to stdout |
| `--audio-device` | system default | PortAudio input device ID |

## Debug / dry-run testing (no lights needed)

Use a fake IP to test the audio analysis pipeline without any hardware connected. UDP sends are fire-and-forget, so they silently fail on unreachable IPs while the full BPM, mood, effect, and song boundary pipeline runs normally.

```bash
# Watch mood/effect/BPM transitions live (60 seconds)
python -m dreamsync govee-live \
    --device 192.168.0.99:7:primary:ptreal \
    --duration 60 \
    --debug-mood

# Longer run to test song boundary detection across a playlist
python -m dreamsync govee-live \
    --device 192.168.0.99:7:primary:ptreal \
    --duration 600 \
    --debug-mood
```

With `--debug-mood` you'll see output like:

```
mood=chill effect=slow_breathe palette=cool mode=breathe energy=0.0812 stability=0.0340 bpm=127.3
mood=groove effect=beat_pulse palette=vivid mode=pulse energy=0.3812 stability=0.0540 bpm=128.1
*** Song boundary detected (#1) — state reset ***
mood=chill effect=wave_drift palette=warm mode=wave energy=0.0023 stability=0.0000 bpm=0.0
```

## Smoke tests

Send a solid color to verify connectivity:

```bash
python -m dreamsync govee-test --device-ip 10.0.0.123 --segments 15 --color '#ff0000' --duration 5
```

Test patterns for segment diagnostics:

```bash
python -m dreamsync govee-test --device-ip 10.0.0.123 --segments 15 --pattern rainbow
python -m dreamsync govee-test --device-ip 10.0.0.123 --segments 15 --pattern walk
```

## Infinite session mode (YAML config)

For production use with a device config file. Runs until Ctrl+C, auto-detects device roles, and handles song boundaries automatically.

```bash
python -m dreamsync session --config devices.yaml --debug-mood
```

### Color profiles

8 built-in color profiles control palette selection and effect pools per mood. Use `--profile` to load one, or `--profile-rotation` to cycle through several:

```bash
# List available profiles
python -m dreamsync profiles --verbose

# Run with a specific profile
python -m dreamsync govee-live --device 10.0.0.1:7:primary:ptreal --duration 120 --profile aurora --debug-mood

# Rotate through profiles every 60 seconds
python -m dreamsync govee-live --device 10.0.0.1:7:primary:ptreal --duration 300 \
    --profile-rotation aurora,neon_city,midnight_rave --rotation-interval 60 --debug-mood

# Validate a profile's YAML structure
python -m dreamsync profile-validate aurora
```

Editing a profile YAML under `src/dreamsync/profiles/` during a live session triggers a hot-reload within ~2 seconds.

| Flag | Default | Description |
|------|---------|-------------|
| `--profile` | none | Load a named color profile |
| `--profile-rotation` | none | Comma-separated profile names to rotate through |
| `--rotation-interval` | 60 | Seconds between profile rotations |

### Device health monitoring

Enable periodic probing to detect offline/online transitions and auto-pause/resume devices:

```bash
python -m dreamsync session --config devices.yaml --health-monitor --health-interval 30 --debug-mood
```

| Flag | Default | Description |
|------|---------|-------------|
| `--health-monitor` | off | Enable periodic device health probing |
| `--health-interval` | 30 | Seconds between health probes |
| `--health-discovery` | off | Scan for new devices on the network |

When a device goes offline (3 consecutive failed probes), its adapter is paused. When it comes back (2 consecutive successes), it resumes automatically. The audio pipeline is never blocked.

## MP3 song capture

Record per-song MP3 files from system audio using FFmpeg. Requires [VB-Audio Virtual Cable](https://vb-audio.com/Cable/) for audio routing on Windows.

### Audio routing setup (Windows)

The capture pipeline uses FFmpeg to read from VB-Cable's output device. You must route system audio through VB-Cable so the capture can see it.

1. **Install VB-Audio Virtual Cable** from https://vb-audio.com/Cable/ (free)
2. **Set CABLE Input as default playback device:**
   - Open **Settings > System > Sound** (or right-click the speaker icon in the taskbar)
   - Under **Output**, select **CABLE Input (VB-Audio Virtual Cable)**
   - All system audio now flows into the virtual cable instead of your speakers
3. **Echo audio back to your speakers:**
   - Open **Control Panel > Sound** (the classic panel, not Settings)
   - Go to the **Recording** tab
   - Right-click **CABLE Output (VB-Audio Virtual Cable)** > **Properties**
   - Go to the **Listen** tab
   - Check **"Listen to this device"**
   - In the dropdown, select your real speakers/headphones
   - Click **OK**
4. **Verify:** Play music. You should hear it through your speakers, and running `ffmpeg -hide_banner -list_devices true -f dshow -i dummy 2>&1` should show `CABLE Output (VB-Audio Virtual Cable)` as an available audio device.

The audio path is: App -> CABLE Input -> CABLE Output -> FFmpeg capture (+ echoed to speakers via Listen).

### Standalone capture

```bash
python -m dreamsync capture --duration 300 --mp3 --output-dir ./songs --naming metadata
```

### Capture during a live session

```bash
python -m dreamsync session --config devices.yaml --capture --capture-dir ./songs --capture-naming metadata --spotify
```

### Capture with govee-live

```bash
python -m dreamsync govee-live --device 10.0.0.1:7:primary:ptreal --duration 300 --capture --capture-dir ./songs
```

| Flag | Default | Description |
|------|---------|-------------|
| `--mp3` | off | Enable MP3 capture pipeline (capture subcommand) |
| `--capture` | off | Enable MP3 capture pipeline (session/govee-live) |
| `--output-dir` / `--capture-dir` | `captured_songs` | Output directory for MP3 files |
| `--naming` / `--capture-naming` | `timestamp` | Filename scheme: `timestamp` or `metadata` |
| `--device-pattern` | `CABLE Output` | DirectShow audio device for FFmpeg |

With `--spotify`, song boundaries come from Spotify's queue API for frame-accurate splits. Without Spotify, the pipeline captures continuously without splitting.

## List audio devices

```bash
python -m dreamsync devices
```

## Run tests

```bash
python -m pytest tests/ -v        # Core subsystems (569 tests)
python -m pytest dev/tests/ -v    # Audio capture pipeline (1353 tests)
```

1353 tests total covering all subsystems:

### Core tests (`tests/`)

| Test file | Tests | Scope |
|---|---|---|
| `test_song_boundary.py` | 14 | Boundary detector, reset methods for BPM/Director/Mood/Effects |
| `test_crossfade_boundary.py` | 13 | Crossfade detector: signal voting, thresholds, cooldown, confirm frames |
| `test_govee_ble.py` | 54 | BLE adapter, packet builders, threading, keep-alive |
| `test_telemetry.py` | 11 | JSONL writing, file rotation, song summaries, session summary |
| `test_config_watcher.py` | 21 | ConfigWatcher mtime detection/reload/add/remove, thread safety |
| `test_auto_detect.py` | 24 | Auto-detect role classification, latency probing |
| `test_profile.py` | 63 | Profile loader/validator, EffectCycler integration, ProfileWatcher, rotation, all 8 built-ins |
| `test_device_health.py` | 29 | Health monitor probe loop, offline/online thresholds, anti-flap, role reclass, discovery |
| `test_live_bpm.py` | 95 | BPM estimation, hybrid onset, noise floor, HPSS percussive, spectral template, noise-lock fixes, harmonic classifier + lock, IOI histogram |
| `test_spectral_template.py` | 6 | SpectralBeatTemplate bootstrap, similarity, adaptation, reset |
| `test_dsp_features.py` | 21 | Feature extraction, spectral features, BPM autocorrelation confidence |

### Audio capture pipeline tests (`dev/tests/`)

| Test file | Tests | Scope |
|---|---|---|
| `test_ffmpeg_device.py` | 13 | FFmpeg DirectShow device discovery, stderr parsing |
| `test_capture_process.py` | 13 | Capture process lifecycle, config validation |
| `test_pcm_reader.py` | 14 | Frame-aligned PCM chunking, partial reads, EOF |
| `test_pcm_buffer.py` | 24 | Thread-safe queue buffer, frame counter, backpressure |
| `test_segment_boundary.py` | 11 | Boundary computation from song duration arrays |
| `test_encoder_process.py` | 10 | Per-segment FFmpeg MP3 encoder lifecycle |
| `test_split_logic.py` | 12 | Zero-loss PCM split at frame boundaries |
| `test_file_namer.py` | 12 | Deterministic naming, sanitization, collision avoidance |
| `test_boundary_queue.py` | 17 | Mutable boundary queue, safety margin enforcement |
| `test_timing_integrator.py` | 8 | External timing integration, periodic refresh |
| `test_pipeline_logger.py` | 12 | Structured JSON + console logging |
| `test_metadata_writer.py` | 12 | JSON sidecar files, atomic writes |
| `test_drift_detector.py` | 12 | 4-level drift detection, boundary correction |
| `test_recovery_manager.py` | 10 | Capture/encoder failure recovery cascade |
| `test_orchestrator.py` | 87 | CaptureOrchestrator: config, lifecycle, threads, DynamicSplitProcessor, timing, drift, recovery, logging |
| `test_cli_capture.py` | 13 | CLI integration: --mp3 args, govee-live --capture, session orchestrator config, Spotify wiring |
| `test_capture_integration.py` | 30 | E2E: split accuracy, metadata sidecars, dynamic boundaries, drift, failure injection, logging, file naming |
| `test_timing_debounce.py` | 5 | Tick debounce after track change: suppression, expiry, logging |
| `test_min_segment_guard.py` | 6 | Min segment duration guard: discard short segments, delete temp files |
| `test_outputfile_metadata.py` | 5 | outputFile sidecar shows renamed path, fallback to encoder path |

## Architecture

```
System Audio → LiveBpmEstimator → beat events + BPM
                                      │
                                      ▼
                               BeatRippleController
                                      │
                                      ▼
                              LightingIntent { mode, intensity, color, bpm }
                                      │
                                      ▼
                              SegmentRenderer → RGB frame buffer (N segments)
                                      │
                                      ▼
                              GoveeLanAdapter → UDP packet to device:4003
```

### Module map

| Component | Location | Key classes/functions |
|---|---|---|
| CLI entry point + all subcommands | `cli.py` | `build_parser()`, `main()` |
| Audio capture (WASAPI loopback) | `live.py` | `LiveBpmEstimator` |
| Beat detection (hybrid onset, adaptive threshold, kick isolation) | `bpm.py` | `BeatDetector` |
| Noise-robust BPM (IOI histogram, HPSS percussive onset, bass-frequency gating, subharmonic snap, autocorrelation confidence, energy-gated template, selectivity monitor, harmonic-lock stabilization) | `live.py`, `dsp/features.py` | `LiveBpmEstimator`, `extract_features()` |
| Composite energy metric (RMS + spectral flux + bass + onset) | `director.py` | `Director`, `DirectorConfig` |
| Mood classification (CHILL / GROOVE / HYPE / DROP) | `mood.py` | `MoodClassifier`, `MoodConfig` |
| Effect cycling + color profiles | `effects.py`, `profile.py` | `EffectCycler`, `ProfileLoader`, `ProfileWatcher` |
| Segment rendering (SOLID, PULSE, SCROLL, BREATHE, STROBE, WAVE, GRADIENT) | `render.py` | `SegmentRenderer` |
| LAN output (ptreal, razer, colorwc over UDP) | `output/govee_lan.py` | `GoveeLanAdapter` |
| BLE output (bleak GATT, mood-follower mode) | `output/govee_ble.py` | `GoveeBleAdapter` |
| Auto-detect device roles (latency-based classification) | `output/auto_detect.py` | `auto_detect_roles()` |
| Device health monitor (probe loop, offline/online, role reclass) | `device_health.py` | `DeviceHealthMonitor` |
| Song boundary detection (silence-gap + crossfade voting) | `live.py` | `SongBoundaryDetector`, `CrossfadeBoundaryDetector` |
| Per-song telemetry (JSONL per song, session summary) | `telemetry.py` | `TelemetryWriter` |
| Hot-reload device config (mtime polling, diff, atomic swap) | `config_watcher.py` | `ConfigWatcher` |
| Infinite session runner (YAML config + Ctrl+C shutdown) | `session.py` | `run_session()` |
| Spotify OAuth + API client | `spotify/auth.py`, `spotify/client.py` | `SpotifyAuth`, `SpotifyClient` |
| Spotify queue watcher (track change detection) | `spotify/queue_watcher.py` | `SpotifyQueueWatcher` — polls playback, fires `on_track_changed(new, old)` |
| Spotify data models | `spotify/models.py` | `PlaybackState`, `Track` (has `.name`, `.artist`, `.album`, `.duration_ms`, `.track_id`, `.progress_ms`) |

All paths relative to `src/dreamsync/`.

### Audio capture pipeline (`src/dreamsync/capture/`)

Separate FFmpeg-based pipeline for recording system audio as per-song MP3 files. Operates at 44.1kHz stereo s16le (4 bytes/frame).

| Component | Location | Key classes/functions |
|---|---|---|
| FFmpeg device discovery (DirectShow) | `capture/ffmpeg_device.py` | `list_devices()`, `find_device()` |
| Capture process lifecycle (spawn/monitor/kill) | `capture/capture_process.py` | `CaptureProcessManager` — wraps FFmpeg subprocess |
| Frame-aligned PCM chunk reader | `capture/pcm_reader.py` | `PcmReader` — reads from stdout pipe, `BYTES_PER_FRAME=4` |
| Thread-safe audio buffer with frame counter | `capture/pcm_buffer.py` | `PcmBuffer` — `.put()` updates frame counter atomically, `.frames_processed` for drift |
| Segment boundary computation from timing data | `capture/segment_boundary.py` | `compute_boundaries(song_durations, current_playback_time, sample_rate)` -> frame offsets |
| Per-segment FFmpeg MP3 encoder | `capture/encoder_process.py` | `EncoderProcess` — one per segment, `.write()` / `.finish()` / `.wait()` |
| Zero-loss PCM split at frame boundaries | `capture/split_logic.py` | `split_at_frame()` |
| Deterministic file naming + sanitization | `capture/file_namer.py` | `FileNamer` — timestamp or metadata naming, collision avoidance |
| Mutable boundary queue with safety margins | `capture/boundary_queue.py` | `BoundaryQueue` — thread-safe sorted queue, `BoundaryEntry` has `.frame_position`, `.metadata` |
| External timing integration + refresh | `capture/timing_integrator.py` | `TimingIntegrator` — `.update(timing_data)`, `.on_track_change(timing_data)` |
| JSON metadata sidecar files (atomic writes) | `capture/metadata_writer.py` | `MetadataWriter`, `SegmentMetadata` dataclass (song_title, artist, album, etc.) |
| Drift detection + boundary correction | `capture/drift_detector.py` | `DriftDetector` — `.measure(actual_frames, elapsed_wall_seconds)`, 4 levels: ok/warning/correction/critical |
| Failure recovery cascade (retry/fallback/skip) | `capture/recovery_manager.py` | `RecoveryManager` — capture/encoder failure retry with backoff |
| Structured JSON + console pipeline logging | `capture/pipeline_logger.py` | `PipelineLogger` — JSONL to `logs/pipeline.jsonl` + console |
| Pipeline orchestrator (unified lifecycle + CLI) | `capture/orchestrator.py` | `CaptureOrchestrator`, `DynamicSplitProcessor`, `OrchestratorConfig` |
| Windows audio routing via PowerShell | `capture/audio_router.py` | `AudioRouter` |

### Capture pipeline data flow

```
CaptureProcessManager (FFmpeg stdin pipe)
    -> PcmReader (frame-aligned chunks)
    -> PcmBuffer (thread-safe queue, atomic frame counter)
    -> DynamicSplitProcessor (checks BoundaryQueue on every chunk)
        -> EncoderProcess (one per segment, PCM -> MP3)
        -> on segment rotate: _on_segment_complete() -> MetadataWriter (JSON sidecar)

TimingIntegrator (periodic Spotify poll or on_track_change)
    -> compute_boundaries() -> BoundaryEntry objects
    -> BoundaryQueue.replace_future() (thread-safe merge)

DriftDetector.measure(buffer.frames_processed, wall_elapsed)
    -> drift_seconds = (actual_frames - expected_frames) / sample_rate
    -> Checked every ~10s in consumer loop
```

### CLI -> capture orchestrator wiring (`cli.py`)

The `main()` function in `cli.py` handles all subcommands. Capture pipeline integration:

1. **`govee-live --capture --spotify`**: `main()` creates `CaptureOrchestrator` + `SpotifyQueueWatcher`. The watcher's `on_track_changed` callback is monkey-patched to call both `capture_orchestrator.on_track_change(timing_data)` (for segment splits) and the original display callback (prints "Spotify: now playing...").

2. **Track change data format** passed to `on_track_change()`:
   ```python
   {"song_durations": [new.duration_ms / 1000.0],
    "current_playback_time": 0.0,
    "current_song": {"song_title": new.name, "artist": new.artist, "album": new.album}}
   ```

3. **Segment metadata flow**: `BoundaryEntry.metadata` -> `_get_segment_metadata(segment_index)` -> `_build_segment_metadata()` -> `SegmentMetadata` dataclass -> `MetadataWriter.write_sidecar()` -> JSON file.

4. **Console display**: `_safe_track_msg(new)` encodes song name with `errors="replace"` for Windows cp1252 safety. Same pattern in `session.py` for the session subcommand.

### Transport protocols

| Protocol | Packet type | Use case |
|----------|-------------|----------|
| `razer` | DreamView per-LED binary | Strips with many segments (e.g. H808A, 25 LEDs) |
| `ptreal` | BLE-over-LAN per-segment | Strips with IC segments (e.g. H612F, 7 segments) |
| `colorwc` | Whole-strip single color | Fallback for unsupported devices |

### BLE protocol reference

GATT identifiers (no authentication or pairing required):

| Identifier | UUID |
|---|---|
| Service | `00010203-0405-0607-0809-0a0b0c0d1910` |
| Write Characteristic | `00010203-0405-0607-0809-0a0b0c0d2b11` |
| Notify/Read Characteristic | `00010203-0405-0607-0809-0a0b0c0d2b10` |

All commands use a fixed 20-byte structure:

```
Byte:  [0]    [1]     [2]      [3..18]        [19]
       IDENT  CMD     SUB      PAYLOAD+PAD    XOR_CHECKSUM
```

| Command | Header | Devices | Description |
|---|---|---|---|
| Power on | `33 01 01` | All | Power on |
| Power off | `33 01 00` | All | Power off |
| Brightness | `33 04 [0x00-0xFF]` | All | 0=off, 255=max |
| Manual color | `33 05 02 RR GG BB` | H6001, H6127, H6159 | Whole-device single color |
| Bulb color | `33 05 0D RR GG BB` | H6006, H615B | Bulb-specific direct color |
| Segment color | `33 05 15 01 RR GG BB [pad] [bitmask]` | H617A, H612F, H6199 | Per-segment with 7-byte bitmask |
| Keep-alive | `AA 01` | All | Send every ~2s to prevent disconnect |

The `ptreal` LAN transport wraps these same 20-byte BLE packets in a JSON/base64 envelope for UDP. Packets are byte-identical; packet builders from `govee_lan.py` are reused by the BLE adapter.

Measured BLE latency — single device (H617A, 100 writes): median 4.1ms, P95 9.0ms, max 17.0ms. Multi-device (7 BLE + 2 LAN, 30 rounds at 5 Hz): median 4-5ms per device, P95 15-17ms.

---

## Tuning reference

### Mood thresholds (`MoodConfig` in `src/dreamsync/mood.py`)

| Parameter | Default | Range | Controls |
|---|---|---|---|
| `chill_energy_ceiling` | 0.20 | 0.10-0.35 | Max energy to enter CHILL |
| `chill_energy_exit` | 0.28 | ceiling+0.05-0.10 | Energy to leave CHILL |
| `groove_energy_ceiling` | 0.50 | 0.35-0.65 | Energy above this → HYPE |
| `groove_energy_exit_low` | 0.15 | 0.08-0.25 | Below this from GROOVE → CHILL |
| `groove_energy_exit_high` | 0.58 | ceiling+0.05-0.10 | Above this from GROOVE → HYPE |
| `hype_energy_exit` | 0.40 | 0.30-0.50 | Below this from HYPE → GROOVE |
| `stability_threshold` | 0.07 | 0.03-0.12 | Max stability for "stable beat" |
| `stability_exit` | 0.09 | threshold+0.01-0.04 | Above this = unstable beat |
| `min_bpm_for_groove` | 70.0 | 50.0-90.0 | BPM floor for GROOVE/HYPE |
| `drop_energy_spike` | 0.25 | 0.15-0.40 | Required energy jump for DROP |
| `drop_energy_dip` | 0.15 | 0.08-0.25 | Energy must dip below this before DROP |
| `drop_window` | 0.5s | 0.3-1.5 | Spike must occur within this time after dip |
| `drop_cooldown` | 10.0s | 5.0-20.0 | Min seconds between DROPs |
| `drop_duration` | 3.0s | 1.5-5.0 | How long DROP lasts |
| `min_dwell_seconds` | 4.0s | 2.0-10.0 | Min time in any mood before switching |

### Composite energy weights (`DirectorConfig` in `src/dreamsync/director.py`)

| Weight | Default | Description |
|---|---|---|
| `w_rms` | 0.25 | Relative volume (auto-calibrated) |
| `w_spectral_flux` | 0.30 | Frame-to-frame spectral change (punchiness) |
| `w_bass_ratio` | 0.20 | Energy below 200Hz (genre sensitivity) |
| `w_onset_strength` | 0.25 | Percussive transient strength |

Weights must sum to 1.0. Self-calibration takes ~10-15 seconds.

### Effect pools

```
CHILL:   warm_glow (2.0), slow_breathe (3.0), color_breathe (1.0), wave_drift (2.0), gradient_flow (2.0)
GROOVE:  color_breathe (1.0), beat_pulse (3.0), color_scroll (2.0), wave_drift (1.0)
HYPE:    fast_scroll (2.0), beat_pulse (1.0)
DROP:    drop_blast (1.0)
```

### Common tuning issues

| Problem | Fix |
|---|---|
| Energy stuck low, always CHILL | Lower `chill_energy_ceiling` to 0.12-0.15, check mic placement |
| Energy always high, never CHILL | Raise `chill_energy_ceiling` to 0.30-0.35 |
| Thrashing GROOVE ↔ HYPE | Widen hysteresis: lower `hype_energy_exit`, raise `groove_energy_exit_high` |
| Thrashing CHILL ↔ GROOVE | Widen gap: lower `groove_energy_exit_low`, raise `chill_energy_exit` |
| DROP never fires | Lower `drop_energy_spike` to 0.18-0.20, widen `drop_window` to 1.0s |
| DROP fires on random loud moments | Raise `drop_energy_spike` to 0.35, increase `drop_cooldown` |
| Moods change too quickly | Increase `min_dwell_seconds` to 6.0-10.0 |
| Moods change too slowly | Decrease `min_dwell_seconds` to 2.0-3.0 (not below 2.0) |

---

## Troubleshooting

### BLE

| Issue | Cause | Fix |
|---|---|---|
| Device doesn't respond | Wrong GATT characteristic or needs different protocol variant | Run GATT enumeration, try segment vs bulb protocol |
| Colors are wrong | BGR byte order or HSV mode on some models | Note requested vs observed, adjust packet builder |
| Connection drops | Low RSSI or Wi-Fi interference | Move closer, check RSSI > -75, disable 2.4GHz Wi-Fi |
| bleak won't import | Missing WinRT backend | `pip install bleak[winrt]`, need Python 3.11+, Windows 10 1709+ |
| Govee app blocks connection | BLE is single-connection | Close/force-quit Govee Home app before running |
| Latency spikes after idle | Connection went stale | Keep-alive packets every 2s (already implemented) |

---

## Dependencies

### Required
- `sounddevice`, `numpy`, `scipy` — audio capture and DSP
- `pyyaml` — device config files and color profiles

### Optional
- `bleak>=0.21` — BLE support (`pip install dreamsync-music-sync[ble]`)
