# DreamSync — Next Steps

## Capture Pipeline Bug Fixes

Three bugs identified from live testing on 2026-03-05 (see `dev/plans/capture-meta-fixes/`):

1. **First segment gets null metadata on track-change split** — metadata attached to boundary (new track) instead of the segment being finalized (old track)
2. **Startup drift offset (~0.4s)** — `_start_time` set before FFmpeg delivers first frame, creating permanent negative offset
3. **Unicode console display** — Japanese/CJK chars show as `??????` on cp1252 terminals (JSON sidecar is correct)

## Manual Validation (Live Testing)

- [ ] Verify captured MP3 files are playable and correctly split at song boundaries
- [ ] Test Spotify metadata naming (`--spotify --capture-naming metadata`)
- [ ] Test boundary accuracy across 5+ songs with Spotify track changes
- [ ] Verify drift stays < 0.5s over a 5-minute session (pipeline.jsonl drift_check events)
- [ ] Test edge cases: short tracks, long tracks, gapless/crossfade playback
- [ ] Test recovery: kill FFmpeg mid-capture, verify auto-restart and gap recording
- [ ] Test drift correction over 30+ minute session

## Remaining Validation Tests

See `dev/VALIDATION_TESTS.md` for the full remaining test matrix (items 14-41), covering:
- Song capture pipeline (component 3) — in progress
- Song structure analyzer (component 4)
- Show playback runtime (component 5)
- Show compiler (feature 3)
- Show cache (feature 4)
