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
    probe_packets: int = 100,
    probe_rate: float = 5.0,
    director_config: DirectorConfig | None = None,
) -> dict[str, Any]:
    """Run an infinite DreamSync session from a YAML config.

    Returns a summary dict when the session ends (via signal or error).
    """
    # 1. Load YAML config
    configs = load_device_config(config_path)
    print(f"Loaded {len(configs)} device(s) from {config_path}")

    # 2. Probe devices
    print("Probing devices...")
    detected = detect_all_devices(configs, num_packets=probe_packets, rate_hz=probe_rate)
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

    # 4. Signal handling
    stop_event = threading.Event()
    original_sigint = signal.getsignal(signal.SIGINT)
    original_sigterm = signal.getsignal(signal.SIGTERM)

    def _handle_signal(signum: int, frame: Any) -> None:
        print(f"\nReceived signal {signum}, shutting down gracefully...")
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # 5. Run live loop
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
        )
    finally:
        # 6. Cleanup
        multi_adapter.deactivate()
        signal.signal(signal.SIGINT, original_sigint)
        signal.signal(signal.SIGTERM, original_sigterm)

    return summary
