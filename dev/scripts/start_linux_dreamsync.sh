#!/usr/bin/env bash
# Start the complete DreamSync desktop-session route, audio source, and application.
#
# Usage:
#   ./dev/scripts/start_linux_dreamsync.sh [options] -- <DreamSync command...>
#
# Example:
#   ./dev/scripts/start_linux_dreamsync.sh \
#     --audio-source spotify-desktop \
#     -- python -m dreamsync gui --config dev/devices.yaml
#
# Or launch Spotify Web in Firefox:
#   ./dev/scripts/start_linux_dreamsync.sh \
#     --browser-url https://open.spotify.com/ \
#     -- python -m dreamsync gui --config dev/devices.yaml

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
audio_source_watcher_pid=""
launcher_pid="$$"

sink_input_ids_for_process() {
    local process_binary="$1"
    pactl list sink-inputs | awk -v process_binary="$process_binary" '
        /^Sink Input #[0-9]+/ {
            input_id = $3
            sub(/^#/, "", input_id)
        }
        /application\.process\.binary =/ {
            binary = $0
            sub(/.*= "/, "", binary)
            sub(/".*/, "", binary)
            if (binary == process_binary) {
                print input_id
            }
        }
    '
}

route_audio_source_streams() {
    local process_binary="$1"
    local input_id=""
    local current_sink=""
    while IFS= read -r input_id; do
        [[ -n "$input_id" ]] || continue
        current_sink="$(pactl list short sink-inputs | awk -v id="$input_id" '$1 == id { print $2; exit }')"
        if [[ -n "$current_sink" && "$current_sink" != "$browser_sink" ]]; then
            pactl move-sink-input "$input_id" "$browser_sink"
            echo "Routed ${process_binary} sink input ${input_id} to DreamSync Capture."
        fi
    done < <(sink_input_ids_for_process "$process_binary")
}

start_audio_source_route_watcher() {
    local process_binary="$1"
    (
        while kill -0 "$launcher_pid" 2>/dev/null; do
            route_audio_source_streams "$process_binary" || true
            sleep 1
        done
    ) &
    audio_source_watcher_pid=$!
}

cleanup() {
    if [[ -n "${audio_source_watcher_pid:-}" ]] && kill -0 "$audio_source_watcher_pid" 2>/dev/null; then
        kill "$audio_source_watcher_pid" 2>/dev/null || true
        wait "$audio_source_watcher_pid" 2>/dev/null || true
    fi
    audio_source_watcher_pid=""
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

# The route helper records the exact physical playback sink it validated.
# Source this state without changing the desktop's global default sink.
state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/dreamsync"
state_file="$state_dir/pipewire-route-state"
if [[ ! -r "$state_file" ]]; then
    echo "DreamSync route state was not created: $state_file" >&2
    exit 1
fi
# Written by setup_linux_pipewire_capture.sh using %q.
# shellcheck disable=SC1090
source "$state_file"
physical_sink="${dreamsync_physical_sink:-}"
if [[ -z "$physical_sink" ]]; then
    echo "DreamSync route has no physical playback sink." >&2
    exit 1
fi

# Route only the launched audio-source process to the capture sink. DreamSync
# itself receives a separate PULSE_SINK below for delayed physical playback.
if [[ "$route_mode" == "live-learning" ]]; then
    browser_sink="dreamsync_live_capture"
    capture_source="${browser_sink}.monitor"
else
    browser_sink="${dreamsync_browser_sink:-}"
    if [[ -z "$browser_sink" ]]; then
        echo "DreamSync Queue route has no ALSA Loopback playback sink." >&2
        exit 1
    fi
    export DREAMSYNC_ALOOP_PLAYBACK_SINK="$browser_sink"
    export DREAMSYNC_ALOOP_CAPTURE_SOURCE="${dreamsync_capture_source:-}"
    if [[ -z "$DREAMSYNC_ALOOP_CAPTURE_SOURCE" ]]; then
        echo "DreamSync Queue route has no ALSA Loopback capture source." >&2
        exit 1
    fi
    expected_capture_source="$browser_sink.monitor"
    if [[ "$DREAMSYNC_ALOOP_CAPTURE_SOURCE" != "$expected_capture_source" ]]; then
        echo "DreamSync Queue capture must use the playback sink monitor: $expected_capture_source" >&2
        echo "Resolved capture source was: $DREAMSYNC_ALOOP_CAPTURE_SOURCE" >&2
        exit 1
    fi
    if [[ "$physical_sink" == "$browser_sink" || "$physical_sink" == "$DREAMSYNC_ALOOP_CAPTURE_SOURCE" ]]; then
        echo "DreamSync Queue playback must use a physical sink distinct from Loopback capture." >&2
        exit 1
    fi
    capture_source="$DREAMSYNC_ALOOP_CAPTURE_SOURCE"
fi

if [[ "$audio_source" == "spotify-desktop" ]]; then
    if ! command -v "$spotify_command" >/dev/null; then
        echo "Spotify Desktop command not found: $spotify_command" >&2
        echo "Install Spotify Desktop or pass --spotify-command /path/to/spotify." >&2
        exit 1
    fi
    PULSE_SINK="$browser_sink" setsid "$spotify_command" >/dev/null 2>&1 &
    audio_source_pid=$!
    start_audio_source_route_watcher "$(basename "$spotify_command")"
    echo "Spotify Desktop audio will route to DreamSync Capture."
else
    if ! command -v "$browser" >/dev/null; then
        echo "Browser command not found: $browser" >&2
        exit 1
    fi
    if [[ -n "$browser_url" ]]; then
        PULSE_SINK="$browser_sink" setsid "$browser" --new-window "$browser_url" >/dev/null 2>&1 &
    else
        PULSE_SINK="$browser_sink" setsid "$browser" --new-window >/dev/null 2>&1 &
    fi
    audio_source_pid=$!
    start_audio_source_route_watcher "$(basename "$browser")"
    echo "Firefox/new browser audio will route to DreamSync Capture."
fi

if command -v pavucontrol >/dev/null; then
    setsid pavucontrol >/dev/null 2>&1 &
    echo "Opening pavucontrol to verify Playback and Recording routes."
else
    echo "pavucontrol is not installed; install it to inspect audio routing." >&2
fi

echo "Starting DreamSync: $*"
cd "$repo_root"
# Keep delayed Queue replay on the validated physical sink. The explicit Pulse
# source makes DreamSync's reactive reader follow the same monitor as FFmpeg
# instead of silently falling back to the desktop microphone.
PULSE_SINK="$physical_sink" PULSE_SOURCE="$capture_source" "$@"
