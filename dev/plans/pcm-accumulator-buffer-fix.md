# Non-Blocking Encoder Rotation (PcmAccumulator)

## Context

During segment splits, the consumer thread blocks for 200-1000ms+ (FFmpeg wait + sidecar I/O + new process spawn). The 10-chunk AudioBuffer (~1s) overflows, stalling the capture FFmpeg process, which drops audio samples from VB-Cable. Result: static clipping and non-contiguous audio at every split point.

**Goal:** Zero blocking on the consumer thread during encoder rotation, using a PCM buffer that absorbs writes while the real encoder spawns in the background.

## Files to Modify

| File | Change |
|------|--------|
| `src/dreamsync/capture/orchestrator.py` | Add `PcmAccumulator` class; modify `_start_encoder`, `_on_segment_complete`, `_build_segment_metadata`, `__init__`, `stop()` |
| `src/dreamsync/capture/file_namer.py` | Add `threading.Lock` around `next_filename()` |
| `dev/tests/test_orchestrator.py` | Add `TestPcmAccumulator` (12 tests), update ~8 existing tests for async callbacks, add `TestNonBlockingRotation` |

**No changes to `DynamicSplitProcessor`** — its `_rotate_encoder` logic stays the same; only the callables it invokes become non-blocking.

## Design

### PcmAccumulator (new class, orchestrator.py)

Duck-types `EncoderProcess` interface. Two modes:

1. **Buffering** (before real encoder is ready): `write()` appends to `list[bytes]`
2. **Pass-through** (after `attach_encoder()`): `write()` delegates directly

```
Consumer thread:           Background thread:
  acc.write(data) ──┐
  acc.write(data)   │     _spawn():
  acc.write(data)   │       encoder = EncoderProcess(...)
                    │       encoder.start()
                    └──→    acc.attach_encoder(encoder)
                              ├─ drain buffer → encoder.write()
  acc.write(data) ────────→   └─ switch to pass-through
  acc.write(data) ────────→ encoder.write(data)
```

Thread safety: `threading.Lock` protects the buffer↔encoder swap. `threading.Event` gates `wait()` and `output_path` (only called from background finalization, not consumer).

Key methods:
- `write(data)` — non-blocking, buffer or pass-through
- `close()` — non-blocking, sets flag or closes real encoder
- `attach_encoder(encoder)` — drains buffer, switches to pass-through
- `wait(timeout)` — blocks on `_attached` event, then `encoder.wait()` (only called from background thread)
- `output_path` — blocks on `_attached`, returns real encoder's path

Edge case: `close()` called before `attach_encoder()` — sets `_close_requested` flag. When encoder attaches, buffer is drained AND encoder is immediately closed.

---

## Steps

### Step 1: Add `PcmAccumulator` class with unit tests

**Prerequisites:** None — this is a standalone new class.

**Deliverables:**
- `PcmAccumulator` class in `src/dreamsync/capture/orchestrator.py` with methods: `write()`, `close()`, `attach_encoder()`, `wait()`, `output_path` property
- `TestPcmAccumulator` test class in `dev/tests/test_orchestrator.py` (12 tests)

**Implementation details:**
- `__init__`: initialize `_buffer: list[bytes]`, `_encoder: EncoderProcess | None`, `_lock: threading.Lock`, `_attached: threading.Event`, `_close_requested: bool`, `_closed: bool`
- `write(data)`: under lock — if encoder attached, delegate; else append to `_buffer`
- `close()`: under lock — if encoder attached, close it; else set `_close_requested = True`
- `attach_encoder(encoder)`: under lock — drain `_buffer` to `encoder.write()`, set `_encoder`, set `_attached` event; if `_close_requested`, close encoder
- `wait(timeout)`: wait on `_attached` event, then call `encoder.wait(timeout)`
- `output_path` property: wait on `_attached` event, return `encoder.output_path`

**Tests (12):**
1. `test_write_buffers_before_attach` — writes buffered until attach
2. `test_attach_drains_buffer` — attach flushes all buffered data to encoder
3. `test_write_passes_through_after_attach` — post-attach writes go directly
4. `test_close_before_attach_sets_flag` — close without encoder sets flag
5. `test_close_after_attach_closes_encoder` — close delegates to encoder
6. `test_attach_after_close_drains_and_closes` — drain + immediate close
7. `test_wait_blocks_until_attached` — wait returns only after attach
8. `test_wait_delegates_to_encoder` — timeout forwarded to encoder.wait()
9. `test_output_path_blocks_until_attached` — property blocks on event
10. `test_output_path_returns_encoder_path` — returns real encoder path
11. `test_thread_safety_concurrent_writes` — multi-thread write doesn't corrupt
12. `test_empty_buffer_attach` — attach with no buffered data is a no-op drain

**Completion criteria:**
- All 12 `TestPcmAccumulator` tests pass
- Existing tests still pass (no regressions)

**Verify:**
```bash
python -m pytest dev/tests/test_orchestrator.py -v -k "TestPcmAccumulator"
python -m pytest dev/tests/test_orchestrator.py -v
```

---

### Step 2: Add thread safety to `FileNamer.next_filename()`

**Prerequisites:** None — independent of Step 1.

**Deliverables:**
- `threading.Lock` added to `FileNamer.__init__`
- `next_filename()` body wrapped with `with self._lock:`

**Implementation details:**
- Add `self._lock = threading.Lock()` in `FileNamer.__init__()`
- Wrap the entire body of `next_filename()` with `with self._lock:`
- Prevents counter races when concurrent background spawn threads call `next_filename()`

**Tests:**
- Existing `FileNamer` tests must still pass
- No new tests required (thread safety is an internal guarantee exercised by integration tests in Step 9)

**Completion criteria:**
- `next_filename()` is guarded by lock
- All existing tests pass

**Verify:**
```bash
python -m pytest dev/tests/test_orchestrator.py -v -k "FileNamer or file_namer"
python -m pytest dev/tests/ -v
```

---

### Step 3: Add finalization thread tracking to `CaptureOrchestrator.__init__`

**Prerequisites:** None — additive only, no behavior change yet.

**Deliverables:**
- `self._finalize_threads: list[threading.Thread]` added to `__init__`
- `self._finalize_lock: threading.Lock` added to `__init__`

**Implementation details:**
- Add both fields after existing `__init__` assignments
- `_finalize_threads` will hold references to background finalization threads (used by Steps 5 and 7)
- `_finalize_lock` protects append to `_finalize_threads` from concurrent access

**Tests:**
- No new tests — this is scaffolding. Verified by existing tests not breaking.

**Completion criteria:**
- Both fields present in `__init__`
- All existing tests pass (no behavior change)

**Verify:**
```bash
python -m pytest dev/tests/test_orchestrator.py -v
```

---

### Step 4: Modify `_build_segment_metadata` to accept optional pre-captured metadata

**Prerequisites:** Step 3 (fields exist on `self`).

**Deliverables:**
- `_build_segment_metadata` gains `boundary_meta: dict | None = None` parameter
- When `boundary_meta` is provided, skip `_get_segment_metadata()` call and use it directly
- When `boundary_meta` is `None` (default), existing behavior is preserved

**Implementation details:**
- Add parameter: `def _build_segment_metadata(self, ..., boundary_meta: dict | None = None)`
- At the point where `_get_segment_metadata()` is called, insert: `meta = boundary_meta if boundary_meta is not None else self._get_segment_metadata()`
- All existing callers pass nothing → falls back to queue peek (backward compatible)

**Tests:**
- Existing `_build_segment_metadata` / segment metadata tests still pass
- No new tests required — exercised by Step 5 integration

**Completion criteria:**
- Method signature updated with backward-compatible default
- All existing tests pass unchanged

**Verify:**
```bash
python -m pytest dev/tests/test_orchestrator.py -v -k "metadata or segment"
python -m pytest dev/tests/test_orchestrator.py -v
```

---

### Step 5: Modify `_on_segment_complete` to dispatch finalization to background thread

**Prerequisites:** Steps 3 and 4 (finalization tracking fields exist; `_build_segment_metadata` accepts pre-captured metadata).

**Deliverables:**
- `_on_segment_complete` captures boundary metadata on the consumer thread, then dispatches all blocking work to a background thread
- New `_finalize_segment` private method contains the moved blocking logic
- Background thread appended to `_finalize_threads` under `_finalize_lock`

**Implementation details:**
- In `_on_segment_complete` (consumer thread):
  1. Increment `_segments_completed`
  2. Capture metadata: `boundary_meta = self._get_segment_metadata()`
  3. Create `threading.Thread(target=self._finalize_segment, args=(encoder, segment_index, boundary_meta, ...))`
  4. Start thread, append to `_finalize_threads` under lock
- New `_finalize_segment(self, encoder, segment_index, boundary_meta, ...)`:
  1. `encoder.wait()` (blocks — OK, this is a background thread)
  2. Build metadata via `_build_segment_metadata(..., boundary_meta=boundary_meta)`
  3. Write sidecar file
  4. Log completion
  5. Invoke user `on_segment_complete` callback

**Tests:**
- Update ~8 existing tests that assert on callback timing/side effects — callbacks now happen asynchronously
- Add `_wait_for_finalizations()` test helper: joins all `_finalize_threads` with a short timeout
- Tests that check callback invocation must call `_wait_for_finalizations()` before asserting

**Completion criteria:**
- `_on_segment_complete` returns immediately (non-blocking)
- Callbacks and sidecar writes still happen (verified after joining threads)
- All updated tests pass

**Verify:**
```bash
python -m pytest dev/tests/test_orchestrator.py -v
```

---

### Step 6: Modify `_start_encoder` to return `PcmAccumulator` with background spawn

**Prerequisites:** Steps 1, 2, and 3 (`PcmAccumulator` exists, `FileNamer` is thread-safe, finalization tracking fields exist).

**Deliverables:**
- `_start_encoder` creates a `PcmAccumulator`, stores it in `_encoders[segment_index]`, spawns encoder creation in a background thread, and returns the accumulator immediately

**Implementation details:**
- In `_start_encoder` (consumer thread):
  1. Create `PcmAccumulator()`
  2. Store in `_encoders[segment_index]`
  3. Capture any metadata needed from boundary queue (consumer thread, before queue can change)
  4. Define `_spawn()` closure:
     - Call `self._file_namer.next_filename(...)` (thread-safe per Step 2)
     - Create `EncoderProcess(...)`, call `.start()`
     - Call `accumulator.attach_encoder(encoder)`
  5. Start `threading.Thread(target=_spawn)`
  6. Return accumulator
- The `DynamicSplitProcessor` receives the accumulator and calls `.write()` on it — non-blocking regardless of encoder state

**Tests:**
- Existing tests that mock `EncoderProcess` may need updates for accumulator layer
- Verify `_start_encoder` returns immediately (timing assertion)

**Completion criteria:**
- `_start_encoder` returns in <1ms (no FFmpeg spawn on consumer thread)
- Writes to the returned accumulator are buffered and then drained to real encoder
- All tests pass

**Verify:**
```bash
python -m pytest dev/tests/test_orchestrator.py -v
```

---

### Step 7: Modify `stop()` to join finalization threads

**Prerequisites:** Steps 3 and 5 (`_finalize_threads` and `_finalize_lock` exist, threads are being tracked).

**Deliverables:**
- `stop()` joins all threads in `_finalize_threads` after joining the consumer thread

**Implementation details:**
- After the existing `self._consumer_thread.join()` in `stop()`:
  1. Copy `_finalize_threads` under `_finalize_lock`
  2. Iterate and call `thread.join(timeout=10)` on each
  3. Log warning for any threads that don't finish within timeout
- Ensures all sidecar files are written and callbacks fired before `stop()` returns

**Tests:**
- Test that `stop()` waits for finalization threads
- Test that `stop()` handles empty `_finalize_threads` (no-op)
- Test that `stop()` logs warning on timeout

**Completion criteria:**
- `stop()` blocks until all finalization work is done (or timeout)
- All tests pass

**Verify:**
```bash
python -m pytest dev/tests/test_orchestrator.py -v -k "stop"
python -m pytest dev/tests/test_orchestrator.py -v
```

---

### Step 8: Update existing tests for async callbacks

**Prerequisites:** Steps 5 and 6 (callbacks are now async, encoder start is async).

**Deliverables:**
- `_wait_for_finalizations(orchestrator, timeout=5)` test helper function
- ~8 existing tests updated to call helper before asserting on callback side effects
- All previously passing tests continue to pass

**Implementation details:**
- Helper function:
  ```python
  def _wait_for_finalizations(orchestrator, timeout=5):
      with orchestrator._finalize_lock:
          threads = list(orchestrator._finalize_threads)
      for t in threads:
          t.join(timeout=timeout)
  ```
- Identify tests that assert on: `on_segment_complete` callback invocations, sidecar file existence, segment metadata content, log output from finalization
- Insert `_wait_for_finalizations(orch)` before those assertions

**Tests:**
- This IS the test update step — no new test class, just fixes to existing tests

**Completion criteria:**
- All existing tests pass with async finalization
- No test relies on synchronous callback behavior

**Verify:**
```bash
python -m pytest dev/tests/test_orchestrator.py -v
python -m pytest dev/tests/ -v
```

---

### Step 9: Add `TestNonBlockingRotation` integration tests

**Prerequisites:** All previous steps (1-8) complete.

**Deliverables:**
- `TestNonBlockingRotation` test class in `dev/tests/test_orchestrator.py`
- Integration tests that verify the full non-blocking pipeline end-to-end

**Tests:**
1. `test_rotation_does_not_block_consumer` — measure wall time of `_start_encoder` call, assert <10ms
2. `test_segment_complete_does_not_block_consumer` — measure wall time of `_on_segment_complete`, assert <10ms
3. `test_concurrent_rotation_and_finalization` — trigger two rapid splits, verify both segments finalize correctly
4. `test_data_continuity_across_split` — write PCM data across a split boundary, verify all data reaches encoders (no drops)
5. `test_stop_waits_for_pending_finalizations` — trigger split, immediately call `stop()`, verify sidecar files exist after stop returns

**Completion criteria:**
- All 5 integration tests pass
- Full test suite passes: `dev/tests/` and `tests/`

**Verify:**
```bash
python -m pytest dev/tests/test_orchestrator.py -v -k "TestNonBlockingRotation"
python -m pytest dev/tests/ -v
python -m pytest tests/ -v
```

---

## Final Verification

After all steps are complete:

```bash
# Run all capture pipeline tests
python -m pytest dev/tests/test_orchestrator.py -v

# Run full dev test suite
python -m pytest dev/tests/ -v

# Run core test suite
python -m pytest tests/ -v

# Live test: 3+ songs with Spotify splitting
mkdir -p out/capture-split-test
python -m dreamsync govee-live \
  --device 10.0.0.1:7:primary:ptreal \
  --duration 600 \
  --capture --capture-dir out/capture-split-test \
  --capture-naming metadata --spotify --debug-mood

# Verify: segment durations add up, no static at boundaries
for f in out/capture-split-test/*.mp3; do
  echo "$f"; ffprobe -v error -show_entries format=duration \
    -of default=noprint_wrappers=1:nokey=1 "$f"
done
```
