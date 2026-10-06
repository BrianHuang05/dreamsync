"""Exercise the real BLE adapter; run with --help for usage.

Stop DreamSync output and close Govee Home first. This powers on the selected
lights and cycles colors at 30% brightness. Lights may retain the final color.
Measures host-side writes without response, NOT visible/device-confirmed latency.
"""

from __future__ import annotations

import argparse
import asyncio
import colorsys
from dataclasses import replace
import ipaddress
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

from dreamsync.output.govee_ble import (
    BleProtocol, GoveeBleAdapter, GoveeBleConfig, build_ble_keepalive_packet,
)
from dreamsync.output.auto_detect import load_device_config


class MeasuredAdapter(GoveeBleAdapter):
    def __init__(self, config, session_variant="baseline"):
        super().__init__(config)
        self.session_variant = session_variant
        self.samples = []
        self.errors = 0
        self.metrics_lock = threading.Lock()
        self.notifications = []
        self.queries = []
        self.session_setups = []
        self._session_client = None
        self._next_query_at = 0.0

    def _on_notification(self, sender, data):
        with self.metrics_lock:
            self.notifications.append((time.monotonic(), bytes(data).hex()))

    async def _ble_write(self, client, data):
        # Experimental session behavior lives in this hardware test only. Use
        # the real adapter's connection, reconnect, coalescing and pacing code.
        if client is not self._session_client:
            self._session_client = client
            subscribed = False
            error = None
            try:
                if self.session_variant != "baseline":
                    await asyncio.wait_for(client.start_notify(
                        "00010203-0405-0607-0809-0a0b0c0d2b10",
                        self._on_notification,
                    ), timeout=self.config.connect_timeout)
                    subscribed = True
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                with self.metrics_lock:
                    self.session_setups.append({
                        "at": time.monotonic(), "subscribed": subscribed, "error": error,
                    })
            self._next_query_at = time.monotonic() + 2.0

        # Check on every write, independently of color activity. A single
        # writer preserves ordering and prevents query/color write races.
        # At 5 Hz the query deadline is serviced on the next frame (~200 ms).
        query = build_ble_keepalive_packet()
        if (self.session_variant == "notify-query" and data != query
                and time.monotonic() >= self._next_query_at):
            await self._record_write(client, query)
            self._next_query_at = time.monotonic() + 2.0
        await self._record_write(client, data)
        if data == query:
            self._next_query_at = time.monotonic() + 2.0

    async def _record_write(self, client, data):
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
        elif data == build_ble_keepalive_packet():
            with self.metrics_lock:
                self.queries.append(time.monotonic())

    def session_report(self, began, ended):
        with self.metrics_lock:
            notifications = [(t, value) for t, value in self.notifications if began <= t <= ended]
            return {
                "session_variant": self.session_variant,
                "status_queries": sum(began <= t <= ended for t in self.queries),
                "notifications_received": len(notifications),
                "notification_samples": [
                    {"elapsed_seconds": t - began, "hex": value}
                    for t, value in notifications[:20]
                ],
                # Includes warmup/setup; negative offsets precede measurement.
                "session_setups": [dict(s, at=s["at"] - began) for s in self.session_setups],
                "session_setup_errors": sum(s["error"] is not None for s in self.session_setups),
            }


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return number


def p95(values):
    return sorted(values)[max(0, math.ceil(len(values) * 0.95) - 1)] if values else None


def load_targets(args):
    """Load enabled BLE entries, retaining each device's protocol and segments."""
    if not args.config:
        if not args.protocol:
            raise ValueError("--protocol is required with --address")
        return [GoveeBleConfig(address=address, protocol=BleProtocol(args.protocol),
                               segments=args.segments)
                for address in dict.fromkeys(a.upper() for a in args.address)]
    targets = []
    seen = set()
    configs = load_device_config(args.config)
    import yaml
    raw_entries = yaml.safe_load(args.config.read_text(encoding="utf-8"))["devices"]
    for cfg, raw in zip(configs, raw_entries):
        if not bool(raw.get("enabled", True)):
            print(f"Skipping disabled device: {cfg.name}", flush=True)
            continue
        kind = cfg.type
        if kind == "auto":
            try:
                ipaddress.ip_address(cfg.address)
                kind = "lan"
            except ValueError:
                kind = "ble"
        if kind != "ble":
            print(f"Skipping {kind.upper()} device: {cfg.name}", flush=True)
            continue
        address = cfg.address.upper()
        if address in seen:
            raise ValueError(f"Duplicate BLE address in config: {address}")
        seen.add(address)
        if not 1 <= cfg.segments <= 56:
            raise ValueError(f"Invalid segment count for {cfg.name}: {cfg.segments}")
        targets.append(GoveeBleConfig(
            address=address, name=cfg.name, protocol=BleProtocol(cfg.protocol or "segment"),
            segments=cfg.segments,
        ))
    if not targets:
        raise ValueError("Config contains no enabled BLE devices")
    return targets


def run_stage(args, rate, targets):
    adapters = [MeasuredAdapter(replace(target, max_fps=rate),
                                getattr(args, "session_variant", "baseline")) for target in targets]
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
        disconnected = {target.address: 0 for target in targets}
        previously_connected = {target.address: True for target in targets}
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
                "name": adapter.config.name, "protocol": adapter.config.protocol.value,
                "address": adapter.config.address, "requested_hz": rate,
                "observed_color_writes_hz": len(samples) / (ended - began),
                "color_writes": len(samples),
                "write_ms_median": statistics.median(durations) if durations else None,
                "write_ms_p95": p95(durations),
                "gap_ms_p95": p95(gaps), "gap_ms_max": max(gaps) if gaps else None,
                "write_errors": errors,
                "observed_disconnects": disconnected[adapter.config.address],
                "connected_at_end": adapter.connected,
                **adapter.session_report(began, ended),
            })
        return results
    finally:
        for adapter in adapters:
            adapter.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--config", type=Path, help="Device YAML; tests enabled BLE entries")
    source.add_argument("--address", action="append",
                        help="BLE MAC address; repeat to test several lights concurrently")
    parser.add_argument("--protocol", choices=["bulb", "segment"],
                        help="All selected devices must use this protocol")
    parser.add_argument("--segments", type=int, default=15)
    parser.add_argument("--rates", nargs="+", type=positive, default=[5, 10, 15, 20])
    parser.add_argument("--seconds", type=positive, default=30)
    parser.add_argument("--session-variants", nargs="+",
                        choices=["baseline", "notify", "notify-query"], default=["baseline"],
                        help="Compare unchanged stream, notifications, and notifications + 2s status queries")
    parser.add_argument("--mode", choices=["individual", "together", "both"], default=None,
                        help="Default: both with --config, together with --address")
    parser.add_argument("--output", type=Path, default=Path("ble-rate-results.json"))
    args = parser.parse_args()
    if not 1 <= args.segments <= 56:
        parser.error("--segments must be between 1 and 56")
    if any(rate > 30 for rate in args.rates):
        parser.error("test rates must be at most 30 Hz")
    if args.config and args.protocol:
        parser.error("--config uses each device's protocol; omit --protocol")
    try:
        targets = load_targets(args)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    mode = args.mode or ("both" if args.config else "together")
    groups = []
    if mode in {"individual", "both"}:
        groups.extend(("individual", [target]) for target in targets)
    if mode in {"together", "both"}:
        groups.append(("together", targets))
    logging.basicConfig(level=logging.WARNING)
    report = {"measurement": "host writes without response; not visible latency",
              "seconds_per_stage": args.seconds,
              "session_variants": args.session_variants,
              "workload": "one solid-color packet per frame, fixed brightness",
              "results": [], "failures": []}
    exit_code = 0
    print(__doc__, flush=True)
    try:
        for phase, group, variant in (
            (phase, group, variant) for phase, group in groups
            for variant in args.session_variants
        ):
            args.session_variant = variant
            for rate in args.rates:
                print(f"Testing {phase}/{variant}: {', '.join(t.name or t.address for t in group)} "
                      f"at {rate:g} Hz for {args.seconds:g} seconds...", flush=True)
                failed = False
                try:
                    rows = run_stage(args, rate, group)
                    for row in rows:
                        row["phase"] = phase
                        row["session_variant"] = variant
                    report["results"].extend(rows)
                    print(json.dumps(rows, indent=2), flush=True)
                    failed = any(r["write_errors"] or r["observed_disconnects"] or
                                 r.get("session_setup_errors", 0) or
                                 not r["connected_at_end"] or not r["color_writes"] for r in rows)
                except Exception as exc:
                    failed = True
                    report["failures"].append({
                        "phase": phase, "addresses": [t.address for t in group],
                        "session_variant": variant,
                        "requested_hz": rate, "error": str(exc),
                    })
                    print(f"Stage failed: {exc}", flush=True)
                args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
                if failed:
                    print("Skipping higher rates for this variant/group; continuing sweep.", flush=True)
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
