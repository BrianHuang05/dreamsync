"""Exercise the real BLE adapter; run with --help for usage.

Stop DreamSync output and close Govee Home first. This powers on the selected
lights and cycles colors at 30% brightness. Lights may retain the final color.
Measures host-side writes without response, NOT visible/device-confirmed latency.
"""

from __future__ import annotations

import argparse
import colorsys
import json
import logging
import math
from pathlib import Path
import statistics
import sys
import threading
import time

# Use this checkout's updated adapter even without an editable installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from dreamsync.output.govee_ble import BleProtocol, GoveeBleAdapter, GoveeBleConfig


class MeasuredAdapter(GoveeBleAdapter):
    def __init__(self, config):
        super().__init__(config)
        self.samples = []
        self.errors = 0
        self.metrics_lock = threading.Lock()

    async def _ble_write(self, client, data):
        started = time.monotonic()
        try:
            await super()._ble_write(client, data)
        except Exception:
            with self.metrics_lock:
                self.errors += 1
            raise
        if data[:2] == bytes([0x33, 0x05]):
            with self.metrics_lock:
                self.samples.append((time.monotonic(), (time.monotonic() - started) * 1000))


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return number


def p95(values):
    return sorted(values)[max(0, math.ceil(len(values) * 0.95) - 1)] if values else None


def run_stage(args, rate):
    adapters = [MeasuredAdapter(GoveeBleConfig(
        address=address, protocol=BleProtocol(args.protocol),
        segments=args.segments, max_fps=rate,
    )) for address in args.address]
    try:
        for adapter in adapters:
            adapter.start()
        # Initial discovery is shared and connection setup is serialized. Allow
        # the whole fleet to finish setup instead of timing out a queued bulb.
        ready_deadline = time.monotonic() + 10 + sum(
            adapter.config.connect_timeout + 1 for adapter in adapters
        )
        for adapter in adapters:
            if not adapter.wait_until_connected(max(0, ready_deadline - time.monotonic())):
                raise RuntimeError(f"Could not connect to {adapter.config.address}")

        # Warm up brightness and sending before recording this stage.
        for adapter in adapters:
            adapter.send_color(128, 0, 0, 30)
        time.sleep(1)
        baseline_errors = {}
        for adapter in adapters:
            with adapter.metrics_lock:
                baseline_errors[adapter.config.address] = adapter.errors
        began = time.monotonic()
        next_frame = began
        disconnected = {address: 0 for address in args.address}
        previously_connected = {address: True for address in args.address}
        while time.monotonic() - began < args.seconds:
            now = time.monotonic()
            rgb = tuple(round(v * 255) for v in colorsys.hsv_to_rgb(
                ((now - began) / 4) % 1, 1, 0.7,
            ))
            for adapter in adapters:
                address = adapter.config.address
                connected = adapter.connected
                if previously_connected[address] and not connected:
                    disconnected[address] += 1
                previously_connected[address] = connected
                adapter.send_color(*rgb, brightness=30)
            next_frame += 1 / 120  # intentionally faster than BLE output
            time.sleep(max(0, next_frame - time.monotonic()))
            next_frame = max(next_frame, time.monotonic())
        ended = time.monotonic()
        results = []
        for adapter in adapters:
            with adapter.metrics_lock:
                samples = [(t, ms) for t, ms in adapter.samples if began <= t <= ended]
                errors = adapter.errors - baseline_errors[adapter.config.address]
            gaps = [(b[0] - a[0]) * 1000 for a, b in zip(samples, samples[1:])]
            durations = [ms for _, ms in samples]
            results.append({
                "address": adapter.config.address, "requested_hz": rate,
                "observed_color_writes_hz": len(samples) / (ended - began),
                "color_writes": len(samples),
                "write_ms_median": statistics.median(durations) if durations else None,
                "write_ms_p95": p95(durations),
                "gap_ms_p95": p95(gaps), "gap_ms_max": max(gaps) if gaps else None,
                "write_errors": errors,
                "observed_disconnects": disconnected[adapter.config.address],
                "connected_at_end": adapter.connected,
            })
        return results
    finally:
        for adapter in adapters:
            adapter.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", action="append", required=True,
                        help="BLE MAC address; repeat to test several lights concurrently")
    parser.add_argument("--protocol", choices=["bulb", "segment"], required=True,
                        help="All selected devices must use this protocol")
    parser.add_argument("--segments", type=int, default=15)
    parser.add_argument("--rates", nargs="+", type=positive, default=[5, 10, 15, 20])
    parser.add_argument("--seconds", type=positive, default=30)
    parser.add_argument("--output", type=Path, default=Path("ble-rate-results.json"))
    args = parser.parse_args()
    if not 1 <= args.segments <= 56:
        parser.error("--segments must be between 1 and 56")
    if any(rate > 30 for rate in args.rates):
        parser.error("test rates must be at most 30 Hz")
    args.address = list(dict.fromkeys(args.address))
    logging.basicConfig(level=logging.WARNING)
    report = {"measurement": "host writes without response; not visible latency",
              "workload": "one solid-color packet per frame, fixed brightness",
              "results": []}
    exit_code = 0
    print(__doc__, flush=True)
    try:
        for rate in args.rates:
            print(f"Testing {rate:g} Hz for {args.seconds:g} seconds...", flush=True)
            rows = run_stage(args, rate)
            report["results"].extend(rows)
            print(json.dumps(rows, indent=2), flush=True)
            if any(r["write_errors"] or r["observed_disconnects"] or
                   not r["connected_at_end"] or not r["color_writes"] for r in rows):
                print("Stopping rate sweep after transport failure.", flush=True)
                exit_code = 1
                break
    except (RuntimeError, KeyboardInterrupt) as exc:
        report["error"] = str(exc) or "Interrupted"
        exit_code = 1
    finally:
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"Results: {args.output}", flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
