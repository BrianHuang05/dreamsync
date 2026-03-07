# DreamSync — Remaining Validation Tests

Tests below cover features not yet validated end-to-end. Phases 1-4 (auto-detect, profiles, health monitor, long-run stability) have been completed and are documented in `README.md`.

> **Prerequisites:**
> - Python virtual environment activated (`.venv`)
> - `ffmpeg` installed and on PATH
> - VB-Audio Virtual Cable installed and configured (see README "Audio routing setup")
> - `devices.yaml` created in the project root (for device tests)
> - Spotify authorized via `dreamsync spotify-auth` (for metadata capture tests)

---

## Phase 5 — Song Capture Pipeline (Component 3)

These tests validate the `--capture` pipeline: continuous audio -> per-song mp3 files on disk. **Requires ffmpeg installed and on PATH.** Requires VB-Audio Virtual Cable configured (see README "Audio routing setup"). Play music through system audio so the capture has real signal.

### 5.6 Song boundary accuracy (5+ songs)

**Setup:** Queue a playlist of 5+ distinct songs on Spotify. Note the actual number of song transitions.

```bash
mkdir -p out/capture-boundary

python -m dreamsync govee-live \
  --device 10.0.0.1:7:primary:ptreal \
  --duration 1800 \
  --capture \
  --capture-dir out/capture-boundary \
  --capture-naming metadata \
  --spotify \
  --debug-mood \
  2>&1 | tee out/capture-boundary/console.log
```

After 5+ songs, Ctrl+C.

**Verify:**

```bash
# Count captured files
ls out/capture-boundary/*.mp3 | wc -l

# Check boundary events in log
grep "Capture: saved" out/capture-boundary/console.log

# Check sidecar metadata
cat out/capture-boundary/*.json | python -m json.tool | grep -E "songTitle|artist"
```

**Pass criteria:**
- Number of mp3 files matches number of songs played (+/- 1 for the flush on exit)
- Each file contains approximately one song (verify by listening to start/end of each file)
- No files shorter than 5 seconds (fragments are discarded by min_segment_frames guard)
- Boundary detection latency < 2 seconds (song transitions in captured files align with actual transitions)
- JSON sidecar `outputFile` field matches the actual filename on disk
- Pipeline log shows `segment_discarded.too_short` or `tick.debounced` events at transitions (proves fix is active)

#### 5.6.1 Test 18 Findings (2026-03-06)

**Result: PARTIAL PASS — boundary detection works, 2 bugs found.**

Ran with 4 songs (Wasia Project, Vacation Manor ×2, Pat Metheny Group) over ~22 minutes. Boundary detection correctly splits at song transitions. Two issues:

**Issue 1: Residual sub-1s fragment files (2 of 3 transitions)**

At transitions 1 and 3, a tiny residual file (~0.7s, ~17-19KB) was created between the real song files. These are caused by a race between the periodic timer `_tick()` and the `on_track_change()` callback:

1. Consumer pops periodic timer's end-of-song boundary (correct split)
2. Periodic timer fires again with **stale** Spotify data (old song still showing as current with ~0.7s remaining)
3. Creates a boundary at `current_frame + 0.7s` → consumer pops it → tiny residual segment
4. `on_track_change()` finally fires and corrects the queue, but too late

Transition 2 had no residual because `on_track_change()` ran before `_tick()` (race won by the correct code path).

Residual files observed:
- `2026-03-06_00-11-13_Wasia Project_-_Is This What Love Is_.mp3` — 19KB, 0.74s, segmentIndex=1
- `2026-03-06_00-19-44_Vacation Manor_-_If Only for Tonight - Midnight Version.mp3` — 17KB, 0.65s, segmentIndex=4

**Issue 2: JSON `outputFile` shows pre-rename temp path**

All JSON sidecars have `outputFile: "out\\capture-boundary\\..._segment_000001.mp3"` instead of the actual renamed filename. The `_build_segment_metadata()` reads `enc.output_path` (original temp path) instead of the post-rename path.

**Fixed:** Two-layer defense implemented:
1. **Tick debounce** — `_tick()` suppressed for 3s after `on_track_change()` (prevents stale data from creating boundaries)
2. **Min segment guard** — segments shorter than 5s (220,500 frames) discarded in `_on_segment_complete()` (catches any residual that slips through)
3. **outputFile fix** — `_build_segment_metadata()` accepts `output_file` param; `_finalize_segment()` passes post-rename `mp3_path`

All 1353 tests pass (16 new tests added). Ready for live rerun.

### 5.7 Edge cases

**Short track (<30s):** Play a very short track (jingle, interlude). Verify it is either captured (if >15s) or discarded with a log message.

**Long track (>10 min):** Play a long track. Verify the full track is captured in one file without truncation (buffer cap is 15 min).

**Gapless/crossfade playback:** Play a playlist with crossfade enabled. Verify boundaries are detected via Spotify track-change events even when audio crossfades.

### 5.8 Non-blocking encoder rotation (PcmAccumulator)

These tests validate that segment splits no longer block the consumer thread. The `PcmAccumulator` buffers PCM writes while a new FFmpeg encoder spawns in the background, eliminating the 200-1000ms+ stall that caused static clipping at split points. **No hardware or audio files needed** — all tests use mocks and in-memory structures.

#### 5.8.1 PcmAccumulator unit tests (12 tests)

```bash
python -m pytest dev/tests/test_orchestrator.py -v -k "TestPcmAccumulator"
```

**Pass:** All 12 tests pass:
1. `test_write_buffers_before_attach` — writes buffered until encoder attached
2. `test_attach_drains_buffer` — attach flushes all buffered data to encoder
3. `test_write_passes_through_after_attach` — post-attach writes go directly to encoder
4. `test_close_before_attach_sets_flag` — close without encoder sets pending flag
5. `test_close_after_attach_closes_encoder` — close delegates to real encoder
6. `test_attach_after_close_drains_and_closes` — drain buffer + immediate close
7. `test_wait_blocks_until_attached` — wait returns only after attach
8. `test_wait_delegates_to_encoder` — timeout forwarded to encoder.wait()
9. `test_output_path_blocks_until_attached` — property blocks on event
10. `test_output_path_returns_encoder_path` — returns real encoder path
11. `test_thread_safety_concurrent_writes` — 4-thread concurrent writes don't corrupt
12. `test_empty_buffer_attach` — attach with no buffered data is a no-op drain

#### 5.8.2 Non-blocking rotation integration tests (5 tests)

```bash
python -m pytest dev/tests/test_orchestrator.py -v -k "TestNonBlockingRotation"
```

**Pass:** All 5 tests pass:
1. `test_rotation_does_not_block_consumer` — `_start_encoder` returns in <10ms
2. `test_segment_complete_does_not_block_consumer` — `_on_segment_complete` returns in <10ms
3. `test_concurrent_rotation_and_finalization` — two rapid splits both finalize correctly
4. `test_data_continuity_across_split` — all PCM data reaches encoders across split boundary (no drops)
5. `test_stop_waits_for_pending_finalizations` — `stop()` waits for finalization threads, sidecar files exist after return

#### 5.8.3 Full orchestrator regression (all tests)

```bash
python -m pytest dev/tests/test_orchestrator.py -v
```

**Pass criteria:**
- All tests pass (87 core + 12 PcmAccumulator + 5 non-blocking rotation = 104 total)
- No regressions from async finalization changes
- Tests using `_wait_for_finalizations()` helper correctly await background thread completion

#### 5.8.4 Live split quality (requires Spotify + VB-Cable)

Play 3+ songs with Spotify splitting and verify no static at split boundaries:

```bash
mkdir -p out/capture-split-test
python -m dreamsync govee-live \
  --device 10.0.0.1:7:primary:ptreal \
  --duration 600 \
  --capture \
  --capture-dir out/capture-split-test \
  --capture-naming metadata \
  --spotify \
  --debug-mood \
  2>&1 | tee out/capture-split-test/console.log
```

After 3+ song transitions, Ctrl+C.

**Verify:**

```bash
# Check segment durations add up (no gaps)
for f in out/capture-split-test/*.mp3; do
  echo "$f"; ffprobe -v error -show_entries format=duration \
    -of default=noprint_wrappers=1:nokey=1 "$f"
done

# Listen to first/last 5 seconds of each file for static or clipping
# (Previously, encoder rotation caused 200ms+ stalls → audible artifacts)
```

**Pass criteria:**
- No static clipping or audio artifacts at the start/end of captured segments
- Segment durations are contiguous (sum ≈ total session duration, no missing audio)
- Console log shows non-blocking rotation messages (no "encoder wait" stalls on consumer thread)
- All sidecar `.json` files written correctly (finalization completed in background)

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

- [x] **14. Capture unit tests** (104 orchestrator tests: 87 core + 12 PcmAccumulator + 5 non-blocking rotation integration)
- [x] **14a. PcmAccumulator unit tests** (12 tests — non-blocking buffer/pass-through/thread-safety)
- [x] **14b. Non-blocking rotation integration** (5 tests — timing, data continuity, finalization)
- [x] **15. Basic capture** (5 min, timestamp naming, dummy device)
- [x] **16. Captured file verification** (playable mp3s with correct content)
- [x] **17. Capture with Spotify metadata** (artist-title naming + track splitting) — 4 bugs fixed: off-by-one naming, generic first filename, 5-10s silence at end, silence gaps (see `dev/NEXT_STEPS.md`)
- [x] **18. Boundary accuracy** (5+ songs with Spotify, file count matches song count) — **BUGS FIXED**, ready for rerun. Two-layer defense (tick debounce + min segment guard) + outputFile sidecar fix. 16 new tests, all 1353 pass. ← **RERUN**
- [x] **18a. Live split quality** (3+ songs, no static/clipping at split boundaries)
- [x] **19. Edge cases** (short track, long track, gapless/crossfade) **INCLUDED IN 18**
- [ ] **20. Analyzer unit tests** (80 tests)
- [x] **21. Single file analysis** (summary output + BPM check)
- [x] **22. JSON round-trip** (serialize/deserialize)
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
- [ ] **37. CLI cache commands** (list, info, clear -- no hardware)
- [ ] **38. Compile with caching** (miss then hit)
- [ ] **39. Compile-and-play with caching** (skip analysis on hit)
- [ ] **40. Profile-aware caching** (same track, different profiles)
- [ ] **41. Per-track invalidation** (selective cache clear)

```bash
# 14. Capture unit tests (68 core + 24 capture-meta fixes = 92)
python -m pytest dev/tests/test_capture_buffer.py dev/tests/test_capture_boundary.py dev/tests/test_capture_writer.py dev/tests/test_capture_pipeline.py dev/tests/test_fetch_timing.py dev/tests/test_unicode_callback.py dev/tests/test_callback_isolation.py -v

# 14a. PcmAccumulator unit tests (12 tests)
python -m pytest dev/tests/test_orchestrator.py -v -k "TestPcmAccumulator"

# 14b. Non-blocking rotation integration tests (5 tests)
python -m pytest dev/tests/test_orchestrator.py -v -k "TestNonBlockingRotation"

# 14c. Full orchestrator regression (104 tests)
python -m pytest dev/tests/test_orchestrator.py -v

# 15+16. Basic capture (5 min, play music through VB-Cable)
mkdir -p out/capture-test
python -m dreamsync govee-live --device 10.0.0.1:7:primary:ptreal --duration 300 --capture --capture-dir out/capture-test --capture-naming timestamp --debug-mood 2>&1 | tee out/capture-test/console.log
ls -la out/capture-test/*.mp3
ffprobe -v error -show_format out/capture-test/*.mp3 2>&1 | grep -E "filename|duration"

# 17. Capture with Spotify metadata (play music on Spotify)
mkdir -p out/capture-meta
python -m dreamsync govee-live --device 10.0.0.1:7:primary:ptreal --duration 600 --capture --capture-dir out/capture-meta --capture-naming metadata --spotify --debug-mood 2>&1 | tee out/capture-meta/console.log

# 18. Boundary accuracy (5+ songs on Spotify)
mkdir -p out/capture-boundary
python -m dreamsync govee-live --device 10.0.0.1:7:primary:ptreal --duration 1800 --capture --capture-dir out/capture-boundary --capture-naming metadata --spotify --debug-mood 2>&1 | tee out/capture-boundary/console.log
ls out/capture-boundary/*.mp3 | wc -l
grep "Capture: saved" out/capture-boundary/console.log

# 18a. Live split quality (3+ songs, verify no static at boundaries)
mkdir -p out/capture-split-test
python -m dreamsync govee-live --device 10.0.0.1:7:primary:ptreal --duration 600 --capture --capture-dir out/capture-split-test --capture-naming metadata --spotify --debug-mood 2>&1 | tee out/capture-split-test/console.log
for f in out/capture-split-test/*.mp3; do echo "$f"; ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$f"; done

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
python -m dreamsync play path/to/song.mp3 --show path/to/show.json --config dev/devices.yaml --debug

# 27. Synchronized playback (with real devices)
python -m dreamsync play path/to/song.mp3 --show path/to/show.json --config dev/devices.yaml --debug

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
python -m dreamsync compile-and-play path/to/song.mp3 --config dev/devices.yaml --profile aurora --debug

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
python -m dreamsync compile-and-play path/to/song.mp3 --config dev/devices.yaml --cache-dir ~/.dreamsync/cache --debug

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
| `No audio device matching 'CABLE Output' found` | Install VB-Audio Virtual Cable from https://vb-audio.com/Cable/. Verify with `ffmpeg -hide_banner -list_devices true -f dshow -i dummy 2>&1`. |
| Capture files are silence | VB-Cable is not receiving system audio. Set **CABLE Input** as default playback device in Windows Sound Settings, and enable **Listen to this device** on CABLE Output (see README). |
| Drift ~1s per 10s in pipeline.jsonl | Sample rate mismatch. VB-Cable may be at a non-default rate. Open VB-Cable Control Panel and verify it's set to 44100 Hz (matches pipeline default). |
| Capture audio has static/glitches | Sample rate mismatch between VB-Cable and capture pipeline. Ensure both use 44100 Hz. If static occurs only at song boundaries (split points), the PcmAccumulator fix should resolve this — verify `PcmAccumulator` tests pass: `pytest dev/tests/test_orchestrator.py -k TestPcmAccumulator`. |
| `UnicodeEncodeError` on Windows | Fixed in current version. Track-change and segment-saved callbacks now encode non-ASCII characters with `errors="replace"` before printing. Callback exceptions are also isolated so they cannot block the capture orchestrator. |
| Encoder exit code 255 on Ctrl+C | Fixed in current version via `CREATE_NEW_PROCESS_GROUP`. If it recurs on older code, update `capture_process.py` and `encoder_process.py`. |
| Capture produces 0 mp3 files | Without `--spotify`, there are no song boundaries, so everything goes into one segment (flushed on Ctrl+C). Use `--spotify` for track-accurate splitting. |
| Capture files have no song metadata (null artist/title) | `--spotify` flag was not passed. Add `--spotify` to enable Spotify track change detection and metadata. Fixed: `_fetch_timing()` now correctly accesses `queue.currently_playing` and gets `progress_ms` from `PlaybackState`. |
| Metadata naming shows timestamps instead of artist-title | Pass `--spotify` along with `--capture-naming metadata`. Without Spotify, metadata naming falls back to timestamp. |
| Files named after wrong (previous) song | Fixed: filenames are now determined at segment finalization using the popped boundary metadata, not at encoder start via `peek_next()`. |
| 5-10s silence at end of captured files | Fixed: `on_track_change()` now inserts an immediate boundary at the transition point instead of relying on the periodic timer. |
| `Spotify: no valid token found` | Run `python -m dreamsync spotify-auth` first to authorize. |
| Tiny residual MP3 files (~0.7s, ~17KB) at song boundaries | Fixed: two-layer defense — (1) `_tick()` debounced for 3s after `on_track_change()` prevents stale boundaries, (2) segments shorter than 5s discarded by min-segment guard in `_on_segment_complete()`. |
| JSON `outputFile` shows `segment_NNNNNN.mp3` instead of renamed path | Fixed: `_build_segment_metadata()` now accepts an `output_file` parameter; `_finalize_segment()` passes the post-rename `mp3_path`. |
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
