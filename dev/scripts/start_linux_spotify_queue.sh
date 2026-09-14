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
playback_device=""
launcher_args=(--route-mode spotify-queue)

usage() {
    sed -n '2,11p' "$0"
    exit 2
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --config) config_path="${2:?--config requires a path}"; shift 2 ;;
        --physical-sink|--browser-profile|--audio-source)
            launcher_args+=("$1" "${2?option requires a value}"); shift 2 ;;
        --playback-device) playback_device="${2:?--playback-device requires an ID or pick}"; shift 2 ;;
        --browser|--browser-url)
            launcher_args+=(--audio-source browser "$1" "${2?option requires a value}"); shift 2 ;;
        --spotify-command)
            launcher_args+=(--audio-source spotify-desktop "$1" "${2:?option requires a value}"); shift 2 ;;
        -h|--help) usage ;;
        *) echo "Unknown option: $1" >&2; usage ;;
    esac
done

if [[ ! -f "$config_path" ]]; then
    echo "DreamSync device config not found: $config_path" >&2
    exit 1
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
