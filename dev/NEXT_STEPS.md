# DreamSync — Next Steps

## Validation Tests

- [x] **20. Analyzer unit tests** (80 tests) — `pytest dev/tests/test_analyzer_*.py -v`
- [x] **23. Batch analysis** (5+ songs in directory)
- [x] **25. Show Player unit tests** (65 tests)
- [x] **26. Audio playback test** (no devices) — `play --dry-run` with NullMultiAdapter
- [ ] **27. Synchronized playback** (with real Govee devices)
- [x] **28. Show file round-trip** (optional)
- [x] **29. Compiler unit tests** (58 tests)
- [x] **30. Single file compile**
- [x] **31. Compile to JSON output**
- [ ] **34. Compile-and-play** (full pipeline with devices)

See the **Validation tests** section in `README.md` for full test details and commands.

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
