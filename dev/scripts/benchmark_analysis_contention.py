"""Compare old/new analysis with a synthetic 60 Hz playback workload.

Run from the repository: python dev/scripts/benchmark_analysis_contention.py
Optional: --audio /path/to/completed.mp3 --repeats 3 --output results.json
The load is Python scheduling pressure, not an audio-device/GUI acceptance test.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import numpy as np

from dreamsync.pipeline_process import PipelineProcesses


def legacy_chroma(mag, freqs, mapping=None):
    bins = np.zeros(12, dtype=np.float64)
    for i, frequency in enumerate(freqs):
        if frequency >= 20:
            bins[int(round(12 * np.log2(frequency / 440))) % 12] += mag[i]
    if bins.max() > 1e-10:
        bins /= bins.max()
    return tuple(float(value) for value in bins)


def prepare(path, legacy):
    from dreamsync.analyzer import features
    from dreamsync.analyzer.analyze import analyze_song
    from dreamsync.compiler.compile import compile_show
    original = features._compute_chroma
    if legacy:
        features._compute_chroma = legacy_chroma
    try:
        start = time.perf_counter()
        structure = analyze_song(path)
        analyzed = time.perf_counter()
        timeline = compile_show(structure, seed=17)
        compiled = time.perf_counter()
        payload = json.dumps(timeline.to_dict(), sort_keys=True).encode()
        return {
            "analysis_seconds": analyzed - start,
            "compile_seconds": compiled - analyzed,
            "output_sha256": hashlib.sha256(payload).hexdigest(),
            "duration_seconds": structure.duration,
        }
    finally:
        features._compute_chroma = original


def measure(path, mode, load):
    runner = PipelineProcesses()
    intervals = []
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            start = last_tick = time.perf_counter()
            if mode == "isolated":
                job = pool.submit(runner.run, prepare, path, False)
            else:
                job = pool.submit(prepare, path, mode == "legacy")
            while not job.done():
                now = time.perf_counter()
                intervals.append(now - last_tick)
                last_tick = now
                if load:
                    # Four milliseconds of Python work per requested 60 Hz tick.
                    until = time.thread_time() + 0.004
                    value = 0
                    while time.thread_time() < until:
                        value = (value * 33 + 1) % 1000003
                time.sleep(max(0, 1 / 60 - (time.perf_counter() - now)))
            result = job.result()
            result.update(
                mode=mode, synthetic_playback=load,
                wall_seconds=time.perf_counter() - start,
                tick_p95_ms=float(np.percentile(intervals[1:] or [0], 95) * 1000),
            )
            return result
    finally:
        runner.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path)
    parser.add_argument("--seconds", type=int, default=15)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.seconds < 1 or args.repeats < 1:
        parser.error("seconds and repeats must be positive")
    # The default Windows temp location can be outside a restricted workspace.
    benchmark_root = Path(__file__).resolve().parents[2] / "out"
    benchmark_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dreamsync-benchmark-", dir=benchmark_root) as directory:
        path = args.audio.resolve() if args.audio else Path(directory) / "synthetic.wav"
        if not args.audio:
            sr = 44100
            t = np.arange(sr * args.seconds) / sr
            envelope = 0.1 + 0.1 * np.maximum(0, np.sin(2 * np.pi * 2 * t))
            signal = np.column_stack((envelope * np.sin(2 * np.pi * 220 * t),
                                      envelope * np.sin(2 * np.pi * 330 * t)))
            with wave.open(str(path), "wb") as output:
                output.setnchannels(2)
                output.setsampwidth(2)
                output.setframerate(sr)
                output.writeframes((signal * 32767).astype("<i2").tobytes())
        results = []
        for repeat in range(args.repeats):
            for load in (False, True):
                for mode in ("legacy", "optimized-thread", "isolated"):
                    result = measure(path, mode, load)
                    result["repeat"] = repeat
                    results.append(result)
                    print(json.dumps(result), flush=True)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(results, indent=2), encoding="utf-8")
        if len({result["output_sha256"] for result in results}) != 1:
            raise RuntimeError("Output differs between benchmark modes")


if __name__ == "__main__":
    main()
