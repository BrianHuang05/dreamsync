"""Post-run telemetry inspector for bar noise tests.

Usage:
    python scripts/inspect_bar_telemetry.py out/bar-noise-v2/session-*/song-*.jsonl
"""

import json
import statistics
import sys
from collections import Counter
from pathlib import Path


def analyze_file(path: str) -> dict:
    rows = []
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            if "rms" in r:
                rows.append(r)

    if not rows:
        return {"path": path, "frames": 0}

    duration = rows[-1]["t"] - rows[0]["t"]
    rms_vals = [r["rms"] for r in rows]
    zero_rms_pct = 100 * sum(1 for r in rms_vals if r == 0) / len(rms_vals)

    bpms = [r["bpm"] for r in rows if r.get("bpm", 0) > 0]
    bpm_mean = statistics.mean(bpms) if bpms else 0
    bpm_std = statistics.stdev(bpms) if len(bpms) > 1 else 0

    beats = sum(1 for r in rows if r.get("beat"))
    sources = Counter(r.get("hybrid_source", "?") for r in rows)

    onsets = [r.get("onset_strength", 0) for r in rows]
    nz_onsets = sum(1 for o in onsets if o > 0)

    thresh_vals = [r.get("onset_thresh", 0) for r in rows]
    thresh_mean = statistics.mean(thresh_vals) if thresh_vals else 0
    neg_thresh = sum(1 for t in thresh_vals if t < 0)

    wf_vals = [r.get("whitened_flux", 0) for r in rows]
    nz_wf = sum(1 for w in wf_vals if w > 0)

    return {
        "path": Path(path).name,
        "frames": len(rows),
        "duration": duration,
        "zero_rms_pct": zero_rms_pct,
        "bpm_mean": bpm_mean,
        "bpm_std": bpm_std,
        "beats": beats,
        "sources": dict(sources),
        "onset_nonzero": nz_onsets,
        "onset_total": len(onsets),
        "thresh_mean": thresh_mean,
        "neg_thresh": neg_thresh,
        "wf_nonzero": nz_wf,
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/inspect_bar_telemetry.py <song-*.jsonl> ...")
        sys.exit(1)

    paths = sys.argv[1:]
    for path in sorted(paths):
        info = analyze_file(path)
        if info["frames"] == 0:
            print(f"{info['path']}: empty")
            continue

        print(f"--- {info['path']} ({info['frames']} frames, {info['duration']:.0f}s) ---")
        print(f"  RMS zero:     {info['zero_rms_pct']:.0f}%")
        print(f"  BPM:          {info['bpm_mean']:.1f} ± {info['bpm_std']:.1f}")
        print(f"  Beats:        {info['beats']}")
        print(f"  Hybrid src:   {info['sources']}")
        print(f"  Onset nonzero:{info['onset_nonzero']}/{info['onset_total']}")
        print(f"  Thresh mean:  {info['thresh_mean']:.4f} (negative: {info['neg_thresh']})")
        print(f"  WF nonzero:   {info['wf_nonzero']}")
        print()


if __name__ == "__main__":
    main()
