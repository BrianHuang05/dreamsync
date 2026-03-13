import argparse
import json
import signal
import sys
import threading
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

    devices_cmd = sub.add_parser("devices", help="List audio input and output devices.")
    devices_cmd.add_argument("--json", action="store_true", dest="json_output",
        help="Output raw JSON (machine-readable).")

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
    capture.add_argument(
        "--mp3",
        action="store_true",
        default=False,
        help="Enable MP3 capture pipeline (records per-song MP3 files via FFmpeg).",
    )
    capture.add_argument(
        "--output-dir",
        type=str,
        default="captured_songs",
        help="Output directory for captured MP3 files (default: captured_songs).",
    )
    capture.add_argument(
        "--naming",
        choices=["timestamp", "metadata"],
        default="timestamp",
        help="Filename scheme for captured songs (default: timestamp).",
    )
    capture.add_argument(
        "--device-pattern",
        type=str,
        default="CABLE Output",
        help="DirectShow audio device name pattern for FFmpeg (default: CABLE Output).",
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
    govee_live.add_argument(
        "--auto-palette",
        action="store_true",
        default=False,
        help="Generate procedural color profiles and chain them with cross-fade.",
    )
    govee_live.add_argument(
        "--auto-palette-seed",
        type=int,
        default=None,
        help="Seed for reproducible profile generation.",
    )
    govee_live.add_argument(
        "--auto-palette-count",
        type=int,
        default=12,
        help="Number of profiles to generate for auto-palette pool (default: 12).",
    )
    govee_live.add_argument(
        "--smart-rotation",
        action="store_true",
        default=False,
        help="Use smart chaining with --profile-rotation profiles (mood-aware, cross-fade).",
    )
    govee_live.add_argument(
        "--chain-blend",
        type=float,
        default=8.0,
        help="Cross-fade duration in seconds between profiles (default: 8.0).",
    )
    govee_live.add_argument(
        "--chain-interval",
        type=str,
        default=None,
        metavar="MIN[-MAX]",
        help="Min[-max] seconds per profile (e.g. '60' or '60-180').",
    )
    govee_live.add_argument(
        "--capture",
        action="store_true",
        default=False,
        help="Enable MP3 capture pipeline (records per-song MP3 files via FFmpeg).",
    )
    govee_live.add_argument(
        "--capture-dir",
        type=str,
        default="captured_songs",
        help="Output directory for captured MP3 files (default: captured_songs).",
    )
    govee_live.add_argument(
        "--capture-naming",
        choices=["timestamp", "metadata"],
        default="timestamp",
        help="Filename scheme for captured songs (default: timestamp).",
    )
    govee_live.add_argument(
        "--capture-buffer",
        type=int,
        default=0,
        metavar="N",
        help="Max MP3 files to keep on disk during capture (0 = unlimited, default: 0). "
             "Oldest files are deleted when the limit is exceeded.",
    )
    govee_live.add_argument(
        "--spotify",
        action="store_true",
        default=False,
        help="Enable Spotify queue watcher for track-accurate capture splits.",
    )
    govee_live.add_argument(
        "--spotify-client-id",
        type=str,
        default=None,
        help="Spotify app client ID (or set DREAMSYNC_SPOTIFY_CLIENT_ID env var).",
    )
    govee_live.add_argument(
        "--spotify-poll-interval",
        type=float,
        default=2.0,
        help="Spotify playback poll interval in seconds (default: 2.0).",
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
        "--auto-palette",
        action="store_true",
        default=False,
        help="Generate procedural color profiles and chain them with cross-fade.",
    )
    session.add_argument(
        "--auto-palette-seed",
        type=int,
        default=None,
        help="Seed for reproducible profile generation.",
    )
    session.add_argument(
        "--auto-palette-count",
        type=int,
        default=12,
        help="Number of profiles to generate for auto-palette pool (default: 12).",
    )
    session.add_argument(
        "--smart-rotation",
        action="store_true",
        default=False,
        help="Use smart chaining with --profile-rotation profiles (mood-aware, cross-fade).",
    )
    session.add_argument(
        "--chain-blend",
        type=float,
        default=8.0,
        help="Cross-fade duration in seconds between profiles (default: 8.0).",
    )
    session.add_argument(
        "--chain-interval",
        type=str,
        default=None,
        metavar="MIN[-MAX]",
        help="Min[-max] seconds per profile (e.g. '60' or '60-180').",
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
    session.add_argument(
        "--capture-buffer",
        type=int,
        default=0,
        metavar="N",
        help="Max MP3 files to keep on disk during capture (0 = unlimited, default: 0). "
             "Oldest files are deleted when the limit is exceeded.",
    )
    session.add_argument(
        "--v3",
        action="store_true",
        default=False,
        help="Enable v3 pre-sequenced show playback (requires --spotify).",
    )
    session.add_argument(
        "--cache-dir",
        type=str,
        default="~/.dreamsync/cache",
        help="Show cache directory (default: ~/.dreamsync/cache).",
    )
    session.add_argument(
        "--pipeline",
        action="store_true",
        default=False,
        help="Stream captured songs through analyze -> compile -> play concurrently.",
    )
    session.add_argument(
        "--playback-device",
        default=None,
        dest="playback_device",
        help="Output audio device ID, or 'pick' for interactive selection.",
    )
    session.add_argument(
        "--purge",
        action="store_true",
        default=False,
        help="Delete MP3 + sidecar after playback (use with --pipeline).",
    )
    session.add_argument(
        "--archive",
        action="store_true",
        default=False,
        help="Archive MP3 files in capture directory on clean shutdown.",
    )
    session.add_argument(
        "--local",
        type=str,
        default=None,
        metavar="AUDIO_PATH",
        help="Play a local audio file with synchronized lighting (no Spotify needed).",
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
    profiles_cmd.add_argument(
        "--generate",
        type=int,
        default=None,
        metavar="N",
        help="Preview N procedurally generated profiles.",
    )
    profiles_cmd.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Seed for profile generation (with --generate).",
    )
    profiles_cmd.add_argument(
        "--chain-preview",
        type=int,
        default=None,
        metavar="N",
        help="Preview N chain transitions (dry-run, with --generate).",
    )

    validate_cmd = sub.add_parser(
        "profile-validate",
        help="Validate a color profile YAML and check color harmony.",
    )
    validate_cmd.add_argument("path", type=str, help="Profile name or path to validate.")

    export_cmd = sub.add_parser(
        "profile-export",
        help="Export a generated profile to a reusable YAML file.",
    )
    export_cmd.add_argument("--seed", type=int, required=True, help="Seed used to generate the pool.")
    export_cmd.add_argument("--index", type=int, required=True, help="Profile index in the pool (0-based).")
    export_cmd.add_argument("--count", type=int, default=12, help="Pool size (default: 12).")
    export_cmd.add_argument("--output", "-o", type=str, required=True, help="Output YAML path.")
    export_cmd.add_argument("--name", type=str, default=None, help="Custom name for the exported profile.")

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
        help="Play audio with synchronized lighting. Accepts a file, directory, or M3U playlist.",
    )
    play_cmd.add_argument("audio_path", type=Path, help="Path to audio file, directory of audio files, or M3U playlist.")
    play_cmd.add_argument("--show", type=Path, default=None, help="Path to compiled show JSON. If omitted, compiles on-the-fly.")
    play_cmd.add_argument("--config", type=Path, default=None, help="Path to YAML device config file (required unless --dry-run).")
    play_cmd.add_argument("--profile", type=str, default=None, help="Profile name or path for on-the-fly compilation.")
    play_cmd.add_argument("--cache-dir", type=str, default="~/.dreamsync/cache", help="Show cache directory (default: ~/.dreamsync/cache).")
    play_cmd.add_argument("--sample-rate", type=int, default=44100, help="Audio sample rate.")
    play_cmd.add_argument("--audio-device", default=None, dest="audio_device", help="Output audio device ID, or 'pick' for interactive selection (None = system default).")
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
    play_cmd.add_argument("--shuffle", action="store_true", default=False, help="Randomize playlist order (for directories and M3U files).")
    play_cmd.add_argument("--repeat", action="store_true", default=False, help="Loop playlist after last track.")
    play_cmd.add_argument("--dry-run", action="store_true", default=False, dest="dry_run",
                          help="Audio only, no device output (skips device detection).")

    # -- Compile command -------------------------------------------------------
    compile_cmd = sub.add_parser(
        "compile",
        help="Compile a SongStructure into a ShowTimeline.",
    )
    compile_cmd.add_argument("structure_path", type=Path, help="Path to SongStructure JSON file.")
    compile_cmd.add_argument("--profile", type=str, default=None, help="Profile name or path.")
    compile_cmd.add_argument("--output", "-o", type=Path, default=None, help="Output show JSON path.")
    compile_cmd.add_argument("--seed", type=int, default=None, help="Random seed for determinism.")
    compile_cmd.add_argument("--summary", action="store_true", help="Print human-readable summary.")
    compile_cmd.add_argument("--cache-dir", type=str, default=None,
                             help="Enable caching — store result in this directory")

    # -- Compile-dir command ---------------------------------------------------
    compile_dir_cmd = sub.add_parser(
        "compile-dir",
        help="Batch-compile all .analysis.json files in a directory into show timelines.",
    )
    compile_dir_cmd.add_argument("input_dir", type=Path, help="Directory containing .analysis.json files.")
    compile_dir_cmd.add_argument("--output-dir", type=Path, default=None,
                                 help="Directory for show JSON files (default: alongside analysis files).")
    compile_dir_cmd.add_argument("--profile", type=str, default=None, help="Profile name or path.")
    compile_dir_cmd.add_argument("--seed", type=int, default=None, help="Random seed for determinism.")
    compile_dir_cmd.add_argument("--summary", action="store_true", help="Print summary for each show.")
    compile_dir_cmd.add_argument("--cache-dir", type=str, default=None,
                                 help="Enable caching — store/check in this directory")

    # -- Compile-and-play command ----------------------------------------------
    cap_cmd = sub.add_parser(
        "compile-and-play",
        help="Analyze, compile, and play a show from an audio file.",
    )
    cap_cmd.add_argument("mp3_path", type=Path, help="Path to audio file.")
    cap_cmd.add_argument("--profile", type=str, default=None, help="Profile name or path.")
    cap_cmd.add_argument("--config", type=Path, required=True, help="Device config YAML.")
    cap_cmd.add_argument("--seed", type=int, default=None, help="Random seed for determinism.")
    cap_cmd.add_argument("--output", "-o", type=Path, default=None, help="Save compiled show JSON.")
    cap_cmd.add_argument("--sample-rate", type=int, default=44100, help="Audio sample rate.")
    cap_cmd.add_argument("--audio-device", default=None, dest="audio_device", help="Output audio device ID, or 'pick' for interactive selection.")
    cap_cmd.add_argument("--fps", type=int, default=30, help="Device frame rate.")
    cap_cmd.add_argument("--brightness", type=float, default=1.0, help="Global brightness (0-1).")
    cap_cmd.add_argument("--mirror", dest="mirror", action="store_true", default=True)
    cap_cmd.add_argument("--no-mirror", dest="mirror", action="store_false")
    cap_cmd.add_argument("--debug", action="store_true", help="Print cue changes.")
    cap_cmd.add_argument("--cache-dir", type=str, default=None,
                         help="Enable caching — check/store in this directory")

    # -- Cache management subcommands ------------------------------------------
    cache_list_cmd = sub.add_parser("cache-list", help="List cached compiled shows")
    cache_list_cmd.add_argument("--cache-dir", type=str, default="~/.dreamsync/cache",
                                help="Cache directory (default: ~/.dreamsync/cache)")

    cache_clear_cmd = sub.add_parser("cache-clear", help="Clear the show cache")
    cache_clear_cmd.add_argument("--track-id", type=str, default=None,
                                 help="Clear only entries for this track ID")
    cache_clear_cmd.add_argument("--cache-dir", type=str, default="~/.dreamsync/cache",
                                 help="Cache directory (default: ~/.dreamsync/cache)")
    cache_clear_cmd.add_argument("--yes", "-y", action="store_true",
                                 help="Skip confirmation prompt")

    cache_info_cmd = sub.add_parser("cache-info", help="Show cache statistics")
    cache_info_cmd.add_argument("--cache-dir", type=str, default="~/.dreamsync/cache",
                                help="Cache directory (default: ~/.dreamsync/cache)")

    # -- Pipeline subcommand ---------------------------------------------------
    pipeline_cmd = sub.add_parser(
        "pipeline",
        help="Run the full pipeline on a capture directory: scan -> analyze -> compile -> play.",
    )
    pipeline_cmd.add_argument("capture_dir", type=Path, help="Path to capture output directory.")
    pipeline_cmd.add_argument("--config", type=Path, default=None,
                              help="Path to YAML device config file (required for play mode).")
    pipeline_cmd.add_argument("--profile", type=str, default=None,
                              help="Profile name or path.")
    pipeline_cmd.add_argument("--cache-dir", type=str, default="~/.dreamsync/cache",
                              help="Show cache directory (default: ~/.dreamsync/cache).")
    pipeline_cmd.add_argument("--sample-rate", type=int, default=44100,
                              help="Audio sample rate.")
    pipeline_cmd.add_argument("--audio-device", default=None, dest="audio_device",
                              help="Output audio device ID, or 'pick' for interactive selection (None = system default).")
    pipeline_cmd.add_argument("--mode", choices=["analyze", "compile", "play"], default="play",
                              help="Pipeline mode: analyze, compile, or play (default: play).")
    pipeline_cmd.add_argument("--shuffle", action="store_true", default=False,
                              help="Randomize track order.")
    pipeline_cmd.add_argument("--repeat", action="store_true", default=False,
                              help="Loop playlist after last track.")
    pipeline_cmd.add_argument("--debug", action="store_true",
                              help="Verbose output.")
    pipeline_cmd.add_argument("--fps", type=int, default=30,
                              help="Device frame rate (default: 30).")
    pipeline_cmd.add_argument("--brightness", type=float, default=1.0,
                              help="Global brightness 0-1 (default: 1.0).")
    pipeline_cmd.add_argument("--mirror", dest="mirror", action="store_true", default=True,
                              help="Scroll from center outward (default).")
    pipeline_cmd.add_argument("--no-mirror", dest="mirror", action="store_false",
                              help="Scroll left-to-right.")

    # -- Archive subcommand ----------------------------------------------------
    archive_cmd = sub.add_parser(
        "archive",
        help="Archive MP3 files in a directory into a zip, leaving JSON sidecars untouched.",
    )
    archive_cmd.add_argument("directory", type=Path, help="Directory containing MP3 files to archive.")
    archive_cmd.add_argument("--name", type=str, default=None,
                             help="Custom archive name (without .zip extension).")
    archive_cmd.add_argument("--keep", action="store_true", default=False,
                             help="Keep original MP3 files after archiving (don't delete).")
    archive_cmd.add_argument("--dry-run", action="store_true", default=False,
                             help="Show what would be archived without creating the zip.")

    return parser


def _format_bytes(n: int) -> str:
    """Format byte count for human display."""
    if n < 1024:
        return f"{n} B"
    elif n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    elif n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    else:
        return f"{n / (1024 * 1024 * 1024):.1f} GB"


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


def _parse_chain_interval(raw: str | None) -> tuple[float, float]:
    """Parse '--chain-interval MIN[-MAX]' into (min, max) seconds."""
    if raw is None:
        return (60.0, 180.0)
    if "-" in raw:
        parts = raw.split("-", 1)
        return (float(parts[0]), float(parts[1]))
    val = float(raw)
    return (val, val * 3)


def _resolve_profile_chain_from_args(args):
    """Build a ProfileChain from --auto-palette or --smart-rotation flags.

    Returns (initial_profile, profile_chain) or (None, None) if not applicable.
    """
    auto_palette = getattr(args, "auto_palette", False)
    smart_rotation = getattr(args, "smart_rotation", False)

    if not auto_palette and not smart_rotation:
        return None, None

    profile_name = getattr(args, "profile", None)
    if auto_palette and profile_name:
        print("Error: --auto-palette and --profile are mutually exclusive.")
        return "error", None

    from .profile_chain import ChainConfig, ProfileChain

    chain_min, chain_max = _parse_chain_interval(getattr(args, "chain_interval", None))
    chain_config = ChainConfig(
        min_profile_duration=chain_min,
        max_profile_duration=chain_max,
        blend_duration=getattr(args, "chain_blend", 8.0),
    )

    if auto_palette:
        from .profile_generator import generate_profile_set

        count = getattr(args, "auto_palette_count", 12)
        seed = getattr(args, "auto_palette_seed", None)
        if seed is None:
            import time
            seed = int(time.time()) % 100000
            print(f"Auto-palette seed: {seed} (reuse with --auto-palette-seed {seed})")
        pool = generate_profile_set(count, seed=seed)
        chain = ProfileChain(pool, chain_config, seed=seed)
        print(f"Auto-palette: {count} generated profiles, blend {chain_config.blend_duration:.0f}s, interval {chain_min:.0f}-{chain_max:.0f}s")
        return chain.current, chain

    if smart_rotation:
        rotation_names = getattr(args, "profile_rotation", None)
        if not rotation_names:
            print("Error: --smart-rotation requires --profile-rotation.")
            return "error", None
        from .profile import load_profile, resolve_profile_path
        names = [n.strip() for n in rotation_names.split(",") if n.strip()]
        profiles = [load_profile(resolve_profile_path(n)) for n in names]
        chain = ProfileChain(profiles, chain_config)
        print(f"Smart rotation: {len(profiles)} profiles, blend {chain_config.blend_duration:.0f}s, interval {chain_min:.0f}-{chain_max:.0f}s")
        return chain.current, chain

    return None, None


def _start_keyboard_listener(session_ref, stop_event, *, debug=False):
    """Start a daemon thread that listens for keyboard input.

    Controls:
    - 'n' or right arrow -> next track
    - 'p' or left arrow -> previous track
    - 'q' -> quit session
    """
    import sys
    import time
    import threading

    def _handle_key(ch, sess_ref, stop_ev):
        session = sess_ref[0] if sess_ref else None
        if ch == "n" and session is not None:
            session.signal_next()
        elif ch == "p" and session is not None:
            session.signal_prev()
        elif ch == "q":
            stop_ev.set()

    def _listener():
        if debug:
            print("[controls] n=next, p=prev, q=quit")

        while not stop_event.is_set():
            try:
                if sys.platform == "win32":
                    import msvcrt
                    if msvcrt.kbhit():
                        ch = msvcrt.getch().decode("utf-8", errors="ignore").lower()
                        _handle_key(ch, session_ref, stop_event)
                else:
                    import select
                    if select.select([sys.stdin], [], [], 0.1)[0]:
                        ch = sys.stdin.read(1).lower()
                        _handle_key(ch, session_ref, stop_event)
            except Exception:
                pass
            time.sleep(0.05)

    # Wrap session_ref for mutability if needed
    if session_ref is None:
        session_ref = [None]
    elif not isinstance(session_ref, list):
        session_ref = [session_ref]

    thread = threading.Thread(target=_listener, daemon=True, name="keyboard-input")
    thread.start()
    return thread


def _resolve_output_device(raw_value: str | None) -> int | None:
    """Resolve --playback-device / --audio-device value to an int or None."""
    if raw_value is None:
        return None
    if raw_value == "pick":
        from dreamsync.audio.system_input import pick_output_device
        return pick_output_device()
    try:
        return int(raw_value)
    except ValueError:
        raise SystemExit(f"Error: invalid device value '{raw_value}'. Use an integer ID or 'pick'.")


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
        from dreamsync.audio.system_input import list_output_devices, format_device_table

        inputs = list_input_devices()
        outputs = list_output_devices()

        if getattr(args, "json_output", False):
            print("=== Audio Input Devices ===")
            for dev in inputs:
                print(json.dumps(dev, separators=(",", ":")))
            print()
            print("=== Audio Output Devices ===")
            for dev in outputs:
                print(json.dumps(dev, separators=(",", ":")))
        else:
            print()
            print("  Audio Input Devices (for --audio-device)")
            print(format_device_table(inputs, kind="input"))
            print()
            print("  Audio Output Devices (for --playback-device / --audio-device)")
            print(format_device_table(outputs, kind="output", mark_capture=True))
            print()
        return 0

    if args.command == "capture":
        if getattr(args, "mp3", False):
            # MP3 capture pipeline mode
            from dreamsync.capture.orchestrator import CaptureOrchestrator, OrchestratorConfig

            orch_cfg = OrchestratorConfig(
                sample_rate=getattr(args, "sample_rate", 48000),
                channels=getattr(args, "channels", 2),
                device_pattern=args.device_pattern,
                output_dir=args.output_dir,
                naming=args.naming,
                max_capture_files=getattr(args, "capture_buffer", 0),
            )
            orchestrator = CaptureOrchestrator(
                config=orch_cfg,
                on_segment_saved=lambda path, meta: print(
                    f"Saved: {Path(path).name}"
                ),
            )
            orchestrator.start()
            print(f"Capturing MP3 to {args.output_dir}/ (Ctrl+C to stop)")

            stop = threading.Event()
            original_sigint = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, lambda *_: stop.set())
            stop.wait(timeout=args.duration)
            signal.signal(signal.SIGINT, original_sigint)

            orchestrator.shutdown()
            stats = orchestrator.stats
            print(
                f"Done: {stats['segments_completed']} segments, "
                f"{stats['elapsed_seconds']:.0f}s captured"
            )
            return 0

        # Feature extraction capture mode (unchanged)
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

        # --- Profile chain (auto-palette / smart-rotation) ---
        profile_chain = None
        chain_profile, profile_chain = _resolve_profile_chain_from_args(args)
        if chain_profile == "error":
            return 1
        if profile_chain is not None:
            profile = chain_profile
        else:
            profile = _resolve_profile_from_args(args)
            if profile == "error":
                return 1

        # Profile rotation (legacy, used when --smart-rotation is not set)
        rotation = None
        if profile_chain is None:
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

        # Spotify queue watcher (for capture track splitting)
        spotify_watcher = None
        if getattr(args, "spotify", False):
            from dreamsync.spotify.auth import TokenStore, refresh_if_needed
            from dreamsync.spotify.client import SpotifyClient
            from dreamsync.spotify.queue_watcher import SpotifyQueueWatcher

            token_store = TokenStore()
            if not token_store.has_valid_token() and not refresh_if_needed(token_store):
                print("Spotify: no valid token found. Run 'dreamsync spotify-auth' first.")
            else:
                client = SpotifyClient(token_store)
                def _safe_track_msg(new):
                    enc = sys.stdout.encoding or "utf-8"
                    name = new.name.encode(enc, errors="replace").decode(enc, errors="replace")
                    artist = new.artist.encode(enc, errors="replace").decode(enc, errors="replace")
                    return f"Spotify: now playing '{name}' by {artist}"

                spotify_watcher = SpotifyQueueWatcher(
                    client,
                    poll_interval=getattr(args, "spotify_poll_interval", 2.0),
                    on_track_changed=lambda new, old: print(_safe_track_msg(new)),
                )
                spotify_watcher.start()

        # Start capture orchestrator if --capture flag is set
        capture_orchestrator = None
        if getattr(args, "capture", False):
            from dreamsync.capture.orchestrator import CaptureOrchestrator, OrchestratorConfig

            capture_dir = getattr(args, "capture_dir", "captured_songs")
            orch_cfg = OrchestratorConfig(
                output_dir=capture_dir,
                naming=getattr(args, "capture_naming", "timestamp"),
                max_capture_files=getattr(args, "capture_buffer", 0),
                log_dir=str(Path(capture_dir) / "logs"),
            )
            def _safe_segment_msg(path, meta):
                enc = sys.stdout.encoding or "utf-8"
                fname = Path(path).name
                if meta.get("song_title"):
                    artist = str(meta.get("artist", "")).encode(enc, errors="replace").decode(enc, errors="replace")
                    title = str(meta.get("song_title", "")).encode(enc, errors="replace").decode(enc, errors="replace")
                    return f"Capture: saved {fname} ({artist} - {title})"
                return f"Capture: saved {fname}"

            capture_orchestrator = CaptureOrchestrator(
                config=orch_cfg,
                on_segment_saved=lambda path, meta: print(_safe_segment_msg(path, meta)),
            )

            # Connect Spotify track changes to capture orchestrator
            if spotify_watcher is not None:
                _orig_on_track = spotify_watcher._on_track_changed

                def _capture_track_changed(new, old, _orig=_orig_on_track):
                    # Critical path: notify orchestrator of track change
                    try:
                        timing_data = {
                            "song_durations": [new.duration_ms / 1000.0],
                            "current_playback_time": 0.0,
                            "current_song": {
                                "song_title": new.name,
                                "artist": new.artist,
                                "album": new.album,
                            },
                        }
                        if old is not None:
                            timing_data["previous_song"] = {
                                "song_title": old.name,
                                "artist": old.artist,
                                "album": old.album,
                            }
                        capture_orchestrator.on_track_change(timing_data)
                    except Exception:
                        pass
                    # Informational: display track change to user
                    if _orig:
                        try:
                            _orig(new, old)
                        except Exception:
                            pass

                spotify_watcher._on_track_changed = _capture_track_changed

            capture_orchestrator.start()

            # Start periodic timing refresh from Spotify queue
            if spotify_watcher is not None:
                def _fetch_timing():
                    queue = spotify_watcher.queue
                    if queue is None or queue.currently_playing is None:
                        return None
                    current = queue.currently_playing
                    pb = spotify_watcher.playback_state
                    return {
                        "song_durations": [
                            t.duration_ms / 1000.0
                            for t in [current, *queue.queue]
                        ],
                        "current_playback_time": (pb.progress_ms / 1000.0) if pb else 0.0,
                        "current_song": {
                            "song_title": current.name,
                            "artist": current.artist,
                            "album": current.album,
                        },
                    }
                capture_orchestrator.start_periodic_timing(_fetch_timing)

            print(f"Capture: enabled -> {capture_dir}/")

        try:
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
                profile_chain=profile_chain,
            )
        finally:
            if capture_orchestrator is not None:
                capture_orchestrator.shutdown()
                stats = capture_orchestrator.stats
                print(
                    f"Capture: {stats['segments_completed']} segments, "
                    f"{stats['elapsed_seconds']:.0f}s captured"
                )
            if spotify_watcher is not None:
                try:
                    spotify_watcher.stop()
                except Exception:
                    pass

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

        # Resolve interactive device picker early (before threads start)
        args.playback_device = _resolve_output_device(getattr(args, "playback_device", None))

        brightness = max(0.0, min(1.0, float(args.brightness)))
        director_config = DirectorConfig()

        # --- Profile chain (auto-palette / smart-rotation) ---
        session_profile_chain = None
        chain_profile, session_profile_chain = _resolve_profile_chain_from_args(args)
        if chain_profile == "error":
            return 1
        if session_profile_chain is not None:
            profile = chain_profile
        else:
            profile = _resolve_profile_from_args(args)
            if profile == "error":
                return 1

            # Profile rotation for session (legacy)
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
            capture_buffer=getattr(args, "capture_buffer", 0),
            v3=getattr(args, "v3", False),
            cache_dir=getattr(args, "cache_dir", "~/.dreamsync/cache"),
            local=getattr(args, "local", None) is not None,
            local_audio=getattr(args, "local", None),
            pipeline=getattr(args, "pipeline", False),
            playback_device=getattr(args, "playback_device", None),
            purge=getattr(args, "purge", False),
            profile_chain=session_profile_chain,
        )
        print(json.dumps(summary, separators=(",", ":")))

        # Auto-archive MP3s on clean shutdown
        if getattr(args, "archive", False) and getattr(args, "capture", False):
            capture_dir = Path(getattr(args, "capture_dir", "captured_songs"))
            if capture_dir.is_dir():
                from .capture.archiver import archive_mp3s
                archive_path = archive_mp3s(capture_dir)
                if archive_path:
                    print(f"Archived MP3s to {archive_path}")

        return 0

    if args.command == "profiles":
        generate_count = getattr(args, "generate", None)
        chain_preview = getattr(args, "chain_preview", None)
        seed = getattr(args, "seed", None)

        if generate_count is not None:
            from .profile_generator import generate_profile_set
            from .color_utils import profile_color_distance
            from .profile_chain import _get_tag

            gen_profiles = generate_profile_set(generate_count, seed=seed)
            for i, p in enumerate(gen_profiles, 1):
                primary = _get_tag(p, "primary:") or "?"
                secondary = _get_tag(p, "secondary:") or "?"
                temp = next((t for t in ("warm", "cool", "neutral") if t in p.tags), "?")
                intensity = next((t for t in ("muted", "medium", "vivid") if t in p.tags), "?")
                tag_str = f"[primary:{primary}  secondary:{secondary}  {temp}/{intensity}]"
                print(f"  {i:>2}. {p.name:40s} {tag_str}")
                if getattr(args, "verbose", False):
                    for pal_name, colors in p.palettes.items():
                        print(f"      {pal_name}: {' '.join(colors)}")

            if seed is not None:
                print(f"\nSeed: {seed} (use --auto-palette-seed {seed} to reuse this palette set)")

            if chain_preview is not None:
                from .profile_chain import ChainConfig, ProfileChain, pick_next_profile, _tag_score

                chain = ProfileChain(gen_profiles, ChainConfig(), seed=seed)
                print(f"\nChain sequence (seed={seed}, {generate_count} profiles):")
                current = chain.current
                for step in range(1, chain_preview + 1):
                    import random as _rnd
                    rng = _rnd.Random(seed if seed is not None else step)
                    nxt = pick_next_profile(current, gen_profiles, [], rng)
                    dist = profile_color_distance(current, nxt)
                    score = _tag_score(current, nxt)
                    cur_p = _get_tag(current, "primary:") or "?"
                    nxt_p = _get_tag(nxt, "primary:") or "?"
                    # Describe the transition reason
                    reasons = []
                    if cur_p != nxt_p:
                        reasons.append("contrast primary")
                    nxt_sec = _get_tag(nxt, "secondary:")
                    if nxt_sec and nxt_sec == cur_p:
                        reasons.append(f"color thread {cur_p}->{nxt_sec}")
                    reason_str = ", ".join(reasons) if reasons else "distance"
                    print(f"  {step}. {current.name} ({cur_p}){' ':>2} -> {nxt.name} ({nxt_p}){' ':>2} score={score:.2f} dist={dist:.0f} [{reason_str}]")
                    current = nxt

            return 0

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

    if args.command == "profile-export":
        from .profile_generator import generate_profile_set, export_profile_yaml

        seed = args.seed
        index = args.index
        count = args.count
        output = args.output

        if index < 0 or index >= count:
            print(f"Error: --index {index} is out of range for pool size {count} (0-{count - 1}).")
            return 1

        pool = generate_profile_set(count, seed=seed)
        profile = pool[index]
        export_profile_yaml(profile, output, name_override=args.name, seed=seed, index=index)
        print(f"Exported profile '{args.name or profile.name}' to {output}")
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

        import sys
        _enc = sys.stdout.encoding or "utf-8"
        def _safe(s: str) -> str:
            return s.encode(_enc, errors="replace").decode(_enc)

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
                print(_safe(f"  [{i}/{len(files)}] {f.name} -> {out_path.name} (BPM={structure.bpm:.0f}, {len(structure.sections)} sections)"))
            except Exception as exc:
                print(_safe(f"  [{i}/{len(files)}] {f.name} - FAILED: {exc}"))
        return 0

    if args.command == "play":
        import signal
        import threading

        # Resolve interactive device picker early (before threads start)
        args.audio_device = _resolve_output_device(getattr(args, "audio_device", None))

        audio_path = args.audio_path
        show_path = args.show
        config_path = args.config
        dry_run = getattr(args, "dry_run", False)

        if not audio_path.exists():
            print(f"Error: audio path not found: {audio_path}")
            return 1
        if not dry_run and not config_path:
            print("Error: --config is required (or use --dry-run for audio-only)")
            return 1
        if config_path and not config_path.exists():
            print(f"Error: config file not found: {config_path}")
            return 1

        brightness = max(0.0, min(1.0, float(args.brightness)))

        def _build_play_adapter():
            if dry_run:
                from .output.null_adapter import NullMultiAdapter
                return NullMultiAdapter()
            from .output.auto_detect import build_multi_adapter, detect_all_devices, load_device_config
            configs = load_device_config(config_path)
            detected = detect_all_devices(configs)
            return build_multi_adapter(detected, fps=args.fps, brightness=brightness, mirror=args.mirror)

        if show_path is not None:
            # Existing behavior: play pre-compiled show (single file only)
            if not show_path.exists():
                print(f"Error: show file not found: {show_path}")
                return 1

            try:
                multi_adapter = _build_play_adapter()
            except Exception as exc:
                print(f"Device setup failed: {exc}")
                return 1

            stop_event = threading.Event()

            def _signal_handler(signum, frame):
                stop_event.set()

            signal.signal(signal.SIGINT, _signal_handler)
            signal.signal(signal.SIGTERM, _signal_handler)

            from .show.runtime import run_show_playback

            try:
                summary = run_show_playback(
                    audio_path,
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
        else:
            # On-the-fly compilation with playlist support
            from .local_session import run_local_session
            from .playlist import PlaylistManager

            try:
                multi_adapter = _build_play_adapter()
            except Exception as exc:
                print(f"Device setup failed: {exc}")
                return 1

            profile = None
            if getattr(args, "profile", None):
                from .profile import load_profile, resolve_profile_path
                profile_path_resolved = resolve_profile_path(args.profile)
                profile = load_profile(profile_path_resolved)

            # Create playlist (auto-detects file, directory, or M3U)
            playlist = PlaylistManager.from_path(
                audio_path,
                shuffle=getattr(args, "shuffle", False),
                repeat=getattr(args, "repeat", False),
            )

            stop_event = threading.Event()
            signal.signal(signal.SIGINT, lambda *_: stop_event.set())

            # If playlist session, set up keyboard controls
            if len(playlist) > 1:
                _start_keyboard_listener(None, stop_event, debug=getattr(args, "debug", False))

            summary = run_local_session(
                multi_adapter,
                audio_path,
                cache_dir=getattr(args, "cache_dir", "~/.dreamsync/cache"),
                profile=profile,
                sample_rate=args.sample_rate,
                audio_device=args.audio_device,
                stop_event=stop_event,
                debug=getattr(args, "debug", False),
                playlist=playlist if len(playlist) > 1 else None,
            )

        print(json.dumps(summary, separators=(",", ":")))
        return 0

    if args.command == "compile":
        from .analyzer.models import SongStructure
        from .compiler import compile_show
        from .compiler.compile import format_summary

        if not args.structure_path.exists():
            print(f"Error: structure file not found: {args.structure_path}")
            return 1

        try:
            structure = SongStructure.from_json(args.structure_path)
        except Exception as exc:
            print(f"Error loading structure: {exc}")
            return 1

        profile = _resolve_profile_from_args(args) if args.profile else None
        if profile == "error":
            return 1

        if args.cache_dir:
            from .cache import ShowCache, cached_compile_show, path_based_track_id
            cache = ShowCache(args.cache_dir)
            track_id = path_based_track_id(args.structure_path)
            timeline, from_cache = cached_compile_show(
                structure, profile, cache=cache, track_id=track_id, seed=args.seed,
            )
            if from_cache:
                print("Cache hit - loaded from cache")
            else:
                print("Cache miss - compiled and cached")
        else:
            timeline = compile_show(structure, profile, seed=args.seed)

        if args.output:
            timeline.to_json(args.output)
            print(f"Show timeline written to {args.output}")
        if args.summary:
            print(format_summary(structure, timeline))
        if not args.output and not args.summary:
            print(json.dumps(timeline.to_dict(), indent=2))
        return 0

    if args.command == "compile-dir":
        from .analyzer.models import SongStructure
        from .compiler import compile_show
        from .compiler.compile import format_summary

        input_dir = args.input_dir
        if not input_dir.is_dir():
            print(f"Not a directory: {input_dir}")
            return 1

        output_dir = args.output_dir or input_dir
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        files = sorted(input_dir.glob("*.analysis.json"))
        if not files:
            print(f"No .analysis.json files found in {input_dir}")
            return 0

        profile = _resolve_profile_from_args(args) if args.profile else None
        if profile == "error":
            return 1

        cache = None
        if args.cache_dir:
            from .cache import ShowCache, cached_compile_show, path_based_track_id
            cache = ShowCache(args.cache_dir)

        import sys
        _enc = sys.stdout.encoding or "utf-8"
        def _safe(s: str) -> str:
            return s.encode(_enc, errors="replace").decode(_enc)

        print(f"Compiling {len(files)} shows...")
        for i, f in enumerate(files, 1):
            # Derive show filename: foo.analysis.json -> foo.show.json
            stem = f.name.removesuffix(".analysis.json")
            out_path = output_dir / f"{stem}.show.json"
            try:
                structure = SongStructure.from_json(f)

                if cache is not None:
                    track_id = path_based_track_id(f)
                    timeline, from_cache = cached_compile_show(
                        structure, profile, cache=cache, track_id=track_id, seed=args.seed,
                    )
                    status = "cache hit" if from_cache else "compiled"
                else:
                    timeline = compile_show(structure, profile, seed=args.seed)
                    status = "compiled"

                timeline.to_json(out_path)
                print(_safe(f"  [{i}/{len(files)}] {stem} -> {out_path.name} ({len(timeline.cues)} cues, {status})"))
                if args.summary:
                    print(format_summary(structure, timeline))
            except Exception as exc:
                print(_safe(f"  [{i}/{len(files)}] {stem} - FAILED: {exc}"))
        return 0

    if args.command == "compile-and-play":
        import signal
        import threading

        # Resolve interactive device picker early (before threads start)
        args.audio_device = _resolve_output_device(getattr(args, "audio_device", None))

        from .analyzer.analyze import analyze_song
        from .compiler import compile_show
        from .output.auto_detect import build_multi_adapter, detect_all_devices, load_device_config
        from .show.runtime import run_show_playback

        if not args.mp3_path.exists():
            print(f"Error: audio file not found: {args.mp3_path}")
            return 1
        if not args.config.exists():
            print(f"Error: config file not found: {args.config}")
            return 1

        profile = _resolve_profile_from_args(args) if args.profile else None
        if profile == "error":
            return 1

        # Derive artifact paths from the MP3 path
        mp3_stem = args.mp3_path.with_suffix("")
        analysis_path = Path(f"{mp3_stem}.analysis.json")
        show_path = args.output or Path(f"{mp3_stem}.show.json")

        if args.cache_dir:
            from .cache import ShowCache, cached_compile_show, path_based_track_id
            cache = ShowCache(args.cache_dir)
            track_id = path_based_track_id(args.mp3_path)

            if cache.has(track_id, profile):
                timeline = cache.get(track_id, profile)
                print("Cache hit - skipping analysis and compilation")
            else:
                print(f"Analyzing {args.mp3_path}...")
                try:
                    structure = analyze_song(args.mp3_path)
                except Exception as exc:
                    print(f"Analysis failed: {exc}")
                    return 1

                structure.to_json(analysis_path)
                print(f"Analysis saved to {analysis_path}")

                print("Cache miss - analyzing and compiling...")
                timeline, _ = cached_compile_show(
                    structure, profile, cache=cache, track_id=track_id, seed=args.seed,
                )
        else:
            print(f"Analyzing {args.mp3_path}...")
            try:
                structure = analyze_song(args.mp3_path)
            except Exception as exc:
                print(f"Analysis failed: {exc}")
                return 1

            structure.to_json(analysis_path)
            print(f"Analysis saved to {analysis_path}")

            print("Compiling show...")
            timeline = compile_show(structure, profile, seed=args.seed)

        timeline.to_json(show_path)
        print(f"Show saved to {show_path}")

        brightness = max(0.0, min(1.0, float(args.brightness)))
        try:
            configs = load_device_config(args.config)
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

        print("Playing show...")
        try:
            summary = run_show_playback(
                args.mp3_path,
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

    if args.command == "cache-list":
        from .cache import ShowCache

        cache = ShowCache(args.cache_dir)
        s = cache.stats()
        entries = cache.list_entries()

        size_str = _format_bytes(s.total_bytes)
        print(f"Show Cache: {s.entry_count} entries ({size_str}) in {s.cache_dir}\n")

        if not entries:
            print("Cache is empty.")
            return 0

        print(f"{'Track':<28} {'Artist':<16} {'Profile':<16} {'Compiled At':<21} {'Size':>8}")
        print("\u2500" * 93)

        for e in entries:
            track = e.track_name[:27]
            artist = e.artist[:15]
            prof = e.profile_name[:15]
            compiled = e.compiled_at[:19].replace("T", " ") if e.compiled_at != "unknown" else "unknown"
            size = _format_bytes(e.file_size)
            print(f"{track:<28} {artist:<16} {prof:<16} {compiled:<21} {size:>8}")

        return 0

    if args.command == "cache-clear":
        from .cache import ShowCache

        cache = ShowCache(args.cache_dir)

        if args.track_id:
            count = cache.invalidate(args.track_id)
            if count:
                print(f"Deleted {count} cached show{'s' if count != 1 else ''} for track {args.track_id}.")
            else:
                print(f"No cached shows found for track {args.track_id}.")
        else:
            s = cache.stats()
            if s.entry_count == 0:
                print("Cache is already empty.")
                return 0

            if not args.yes:
                answer = input(f"Clear all {s.entry_count} cached shows? [y/N]: ")
                if answer.lower() != "y":
                    print("Aborted.")
                    return 0

            count = cache.clear()
            print(f"Deleted {count} cached show{'s' if count != 1 else ''}.")

        return 0

    if args.command == "cache-info":
        from .cache import ShowCache

        cache = ShowCache(args.cache_dir)
        s = cache.stats()

        print("Show Cache Info")
        print(f"  Directory:   {s.cache_dir}")
        print(f"  Entries:     {s.entry_count}")
        print(f"  Tracks:      {s.track_count}")
        print(f"  Disk usage:  {_format_bytes(s.total_bytes)}")

        return 0

    if args.command == "pipeline":
        import signal
        import threading

        # Resolve interactive device picker early (before threads start)
        args.audio_device = _resolve_output_device(getattr(args, "audio_device", None))

        from .cache import ShowCache
        from .dir_pipeline import DirectoryPipeline

        capture_dir = args.capture_dir
        if not capture_dir.is_dir():
            print(f"Error: capture directory not found: {capture_dir}")
            return 1

        if args.mode == "play" and not args.config:
            print("Error: --config is required for play mode.")
            return 1

        profile = None
        if getattr(args, "profile", None):
            profile = _resolve_profile_from_args(args)
            if profile == "error":
                return 1

        cache = ShowCache(args.cache_dir)

        def on_progress(step, index, total, track):
            label = track.song_title or track.mp3_path.name
            print(f"  [{index + 1}/{total}] {step}: {label}")

        pipeline = DirectoryPipeline(
            capture_dir,
            cache=cache,
            profile=profile,
            sample_rate=args.sample_rate,
            on_progress=on_progress if args.debug else None,
        )

        print(f"Scanning {capture_dir}...")
        result = pipeline.prepare()

        print(f"\n{len(result.tracks)} tracks scanned, "
              f"{result.analyzed} analyzed, "
              f"{result.compiled} compiled "
              f"({result.cache_hits} cache hits), "
              f"{result.errors} errors")

        for tr in result.tracks:
            if tr.error:
                label = tr.track.song_title or tr.track.mp3_path.name
                print(f"  WARNING: {label}: {tr.error}")

        if args.mode in ("analyze", "compile"):
            return 0

        # -- Play mode --
        from .local_session import run_local_session
        from .output.auto_detect import build_multi_adapter, detect_all_devices, load_device_config
        from .playlist import PlaylistManager

        if not args.config.exists():
            print(f"Error: config file not found: {args.config}")
            return 1

        playable = pipeline.playable_tracks()
        if not playable:
            print("No playable tracks. Exiting.")
            return 1

        brightness = max(0.0, min(1.0, float(args.brightness)))
        try:
            configs = load_device_config(args.config)
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

        playlist = PlaylistManager(
            playable,
            shuffle=args.shuffle,
            repeat=args.repeat,
        )

        stop_event = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: stop_event.set())

        if len(playlist) > 1:
            _start_keyboard_listener(None, stop_event, debug=args.debug)

        print(f"\nPlaying {len(playable)} tracks...")
        summary = run_local_session(
            multi_adapter,
            playable[0],
            cache_dir=args.cache_dir,
            profile=profile,
            sample_rate=args.sample_rate,
            audio_device=args.audio_device,
            stop_event=stop_event,
            debug=args.debug,
            playlist=playlist if len(playlist) > 1 else None,
        )

        print(json.dumps(summary, separators=(",", ":")))
        return 0

    if args.command == "archive":
        from .capture.archiver import archive_mp3s

        directory = args.directory
        if not directory.is_dir():
            print(f"Error: directory not found: {directory}")
            return 1

        mp3s = sorted(directory.glob("*.mp3"))
        if not mp3s:
            print(f"No MP3 files found in {directory}")
            return 0

        if args.dry_run:
            print(f"Would archive {len(mp3s)} MP3 file(s):")
            for p in mp3s:
                print(f"  {p.name}  ({_format_bytes(p.stat().st_size)})")
            json_count = len(list(directory.glob("*.json")))
            print(f"\n{json_count} JSON file(s) would be left untouched.")
            return 0

        result = archive_mp3s(
            directory,
            archive_name=args.name,
            delete_originals=not args.keep,
        )

        if result is None:
            print(f"No MP3 files found in {directory}")
            return 0

        action = "kept" if args.keep else "deleted"
        print(f"Archived {len(mp3s)} MP3 file(s) to {result.name} "
              f"({_format_bytes(result.stat().st_size)}), originals {action}")
        return 0

    parser.print_help()
    return 0
