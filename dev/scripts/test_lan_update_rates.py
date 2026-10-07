"""LAN-only changing-frame benchmark; stop DreamSync/DreamView before running.

Powers on configured LAN lights at 30% brightness; does not restore prior state.
Measures host UDP sends, NOT device delivery or visible latency. No BLE is used.
"""
from __future__ import annotations

import argparse
import colorsys
import ipaddress
import json
import math
from pathlib import Path
import socket
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from dreamsync.output.govee_lan import (
    GoveeLanAdapter, GoveeLanConfig, TransportMode, build_command_json,
    build_ptreal_json, build_ptreal_segment_packets, build_razer_json,
    build_razer_packet, build_razer_activate_packet,
)


def positive(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("must be finite and positive")
    return value


def load_targets(path):
    import yaml
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    targets, seen = [], set()
    for entry in document["devices"]:
        if not entry.get("enabled", True):
            continue
        kind = entry.get("type", "auto")
        address = str(entry["address"])
        if kind == "ble" or (kind == "auto" and ":" in address):
            continue
        if kind not in {"lan", "auto"}:
            continue
        ipaddress.IPv4Address(address)
        if address in seen:
            raise ValueError(f"Duplicate LAN address: {address}")
        seen.add(address)
        transport = TransportMode(entry.get("transport", "razer"))
        segments = int(entry.get("segments", 15))
        limit = 56 if transport == TransportMode.PTREAL else 255
        if not 1 <= segments <= limit:
            raise ValueError(f"Invalid segment count for {address}: {segments}")
        targets.append(dict(name=entry.get("name", address), address=address,
                            segments=segments, transport=transport.value))
    if not targets:
        raise ValueError("No enabled LAN devices in config")
    return targets


def frame_colors(segments, index, pattern):
    # A different hue each frame also makes wraparound distinguishable.
    color = tuple(round(c * 255) for c in colorsys.hsv_to_rgb((index * 0.037) % 1, 1, 1))
    if pattern == "solid":
        return [color] * segments
    return [color if n == index % segments else (0, 0, 0) for n in range(segments)]


def encode_frame(target, colors):
    if target["transport"] == "razer":
        return build_razer_json(build_razer_packet(colors))
    if target["transport"] == "ptreal":
        return build_ptreal_json(build_ptreal_segment_packets(colors))
    r, g, b = colors[len(colors) // 2]
    return build_command_json("colorwc", {"color": dict(r=r, g=g, b=b), "colorTemInKelvin": 0})


def percentile95(values):
    return sorted(values)[max(0, math.ceil(len(values) * .95) - 1)] if values else None


def run_stage(targets, rate, seconds, pattern, *, clock=time.monotonic,
              sleep=time.sleep, socket_factory=socket.socket):
    samples = {t["address"]: [] for t in targets}
    errors = {t["address"]: [] for t in targets}
    activated = []
    with socket_factory(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        def send(payload, ip, port):
            sock.sendto(payload, (ip, port))

        try:
            for target in targets:
                adapter = GoveeLanAdapter(GoveeLanConfig(
                    device_ip=target["address"], segments=target["segments"],
                    transport=TransportMode(target["transport"])), transport=send)
                activated.append(target)
                adapter.turn_on()
                sleep(.8)
                adapter.set_brightness(30)
                sleep(.3)

            started = clock()
            index, skipped = 0, 0
            while True:
                deadline = started + index / rate
                if deadline >= started + seconds:
                    break
                sleep(max(0, deadline - clock()))
                now = clock()
                if now >= started + seconds:
                    break
                # Drop missed slots; never burst stale frames to catch up.
                due = max(index, int((now - started) * rate))
                skipped += due - index
                index = due
                deadline = started + index / rate
                for target in targets:
                    ip = target["address"]
                    payload = encode_frame(target, frame_colors(target["segments"], index, pattern))
                    before = clock()
                    try:
                        send(payload, ip, 4003)
                    except OSError as exc:
                        errors[ip].append(str(exc))
                    else:
                        after = clock()
                        samples[ip].append((before, (after - before) * 1000,
                                            max(0, before - deadline) * 1000, len(payload)))
                index += 1
            # Keep the measurement window consistent, including the last interval.
            sleep(max(0, started + seconds - clock()))
            elapsed = clock() - started
        finally:
            # Release streaming mode without turning off the lights.
            for target in activated:
                if target["transport"] == "razer":
                    try:
                        send(build_razer_json(build_razer_activate_packet(False)), target["address"], 4003)
                    except OSError:
                        pass

    rows = []
    for target in targets:
        values = samples[target["address"]]
        gaps = [(b[0] - a[0]) * 1000 for a, b in zip(values, values[1:])]
        durations = [v[1] for v in values]
        rows.append({**target, "requested_hz": rate, "pattern": pattern,
                     "elapsed_seconds": elapsed, "frames_sent": len(values),
                     "observed_send_hz": len(values) / elapsed,
                     "send_ms_median": statistics.median(durations) if durations else None,
                     "send_ms_p95": percentile95(durations),
                     "gap_ms_p95": percentile95(gaps), "gap_ms_max": max(gaps) if gaps else None,
                     "deadline_lateness_ms_p95": percentile95([v[2] for v in values]),
                     "skipped_frame_slots": skipped, "udp_bytes_sent": sum(v[3] for v in values),
                     "send_errors": len(errors[target["address"]]),
                     "error_samples": errors[target["address"]][:10]})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("dev/devices.yaml"))
    parser.add_argument("--rates", nargs="+", type=positive, default=[5, 10, 20, 30])
    parser.add_argument("--seconds", type=positive, default=30)
    parser.add_argument("--pattern", choices=["walk", "solid"], default="walk")
    parser.add_argument("--mode", choices=["individual", "together", "both"], default="both")
    parser.add_argument("--output", type=Path, default=Path("lan-rate-results.json"))
    args = parser.parse_args()
    if max(args.rates) > 60:
        parser.error("rates must be at most 60 Hz")
    try:
        targets = load_targets(args.config)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    if args.pattern == "walk" and any(t["transport"] == "colorwc" for t in targets):
        parser.error("colorwc cannot walk segments; use --pattern solid")
    report = {"measurement": "host UDP send timing; not delivery or visible latency",
              "pacing": "absolute deadlines; missed frames skipped; persistent UDP socket",
              "seconds_per_stage": args.seconds, "results": [], "failures": []}
    def save():
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    save()  # Fail before touching lights if the output path is unwritable.
    groups = []
    if args.mode in {"individual", "both"}:
        groups.extend(("individual", [target]) for target in targets)
    if args.mode in {"together", "both"}:
        groups.append(("together", targets))
    failed = False
    print(__doc__, flush=True)
    try:
        for phase, group in groups:
            for rate in args.rates:
                print(f"{phase}: {', '.join(t['name'] for t in group)}; "
                      f"{rate:g} Hz, {args.seconds:g}s, {args.pattern}", flush=True)
                try:
                    rows = run_stage(group, rate, args.seconds, args.pattern)
                    for row in rows:
                        row["phase"] = phase
                    report["results"].extend(rows)
                    print(json.dumps(rows, indent=2), flush=True)
                    stage_failed = any(r["send_errors"] or not r["frames_sent"] for r in rows)
                except Exception as exc:
                    report["failures"].append({"phase": phase, "requested_hz": rate,
                                               "addresses": [t["address"] for t in group],
                                               "error": str(exc)})
                    stage_failed = True
                save()
                if stage_failed:
                    failed = True
                    break
    except KeyboardInterrupt:
        report["interrupted"] = True
        failed = True
    finally:
        save()
    print(f"Results saved to {args.output}", flush=True)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
