# DreamSync — Next Steps

## Audio Capture Pipeline — Complete

All 15 capture modules and the `CaptureOrchestrator` are implemented with 339 tests (185 unit + 87 orchestrator + 13 CLI + 30 integration + 24 capture-meta fixes). 1309 total dev tests pass.

### Completed

- [x] Wire modules into `CaptureOrchestrator` (`capture/orchestrator.py`)
  - `CaptureProcessManager` -> `pcm_reader` -> `AudioBuffer` -> `DynamicSplitProcessor`
  - `TimingIntegrator` -> `BoundaryQueue` -> `DynamicSplitProcessor`
  - `DynamicSplitProcessor` -> `EncoderProcess` (per segment) -> `FileNamer` + `MetadataWriter`
  - `DriftDetector` with periodic measurement in consumer loop
  - `RecoveryManager` for capture/encoder failures with retry/fallback/skip cascade
  - `PipelineLogger` for structured JSONL + console events
- [x] CLI: `dreamsync capture --mp3` standalone capture mode
- [x] CLI: `dreamsync session --capture` with Spotify timing integration
- [x] CLI: `dreamsync govee-live --capture` for capture alongside lights
- [x] CLI: `dreamsync govee-live --spotify` with Spotify track splitting + metadata
- [x] 30 end-to-end integration tests with synthetic audio (no FFmpeg needed)

### Bug Fixes Applied During Live Testing

- [x] FFmpeg device discovery: support flat output format (newer ffmpeg versions)
- [x] Unicode `->` arrows: replaced `\u2192` with ASCII `->` for Windows cp1252
- [x] Ctrl+C encoder kill: `CREATE_NEW_PROCESS_GROUP` on Windows prevents signal propagation
- [x] Sample rate mismatch: changed default from 48kHz to 44.1kHz (matches VB-Cable default)
- [x] Spotify wiring in govee-live: added `--spotify` flag + full capture integration
- [x] C1: `queue.current` -> `queue.currently_playing` + `progress_ms` from `PlaybackState` + list/tuple fix + null guard
- [x] C2: Unicode-safe `print()` in track-change and segment-saved callbacks (encode/replace for Windows cp1252)
- [x] C3: Callback exception isolation — orchestrator called first, display callback wrapped in try/except

### Manual Validation (Live Testing)

- [x] Run live capture with VB-Cable routing (5 min, timestamp naming) -- verify audio is not silence
- [ ] Verify captured MP3 files are playable and correctly split at song boundaries
- [ ] Test Spotify metadata naming (`--spotify --capture-naming metadata`)
- [ ] Test boundary accuracy across 5+ songs with Spotify track changes
- [ ] Verify drift stays < 0.5s over a 5-minute session (pipeline.jsonl drift_check events)
- [ ] Test edge cases: short tracks, long tracks, gapless/crossfade playback
- [ ] Test recovery: kill FFmpeg mid-capture, verify auto-restart and gap recording
- [ ] Test drift correction over 30+ minute session

### Remaining Validation Tests

See `dev/VALIDATION_TESTS.md` for the full remaining test matrix (items 14-41), covering:
- Song capture pipeline (component 3) -- in progress
- Song structure analyzer (component 4)
- Show playback runtime (component 5)
- Show compiler (feature 3)
- Show cache (feature 4)
