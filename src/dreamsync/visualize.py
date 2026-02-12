from __future__ import annotations

import json
from pathlib import Path


def _load_feature_jsonl(path: Path) -> tuple[list[float], list[float], list[float], list[bool]]:
    ts: list[float] = []
    rms: list[float] = []
    bpm: list[float] = []
    beat: list[bool] = []

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            ts.append(float(row["t"]))
            rms.append(float(row["rms"]))
            bpm.append(float(row["bpm"]))
            beat.append(bool(row["beat"]))
    return ts, rms, bpm, beat


def render_feature_plot(input_jsonl: Path, output_png: Path) -> None:
    ts, rms, bpm, beat = _load_feature_jsonl(input_jsonl)
    if not ts:
        raise ValueError(f"no feature rows found in {input_jsonl}")

    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("matplotlib is required for plotting") from exc

    fig, ax1 = plt.subplots(figsize=(12, 5))
    ax1.set_title("Govee Audio Features")
    ax1.set_xlabel("Time (s)")
    ax1.set_ylabel("RMS")
    ax1.plot(ts, rms, color="#1f77b4", linewidth=1.2, label="RMS")

    beat_ts = [t for t, b in zip(ts, beat) if b]
    for t in beat_ts:
        ax1.axvline(t, color="#d62728", alpha=0.15, linewidth=1.0)

    ax2 = ax1.twinx()
    ax2.set_ylabel("BPM")
    ax2.plot(ts, bpm, color="#2ca02c", linewidth=1.2, label="BPM")

    fig.tight_layout()
    output_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_png, dpi=140)
    plt.close(fig)
