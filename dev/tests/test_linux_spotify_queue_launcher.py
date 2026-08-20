"""Static contracts for the daily-use Linux Spotify Queue launcher."""

from pathlib import Path


def test_queue_launcher_has_one_command_defaults_and_uses_queue_route():
    script = Path("dev/scripts/start_linux_spotify_queue.sh").read_text(encoding="utf-8")
    assert 'config_path="$repo_root/dev/devices.yaml"' in script
    assert "--route-mode spotify-queue" in script
    assert "--audio-route spotify-queue" in script
    assert "--spotify" in script
    assert "--capture" in script
    assert "--pipeline" in script


def test_linux_launcher_closes_the_browser_it_started_during_cleanup():
    script = Path("dev/scripts/start_linux_dreamsync.sh").read_text(encoding="utf-8")

    assert 'browser_pid=""' in script
    assert 'setsid "$browser" --new-window' in script
    assert 'kill -- "-$browser_pid"' in script
