"""Linux queue-route contracts that can run without a PipeWire daemon."""

from pathlib import Path

import pytest

from dreamsync.audio.route import AudioRouteMode, resolve_audio_route


def test_queue_route_is_capture_only_and_uses_its_own_monitor():
    route = resolve_audio_route("spotify-queue", physical_sink="alsa_output.usb_speakers")
    assert route.mode is AudioRouteMode.SPOTIFY_QUEUE
    assert route.browser_sink == "dreamsync_queue_capture"
    assert route.capture_source == "dreamsync_queue_capture.monitor"


def test_live_route_keeps_a_separate_audible_capture_sink():
    route = resolve_audio_route("live-learning")
    assert route.browser_sink == "dreamsync_live_capture"
    assert route.capture_source == "dreamsync_live_capture.monitor"


@pytest.mark.parametrize("target", ["dreamsync_queue_capture", "dreamsync_queue_capture.monitor"])
def test_queue_route_rejects_capture_endpoint_as_playback_target(target):
    with pytest.raises(ValueError, match="physical device"):
        resolve_audio_route("spotify-queue", physical_sink=target)


def test_pipewire_helper_uses_null_sink_for_queue_without_slave():
    script = Path("dev/scripts/setup_linux_pipewire_capture.sh").read_text(encoding="utf-8")
    queue_block = script.split('if [[ "$mode" == "live-learning" ]]', 1)[1]
    assert "module-null-sink" in queue_block
    assert 'sink_name="$browser_sink" rate=48000 channels=2' in queue_block
    assert 'sinks="$physical_sink"' not in queue_block.split("else", 1)[1]
