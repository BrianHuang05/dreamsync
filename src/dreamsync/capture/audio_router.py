"""Toggle Windows audio routing to/from VB-Audio Virtual Cable.

Provides start/stop control so the Director can route system audio
through the virtual cable only during active capture sessions.
"""

import json
import subprocess
import sys
from pathlib import Path

ROUTER_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "audio-capture" / "audio_router.ps1"


def _run_router(action: str) -> str:
    """Run the PowerShell audio router script and return its output."""
    if not ROUTER_SCRIPT.exists():
        raise FileNotFoundError(f"Audio router script not found: {ROUTER_SCRIPT}")

    result = subprocess.run(
        [
            "powershell",
            "-ExecutionPolicy", "Bypass",
            "-File", str(ROUTER_SCRIPT),
            action,
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"audio_router.ps1 {action} failed (rc={result.returncode}): "
            f"{result.stderr.strip()}"
        )
    return result.stdout.strip()


def start() -> str:
    """Route system audio to CABLE Input. Returns status message."""
    return _run_router("start")


def stop() -> str:
    """Restore previous default audio device. Returns status message."""
    return _run_router("stop")


def status() -> str:
    """Return current routing status."""
    return _run_router("status")


def is_active() -> bool:
    """Check if audio routing is currently active."""
    state_file = ROUTER_SCRIPT.parent / ".audio_route_state.json"
    return state_file.exists()
