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

### Device config

```yaml
# devices.yaml
devices:
  - name: "Desk Strip"
    address: "192.168.1.100"
    segments: 15
    role: primary            # "primary" or "accent" (optional, auto-assigned by device type)
    brightness_scale: 0.4    # 0.0–1.0, physical brightness calibration (optional)
  - name: "Ceiling Bulb"
    address: "192.168.1.101"
    segments: 1
    role: primary
    brightness_scale: 1.0
```

**Auto-defaults** when `role` or `brightness_scale` are omitted:

| Device type | Default role | Default brightness_scale |
|---|---|---|
| Bulb (1 segment + "bulb"/"light" in name) | primary | 1.0 |
| Single-segment strip | accent | 0.5 |
| Multi-segment strip | primary | 0.4 |

Explicit config always overrides defaults. `brightness_scale` stacks multiplicatively with role-based scaling (accent = 0.6x intensity).

### Color profiles

8 built-in color profiles control palette selection and effect pools per mood. Use `--profile` to load one, `--profile-rotation` to cycle through several, or `--auto-palette` for unlimited procedural variety:

```bash
# List available profiles
python -m dreamsync profiles --verbose

# Run with a specific profile
python -m dreamsync govee-live --device 10.0.0.1:7:primary:ptreal --duration 120 --profile aurora --debug-mood

# Rotate through profiles every 60 seconds
python -m dreamsync govee-live --device 10.0.0.1:7:primary:ptreal --duration 300 \
    --profile-rotation aurora,neon_city,midnight_rave --rotation-interval 60 --debug-mood

# Auto-palette: procedural generation + smart chaining with cross-fade
python -m dreamsync govee-live --device 10.0.0.1:7:primary:ptreal --duration 300 \
    --auto-palette --debug-mood

# Smart rotation: mood-aware chaining through named profiles
python -m dreamsync govee-live --device 10.0.0.1:7:primary:ptreal --duration 300 \
    --smart-rotation --profile-rotation aurora,neon_city,ocean_deep,warm_sunset --debug-mood

# Preview generated profiles with ROYGBIVW color tags
python -m dreamsync profiles --generate 12 --seed 42

# Preview chain sequence with tag-based scoring
python -m dreamsync profiles --generate 12 --seed 42 --chain-preview 10

# Export a discovered palette to a reusable YAML file
python -m dreamsync profile-export --seed 42 --index 3 --output my_palette.yaml

# Validate a profile's YAML structure
python -m dreamsync profile-validate aurora
```

Editing a profile YAML under `src/dreamsync/profiles/` during a live session triggers a hot-reload within ~2 seconds.

### Device health monitoring

Enable periodic probing to detect offline/online transitions and auto-pause/resume devices:

```bash
python -m dreamsync session --config devices.yaml --health-monitor --health-interval 30 --debug-mood
```

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
3. **Verify:** Run `ffmpeg -hide_banner -list_devices true -f dshow -i dummy 2>&1` and confirm `CABLE Output (VB-Audio Virtual Cable)` appears as an available audio device.

The audio path is: App -> CABLE Input -> CABLE Output -> FFmpeg capture -> DreamSync -> `--playback-device` (speakers/aux out).

> **Note:** No VB-Cable loopback ("Listen to this device") is needed. The streaming pipeline captures audio from VB-Cable, processes it (analyze + compile), and plays it back through `--playback-device`. DreamSync itself handles the routing between capture and playback — the only delay is the pipeline processing time between songs.
>
> If you're using `govee-live` without `--pipeline` (real-time beat detection only, no show playback), you'll need to hear the music through other means — either enable "Listen to this device" on CABLE Output in the Recording tab, or use a hardware splitter.

### VB-Cable reinstall (when the driver breaks)

If VB-Cable stops appearing in your audio devices or behaves erratically, do a clean reinstall:

1. **Uninstall the device from Device Manager:**
   - Open **Device Manager** > **Sound, video and game controllers**
   - Right-click **VB-Audio Virtual Cable** > **Uninstall device**
2. **Remove the driver package via PowerShell (run as Administrator):**
   ```powershell
   # Find the VB-Cable driver OEM number
   pnputil /enum-drivers | Select-String -Context 3,3 "vb"

   # Stop audio services
   net stop audiosrv
   net stop AudioEndpointBuilder

   # Delete the driver (replace oemXX.inf with the actual number from step above)
   pnputil /delete-driver oemXX.inf /uninstall /force

   # Restart audio services
   net start AudioEndpointBuilder
   net start audiosrv
   ```
3. **Reboot** the computer.
4. **Download and install** the latest VB-Cable from https://vb-audio.com/Cable/.
5. **Reboot** again as part of the installation.
6. **Re-do audio routing** — follow the steps in [Audio routing setup](#audio-routing-setup-windows) above.

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

With `--spotify`, song boundaries come from Spotify's queue API for frame-accurate splits. Without Spotify, the pipeline captures continuously without splitting.

## Show pipeline: analyze → compile → play

The show pipeline turns MP3 files into synchronized light shows. Each step produces a JSON file that feeds the next:

```
MP3 file ──analyze──▸ .analysis.json ──compile──▸ .show.json ──play──▸ audio + lights
```

### Step 1: Analyze

Extract BPM, sections, beat grid, and energy features from an audio file.

```bash
# Single file
python -m dreamsync analyze path/to/song.mp3 --summary
python -m dreamsync analyze path/to/song.mp3 --output song.analysis.json

# Batch — all audio files in a directory
python -m dreamsync analyze-dir path/to/songs/ --output-dir out/analysis/
```

### Step 2: Compile

Turn analysis into a show timeline (cue list with render modes, transitions, intensities).

```bash
# Single file
python -m dreamsync compile song.analysis.json --summary
python -m dreamsync compile song.analysis.json --output song.show.json

# Batch — all .analysis.json files in a directory
python -m dreamsync compile-dir out/analysis/ --summary
python -m dreamsync compile-dir out/analysis/ --output-dir out/shows/
```

### Step 3: Play

Play audio with synchronized lighting from a compiled show.

```bash
# Single file with pre-compiled show
python -m dreamsync play path/to/song.mp3 --show song.show.json --config devices.yaml --debug

# Single file — compile on-the-fly (no pre-compiled show needed)
python -m dreamsync play path/to/song.mp3 --config devices.yaml --debug

# Directory of MP3s — analyze, compile, and play all tracks
python -m dreamsync play path/to/songs/ --config devices.yaml --debug

# Audio only (no Govee devices needed)
python -m dreamsync play path/to/song.mp3 --dry-run --debug
```

### Shortcut: compile-and-play

Analyze, compile, and play a single file in one command:

```bash
python -m dreamsync compile-and-play path/to/song.mp3 --config devices.yaml --profile aurora --debug
```

### Shortcut: directory pipeline

Run a capture directory through any stage of the pipeline:

```bash
python -m dreamsync pipeline out/captured-songs/ --mode analyze --debug
python -m dreamsync pipeline out/captured-songs/ --mode compile --debug
python -m dreamsync pipeline out/captured-songs/ --config devices.yaml --debug   # full play
```

### Streaming pipeline (capture + play concurrently)

Stream captured songs through analyze, compile, and play as they arrive. While song N plays with its compiled show, song N+1 is being compiled, and song N+2 is being analyzed.

```bash
# Full streaming pipeline (Spotify + devices + interactive device picker)
python -m dreamsync session --config devices.yaml \
  --pipeline --capture --capture-dir out/streaming \
  --capture-naming metadata --spotify \
  --playback-device pick --purge --debug-mood

# Or with a known device ID
python -m dreamsync session --config devices.yaml \
  --pipeline --capture --capture-dir out/streaming \
  --capture-naming metadata --spotify \
  --playback-device 5 --purge --debug-mood

# Dry-run streaming (no devices, just capture + analyze + compile)
python -m dreamsync session --config devices.yaml \
  --pipeline --capture --capture-dir out/streaming \
  --capture-naming metadata --spotify --debug-mood
```

## Archive MP3s

After a session, archive captured MP3 files into a zip to reclaim disk space. JSON sidecar files (`.analysis.json`, `.show.json`, `.meta.json`) are left untouched for re-runs and inspection.

```bash
# Dry run — see what would be archived
python -m dreamsync archive out/capture-boundary/ --dry-run

# Archive and delete originals
python -m dreamsync archive out/capture-boundary/

# Archive with custom name
python -m dreamsync archive out/capture-boundary/ --name "session-2026-03-13"

# Keep originals (just create the zip)
python -m dreamsync archive out/capture-boundary/ --keep
```

To auto-archive at the end of a capture session:

```bash
python -m dreamsync session --config devices.yaml --capture --capture-dir out/songs --archive
```

## List audio devices

```bash
python -m dreamsync devices          # formatted table
python -m dreamsync devices --json   # raw JSON (machine-readable, backward compat)
```

Lists both **input** devices (for `--audio-device`, used by the beat detector) and **output** devices (for `--playback-device`, used by the show player). Capture/virtual-cable devices are marked with a warning.

### Interactive device picker

Instead of looking up device IDs manually, use `pick` to get an interactive menu:

```bash
python -m dreamsync play song.mp3 --audio-device pick --config devices.yaml
python -m dreamsync session --config devices.yaml --pipeline --playback-device pick
```

The picker lists all output devices, warns if you select a capture device (to prevent feedback loops), and returns the selected device ID. Integer IDs (`--playback-device 4`) still work unchanged.

### Audio routing for streaming pipeline

The streaming pipeline (`session --pipeline`) captures and plays audio simultaneously. To avoid feedback (the show player's audio being re-captured), the capture and playback devices **must be different**:

```
Spotify  ──▸  CABLE Input (system default)  ──▸  CABLE Output  ──▸  FFmpeg capture
                                                                     (--device-pattern "CABLE Output")
                                                                          │
                                              DreamSync (analyze + compile + play)
                                                                          │
                                                                          ▼
                                              Speakers / AUX out  ──▸  physical audio
                                              (--playback-device N)
```

Use `dreamsync devices` to find the right IDs, then:

```bash
python -m dreamsync session --config devices.yaml \
  --pipeline --capture --capture-dir out/streaming \
  --spotify --playback-device 4 --debug-mood
```

Common device IDs (run `dreamsync devices` to confirm yours):

| Device | Use for | Feedback-safe? |
|---|---|---|
| `Speakers (Realtek)` | `--playback-device` | Yes |
| `Headphones (Realtek)` | `--playback-device` | Yes |
| `CABLE Input (VB-Audio)` | **Do not use** for playback | No — feeds back into capture |
| `CABLE Output (VB-Audio)` | `--audio-device` (beat detection) | N/A (input only) |

## CLI flag reference

All flags in one table, grouped by category. Not every flag applies to every subcommand — see `--help` on each for specifics.

| Flag | Default | Description |
|------|---------|-------------|
| **Device targeting** | | |
| `--device-ip` | | Single device IP (use with `--segments`) |
| `--device` | | Repeatable device spec: `IP:SEGMENTS[:ROLE[:TRANSPORT]]` |
| `--segments` | 15 | Segment count (with `--device-ip`) |
| `--config` | | Path to YAML device config file |
| **Audio** | | |
| `--audio-device` | system default | Input/output device ID, or `pick` for interactive selection |
| `--playback-device` | None | Output device ID for show playback, or `pick` (must differ from capture device) |
| `--sample-rate` | 44100 | Audio sample rate |
| `--duration` | | Capture duration in seconds (govee-live, capture) |
| **Rendering** | | |
| `--render-mode` | scroll | `solid`, `pulse`, `scroll`, or `breathe` |
| `--transport` | ptreal | `razer` (DreamView per-LED), `ptreal` (BLE-over-LAN per-segment), `colorwc` (whole-strip) |
| `--fps` | 30 | Frame rate |
| `--brightness` | 1.0 | Global brightness (0-1) |
| `--colors` | auto | Comma-separated hex colors to cycle on beats |
| `--mirror` / `--no-mirror` | mirror | Scroll from center outward vs left-to-right |
| `--half-time` | off | Halve detected BPM (fixes octave-doubled detection) |
| `--max-brightness` | off | Force all frames to full intensity |
| **Mood & effects** | | |
| `--auto-cycle` / `--no-auto-cycle` | on | Mood-driven effect cycling |
| `--cycle-interval` | 16 | Seconds between effect changes within same mood |
| `--debug-mood` | off | Print mood, effect, BPM, and song boundary events to stdout |
| **Color profiles** | | |
| `--profile` | none | Load a named color profile |
| `--profile-rotation` | none | Comma-separated profile names to rotate through |
| `--rotation-interval` | 60 | Seconds between profile rotations |
| `--auto-palette` | off | Generate procedural profiles + smart chaining with cross-fade |
| `--auto-palette-seed` | none | Seed for reproducible profile generation |
| `--auto-palette-count` | 12 | Number of profiles to generate for the pool |
| `--smart-rotation` | off | Use mood-aware smart chaining with `--profile-rotation` profiles |
| `--chain-blend` | 8.0 | Cross-fade duration in seconds between profiles |
| `--chain-interval` | 60-180 | Min[-max] seconds per profile before switching |
| **Device health** | | |
| `--health-monitor` | off | Enable periodic device health probing |
| `--health-interval` | 30 | Seconds between health probes |
| `--health-discovery` | off | Scan for new devices on the network during health monitoring |
| **MP3 capture** | | |
| `--mp3` | off | Enable MP3 capture pipeline (capture subcommand) |
| `--capture` | off | Enable MP3 capture pipeline (session/govee-live) |
| `--output-dir` / `--capture-dir` | `captured_songs` | Output directory for MP3 files |
| `--naming` / `--capture-naming` | `timestamp` | Filename scheme: `timestamp` or `metadata` |
| `--device-pattern` | `CABLE Output` | DirectShow audio device for FFmpeg |
| `--spotify` | off | Use Spotify queue API for song boundary detection |
| `--capture-buffer` | off | Max MP3 files to keep on disk (rotating buffer) |
| `--archive` | off | Archive MP3 files in capture directory on clean shutdown (session) |
| **Streaming pipeline** | | |
| `--pipeline` | off | Enable concurrent capture + analyze + compile + play |
| `--purge` | off | Delete MP3 + sidecar files after playback |
| `--dry-run` | off | Skip device detection, audio-only playback |
| `--debug` | off | Verbose debug output |

## Run tests

```bash
python -m pytest tests/ -v        # Core subsystems (569 tests)
python -m pytest dev/tests/ -v    # Dev tests (1680+ tests)
```

Tests covering all subsystems:

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

### Dev tests (`dev/tests/`)

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
| `test_cli_capture.py` | 17 | CLI integration: --mp3 args, govee-live --capture, session orchestrator config, Spotify wiring, previous_song boundary |
| `test_capture_integration.py` | 30 | E2E: split accuracy, metadata sidecars, dynamic boundaries, drift, failure injection, logging, file naming |
| `test_timing_debounce.py` | 5 | Tick debounce after track change: suppression, expiry, logging |
| `test_min_segment_guard.py` | 6 | Min segment duration guard: discard short segments, delete temp files |
| `test_outputfile_metadata.py` | 5 | outputFile sidecar shows renamed path, fallback to encoder path |
| `test_capture_scanner.py` | 10 | Capture directory scanner, sidecar parsing, sort by index |
| `test_cache_sidecar.py` | 5 | Sidecar-based cache keys, deterministic, case-insensitive |
| `test_dir_pipeline.py` | 17 | Directory pipeline, cache hit/miss, progress, integration |
| `test_analyzer_*.py` (6 files) | 101 | Audio decode, feature extraction, BPM, sections, models, phrases |
| `test_show_*.py` (4 files) | 81 | Show timeline models, player, runtime (intensity ramp, color cycling), CLI |
| `test_compiler_*.py` (5 files) | 79 | Arc planner, treatments (song palettes), transitions, assembly (micro-cues, fade-to-black), compile |
| `test_cache*.py` (3 files) | 36 | Cache fingerprint, store, compile integration |
| `test_null_adapter.py` | 6 | NullMultiAdapter interface compliance, runtime + session compatibility |
| `test_show_pipeline_worker.py` | 12 | Background analyze + compile worker, queue, error handling, concurrency |
| `test_show_playback_consumer.py` | 10 | Playback consumer thread, purge, adapter lifecycle, ordering |
| `test_color_utils.py` | 24 | HSL conversions, interpolation, harmony generators, palette distance |
| `test_profile_generator.py` | 14 | Procedural profile generation, validation, determinism, harmony/temp/saturation |
| `test_profile_chain.py` | 30 | Distance matrix, neighbor selection, cross-fade blending, chain controller state machine |
| `test_cli_auto_palette.py` | 12 | CLI flag parsing, mutual exclusivity, chain wiring |
| `test_color_tags.py` | 31 | ROYGBIVW hue classification, palette color classification, profile tag generation |
| `test_tag_chaining.py` | 25 | Tag parsing, tag scoring, tag-aware profile selection, mood preferences, backward compat |
| `test_profile_export.py` | 9 | YAML export, round-trip, name override, tag preservation, CLI export |
| `test_device_picker.py` | 15 | `is_capture_device`, `format_device_table`, `pick_output_device` interactive picker |
| `test_archiver.py` | 8 | MP3 archiving: zip creation, delete/keep originals, JSON untouched, min age, custom name |

### Validation tests

End-to-end validation beyond unit tests. Prerequisites vary by section.

> **Common prerequisites:** `.venv` activated, `ffmpeg` on PATH.
> **Capture tests:** VB-Cable installed, Spotify authorized (`dreamsync spotify-auth`).
> **Device tests:** `devices.yaml` in project root, Govee devices on LAN.

#### Live capture (Spotify + VB-Cable)

**Boundary accuracy (5+ songs):**

```bash
mkdir -p out/capture-boundary
python -m dreamsync govee-live \
  --device 10.0.0.1:7:primary:ptreal --duration 1800 \
  --capture --capture-dir out/capture-boundary \
  --capture-naming metadata --spotify --debug-mood \
  2>&1 | tee out/capture-boundary/console.log
```

Pass: MP3 count ≈ song count (±1), no files <5s, boundary latency <2s, sidecar `outputFile` matches filename.

**Split quality (no static at boundaries):**

```bash
mkdir -p out/capture-split-test
python -m dreamsync govee-live \
  --device 10.0.0.1:7:primary:ptreal --duration 600 \
  --capture --capture-dir out/capture-split-test \
  --capture-naming metadata --spotify --debug-mood
```

Pass: No static/clipping at split points, segment durations contiguous (sum ≈ session duration).

**Edge cases:** Short track (<30s) → captured if >15s, else discarded with log. Long track (>10min) → captured whole. Gapless/crossfade → splits via Spotify events.

**Rotating buffer:**

```bash
python -m dreamsync govee-live \
  --device 10.0.0.1:7:primary:ptreal --duration 600 \
  --capture --capture-dir out/buffer-test \
  --capture-naming metadata --spotify --capture-buffer 5
```

Pass: ≤5 MP3 files on disk after 7+ songs, eviction events in logs.

#### Analyzer (ffmpeg required)

```bash
# Single file
python -m dreamsync analyze path/to/song.mp3 --summary

# JSON round-trip
python -m dreamsync analyze path/to/song.mp3 --output out/analysis/test.json

# Batch (5+ songs)
python -m dreamsync analyze-dir path/to/songs/ --output-dir out/analysis/
```

Pass: BPM ±5 of known tempo, ≥2 sections, reasonable labels, JSON round-trips, <30s per song.

#### Show player (ffmpeg required, devices optional)

```bash
# Audio only (no devices, --dry-run skips device detection)
python -m dreamsync play path/to/song.mp3 --dry-run --debug

# With Govee devices
python -m dreamsync play path/to/song.mp3 --show path/to/show.json --config devices.yaml --debug
```

Pass: Audio plays without glitches, cue transitions in debug output, `ShowTimeline.from_json()` / `.to_json()` round-trips.

#### Compiler (no hardware needed)

```bash
# Single file
python -m dreamsync compile path/to/song.analysis.json --summary
python -m dreamsync compile path/to/song.analysis.json --output song.show.json

# Batch — all .analysis.json files in a directory
python -m dreamsync compile-dir out/analysis/ --summary
python -m dreamsync compile-dir out/analysis/ --output-dir out/shows/

# Full pipeline (requires mp3 + devices)
python -m dreamsync compile-and-play path/to/song.mp3 --config devices.yaml --profile aurora --debug
```

Pass: Summary table with Duration/BPM/Sections/Cues, JSON round-trips, batch compiles all files, full pipeline runs analyze→compile→play.

#### Cache (no hardware needed)

```bash
python -m dreamsync cache-list
python -m dreamsync cache-info

# First compile = miss, second = hit
python -m dreamsync compile path/to/song.analysis.json --cache-dir ~/.dreamsync/cache --summary
python -m dreamsync compile path/to/song.analysis.json --cache-dir ~/.dreamsync/cache --summary

python -m dreamsync cache-clear --yes
```

Pass: First = "Cache miss", second = "Cache hit", `cache-list` shows entries, `cache-clear` removes them.

#### Directory pipeline

```bash
python -m dreamsync pipeline --help
python -m dreamsync pipeline out/capture-test/ --mode analyze --debug
python -m dreamsync pipeline out/capture-test/ --mode compile --debug
python -m dreamsync pipeline out/capture-test/ --config devices.yaml --debug
```

Pass: Analyze writes `.analysis.json` files, compile caches shows, play runs full playback.

#### Archive (no hardware needed)

```bash
# Unit tests
python -m pytest dev/tests/test_archiver.py -v

# Dry run — show what would be archived
python -m dreamsync archive out/capture-boundary/ --dry-run

# Archive MP3s, delete originals
python -m dreamsync archive out/capture-boundary/

# Confirm JSON files remain, MP3s are in zip
ls out/capture-boundary/*.json
unzip -l out/capture-boundary/archived_*.zip

# Archive with custom name, keep originals
python -m dreamsync archive out/capture-boundary/ --name "test-session" --keep
```

Pass: Zip contains all MP3s, JSON sidecars untouched, `--dry-run` lists files without creating zip, `--keep` preserves originals, `--name` sets custom archive name.

#### Capture troubleshooting

| Symptom | Fix |
|---|---|
| `ffmpeg not found on PATH` | Install ffmpeg, verify with `ffmpeg -version` |
| `No audio device matching 'CABLE Output'` | Install VB-Audio Virtual Cable, verify with `ffmpeg -list_devices true -f dshow -i dummy 2>&1` |
| Capture files are silence | Set CABLE Input as default playback device, enable "Listen to this device" on CABLE Output |
| Drift ~1s per 10s | Sample rate mismatch — set VB-Cable to 44100 Hz |
| Static/glitches at boundaries | Sample rate mismatch, or pre-PcmAccumulator code — verify tests pass |
| 0 mp3 files produced | Without `--spotify`, no boundaries → one segment flushed on Ctrl+C |
| No song metadata (null artist/title) | Add `--spotify` flag |
| Files named after wrong song | Fixed in current version (deferred naming at finalization) |
| 5-10s silence at end of files | Fixed in current version (immediate boundary at transition) |
| Fewer MP3s than songs (intermittent missed splits) | Fixed in current version (govee-live now passes `previous_song` for immediate boundaries) |
| Tiny residual files (~0.7s) at boundaries | Fixed in current version (tick debounce + min segment guard) |
| JSON `outputFile` wrong | Fixed in current version (post-rename path in sidecar) |
| `Spotify: no valid token found` | Run `python -m dreamsync spotify-auth` first |
| Analyzer BPM wrong by 2x | Harmonic aliasing — should auto-resolve; file an issue if persistent |
| Analysis takes >30s | Expected for long songs or slow hardware |

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
| Procedural profile generation + export | `profile_generator.py` | `generate_profile()`, `generate_profile_set()`, `GeneratorParams`, `compute_profile_tags()`, `export_profile_yaml()` |
| Smart profile chaining + cross-fade | `profile_chain.py` | `ProfileChain`, `ChainConfig`, `blend_profiles()`, `pick_next_profile()`, `_tag_score()` |
| HSL color utilities + ROYGBIVW classification | `color_utils.py` | `hex_to_hsl()`, `interpolate_hex_hsl()`, `generate_palette()`, `hue_to_color_name()`, `classify_palette_colors()`, harmony generators |
| Segment rendering (SOLID, PULSE, SCROLL, BREATHE, STROBE, WAVE, GRADIENT) | `render.py` | `SegmentRenderer` |
| LAN output (ptreal, razer, colorwc over UDP) | `output/govee_lan.py` | `GoveeLanAdapter` |
| BLE output (bleak GATT, mood-follower mode) | `output/govee_ble.py` | `GoveeBleAdapter` |
| Device roles, types, brightness scaling | `output/roles.py` | `DeviceRole`, `DeviceType`, `transform_intent()`, `adapt_render_mode()`, `default_device_config()` |
| Auto-detect device roles (latency-based classification) | `output/auto_detect.py` | `auto_detect_roles()` |
| Phrase segmentation + instrument events | `analyzer/phrases.py` | `PhraseSegmenter`, `InstrumentEventDetector`, `Phrase`, `InstrumentEvent` |
| Device health monitor (probe loop, offline/online, role reclass) | `device_health.py` | `DeviceHealthMonitor` |
| Song boundary detection (silence-gap + crossfade voting) | `live.py` | `SongBoundaryDetector`, `CrossfadeBoundaryDetector` |
| Per-song telemetry (JSONL per song, session summary) | `telemetry.py` | `TelemetryWriter` |
| Hot-reload device config (mtime polling, diff, atomic swap) | `config_watcher.py` | `ConfigWatcher` |
| Infinite session runner (YAML config + Ctrl+C shutdown) | `session.py` | `run_session()` |
| Null adapter (dry-run / audio-only testing) | `output/null_adapter.py` | `NullMultiAdapter` |
| Streaming pipeline worker (background analyze + compile) | `show_pipeline_worker.py` | `ShowPipelineWorker` |
| Streaming playback consumer (play from ready queue) | `show_playback_consumer.py` | `ShowPlaybackConsumer` |
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
| MP3 archiver (zip + cleanup) | `capture/archiver.py` | `archive_mp3s()` — zip MP3s, leave JSON sidecars |

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
    "current_song": {"song_title": new.name, "artist": new.artist, "album": new.album},
    "previous_song": {"song_title": old.name, "artist": old.artist, "album": old.album}}  # when old is not None
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
