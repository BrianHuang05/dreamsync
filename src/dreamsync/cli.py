import argparse
import collections
import json
from pathlib import Path

from .audio.system_input import list_input_devices
from .director import EffectMode, LightingIntent
from .basic_controller import BeatFlashConfig
from .live import run_live_beat_flash_to_ledfx, run_live_input_to_ledfx
from .output.ledfx import LedFxConfig, LedFxOutputAdapter
from .pipeline import capture_system_input_features_to_stream, extract_wav_features_to_stream
from .replay import replay_wav_to_fake_output, replay_wav_to_ledfx_output
from .visualize import render_feature_plot


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
    ledfx_test.add_argument("--virtual-id", required=True, help="LedFx virtual id.")
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
    ledfx_replay.add_argument("--virtual-id", required=True, help="LedFx virtual id.")
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
    ledfx_live.add_argument("--virtual-id", required=True, help="LedFx virtual id.")
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

    ledfx_beat = sub.add_parser(
        "ledfx-beat",
        help="Capture live input and flash lights on detected beats (prints beat timing).",
    )
    ledfx_beat.add_argument("--duration", type=float, required=True, help="Capture duration in seconds.")
    ledfx_beat.add_argument("--base-url", required=True, help="LedFx base URL, e.g. http://127.0.0.1:8888")
    ledfx_beat.add_argument("--virtual-id", required=True, help="LedFx virtual id.")
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
        adapter = LedFxOutputAdapter(LedFxConfig(base_url=args.base_url, virtual_id=args.virtual_id))
        sent = adapter.emit(0.0, intent)
        print(json.dumps({"sent": sent, "mode": intent.mode.value}, separators=(",", ":")))
        return 0

    if args.command == "ledfx-replay":
        adapter = LedFxOutputAdapter(
            LedFxConfig(
                base_url=args.base_url,
                virtual_id=args.virtual_id,
                min_update_interval_seconds=max(0.0, float(args.min_interval)),
                timeout_seconds=max(0.1, float(args.timeout_seconds)),
            )
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
        adapter = LedFxOutputAdapter(
            LedFxConfig(
                base_url=args.base_url,
                virtual_id=args.virtual_id,
                min_update_interval_seconds=max(0.0, float(args.min_interval)),
                timeout_seconds=max(0.1, float(args.timeout_seconds)),
            )
        )
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
        adapter = LedFxOutputAdapter(
            LedFxConfig(
                base_url=args.base_url,
                virtual_id=args.virtual_id,
                min_update_interval_seconds=max(0.0, float(args.min_interval)),
                timeout_seconds=max(0.1, float(args.timeout_seconds)),
            )
        )
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

    parser.print_help()
    return 0
