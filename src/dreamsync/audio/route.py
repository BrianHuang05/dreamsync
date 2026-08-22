"""Platform-neutral descriptions and validation for capture/playback routes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AudioRouteMode(str, Enum):
    LIVE_LEARNING = "live-learning"
    SPOTIFY_QUEUE = "spotify-queue"


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
) -> AudioRoute:
    """Resolve the stable Linux endpoint names for *mode*.

    A caller can still specify ``capture_source`` for advanced/non-Linux
    routing, but queue mode never permits that source to be its output sink.
    """
    normalized = AudioRouteMode(mode)
    browser_sink = (
        "dreamsync_live_capture"
        if normalized is AudioRouteMode.LIVE_LEARNING
        else "dreamsync_queue_capture"
    )
    route = AudioRoute(
        mode=normalized,
        capture_source=capture_source or f"{browser_sink}.monitor",
        physical_sink=physical_sink,
        browser_sink=browser_sink,
    )
    route.validate_playback_target(physical_sink)
    return route
