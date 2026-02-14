import argparse
import collections
import json
import logging
from pathlib import Path

from .audio.system_input import list_input_devices
from .director import EffectMode, LightingIntent
from .basic_controller import BeatFlashConfig, BeatRippleConfig
from .live import run_live_beat_flash_to_ledfx, run_live_beat_ripple_to_ledfx, run_live_input_to_ledfx
from .output.ledfx import LedFxConfig, LedFxOutputAdapter, MultiLedFxOutputAdapter
from .output.roles import DeviceRole
from .pipeline import capture_system_input_features_to_stream, extract_wav_features_to_stream
from .replay import replay_wav_to_fake_output, replay_wav_to_ledfx_output
from .visualize import render_feature_plot


def _parse_virtual_id(spec: str) -> tuple[str, DeviceRole]:
    """Parse 'ID' or 'ID:ROLE' into (virtual_id, DeviceRole)."""
    if ":" in spec:
        vid, role_str = spec.rsplit(":", 1)
        return vid, DeviceRole(role_str)
    return spec, DeviceRole.PRIMARY


def _build_adapter(
    args: argparse.Namespace,
    *,
    min_interval: float | None = None,
    timeout_seconds: float | None = None,
    debug: bool = False,
    effect_type_override: str | None = None,
) -> LedFxOutputAdapter | MultiLedFxOutputAdapter:
    """Build a single or multi-device adapter from --virtual-id args."""
    parsed = [_parse_virtual_id(spec) for spec in args.virtual_id]
    interval = min_interval if min_interval is not None else 0.3
    timeout = timeout_seconds if timeout_seconds is not None else 3.0

    devices: list[tuple[LedFxOutputAdapter, DeviceRole]] = []
    for vid, role in parsed:
        adapter = LedFxOutputAdapter(
            LedFxConfig(
                base_url=args.base_url,
                virtual_id=vid,
                min_update_interval_seconds=interval,
                timeout_seconds=timeout,
                debug=debug,
                effect_type_override=effect_type_override,
            )
        )
        devices.append((adapter, role))

    if len(devices) == 1:
        return devices[0][0]
    return MultiLedFxOutputAdapter(devices)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dreamsync",
        description="Local audio-reactive control for Govee DreamView/LAN devices.",
    )
    parser.add_argument("--version", action="store_true", help="Print version and exit.")

    sub = parser.add_subparsers(dest="command")

    wav = sub.add_parser("wav", help="Run offline WAV feature extraction.")
    wav.add_argument("path", type=Path, help="Path to a WAV file.")
    wav.add_argument(
        "--jsonl",
        type=Path,
        default=None,
        help="Write JSON Lines output to this path (defaults to stdout).",
    )
    wav.add_argument("--frame-size", type=int, default=2048, help="Frame size in samples.")
    wav.add_argument("--hop-size", type=int, default=512, help="Hop size in samples.")

    plot = sub.add_parser("plot", help="Plot a feature JSONL stream to PNG.")
    plot.add_argument("input_jsonl", type=Path, help="Path to feature JSONL.")
    plot.add_argument("output_png", type=Path, help="Output PNG path.")

    replay = sub.add_parser("replay", help="Replay WAV through Director -> FakeOutput.")
    replay.add_argument("path", type=Path, help="Path to WAV file.")
    replay.add_argument("--frame-size", type=int, default=2048, help="Frame size in samples.")
    replay.add_argument("--hop-size", type=int, default=512, help="Hop size in samples.")
    replay.add_argument(
        "--jsonl",
        type=Path,
        default=None,
        help="Write replay intent logs to JSONL path (defaults to stdout).",
    )

    ledfx_test = sub.add_parser("ledfx-test", help="Send one test intent to LedFx.")
    ledfx_test.add_argument("--base-url", required=True, help="LedFx base URL, e.g. http://127.0.0.1:8888")
    ledfx_test.add_argument(
        "--virtual-id",
        action="append",
        required=True,
        metavar="ID[:ROLE]",
        help="LedFx virtual id (optionally with :primary or :accent role). Repeatable.",
    )
    ledfx_test.add_argument(
        "--mode",
        choices=["ambient", "pulse", "motion"],
        default="pulse",
        help="Intent mode to send.",
    )
    ledfx_test.add_argument("--intensity", type=float, default=0.4, help="Intent intensity (0-1).")
    ledfx_test.add_argument("--speed", type=float, default=0.6, help="Intent speed (0-1).")
    ledfx_test.add_argument("--bpm", type=float, default=120.0, help="Intent BPM hint.")

    ledfx_replay = sub.add_parser(
        "ledfx-replay",
        help="Replay WAV through Director -> LedFx and emit run logs.",
    )
    ledfx_replay.add_argument("path", type=Path, help="Path to WAV file.")
    ledfx_replay.add_argument("--base-url", required=True, help="LedFx base URL, e.g. http://127.0.0.1:8888")
    ledfx_replay.add_argument(
        "--virtual-id",
        action="append",
        required=True,
        metavar="ID[:ROLE]",
        help="LedFx virtual id (optionally with :primary or :accent role). Repeatable.",
    )
    ledfx_replay.add_argument("--frame-size", type=int, default=2048, help="Frame size in samples.")
    ledfx_replay.add_argument("--hop-size", type=int, default=512, help="Hop size in samples.")
    ledfx_replay.add_argument(
        "--realtime",
        action="store_true",
        help="Replay events in source timing order (sleeps between frames).",
    )
    ledfx_replay.add_argument(
        "--max-events",
        type=int,
        default=None,
        help="Optional cap on number of feature frames to replay.",
    )
    ledfx_replay.add_argument(
        "--min-interval",
        type=float,
        default=0.3,
        help="Minimum interval between LedFx API writes.",
    )
    ledfx_replay.add_argument(
        "--timeout-seconds",
        type=float,
        default=3.0,
        help="HTTP timeout for LedFx API requests.",
    )
    ledfx_replay.add_argument(
        "--jsonl",
        type=Path,
        default=None,
        help="Write replay run logs to JSONL path (defaults to stdout).",
    )

    ledfx_live = sub.add_parser(
        "ledfx-live",
        help="Capture live input and stream Director-driven intents to LedFx.",
    )
    ledfx_live.add_argument("--duration", type=float, required=True, help="Capture duration in seconds.")
    ledfx_live.add_argument("--base-url", required=True, help="LedFx base URL, e.g. http://127.0.0.1:8888")
    ledfx_live.add_argument(
        "--virtual-id",
        action="append",
        required=True,
        metavar="ID[:ROLE]",
        help="LedFx virtual id (optionally with :primary or :accent role). Repeatable.",
    )
    ledfx_live.add_argument("--sample-rate", type=int, default=44100, help="Input sample rate.")
    ledfx_live.add_argument("--channels", type=int, default=1, help="Input channel count.")
    ledfx_live.add_argument("--device", type=int, default=None, help="Optional input device id.")
    ledfx_live.add_argument("--frame-size", type=int, default=2048, help="Frame size in samples.")
    ledfx_live.add_argument("--hop-size", type=int, default=512, help="Hop size in samples.")
    ledfx_live.add_argument("--blocksize", type=int, default=1024, help="PortAudio callback blocksize.")
    ledfx_live.add_argument(
        "--heartbeat-seconds",
        type=float,
        default=1.0,
        help="Telemetry heartbeat period during live capture.",
    )
    ledfx_live.add_argument(
        "--min-interval",
        type=float,
        default=0.3,
        help="Minimum interval between LedFx API writes.",
    )
    ledfx_live.add_argument(
        "--timeout-seconds",
        type=float,
        default=3.0,
        help="HTTP timeout for LedFx API requests.",
    )
    ledfx_live.add_argument(
        "--jsonl",
        type=Path,
        default=None,
        help="Write live run logs to JSONL path (defaults to stdout).",
    )
    ledfx_live.add_argument(
        "--force-stop",
        dest="force_stop",
        action="store_true",
        default=True,
        help="Clear any existing LedFx effect on the virtual before starting (default).",
    )
    ledfx_live.add_argument(
        "--no-force-stop",
        dest="force_stop",
        action="store_false",
        help="Do not clear the LedFx effect before starting.",
    )
    ledfx_live.add_argument(
        "--debug-ledfx",
        action="store_true",
        help="Log LedFx payloads and control requests.",
    )

    ledfx_beat = sub.add_parser(
        "ledfx-beat",
        help="Capture live input and flash lights on detected beats (prints beat timing).",
    )
    ledfx_beat.add_argument("--duration", type=float, required=True, help="Capture duration in seconds.")
    ledfx_beat.add_argument("--base-url", required=True, help="LedFx base URL, e.g. http://127.0.0.1:8888")
    ledfx_beat.add_argument(
        "--virtual-id",
        action="append",
        required=True,
        metavar="ID[:ROLE]",
        help="LedFx virtual id (optionally with :primary or :accent role). Repeatable.",
    )
    ledfx_beat.add_argument("--sample-rate", type=int, default=44100, help="Input sample rate.")
    ledfx_beat.add_argument("--channels", type=int, default=1, help="Input channel count.")
    ledfx_beat.add_argument("--device", type=int, default=None, help="Optional input device id.")
    ledfx_beat.add_argument("--frame-size", type=int, default=2048, help="Frame size in samples.")
    ledfx_beat.add_argument("--hop-size", type=int, default=512, help="Hop size in samples.")
    ledfx_beat.add_argument("--blocksize", type=int, default=1024, help="PortAudio callback blocksize.")
    ledfx_beat.add_argument(
        "--heartbeat-seconds",
        type=float,
        default=1.0,
        help="Telemetry heartbeat period during live capture.",
    )
    ledfx_beat.add_argument(
        "--min-interval",
        type=float,
        default=0.05,
        help="Minimum interval between LedFx API writes.",
    )
    ledfx_beat.add_argument(
        "--timeout-seconds",
        type=float,
        default=3.0,
        help="HTTP timeout for LedFx API requests.",
    )
    ledfx_beat.add_argument(
        "--flash-duration",
        type=float,
        default=0.08,
        help="Flash duration in seconds.",
    )
    ledfx_beat.add_argument(
        "--flash-intensity",
        type=float,
        default=1.0,
        help="Brightness during beat flashes (0-1).",
    )
    ledfx_beat.add_argument(
        "--idle-intensity",
        type=float,
        default=0.05,
        help="Brightness between beats (0-1).",
    )
    ledfx_beat.add_argument(
        "--min-flash-interval",
        type=float,
        default=0.2,
        help="Minimum seconds between beat flashes.",
    )
    ledfx_beat.add_argument(
        "--jsonl",
        type=Path,
        default=None,
        help="Write beat run logs to JSONL path (defaults to stdout).",
    )
    ledfx_beat.add_argument(
        "--force-stop",
        dest="force_stop",
        action="store_true",
        default=True,
        help="Clear any existing LedFx effect on the virtual before starting (default).",
    )
    ledfx_beat.add_argument(
        "--no-force-stop",
        dest="force_stop",
        action="store_false",
        help="Do not clear the LedFx effect before starting.",
    )
    ledfx_beat.add_argument(
        "--debug-ledfx",
        action="store_true",
        help="Log LedFx payloads and control requests.",
    )

    ledfx_ripple = sub.add_parser(
        "ledfx-ripple",
        help="Capture live input and smooth-ripple lights on detected beats.",
    )
    ledfx_ripple.add_argument("--duration", type=float, required=True, help="Capture duration in seconds.")
    ledfx_ripple.add_argument("--base-url", required=True, help="LedFx base URL, e.g. http://127.0.0.1:8888")
    ledfx_ripple.add_argument(
        "--virtual-id",
        action="append",
        required=True,
        metavar="ID[:ROLE]",
        help="LedFx virtual id (optionally with :primary or :accent role). Repeatable.",
    )
    ledfx_ripple.add_argument("--sample-rate", type=int, default=44100, help="Input sample rate.")
    ledfx_ripple.add_argument("--channels", type=int, default=1, help="Input channel count.")
    ledfx_ripple.add_argument("--device", type=int, default=None, help="Optional input device id.")
    ledfx_ripple.add_argument("--frame-size", type=int, default=2048, help="Frame size in samples.")
    ledfx_ripple.add_argument("--hop-size", type=int, default=512, help="Hop size in samples.")
    ledfx_ripple.add_argument("--blocksize", type=int, default=1024, help="PortAudio callback blocksize.")
    ledfx_ripple.add_argument(
        "--heartbeat-seconds",
        type=float,
        default=1.0,
        help="Telemetry heartbeat period during live capture.",
    )
    ledfx_ripple.add_argument(
        "--min-interval",
        type=float,
        default=0.05,
        help="Minimum interval between LedFx API writes.",
    )
    ledfx_ripple.add_argument(
        "--timeout-seconds",
        type=float,
        default=3.0,
        help="HTTP timeout for LedFx API requests.",
    )
    ledfx_ripple.add_argument(
        "--brightness",
        type=float,
        default=0.8,
        help="Constant brightness for the wave (0-1).",
    )
    ledfx_ripple.add_argument(
        "--colors",
        type=str,
        default=None,
        help="Comma-separated hex colors to cycle on each beat (e.g. '#ff0000,#00ff00,#0000ff').",
    )
    ledfx_ripple.add_argument(
        "--speed-divisor",
        type=float,
        default=480.0,
        help="Speed = BPM / divisor. Higher = slower wave.",
    )
    ledfx_ripple.add_argument(
        "--effect-type",
        type=str,
        default=None,
        help="LedFx effect type override (e.g. 'power', 'wavelength', 'bar'). Defaults to 'power'.",
    )
    ledfx_ripple.add_argument(
        "--min-beat-interval",
        type=float,
        default=0.2,
        help="Minimum seconds between beat triggers.",
    )
    ledfx_ripple.add_argument(
        "--jsonl",
        type=Path,
        default=None,
        help="Write ripple run logs to JSONL path (defaults to stdout).",
    )
    ledfx_ripple.add_argument(
        "--force-stop",
        dest="force_stop",
        action="store_true",
        default=True,
        help="Clear any existing LedFx effect on the virtual before starting (default).",
    )
    ledfx_ripple.add_argument(
        "--no-force-stop",
        dest="force_stop",
        action="store_false",
        help="Do not clear the LedFx effect before starting.",
    )
    ledfx_ripple.add_argument(
        "--debug-ledfx",
        action="store_true",
        help="Log LedFx payloads and control requests.",
    )

    sub.add_parser("devices", help="List real-time audio input devices.")

    capture = sub.add_parser("capture", help="Capture system input and emit feature JSONL.")
    capture.add_argument("--duration", type=float, required=True, help="Capture duration in seconds.")
    capture.add_argument("--sample-rate", type=int, default=44100, help="Input sample rate.")
    capture.add_argument("--channels", type=int, default=1, help="Input channel count.")
    capture.add_argument("--device", type=int, default=None, help="Optional input device id.")
    capture.add_argument("--frame-size", type=int, default=2048, help="Frame size in samples.")
    capture.add_argument("--hop-size", type=int, default=512, help="Hop size in samples.")
    capture.add_argument(
        "--heartbeat-seconds",
        type=float,
        default=5.0,
        help="Telemetry heartbeat period during capture (0 disables).",
    )
    capture.add_argument(
        "--jsonl",
        type=Path,
        default=None,
        help="Write feature stream to JSONL path (defaults to stdout).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        from . import __version__

        print(__version__)
        return 0

    if args.command == "wav":
        stream = extract_wav_features_to_stream(
            args.path, frame_size=args.frame_size, hop_size=args.hop_size
        )
        if args.jsonl:
            args.jsonl.parent.mkdir(parents=True, exist_ok=True)
            with args.jsonl.open("w", encoding="utf-8") as f:
                for item in stream:
                    f.write(json.dumps(item, separators=(",", ":")) + "\n")
        else:
            for item in stream:
                print(json.dumps(item, separators=(",", ":")))
        return 0

    if args.command == "plot":
        render_feature_plot(args.input_jsonl, args.output_png)
        print(args.output_png)
        return 0

    if args.command == "replay":
        logs = replay_wav_to_fake_output(args.path, frame_size=args.frame_size, hop_size=args.hop_size)
        if args.jsonl:
            args.jsonl.parent.mkdir(parents=True, exist_ok=True)
            with args.jsonl.open("w", encoding="utf-8") as f:
                for item in logs:
                    f.write(json.dumps(item, separators=(",", ":")) + "\n")
        else:
            for item in logs:
                print(json.dumps(item, separators=(",", ":")))
        print(json.dumps({"replay_rows": len(logs)}, separators=(",", ":")))
        return 0

    if args.command == "ledfx-test":
        mode = EffectMode(args.mode)
        intent = LightingIntent(
            mode=mode,
            intensity=max(0.0, min(1.0, float(args.intensity))),
            speed=max(0.0, min(1.0, float(args.speed))),
            bpm=max(0.0, float(args.bpm)),
        )
        adapter = _build_adapter(args)
        sent = adapter.emit(0.0, intent)
        devices = [_parse_virtual_id(s) for s in args.virtual_id]
        print(json.dumps(
            {"sent": sent, "mode": intent.mode.value, "devices": [{"id": v, "role": r.value} for v, r in devices]},
            separators=(",", ":"),
        ))
        return 0

    if args.command == "ledfx-replay":
        adapter = _build_adapter(
            args,
            min_interval=max(0.0, float(args.min_interval)),
            timeout_seconds=max(0.1, float(args.timeout_seconds)),
        )
        logs = replay_wav_to_ledfx_output(
            args.path,
            adapter,
            frame_size=args.frame_size,
            hop_size=args.hop_size,
            realtime=args.realtime,
            max_events=args.max_events,
        )
        if args.jsonl:
            args.jsonl.parent.mkdir(parents=True, exist_ok=True)
            with args.jsonl.open("w", encoding="utf-8") as f:
                for item in logs:
                    f.write(json.dumps(item, separators=(",", ":")) + "\n")
        else:
            for item in logs:
                print(json.dumps(item, separators=(",", ":")))
        sent = sum(1 for row in logs if bool(row["sent"]))
        mode_counts = collections.Counter(str(row["mode"]) for row in logs)
        print(
            json.dumps(
                {
                    "rows": len(logs),
                    "sent": sent,
                    "mode_counts": dict(mode_counts),
                },
                separators=(",", ":"),
            )
        )
        return 0

    if args.command == "devices":
        devices = list_input_devices()
        for dev in devices:
            print(json.dumps(dev, separators=(",", ":")))
        return 0

    if args.command == "capture":
        stream, meta, telemetry_rows = capture_system_input_features_to_stream(
            duration_seconds=args.duration,
            sample_rate=args.sample_rate,
            channels=args.channels,
            device=args.device,
            frame_size=args.frame_size,
            hop_size=args.hop_size,
            telemetry_interval_seconds=args.heartbeat_seconds if args.heartbeat_seconds > 0 else None,
        )
        if args.jsonl:
            args.jsonl.parent.mkdir(parents=True, exist_ok=True)
            with args.jsonl.open("w", encoding="utf-8") as f:
                for item in telemetry_rows:
                    f.write(json.dumps(item, separators=(",", ":")) + "\n")
                for item in stream:
                    f.write(json.dumps(item, separators=(",", ":")) + "\n")
        else:
            for item in telemetry_rows:
                print(json.dumps(item, separators=(",", ":")))
            for item in stream:
                print(json.dumps(item, separators=(",", ":")))
        print(json.dumps({"capture_meta": meta}, separators=(",", ":")))
        return 0

    if args.command == "ledfx-live":
        if args.debug_ledfx:
            logging.basicConfig(level=logging.INFO)
        adapter = _build_adapter(
            args,
            min_interval=max(0.0, float(args.min_interval)),
            timeout_seconds=max(0.1, float(args.timeout_seconds)),
            debug=bool(args.debug_ledfx),
        )
        if args.force_stop:
            adapter.clear_effect()
            devices = [_parse_virtual_id(s) for s in args.virtual_id]
            print(json.dumps(
                {"force_stop": True, "devices": [{"id": v, "role": r.value} for v, r in devices]},
                separators=(",", ":"),
            ))
        logs, summary = run_live_input_to_ledfx(
            adapter=adapter,
            duration_seconds=args.duration,
            sample_rate=args.sample_rate,
            channels=args.channels,
            device=args.device,
            frame_size=args.frame_size,
            hop_size=args.hop_size,
            telemetry_interval_seconds=max(0.1, float(args.heartbeat_seconds)),
            blocksize=args.blocksize,
        )
        if args.jsonl:
            args.jsonl.parent.mkdir(parents=True, exist_ok=True)
            with args.jsonl.open("w", encoding="utf-8") as f:
                for item in logs:
                    f.write(json.dumps(item, separators=(",", ":")) + "\n")
        else:
            for item in logs:
                print(json.dumps(item, separators=(",", ":")))
        print(json.dumps(summary, separators=(",", ":")))
        return 0

    if args.command == "ledfx-beat":
        if args.debug_ledfx:
            logging.basicConfig(level=logging.INFO)
        adapter = _build_adapter(
            args,
            min_interval=max(0.0, float(args.min_interval)),
            timeout_seconds=max(0.1, float(args.timeout_seconds)),
            debug=bool(args.debug_ledfx),
        )
        if args.force_stop:
            adapter.clear_effect()
            devices = [_parse_virtual_id(s) for s in args.virtual_id]
            print(json.dumps(
                {"force_stop": True, "devices": [{"id": v, "role": r.value} for v, r in devices]},
                separators=(",", ":"),
            ))
        flash_config = BeatFlashConfig(
            flash_intensity=max(0.0, min(1.0, float(args.flash_intensity))),
            idle_intensity=max(0.0, min(1.0, float(args.idle_intensity))),
            flash_duration_seconds=max(0.01, float(args.flash_duration)),
            min_flash_interval_seconds=max(0.01, float(args.min_flash_interval)),
        )
        logs, summary = run_live_beat_flash_to_ledfx(
            adapter=adapter,
            duration_seconds=args.duration,
            sample_rate=args.sample_rate,
            channels=args.channels,
            device=args.device,
            frame_size=args.frame_size,
            hop_size=args.hop_size,
            telemetry_interval_seconds=max(0.1, float(args.heartbeat_seconds)),
            blocksize=args.blocksize,
            flash_config=flash_config,
        )
        if args.jsonl:
            args.jsonl.parent.mkdir(parents=True, exist_ok=True)
            with args.jsonl.open("w", encoding="utf-8") as f:
                for item in logs:
                    f.write(json.dumps(item, separators=(",", ":")) + "\n")
        print(json.dumps(summary, separators=(",", ":")))
        return 0

    if args.command == "ledfx-ripple":
        if args.debug_ledfx:
            logging.basicConfig(level=logging.INFO)
        adapter = _build_adapter(
            args,
            min_interval=max(0.0, float(args.min_interval)),
            timeout_seconds=max(0.1, float(args.timeout_seconds)),
            debug=bool(args.debug_ledfx),
            effect_type_override=args.effect_type,
        )
        if args.force_stop:
            adapter.clear_effect()
            devices = [_parse_virtual_id(s) for s in args.virtual_id]
            print(json.dumps(
                {"force_stop": True, "devices": [{"id": v, "role": r.value} for v, r in devices]},
                separators=(",", ":"),
            ))
        colors = None
        if args.colors:
            colors = tuple(c.strip() for c in args.colors.split(","))
        ripple_kwargs: dict = {
            "brightness": max(0.0, min(1.0, float(args.brightness))),
            "min_beat_interval_seconds": max(0.01, float(args.min_beat_interval)),
            "speed_divisor": max(1.0, float(args.speed_divisor)),
        }
        if colors:
            ripple_kwargs["colors"] = colors
        ripple_config = BeatRippleConfig(**ripple_kwargs)
        logs, summary = run_live_beat_ripple_to_ledfx(
            adapter=adapter,
            duration_seconds=args.duration,
            sample_rate=args.sample_rate,
            channels=args.channels,
            device=args.device,
            frame_size=args.frame_size,
            hop_size=args.hop_size,
            telemetry_interval_seconds=max(0.1, float(args.heartbeat_seconds)),
            blocksize=args.blocksize,
            ripple_config=ripple_config,
        )
        if args.jsonl:
            args.jsonl.parent.mkdir(parents=True, exist_ok=True)
            with args.jsonl.open("w", encoding="utf-8") as f:
                for item in logs:
                    f.write(json.dumps(item, separators=(",", ":")) + "\n")
        print(json.dumps(summary, separators=(",", ":")))
        return 0

    parser.print_help()
    return 0
