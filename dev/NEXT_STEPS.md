# DreamSync — Next Steps

## Current: Test 17 — Capture with Spotify Metadata + Track Splitting

Basic capture (test 15-16) passed: single MP3 is valid and playable. Next is verifying Spotify-driven track splitting.

```bash
mkdir -p out/capture-meta
python -m dreamsync govee-live \
  --device 10.0.0.1:7:primary:ptreal \
  --duration 600 \
  --capture \
  --capture-dir out/capture-meta \
  --capture-naming metadata \
  --spotify \
  --debug-mood \
  2>&1 | tee out/capture-meta/console.log
```

**What to verify:**
- MP3 count matches song count (±1 for first/last partial)
- Each MP3 contains approximately one song (listen to start/end)
- Files named with artist-title (not timestamp fallback)
- `pipeline.jsonl` shows successful timing refresh events (no AttributeError)
- Console shows "Capture: saved" messages with metadata

**If splitting is still broken**, check `dev/plans/split-pipeline-root-cause.md` for the full analysis. The root cause was `_fetch_timing()` failing every tick (Bug 1), meaning `BoundaryQueue` was permanently empty and `DynamicSplitProcessor` never split. All 3 bugs from `dev/plans/capture-meta-log-analysis.md` have been fixed in source.

**Known gap:** No initial timing fetch on startup — first 5 seconds have no boundaries. If a song ends in that window, the split will be missed.

## Recently Completed: PcmAccumulator Non-Blocking Encoder Rotation

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
- [ ] Spotify metadata naming + track splitting (test 17) ← **NEXT**
- [ ] Boundary accuracy across 5+ songs (test 18)
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
