"""Platform-specific audio-source discovery for FFmpeg capture."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import logging
import re
import subprocess
import sys

from dreamsync.ffmpeg import resolve_ffmpeg

logger = logging.getLogger(__name__)


class CaptureBackend(str, Enum):
    DIRECTSHOW = "dshow"
    PULSE = "pulse"


@dataclass(frozen=True)
class PulseSource:
    index: int
    name: str
    sample_format: str = ""
    channels: int | None = None
    sample_rate: int | None = None
    state: str = ""

    @property
    def is_monitor(self) -> bool:
        return self.name.lower().endswith(".monitor")


@dataclass(frozen=True)
class CaptureDevice:
    backend: CaptureBackend
    name: str
    sample_format: str = ""
    channels: int | None = None
    sample_rate: int | None = None
    state: str = ""
    match_rule: str = "explicit"


class CaptureDiscoveryError(RuntimeError):
    """A capture backend could not find one safe source to use."""


def capture_backend(platform: str | None = None) -> CaptureBackend:
    """Return the supported capture backend for *platform*."""
    return CaptureBackend.DIRECTSHOW if (platform or sys.platform) == "win32" else CaptureBackend.PULSE


def default_capture_pattern(platform: str | None = None) -> str:
    return "CABLE Output" if capture_backend(platform) is CaptureBackend.DIRECTSHOW else "dreamsync_live_capture.monitor"


def discover_capture_device(
    pattern: str | None = None,
    *,
    platform: str | None = None,
    allow_auto_select: bool = False,
) -> CaptureDevice:
    """Resolve a safe capture source for the active platform."""
    backend = capture_backend(platform)
    pattern = pattern or default_capture_pattern(platform)
    if backend is CaptureBackend.DIRECTSHOW:
        name = discover_audio_device(pattern)
        if name is None:
            raise CaptureDiscoveryError(
                f"No DirectShow audio device matching {pattern!r} found."
            )
        return CaptureDevice(backend=backend, name=name, match_rule="substring")
    source, rule = select_pulse_source(list_pulse_sources(), pattern, allow_auto_select=allow_auto_select)
    return CaptureDevice(
        backend=backend, name=source.name, sample_format=source.sample_format,
        channels=source.channels, sample_rate=source.sample_rate, state=source.state,
        match_rule=rule,
    )


def list_pulse_sources(timeout: float = 5.0) -> list[PulseSource]:
    """List PulseAudio/PipeWire sources using the Pulse compatibility API."""
    try:
        result = subprocess.run(
            ["pactl", "list", "short", "sources"], capture_output=True,
            text=True, timeout=timeout, check=False,
        )
    except FileNotFoundError as exc:
        raise CaptureDiscoveryError(
            "Pulse capture is unavailable: pactl was not found. Install PipeWire's PulseAudio tools."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise CaptureDiscoveryError("Pulse source listing timed out; is PipeWire/PulseAudio running?") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown pactl error"
        raise CaptureDiscoveryError(f"Pulse source listing failed: {detail}")
    return parse_pulse_sources(result.stdout)


def parse_pulse_sources(output: str) -> list[PulseSource]:
    """Parse ``pactl list short sources`` output without depending on locale."""
    sources: list[PulseSource] = []
    spec_re = re.compile(r"^(\S+)\s+(\d+)ch\s+(\d+)Hz$")
    for line in output.splitlines():
        columns = line.split("\t")
        if len(columns) < 2:
            logger.debug("Ignoring malformed pactl source row: %r", line)
            continue
        try:
            index = int(columns[0])
        except ValueError:
            logger.debug("Ignoring pactl source row with non-numeric index: %r", line)
            continue
        sample_format = columns[3] if len(columns) > 3 else ""
        match = spec_re.match(sample_format)
        sources.append(PulseSource(
            index=index, name=columns[1], sample_format=sample_format,
            channels=int(match.group(2)) if match else None,
            sample_rate=int(match.group(3)) if match else None,
            state=columns[4] if len(columns) > 4 else "",
        ))
    return sources


def select_pulse_source(
    sources: list[PulseSource], pattern: str,
    *, allow_auto_select: bool = False,
) -> tuple[PulseSource, str]:
    """Select a Pulse source, rejecting ambiguous and microphone fallbacks."""
    if not sources:
        raise CaptureDiscoveryError("No Pulse sources found; is PipeWire/PulseAudio running?")
    exact = [source for source in sources if source.name == pattern]
    if exact:
        return exact[0], "exact"
    folded = [source for source in sources if source.name.lower() == pattern.lower()]
    if folded:
        return folded[0], "case-insensitive exact"
    matches = [source for source in sources if pattern.lower() in source.name.lower()]
    if len(matches) == 1:
        return matches[0], "substring"
    if len(matches) > 1:
        raise _ambiguous_source_error(pattern, matches)
    monitors = [source for source in sources if source.is_monitor]
    if allow_auto_select and len(monitors) == 1:
        return monitors[0], "single monitor auto-select"
    available = ", ".join(source.name for source in sources)
    raise CaptureDiscoveryError(
        f"No Pulse source matching {pattern!r}. Available Pulse sources: {available}"
    )


def _ambiguous_source_error(pattern: str, sources: list[PulseSource]) -> CaptureDiscoveryError:
    return CaptureDiscoveryError(
        f"Pulse source pattern {pattern!r} is ambiguous: " + ", ".join(source.name for source in sources)
    )


def discover_audio_device(pattern: str = "CABLE Output") -> str | None:
    """Discover a DirectShow audio device whose name matches *pattern*."""
    ffmpeg = resolve_ffmpeg()
    if ffmpeg is None:
        logger.error("ffmpeg not found on PATH")
        return None
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError:
        logger.error("ffmpeg not found on PATH")
        return None
    except subprocess.TimeoutExpired:
        logger.error("ffmpeg device listing timed out")
        return None
    return _parse_device_list(result.stderr, pattern)


def _parse_device_list(stderr: str, pattern: str) -> str | None:
    """Extract the first DirectShow audio device name matching *pattern*."""
    in_audio_section = False
    device_re = re.compile(r'\[dshow\s*@\s*[^\]]+\]\s+"([^"]+)"')
    flat_re = re.compile(r'^\[[^\]]+\]\s+"([^"]+)"\s+\(audio\)\s*$')
    pattern_lower = pattern.lower()
    has_section_headers = "DirectShow audio devices" in stderr
    for line in stderr.splitlines():
        if has_section_headers:
            if "DirectShow audio devices" in line:
                in_audio_section = True
                continue
            if in_audio_section and "DirectShow video devices" in line:
                break
            if in_audio_section:
                match = device_re.search(line)
                if match and pattern_lower in match.group(1).lower():
                    return match.group(1)
        else:
            match = flat_re.search(line)
            if match and pattern_lower in match.group(1).lower():
                return match.group(1)
    logger.warning("No audio device matching %r found", pattern)
    return None
