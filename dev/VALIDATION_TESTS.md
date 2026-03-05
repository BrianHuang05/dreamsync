# DreamSync — Remaining Validation Tests

Tests below cover features not yet validated end-to-end. Phases 1-4 (auto-detect, profiles, health monitor, long-run stability) have been completed and are documented in `README.md`.

> **Prerequisites:**
> - Python virtual environment activated (`.venv`)
> - `ffmpeg` installed and on PATH
> - `devices.yaml` created in the project root (for device tests)

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

**Setup:** Start playing music before launching the command.

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

### 5.5 Capture with metadata naming

**Setup:** Ensure the metadata source is configured (e.g., a music service integration or local file tags), then play music.

```bash
python -m dreamsync session \
  --config devices.yaml \
  --capture \
  --capture-dir out/capture-meta \
  --capture-naming metadata \
  --debug-mood
```

Let at least 2 songs play through, then Ctrl+C.

**Pass criteria:**
- Console shows `Capture: saved Artist - Title.mp3` messages
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

**Gapless/crossfade playback:** Play a playlist with crossfade enabled. Verify boundaries are detected (may use crossfade or separate signals rather than silence).

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

## Quick Reference — Remaining Test Sequence

- [ ] **14. Capture unit tests** (68 tests)
- [ ] **15. Basic capture** (5 min, timestamp naming)
- [ ] **16. Captured file verification** (playable mp3s with correct content)
- [ ] **17. Metadata capture** (artist-title naming)
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
# 14. Capture unit tests
python -m pytest dev/tests/test_capture_buffer.py dev/tests/test_capture_boundary.py dev/tests/test_capture_writer.py dev/tests/test_capture_pipeline.py -v

# 15+16. Basic capture (5 min, play music through system audio)
mkdir -p out/capture-test
python -m dreamsync session --config devices.yaml --capture --capture-dir out/capture-test --capture-naming timestamp --debug-mood 2>&1 | tee out/capture-test/console.log
ls -la out/capture-test/*.mp3

# 17. Metadata capture
python -m dreamsync session --config devices.yaml --capture --capture-dir out/capture-meta --capture-naming metadata --debug-mood

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
| `Capture: ffmpeg not found on PATH` | Install ffmpeg and ensure it's on PATH. Run `ffmpeg -version` to verify. |
| Capture produces 0 mp3 files | No song boundaries detected — ensure music is playing and songs actually transition. Also check `min_duration_seconds` (15s default) isn't filtering short fragments. |
| Capture files have no audio | Check that system audio loopback is working. The capture pipeline records what the audio callback receives — if the callback gets silence, so does the capture. |
| Metadata naming shows timestamps instead of artist-title | Metadata source must be active and configured. Without a metadata source, metadata naming falls back to timestamp. |
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
