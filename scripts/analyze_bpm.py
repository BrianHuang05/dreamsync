"""Analyze BPM stability from telemetry JSONL files.

Usage:
    python scripts/analyze_bpm.py out/longrun/bpm-15m
    python scripts/analyze_bpm.py out/longrun/bpm-15m --window 15
    python scripts/analyze_bpm.py out/longrun/bpm-15m -o out/longrun/bpm-analysis.txt
"""

import io
import json
import glob
import statistics
import sys
from collections import Counter
from pathlib import Path


class Tee(io.TextIOBase):
    """Write to both stdout and a file."""

    def __init__(self, path: str) -> None:
        self._file = open(path, "w", encoding="utf-8")
        self._stdout = sys.stdout

    def write(self, s: str) -> int:
        self._stdout.write(s)
        self._file.write(s)
        return len(s)

    def flush(self) -> None:
        self._stdout.flush()
        self._file.flush()

    def close(self) -> None:
        self._file.close()


def bpm_bucket(bpm: float, center: float) -> str:
    """Classify a BPM relative to a center as normal, half, double, etc."""
    ratio = bpm / center
    if 0.85 <= ratio <= 1.15:
        return "1x"
    elif 0.42 <= ratio <= 0.58:
        return "1/2x"
    elif 1.85 <= ratio <= 2.15:
        return "2x"
    elif 0.62 <= ratio <= 0.78:
        return "2/3x"
    elif 1.42 <= ratio <= 1.58:
        return "3/2x"
    else:
        return "other"


def analyze(telemetry_dir: str, window_sec: float = 30.0) -> None:
    base = Path(telemetry_dir)
    # Try session-*/song-*.jsonl first; fall back to song-*.jsonl (direct session dir)
    files = sorted(glob.glob(str(base / "session-*" / "song-*.jsonl")))
    if not files:
        files = sorted(glob.glob(str(base / "song-*.jsonl")))
    if not files:
        print(f"No JSONL files found in {telemetry_dir}")
        return

    print(f"Files: {len(files)}")
    for f in files:
        print(f"  {f}")
    print()

    # Collect all data with timestamps
    all_bpms: list[float] = []
    all_timed: list[tuple[float, float]] = []  # (absolute_t, bpm)
    per_song: dict[str, list[tuple[float, float]]] = {}
    cumulative_t = 0.0

    for f in files:
        song_name = Path(f).stem
        song_data: list[tuple[float, float]] = []
        last_t = 0.0
        for line in open(f):
            row = json.loads(line)
            t = row.get("t", 0.0)
            b = row.get("bpm", 0)
            last_t = t
            if b > 0:
                all_bpms.append(b)
                all_timed.append((cumulative_t + t, b))
                song_data.append((t, b))
        per_song[song_name] = song_data
        cumulative_t += last_t

    if not all_bpms:
        print("No BPM data found")
        return

    # --- Overall stats ---
    print(f"Total BPM samples: {len(all_bpms)}")
    print(f"BPM: mean={statistics.mean(all_bpms):.1f}, stdev={statistics.stdev(all_bpms):.1f}")
    print(f"Range: {min(all_bpms):.1f} - {max(all_bpms):.1f}")
    median = statistics.median(all_bpms)
    print(f"Median: {median:.1f}")

    outliers = [b for b in all_bpms if b < 40 or b > 220]
    print(f"Outliers (<40 or >220): {len(outliers)} ({100 * len(outliers) / len(all_bpms):.1f}%)")

    # Half/double-time analysis
    buckets = Counter(bpm_bucket(b, median) for b in all_bpms)
    print(f"\nRelative to median ({median:.0f} BPM):")
    for label in ["1x", "1/2x", "2x", "2/3x", "3/2x", "other"]:
        count = buckets.get(label, 0)
        if count:
            print(f"  {label}: {count} ({100 * count / len(all_bpms):.1f}%)")
    print()

    # --- Per-song breakdown ---
    print("Per-song breakdown:")
    for song, sdata in per_song.items():
        if not sdata:
            print(f"  {song}: no BPM data")
            continue
        sbpms = [b for _, b in sdata]
        mean = statistics.mean(sbpms)
        stdev = statistics.stdev(sbpms) if len(sbpms) > 1 else 0.0
        smed = statistics.median(sbpms)
        duration = sdata[-1][0] if sdata else 0
        # Flag half/double-time within song
        song_buckets = Counter(bpm_bucket(b, smed) for b in sbpms)
        off_center = sum(v for k, v in song_buckets.items() if k != "1x")
        pct_off = 100 * off_center / len(sbpms) if sbpms else 0
        flag = " *** UNSTABLE" if pct_off > 20 else ""
        print(f"  {song} ({duration:.0f}s): n={len(sbpms)}, median={smed:.1f}, "
              f"stdev={stdev:.1f}, range={min(sbpms):.1f}-{max(sbpms):.1f}, "
              f"off-center={pct_off:.0f}%{flag}")
    print()

    # --- Time-windowed view ---
    print(f"Time-windowed BPM ({window_sec:.0f}s windows, absolute time):")
    print(f"  {'Window':>10s}  {'n':>5s}  {'median':>7s}  {'stdev':>7s}  {'range':>15s}  {'flag'}")
    print(f"  {'------':>10s}  {'---':>5s}  {'------':>7s}  {'-----':>7s}  {'-----':>15s}  {'----'}")

    if not all_timed:
        return

    max_t = all_timed[-1][0]
    window_start = 0.0
    while window_start < max_t:
        window_end = window_start + window_sec
        wbpms = [b for t, b in all_timed if window_start <= t < window_end]
        if wbpms:
            wmed = statistics.median(wbpms)
            wstdev = statistics.stdev(wbpms) if len(wbpms) > 1 else 0.0
            flag = ""
            if wstdev > 15:
                flag = "HIGH-VARIANCE"
            elif wstdev > 8:
                flag = "moderate-var"
            label = f"{int(window_start):>4d}-{int(window_end):>4d}s"
            print(f"  {label:>10s}  {len(wbpms):>5d}  {wmed:>7.1f}  {wstdev:>7.1f}  "
                  f"{min(wbpms):>6.1f}-{max(wbpms):<6.1f}  {flag}")
        window_start = window_end


if __name__ == "__main__":
    args = sys.argv[1:]
    telemetry_dir = "out/longrun/bpm-15m"
    window = 30.0
    output_file = None

    i = 0
    while i < len(args):
        if args[i] == "--window" and i + 1 < len(args):
            window = float(args[i + 1])
            i += 2
        elif args[i] in ("-o", "--output") and i + 1 < len(args):
            output_file = args[i + 1]
            i += 2
        else:
            telemetry_dir = args[i]
            i += 1

    tee = None
    if output_file:
        Path(output_file).parent.mkdir(parents=True, exist_ok=True)
        tee = Tee(output_file)
        sys.stdout = tee

    try:
        analyze(telemetry_dir, window_sec=window)
    finally:
        if tee:
            sys.stdout = tee._stdout
            tee.close()
            print(f"\nOutput written to {output_file}")
