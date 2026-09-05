"""Linux ALSA Loopback Queue-route contracts without a PipeWire daemon."""

from pathlib import Path

import pytest

from dreamsync.audio.route import (
    AlsaLoopbackUnavailableError,
    AudioRouteMode,
    discover_alsa_loopback_endpoints,
    resolve_audio_route,
)


def test_queue_route_uses_exact_alsa_loopback_endpoints():
    route = resolve_audio_route(
        "spotify-queue",
        physical_sink="alsa_output.usb_speakers",
        loopback_playback_sink="alsa_output.platform-snd_aloop.0.analog-stereo",
        capture_source="alsa_input.platform-snd_aloop.1.analog-stereo",
    )
    assert route.mode is AudioRouteMode.SPOTIFY_QUEUE
    assert route.browser_sink == "alsa_output.platform-snd_aloop.0.analog-stereo"
    assert route.capture_source == "alsa_input.platform-snd_aloop.1.analog-stereo"


def test_queue_route_rejects_a_null_sink_only_configuration(monkeypatch):
    monkeypatch.setattr("dreamsync.audio.route.sys.platform", "linux")
    with pytest.raises(ValueError, match="null-sink-only"):
        resolve_audio_route(
            "spotify-queue",
            loopback_playback_sink="dreamsync_queue_capture",
            capture_source="dreamsync_queue_capture.monitor",
        )


def test_live_route_keeps_a_separate_audible_capture_sink():
    route = resolve_audio_route("live-learning")
    assert route.browser_sink == "dreamsync_live_capture"
    assert route.capture_source == "dreamsync_live_capture.monitor"


@pytest.mark.parametrize(
    "target",
    ["alsa_output.platform-snd_aloop.0.analog-stereo", "alsa_input.platform-snd_aloop.1.analog-stereo"],
)
def test_queue_route_rejects_loopback_endpoint_as_playback_target(target):
    with pytest.raises(ValueError, match="physical device"):
        resolve_audio_route(
            "spotify-queue",
            physical_sink=target,
            loopback_playback_sink="alsa_output.platform-snd_aloop.0.analog-stereo",
            capture_source="alsa_input.platform-snd_aloop.1.analog-stereo",
        )


def test_alsa_loopback_discovery_uses_exact_pipewire_names(monkeypatch):
    def endpoints(kind: str, **_kwargs):
        return {
            "sinks": ("alsa_output.platform-snd_aloop.0.analog-stereo",),
            "sources": ("alsa_input.platform-snd_aloop.1.analog-stereo",),
        }[kind]

    monkeypatch.setattr("dreamsync.audio.route._list_pulse_endpoint_names", endpoints)
    route = discover_alsa_loopback_endpoints()
    assert route.playback_sink == "alsa_output.platform-snd_aloop.0.analog-stereo"
    assert route.capture_source == "alsa_input.platform-snd_aloop.1.analog-stereo"


def test_alsa_loopback_discovery_requires_explicit_choice_when_ambiguous(monkeypatch):
    monkeypatch.setattr(
        "dreamsync.audio.route._list_pulse_endpoint_names",
        lambda kind, **_kwargs: (
            "alsa_output.platform-snd_aloop.0.analog-stereo",
            "alsa_output.platform-snd_aloop.2.analog-stereo",
        ) if kind == "sinks" else ("alsa_input.platform-snd_aloop.1.analog-stereo",),
    )
    with pytest.raises(AlsaLoopbackUnavailableError, match="Multiple ALSA Loopback playback sink"):
        discover_alsa_loopback_endpoints()


def test_pipewire_helper_uses_loopback_for_queue_and_not_a_null_sink():
    script = Path("dev/scripts/setup_linux_pipewire_capture.sh").read_text(encoding="utf-8")
    queue_block = script.split('if [[ "$mode" == "live-learning" ]]', 1)[1]
    assert "require_alsa_loopback" in queue_block
    assert "resolve_loopback_endpoint sinks" in queue_block
    assert "resolve_loopback_endpoint sources" in queue_block
    assert "module-null-sink" not in queue_block.split("else", 1)[1]
    assert "modprobe" not in queue_block
