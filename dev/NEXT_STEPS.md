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

## Capture Pipeline Bug Fixes (DONE)

Three bugs from live testing on 2026-03-05 — all fixed (see `dev/plans/capture-meta-log-analysis.md`):

1. ~~`queue.current` → `queue.currently_playing`~~ — fixed in `cli.py:1185`, `session.py:248`
2. ~~`charmap` UnicodeEncodeError on track change~~ — fixed with `errors="replace"` in `cli.py:1142-1143`
3. ~~Callback exception skips orchestrator~~ — fixed: orchestrator called first in `cli.py:1156-1169`, `_orig` in separate try/except

Root cause analysis: `dev/plans/split-pipeline-root-cause.md`

## Manual Validation (Live Testing)

- [x] Basic capture produces valid, playable MP3 (test 15-16)
- [ ] Spotify metadata naming + track splitting (test 17) ← **NEXT**
- [ ] Boundary accuracy across 5+ songs (test 18)
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
