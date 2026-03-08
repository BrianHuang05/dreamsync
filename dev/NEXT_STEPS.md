# DreamSync — Next Steps

## Validation Tests

- [x] **20. Analyzer unit tests** (80 tests) — `pytest dev/tests/test_analyzer_*.py -v`
- [x] **23. Batch analysis** (5+ songs in directory)
- [x] **25. Show Player unit tests** (65 tests)
- [x] **26. Audio playback test** (no devices) — `play --dry-run` with NullMultiAdapter
- [x] **27. Synchronized playback** (with real Govee devices)
- [x] **28. Show file round-trip** (optional)
- [x] **29. Compiler unit tests** (58 tests)
- [x] **30. Single file compile**
- [x] **31. Compile to JSON output**
- [ ] **34. Compile-and-play** (full pipeline with devices)

See the **Validation tests** section in `README.md` for full test details and commands.

## Show Quality Improvements (Issues 1-6) — Complete

All six show quality issues implemented and tested (69 new tests, 1508 total passing):

- [x] **Issue 1 — Beat Alignment**: Two-pass phase search, hybrid beat grid (snap to onsets), harmonic alias octave detection
- [x] **Issue 2 — Sub-Section Granularity**: Phrase segmenter (4-bar phrases), instrument event detector (kick/bass), micro-cue insertion
- [x] **Issue 3 — Palette Coherence**: Song-level primary/accent palette, downbeat-only color cycling, smooth hex interpolation
- [x] **Issue 4 — Bulb vs Strip Behavior**: Device-type render mode mapping, faster pulse decay for single-color devices
- [x] **Issue 5 — Brightness Calibration**: Per-device `brightness_scale` in config, auto-role/brightness defaults by device type
- [x] **Issue 6 — Show End Fadeout**: Fade-to-black after last energetic beat, outro intensity ramp (`intensity_start`)

Plans: `dev/plans/issue1-beat-alignment.md` through `dev/plans/issue6-show-end-fadeout.md`

## Streaming Pipeline (session --pipeline)

Capture + analyze + compile + play concurrently. Implemented in:
- `NullMultiAdapter` (`src/dreamsync/output/null_adapter.py`) — 6 tests
- `ShowPipelineWorker` (`src/dreamsync/show_pipeline_worker.py`) — 12 tests
- `ShowPlaybackConsumer` (`src/dreamsync/show_playback_consumer.py`) — 10 tests
- CLI: `play --dry-run`, `session --pipeline --playback-device N --purge`

## Out of Scope (deferred)

- 24 (Genre variety — analyzer tuning, not correctness)
- 32 (Seed determinism — no reproducibility requirement)
- 33 (Profile override — not actively using multiple profiles)
- 35 (Compiler genre variety — quality polish, not a gate)
- 36-41 (Cache — premature optimization; add later if re-analysis latency is a problem)
