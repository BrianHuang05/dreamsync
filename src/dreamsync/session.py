"""Infinite session orchestrator for DreamSync.

Loads a YAML device config, probes devices, auto-classifies roles,
and runs the live audio loop until SIGINT/SIGTERM (Ctrl+C).
"""

from __future__ import annotations

import json
import signal
import threading
from pathlib import Path
from typing import Any

from dreamsync.director import DirectorConfig
from dreamsync.live import run_live_to_govee
from dreamsync.output.auto_detect import (
    build_multi_adapter,
    detect_all_devices,
    load_device_config,
    print_detection_report,
)
from dreamsync.render import RenderMode


def run_session(
    config_path: Path,
    *,
    sample_rate: int = 44100,
    channels: int = 1,
    audio_device: int | None = None,
    frame_size: int = 2048,
    hop_size: int = 512,
    blocksize: int = 1024,
    render_mode: str = "scroll",
    fps: int = 30,
    brightness: float = 1.0,
    mirror: bool = True,
    half_time: bool = False,
    max_brightness: bool = False,
    auto_cycle: bool = True,
    cycle_interval: float = 16.0,
    debug_mood: bool = False,
    probe_packets: int | None = None,
    probe_rate: float = 5.0,
    parallel_probe: bool = True,
    director_config: DirectorConfig | None = None,
    telemetry_dir: Path | None = None,
    hot_reload: bool = True,
    profile: Any | None = None,
    profile_path: Path | None = None,
    health_monitor: bool = False,
    health_interval: float = 30.0,
    health_discovery: bool = False,
    spotify: bool = False,
    spotify_client_id: str = "",
    spotify_poll_interval: float = 2.0,
    capture: bool = False,
    capture_dir: str = "captured_songs",
    capture_naming: str = "timestamp",
) -> dict[str, Any]:
    """Run an infinite DreamSync session from a YAML config.

    Returns a summary dict when the session ends (via signal or error).
    """
    # 1. Load YAML config
    configs = load_device_config(config_path)
    print(f"Loaded {len(configs)} device(s) from {config_path}")

    # 2. Probe devices
    print("Probing devices...")
    detect_kwargs: dict[str, Any] = {"parallel": parallel_probe}
    if probe_packets is not None:
        detect_kwargs["num_packets"] = probe_packets
        detect_kwargs["rate_hz"] = probe_rate
    detected = detect_all_devices(configs, **detect_kwargs)
    print_detection_report(detected)

    # 3. Build adapter
    mode = RenderMode(render_mode)
    multi_adapter = build_multi_adapter(
        detected,
        render_mode=mode,
        mirror=mirror,
        brightness=brightness,
        fps=fps,
    )

    # 4. Hot-reload watchers
    watcher = None
    if hot_reload:
        from dreamsync.config_watcher import ConfigWatcher

        watcher = ConfigWatcher(
            config_path,
            multi_adapter,
            probe_packets=min(probe_packets or 5, 10),
            probe_rate=probe_rate,
            render_mode=mode,
            mirror=mirror,
            brightness=brightness,
            fps=fps,
        )
        watcher.start()

    # 4b. Health monitor
    health_mon = None
    if health_monitor:
        from dreamsync.device_health import DeviceHealthMonitor

        health_mon = DeviceHealthMonitor(
            multi_adapter,
            configs,
            probe_interval=health_interval,
            enable_discovery=health_discovery,
        )
        health_mon.start()

    # 4c. Create EffectCycler upfront so ProfileWatcher can hold a reference
    effect_cycler = None
    profile_watcher = None
    if auto_cycle:
        from dreamsync.effects import EffectCycler, EffectCyclerConfig

        effect_cycler = EffectCycler(
            EffectCyclerConfig(cycle_interval=cycle_interval), profile=profile,
        )

        # Start profile watcher if we have a source file and hot-reload is on
        if profile_path and hot_reload:
            from dreamsync.profile import ProfileWatcher

            profile_watcher = ProfileWatcher(
                profile_path,
                effect_cycler.set_profile,
            )
            profile_watcher.start()

    # 4d. Spotify queue watcher
    spotify_watcher = None
    if spotify:
        from dreamsync.spotify.auth import TokenStore, refresh_if_needed
        from dreamsync.spotify.client import SpotifyClient
        from dreamsync.spotify.queue_watcher import SpotifyQueueWatcher

        token_store = TokenStore()
        if not token_store.has_valid_token() and not refresh_if_needed(token_store):
            print("Spotify: no valid token found. Run 'dreamsync spotify-auth' first.")
            print("Continuing in v2 reactive mode.")
        else:
            client = SpotifyClient(token_store)
            spotify_watcher = SpotifyQueueWatcher(
                client,
                poll_interval=spotify_poll_interval,
                on_track_changed=lambda new, old: print(
                    f"Spotify: now playing '{new.name}' by {new.artist}"
                ),
                on_queue_updated=lambda q: print(
                    f"Spotify: queue updated ({len(q.queue)} upcoming tracks)"
                ),
            )
            spotify_watcher.start()

    # 4e. Capture pipeline
    capture_pipeline = None
    if capture:
        from dreamsync.capture.pipeline import CaptureConfig, StreamCapturePipeline
        from dreamsync.capture.buffer import BufferConfig
        from dreamsync.capture.writer import WriterConfig, check_ffmpeg

        if not check_ffmpeg():
            print("Capture: ffmpeg not found on PATH. Install ffmpeg to enable song capture.")
            print("Continuing without capture.")
        else:
            cap_cfg = CaptureConfig(
                buffer=BufferConfig(sample_rate=sample_rate, channels=channels),
                writer=WriterConfig(
                    output_dir=capture_dir,
                    sample_rate=sample_rate,
                    channels=channels,
                    naming=capture_naming,
                ),
                hop_size=hop_size,
                sample_rate=sample_rate,
            )
            capture_pipeline = StreamCapturePipeline(
                config=cap_cfg,
                on_song_saved=lambda path, meta: print(
                    f"Capture: saved {path.name} ({meta.get('source', '?')})"
                ),
            )
            # Connect Spotify track changes to capture boundary detector
            if spotify_watcher is not None:
                _orig_on_track = spotify_watcher._on_track_changed

                def _capture_track_changed(new, old, _orig=_orig_on_track):
                    if _orig:
                        _orig(new, old)
                    capture_pipeline.notify_track_changed(new, old)

                spotify_watcher._on_track_changed = _capture_track_changed

            print(f"Capture: enabled → {capture_dir}/ (naming={capture_naming})")

    # 5. Signal handling
    stop_event = threading.Event()
    original_sigint = signal.getsignal(signal.SIGINT)
    original_sigterm = signal.getsignal(signal.SIGTERM)

    def _handle_signal(signum: int, frame: Any) -> None:
        print(f"\nReceived signal {signum}, shutting down gracefully...")
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # 6. Run live loop
    try:
        logs, summary = run_live_to_govee(
            multi_adapter=multi_adapter,
            duration_seconds=None,
            sample_rate=sample_rate,
            channels=channels,
            device=audio_device,
            frame_size=frame_size,
            hop_size=hop_size,
            telemetry_interval_seconds=1.0,
            blocksize=blocksize,
            director_config=director_config,
            half_time=half_time,
            max_brightness=max_brightness,
            auto_cycle=auto_cycle,
            cycle_interval=cycle_interval,
            debug_mood=debug_mood,
            stop_event=stop_event,
            telemetry_dir=telemetry_dir,
            profile=profile,
            effect_cycler_override=effect_cycler,
            capture_pipeline=capture_pipeline,
        )
    finally:
        # 7. Cleanup
        if capture_pipeline is not None:
            flushed = capture_pipeline.flush()
            if flushed:
                print(f"Capture: flushed final song → {flushed.name}")
        if spotify_watcher is not None:
            spotify_watcher.stop()
        if health_mon is not None:
            health_mon.stop()
        if profile_watcher is not None:
            profile_watcher.stop()
        if watcher is not None:
            watcher.stop()
        multi_adapter.deactivate()
        signal.signal(signal.SIGINT, original_sigint)
        signal.signal(signal.SIGTERM, original_sigterm)

    return summary
