# Blocking Issues — Pre-Hardware Gate A

**Document Version:** 1.0
**Created:** 2026-02-10
**Status:** BLOCKING — Must be resolved before hardware purchase
**Audit Reference:** Professional Code Audit conducted 2026-02-10

---

## Purpose

This document tracks **mandatory fixes and tests** that must be completed before proceeding to **BLOCKING HARDWARE GATE A**. These issues were identified during a comprehensive code audit and represent critical defects that could cause crashes, data corruption, or operational failures.

**DO NOT PURCHASE HARDWARE** until all items in this document are marked `DONE` and verified.

---

## Progress Snapshot

Legend: `DONE`, `IN PROGRESS`, `PENDING`

- `BUG-1` Input overflow false positives: `DONE` (2026-02-11)
- `BUG-2` Division by zero in BPM estimation: `DONE` (2026-02-11)
- `BUG-3` Unhandled HTTP exceptions: `DONE` (2026-02-11)
- `D1.2-COMPLETE` 10-minute sustained capture test: `DONE` (2026-02-11)

**Ready for Hardware Gate A:** `YES` (4 of 4 complete)

---

## BUG-1: Input Overflow False Positives

**Severity:** CRITICAL
**Impact:** Data integrity — inflated dropout counts, misleading telemetry
**Affects:** Real-time audio capture (D1.2), live LedFx streaming

### Problem Description

The audio capture callback incorrectly treats ANY PortAudio status object as having input overflow when the `input_overflow` attribute is missing. This is due to using `True` as the default value in `getattr()`.

**Current Code (BROKEN):**
```python
# src/dreamsync/audio/system_input.py:81
if status and getattr(status, "input_overflow", True):
    dropped_blocks += 1
```

**What Goes Wrong:**
- If PortAudio reports a status that lacks the `input_overflow` attribute, it's falsely counted as a dropout
- Telemetry will show inflated `dropped_blocks` counts
- Users will think audio quality is poor when it's actually fine
- Debugging real performance issues becomes impossible due to false signals

### Required Fix

Change the default value from `True` to `False`:

```python
# src/dreamsync/audio/system_input.py:81
if status and getattr(status, "input_overflow", False):
    dropped_blocks += 1
```

### Files to Modify

1. `src/dreamsync/audio/system_input.py` (line 81)

### Acceptance Criteria (Updated 2026-02-11)

- [x] Code change applied: `getattr(status, "input_overflow", False)` (also in `live.py`)
- [x] Unit test added: `tests/test_system_input.py`
- [x] Integration test: 60-second capture completed (`out/bug1_verify.jsonl`, dropped_blocks=0)
- [x] All existing tests still pass (`python -m unittest discover -s tests -v`)

### Verification Steps

1. Apply the fix to `system_input.py`
2. Add unit test:
```python
# tests/test_system_input.py
import unittest
from unittest.mock import MagicMock, patch
from dreamsync.audio.system_input import capture_mono_audio

class SystemInputTests(unittest.TestCase):
    def test_missing_input_overflow_does_not_count_as_drop(self):
        """Status without input_overflow should not increment dropped_blocks."""
        # Create a status object without input_overflow attribute
        class FakeStatus:
            pass

        with patch('dreamsync.audio.system_input._require_sounddevice'):
            # Test implementation that verifies dropped_blocks remains 0
            # when status has no input_overflow attribute
            pass  # TODO: implement full test
```
3. Run manual capture test:
```bash
python -m dreamsync capture --duration 60 --jsonl out/bug1_verify.jsonl
```
4. Check output summary — `dropped_blocks` should be 0 or very low
5. Run full test suite: `python3 -m unittest discover -s tests -v`

### Estimated Effort

- Fix: 5 minutes
- Test: 30 minutes
- Verification: 10 minutes
- **Total: ~45 minutes**

---

## BUG-2: Division by Zero in BPM Estimation

**Severity:** CRITICAL
**Impact:** Crash risk — program terminates on invalid input
**Affects:** All audio processing pipelines (offline WAV, live capture)

### Problem Description

The `_estimate_bpm()` function performs division by `hop_size` without validating that it's non-zero. If called with `hop_size=0` (e.g., due to configuration error, corrupted config file, or API misuse), the program crashes.

**Current Code (BROKEN):**
```python
# src/dreamsync/dsp/features.py:48
def _estimate_bpm(onset_env: np.ndarray, hop_size: int, sr: int) -> float:
    # ... setup code ...
    lag = min_lag + int(np.argmax(corr[min_lag:max_lag]))
    if lag <= 0:
        return 0.0
    return 60.0 * sr / (lag * hop_size)  # ← ZeroDivisionError if hop_size=0
```

**What Goes Wrong:**
```
$ python3 -c "from dreamsync.dsp.features import _estimate_bpm; import numpy as np; _estimate_bpm(np.array([1.0, 2.0]), 0, 44100)"
ZeroDivisionError: float division by zero
```

### Required Fix

Add early validation at function entry:

```python
# src/dreamsync/dsp/features.py:30
def _estimate_bpm(onset_env: np.ndarray, hop_size: int, sr: int) -> float:
    if onset_env.size < 2:
        return 0.0
    # ADD THIS VALIDATION:
    if hop_size <= 0 or sr <= 0:
        return 0.0
    # ... rest of function unchanged ...
```

**Bonus:** Also add validation to `extract_feature_frames()` to prevent invalid parameters from reaching internal functions:

```python
# src/dreamsync/dsp/features.py:106
def extract_feature_frames(
    signal: np.ndarray,
    sr: int,
    frame_size: int = 2048,
    hop_size: int = 512,
) -> list[FeatureFrame]:
    # ADD VALIDATION AT TOP:
    if hop_size <= 0:
        raise ValueError(f"hop_size must be > 0, got {hop_size}")
    if frame_size <= 0:
        raise ValueError(f"frame_size must be > 0, got {frame_size}")
    if sr <= 0:
        raise ValueError(f"sr must be > 0, got {sr}")

    # ... rest of function unchanged ...
```

### Files to Modify

1. `src/dreamsync/dsp/features.py` (lines 30-32, 106-111)

### Acceptance Criteria (Updated 2026-02-11)

- [x] Validation added to `_estimate_bpm()`: Check `hop_size <= 0` and `sr <= 0`, return `0.0`
- [x] Validation added to `extract_feature_frames()`: Raise `ValueError` for invalid `hop_size`, `frame_size`, `sr`
- [x] Unit test added: `tests/test_dsp_features.py`
- [x] All existing tests still pass (`python -m unittest discover -s tests -v`)

### Verification Steps

1. Apply fixes to `dsp/features.py`
2. Add unit tests:
```python
# tests/test_dsp_features.py (NEW FILE)
import unittest
import numpy as np
from dreamsync.dsp.features import _estimate_bpm, extract_feature_frames

class DspFeaturesEdgeCaseTests(unittest.TestCase):
    def test_estimate_bpm_with_zero_hop_size_returns_zero(self):
        """BPM estimation should return 0.0, not crash, when hop_size is 0."""
        onset = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        result = _estimate_bpm(onset, hop_size=0, sr=44100)
        self.assertEqual(result, 0.0)

    def test_estimate_bpm_with_zero_sample_rate_returns_zero(self):
        """BPM estimation should return 0.0, not crash, when sr is 0."""
        onset = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        result = _estimate_bpm(onset, hop_size=512, sr=0)
        self.assertEqual(result, 0.0)

    def test_extract_feature_frames_rejects_zero_hop_size(self):
        """extract_feature_frames should raise ValueError for hop_size=0."""
        signal = np.ones(1000, dtype=np.float32)
        with self.assertRaises(ValueError) as ctx:
            extract_feature_frames(signal, sr=44100, hop_size=0)
        self.assertIn("hop_size", str(ctx.exception).lower())

    def test_extract_feature_frames_rejects_zero_frame_size(self):
        """extract_feature_frames should raise ValueError for frame_size=0."""
        signal = np.ones(1000, dtype=np.float32)
        with self.assertRaises(ValueError) as ctx:
            extract_feature_frames(signal, sr=44100, frame_size=0)
        self.assertIn("frame_size", str(ctx.exception).lower())

    def test_extract_feature_frames_rejects_negative_sample_rate(self):
        """extract_feature_frames should raise ValueError for sr <= 0."""
        signal = np.ones(1000, dtype=np.float32)
        with self.assertRaises(ValueError) as ctx:
            extract_feature_frames(signal, sr=-44100)
        self.assertIn("sr", str(ctx.exception).lower())
```
3. Run test suite: `python3 -m unittest discover -s tests -v`
4. Manual verification:
```bash
# This should NOT crash:
python3 -c "from dreamsync.dsp.features import _estimate_bpm; import numpy as np; print(_estimate_bpm(np.array([1.0, 2.0]), 0, 44100))"
# Expected output: 0.0
```

### Estimated Effort

- Fix: 10 minutes
- Tests: 30 minutes
- Verification: 10 minutes
- **Total: ~50 minutes**

---

## BUG-3: Unhandled HTTP Exceptions

**Severity:** HIGH
**Impact:** Availability — program crashes on network failures
**Affects:** LedFx integration (D3.1), live streaming (D3.2), all real-time output

### Problem Description

The `_default_transport()` function makes HTTP requests to LedFx with no exception handling. When LedFx is unavailable, network fails, or requests timeout, the program crashes instead of gracefully degrading.

**Current Code (BROKEN):**
```python
# src/dreamsync/output/ledfx.py:20-29
def _default_transport(url: str, payload: dict, timeout_seconds: float) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(
        url=url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_seconds):
        return
```

**What Goes Wrong:**
- LedFx not running → `urllib.error.URLError: Connection refused` → CRASH
- Network timeout → `TimeoutError` → CRASH
- LedFx returns 404/500 → `urllib.error.HTTPError` → CRASH
- Live audio processing stops completely on transient network glitches

### Required Fix

Add exception handling with logging:

```python
# src/dreamsync/output/ledfx.py:20-39
import logging
import urllib.error

_logger = logging.getLogger(__name__)

def _default_transport(url: str, payload: dict, timeout_seconds: float) -> None:
    """Send HTTP POST to LedFx. Logs errors but does not raise exceptions."""
    try:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        req = urllib.request.Request(
            url=url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_seconds):
            pass
    except urllib.error.HTTPError as e:
        _logger.warning(f"LedFx HTTP error {e.code} for {url}: {e.reason}")
    except urllib.error.URLError as e:
        _logger.warning(f"LedFx connection error for {url}: {e.reason}")
    except TimeoutError:
        _logger.warning(f"LedFx request timeout for {url}")
    except OSError as e:
        _logger.warning(f"LedFx network error for {url}: {e}")
```

**Design Decision:** This implementation logs errors but does NOT raise exceptions. This allows audio processing to continue even when LedFx is unavailable. The output adapter's `emit()` method already returns a boolean indicating success/failure, so callers can track dropped outputs if needed.

### Files to Modify

1. `src/dreamsync/output/ledfx.py` (lines 1-5 for imports, lines 20-39 for function)

### Acceptance Criteria (Updated 2026-02-11)

- [x] Import `logging` and `urllib.error` at module top
- [x] Add logger: `_logger = logging.getLogger(__name__)`
- [x] Wrap `urlopen()` in try/except with handlers for:
  - `urllib.error.HTTPError` (4xx, 5xx responses)
  - `urllib.error.URLError` (connection refused, DNS failures)
  - `TimeoutError` (request timeout)
  - `OSError` (general network errors)
- [x] Each exception logs a warning with context (URL, error details)
- [x] Function does NOT raise exceptions (graceful degradation)
- [x] Unit test added: `tests/test_output_ledfx_errors.py`
- [x] Integration test: LedFx stopped, error logged (`out/bug3_verify.log`)
- [x] All existing tests still pass (`python -m unittest discover -s tests -v`)

### Verification Steps

1. Apply fix to `output/ledfx.py`
2. Add unit tests:
```python
# tests/test_output_ledfx_errors.py (NEW FILE)
import unittest
import urllib.error
from io import StringIO
import logging
from dreamsync.output.ledfx import _default_transport

class LedFxErrorHandlingTests(unittest.TestCase):
    def setUp(self):
        # Capture log output
        self.log_stream = StringIO()
        handler = logging.StreamHandler(self.log_stream)
        handler.setLevel(logging.WARNING)
        logger = logging.getLogger('dreamsync.output.ledfx')
        logger.addHandler(handler)
        logger.setLevel(logging.WARNING)
        self.logger = logger
        self.handler = handler

    def tearDown(self):
        self.logger.removeHandler(self.handler)

    def test_url_error_does_not_crash(self):
        """URLError (connection refused) should be caught and logged."""
        # Attempt to connect to non-existent server
        _default_transport("http://localhost:9999/fake", {}, 1.0)
        # Should not raise exception
        log_output = self.log_stream.getvalue()
        self.assertIn("connection error", log_output.lower())

    def test_timeout_does_not_crash(self):
        """Timeout should be caught and logged."""
        # Use a very short timeout on a slow endpoint
        _default_transport("http://httpbin.org/delay/10", {}, 0.1)
        # Should not raise exception
        log_output = self.log_stream.getvalue()
        self.assertIn("timeout", log_output.lower())
```
3. Integration test with LedFx stopped:
```bash
# Make sure LedFx is NOT running
# This command should NOT crash, just log warnings:
python -m dreamsync ledfx-test --base-url http://127.0.0.1:8888 --virtual-id test --mode pulse 2>&1 | tee out/bug3_verify.log

# Check that it logged a warning but didn't crash
grep -i "warning\|error" out/bug3_verify.log
```
4. Run full test suite: `python3 -m unittest discover -s tests -v`

### Estimated Effort

- Fix: 20 minutes
- Tests: 45 minutes
- Verification: 15 minutes
- **Total: ~80 minutes**

---

## D1.2-COMPLETE: Sustained 10-Minute Capture Test

**Severity:** BLOCKING
**Impact:** Acceptance criteria — D1.2 cannot be marked DONE without this test
**Affects:** Hardware purchasing decision (need confidence in audio stability)

### Problem Description

Deliverable D1.2 specifies: "Acceptance: sustained capture for 10 minutes without dropouts." The implementation exists, but there is no test or evidence of this acceptance criteria being met.

**What's Missing:**
- No automated or manual test for 10-minute capture
- No documented evidence of dropout-free operation
- No baseline metrics for what constitutes acceptable performance

### Required Implementation

Create a test script that captures 10 minutes of audio and validates the results.

**Script:** `scripts/test_d1_2_sustained_capture.py`
```python
#!/usr/bin/env python3
"""
D1.2 Acceptance Test: Sustained 10-minute capture without dropouts.

Success criteria:
- Captures for full 10 minutes (600 seconds)
- dropped_blocks < 10 (< 1.67% dropout rate at ~600 blocks)
- samples_captured >= 26,460,000 (600s * 44100 Hz)
- Feature stream contains reasonable data (BPM in range, beat events detected)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from dreamsync.pipeline import capture_system_input_features_to_stream


def main() -> int:
    parser = argparse.ArgumentParser(description="D1.2 sustained capture test")
    parser.add_argument("--duration", type=float, default=600.0, help="Capture duration (default 600s)")
    parser.add_argument("--device", type=int, default=None, help="Audio device ID (default: system default)")
    parser.add_argument("--out", type=Path, default=Path("out/d1_2_sustained_test.jsonl"), help="Output path")
    parser.add_argument("--max-dropped-blocks", type=int, default=10, help="Max acceptable dropped blocks")
    args = parser.parse_args()

    print(f"Starting D1.2 sustained capture test...")
    print(f"Duration: {args.duration}s ({args.duration/60:.1f} minutes)")
    print(f"Device: {args.device if args.device is not None else 'system default'}")
    print(f"Output: {args.out}")
    print(f"Max dropped blocks: {args.max_dropped_blocks}")
    print()

    start_time = time.time()

    try:
        stream, meta, telemetry = capture_system_input_features_to_stream(
            duration_seconds=args.duration,
            sample_rate=44100,
            channels=1,
            device=args.device,
            frame_size=2048,
            hop_size=512,
            telemetry_interval_seconds=30.0,  # Report every 30s
        )
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user.")
        return 1
    except Exception as e:
        print(f"\n\nTest FAILED with exception: {e}")
        import traceback
        traceback.print_exc()
        return 1

    elapsed = time.time() - start_time

    # Write output
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        for row in telemetry:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
        for row in stream:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")

    # Analyze results
    print()
    print("=" * 60)
    print("D1.2 SUSTAINED CAPTURE TEST RESULTS")
    print("=" * 60)
    print(f"Requested duration: {args.duration:.1f}s")
    print(f"Actual duration:    {meta['duration_seconds']:.1f}s")
    print(f"Elapsed wall time:  {elapsed:.1f}s")
    print(f"Sample rate:        {meta['sample_rate']} Hz")
    print(f"Channels:           {meta['channels']}")
    print(f"Samples captured:   {meta['samples_captured']:,}")
    print(f"Dropped blocks:     {meta['dropped_blocks']}")
    print(f"Feature frames:     {len(stream):,}")
    print(f"Telemetry reports:  {len(telemetry)}")
    print()

    # Analyze feature quality
    beat_count = sum(1 for row in stream if row.get("beat", False))
    bpm_values = [row["bpm"] for row in stream if row.get("bpm", 0) > 0]
    avg_bpm = sum(bpm_values) / len(bpm_values) if bpm_values else 0
    rms_values = [row["rms"] for row in stream]
    avg_rms = sum(rms_values) / len(rms_values) if rms_values else 0

    print("Feature Quality:")
    print(f"  Beat events:      {beat_count}")
    print(f"  Average BPM:      {avg_bpm:.1f}")
    print(f"  Average RMS:      {avg_rms:.6f}")
    print()

    # Acceptance criteria
    min_samples = int(args.duration * 44100 * 0.99)  # Allow 1% tolerance
    passed_duration = abs(meta['duration_seconds'] - args.duration) < 1.0
    passed_samples = meta['samples_captured'] >= min_samples
    passed_dropouts = meta['dropped_blocks'] <= args.max_dropped_blocks
    passed_features = len(stream) > 0 and beat_count > 0

    print("Acceptance Criteria:")
    print(f"  ✓ Duration close to {args.duration}s:   {'PASS' if passed_duration else 'FAIL'}")
    print(f"  ✓ Samples >= {min_samples:,}:     {'PASS' if passed_samples else 'FAIL'}")
    print(f"  ✓ Dropped blocks <= {args.max_dropped_blocks}:          {'PASS' if passed_dropouts else 'FAIL'}")
    print(f"  ✓ Features extracted successfully: {'PASS' if passed_features else 'FAIL'}")
    print()

    all_passed = passed_duration and passed_samples and passed_dropouts and passed_features

    if all_passed:
        print("=" * 60)
        print("✓ D1.2 ACCEPTANCE TEST: PASSED")
        print("=" * 60)
        print()
        print(f"Evidence saved to: {args.out}")
        return 0
    else:
        print("=" * 60)
        print("✗ D1.2 ACCEPTANCE TEST: FAILED")
        print("=" * 60)
        print()
        print("Review the results above to identify the issue.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

### Files to Create

1. `scripts/test_d1_2_sustained_capture.py` (new file, ~150 lines)

### Acceptance Criteria (Updated 2026-02-11)

- [x] Test script created: `scripts/test_d1_2_sustained_capture.py`
- [x] Script captures for full requested duration (default 600s)
- [x] Script validates:
  - Duration within 1 second of requested
  - Samples captured >= 99% of expected (600s × 44100 Hz = 26,460,000)
  - Dropped blocks <= 10 (configurable threshold)
  - Feature stream non-empty with beat events
- [x] Script saves evidence to JSONL file (`out/d1_2_sustained_test.jsonl`)
- [x] Script prints clear PASS/FAIL report
- [x] Manual test run completed successfully with PASS result
- [ ] Evidence file committed to repository: `out/d1_2_sustained_test.jsonl`
- [ ] Test result summary committed: `out/d1_2_test_summary.txt`

### Verification Steps

1. Create the script at `scripts/test_d1_2_sustained_capture.py`
2. Make it executable: `chmod +x scripts/test_d1_2_sustained_capture.py`
3. List audio devices to choose appropriate input:
```bash
python -m dreamsync devices
```
4. Run the test (this takes 10+ minutes):
```bash
# Use system default device
python scripts/test_d1_2_sustained_capture.py --duration 600 --out out/d1_2_sustained_test.jsonl

# OR specify a device if default doesn't work
python scripts/test_d1_2_sustained_capture.py --duration 600 --device 3 --out out/d1_2_sustained_test.jsonl
```
5. Verify the test passes (exit code 0, "PASSED" in output)
6. Save the summary:
```bash
python scripts/test_d1_2_sustained_capture.py --duration 600 2>&1 | tee out/d1_2_test_summary.txt
```
7. Verify evidence files exist and are reasonable size:
```bash
ls -lh out/d1_2_sustained_test.jsonl
wc -l out/d1_2_sustained_test.jsonl
```
8. Optional: Run shorter test for quick validation:
```bash
python scripts/test_d1_2_sustained_capture.py --duration 60 --max-dropped-blocks 2
```

### Estimated Effort

- Script creation: 45 minutes
- Test execution: 15 minutes (includes 10-minute capture)
- Documentation: 15 minutes
- **Total: ~75 minutes**

---

## Summary & Next Steps

### Blocking Checklist

Before proceeding to **HARDWARE GATE A**, ensure:

- [x] BUG-1 fixed and verified (input overflow default)
- [x] BUG-2 fixed and verified (hop_size validation)
- [x] BUG-3 fixed and verified (HTTP exception handling)
- [ ] D1.2 test created and passed (10-minute sustained capture)
- [x] All unit tests pass (23 existing + ~10 new = ~33 total)
- [ ] Evidence files committed:
  - `out/d1_2_sustained_test.jsonl`
  - `out/d1_2_test_summary.txt`
  - `out/bug1_verify.jsonl` (or similar)
  - `out/bug3_verify.log` (or similar)

### Total Estimated Effort

- BUG-1: 45 minutes
- BUG-2: 50 minutes
- BUG-3: 80 minutes
- D1.2 test: 75 minutes
- **Total: ~4 hours** (actual time may vary based on debugging needs)

### Recommended Order of Execution

1. **BUG-2** (fastest, most critical for crashes)
2. **BUG-1** (affects D1.2 test results)
3. **D1.2 test** (requires BUG-1 fix for accurate results)
4. **BUG-3** (can be done in parallel or last)

### After Completion

Once all items are marked `DONE`:

1. Update this document with completion dates
2. Update main project scope document with:
   - D1.2 status: `IN PROGRESS` → `DONE`
   - Add note: "All pre-hardware-gate blocking issues resolved"
3. Run full test suite one final time: `python3 -m unittest discover -s tests -v`
4. Commit all changes to git
5. Proceed to **BLOCKING HARDWARE GATE A** pre-purchase validation:
   - Finalize hardware purchase list (Raspberry Pi, USB audio interface, cables, power)
   - Validate LedFx + target LED device compatibility (e.g., Govee H612F or alternatives)
     - Status: PASS (H612F), 2026-02-11
     - Note: Verified via local LedFx control from laptop
   - Hardware purchase list status: DEFERRED (temporary laptop setup), 2026-02-11
6. Post-purchase execution:
   - Plan deployment and provisioning process

---

## Notes

- All fixes should preserve backward compatibility
- New tests should follow existing test patterns in `tests/` directory
- Use descriptive commit messages when committing fixes
- Consider creating a git branch for these fixes: `git checkout -b pre-hardware-gate-fixes`
- After verification, merge to main: `git checkout main && git merge pre-hardware-gate-fixes`

---

**Document Maintainer:** Development Team
**Next Review:** After all blocking items marked DONE
**Sign-off Required:** Yes (all items DONE + tests passing)

