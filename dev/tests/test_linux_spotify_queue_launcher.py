"""Static contracts for the daily-use Linux Spotify Queue launcher."""

from pathlib import Path


def test_queue_launcher_has_one_command_defaults_and_uses_queue_route():
    script = Path("dev/scripts/start_linux_spotify_queue.sh").read_text(encoding="utf-8")
    assert 'config_path="$repo_root/dev/devices.yaml"' in script
    assert "--route-mode spotify-queue" in script
    assert 'launcher_args=(--route-mode spotify-queue)' in script
    assert 'audio_source="spotify-desktop"' not in script
    assert "--audio-route spotify-queue" in script
    assert "--spotify" in script
    assert "--capture" in script
    assert "--pipeline" in script
    assert "ALSA Loopback" in Path("README.md").read_text(encoding="utf-8")


def test_linux_launcher_supports_spotify_desktop_and_closes_its_audio_source():
    script = Path("dev/scripts/start_linux_dreamsync.sh").read_text(encoding="utf-8")

    assert '--audio-source requires browser or spotify-desktop' in script
    assert 'audio_source="browser"' in script
    assert 'browser_profile="DreamSync"' in script
    assert 'spotify_command="spotify"' in script
    assert 'PULSE_SINK="$browser_sink" setsid "$spotify_command"' in script
    assert 'audio_source_pid=""' in script
    assert '--browser-profile requires a Firefox profile name' in script
    assert 'setsid "$browser" --new-instance -P "$browser_profile" --new-window' in script
    assert 'start_audio_source_route_watcher "$(basename "$browser")"' in script
    assert 'pactl move-sink-input "$input_id" "$browser_sink"' in script
    assert 'setsid pavucontrol' in script
    assert 'pavucontrol_pid=""' in script
    assert 'pavucontrol_pid=$!' in script
    assert 'kill -- "-$pavucontrol_pid"' in script
    assert 'pavucontrol is not installed' in script
    assert 'kill -- "-$audio_source_pid"' in script
    assert 'DREAMSYNC_ALOOP_PLAYBACK_SINK' in script
    assert 'DREAMSYNC_ALOOP_CAPTURE_SOURCE' in script


def test_linux_launcher_uses_only_process_scoped_sink_routing():
    launcher = Path("dev/scripts/start_linux_dreamsync.sh").read_text(encoding="utf-8")
    queue_launcher = Path("dev/scripts/start_linux_spotify_queue.sh").read_text(
        encoding="utf-8"
    )
    route_helper = Path("dev/scripts/setup_linux_pipewire_capture.sh").read_text(
        encoding="utf-8"
    )

    assert "pactl set-default-sink" not in launcher
    assert "pactl set-default-sink" not in queue_launcher
    assert "pactl set-default-sink" not in route_helper
    assert 'PULSE_SINK="$browser_sink" setsid "$spotify_command"' in launcher
    assert 'setsid "$browser" --new-instance -P "$browser_profile" --new-window' in launcher
    assert 'PULSE_SINK="$physical_sink" PULSE_SOURCE="$capture_source" "$@"' in launcher
    assert 'PULSE_SOURCE="$capture_source"' in launcher


def test_queue_launcher_keeps_capture_and_playback_targets_distinct():
    script = Path("dev/scripts/start_linux_dreamsync.sh").read_text(encoding="utf-8")

    assert 'expected_capture_source="$browser_sink.monitor"' in script
    assert 'export DREAMSYNC_ALOOP_CAPTURE_SOURCE=' in script
    assert (
        '[[ "$DREAMSYNC_ALOOP_CAPTURE_SOURCE" != "$expected_capture_source" ]]'
        in script
    )
    assert '[[ "$physical_sink" == "$browser_sink" ||' in script
    assert '"$physical_sink" == "$DREAMSYNC_ALOOP_CAPTURE_SOURCE" ]]' in script
    assert 'PULSE_SINK="$browser_sink"' in script
    assert 'PULSE_SINK="$physical_sink" PULSE_SOURCE="$capture_source" "$@"' in script
    assert 'PULSE_SOURCE="$capture_source"' in script
    assert 'PULSE_SINK="$DREAMSYNC_ALOOP_CAPTURE_SOURCE"' not in script
