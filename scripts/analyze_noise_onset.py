"""Deep analysis of noise-robust onset detection telemetry.

Usage:
    python scripts/analyze_noise_onset.py out/bar-noise-v2/session-*/song-002.jsonl
"""

import json
import math
import statistics
import sys
from collections import Counter

import numpy as np


def load_frames(path: str) -> list[dict]:
    rows = []
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            if "rms" in r:
                rows.append(r)
    return rows


def analyze(path: str) -> None:
    rows = load_frames(path)
    if not rows:
        print(f"{path}: empty")
        return

    n = len(rows)
    dur = rows[-1]["t"] - rows[0]["t"]
    print(f"=== {path} ({n} frames, {dur:.0f}s) ===\n")

    # --- Signal presence ---
    rms = [r["rms"] for r in rows]
    active = [i for i, r in enumerate(rows) if r["rms"] > 0.001]
    print(f"Signal: {len(active)}/{n} frames active ({100*len(active)/n:.0f}%)")
    print(f"  RMS when active: mean={np.mean([rms[i] for i in active]):.5f}")

    # --- Whitened flux distribution ---
    wf_all = [r.get("whitened_flux", 0) for r in rows]
    wf_nz = [w for w in wf_all if w > 0]
    print(f"\nWhitened flux: {len(wf_nz)} nonzero")
    if wf_nz:
        print(f"  raw:  mean={np.mean(wf_nz):.1f}  median={np.median(wf_nz):.1f}  "
              f"p25={np.percentile(wf_nz, 25):.1f}  p75={np.percentile(wf_nz, 75):.1f}  "
              f"p95={np.percentile(wf_nz, 95):.1f}  max={max(wf_nz):.1f}")
        log_wf = [math.log1p(w) for w in wf_nz]
        print(f"  log1p: mean={np.mean(log_wf):.2f}  median={np.median(log_wf):.2f}  "
              f"p25={np.percentile(log_wf, 25):.2f}  p75={np.percentile(log_wf, 75):.2f}  "
              f"p95={np.percentile(log_wf, 95):.2f}  max={max(log_wf):.2f}")

    # --- Onset after log1p: how selective is detection? ---
    onset_raw = [math.log1p(r.get("whitened_flux", 0)) for r in rows]
    onset_arr = np.array(onset_raw, dtype=np.float32)
    onset_std = float(onset_arr.std())
    if onset_std > 1e-8:
        onset_normed = onset_arr / onset_std
    else:
        onset_normed = onset_arr

    # Simulate adaptive threshold
    sr, hop = 44100, 512
    w = max(1, int(0.5 * sr / hop))
    local_thresh = np.empty(n, dtype=np.float32)
    for i in range(n):
        lo = max(0, i - w)
        hi = min(n, i + w + 1)
        local_thresh[i] = float(np.median(onset_normed[lo:hi])) * 1.8

    above = onset_normed > local_thresh
    print(f"\nAdaptive threshold selectivity:")
    print(f"  Frames above threshold: {above.sum()}/{n} ({100*above.sum()/n:.0f}%)")
    print(f"  Threshold: mean={local_thresh.mean():.4f}  median={np.median(local_thresh):.4f}")

    # What if we add a floor = mean + 0.5*std?
    onset_mean = float(onset_normed.mean())
    floor = onset_mean + 0.5 * float(onset_normed.std())
    local_thresh_floored = np.maximum(local_thresh, floor)
    above_floored = onset_normed > local_thresh_floored
    print(f"\n  With floor ({floor:.3f} = mean + 0.5*std):")
    print(f"    Frames above: {above_floored.sum()}/{n} ({100*above_floored.sum()/n:.0f}%)")

    # What about higher floors?
    for k in [1.0, 1.5, 2.0]:
        fl = onset_mean + k * float(onset_normed.std())
        above_k = (onset_normed > np.maximum(local_thresh, fl)).sum()
        pct = 100 * above_k / n
        # Expected beat count at various BPMs
        beats_at_130 = dur * 130 / 60
        print(f"    floor=mean+{k:.1f}*std ({fl:.3f}): {above_k} frames above "
              f"({pct:.1f}%, expect ~{beats_at_130:.0f} at 130 BPM)")

    # --- IOI analysis with different threshold floors ---
    print(f"\nIOI analysis with threshold floor = mean + 1.0*std:")
    fl = onset_mean + 1.0
    lt = np.maximum(local_thresh, fl)
    peaks_mask = np.zeros(n, dtype=bool)
    peaks_mask[1:-1] = (
        (onset_normed[1:-1] > onset_normed[:-2])
        & (onset_normed[1:-1] >= onset_normed[2:])
        & (onset_normed[1:-1] > lt[1:-1])
    )
    peak_idx = np.where(peaks_mask)[0].tolist()
    min_gap = max(1, int(sr * 0.15 / hop))
    deduped = [peak_idx[0]] if peak_idx else []
    for idx in peak_idx[1:]:
        if idx - deduped[-1] >= min_gap:
            deduped.append(idx)

    print(f"  Peaks: {len(peak_idx)} raw, {len(deduped)} deduped (min_gap={min_gap})")
    if len(deduped) > 2:
        iois = [deduped[i + 1] - deduped[i] for i in range(len(deduped) - 1)]
        ioi_s = [ioi * hop / sr for ioi in iois]
        ioi_bpm = [60.0 / s for s in ioi_s if s > 0.1]
        print(f"  IOI: mean={np.mean(ioi_s):.3f}s  median={np.median(ioi_s):.3f}s  "
              f"std={np.std(ioi_s):.3f}s")
        if ioi_bpm:
            print(f"  BPM: mean={np.mean(ioi_bpm):.1f}  median={np.median(ioi_bpm):.1f}  "
                  f"std={np.std(ioi_bpm):.1f}")

        # IOI histogram
        ioi_bins = [round(ioi / 5) * 5 for ioi in iois]
        hist = Counter(ioi_bins)
        print(f"  IOI histogram (top 8):")
        for k, v in hist.most_common(8):
            bpm = 60.0 / (k * hop / sr) if k > 0 else 0
            print(f"    {k:4d} frames ({k*hop/sr:.3f}s, ~{bpm:.0f} BPM): {v}")

    # --- Beat rate over time ---
    print(f"\nBPM over time (10s windows):")
    bpm_vals = [r["bpm"] for r in rows]
    beats = [r["beat"] for r in rows]
    chunk_frames = int(10.0 * sr / hop)
    for start in range(0, n, chunk_frames):
        chunk_rows = rows[start : start + chunk_frames]
        chunk_bpms = [r["bpm"] for r in chunk_rows if r["bpm"] > 0]
        chunk_beats = sum(1 for r in chunk_rows if r["beat"])
        t0 = chunk_rows[0]["t"]
        t1 = chunk_rows[-1]["t"]
        dt = t1 - t0
        actual_rate = chunk_beats / dt if dt > 0 else 0
        if chunk_bpms:
            bpm_med = statistics.median(chunk_bpms)
            expected_rate = bpm_med / 60
            print(f"  t={t0:6.1f}-{t1:6.1f}s  bpm={bpm_med:5.1f}  "
                  f"beats={chunk_beats:3d} ({actual_rate:.1f}/s, expect {expected_rate:.1f}/s)")


def main():
    for path in sys.argv[1:]:
        analyze(path)
        print()


if __name__ == "__main__":
    main()
