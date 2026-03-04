# DreamSync — Home Validation Tests (No Music)

All tests below run against real hardware with **no music playing**. They validate system infrastructure — role classification, color profiles, and device health — independently from the audio pipeline.

> **Prerequisites:**
> - Govee LAN device(s) powered on and connected to your network
> - Python virtual environment activated (`.venv`)
> - `devices.yaml` created in the project root (see [Device Config](#device-config) below)

---

## Device Config

Create `devices.yaml` in the project root before running session-mode tests:

```yaml
devices:
  - name: "LED strip small"
    address: "10.126.166.180"
    type: lan
    segments: 7
    transport: ptreal
    role: primary

  - name: "LED strip large"
    address: "10.126.166.156"
    type: lan
    segments: 25
    transport: razer
    role: primary
```

Adjust IPs/segments to match your actual devices.

---

## Phase 1 — Auto-Detect Role Classification

**Goal:** Verify that `detect_all_devices()` correctly probes LAN devices, measures latency, and assigns roles.

### 1.1 Unit tests (sanity check)

```bash
python -m pytest tests/test_auto_detect.py -v
```

**Pass:** All 24 tests pass.

### 1.2 LAN device scan

Confirm devices are reachable on the network:

```bash
python -m dreamsync govee-scan
```

**Pass:** Both devices appear with their IPs and SKUs.

### 1.3 Session auto-detect (short run)

Run a session with auto-detect and debug output but no music. Ctrl+C after observing the detection report (~10s):

```bash
python -m dreamsync session --config devices.yaml --debug-mood --telemetry-dir out/role-test
```

**Pass criteria:**
- Detection report prints for each device: IP, latency stats (median, p95), classified role
- LAN devices classified as `realtime` (median latency < 20ms)
- Unreachable devices (if any) show `role: unreachable` and are skipped
- No crash on startup; Ctrl+C shuts down cleanly

### 1.4 Connectivity test (solid color)

Verify each device actually receives frames:

```bash
# Small strip (7 segments, ptreal)
python -m dreamsync govee-test --device-ip 10.126.166.180 --segments 7 --transport ptreal --color "#00ff00" --duration 5 --pattern walk

# Large strip (25 segments, razer)
python -m dreamsync govee-test --device-ip 10.126.166.156 --segments 25 --transport razer --color "#0000ff" --duration 5 --pattern rainbow
```

**Pass:** Each device lights up with the specified pattern and turns off after 5 seconds.

---

## Phase 2 — Color Profile System

### 2a — Basic Profile Loading

**Goal:** Verify a profile loads, palettes resolve, and the EffectCycler uses profile-defined colors.

#### 2a.1 Unit tests

```bash
python -m pytest tests/test_profile.py -v
```

**Pass:** All 63 tests pass.

#### 2a.2 List available profiles

```bash
python -m dreamsync profiles --verbose
```

**Pass:** All 8 built-in profiles listed with name, description, and tags.

#### 2a.3 Validate a profile

```bash
python -m dreamsync profile-validate aurora
```

**Pass:** Prints profile name, moods (chill, drop, groove, hype), palette names, and "No harmony warnings."

#### 2a.4 Live profile test (with device, no music)

Run with a profile and debug output. Even without music, the system will enter CHILL mood and render effects:

```bash
python -m dreamsync govee-live --device 10.126.166.180:7:primary:ptreal --duration 60 --profile aurora --debug-mood
```

**Pass criteria:**
- Console shows `Loaded profile: Aurora Borealis`
- Debug output shows palette names from the aurora profile (e.g., `aurora_green`, `aurora_purple`) — NOT default built-in palette names
- Device displays aurora-themed colors (greens, purples, pinks)
- Effects cycle from the profile's CHILL effect pool (wave_drift, slow_breathe)
- Ctrl+C shuts down cleanly

#### 2a.5 Different profile comparison

Run with a visually distinct profile to confirm the profile is actually being used:

```bash
python -m dreamsync govee-live --device 10.126.166.180:7:primary:ptreal --duration 30 --profile neon_city --debug-mood
```

**Pass:** Colors are visually different from the aurora test (hot pinks, electric blues, lime greens). Debug output shows neon_city palette names.

### 2b — Hot-Swap

**Goal:** Verify editing a profile YAML mid-session triggers a live reload.

#### 2b.1 Unit tests (ProfileWatcher)

```bash
python -m pytest tests/test_profile.py -v -k "watcher"
```

**Pass:** ProfileWatcher tests pass.

#### 2b.2 Live hot-swap test

**Step 1:** Start a session with profile watching enabled:

```bash
python -m dreamsync session --config devices.yaml --profile aurora --debug-mood
```

**Step 2:** While the session is running, open `src/dreamsync/profiles/aurora.yaml` in a text editor and change one of the palette colors (e.g., change `#003300` to `#ff0000` in `aurora_green`).

**Step 3:** Save the file and watch the console.

**Pass criteria:**
- Within ~2 seconds of saving, console shows a profile reload message
- Device colors visibly change to reflect the edited palette
- No crash or error on reload

**Step 4:** Introduce a YAML syntax error (e.g., delete a colon). Save.

**Pass:** Console logs a warning about the invalid YAML. Old profile remains active. Device keeps running.

**Step 5:** Fix the syntax error and save again.

**Pass:** Console shows profile reloaded. Colors update to the corrected version.

### 2c — Profile Rotation

**Goal:** Verify timed rotation through multiple profiles.

#### 2c.1 Unit tests (ProfileRotation)

```bash
python -m pytest tests/test_profile.py -v -k "rotation"
```

**Pass:** ProfileRotation tests pass.

#### 2c.2 Live rotation test

Run with 3 profiles rotating every 30 seconds (shortened for testing):

```bash
python -m dreamsync govee-live --device 10.126.166.180:7:primary:ptreal --duration 120 --profile-rotation aurora,neon_city,midnight_rave --rotation-interval 30 --debug-mood
```

**Pass criteria:**
- Console shows `Profile rotation: 3 profiles, rotating every 30s`
- At ~30s: `[rotation] switched to profile: Neon City` — device colors shift to neon tones
- At ~60s: `[rotation] switched to profile: Midnight Rave` — device colors shift to purple/blue neons
- At ~90s: back to `Aurora Borealis` — device colors shift to greens/purples
- Each switch causes a visible color change on the device

---

## Phase 3 — Device Health Monitor

### 3.1 Unit tests

```bash
python -m pytest tests/test_device_health.py -v
```

**Pass:** All 29 tests pass.

### 3.2 Health monitor cold start (probe lifecycle)

Run the health monitor with a short probe interval and observe the probe logs:

```bash
python -m dreamsync session --config devices.yaml --health-monitor --health-interval 15 --debug-mood
```

**Pass criteria:**
- Health probe logs appear every ~15 seconds showing device status + latency
- All devices show `online` status
- No thread errors or leaks in the console
- Ctrl+C cleanly shuts down the health thread + all devices

### 3.3 Offline detection

**Step 1:** Start the session with health monitoring:

```bash
python -m dreamsync session --config devices.yaml --health-monitor --health-interval 15 --debug-mood
```

**Step 2:** Power off (or unplug) one of your Govee devices while the session is running.

**Pass criteria:**
- After ~45 seconds (3 failed probes at 15s interval): `WARNING: Device <IP> went offline`
- The offline device's adapter is paused (no UDP error spam in logs)
- The other device(s) continue receiving frames normally

### 3.4 Online recovery

**Step 1:** With the device still offline from test 3.3, power the device back on.

**Pass criteria:**
- After ~30 seconds (2 successful probes): `INFO: Device <IP> back online`
- Device resumes receiving frames immediately — colors appear on the restored device
- No manual intervention required

### 3.5 Health + telemetry

Run with telemetry enabled to verify health snapshots are written:

```bash
python -m dreamsync session --config devices.yaml --health-monitor --health-interval 15 --telemetry-dir out/health-test --debug-mood
```

Let it run for ~60 seconds (at least 4 probe cycles), then Ctrl+C.

**Verify telemetry:**

```bash
python -c "
import json, glob
for f in glob.glob('out/health-test/session-*/song-*.jsonl'):
    with open(f) as fh:
        for line in fh:
            row = json.loads(line)
            if row.get('kind') == 'device_health':
                print(json.dumps(row, indent=2))
"
```

**Pass:** At least 2-3 `device_health` rows with per-device status, role, latency, and failure counts.

### 3.6 Discovery scan (optional)

If you have additional Govee devices on the network not listed in `devices.yaml`:

```bash
python -m dreamsync session --config devices.yaml --health-monitor --health-discovery --debug-mood
```

**Pass:** `INFO: New device discovered: X.X.X.X (HXXXX)` if an unlisted Govee device is found.

---

## Phase 4 — Long-Run Stability (No Hardware Required)

These tests validate the audio pipeline over sustained runs. **No Govee devices need to be connected** — the commands use a device IP from your config but UDP frames silently drop if the device is unreachable. Play music through your system audio (Spotify, local files, etc.) so the loopback capture has real signal.

> **Output sizing:** Telemetry writes ~94 rows/sec × ~500 bytes/row. A 15-min run produces ~43 MB of JSONL; 30 min produces ~88 MB. Console output is <1 MB regardless of duration.

### 4.1 BPM stability (15 min)

**Goal:** Verify BPM estimation remains stable across multiple songs with varied tempos, no drift to nonsense values.

**Setup:** Queue a 15+ minute playlist with at least 3 songs of different tempos (e.g., 90 BPM chill → 128 BPM house → 170 BPM drum & bass).

```bash
mkdir -p out/longrun

python -m dreamsync govee-live \
  --device 10.126.166.180:7:primary:ptreal \
  --duration 900 \
  --debug-mood \
  --telemetry-dir out/longrun/bpm-15m \
  2>&1 | tee out/longrun/bpm-15m-console.log
```

**Analyze after run:**

```bash
# Full analysis (per-song breakdown, 30s windows, harmonic ratios)
python scripts/analyze_bpm.py out/longrun/bpm-15m/session-YYYYMMDD-HHMMSS -o out/longrun/bpm-analysis.txt

# Or analyze all sessions together
python scripts/analyze_bpm.py out/longrun/bpm-15m -o out/longrun/bpm-analysis.txt
```

**Pass criteria:**
- Outliers (<40 or >220 BPM) are < 5% of all samples
- BPM visibly tracks tempo changes across songs in the console log (grep for `bpm=`)
- No sustained lock onto a nonsense value (e.g., 30 BPM for >60 seconds)
- No crash or hang over the full 15 minutes

### 4.2 Song boundary detection (30 min)

**Goal:** Verify song boundaries fire at actual transitions, not mid-song.

**Setup:** Queue a 30+ minute playlist with 6–8 distinct songs. Note the number of transitions (songs − 1). Mix in at least one quiet-intro song and one crossfade transition if possible.

```bash
python -m dreamsync govee-live \
  --device 10.126.166.180:7:primary:ptreal \
  --duration 1800 \
  --debug-mood \
  --crossfade-detect \
  --telemetry-dir out/longrun/boundary-30m \
  2>&1 | tee out/longrun/boundary-30m-console.log
```

**Analyze after run:**

```bash
# Count boundary events
grep -c "Song boundary" out/longrun/boundary-30m-console.log

# Show timestamps of each boundary
grep "Song boundary" out/longrun/boundary-30m-console.log
```

```bash
# Count telemetry song files (each boundary starts a new file)
ls out/longrun/boundary-30m/session-*/song-*.jsonl | wc -l
```

**Pass criteria:**
- Boundary count matches actual song transitions (±1 for crossfades)
- No false boundaries mid-song (especially during quiet breakdowns or intros)
- Each `song-NNN.jsonl` file corresponds to roughly one song duration
- Crossfade boundaries (if any) logged as `[crossfade]` not `[silence]`

### 4.3 Mood & effect cycling (30 min)

**Goal:** Verify the mood state machine visits multiple states and effects cycle normally over a long session.

This uses the same run as test 4.2 — no need to run separately. Analyze the same output files.

**Analyze after run:**

```bash
python -c "
import json, glob
from collections import Counter
moods = Counter()
effects = Counter()
total = 0
for f in sorted(glob.glob('out/longrun/boundary-30m/session-*/song-*.jsonl')):
    for line in open(f):
        row = json.loads(line)
        m = row.get('mood')
        e = row.get('effect')
        if m:
            moods[m] += 1
            total += 1
        if e:
            effects[e] += 1
print(f'Total frames: {total}')
print()
print('Mood distribution:')
for mood, count in moods.most_common():
    print(f'  {mood}: {count} ({100*count/total:.1f}%)')
print()
print(f'Unique effects seen: {len(effects)}')
for effect, count in effects.most_common(10):
    print(f'  {effect}: {count}')
"
```

**Pass criteria:**
- At least 2 distinct mood states visited (ideally 3+ with varied music)
- No single mood > 90% of total frames (indicates stuck state)
- At least 3 distinct effects cycled through
- Effect names change over time (not stuck on one effect for the whole run)

### 4.4 Resource stability (30 min)

**Goal:** Verify no memory leaks or resource exhaustion over a sustained run.

Run alongside test 4.2. In a **separate terminal**, sample memory every 30 seconds:

```bash
# Start this BEFORE launching the 30-min run in test 4.2
while true; do
  echo "$(date +%H:%M:%S) $(ps aux | grep 'dreamsync govee-live' | grep -v grep | awk '{print "RSS=" $6 "KB VSZ=" $5 "KB"}')" \
    >> out/longrun/memory-30m.log
  sleep 30
done
```

After the run completes, Ctrl+C the memory monitor.

**Analyze:**

```bash
cat out/longrun/memory-30m.log
```

**Pass criteria:**
- RSS memory does not grow by more than 100 MB over the 30-minute run
- No `MemoryError` or `OSError` in the console log
- Process exits cleanly on completion (no zombie threads)

---

## Phase 5 — Song Capture Pipeline (Component 3)

These tests validate the `--capture` pipeline: continuous audio → per-song mp3 files on disk. **Requires ffmpeg installed and on PATH.** Play music through system audio so the loopback capture has real signal.

### 5.1 Unit tests

```bash
python -m pytest dev/tests/test_capture_buffer.py dev/tests/test_capture_boundary.py dev/tests/test_capture_writer.py dev/tests/test_capture_pipeline.py -v
```

**Pass:** All 68 tests pass.

### 5.2 ffmpeg availability

```bash
ffmpeg -version
```

**Pass:** ffmpeg version string printed. If missing, install ffmpeg and add to PATH before proceeding.

### 5.3 Basic capture (5 min, no hardware required)

**Setup:** Start playing music through Spotify (or any audio source) before launching the command.

```bash
mkdir -p out/capture-test

python -m dreamsync session \
  --config devices.yaml \
  --capture \
  --capture-dir out/capture-test \
  --capture-naming timestamp \
  --debug-mood \
  --duration 300 \
  2>&1 | tee out/capture-test/console.log
```

If no `devices.yaml` is available, use `govee-live` with a dummy device (UDP frames silently drop):

```bash
python -m dreamsync govee-live \
  --device 10.126.166.180:7:primary:ptreal \
  --duration 300 \
  --capture \
  --capture-dir out/capture-test \
  --capture-naming timestamp \
  --debug-mood \
  2>&1 | tee out/capture-test/console.log
```

**Pass criteria:**
- Console shows `Capture: enabled → out/capture-test/ (naming=timestamp)`
- At song boundaries: `Capture: saved YYYYMMDD_HHMMSS.mp3 (silence)` messages appear
- After Ctrl+C: `Capture: flushed final song → YYYYMMDD_HHMMSS.mp3` (if buffer had data)
- No crashes or ffmpeg errors in the log

### 5.4 Verify captured files

```bash
ls -la out/capture-test/*.mp3
```

**Pass criteria:**
- At least 1 mp3 file exists (more if multiple songs played)
- File sizes are reasonable (a 3-min song at 192k ≈ 4.3 MB)
- Files are playable — open each in any mp3 player and verify audio is correct

### 5.5 Capture with metadata naming (requires --spotify)

**Setup:** Authenticate with Spotify first (`dreamsync spotify-auth`), then play music from Spotify.

```bash
python -m dreamsync session \
  --config devices.yaml \
  --spotify \
  --capture \
  --capture-dir out/capture-meta \
  --capture-naming metadata \
  --debug-mood
```

Let at least 2 songs play through, then Ctrl+C.

**Pass criteria:**
- Console shows `Capture: saved Artist - Title.mp3 (spotify)` messages
- Files in `out/capture-meta/` have `Artist - Title.mp3` filenames
- Filenames are sanitised (no illegal path characters)
- If duplicate names occur, suffixed with `_2`, `_3`, etc.

### 5.6 Song boundary accuracy (5+ songs)

**Setup:** Queue a playlist of 5+ distinct songs. Play through from start. Note the actual number of song transitions.

```bash
python -m dreamsync session \
  --config devices.yaml \
  --capture \
  --capture-dir out/capture-boundary \
  --capture-naming timestamp \
  --debug-mood \
  2>&1 | tee out/capture-boundary/console.log
```

After the playlist finishes (or after 5+ songs), Ctrl+C.

**Verify:**

```bash
# Count captured files
ls out/capture-boundary/*.mp3 | wc -l

# Check boundary events in log
grep "Capture: saved" out/capture-boundary/console.log
```

**Pass criteria:**
- Number of mp3 files matches number of songs played (±1 for the flush on exit)
- Each file contains approximately one song (verify by listening to start/end of each file)
- No files shorter than 15 seconds (fragments are discarded by min_duration_seconds)
- Boundary detection latency < 2 seconds (song transitions in captured files align with actual transitions)

### 5.7 Edge cases

**Short track (<30s):** Play a very short track (jingle, interlude). Verify it is either captured (if >15s) or discarded with a log message.

**Long track (>10 min):** Play a long track. Verify the full track is captured in one file without truncation (buffer cap is 15 min).

**Gapless/crossfade playback:** Play a playlist with crossfade enabled. Verify boundaries are detected (may use crossfade or Spotify signals rather than silence).

---

## Phase 6 — Song Structure Analyzer (Component 4)

These tests validate the offline analyzer pipeline. **Requires ffmpeg installed and on PATH.** No Govee hardware needed.

### 6.1 Analyzer unit tests

```bash
python -m pytest dev/tests/test_analyzer_decode.py dev/tests/test_analyzer_features.py dev/tests/test_analyzer_bpm.py dev/tests/test_analyzer_sections.py dev/tests/test_analyzer_models.py -v
```

**Pass:** All 80 tests pass.

### 6.2 Single file analysis (requires an mp3 file)

Analyze a single mp3 and verify the output is reasonable:

```bash
python -m dreamsync analyze path/to/song.mp3 --summary
```

**Pass criteria:**
- BPM is within ±5 BPM of the known tempo
- At least 2 sections detected
- Section labels are reasonable (intro/verse/chorus/outro)
- Analysis completes in < 30 seconds

### 6.3 JSON output round-trip

```bash
python -m dreamsync analyze path/to/song.mp3 --output out/analysis/test.json
python -c "
from dreamsync.analyzer.models import SongStructure
s = SongStructure.from_json('out/analysis/test.json')
print(f'BPM: {s.bpm}, Sections: {len(s.sections)}, Duration: {s.duration:.1f}s')
for sec in s.sections:
    print(f'  {sec.start_t:6.1f}s – {sec.end_t:6.1f}s  {sec.label:<12s} [{sec.section_id}]  energy={sec.energy_mean:.2f}  mood={sec.mood}')
"
```

**Pass:** JSON round-trips correctly. All fields populated.

### 6.4 Batch analysis (5+ songs)

```bash
mkdir -p out/analysis
python -m dreamsync analyze-dir path/to/captured_songs/ --output-dir out/analysis/
```

**Pass criteria:**
- One `.analysis.json` file per input audio file
- BPM values are reasonable across all songs
- Analysis completes without errors

### 6.5 Genre variety (10 songs across genres)

Analyze 10+ songs spanning genres (pop, EDM, hip-hop, rock, chill, DnB). For each:

```bash
python -m dreamsync analyze song.mp3 --summary
```

**Pass criteria:**
- BPM within ±5 of ground truth on 8/10 songs
- Section labels are reasonable on 7/10 songs
- EDM songs detect drop/breakdown patterns
- Slow songs (<90 BPM) not reported at double tempo
- Fast songs (>170 BPM) not reported at half tempo

---

## Phase 7 — Show Playback Runtime (Component 5)

These tests validate the pre-sequenced show player. **Requires ffmpeg installed and on PATH.** Requires a show timeline JSON file (either hand-crafted or generated by the Show Compiler once it's built).

### 7.1 Show Player unit tests

```bash
python -m pytest dev/tests/test_show_models.py dev/tests/test_show_player.py dev/tests/test_show_runtime.py dev/tests/test_show_cli.py -v
```

**Pass:** All 65 tests pass.

### 7.2 Audio playback test (no devices)

Create a test show file manually or generate one, then play audio through speakers:

```bash
python -m dreamsync play path/to/song.mp3 --show path/to/show.json --config devices.yaml --debug
```

**Pass criteria:**
- Audio plays through system default speakers without glitches
- Console shows `[show] Playing: ...` and cue transitions with timestamps
- Ctrl+C cleanly stops playback
- Summary JSON printed on exit

### 7.3 Synchronized playback (with devices)

Run with real Govee devices connected:

```bash
python -m dreamsync play path/to/song.mp3 --show path/to/show.json --config devices.yaml --debug
```

**Pass criteria:**
- Audio and lights start simultaneously
- Cue transitions are visible on devices (color/mode changes match debug output)
- Beat-driven color cycling is perceptible and aligned with the audio beat
- Fade transitions produce smooth intensity changes over the configured beat count
- At song end: devices deactivate cleanly, summary includes frames_sent, cues_played, beats_hit

### 7.4 Show file round-trip

```bash
python -c "
from dreamsync.show.models import ShowTimeline
tl = ShowTimeline.from_json('path/to/show.json')
print(f'BPM: {tl.bpm}, Cues: {len(tl.cues)}, Beats: {len(tl.beat_times)}')
for c in tl.cues:
    print(f'  {c.t:6.1f}s  {c.render_mode:<10s}  intensity={c.intensity:.2f}  {c.transition}')
tl.to_json('/tmp/show_copy.json')
tl2 = ShowTimeline.from_json('/tmp/show_copy.json')
assert len(tl2.cues) == len(tl.cues)
print('Round-trip OK')
"
```

**Pass:** All fields round-trip correctly.

---

## Phase 8 — Show Compiler (Feature 3)

These tests validate the show compiler pipeline. **No Govee hardware or audio files needed** — all tests use in-memory structures.

### 8.1 Compiler unit tests (58 tests)

```bash
python -m pytest dev/tests/test_compiler_arc.py dev/tests/test_compiler_treatments.py dev/tests/test_compiler_transitions.py dev/tests/test_compiler_assemble.py dev/tests/test_compiler_compile.py -v
```

**Pass:** All 58 tests pass.

### 8.2 Single file compile (requires an analyzed structure JSON)

```bash
python -m dreamsync compile path/to/structure.json --summary
```

**Pass criteria:**
- Summary table printed with Duration, BPM, Sections, Cues
- Each section has a cue row with time, label, mood, effect, transition, intensity
- Runs in < 1 second

### 8.3 Compile to JSON output

```bash
python -m dreamsync compile path/to/structure.json --output show.json
```

**Pass criteria:**
- `show.json` written, contains valid ShowTimeline JSON
- Round-trips: `ShowTimeline.from_json("show.json")` succeeds

### 8.4 Seed determinism

```bash
python -m dreamsync compile path/to/structure.json --seed 42 --output show1.json
python -m dreamsync compile path/to/structure.json --seed 42 --output show2.json
diff show1.json show2.json
```

**Pass:** No differences between show1.json and show2.json.

### 8.5 Profile override

```bash
python -m dreamsync compile path/to/structure.json --profile aurora --summary
python -m dreamsync compile path/to/structure.json --profile neon_city --summary
```

**Pass:** Summaries show different effects/palettes for each profile.

### 8.6 Compile-and-play end-to-end (requires mp3 + devices)

```bash
python -m dreamsync compile-and-play path/to/song.mp3 --config devices.yaml --profile aurora --debug
```

**Pass criteria:**
- "Analyzing..." → "Compiling show..." → "Playing show..." messages in order
- Audio plays through speakers
- Devices light up with cue transitions matching debug output
- Ctrl+C cleanly stops playback

### 8.7 Genre variety (5+ songs)

Compile 5+ songs across genres and review show quality:

```bash
for f in path/to/songs/*.mp3; do
  python -m dreamsync analyze "$f" --output "/tmp/$(basename "$f" .mp3).json"
  python -m dreamsync compile "/tmp/$(basename "$f" .mp3).json" --summary --seed 42
done
```

**Pass criteria:**
- All songs compile without errors
- Intro sections have low intensity (< 0.30)
- Outro sections have low intensity (< 0.20)
- Chorus/drop sections have higher intensity than verse sections
- Transitions make musical sense (drops get cuts, outros get long fades)

---

## Phase 9 — Show Cache (Feature 4)

These tests validate the show cache system. **No hardware or audio files needed** — all tests use in-memory structures and tmp directories.

### 9.1 Cache unit tests (36 tests)

```bash
python -m pytest dev/tests/test_cache_fingerprint.py dev/tests/test_cache.py dev/tests/test_cache_compile.py -v
```

**Pass:** All 36 tests pass.

### 9.2 CLI cache commands (no hardware required)

```bash
# List cache (empty)
python -m dreamsync cache-list

# Show cache info
python -m dreamsync cache-info

# Compile with caching enabled
python -m dreamsync compile path/to/structure.json --cache-dir ~/.dreamsync/cache --summary

# Second compile — should hit cache
python -m dreamsync compile path/to/structure.json --cache-dir ~/.dreamsync/cache --summary

# List cache (should show 1 entry)
python -m dreamsync cache-list

# Clear cache
python -m dreamsync cache-clear --yes
```

**Pass criteria:**
- First compile prints "Cache miss — compiled and cached"
- Second compile prints "Cache hit — loaded from cache"
- `cache-list` shows the entry with track name, profile, compiled time, size
- `cache-info` shows accurate stats (1 entry, 1 track, disk usage > 0)
- `cache-clear --yes` prints "Deleted 1 cached show."

### 9.3 Compile-and-play with caching (requires mp3 + devices)

```bash
# First run — cache miss, full pipeline
python -m dreamsync compile-and-play path/to/song.mp3 --config devices.yaml --cache-dir ~/.dreamsync/cache --debug

# Second run — cache hit, skip analysis + compilation
python -m dreamsync compile-and-play path/to/song.mp3 --config devices.yaml --cache-dir ~/.dreamsync/cache --debug
```

**Pass criteria:**
- First run: "Analyzing..." → "Cache miss — analyzing and compiling..." → "Playing show..."
- Second run: "Cache hit — skipping analysis and compilation" → "Playing show..." (much faster startup)
- Both runs play audio and drive devices identically

### 9.4 Profile-aware caching

```bash
python -m dreamsync compile path/to/structure.json --cache-dir ~/.dreamsync/cache --profile aurora --summary
python -m dreamsync compile path/to/structure.json --cache-dir ~/.dreamsync/cache --profile neon_city --summary
python -m dreamsync cache-list
```

**Pass criteria:**
- Two distinct cache entries for the same track (different profile fingerprints)
- `cache-list` shows both entries with different profile names

### 9.5 Per-track invalidation

```bash
python -m dreamsync cache-list
python -m dreamsync cache-clear --track-id <track_id_from_list>
python -m dreamsync cache-list
```

**Pass criteria:**
- Only entries for the specified track are deleted
- Other track entries remain

---

## Quick Reference — Full Test Sequence

Run these in order for a complete validation pass:

- [x] **1. Unit tests** (all systems)
- [x] **2. Network scan**
- [x] **3. Device connectivity**
- [x] **4. Auto-detect + role classification**
- [x] **5. Profile basic** (aurora + neon_city comparison)
- [x] **6. Profile hot-swap** (edit aurora.yaml while running)
- [x] **7. Profile rotation** (3 profiles, 30s intervals) — **PASSED**
- [x] **8. Health monitor** (probe lifecycle + telemetry) — **PASSED**
- [x] **9. Offline/online** (power cycle a device during test 8) — **PASSED**: LAN and BLE both reconnect reliably after BLE health monitor fixes
- [x] **10. BPM stability** (15 min, no hardware) — **PASSED**: stdev 18.0, 0% outliers, 0/8 unstable songs, 3/30 HIGH-VARIANCE windows (all at song transitions)
- [x] **11. Song boundary detection** (30 min, no hardware) — **PASSED**: 13 boundaries for ~14 songs, all `[silence]`, 0 false positives
- [x] **12. Mood & effect cycling** (shared with test 11) — **PASSED**: 4 moods visited (CHILL 51%, DROP 20%, GROOVE 16%, HYPE 14%), 9 effects cycled
- [x] **13. Resource stability** (shared with test 11) — **PASSED**: 0 errors, 0 dropped blocks, clean exit after 30 min
- [ ] **14. Capture unit tests** (68 tests)
- [ ] **15. Basic capture** (5 min, timestamp naming)
- [ ] **16. Captured file verification** (playable mp3s with correct content)
- [ ] **17. Metadata capture** (with --spotify, artist-title naming)
- [ ] **18. Boundary accuracy** (5+ songs, file count matches song count)
- [ ] **19. Edge cases** (short track, long track, gapless/crossfade)
- [ ] **20. Analyzer unit tests** (80 tests)
- [ ] **21. Single file analysis** (summary output + BPM check)
- [ ] **22. JSON round-trip** (serialize/deserialize)
- [ ] **23. Batch analysis** (5+ songs in directory)
- [ ] **24. Genre variety** (10 songs across genres, BPM + section accuracy)
- [ ] **25. Show Player unit tests** (65 tests)
- [ ] **26. Audio playback test** (play mp3 through speakers, no devices)
- [ ] **27. Synchronized playback** (mp3 + show file + real Govee devices)
- [ ] **28. Show file round-trip** (serialize/deserialize ShowTimeline)
- [ ] **29. Compiler unit tests** (58 tests)
- [ ] **30. Single file compile** (summary output)
- [ ] **31. Compile to JSON output** (round-trip)
- [ ] **32. Seed determinism** (identical output with same seed)
- [ ] **33. Profile override** (different profiles produce different shows)
- [ ] **34. Compile-and-play** (full pipeline with devices)
- [ ] **35. Compiler genre variety** (5+ songs across genres)
- [ ] **36. Cache unit tests** (36 tests)
- [ ] **37. CLI cache commands** (list, info, clear — no hardware)
- [ ] **38. Compile with caching** (miss then hit)
- [ ] **39. Compile-and-play with caching** (skip analysis on hit)
- [ ] **40. Profile-aware caching** (same track, different profiles)
- [ ] **41. Per-track invalidation** (selective cache clear)

```bash
# 1. Unit tests (all systems) ✅
python -m pytest tests/test_auto_detect.py tests/test_profile.py tests/test_device_health.py -v

# 2. Network scan ✅
python -m dreamsync govee-scan

# 3. Device connectivity ✅
python -m dreamsync govee-test --device-ip 10.126.166.180 --segments 7 --transport ptreal --color "#00ff00" --duration 5 --pattern walk

# 4. Auto-detect + role classification ✅
python -m dreamsync session --config devices.yaml --debug-mood --telemetry-dir out/role-test

# 5. Profile basic ✅
python -m dreamsync profiles --verbose
python -m dreamsync profile-validate aurora
python -m dreamsync govee-live --device 10.126.166.180:7:primary:ptreal --duration 60 --profile aurora --debug-mood

# 6. Profile hot-swap (edit aurora.yaml while running) ✅
python -m dreamsync session --config devices.yaml --profile aurora --debug-mood

# 7. Profile rotation ✅
python -m dreamsync govee-live --device 10.126.166.180:7:primary:ptreal --duration 120 --profile-rotation aurora,neon_city,midnight_rave --rotation-interval 30 --debug-mood

# 8. Health monitor
python -m dreamsync session --config devices.yaml --health-monitor --health-interval 15 --debug-mood --telemetry-dir out/health-test

# 9. Offline/online (power cycle a device during test 8)

# 10. BPM stability (15 min, no hardware needed)
mkdir -p out/longrun
python -m dreamsync govee-live --device 10.126.166.180:7:primary:ptreal --duration 900 --debug-mood --telemetry-dir out/longrun/bpm-15m 2>&1 | tee out/longrun/bpm-15m-console.log

# 11+12+13. Boundary detection + mood cycling + resource stability (30 min, no hardware needed)
# Terminal 1 — memory monitor:
# while true; do echo "$(date +%H:%M:%S) $(ps aux | grep 'dreamsync govee-live' | grep -v grep | awk '{print "RSS=" $6 "KB"}')" >> out/longrun/memory-30m.log; sleep 30; done
# Terminal 2 — the actual run:
python -m dreamsync govee-live --device 10.126.166.180:7:primary:ptreal --duration 1800 --debug-mood --crossfade-detect --telemetry-dir out/longrun/boundary-30m 2>&1 | tee out/longrun/boundary-30m-console.log

# 14. Capture unit tests
python -m pytest dev/tests/test_capture_buffer.py dev/tests/test_capture_boundary.py dev/tests/test_capture_writer.py dev/tests/test_capture_pipeline.py -v

# 15+16. Basic capture (5 min, play music through system audio)
mkdir -p out/capture-test
python -m dreamsync session --config devices.yaml --capture --capture-dir out/capture-test --capture-naming timestamp --debug-mood 2>&1 | tee out/capture-test/console.log
ls -la out/capture-test/*.mp3

# 17. Metadata capture (requires spotify auth)
python -m dreamsync session --config devices.yaml --spotify --capture --capture-dir out/capture-meta --capture-naming metadata --debug-mood

# 18. Boundary accuracy (5+ songs)
python -m dreamsync session --config devices.yaml --capture --capture-dir out/capture-boundary --capture-naming timestamp --debug-mood 2>&1 | tee out/capture-boundary/console.log
ls out/capture-boundary/*.mp3 | wc -l
grep "Capture: saved" out/capture-boundary/console.log

# 20. Analyzer unit tests (80 tests)
python -m pytest dev/tests/test_analyzer_decode.py dev/tests/test_analyzer_features.py dev/tests/test_analyzer_bpm.py dev/tests/test_analyzer_sections.py dev/tests/test_analyzer_models.py -v

# 21. Single file analysis
python -m dreamsync analyze path/to/song.mp3 --summary

# 22. JSON round-trip
python -m dreamsync analyze path/to/song.mp3 --output out/analysis/test.json

# 23. Batch analysis
mkdir -p out/analysis
python -m dreamsync analyze-dir path/to/captured_songs/ --output-dir out/analysis/

# 24. Genre variety (repeat for 10 songs)
python -m dreamsync analyze song.mp3 --summary

# 25. Show Player unit tests (65 tests)
python -m pytest dev/tests/test_show_models.py dev/tests/test_show_player.py dev/tests/test_show_runtime.py dev/tests/test_show_cli.py -v

# 26. Audio playback test (no devices required)
python -m dreamsync play path/to/song.mp3 --show path/to/show.json --config devices.yaml --debug

# 27. Synchronized playback (with real devices)
python -m dreamsync play path/to/song.mp3 --show path/to/show.json --config devices.yaml --debug

# 28. Show file round-trip
python -c "from dreamsync.show.models import ShowTimeline; tl = ShowTimeline.from_json('path/to/show.json'); tl.to_json('/tmp/copy.json'); print('OK')"

# 29. Compiler unit tests (58 tests)
python -m pytest dev/tests/test_compiler_arc.py dev/tests/test_compiler_treatments.py dev/tests/test_compiler_transitions.py dev/tests/test_compiler_assemble.py dev/tests/test_compiler_compile.py -v

# 30. Single file compile
python -m dreamsync compile path/to/structure.json --summary

# 31. Compile to JSON output
python -m dreamsync compile path/to/structure.json --output show.json

# 32. Seed determinism
python -m dreamsync compile path/to/structure.json --seed 42 --output show1.json
python -m dreamsync compile path/to/structure.json --seed 42 --output show2.json

# 33. Profile override
python -m dreamsync compile path/to/structure.json --profile aurora --summary
python -m dreamsync compile path/to/structure.json --profile neon_city --summary

# 34. Compile-and-play (requires mp3 + devices)
python -m dreamsync compile-and-play path/to/song.mp3 --config devices.yaml --profile aurora --debug

# 35. Compiler genre variety (5+ songs)
# for f in path/to/songs/*.mp3; do python -m dreamsync analyze "$f" --output "/tmp/$(basename "$f" .mp3).json"; python -m dreamsync compile "/tmp/$(basename "$f" .mp3).json" --summary --seed 42; done

# 36. Cache unit tests (36 tests)
python -m pytest dev/tests/test_cache_fingerprint.py dev/tests/test_cache.py dev/tests/test_cache_compile.py -v

# 37. CLI cache commands (no hardware required)
python -m dreamsync cache-list
python -m dreamsync cache-info

# 38. Compile with caching (first run = miss, second = hit)
python -m dreamsync compile path/to/structure.json --cache-dir ~/.dreamsync/cache --summary
python -m dreamsync compile path/to/structure.json --cache-dir ~/.dreamsync/cache --summary

# 39. Compile-and-play with caching (requires mp3 + devices)
python -m dreamsync compile-and-play path/to/song.mp3 --config devices.yaml --cache-dir ~/.dreamsync/cache --debug

# 40. Profile-aware caching
python -m dreamsync compile path/to/structure.json --cache-dir ~/.dreamsync/cache --profile aurora --summary
python -m dreamsync compile path/to/structure.json --cache-dir ~/.dreamsync/cache --profile neon_city --summary
python -m dreamsync cache-list

# 41. Per-track invalidation
python -m dreamsync cache-clear --track-id <TRACK_ID>
python -m dreamsync cache-clear --yes
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `govee-scan` finds no devices | Check devices are powered on and on same subnet. Try power-cycling the device. |
| `govee-test` sends frames but device stays dark | Device may be in a bad state — power cycle it. Check transport matches the model (ptreal for H612F, razer for H808A). |
| Session fails with "No reachable devices" | All devices failed probing. Check IPs in `devices.yaml`. Run `govee-scan` to verify. |
| Profile not loading | Check spelling matches a built-in name. Run `python -m dreamsync profiles` to see available names. |
| Hot-swap not triggering | `ProfileWatcher` polls every 2s. Ensure you're editing the correct file (the one under `src/dreamsync/profiles/`). Ensure session was started with `--profile` (not `--profile-rotation`). |
| Health monitor offline detection too slow | Reduce `--health-interval` (e.g., `10`). Default offline threshold is 3 probes. |
| Long-run test shows 0 BPM / no boundaries | No audio playing — start music before (or shortly after) launching the command. The pipeline captures system audio via loopback. |
| Long-run memory log is empty | The `ps aux | grep` pattern may not match on Windows. Use Task Manager or `Get-Process` in PowerShell instead. |
| `Capture: ffmpeg not found on PATH` | Install ffmpeg and ensure it's on PATH. Run `ffmpeg -version` to verify. |
| Capture produces 0 mp3 files | No song boundaries detected — ensure music is playing and songs actually transition. Also check `min_duration_seconds` (15s default) isn't filtering short fragments. |
| Capture files have no audio | Check that system audio loopback is working. The capture pipeline records what the audio callback receives — if the callback gets silence, so does the capture. |
| Metadata naming shows timestamps instead of artist-title | Spotify watcher must be active (`--spotify`) and authenticated. Without Spotify, metadata naming falls back to timestamp. |
| `dreamsync analyze` fails with DecodeError | Ensure ffmpeg is installed and on PATH. Run `ffmpeg -version` to verify. Check the audio file is a valid format. |
| Analyzer BPM is wrong by exactly 2x | Harmonic aliasing — the analyzer should auto-resolve this for BPMs outside 80-160 range. If persistent, file an issue. |
| Analyzer produces only 1 section | Song may lack clear structural changes. Try a song with distinct verse/chorus dynamics. |
| Analysis takes > 30 seconds | Expected for songs > 5 minutes or on slow hardware. Feature pipeline processes ~1000 frames/second. |
| `dreamsync compile` fails with "structure file not found" | Check the path to the structure JSON. Run `dreamsync analyze` first to generate it. |
| `dreamsync compile` fails with "Error loading structure" | The JSON file may be invalid or incompatible. Re-generate with `dreamsync analyze`. |
| Compiler summary shows flat intensity | Song may have all sections with similar energy. The arc planner amplifies existing differences — monotone songs produce monotone shows. |
| `compile-and-play` fails at "Device setup failed" | Check `devices.yaml` is valid and devices are reachable. Run `dreamsync govee-scan` to verify. |
| `cache-list` shows 0 entries after compiling | Ensure `--cache-dir` was passed to the compile command. Without it, caching is disabled. |
| Second compile still says "Cache miss" | Profile changed between runs, or `--cache-dir` points to a different directory. Run `cache-list` to inspect. |
| `cache-clear` hangs | It prompts for confirmation. Pass `--yes` to skip, or type `y` + Enter. |
