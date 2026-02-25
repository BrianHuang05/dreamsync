#!/usr/bin/env python3
"""
Analyze a log file and identify where song boundaries are triggered.

What it detects:
1) Explicit boundary markers like:
   "*** Song boundary detected (#N) — state reset ***"

2) Heuristic ("implicit") boundaries when the log *looks* like a reset even if the
   explicit marker is missing, e.g.:
   - bpm drops from > 0 to 0 (or near 0) and stays there for a short window
   - "beats" or "sent" counters appear to reset/decrease (when present)
   - mode flips to ambient/solid and bpm=0 around the same time

Outputs:
- A human-readable report to stdout
- Optional JSON / CSV export

Usage:
  python detect_song_boundaries.py /path/to/log.txt
  python detect_song_boundaries.py /path/to/log.txt --json out.json
  python detect_song_boundaries.py /path/to/log.txt --csv out.csv
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import re
from typing import Any, Dict, List, Optional, Tuple


# --- Regexes for common line types ---
RE_GOVEE = re.compile(
    r"govee\s+t=(?P<t>\d+(?:\.\d+)?)s\s+beats=(?P<beats>\d+)\s+\([^)]*\)\s+sent=(?P<sent>\d+)\s+dropped=(?P<dropped>\d+)\s+bpm=(?P<bpm>-?\d+(?:\.\d+)?)\s+\(raw=(?P<raw>-?\d+(?:\.\d+)?)\)\s+mode=(?P<mode>\w+)"
)

RE_MOOD = re.compile(r"^(?P<prefix>mood=.*)$")
RE_KV = re.compile(r"(?P<k>[a-zA-Z_]+)=(?P<v>[^ \n\r\t]+)")
RE_BOUNDARY = re.compile(r"\*\*\*\s*Song boundary detected\s*\(#(?P<n>\d+)\)", re.IGNORECASE)
RE_JSON_LINE = re.compile(r"^\s*\{.*\}\s*$")


@dataclasses.dataclass
class Snapshot:
    line_no: int
    t_seconds: Optional[float] = None

    # from govee line
    beats: Optional[int] = None
    sent: Optional[int] = None
    dropped: Optional[int] = None
    govee_mode: Optional[str] = None
    govee_bpm: Optional[float] = None
    govee_raw_bpm: Optional[float] = None

    # from mood/effect lines (latest seen)
    mood: Optional[str] = None
    effect: Optional[str] = None
    palette: Optional[str] = None
    render_mode: Optional[str] = None  # "mode=" from mood lines (not govee mode)
    energy: Optional[float] = None
    stability: Optional[float] = None
    bpm: Optional[float] = None  # bpm from mood lines

    def best_bpm(self) -> Optional[float]:
        # Prefer mood-line bpm, fall back to govee bpm
        return self.bpm if self.bpm is not None else self.govee_bpm


@dataclasses.dataclass
class BoundaryEvent:
    kind: str  # "explicit" or "implicit"
    boundary_index: Optional[int]  # from "(#N)" if explicit, else None
    line_no: int
    approx_t_seconds: Optional[float]

    pre: Optional[Snapshot]
    post: Optional[Snapshot]

    reason: str


def parse_float(s: str) -> Optional[float]:
    try:
        return float(s)
    except Exception:
        return None


def parse_int(s: str) -> Optional[int]:
    try:
        return int(s)
    except Exception:
        return None


def parse_mood_kv(line: str) -> Dict[str, Any]:
    # Extract key=value tokens from a "mood=..." line
    out: Dict[str, Any] = {}
    for m in RE_KV.finditer(line):
        k, v = m.group("k"), m.group("v")
        if k in ("energy", "stability", "bpm"):
            out[k] = parse_float(v)
        else:
            out[k] = v
    return out


def parse_govee(line: str) -> Optional[Dict[str, Any]]:
    m = RE_GOVEE.search(line)
    if not m:
        return None
    return {
        "t_seconds": parse_float(m.group("t")),
        "beats": parse_int(m.group("beats")),
        "sent": parse_int(m.group("sent")),
        "dropped": parse_int(m.group("dropped")),
        "govee_bpm": parse_float(m.group("bpm")),
        "govee_raw_bpm": parse_float(m.group("raw")),
        "govee_mode": m.group("mode"),
    }


def safe_json_load(line: str) -> Optional[Dict[str, Any]]:
    if not RE_JSON_LINE.match(line):
        return None
    try:
        obj = json.loads(line)
        if isinstance(obj, dict):
            return obj
        return None
    except Exception:
        return None


def snapshot_from_state(line_no: int, state: Snapshot, t_override: Optional[float] = None) -> Snapshot:
    # copy current state
    snap = dataclasses.replace(state)
    snap.line_no = line_no
    if t_override is not None:
        snap.t_seconds = t_override
    return snap


def analyze(
    path: str,
    context_lines: int = 8,
    implicit_window: int = 25,
    bpm_zero_threshold: float = 1e-3,
    require_bpm_drop: bool = True,
) -> Tuple[List[BoundaryEvent], Dict[str, Any]]:
    """
    Returns (events, meta)
    """
    events: List[BoundaryEvent] = []
    meta: Dict[str, Any] = {"path": path, "summary_json": None}

    # Rolling state (latest known values)
    state = Snapshot(line_no=0)

    # Store snapshots so we can reference around events
    snaps: List[Snapshot] = []

    # Keep last known timestamp (from govee lines)
    last_t: Optional[float] = None

    # For implicit detection we want a buffer of recent best_bpm values
    recent_best_bpms: List[Tuple[int, Optional[float], Optional[float]]] = []  # (line_no, t, bpm)

    # Also track counters to detect resets
    last_beats: Optional[int] = None
    last_sent: Optional[int] = None

    # Read file
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    # First pass: parse into snapshots per line when something meaningful occurs
    explicit_markers: List[Tuple[int, int]] = []  # (line_no, boundary_idx)

    for i, raw in enumerate(lines, start=1):
        line = raw.rstrip("\n")

        # Capture run summary JSON (if present)
        js = safe_json_load(line)
        if js and ("song_boundaries" in js or "duration_seconds" in js):
            # Keep the last summary-like JSON line
            meta["summary_json"] = js

        # Explicit boundary marker
        bm = RE_BOUNDARY.search(line)
        if bm:
            explicit_markers.append((i, int(bm.group("n"))))

        # govee line
        g = parse_govee(line)
        if g:
            for k, v in g.items():
                setattr(state, k if k != "t_seconds" else "t_seconds", v)
            last_t = state.t_seconds

            # update counters
            if state.beats is not None:
                last_beats = state.beats
            if state.sent is not None:
                last_sent = state.sent

            snaps.append(snapshot_from_state(i, state))
            recent_best_bpms.append((i, state.t_seconds, state.best_bpm()))
            continue

        # mood/effect line
        if RE_MOOD.match(line):
            kv = parse_mood_kv(line)
            state.mood = kv.get("mood", state.mood)
            state.effect = kv.get("effect", state.effect)
            state.palette = kv.get("palette", state.palette)
            state.render_mode = kv.get("mode", state.render_mode)
            if "energy" in kv:
                state.energy = kv["energy"]
            if "stability" in kv:
                state.stability = kv["stability"]
            if "bpm" in kv:
                state.bpm = kv["bpm"]

            # we don't always have a timestamp on these lines; carry last_t if known
            if state.t_seconds is None and last_t is not None:
                state.t_seconds = last_t

            snaps.append(snapshot_from_state(i, state))
            recent_best_bpms.append((i, state.t_seconds, state.best_bpm()))
            continue

        # anything else: we still want to know where we are, but only add sparse snapshots
        # (skip to keep memory reasonable)

    # Helper: get nearest snapshots around a line number
    def get_context(line_no: int) -> Tuple[Optional[Snapshot], Optional[Snapshot]]:
        # Find last snapshot <= line_no, and first snapshot >= line_no
        pre = None
        post = None
        for s in snaps:
            if s.line_no <= line_no:
                pre = s
            if s.line_no >= line_no:
                post = s
                break
        return pre, post

    # Add explicit events
    for ln, idx in explicit_markers:
        pre, post = get_context(ln)
        approx_t = pre.t_seconds if pre else None
        events.append(
            BoundaryEvent(
                kind="explicit",
                boundary_index=idx,
                line_no=ln,
                approx_t_seconds=approx_t,
                pre=pre,
                post=post,
                reason="Explicit marker line found.",
            )
        )

    # Implicit detection (skip regions close to explicit markers)
    explicit_lines = {ln for ln, _ in explicit_markers}

    # Create a quick lookup for best bpm by line for recent windowing
    bpm_points = [(ln, t, bpm) for (ln, t, bpm) in recent_best_bpms if bpm is not None]

    def is_near_explicit(ln: int, radius: int = 10) -> bool:
        for eln in explicit_lines:
            if abs(eln - ln) <= radius:
                return True
        return False

    # Detect: a "drop to ~0" that persists for a few samples
    # We'll scan the bpm_points list.
    for j in range(1, len(bpm_points)):
        ln_prev, t_prev, bpm_prev = bpm_points[j - 1]
        ln_cur, t_cur, bpm_cur = bpm_points[j]

        if is_near_explicit(ln_cur):
            continue

        if bpm_prev is None or bpm_cur is None:
            continue

        dropped_to_zero = (bpm_prev > bpm_zero_threshold) and (bpm_cur <= bpm_zero_threshold)
        if require_bpm_drop and not dropped_to_zero:
            continue

        # look ahead for persistence
        persist = 0
        for k in range(j, min(j + implicit_window, len(bpm_points))):
            _, _, bpm_k = bpm_points[k]
            if bpm_k is not None and bpm_k <= bpm_zero_threshold:
                persist += 1

        if persist >= max(3, implicit_window // 5):
            pre, post = get_context(ln_cur)
            # try to find a better timestamp from surrounding snaps
            approx_t = (pre.t_seconds if pre and pre.t_seconds is not None else t_cur)

            reason = f"bpm dropped from {bpm_prev:.3f} to {bpm_cur:.3f} and stayed ~0 for {persist} samples."
            events.append(
                BoundaryEvent(
                    kind="implicit",
                    boundary_index=None,
                    line_no=ln_cur,
                    approx_t_seconds=approx_t,
                    pre=pre,
                    post=post,
                    reason=reason,
                )
            )

    # Deduplicate implicit events that are very close to each other (keep first)
    events_sorted = sorted(events, key=lambda e: (e.line_no, e.kind))
    deduped: List[BoundaryEvent] = []
    last_ln = -10_000
    for e in events_sorted:
        if e.kind == "implicit" and (e.line_no - last_ln) <= 10:
            continue
        deduped.append(e)
        last_ln = e.line_no

    return deduped, meta


def format_snapshot(s: Optional[Snapshot]) -> str:
    if not s:
        return "(none)"
    parts = []
    if s.t_seconds is not None:
        parts.append(f"t={s.t_seconds:.1f}s")
    bpm = s.best_bpm()
    if bpm is not None:
        parts.append(f"bpm={bpm:.1f}")
    if s.mood:
        parts.append(f"mood={s.mood}")
    if s.effect:
        parts.append(f"effect={s.effect}")
    if s.palette:
        parts.append(f"palette={s.palette}")
    if s.render_mode:
        parts.append(f"mode={s.render_mode}")
    if s.energy is not None:
        parts.append(f"energy={s.energy:.3f}")
    if s.stability is not None:
        parts.append(f"stability={s.stability:.3f}")
    if s.govee_mode:
        parts.append(f"govee_mode={s.govee_mode}")
    if s.beats is not None:
        parts.append(f"beats={s.beats}")
    if s.sent is not None:
        parts.append(f"sent={s.sent}")
    return " ".join(parts) if parts else "(empty snapshot)"


def events_to_jsonable(events: List[BoundaryEvent], meta: Dict[str, Any]) -> Dict[str, Any]:
    def snap_to_dict(s: Optional[Snapshot]) -> Optional[Dict[str, Any]]:
        if not s:
            return None
        d = dataclasses.asdict(s)
        d["best_bpm"] = s.best_bpm()
        return d

    return {
        "meta": meta,
        "events": [
            {
                "kind": e.kind,
                "boundary_index": e.boundary_index,
                "line_no": e.line_no,
                "approx_t_seconds": e.approx_t_seconds,
                "reason": e.reason,
                "pre": snap_to_dict(e.pre),
                "post": snap_to_dict(e.post),
            }
            for e in events
        ],
    }


def write_csv(events: List[BoundaryEvent], path: str) -> None:
    fields = [
        "kind",
        "boundary_index",
        "line_no",
        "approx_t_seconds",
        "reason",
        "pre_best_bpm",
        "post_best_bpm",
        "pre_mood",
        "post_mood",
        "pre_effect",
        "post_effect",
        "pre_palette",
        "post_palette",
        "pre_render_mode",
        "post_render_mode",
        "pre_energy",
        "post_energy",
        "pre_stability",
        "post_stability",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for e in events:
            row = {
                "kind": e.kind,
                "boundary_index": e.boundary_index,
                "line_no": e.line_no,
                "approx_t_seconds": e.approx_t_seconds,
                "reason": e.reason,
                "pre_best_bpm": (e.pre.best_bpm() if e.pre else None),
                "post_best_bpm": (e.post.best_bpm() if e.post else None),
                "pre_mood": (e.pre.mood if e.pre else None),
                "post_mood": (e.post.mood if e.post else None),
                "pre_effect": (e.pre.effect if e.pre else None),
                "post_effect": (e.post.effect if e.post else None),
                "pre_palette": (e.pre.palette if e.pre else None),
                "post_palette": (e.post.palette if e.post else None),
                "pre_render_mode": (e.pre.render_mode if e.pre else None),
                "post_render_mode": (e.post.render_mode if e.post else None),
                "pre_energy": (e.pre.energy if e.pre else None),
                "post_energy": (e.post.energy if e.post else None),
                "pre_stability": (e.pre.stability if e.pre else None),
                "post_stability": (e.post.stability if e.post else None),
            }
            w.writerow(row)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?", default="/mnt/data/508d90f6-2e7c-496c-9c8f-2c0503fbd340.txt")
    ap.add_argument("--context", type=int, default=8, help="context window size for implicit detection")
    ap.add_argument("--implicit-window", type=int, default=25, help="how many samples to check for bpm staying ~0")
    ap.add_argument("--bpm-zero-threshold", type=float, default=1e-3, help="treat bpm <= this as zero")
    ap.add_argument("--no-require-bpm-drop", action="store_true", help="allow implicit detection without an immediate bpm drop")
    ap.add_argument("--json", dest="json_out", default=None, help="write JSON report to this path")
    ap.add_argument("--csv", dest="csv_out", default=None, help="write CSV report to this path")
    args = ap.parse_args()

    events, meta = analyze(
        path=args.file,
        context_lines=args.context,
        implicit_window=args.implicit_window,
        bpm_zero_threshold=args.bpm_zero_threshold,
        require_bpm_drop=(not args.no_require_bpm_drop),
    )

    print(f"File: {meta['path']}")
    if meta.get("summary_json"):
        sj = meta["summary_json"]
        sb = sj.get("song_boundaries")
        dur = sj.get("duration_seconds")
        rows = sj.get("rows")
        print(f"Summary JSON: duration_seconds={dur} rows={rows} song_boundaries={sb}")
    print()

    if not events:
        print("No song boundary events detected.")
    else:
        print(f"Detected {len(events)} boundary event(s):\n")
        for n, e in enumerate(events, start=1):
            t_str = f"{e.approx_t_seconds:.1f}s" if e.approx_t_seconds is not None else "unknown"
            idx = f"#{e.boundary_index}" if e.boundary_index is not None else "(no index)"
            print(f"[{n}] {e.kind.upper()} boundary {idx} at line {e.line_no} (t~{t_str})")
            print(f"     reason: {e.reason}")
            print(f"     pre : {format_snapshot(e.pre)}")
            print(f"     post: {format_snapshot(e.post)}")
            print()

    if args.json_out:
        payload = events_to_jsonable(events, meta)
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"Wrote JSON: {args.json_out}")

    if args.csv_out:
        write_csv(events, args.csv_out)
        print(f"Wrote CSV: {args.csv_out}")


if __name__ == "__main__":
    main()