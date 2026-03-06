# DreamSync — Next Steps

## Current: Test 18 — Boundary Accuracy (5+ Songs)

Tests 15-17 passed. Naming/silence/split bugs fixed (see below). Next is verifying boundary accuracy across 5+ songs with Spotify.

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

**What to verify:**
- MP3 count matches song count (±1 for first/last partial)
- Each MP3 contains approximately one song (listen to start/end)
- Files named after the CORRECT song (not the previous or next song)
- JSON sidecar metadata matches the filename
- No 5-10s silence at end of files (immediate split on track change)
- Boundary detection latency < 2 seconds
- `pipeline.jsonl` shows `segment.renamed` events confirming deferred naming

## Recently Completed: Capture Naming, Silence, and Immediate Split Fixes

Four bugs from live test 17 fixed in a single pass (all 1337 dev tests pass):

1. **Off-by-one naming** — Files were named after the PREVIOUS track because `_start_encoder()` called `_get_segment_metadata()` → `peek_next()` at segment START, which returned stale/wrong metadata. **Fix**: `_start_encoder()` now always uses a temp name (`None` → timestamp fallback). `_finalize_segment()` renames the MP3 to the correct metadata-based name after the boundary is popped (known-correct metadata).

2. **First file named generically** — "segment_000001" because no boundaries existed yet at startup. Same root cause as #1, fixed by deferred naming.

3. **5-10s silence at end of files** — `on_track_change()` only placed a boundary at the END of the new song, not at the transition point. The actual split relied on the periodic timer (5s interval + Spotify polling lag). **Fix**: `on_track_change()` now inserts an immediate boundary at `current_frame` with the OLD song's metadata, forcing a split right at the transition. Stale nearby boundaries are cleared first via new `BoundaryQueue.remove_near()`.

4. **Silence gaps in first file** — Partially addressed by immediate splits reducing wrong-audio capture. Residual startup delay from FFmpeg/VB-Cable is inherent to the audio source.

**Files changed:**
- `capture/boundary_queue.py` — added `remove_near(frame_position, window_frames)`
- `capture/file_namer.py` — added `rename_to_metadata(old_path, metadata)`
- `capture/timing_integrator.py` — `on_track_change()` inserts immediate boundary with old song metadata
- `capture/orchestrator.py` — `_start_encoder()` uses temp name; `_finalize_segment()` renames to metadata
- `session.py` — `_capture_track_changed` passes `previous_song` (old track info) in timing_data

## Previously Completed: PcmAccumulator Non-Blocking Encoder Rotation

All 9 implementation steps from `dev/plans/pcm-accumulator-buffer-fix.md` are complete:

- **PcmAccumulator class** — duck-types `EncoderProcess`, buffers PCM writes while real encoder spawns in background (`src/dreamsync/capture/orchestrator.py:84-161`)
- **FileNamer thread safety** — `next_filename()` guarded by `threading.Lock` (`src/dreamsync/capture/file_namer.py:40,52-55`)
- **Async finalization** — `_on_segment_complete` dispatches blocking work (encoder wait, sidecar write, callbacks) to background thread
- **Non-blocking `_start_encoder`** — returns `PcmAccumulator` immediately, spawns FFmpeg in background thread
- **`stop()` joins finalization threads** — ensures all sidecars written before exit

**Unit tests (all passing):**
```bash
# 12 PcmAccumulator tests
python -m pytest dev/tests/test_orchestrator.py -v -k "TestPcmAccumulator"

# 5 non-blocking rotation integration tests
python -m pytest dev/tests/test_orchestrator.py -v -k "TestNonBlockingRotation"

# Full orchestrator regression (104 tests)
python -m pytest dev/tests/test_orchestrator.py -v
```

**Live validation still needed:** Test 18a — play 3+ songs with Spotify splitting, verify no static/clipping at split boundaries (the original symptom this fix addresses).

## Manual Validation (Live Testing)

- [x] Basic capture produces valid, playable MP3 (test 15-16)
- [x] PcmAccumulator unit + integration tests (12 + 5 = 17 tests passing)
- [x] Spotify metadata naming + track splitting (test 17) — 4 bugs fixed
- [ ] Boundary accuracy across 5+ songs (test 18) ← **NEXT**
- [ ] Live split quality — no static at boundaries (test 18a)
- [ ] Verify drift stays < 0.5s over a 5-minute session (pipeline.jsonl drift_check events)
- [ ] Edge cases: short tracks, long tracks, gapless/crossfade (test 19)
- [ ] Recovery: kill FFmpeg mid-capture, verify auto-restart and gap recording
- [ ] Drift correction over 30+ minute session

## Remaining Validation Tests

See `dev/VALIDATION_TESTS.md` for the full remaining test matrix (items 17-41), covering:
- Song capture pipeline (component 3) — in progress
- Song structure analyzer (component 4)
- Show playback runtime (component 5)
- Show compiler (feature 3)
- Show cache (feature 4)
