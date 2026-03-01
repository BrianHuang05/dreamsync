"""Inspect spectral telemetry from a govee-live session.

Usage:
    python scripts/inspect_telemetry.py telemetry-test/session-20260226-155437/song-001.jsonl
    python scripts/inspect_telemetry.py telemetry-test  # auto-picks latest session
"""

import json
import sys
from pathlib import Path


def find_jsonl(arg: str) -> Path:
    p = Path(arg)
    if p.is_file():
        return p
    # If it's a directory, find the latest session's song-001.jsonl
    if p.is_dir():
        sessions = sorted(p.glob("session-*/song-001.jsonl"))
        if not sessions:
            print(f"No session files found in {p}", file=sys.stderr)
            sys.exit(1)
        return sessions[-1]
    print(f"Not found: {arg}", file=sys.stderr)
    sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/inspect_telemetry.py <jsonl-file-or-telemetry-dir>")
        sys.exit(1)

    path = find_jsonl(sys.argv[1])
    print(f"Reading: {path}\n")

    rows = []
    for line in path.open():
        row = json.loads(line)
        if "onset_thresh" in row:
            rows.append(row)

    if not rows:
        # Fall back to old format (no spectral fields)
        path.open().seek(0)
        for line in path.open():
            row = json.loads(line)
            if "bpm" in row:
                rows.append(row)
        if rows:
            print("Old telemetry format (no spectral fields). Showing basic info:\n")
            print(f"{'t':>7}  {'bpm':>7}  {'rms':>8}  {'onset':>8}  {'beat'}")
            print("-" * 50)
            for r in rows[::50]:
                print(
                    f"{r['t']:7.1f}  "
                    f"{r.get('bpm', 0):7.1f}  "
                    f"{r.get('rms', 0):8.5f}  "
                    f"{r.get('onset_strength', 0):8.4f}  "
                    f"{r.get('beat', False)}"
                )
        else:
            print("No data rows found.")
        return

    print(f"Frames: {len(rows)}")
    print(f"Duration: {rows[-1]['t']:.1f}s\n")

    # Summary stats
    bpms = [r["bpm"] for r in rows if r["bpm"] > 0]
    if bpms:
        from statistics import mean, median, stdev
        print(f"BPM:  median={median(bpms):.1f}  mean={mean(bpms):.1f}  std={stdev(bpms):.1f}  range=[{min(bpms):.1f}, {max(bpms):.1f}]")

    sims = [r["template_similarity"] for r in rows if r["template_ready"]]
    if sims:
        from statistics import mean as m2, median as md2
        print(f"Template sim (after ready):  median={md2(sims):.3f}  mean={m2(sims):.3f}  range=[{min(sims):.3f}, {max(sims):.3f}]")

    ready_t = next((r["t"] for r in rows if r.get("template_ready")), None)
    if ready_t is not None:
        print(f"Template ready at: t={ready_t:.1f}s")
    else:
        print("Template never became ready")

    sources = {}
    for r in rows:
        s = r.get("hybrid_source", "?")
        sources[s] = sources.get(s, 0) + 1
    print(f"Hybrid source: {sources}")

    # Percussive onset stats
    perc_vals = [r.get("percussive_onset", 0) for r in rows]
    perc_nz = [p for p in perc_vals if p > 0]
    if perc_nz:
        from statistics import mean as pm, median as pmd, stdev as psd
        print(f"Percussive onset: {len(perc_nz)} nonzero / {len(rows)} total")
        print(f"  mean={pm(perc_nz):.4f}  median={pmd(perc_nz):.4f}  std={psd(perc_nz):.4f}  range=[{min(perc_nz):.4f}, {max(perc_nz):.4f}]")

    rms_vals = [r.get("rms", 0) for r in rows]
    rms_zero_pct = 100 * sum(1 for r in rms_vals if r == 0) / len(rms_vals) if rms_vals else 0
    print(f"RMS zero: {rms_zero_pct:.0f}%")

    # Time series
    has_perc = any(r.get("percussive_onset", 0) > 0 for r in rows)
    if has_perc:
        print(f"\n{'t':>7}  {'bpm':>7}  {'onset':>8}  {'thresh':>8}  {'perc':>8}  {'wf':>8}  {'src':<6}  {'sim':>6}  {'phase':>6}  {'beat'}")
        print("-" * 100)
    else:
        print(f"\n{'t':>7}  {'bpm':>7}  {'onset':>8}  {'thresh':>8}  {'kick':>8}  {'wf':>8}  {'src':<5}  {'sim':>6}  {'phase':>6}  {'beat'}")
        print("-" * 95)
    step = max(1, len(rows) // 40)  # ~40 rows of output
    for r in rows[::step]:
        if has_perc:
            print(
                f"{r['t']:7.1f}  "
                f"{r['bpm']:7.1f}  "
                f"{r['onset_strength']:8.4f}  "
                f"{r['onset_thresh']:8.4f}  "
                f"{r.get('percussive_onset', 0):8.4f}  "
                f"{r['whitened_flux']:8.4f}  "
                f"{r['hybrid_source']:<6}  "
                f"{r['template_similarity']:6.3f}  "
                f"{r['beat_phase']:6.3f}  "
                f"{r['beat']}"
            )
        else:
            print(
                f"{r['t']:7.1f}  "
                f"{r['bpm']:7.1f}  "
                f"{r['onset_strength']:8.4f}  "
                f"{r['onset_thresh']:8.4f}  "
                f"{r['kick_flux']:8.4f}  "
                f"{r['whitened_flux']:8.4f}  "
                f"{r['hybrid_source']:<5}  "
                f"{r['template_similarity']:6.3f}  "
                f"{r['beat_phase']:6.3f}  "
                f"{r['beat']}"
            )


if __name__ == "__main__":
    main()
