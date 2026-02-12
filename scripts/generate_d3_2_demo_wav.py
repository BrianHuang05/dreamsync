from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from scipy.io import wavfile


def build_signal(sample_rate: int) -> np.ndarray:
    segment_seconds = 2.0
    t = np.linspace(0, segment_seconds, int(sample_rate * segment_seconds), endpoint=False)
    low = (0.04 * np.sin(2.0 * np.pi * 220.0 * t)).astype(np.float32)
    mid = (0.10 * np.sin(2.0 * np.pi * 110.0 * t)).astype(np.float32)
    high = (0.35 * np.sin(2.0 * np.pi * 55.0 * t)).astype(np.float32)
    return np.concatenate([low, mid, high]).astype(np.float32)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate deterministic D3.2 demo WAV.")
    parser.add_argument("--out", type=Path, required=True, help="Output WAV path.")
    parser.add_argument("--sample-rate", type=int, default=44100, help="Sample rate.")
    args = parser.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    signal = build_signal(args.sample_rate)
    wavfile.write(args.out, args.sample_rate, signal)
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
