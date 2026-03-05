"""FFmpeg DirectShow device discovery for audio capture."""

from __future__ import annotations

import logging
import re
import subprocess

logger = logging.getLogger(__name__)


def discover_audio_device(pattern: str = "CABLE Output") -> str | None:
    """Discover a DirectShow audio device whose name matches *pattern*.

    Spawns ``ffmpeg -list_devices`` and parses the stderr output to find
    audio devices.  Returns the full device name (e.g.
    ``"CABLE Output (VB-Audio Virtual Cable)"``) or ``None`` if no match.
    """
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-list_devices", "true",
                "-f", "dshow",
                "-i", "dummy",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        logger.error("ffmpeg not found on PATH")
        return None
    except subprocess.TimeoutExpired:
        logger.error("ffmpeg device listing timed out")
        return None

    # FFmpeg writes the device list to stderr and always exits non-zero
    # when using -i dummy, so we ignore returncode.
    return _parse_device_list(result.stderr, pattern)


def _parse_device_list(stderr: str, pattern: str) -> str | None:
    """Extract the first audio device name matching *pattern* from FFmpeg output.

    FFmpeg DirectShow device listing format (stderr)::

        [dshow @ ...] DirectShow video devices ...
        [dshow @ ...]  "Video Device"
        ...
        [dshow @ ...] DirectShow audio devices ...
        [dshow @ ...]  "Microphone (Realtek)"
        [dshow @ ...]  "CABLE Output (VB-Audio Virtual Cable)"

    We scan only the audio section and return the first device whose name
    contains *pattern* (case-insensitive).
    """
    in_audio_section = False
    device_re = re.compile(r'\[dshow\s*@\s*[^\]]+\]\s+"([^"]+)"')
    pattern_lower = pattern.lower()

    for line in stderr.splitlines():
        if "DirectShow audio devices" in line:
            in_audio_section = True
            continue
        if in_audio_section and "DirectShow video devices" in line:
            # Unlikely ordering, but guard against it.
            break

        if in_audio_section:
            m = device_re.search(line)
            if m:
                device_name = m.group(1)
                if pattern_lower in device_name.lower():
                    logger.info("Discovered audio device: %s", device_name)
                    return device_name

    logger.warning("No audio device matching %r found", pattern)
    return None
