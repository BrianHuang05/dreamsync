# DreamSync — Next Steps

## Audio Capture Pipeline — Integration Phase

All individual modules (phases 1-5) are implemented and tested (185 unit tests). Remaining work is integration and end-to-end validation.

### Pipeline Orchestrator

- [ ] Wire modules into a single `CaptureOrchestrator` class that connects:
  - `CaptureProcessManager` → `pcm_reader` → `AudioBuffer` → `SplitProcessor`
  - `TimingIntegrator` → `BoundaryQueue` → `SplitProcessor`
  - `SplitProcessor` → `EncoderProcess` (per segment) → `FileNamer` + `MetadataWriter`
  - `DriftDetector` monitoring throughout
  - `RecoveryManager` handling failures
  - `PipelineLogger` for all events
- [ ] Add CLI subcommand (`dreamsync capture`) to start/stop the pipeline
- [ ] Connect to existing `session` command via `--capture` flag

### End-to-End Testing

- [ ] Run live capture with real audio (5 min, timestamp naming)
- [ ] Verify captured MP3 files are playable and correctly split at song boundaries
- [ ] Test metadata naming with Spotify timing data
- [ ] Test boundary accuracy across 5+ songs
- [ ] Test edge cases: short tracks, long tracks, gapless/crossfade playback
- [ ] Test recovery: kill FFmpeg mid-capture, verify auto-restart and gap recording
- [ ] Test drift correction over 30+ minute session

### Remaining Validation Tests

See `dev/VALIDATION_TESTS.md` for the full remaining test matrix (items 14-41), covering:
- Song capture pipeline (component 3)
- Song structure analyzer (component 4)
- Show playback runtime (component 5)
- Show compiler (feature 3)
- Show cache (feature 4)
