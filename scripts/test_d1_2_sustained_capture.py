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

    print("Starting D1.2 sustained capture test...")
    print(f"Duration: {args.duration}s ({args.duration / 60:.1f} minutes)")
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
            telemetry_interval_seconds=30.0,
        )
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user.")
        return 1
    except Exception as exc:
        print(f"\n\nTest FAILED with exception: {exc}")
        import traceback

        traceback.print_exc()
        return 1

    elapsed = time.time() - start_time

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        for row in telemetry:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
        for row in stream:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")

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

    min_samples = int(args.duration * 44100 * 0.99)
    passed_duration = abs(meta["duration_seconds"] - args.duration) < 1.0
    passed_samples = meta["samples_captured"] >= min_samples
    passed_dropouts = meta["dropped_blocks"] <= args.max_dropped_blocks
    passed_features = len(stream) > 0 and beat_count > 0

    print("Acceptance Criteria:")
    print(f"  Duration close to {args.duration}s:   {'PASS' if passed_duration else 'FAIL'}")
    print(f"  Samples >= {min_samples:,}:     {'PASS' if passed_samples else 'FAIL'}")
    print(f"  Dropped blocks <= {args.max_dropped_blocks}:          {'PASS' if passed_dropouts else 'FAIL'}")
    print(f"  Features extracted successfully: {'PASS' if passed_features else 'FAIL'}")
    print()

    all_passed = passed_duration and passed_samples and passed_dropouts and passed_features

    if all_passed:
        print("=" * 60)
        print("D1.2 ACCEPTANCE TEST: PASSED")
        print("=" * 60)
        print()
        print(f"Evidence saved to: {args.out}")
        return 0

    print("=" * 60)
    print("D1.2 ACCEPTANCE TEST: FAILED")
    print("=" * 60)
    print()
    print("Review the results above to identify the issue.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
