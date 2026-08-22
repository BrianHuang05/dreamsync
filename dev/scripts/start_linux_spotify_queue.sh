#!/usr/bin/env bash
# Start DreamSync's complete Linux Spotify Queue workflow.
#
# Normal daily use:
#   ./dev/scripts/start_linux_spotify_queue.sh
#
# Optional overrides:
#   ./dev/scripts/start_linux_spotify_queue.sh --config /path/to/devices.yaml \
#       --physical-sink alsa_output.usb-Speakers --playback-device 4

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"
config_path="$repo_root/dev/devices.yaml"
physical_sink=""
playback_device=""
browser="firefox"
browser_url="https://open.spotify.com/"

usage() {
    sed -n '2,11p' "$0"
    exit 2
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --config) config_path="${2:?--config requires a path}"; shift 2 ;;
        --physical-sink) physical_sink="${2:?--physical-sink requires a sink}"; shift 2 ;;
        --playback-device) playback_device="${2:?--playback-device requires an ID or pick}"; shift 2 ;;
        --browser) browser="${2:?--browser requires a command}"; shift 2 ;;
        --browser-url) browser_url="${2:?--browser-url requires a URL}"; shift 2 ;;
        -h|--help) usage ;;
        *) echo "Unknown option: $1" >&2; usage ;;
    esac
done

if [[ ! -f "$config_path" ]]; then
    echo "DreamSync device config not found: $config_path" >&2
    exit 1
fi

launcher_args=(
    --route-mode spotify-queue
    --browser "$browser"
    --browser-url "$browser_url"
)
if [[ -n "$physical_sink" ]]; then
    launcher_args+=(--physical-sink "$physical_sink")
fi

session_args=(
    session
    --config "$config_path"
    --spotify
    --capture
    --pipeline
    --audio-route spotify-queue
)
if [[ -n "$playback_device" ]]; then
    session_args+=(--playback-device "$playback_device")
fi

exec "$script_dir/start_linux_dreamsync.sh" "${launcher_args[@]}" -- \
    python -m dreamsync "${session_args[@]}"
