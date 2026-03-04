import argparse
import json
from pathlib import Path

from .audio.system_input import list_input_devices
from .director import DirectorConfig
from .live import run_live_to_govee
from .output.govee_lan import GoveeLanAdapter, GoveeLanConfig, MultiGoveeLanAdapter, TransportMode, parse_device_spec
from .render import RenderMode, SegmentRenderer
from .pipeline import capture_system_input_features_to_stream, extract_wav_features_to_stream
from .replay import replay_wav_to_fake_output
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
    # -- Govee LAN direct commands ----------------------------------------
    sub.add_parser("govee-scan", help="Scan for Govee devices on the local network.")

    govee_test = sub.add_parser(
        "govee-test",
        help="Send a solid color to a Govee device over LAN for N seconds.",
    )
    govee_test.add_argument("--device-ip", required=True, help="Govee device IP address.")
    govee_test.add_argument("--segments", type=int, default=15, help="Number of addressable segments.")
    govee_test.add_argument("--color", type=str, default="#ff0000", help="Hex color to display.")
    govee_test.add_argument("--duration", type=float, default=5.0, help="Duration in seconds.")
    govee_test.add_argument("--fps", type=int, default=30, help="Frame rate.")
    govee_test.add_argument("--brightness", type=float, default=1.0, help="Global brightness (0-1).")
    govee_test.add_argument(
        "--transport",
        choices=["razer", "ptreal", "colorwc"],
        default="ptreal",
        help="Transport protocol: razer (DreamView), ptreal (BLE-over-LAN per-segment), colorwc (whole-strip fallback). Default: ptreal.",
    )
    govee_test.add_argument(
        "--pattern",
        choices=["solid", "alternate", "rainbow", "walk"],
        default="solid",
        help="Test pattern: solid (one color), alternate (odd/even), rainbow (per-segment), walk (one segment at a time).",
    )

    # -- Govee BLE commands --------------------------------------------------
    ble_scan = sub.add_parser("govee-ble-scan", help="Scan for Govee BLE devices.")
    ble_scan.add_argument("--timeout", type=float, default=10.0, help="Scan duration in seconds.")

    ble_test = sub.add_parser(
        "govee-ble-test",
        help="Connect to a Govee BLE device and set a color.",
    )
    ble_test.add_argument("--address", required=True, help="BLE device address (MAC or UUID).")
    ble_test.add_argument("--color", type=str, default="#ff0000", help="Hex color to display.")
    ble_test.add_argument("--brightness", type=int, default=100, help="Brightness 0-100.")
    ble_test.add_argument("--duration", type=float, default=5.0, help="Duration in seconds.")
    ble_test.add_argument(
        "--protocol", choices=["segment", "bulb"], default="segment",
        help="BLE protocol: segment (strips, default) or bulb (H6006 family).",
    )

    govee_live = sub.add_parser(
        "govee-live",
        help="Live audio → beat detection → renderer → Govee device (no LedFx).",
    )
    govee_device_group = govee_live.add_mutually_exclusive_group(required=False)
    govee_device_group.add_argument("--device-ip", help="Govee device IP address (single device, use with --segments).")
    govee_device_group.add_argument(
        "--device",
        action="append",
        dest="govee_devices",
        metavar="IP:SEGMENTS[:ROLE[:TRANSPORT]]",
        help="Govee device spec (repeatable). TRANSPORT overrides --transport per device. Example: --device 10.0.0.1:7:primary:ptreal --device 10.0.0.2:25:primary:razer",
    )
    govee_live.add_argument("--segments", type=int, default=15, help="Number of addressable segments (used with --device-ip).")
    govee_live.add_argument(
        "--ble-device",
        action="append",
        dest="ble_devices",
        metavar="ADDRESS[:PROTOCOL]",
        help="BLE mood follower (repeatable). PROTOCOL is 'segment' (strips, default) or 'bulb' (H6006). Example: --ble-device AA:BB:CC:DD:EE:FF:bulb",
    )
    govee_live.add_argument("--duration", type=float, required=True, help="Capture duration in seconds.")
    govee_live.add_argument(
        "--render-mode",
        choices=["solid", "pulse", "scroll", "breathe"],
        default="scroll",
        help="Visual render mode.",
    )
    govee_live.add_argument("--fps", type=int, default=30, help="Frame rate.")
    govee_live.add_argument("--brightness", type=float, default=1.0, help="Global brightness (0-1).")
    govee_live.add_argument(
        "--colors",
        type=str,
        default=None,
        help="Comma-separated hex colors to cycle on each beat (e.g. '#ff0000,#00ff00,#0000ff').",
    )
    govee_live.add_argument(
        "--mirror",
        dest="mirror",
        action="store_true",
        default=True,
        help="Scroll from center outward (default).",
    )
    govee_live.add_argument(
        "--no-mirror",
        dest="mirror",
        action="store_false",
        help="Scroll left-to-right instead of center-outward.",
    )
    govee_live.add_argument("--sample-rate", type=int, default=44100, help="Input sample rate.")
    govee_live.add_argument("--channels", type=int, default=1, help="Input channel count.")
    govee_live.add_argument("--audio-device", type=int, default=None, dest="audio_device", help="Optional input device id.")
    govee_live.add_argument("--frame-size", type=int, default=2048, help="Frame size in samples.")
    govee_live.add_argument("--hop-size", type=int, default=512, help="Hop size in samples.")
    govee_live.add_argument("--blocksize", type=int, default=1024, help="PortAudio callback blocksize.")
    govee_live.add_argument(
        "--heartbeat-seconds",
        type=float,
        default=1.0,
        help="Telemetry heartbeat period during live capture.",
    )
    govee_live.add_argument(
        "--jsonl",
        type=Path,
        default=None,
        help="Write live run logs to JSONL path (defaults to stdout).",
    )
    govee_live.add_argument(
        "--transport",
        choices=["razer", "ptreal", "colorwc"],
        default="ptreal",
        help="Transport protocol: razer (DreamView), ptreal (BLE-over-LAN per-segment), colorwc (whole-strip fallback). Default: ptreal.",
    )
    govee_live.add_argument(
        "--half-time", action="store_true",
        help="Halve the detected BPM (fixes octave-doubled detection).",
    )
    govee_live.add_argument(
        "--max-brightness", action="store_true",
        help="Force all frames to full intensity (overrides Director dynamics).",
    )
    govee_live.add_argument(
        "--auto-cycle",
        dest="auto_cycle",
        action="store_true",
        default=True,
        help="Enable mood-driven effect cycling (default).",
    )
    govee_live.add_argument(
        "--no-auto-cycle",
        dest="auto_cycle",
        action="store_false",
        help="Disable effect cycling; use fixed --render-mode.",
    )
    govee_live.add_argument(
        "--cycle-interval",
        type=float,
        default=16.0,
        help="Seconds between automatic effect changes within same mood (default: 16).",
    )
    govee_live.add_argument(
        "--debug-mood",
        action="store_true",
        help="Print mood/effect transitions to stdout.",
    )
    govee_live.add_argument(
        "--telemetry-dir",
        type=Path,
        default=None,
        help="Write per-song telemetry files to this directory.",
    )
    govee_live.add_argument(
        "--crossfade-detect",
        action="store_true",
        help="Enable crossfade-aware song boundary detection (experimental).",
    )
    govee_live.add_argument(
        "--profile",
        type=str,
        default=None,
        help="Color profile name or path (e.g. 'aurora' or './my_profile.yaml').",
    )
    govee_live.add_argument(
        "--auto-profile",
        action="store_true",
        help="Automatically select a profile based on time of day.",
    )
    govee_live.add_argument(
        "--profile-rotation",
        type=str,
        default=None,
        metavar="NAME1,NAME2,...",
        help="Rotate through multiple profiles (comma-separated names/paths).",
    )
    govee_live.add_argument(
        "--rotation-interval",
        type=float,
        default=300.0,
        help="Seconds between profile rotations when using --profile-rotation (default: 300).",
    )

    # -- Session command (YAML config + auto-detect + infinite loop) ----------
    session = sub.add_parser(
        "session",
        help="Run an infinite DreamSync session from a YAML device config.",
    )
    session.add_argument("--config", type=Path, required=True, help="Path to YAML device config file.")
    session.add_argument("--sample-rate", type=int, default=44100, help="Input sample rate.")
    session.add_argument("--channels", type=int, default=1, help="Input channel count.")
    session.add_argument("--audio-device", type=int, default=None, dest="audio_device", help="Optional input device id.")
    session.add_argument("--fps", type=int, default=30, help="Frame rate.")
    session.add_argument("--brightness", type=float, default=1.0, help="Global brightness (0-1).")
    session.add_argument(
        "--mirror",
        dest="mirror",
        action="store_true",
        default=True,
        help="Scroll from center outward (default).",
    )
    session.add_argument(
        "--no-mirror",
        dest="mirror",
        action="store_false",
        help="Scroll left-to-right instead of center-outward.",
    )
    session.add_argument(
        "--render-mode",
        choices=["solid", "pulse", "scroll", "breathe"],
        default="scroll",
        help="Visual render mode.",
    )
    session.add_argument(
        "--half-time", action="store_true",
        help="Halve the detected BPM (fixes octave-doubled detection).",
    )
    session.add_argument(
        "--max-brightness", action="store_true",
        help="Force all frames to full intensity (overrides Director dynamics).",
    )
    session.add_argument(
        "--auto-cycle",
        dest="auto_cycle",
        action="store_true",
        default=True,
        help="Enable mood-driven effect cycling (default).",
    )
    session.add_argument(
        "--no-auto-cycle",
        dest="auto_cycle",
        action="store_false",
        help="Disable effect cycling; use fixed --render-mode.",
    )
    session.add_argument(
        "--cycle-interval",
        type=float,
        default=16.0,
        help="Seconds between automatic effect changes within same mood (default: 16).",
    )
    session.add_argument(
        "--debug-mood",
        action="store_true",
        help="Print mood/effect transitions to stdout.",
    )
    session.add_argument("--frame-size", type=int, default=2048, help="Frame size in samples.")
    session.add_argument("--hop-size", type=int, default=512, help="Hop size in samples.")
    session.add_argument("--blocksize", type=int, default=1024, help="PortAudio callback blocksize.")
    session.add_argument(
        "--probe-packets", type=int, default=None,
        help="Override probe packet count (default: 5 LAN, 10 BLE). Set to 100 for deep probe.",
    )
    session.add_argument("--probe-rate", type=float, default=5.0, help="Probe packet rate in Hz.")
    session.add_argument(
        "--no-parallel-probe", action="store_true", default=False,
        help="Disable parallel device probing (probe sequentially).",
    )
    session.add_argument(
        "--telemetry-dir",
        type=Path,
        default=None,
        help="Write per-song telemetry files to this directory.",
    )
    session.add_argument(
        "--hot-reload",
        dest="hot_reload",
        action="store_true",
        default=True,
        help="Watch devices.yaml and hot-reload on changes (default: enabled).",
    )
    session.add_argument(
        "--no-hot-reload",
        dest="hot_reload",
        action="store_false",
        help="Disable hot-reload of device config.",
    )
    session.add_argument(
        "--health-monitor",
        dest="health_monitor",
        action="store_true",
        default=False,
        help="Enable periodic device health probing.",
    )
    session.add_argument(
        "--health-interval",
        type=float,
        default=30.0,
        help="Seconds between health probes (default: 30).",
    )
    session.add_argument(
        "--health-discovery",
        action="store_true",
        default=False,
        help="Scan for new devices on the network during health monitoring.",
    )
    session.add_argument(
        "--profile",
        type=str,
        default=None,
        help="Color profile name or path (e.g. 'aurora' or './my_profile.yaml').",
    )
    session.add_argument(
        "--auto-profile",
        action="store_true",
        help="Automatically select a profile based on time of day.",
    )
    session.add_argument(
        "--profile-rotation",
        type=str,
        default=None,
        metavar="NAME1,NAME2,...",
        help="Rotate through multiple profiles (comma-separated names/paths).",
    )
    session.add_argument(
        "--rotation-interval",
        type=float,
        default=300.0,
        help="Seconds between profile rotations when using --profile-rotation (default: 300).",
    )
    session.add_argument(
        "--spotify",
        action="store_true",
        default=False,
        help="Enable Spotify queue watcher (requires prior auth via spotify-auth).",
    )
    session.add_argument(
        "--spotify-client-id",
        type=str,
        default=None,
        help="Spotify app client ID (or set DREAMSYNC_SPOTIFY_CLIENT_ID env var).",
    )
    session.add_argument(
        "--spotify-poll-interval",
        type=float,
        default=2.0,
        help="Spotify playback poll interval in seconds (default: 2.0).",
    )
    session.add_argument(
        "--capture",
        action="store_true",
        default=False,
        help="Enable per-song mp3 capture to disk.",
    )
    session.add_argument(
        "--capture-dir",
        type=str,
        default="captured_songs",
        help="Output directory for captured mp3 files (default: captured_songs).",
    )
    session.add_argument(
        "--capture-naming",
        choices=["timestamp", "metadata"],
        default="timestamp",
        help="Filename scheme for captured songs (default: timestamp).",
    )

    # -- Spotify auth command ------------------------------------------------
    spotify_auth = sub.add_parser(
        "spotify-auth",
        help="Authorize DreamSync with your Spotify account (OAuth PKCE).",
    )
    spotify_auth.add_argument(
        "--client-id",
        type=str,
        required=True,
        help="Spotify Developer App client ID.",
    )
    spotify_auth.add_argument(
        "--port",
        type=int,
        default=8888,
        help="Local redirect port for OAuth callback (default: 8888).",
    )

    # -- Profile management commands -----------------------------------------
    profiles_cmd = sub.add_parser(
        "profiles",
        help="List available color profiles.",
    )
    profiles_cmd.add_argument(
        "--verbose", "-v", action="store_true",
        help="Show full details including tags.",
    )

    validate_cmd = sub.add_parser(
        "profile-validate",
        help="Validate a color profile YAML and check color harmony.",
    )
    validate_cmd.add_argument("path", type=str, help="Profile name or path to validate.")

    # -- Analyze command --------------------------------------------------------
    analyze_cmd = sub.add_parser(
        "analyze",
        help="Analyze an mp3 file and produce a SongStructure (BPM, sections, beat grid).",
    )
    analyze_cmd.add_argument("path", type=Path, help="Path to an mp3 (or any audio) file.")
    analyze_cmd.add_argument(
        "--output", "-o", type=Path, default=None,
        help="Write analysis JSON to this path (defaults to stdout).",
    )
    analyze_cmd.add_argument(
        "--summary", action="store_true",
        help="Print a human-readable summary instead of JSON.",
    )
    analyze_cmd.add_argument("--sample-rate", type=int, default=44100, help="Target sample rate.")
    analyze_cmd.add_argument("--frame-size", type=int, default=2048, help="Frame size in samples.")
    analyze_cmd.add_argument("--hop-size", type=int, default=512, help="Hop size in samples.")

    analyze_dir_cmd = sub.add_parser(
        "analyze-dir",
        help="Batch-analyze all audio files in a directory.",
    )
    analyze_dir_cmd.add_argument("input_dir", type=Path, help="Directory containing audio files.")
    analyze_dir_cmd.add_argument(
        "--output-dir", type=Path, default=None,
        help="Directory for analysis JSON files (default: alongside input files).",
    )
    analyze_dir_cmd.add_argument("--sample-rate", type=int, default=44100, help="Target sample rate.")
    analyze_dir_cmd.add_argument("--frame-size", type=int, default=2048, help="Frame size in samples.")
    analyze_dir_cmd.add_argument("--hop-size", type=int, default=512, help="Hop size in samples.")

    # -- Play command (show playback) ------------------------------------------
    play_cmd = sub.add_parser(
        "play",
        help="Play a pre-sequenced show: mp3 audio + lighting timeline.",
    )
    play_cmd.add_argument("mp3_path", type=Path, help="Path to the mp3 audio file.")
    play_cmd.add_argument("--show", type=Path, required=True, help="Path to the show timeline JSON file.")
    play_cmd.add_argument("--config", type=Path, required=True, help="Path to YAML device config file.")
    play_cmd.add_argument("--sample-rate", type=int, default=44100, help="Audio sample rate.")
    play_cmd.add_argument("--audio-device", type=int, default=None, dest="audio_device", help="Output audio device ID (None = system default).")
    play_cmd.add_argument("--fps", type=int, default=30, help="Device frame rate.")
    play_cmd.add_argument("--brightness", type=float, default=1.0, help="Global brightness (0-1).")
    play_cmd.add_argument(
        "--mirror", dest="mirror", action="store_true", default=True,
        help="Scroll from center outward (default).",
    )
    play_cmd.add_argument(
        "--no-mirror", dest="mirror", action="store_false",
        help="Scroll left-to-right instead of center-outward.",
    )
    play_cmd.add_argument("--debug", action="store_true", help="Print cue changes, beat counts, position.")

    return parser


def _resolve_profile_from_args(args: argparse.Namespace):
    """Resolve a ProfileConfig from --profile, --auto-profile, or None."""
    from .profile import load_profile, resolve_profile_path, suggest_profile

    profile_name = getattr(args, "profile", None)
    auto_profile = getattr(args, "auto_profile", False)

    if profile_name and auto_profile:
        print("Error: --profile and --auto-profile are mutually exclusive.")
        return "error"

    if auto_profile:
        profile_name = suggest_profile()
        print(f"Auto-selected profile: {profile_name}")

    if profile_name:
        path = resolve_profile_path(profile_name)
        profile = load_profile(path)
        print(f"Loaded profile: {profile.name}")
        return profile
    return None


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

    if args.command == "govee-scan":
        from .output.discovery import scan_devices

        print("Scanning for Govee devices (5 seconds)...")
        devices = scan_devices(timeout=5.0)
        if not devices:
            print("No devices found.")
        for dev in devices:
            print(json.dumps(
                {"ip": dev.ip, "sku": dev.sku, "device_id": dev.device_id},
                separators=(",", ":"),
            ))
        print(json.dumps({"devices_found": len(devices)}, separators=(",", ":")))
        return 0

    if args.command == "govee-test":
        import time

        config = GoveeLanConfig(
            device_ip=args.device_ip,
            segments=args.segments,
            fps=args.fps,
            brightness=max(0.0, min(1.0, float(args.brightness))),
            transport=TransportMode(args.transport),
        )
        adapter = GoveeLanAdapter(config)

        hex_color = args.color.lstrip("#")
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)

        # Build color frame based on pattern
        rainbow_palette = [
            (255, 0, 0), (255, 127, 0), (255, 255, 0), (0, 255, 0),
            (0, 255, 255), (0, 0, 255), (127, 0, 255),
        ]
        pattern = args.pattern
        if pattern == "solid":
            colors = [(r, g, b)] * args.segments
        elif pattern == "alternate":
            colors = [(r, g, b) if i % 2 == 0 else (0, 0, 0) for i in range(args.segments)]
        elif pattern == "rainbow":
            colors = [rainbow_palette[i % len(rainbow_palette)] for i in range(args.segments)]
        else:
            colors = [(0, 0, 0)] * args.segments  # walk starts black

        print(json.dumps(
            {"device_ip": args.device_ip, "segments": args.segments,
             "color": args.color, "duration": args.duration, "fps": args.fps,
             "pattern": pattern},
            separators=(",", ":"),
        ))

        # Activate: turn on + set brightness to 100 (with delays so
        # the device processes power-on before receiving color data)
        adapter.turn_on()
        time.sleep(0.8)
        adapter.set_brightness(100)
        time.sleep(0.3)

        if pattern == "walk":
            # Light one segment at a time, cycling through the strip
            seg = 0
            end_time = time.monotonic() + args.duration
            frames_sent = 0
            while time.monotonic() < end_time:
                frame = [(0, 0, 0)] * args.segments
                frame[seg] = rainbow_palette[seg % len(rainbow_palette)]
                if adapter.send_frame(frame):
                    frames_sent += 1
                    print(f"  segment {seg}/{args.segments}: {frame[seg]}")
                    seg = (seg + 1) % args.segments
                time.sleep(0.3)
        else:
            # Stream static pattern
            end_time = time.monotonic() + args.duration
            frames_sent = 0
            while time.monotonic() < end_time:
                if adapter.send_frame(colors):
                    frames_sent += 1
                time.sleep(0.005)

        time.sleep(0.3)
        adapter.turn_off()
        print(json.dumps(
            {"frames_sent": frames_sent, "done": True},
            separators=(",", ":"),
        ))
        return 0

    if args.command == "govee-ble-scan":
        from .output.govee_ble import scan_ble_devices

        print(f"Scanning for Govee BLE devices ({args.timeout:.0f} seconds)...")
        devices = scan_ble_devices(timeout=args.timeout)
        if not devices:
            print("No BLE devices found.")
        for dev in devices:
            print(json.dumps(
                {"name": dev.name, "address": dev.address, "rssi": dev.rssi},
                separators=(",", ":"),
            ))
        print(json.dumps({"ble_devices_found": len(devices)}, separators=(",", ":")))
        return 0

    if args.command == "govee-ble-test":
        import time
        from .output.govee_ble import BleProtocol, GoveeBleAdapter, GoveeBleConfig

        hex_color = args.color.lstrip("#")
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)

        protocol = BleProtocol(args.protocol)
        print(json.dumps(
            {"address": args.address, "color": args.color,
             "brightness": args.brightness, "duration": args.duration,
             "protocol": protocol.value},
            separators=(",", ":"),
        ))

        config = GoveeBleConfig(address=args.address, protocol=protocol)
        adapter = GoveeBleAdapter(config)
        adapter.start()

        # Wait for connection
        print("Connecting...")
        connect_start = time.monotonic()
        while not adapter.connected and (time.monotonic() - connect_start) < config.connect_timeout:
            time.sleep(0.2)

        if not adapter.connected:
            print("Failed to connect.")
            adapter.stop()
            return 1

        print("Connected. Setting color...")
        adapter.send_color(r, g, b, args.brightness)
        time.sleep(args.duration)

        print("Done. Disconnecting...")
        adapter.stop()
        print(json.dumps({"done": True}, separators=(",", ":")))
        return 0

    if args.command == "govee-live":
        render_mode = RenderMode(args.render_mode)
        brightness = max(0.0, min(1.0, float(args.brightness)))
        fps = args.fps
        mirror = args.mirror

        colors = None
        if args.colors:
            colors = tuple(c.strip() for c in args.colors.split(","))
        director_kwargs: dict = {}
        if colors:
            director_kwargs["colors"] = colors
        director_config = DirectorConfig(**director_kwargs)

        # Build device list from --device specs or legacy --device-ip/--segments
        ble_addresses = getattr(args, "ble_devices", None) or []
        specs = []
        if args.govee_devices:
            specs = [parse_device_spec(s) for s in args.govee_devices]
        elif args.device_ip:
            from .output.govee_lan import GoveeDeviceSpec
            specs = [GoveeDeviceSpec(ip=args.device_ip, segments=args.segments)]
        elif not ble_addresses:
            print("Error: at least one of --device-ip, --device, or --ble-device is required.")
            return 1

        global_transport = TransportMode(args.transport)
        device_triples = []
        for spec in specs:
            transport = spec.transport if spec.transport is not None else global_transport
            # colorwc doesn't need high FPS; ptreal is heavier than razer
            if transport == TransportMode.COLORWC:
                effective_fps = min(fps, 10)
            elif transport == TransportMode.PTREAL:
                effective_fps = min(fps, 20)
            else:
                effective_fps = fps
            config = GoveeLanConfig(
                device_ip=spec.ip, segments=spec.segments, fps=effective_fps,
                brightness=brightness, transport=transport,
            )
            adapter = GoveeLanAdapter(config)
            renderer = SegmentRenderer(
                segments=spec.segments, mode=render_mode, mirror=mirror,
            )
            device_triples.append((adapter, renderer, spec.role))

        # Build BLE mood followers — spec format: ADDRESS[:PROTOCOL]
        ble_followers = []
        ble_info = []
        if ble_addresses:
            from .output.govee_ble import BleProtocol, GoveeBleAdapter, GoveeBleConfig
            for spec_str in ble_addresses:
                parts = spec_str.rsplit(":", 1)
                # Distinguish "AA:BB:CC:DD:EE:FF:bulb" from "AA:BB:CC:DD:EE:FF"
                # MAC addresses have 5 colons; if last part is a protocol name, split it off
                if len(parts) == 2 and parts[1] in ("segment", "bulb"):
                    addr = parts[0]
                    proto = BleProtocol(parts[1])
                else:
                    addr = spec_str
                    proto = BleProtocol.SEGMENT
                ble_config = GoveeBleConfig(address=addr, protocol=proto)
                ble_followers.append(GoveeBleAdapter(ble_config))
                ble_info.append({"address": addr, "protocol": proto.value})

        multi_adapter = MultiGoveeLanAdapter(device_triples, ble_followers=ble_followers)

        device_info = [
            {
                "ip": s.ip, "segments": s.segments, "role": s.role.value,
                "transport": (s.transport or global_transport).value,
            }
            for s in specs
        ]
        print(json.dumps(
            {
                "devices": device_info,
                "ble_followers": ble_info,
                "render_mode": args.render_mode,
                "fps": fps,
                "brightness": brightness,
                "mirror": mirror,
                "duration": args.duration,
            },
            separators=(",", ":"),
        ))

        profile = _resolve_profile_from_args(args)
        if profile == "error":
            return 1

        # Profile rotation
        rotation = None
        rotation_names = getattr(args, "profile_rotation", None)
        if rotation_names:
            from .profile import ProfileRotation, load_profile, resolve_profile_path
            names = [n.strip() for n in rotation_names.split(",") if n.strip()]
            rotation_profiles = []
            for n in names:
                p = resolve_profile_path(n)
                rotation_profiles.append(load_profile(p))
            rotation = ProfileRotation(rotation_profiles, interval_seconds=args.rotation_interval)
            profile = rotation.current
            print(f"Profile rotation: {len(rotation_profiles)} profiles, rotating every {args.rotation_interval:.0f}s")

        logs, summary = run_live_to_govee(
            multi_adapter=multi_adapter,
            duration_seconds=args.duration,
            sample_rate=args.sample_rate,
            channels=args.channels,
            device=args.audio_device,
            frame_size=args.frame_size,
            hop_size=args.hop_size,
            telemetry_interval_seconds=max(0.1, float(args.heartbeat_seconds)),
            blocksize=args.blocksize,
            director_config=director_config,
            half_time=args.half_time,
            max_brightness=args.max_brightness,
            auto_cycle=args.auto_cycle,
            cycle_interval=max(1.0, float(args.cycle_interval)),
            debug_mood=args.debug_mood,
            telemetry_dir=args.telemetry_dir,
            crossfade_detect=getattr(args, "crossfade_detect", False),
            profile=profile,
            profile_rotation=rotation,
        )
        if args.jsonl:
            args.jsonl.parent.mkdir(parents=True, exist_ok=True)
            with args.jsonl.open("w", encoding="utf-8") as f:
                for item in logs:
                    f.write(json.dumps(item, separators=(",", ":")) + "\n")
        print(json.dumps(summary, separators=(",", ":")))
        return 0

    if args.command == "spotify-auth":
        from .spotify.auth import start_auth_flow

        try:
            start_auth_flow(args.client_id, redirect_port=args.port)
            print("Spotify authorization complete.")
        except Exception as exc:
            print(f"Spotify authorization failed: {exc}")
            return 1
        return 0

    if args.command == "session":
        from .session import run_session

        brightness = max(0.0, min(1.0, float(args.brightness)))
        director_config = DirectorConfig()

        profile = _resolve_profile_from_args(args)
        if profile == "error":
            return 1

        # Profile rotation for session
        rotation_names = getattr(args, "profile_rotation", None)
        if rotation_names:
            from .profile import ProfileRotation, load_profile, resolve_profile_path as _resolve
            names = [n.strip() for n in rotation_names.split(",") if n.strip()]
            rotation_profiles = []
            for n in names:
                p = _resolve(n)
                rotation_profiles.append(load_profile(p))
            profile = rotation_profiles[0] if rotation_profiles else profile
            print(f"Profile rotation: {len(rotation_profiles)} profiles, rotating every {args.rotation_interval:.0f}s")

        profile_path = None
        if profile is not None:
            profile_path = profile.source_path

        import os

        spotify_client_id = (
            getattr(args, "spotify_client_id", None)
            or os.environ.get("DREAMSYNC_SPOTIFY_CLIENT_ID", "")
        )

        summary = run_session(
            config_path=args.config,
            sample_rate=args.sample_rate,
            channels=args.channels,
            audio_device=args.audio_device,
            frame_size=args.frame_size,
            hop_size=args.hop_size,
            blocksize=args.blocksize,
            render_mode=args.render_mode,
            fps=args.fps,
            brightness=brightness,
            mirror=args.mirror,
            half_time=args.half_time,
            max_brightness=args.max_brightness,
            auto_cycle=args.auto_cycle,
            cycle_interval=max(1.0, float(args.cycle_interval)),
            debug_mood=args.debug_mood,
            probe_packets=args.probe_packets,
            probe_rate=args.probe_rate,
            parallel_probe=not args.no_parallel_probe,
            director_config=director_config,
            telemetry_dir=args.telemetry_dir,
            hot_reload=args.hot_reload,
            profile=profile,
            profile_path=profile_path,
            health_monitor=getattr(args, "health_monitor", False),
            health_interval=getattr(args, "health_interval", 30.0),
            health_discovery=getattr(args, "health_discovery", False),
            spotify=getattr(args, "spotify", False),
            spotify_client_id=spotify_client_id,
            spotify_poll_interval=getattr(args, "spotify_poll_interval", 2.0),
            capture=getattr(args, "capture", False),
            capture_dir=getattr(args, "capture_dir", "captured_songs"),
            capture_naming=getattr(args, "capture_naming", "timestamp"),
        )
        print(json.dumps(summary, separators=(",", ":")))
        return 0

    if args.command == "profiles":
        from .profile import list_available_profiles

        profiles = list_available_profiles()
        if not profiles:
            print("No profiles found.")
            return 0
        for p in profiles:
            if getattr(args, "verbose", False):
                print(json.dumps(p, separators=(",", ":")))
            else:
                desc = f" — {p['description']}" if p.get("description") else ""
                print(f"  {p['name']}{desc}")
        return 0

    if args.command == "profile-validate":
        from .profile import load_profile, resolve_profile_path, validate_color_harmony

        try:
            path = resolve_profile_path(args.path)
            profile = load_profile(path)
        except Exception as exc:
            print(f"Validation failed: {exc}")
            return 1

        print(f"Profile: {profile.name}")
        print(f"  Description: {profile.description or '(none)'}")
        print(f"  Moods: {', '.join(sorted(profile.moods.keys()))}")
        print(f"  Palettes: {', '.join(sorted(profile.palettes.keys()))}")

        warnings_total = 0
        for pal_name, colors in profile.palettes.items():
            warnings = validate_color_harmony(colors)
            for w in warnings:
                print(f"  WARNING [{pal_name}]: {w}")
                warnings_total += 1

        if warnings_total == 0:
            print("  No harmony warnings.")
        else:
            print(f"  {warnings_total} warning(s) found.")
        return 0

    if args.command == "analyze":
        from .analyzer.analyze import analyze_song

        try:
            structure = analyze_song(
                args.path,
                sample_rate=args.sample_rate,
                frame_size=args.frame_size,
                hop_size=args.hop_size,
            )
        except Exception as exc:
            print(f"Analysis failed: {exc}")
            return 1

        if args.summary:
            print(f"File: {structure.path}")
            print(f"Duration: {structure.duration:.1f}s")
            print(f"BPM: {structure.bpm:.1f}")
            print(f"Time signature: {structure.time_signature}/4")
            print(f"Sections ({len(structure.sections)}):")
            for s in structure.sections:
                print(
                    f"  {s.start_t:6.1f}s – {s.end_t:6.1f}s  "
                    f"{s.label:<12s} [{s.section_id}]  "
                    f"energy={s.energy_mean:.2f}  mood={s.mood}  bpm={s.bpm:.0f}"
                )
            return 0

        data = structure.to_dict()
        if args.output:
            structure.to_json(args.output)
            print(f"Analysis written to {args.output}")
        else:
            print(json.dumps(data, indent=2))
        return 0

    if args.command == "analyze-dir":
        from .analyzer.analyze import analyze_song

        input_dir = args.input_dir
        if not input_dir.is_dir():
            print(f"Not a directory: {input_dir}")
            return 1

        output_dir = args.output_dir or input_dir
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        audio_extensions = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac"}
        files = sorted(f for f in input_dir.iterdir() if f.suffix.lower() in audio_extensions)
        if not files:
            print(f"No audio files found in {input_dir}")
            return 0

        print(f"Analyzing {len(files)} files...")
        for i, f in enumerate(files, 1):
            out_path = output_dir / f"{f.stem}.analysis.json"
            try:
                structure = analyze_song(
                    f,
                    sample_rate=args.sample_rate,
                    frame_size=args.frame_size,
                    hop_size=args.hop_size,
                )
                structure.to_json(out_path)
                print(f"  [{i}/{len(files)}] {f.name} → {out_path.name} (BPM={structure.bpm:.0f}, {len(structure.sections)} sections)")
            except Exception as exc:
                print(f"  [{i}/{len(files)}] {f.name} — FAILED: {exc}")
        return 0

    if args.command == "play":
        import signal
        import threading

        from .output.auto_detect import build_multi_adapter, detect_all_devices, load_device_config
        from .show.runtime import run_show_playback

        mp3_path = args.mp3_path
        show_path = args.show
        config_path = args.config

        if not mp3_path.exists():
            print(f"Error: mp3 file not found: {mp3_path}")
            return 1
        if not show_path.exists():
            print(f"Error: show file not found: {show_path}")
            return 1
        if not config_path.exists():
            print(f"Error: config file not found: {config_path}")
            return 1

        brightness = max(0.0, min(1.0, float(args.brightness)))

        try:
            configs = load_device_config(config_path)
            detected = detect_all_devices(configs)
            multi_adapter = build_multi_adapter(
                detected,
                fps=args.fps,
                brightness=brightness,
                mirror=args.mirror,
            )
        except Exception as exc:
            print(f"Device setup failed: {exc}")
            return 1

        stop_event = threading.Event()

        def _signal_handler(signum, frame):
            stop_event.set()

        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)

        print(json.dumps({
            "command": "play",
            "mp3": str(mp3_path),
            "show": str(show_path),
            "config": str(config_path),
        }, separators=(",", ":")))

        try:
            summary = run_show_playback(
                mp3_path,
                show_path,
                multi_adapter,
                sample_rate=args.sample_rate,
                audio_device=args.audio_device,
                stop_event=stop_event,
                debug=args.debug,
            )
        except Exception as exc:
            print(f"Playback failed: {exc}")
            return 1

        print(json.dumps(summary, separators=(",", ":")))
        return 0

    parser.print_help()
    return 0
