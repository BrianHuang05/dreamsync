"""Platform-neutral descriptions and validation for capture/playback routes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
import re
import subprocess
import sys


class AudioRouteMode(str, Enum):
    LIVE_LEARNING = "live-learning"
    SPOTIFY_QUEUE = "spotify-queue"


@dataclass(frozen=True)
class AlsaLoopbackEndpoints:
    """The PipeWire/Pulse endpoints exposed by one ALSA Loopback card."""

    playback_sink: str
    capture_source: str


class AlsaLoopbackUnavailableError(RuntimeError):
    """The manually provisioned ``snd-aloop`` route cannot be used."""


_LOOPBACK_NAME = re.compile(r"(?:snd[_-]?aloop|alsa[_-]?loopback|loopback)", re.IGNORECASE)


def is_alsa_loopback_endpoint(name: str) -> bool:
    """Whether a PipeWire/Pulse endpoint name identifies ALSA Loopback."""
    return bool(_LOOPBACK_NAME.search(name))


def _list_pulse_endpoint_names(kind: str, *, timeout: float = 5.0) -> tuple[str, ...]:
    """List exact Pulse endpoint names without depending on a locale."""
    try:
        result = subprocess.run(
            ["pactl", "list", "short", kind],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise AlsaLoopbackUnavailableError(
            "ALSA Loopback Queue capture needs pactl and PipeWire's PulseAudio "
            "server. Install PipeWire PulseAudio utilities, then try again."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise AlsaLoopbackUnavailableError(
            "PipeWire/PulseAudio did not respond while looking for ALSA Loopback endpoints."
        ) from exc
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown pactl error"
        raise AlsaLoopbackUnavailableError(
            f"Could not list PipeWire/PulseAudio {kind}: {detail}"
        )
    return tuple(
        columns[1]
        for line in result.stdout.splitlines()
        if len(columns := line.split("\t")) >= 2
    )


def discover_alsa_loopback_endpoints(
    *,
    playback_sink: str | None = None,
    capture_source: str | None = None,
) -> AlsaLoopbackEndpoints:
    """Find one paired ALSA Loopback playback/capture route.

    PipeWire names are host-specific, so callers must never manufacture them.
    When more than one Loopback endpoint is exposed, require exact explicit
    names rather than guessing which card/device pair Spotify should use.
    """
    sinks = _list_pulse_endpoint_names("sinks")
    sources = _list_pulse_endpoint_names("sources")
    sink_matches = tuple(name for name in sinks if is_alsa_loopback_endpoint(name))
    source_matches = tuple(name for name in sources if is_alsa_loopback_endpoint(name))

    def choose(kind: str, requested: str | None, available: tuple[str, ...]) -> str:
        if requested:
            if requested not in available:
                raise AlsaLoopbackUnavailableError(
                    f"Configured ALSA Loopback {kind} {requested!r} was not found. "
                    f"Available Loopback {kind}s: {', '.join(available) or '(none)'}."
                )
            return requested
        if len(available) == 1:
            return available[0]
        if not available:
            raise AlsaLoopbackUnavailableError(
                f"No ALSA Loopback {kind} is exposed through PipeWire/PulseAudio. "
                "Verify snd-aloop is already loaded with `aplay -l` and `arecord -l`; "
                "do not run modprobe automatically."
            )
        raise AlsaLoopbackUnavailableError(
            f"Multiple ALSA Loopback {kind}s were found: {', '.join(available)}. "
            f"Select the exact {kind} explicitly."
        )

    return AlsaLoopbackEndpoints(
        playback_sink=choose("playback sink", playback_sink, sink_matches),
        capture_source=choose("capture source", capture_source, source_matches),
    )


@dataclass(frozen=True)
class AudioRoute:
    """The three endpoints involved in a DreamSync capture route.

    ``browser_sink`` and ``capture_source`` name Pulse/PipeWire endpoints on
    Linux. ``physical_sink`` is deliberately a string because PipeWire sink
    names are not PortAudio output-device ids.
    """

    mode: AudioRouteMode
    capture_source: str
    physical_sink: str | None = None
    browser_sink: str | None = None

    def validate_playback_target(self, playback_target: str | None) -> None:
        """Reject an attempt to replay a queue item into its capture route."""
        if not playback_target:
            return
        forbidden = {value for value in (self.browser_sink, self.capture_source) if value}
        if playback_target in forbidden or playback_target.endswith(".monitor") and playback_target[:-8] in forbidden:
            raise ValueError(
                "Queue playback output must be a physical device, not the DreamSync "
                "capture sink or monitor."
            )


def resolve_audio_route(
    mode: AudioRouteMode | str = AudioRouteMode.LIVE_LEARNING,
    *,
    capture_source: str | None = None,
    physical_sink: str | None = None,
    loopback_playback_sink: str | None = None,
    discover_loopback: bool = False,
) -> AudioRoute:
    """Resolve the stable Linux endpoint names for *mode*.

    A caller can still specify ``capture_source`` for advanced/non-Linux
    routing, but queue mode never permits that source to be its output sink.
    """
    normalized = AudioRouteMode(mode)
    if normalized is AudioRouteMode.SPOTIFY_QUEUE and discover_loopback:
        endpoints = discover_alsa_loopback_endpoints(
            playback_sink=loopback_playback_sink or os.environ.get("DREAMSYNC_ALOOP_PLAYBACK_SINK"),
            capture_source=capture_source or os.environ.get("DREAMSYNC_ALOOP_CAPTURE_SOURCE"),
        )
        browser_sink = endpoints.playback_sink
        resolved_capture_source = endpoints.capture_source
    else:
        browser_sink = "dreamsync_live_capture" if normalized is AudioRouteMode.LIVE_LEARNING else loopback_playback_sink
        resolved_capture_source = capture_source
        if normalized is AudioRouteMode.SPOTIFY_QUEUE and not browser_sink and sys.platform == "win32":
            # Queue capture is supplied by VB-Cable on Windows; this name is
            # retained only for the route validation contract, not used as a
            # Pulse endpoint by the Windows capture backend.
            browser_sink = "dreamsync_queue_capture"
        if normalized is AudioRouteMode.SPOTIFY_QUEUE and not browser_sink:
            raise ValueError(
                "Spotify Queue requires an ALSA Loopback playback sink. Start the Linux "
                "Queue launcher or resolve the exact Loopback endpoints first."
            )
        if normalized is AudioRouteMode.SPOTIFY_QUEUE and sys.platform != "win32":
            if not is_alsa_loopback_endpoint(browser_sink):
                raise ValueError(
                    "Spotify Queue rejects a null-sink-only route; its playback target must "
                    "be an ALSA Loopback PipeWire endpoint."
                )
            if resolved_capture_source and not is_alsa_loopback_endpoint(resolved_capture_source):
                raise ValueError(
                    "Spotify Queue rejects a null-sink-only route; its capture source must "
                    "be the paired ALSA Loopback PipeWire endpoint."
                )
    route = AudioRoute(
        mode=normalized,
        capture_source=resolved_capture_source or f"{browser_sink}.monitor",
        physical_sink=physical_sink,
        browser_sink=browser_sink,
    )
    route.validate_playback_target(physical_sink)
    return route
