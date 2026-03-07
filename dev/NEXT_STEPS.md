# DreamSync — Next Steps

## Current Priorities

### ~~1. Capture rotating buffer~~ ✅
Cap the number of MP3s kept on disk during long capture sessions to prevent filling the drive. Oldest files are deleted once the buffer limit is reached. **Done:** `RotatingFileBuffer` class, `--capture-buffer N` CLI arg, startup scan, 17 new tests (1370 total).

### ~~2. Directory-based analysis/compile/play pipeline~~ ✅
The analysis → compile → play pipeline reads MP3 files from a capture directory on disk. **Done:** `CaptureDirectoryScanner`, `CaptureTrack`, `DirectoryPipeline`, `sidecar_track_id()`, `track_id_for_capture()`, `PlaylistManager.from_tracks()`, `pipeline` CLI subcommand (`--mode analyze|compile|play`), 32 new tests (1402 total).

### 3. Validation tests (scoped down)

- [ ] **20. Analyzer unit tests** (80 tests) — `pytest dev/tests/test_analyzer_*.py -v`
- [ ] **23. Batch analysis** (5+ songs in directory)
- [ ] **25. Show Player unit tests** (65 tests)
- [ ] **26. Audio playback test** (no devices)
- [ ] **27. Synchronized playback** (with real Govee devices)
- [ ] **28. Show file round-trip** (optional)
- [ ] **29. Compiler unit tests** (58 tests)
- [ ] **30. Single file compile**
- [ ] **31. Compile to JSON output**
- [ ] **34. Compile-and-play** (full pipeline with devices)

See `dev/VALIDATION_TESTS.md` for full test details and commands.

### Out of scope (deferred)
- 24 (Genre variety — analyzer tuning, not correctness)
- 32 (Seed determinism — no reproducibility requirement)
- 33 (Profile override — not actively using multiple profiles)
- 35 (Compiler genre variety — quality polish, not a gate)
- 36-41 (Cache — premature optimization; add later if re-analysis latency is a problem)

## Previously Completed

- [x] Directory-based pipeline (`pipeline` CLI, `CaptureDirectoryScanner`, `DirectoryPipeline`, `sidecar_track_id`, 32 tests)
- [x] Capture rotating buffer (`--capture-buffer N`, `RotatingFileBuffer`, startup scan, 17 tests)
- [x] Basic capture produces valid, playable MP3 (test 15-16)
- [x] PcmAccumulator unit + integration tests (12 + 5 = 17 tests passing)
- [x] Spotify metadata naming + track splitting (test 17) — 4 bugs fixed
- [x] Live split quality — no static at boundaries (test 18a)
- [x] Residual fragment + outputFile fixes (pre-test 18 rerun) — 2 bugs fixed, 16 new tests
- [x] Boundary accuracy across 5+ songs (test 18) — passed
- [x] Edge cases: short tracks, long tracks, gapless/crossfade (test 19)
- [x] Single file analysis (test 21) — summary output + BPM check
- [x] JSON round-trip (test 22) — serialize/deserialize

## Previously Completed: Residual Fragment + outputFile Fixes

Two bugs from test 18 (2026-03-06) fixed with two-layer defense + metadata fix (all 1353 dev tests pass):

1. **Tick debounce** (Layer 1) — `on_track_change()` records `time.monotonic()`. `_tick()` skips if within 3s of last track change. Prevents stale Spotify data from creating spurious boundaries. (`timing_integrator.py`)

2. **Min segment duration guard** (Layer 2) — `_on_segment_complete()` discards segments shorter than `min_segment_frames` (5s at 44.1kHz = 220,500 frames). Temp file is deleted in a background thread. (`orchestrator.py`)

3. **outputFile sidecar fix** — `_build_segment_metadata()` accepts an `output_file` parameter. `_finalize_segment()` passes the post-rename `mp3_path`. JSON sidecar now shows the actual filename on disk. (`orchestrator.py`)

## Previously Completed: Capture Naming, Silence, and Immediate Split Fixes

Four bugs from live test 17 fixed in a single pass:

1. **Off-by-one naming** — Files were named after the PREVIOUS track because `_start_encoder()` called `_get_segment_metadata()` → `peek_next()` at segment START, which returned stale/wrong metadata. **Fix**: `_start_encoder()` now always uses a temp name (`None` → timestamp fallback). `_finalize_segment()` renames the MP3 to the correct metadata-based name after the boundary is popped (known-correct metadata).

2. **First file named generically** — "segment_000001" because no boundaries existed yet at startup. Same root cause as #1, fixed by deferred naming.

3. **5-10s silence at end of files** — `on_track_change()` only placed a boundary at the END of the new song, not at the transition point. The actual split relied on the periodic timer (5s interval + Spotify polling lag). **Fix**: `on_track_change()` now inserts an immediate boundary at `current_frame` with the OLD song's metadata, forcing a split right at the transition. Stale nearby boundaries are cleared first via new `BoundaryQueue.remove_near()`.

4. **Silence gaps in first file** — Partially addressed by immediate splits reducing wrong-audio capture. Residual startup delay from FFmpeg/VB-Cable is inherent to the audio source.

## Previously Completed: PcmAccumulator Non-Blocking Encoder Rotation

All 9 implementation steps from `dev/plans/pcm-accumulator-buffer-fix.md` are complete:

- **PcmAccumulator class** — duck-types `EncoderProcess`, buffers PCM writes while real encoder spawns in background (`src/dreamsync/capture/orchestrator.py:84-161`)
- **FileNamer thread safety** — `next_filename()` guarded by `threading.Lock` (`src/dreamsync/capture/file_namer.py:40,52-55`)
- **Async finalization** — `_on_segment_complete` dispatches blocking work (encoder wait, sidecar write, callbacks) to background thread
- **Non-blocking `_start_encoder`** — returns `PcmAccumulator` immediately, spawns FFmpeg in background thread
- **`stop()` joins finalization threads** — ensures all sidecars written before exit

## Remaining Validation Tests

See `dev/VALIDATION_TESTS.md` for the full test matrix. Active scope: tests 20, 23, 25-31, 34.
