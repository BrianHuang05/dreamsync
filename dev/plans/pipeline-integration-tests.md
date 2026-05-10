# Pipeline Integration Tests — Worker ↔ Consumer Handoff

## Background

The streaming pipeline has three stages wired together in `session.py`:

```
CaptureOrchestrator.on_segment_saved(path, meta)
        ↓ callback
ShowPipelineWorker._process(path) → analyze → compile → ready_queue.put()
        ↓ queue.Queue
ShowPlaybackConsumer.run() → ready_queue.get() → play → purge
```

Each component is unit-tested in isolation:
- `test_show_pipeline_worker.py` (12 tests) — mocks analyze/compile, verifies queue output
- `test_show_playback_consumer.py` (10 tests) — mocks AudioPlayer, verifies playback/purge
- `test_capture_integration.py` (~30 tests) — mocks FFmpeg, verifies split/encode

**What's missing:** tests that wire Worker + Consumer together through a shared
`queue.Queue`, verifying the handoff behaves correctly under normal and error conditions.

### Scope

All tests use mocked analysis/compilation/playback (no real audio). The goal is to
verify the threading, queue, and lifecycle interactions — not the DSP or device I/O.

---

## Test File

`dev/tests/test_pipeline_integration.py`

### Shared Fixtures

```python
# Reuse from existing test files:
# - _fake_structure(), _fake_timeline() from test_show_pipeline_worker.py
# - _mock_player() from test_show_playback_consumer.py
# - NullMultiAdapter from dreamsync.output.null_adapter

def _run_pipeline(n_segments, worker, consumer, stop_event, timeout=10):
    """Helper: start consumer thread, fire n_segments through worker, wait."""
```

---

## Tests (7 total)

### 1. `test_worker_to_consumer_handoff`
**What:** Worker processes a segment → puts (path, timeline) on ready_queue →
Consumer picks it up and "plays" it.

**Setup:**
- Shared `queue.Queue` between Worker and Consumer
- Mock `analyze_song` and `cached_compile_show` (instant return)
- Mock `AudioPlayer` (finished=True immediately)
- Consumer runs in a thread

**Assert:**
- `consumer.tracks_played == 1`
- Worker `stats()["processed"] == 1`
- Queue is empty after completion

**Why this matters:** Proves the two components agree on the queue item format
`(Path, ShowTimeline)` and the consumer correctly unpacks what the worker produces.

---

### 2. `test_multiple_segments_flow_through_pipeline`
**What:** 3 segments submitted to worker → all 3 reach consumer in order.

**Setup:**
- `max_workers=1` on worker (ensures ordering)
- Consumer thread running concurrently
- Submit 3 segments via `on_segment_saved`

**Assert:**
- `consumer.tracks_played == 3`
- Worker `stats()["processed"] == 3`
- Tracks played in submission order (track via `_play_one` wrapper)

**Why this matters:** Validates FIFO behavior end-to-end, not just within each component.

---

### 3. `test_worker_failure_doesnt_block_consumer`
**What:** Worker fails on segment 2 of 3. Consumer still plays segments 1 and 3.

**Setup:**
- `analyze_song` raises on call #2, succeeds on #1 and #3
- Consumer thread running

**Assert:**
- `consumer.tracks_played == 2`
- Worker `stats()["errors"] == 1`, `stats()["processed"] == 2`
- Consumer didn't hang or crash

**Why this matters:** A bad MP3 in the capture stream must not stall the entire
pipeline. The consumer should keep playing whatever the worker produces.

---

### 4. `test_consumer_survives_empty_queue_between_tracks`
**What:** Worker produces segment 1, then nothing for 2+ seconds, then segment 2.
Consumer should block patiently and play both.

**Setup:**
- Submit segment 1 → wait 2s → submit segment 2
- Consumer thread running the whole time

**Assert:**
- `consumer.tracks_played == 2`
- No exceptions raised

**Why this matters:** In real usage there are gaps between songs (30s-5min analysis
time). The consumer must not timeout or exit during idle periods.

---

### 5. `test_purge_during_active_pipeline`
**What:** Consumer purges segment 1's files while worker is processing segment 2.

**Setup:**
- Create real temp files (mp3, json, analysis.json) for 2 segments
- Worker processes both (gated so segment 2 starts while segment 1 is being purged)
- Consumer has `purge=True`

**Assert:**
- Segment 1 files deleted after playback
- Segment 2 files deleted after playback
- No race condition (worker doesn't read files that consumer already purged)
- `consumer.tracks_purged == 2`

**Why this matters:** Worker reads MP3 for analysis. If consumer purges the MP3
before worker reads it, analysis would fail. In practice this can't happen (worker
finishes before consumer receives the item), but the test documents that guarantee.

---

### 6. `test_stop_event_drains_gracefully`
**What:** Stop event fires while worker has pending items and consumer is playing.

**Setup:**
- Worker processing a slow segment (gated with threading.Event)
- Consumer playing a track
- Fire `stop_event` mid-flight

**Assert:**
- Consumer exits its loop (thread joins within timeout)
- Worker's pending items are abandoned (not fatal)
- No deadlock — test completes within timeout
- Adapter `deactivate()` called

**Why this matters:** Ctrl+C during a live session must not hang. The consumer
thread must exit cleanly even if the queue has items or the worker is mid-process.

---

### 7. `test_backpressure_queue_does_not_overflow`
**What:** Worker produces 5 items faster than consumer can play them. Queue
buffers them; nothing is lost.

**Setup:**
- Worker: instant analysis/compile (no delay)
- Consumer: each `_play_one` takes 0.2s (simulated with `time.sleep`)
- Submit 5 segments rapidly

**Assert:**
- All 5 eventually played (`consumer.tracks_played == 5`)
- Queue was non-empty at some point during the run (backpressure happened)
- No items lost

**Why this matters:** During a session, a short song might finish capture
while the previous song's show is still playing. The queue must buffer
without dropping items or blocking the worker.

---

## Implementation Notes

- All tests mock `analyze_song`, `cached_compile_show`, `AudioPlayer`, and
  `ShowPlaybackRuntime` — no real audio I/O, no FFmpeg, no Govee devices
- Use `NullMultiAdapter` for the light adapter
- Each test creates its own `queue.Queue`, `threading.Event`, and cleanup
- Timeouts on all blocking operations (queue.get, thread.join) to prevent
  hanging tests — fail fast with `pytest.fail()` if timeout exceeded
- Use `tmp_path` fixture for any file I/O (purge tests)

## Completion Criteria

- [ ] `dev/tests/test_pipeline_integration.py` created with 7 tests
- [ ] All 7 tests pass: `python -m pytest dev/tests/test_pipeline_integration.py -v`
- [ ] All existing tests still pass: `python -m pytest dev/tests/ -q`
- [ ] No real audio, FFmpeg, or device dependencies (fully mocked)
- [ ] Each test completes in < 5 seconds (no long sleeps)
