#!/usr/bin/env bash
# Start the complete DreamSync desktop-session route, browser, and application.
#
# Usage:
#   ./dev/scripts/start_linux_dreamsync.sh [options] -- <DreamSync command...>
#
# Example:
#   ./dev/scripts/start_linux_dreamsync.sh \
#     --browser-url https://open.spotify.com/ \
#     -- python -m dreamsync gui --config devices.yaml

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
browser="firefox"
browser_url=""
physical_sink=""

usage() {
    sed -n '2,13p' "$0"
    exit 2
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --browser)
            browser="${2:?--browser requires a command}"
            shift 2
            ;;
        --browser-url)
            browser_url="${2:?--browser-url requires a URL}"
            shift 2
            ;;
        --physical-sink)
            physical_sink="${2:?--physical-sink requires a sink name}"
            shift 2
            ;;
        --)
            shift
            break
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage
            ;;
    esac
done

if [[ $# -eq 0 ]]; then
    echo "A DreamSync command is required after --." >&2
    usage
fi

setup_args=()
if [[ -n "$physical_sink" ]]; then
    setup_args+=("$physical_sink")
fi
"$script_dir/setup_linux_pipewire_capture.sh" "${setup_args[@]}"

# New browser streams inherit this sink. This replaces the pavucontrol Playback
# selection for the normal cold-boot case.
pactl set-default-sink dreamsync_capture

if ! command -v "$browser" >/dev/null; then
    echo "Browser command not found: $browser" >&2
    exit 1
fi

if [[ -n "$browser_url" ]]; then
    "$browser" --new-window "$browser_url" >/dev/null 2>&1 &
else
    "$browser" --new-window >/dev/null 2>&1 &
fi

echo "Firefox/new browser audio will route to DreamSync Capture."
echo "Starting DreamSync: $*"
exec "$@"
