#!/usr/bin/env bash
# Start the complete DreamSync desktop-session route, audio source, and application.
#
# Usage:
#   ./dev/scripts/start_linux_dreamsync.sh [options] -- <DreamSync command...>
#
# Example:
#   ./dev/scripts/start_linux_dreamsync.sh \
#     --audio-source spotify-desktop \
#     -- python -m dreamsync gui --config devices.yaml
#
# Or launch Spotify Web in Firefox:
#   ./dev/scripts/start_linux_dreamsync.sh \
#     --browser-url https://open.spotify.com/ \
#     -- python -m dreamsync gui --config devices.yaml

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"
browser="firefox"
browser_url=""
audio_source="browser"
spotify_command="spotify"
physical_sink=""
route_mode="spotify-queue"

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
        --audio-source)
            audio_source="${2:?--audio-source requires browser or spotify-desktop}"
            if [[ "$audio_source" != "browser" && "$audio_source" != "spotify-desktop" ]]; then
                echo "--audio-source must be browser or spotify-desktop" >&2
                exit 2
            fi
            shift 2
            ;;
        --spotify-command)
            spotify_command="${2:?--spotify-command requires a command}"
            shift 2
            ;;
        --physical-sink)
            physical_sink="${2:?--physical-sink requires a sink name}"
            shift 2
            ;;
        --route-mode)
            route_mode="${2:?--route-mode requires live-learning or spotify-queue}"
            if [[ "$route_mode" != "live-learning" && "$route_mode" != "spotify-queue" ]]; then
                echo "--route-mode must be live-learning or spotify-queue" >&2
                exit 2
            fi
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

# Desktop autostart does not activate the project's virtual environment, and
# Ubuntu commonly has ``python3`` but no ``python`` command. Let the documented
# ``-- python -m dreamsync ...`` form reliably use this checkout's venv.
if [[ "$1" == "python" || "$1" == "python3" ]]; then
    venv_python="$repo_root/.venv/bin/python"
    if [[ ! -x "$venv_python" ]]; then
        echo "DreamSync virtual environment not found: $venv_python" >&2
        echo "Create it first with: python3 -m venv .venv && .venv/bin/pip install -e '.[gui,session]'" >&2
        exit 1
    fi
    set -- "$venv_python" "${@:2}"
fi

setup_args=("$route_mode")
if [[ -n "$physical_sink" ]]; then
    setup_args+=("$physical_sink")
fi
"$script_dir/setup_linux_pipewire_capture.sh" "${setup_args[@]}"
route_active=true
audio_source_pid=""
cleanup() {
    if [[ -n "${audio_source_pid:-}" ]] && kill -0 "$audio_source_pid" 2>/dev/null; then
        # The audio source runs in its own session, so this only closes the
        # process tree started by this launcher (not an unrelated desktop app).
        kill -- "-$audio_source_pid" 2>/dev/null || kill "$audio_source_pid" 2>/dev/null || true
        wait "$audio_source_pid" 2>/dev/null || true
    fi
    audio_source_pid=""
    if [[ "${route_active:-false}" == true ]]; then
        "$script_dir/setup_linux_pipewire_capture.sh" --teardown || true
    fi
}
trap cleanup EXIT INT TERM

# New audio-source streams inherit this sink. This replaces the pavucontrol
# Playback selection for the normal cold-boot case.
if [[ "$route_mode" == "live-learning" ]]; then
    browser_sink="dreamsync_live_capture"
else
    browser_sink="dreamsync_queue_capture"
fi
desktop_default="$(pactl get-default-sink)"
pactl set-default-sink "$browser_sink"

if [[ "$audio_source" == "spotify-desktop" ]]; then
    if ! command -v "$spotify_command" >/dev/null; then
        echo "Spotify Desktop command not found: $spotify_command" >&2
        echo "Install Spotify Desktop or pass --spotify-command /path/to/spotify." >&2
        exit 1
    fi
    setsid "$spotify_command" >/dev/null 2>&1 &
    audio_source_pid=$!
    echo "Spotify Desktop audio will route to DreamSync Capture."
else
    if ! command -v "$browser" >/dev/null; then
        echo "Browser command not found: $browser" >&2
        exit 1
    fi
    if [[ -n "$browser_url" ]]; then
        setsid "$browser" --new-window "$browser_url" >/dev/null 2>&1 &
    else
        setsid "$browser" --new-window >/dev/null 2>&1 &
    fi
    audio_source_pid=$!
    echo "Firefox/new browser audio will route to DreamSync Capture."
fi

if command -v pavucontrol >/dev/null; then
    setsid pavucontrol >/dev/null 2>&1 &
    echo "Opening pavucontrol to verify Playback and Recording routes."
else
    echo "pavucontrol is not installed; install it to inspect audio routing." >&2
fi

echo "Starting DreamSync: $*"
# Restore the desktop default immediately; only the audio-source stream
# launched above inherits the temporary DreamSync target.
pactl set-default-sink "$desktop_default" 2>/dev/null || true
cd "$repo_root"
"$@"
