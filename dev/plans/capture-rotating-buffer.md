# Capture Rotating Buffer

## Context

During long capture sessions (multi-hour playlists, overnight recordings), the capture pipeline writes one MP3 + one JSON sidecar per song to `output_dir`. With no limit, a 12-hour session at ~4 MB/song produces hundreds of files and several GB of disk usage. If the downstream analysis/compile/play pipeline only needs the most recent N songs, older files are wasted space — and on small drives, the disk can fill entirely, crashing FFmpeg mid-encode.

**Goal:** Cap the number of retained MP3 (+sidecar) pairs on disk during capture. When a new segment is finalized and the count exceeds the limit, delete the oldest file(s). The buffer limit is configurable and defaults to unlimited (no deletion) for backward compatibility.

## Files to Modify

| File | Change |
|------|--------|
| `src/dreamsync/capture/orchestrator.py` | Add `max_capture_files` to `OrchestratorConfig`; add `RotatingFileBuffer` class; wire into `_finalize_segment` |
| `src/dreamsync/capture/file_namer.py` | No changes required |
| `src/dreamsync/cli.py` | Add `--capture-buffer` CLI argument, pass to `OrchestratorConfig` |
| `src/dreamsync/session.py` | Pass `max_capture_files` through to `OrchestratorConfig` |
| `dev/tests/test_orchestrator.py` | Add `TestRotatingFileBuffer` (10 tests), add integration tests |

**No changes to `DynamicSplitProcessor`, `PcmAccumulator`, `EncoderProcess`, or `MetadataWriter`** — the rotating buffer operates purely at the file-management layer after segments are finalized.

## Design

### RotatingFileBuffer (new class, orchestrator.py)

Tracks finalized segment files and enforces a maximum count. Thread-safe (called from background finalization threads).

```
_finalize_segment completes:
  mp3_path = "2026-03-07_Artist_-_Song.mp3"
  sidecar_path = "2026-03-07_Artist_-_Song.json"
      │
      ▼
  rotating_buffer.add(mp3_path, sidecar_path)
      │
      ├─ append to internal deque
      ├─ if len(deque) > max_files:
      │     oldest = deque.popleft()
      │     delete oldest.mp3_path
      │     delete oldest.sidecar_path
      │     log deletion
      └─ return list of deleted paths (for logging/callback)
```

Key properties:
- **Thread-safe**: `threading.Lock` protects the deque (multiple finalization threads may complete concurrently)
- **Pair-aware**: each entry tracks both the MP3 and its JSON sidecar — both are deleted together
- **Tolerant**: missing files are logged and skipped (no crash if file was already removed externally)
- **Disabled by default**: `max_files=0` means unlimited — no entries are ever evicted
- **Ordered**: entries are appended in finalization order; oldest is always evicted first

### Data flow

```
CaptureOrchestrator.__init__
  └─ self._rotating_buffer = RotatingFileBuffer(max_files=config.max_capture_files)

_finalize_segment (background thread)
  └─ after sidecar write + rename:
       deleted = self._rotating_buffer.add(mp3_path, sidecar_path)
       if deleted:
           self._logger.log("INFO", "buffer", "rotating_buffer.evicted", ...)

on_segment_saved callback
  └─ fires BEFORE eviction (user sees "Saved: X" then "Evicted: Y")
```

---

## Steps

> **All steps completed 2026-03-07.** 125 orchestrator tests pass (108 existing + 17 new). 1370 total dev tests pass. CLI wired for `govee-live`, `session`, and `capture` subcommands.

### Step 1: Add `RotatingFileBuffer` class with unit tests ✅

**Prerequisites:** None — standalone new class.

**Deliverables:**
- `RotatingFileBuffer` class in `src/dreamsync/capture/orchestrator.py`
- `TestRotatingFileBuffer` test class in `dev/tests/test_orchestrator.py` (10 tests)

**Implementation details:**

```python
@dataclass
class BufferEntry:
    mp3_path: str
    sidecar_path: str

class RotatingFileBuffer:
    def __init__(self, max_files: int = 0) -> None:
        self._max_files = max_files
        self._entries: collections.deque[BufferEntry] = collections.deque()
        self._lock = threading.Lock()

    @property
    def max_files(self) -> int: ...

    @property
    def current_count(self) -> int: ...

    def add(self, mp3_path: str, sidecar_path: str) -> list[BufferEntry]:
        """Register a new file pair. Evict oldest if over limit.
        Returns list of evicted entries (empty if none)."""

    def _evict(self, entry: BufferEntry) -> None:
        """Delete mp3 and sidecar from disk. Log and skip on OSError."""

    def entries(self) -> list[BufferEntry]:
        """Return a snapshot of current entries (for testing/stats)."""
```

- `add()`: under lock — append entry, then while `len > max_files and max_files > 0`, popleft and call `_evict()`
- `_evict()`: call `os.unlink()` for both paths, catch `OSError` (file missing = skip)
- `entries()`: under lock — return `list(self._entries)`

**Tests (10):**
1. `test_unlimited_no_eviction` — `max_files=0`, add 100 entries, none evicted
2. `test_under_limit_no_eviction` — `max_files=5`, add 3, none evicted
3. `test_at_limit_no_eviction` — `max_files=3`, add 3, none evicted
4. `test_over_limit_evicts_oldest` — `max_files=3`, add 4, first entry evicted
5. `test_eviction_deletes_mp3_and_sidecar` — verify both files removed from disk (use `tmp_path`)
6. `test_eviction_skips_missing_files` — evict entry whose files don't exist, no exception
7. `test_multiple_evictions` — `max_files=2`, add 5, verify only last 2 remain
8. `test_entries_returns_snapshot` — snapshot is a copy, not a reference
9. `test_thread_safety_concurrent_adds` — 10 threads each add 10 entries, no corruption
10. `test_add_returns_evicted_list` — verify return value contains exactly the evicted entries

**Completion criteria:**
- All 10 `TestRotatingFileBuffer` tests pass
- Existing tests still pass (no regressions)

**Verify:**
```bash
python -m pytest dev/tests/test_orchestrator.py -v -k "TestRotatingFileBuffer"
python -m pytest dev/tests/test_orchestrator.py -v
```

---

### Step 2: Add `max_capture_files` to `OrchestratorConfig` ✅

**Prerequisites:** None — additive field, no behavior change.

**Deliverables:**
- New field `max_capture_files: int = 0` on `OrchestratorConfig`

**Implementation details:**
- Add `max_capture_files: int = 0` to the `OrchestratorConfig` dataclass, in the "Output paths" section alongside `output_dir` and `naming`
- Default `0` means unlimited (backward compatible — no eviction unless explicitly configured)

**Tests:**
- Existing `TestOrchestratorConfig.test_config_defaults` should be updated to assert `cfg.max_capture_files == 0`
- No new test class required

**Completion criteria:**
- Field present with default `0`
- All existing config tests pass

**Verify:**
```bash
python -m pytest dev/tests/test_orchestrator.py -v -k "TestOrchestratorConfig"
python -m pytest dev/tests/test_orchestrator.py -v
```

---

### Step 3: Wire `RotatingFileBuffer` into `CaptureOrchestrator` ✅

**Prerequisites:** Steps 1 and 2 (`RotatingFileBuffer` exists, `max_capture_files` config field exists).

**Deliverables:**
- `CaptureOrchestrator.__init__` creates `self._rotating_buffer = RotatingFileBuffer(max_files=self._config.max_capture_files)`
- `_finalize_segment` calls `self._rotating_buffer.add(mp3_path, sidecar_path)` after sidecar write, before firing the user callback
- Eviction events are logged

**Implementation details:**

In `__init__`, after `self._metadata_writer`:
```python
self._rotating_buffer = RotatingFileBuffer(
    max_files=self._config.max_capture_files,
)
```

In `_finalize_segment`, after the sidecar write block and before the user callback:
```python
# Rotating buffer: track file and evict oldest if over limit
evicted = self._rotating_buffer.add(mp3_path, sidecar_path)
for entry in evicted:
    self._logger.log(
        "INFO", "buffer", "rotating_buffer.evicted",
        data={
            "evicted_mp3": entry.mp3_path,
            "evicted_sidecar": entry.sidecar_path,
            "current_count": self._rotating_buffer.current_count,
            "max_files": self._rotating_buffer.max_files,
        },
    )
```

**Tests:**
- Add 2 integration tests in a new `TestRotatingBufferIntegration` class:
  1. `test_finalize_registers_files_in_buffer` — trigger segment completion, verify `_rotating_buffer.current_count` increments
  2. `test_finalize_evicts_when_over_limit` — configure `max_capture_files=2`, finalize 3 segments, verify first MP3+sidecar deleted from disk

**Completion criteria:**
- `_rotating_buffer` is created in `__init__`
- `_finalize_segment` calls `add()` after sidecar write
- Integration tests pass
- All existing tests pass (rotating buffer with `max_files=0` is a no-op)

**Verify:**
```bash
python -m pytest dev/tests/test_orchestrator.py -v -k "TestRotatingBufferIntegration"
python -m pytest dev/tests/test_orchestrator.py -v
```

---

### Step 4: Add `--capture-buffer` CLI argument ✅

**Prerequisites:** Step 2 (`max_capture_files` field exists on `OrchestratorConfig`).

**Deliverables:**
- `--capture-buffer` argument on the `govee-live` subcommand (and `capture` subcommand if present)
- Value passed through to `OrchestratorConfig(max_capture_files=...)`

**Implementation details:**

In `cli.py`, after the `--capture-naming` argument for `govee-live`:
```python
govee_live.add_argument(
    "--capture-buffer",
    type=int,
    default=0,
    metavar="N",
    help="Max MP3 files to keep on disk during capture (0 = unlimited, default: 0). "
         "Oldest files are deleted when the limit is exceeded.",
)
```

Where `OrchestratorConfig` is constructed for `govee-live` (around line 1133):
```python
orch_cfg = OrchestratorConfig(
    ...
    max_capture_files=getattr(args, "capture_buffer", 0),
)
```

Same for the `capture` subcommand (around line 794):
```python
orch_cfg = OrchestratorConfig(
    ...
    max_capture_files=getattr(args, "capture_buffer", 0),
)
```

In `session.py`, add `max_capture_files` parameter to `run_session()` and pass through to `OrchestratorConfig`:
```python
def run_session(..., max_capture_files: int = 0, ...):
    ...
    orch_cfg = OrchestratorConfig(
        ...
        max_capture_files=max_capture_files,
    )
```

**Tests:**
- No automated tests for CLI wiring (tested manually)
- Existing CLI tests must still pass

**Completion criteria:**
- `--capture-buffer 20` accepted by `govee-live` and `capture` subcommands
- Value flows through to `OrchestratorConfig.max_capture_files`
- `--capture-buffer 0` (default) preserves existing unlimited behavior

**Verify:**
```bash
python -m dreamsync govee-live --help | grep capture-buffer
python -m dreamsync capture --help | grep capture-buffer
python -m pytest tests/ -v
```

---

### Step 5: Add `rotating_buffer` stats to `CaptureOrchestrator.stats` ✅

**Prerequisites:** Step 3 (`_rotating_buffer` exists on orchestrator).

**Deliverables:**
- `stats` property includes `buffer_current_files`, `buffer_max_files`, and `buffer_evictions` fields

**Implementation details:**

Add an eviction counter to `RotatingFileBuffer`:
```python
def __init__(self, max_files: int = 0) -> None:
    ...
    self._total_evictions: int = 0

@property
def total_evictions(self) -> int:
    return self._total_evictions
```

Increment in `add()` for each evicted entry.

In `CaptureOrchestrator.stats`:
```python
return {
    ...
    "buffer_current_files": self._rotating_buffer.current_count,
    "buffer_max_files": self._rotating_buffer.max_files,
    "buffer_evictions": self._rotating_buffer.total_evictions,
}
```

**Tests:**
- `test_stats_includes_buffer_fields` — verify all three fields present and correct after finalization

**Completion criteria:**
- Stats dict includes buffer fields
- All tests pass

**Verify:**
```bash
python -m pytest dev/tests/test_orchestrator.py -v -k "stats or buffer"
python -m pytest dev/tests/test_orchestrator.py -v
```

---

### Step 6: Handle `_discard_segment` interaction ✅

**Prerequisites:** Step 3 (rotating buffer wired in).

**Deliverables:**
- Discarded segments (too-short fragments) are NOT added to the rotating buffer

**Implementation details:**

Verify that `_discard_segment` does NOT call `self._rotating_buffer.add()`. This should already be the case since `_discard_segment` deletes the temp file directly and never calls `_finalize_segment`. This step is a verification + explicit test.

**Tests:**
- `test_discarded_segment_not_tracked_in_buffer` — configure `min_segment_frames` high, trigger a short segment, verify `_rotating_buffer.current_count == 0`

**Completion criteria:**
- Discarded segments do not pollute the rotating buffer
- Test passes

**Verify:**
```bash
python -m pytest dev/tests/test_orchestrator.py -v -k "discarded"
python -m pytest dev/tests/test_orchestrator.py -v
```

---

### Step 7: Startup scan (optional, recommended) ✅

**Prerequisites:** Step 1 (`RotatingFileBuffer` exists).

**Deliverables:**
- `RotatingFileBuffer.scan_existing(output_dir)` method that pre-populates the buffer with MP3+sidecar pairs already on disk, sorted by modification time (oldest first)
- Called during `CaptureOrchestrator.__init__` so that resuming capture into an existing directory respects the buffer limit immediately

**Implementation details:**

```python
def scan_existing(self, output_dir: str) -> int:
    """Scan output_dir for existing .mp3 files, register them oldest-first.
    Returns the number of files registered."""
    dir_path = Path(output_dir)
    if not dir_path.is_dir():
        return 0
    mp3s = sorted(dir_path.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
    registered = 0
    for mp3 in mp3s:
        sidecar = mp3.with_suffix(".json")
        self.add(str(mp3), str(sidecar) if sidecar.exists() else "")
        registered += 1
    return registered
```

In `CaptureOrchestrator.__init__`, after creating `_rotating_buffer`:
```python
if self._config.max_capture_files > 0:
    scanned = self._rotating_buffer.scan_existing(self._config.output_dir)
    if scanned > 0:
        self._logger.log(
            "INFO", "buffer", "rotating_buffer.scan",
            data={"existing_files": scanned, "max_files": self._config.max_capture_files},
        )
```

**Tests (3):**
1. `test_scan_existing_populates_buffer` — create 5 MP3+JSON files in `tmp_path`, scan, verify `current_count == 5`
2. `test_scan_existing_evicts_over_limit` — create 5 files, `max_files=3`, scan, verify oldest 2 deleted
3. `test_scan_existing_empty_dir` — scan empty dir, `current_count == 0`

**Completion criteria:**
- Resuming capture with `--capture-buffer 10` into a dir with 15 existing files immediately evicts the 5 oldest
- All tests pass

**Verify:**
```bash
python -m pytest dev/tests/test_orchestrator.py -v -k "scan_existing"
python -m pytest dev/tests/test_orchestrator.py -v
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

# Manual: 10-minute capture with buffer limit
mkdir -p out/buffer-test
python -m dreamsync govee-live \
  --device 10.0.0.1:7:primary:ptreal \
  --duration 600 \
  --capture --capture-dir out/buffer-test \
  --capture-naming metadata --spotify \
  --capture-buffer 5

# Verify: at most 5 MP3 files on disk at any time
ls out/buffer-test/*.mp3 | wc -l   # should be <= 5

# Verify: oldest files were deleted (check logs)
grep "rotating_buffer.evicted" out/buffer-test/logs/*.jsonl
```
