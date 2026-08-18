#!/usr/bin/env bash
# Create or remove DreamSync's PipeWire capture route for the desktop session.
#
# Usage:
#   ./dev/scripts/setup_linux_pipewire_capture.sh [live-learning|spotify-queue] [physical-sink-name]
#   ./dev/scripts/setup_linux_pipewire_capture.sh --teardown
#
# Run this as the logged-in desktop user, never through sudo. If the optional
# physical sink name is omitted, the helper remembers the last physical sink
# used and falls back to the current default sink on its first run.

set -euo pipefail

if ! command -v pactl >/dev/null; then
    echo "pactl is required (install PipeWire PulseAudio utilities)." >&2
    exit 1
fi

if ! pactl info >/dev/null 2>&1; then
    echo "Cannot reach the current user's PipeWire/PulseAudio server." >&2
    echo "Run this from a terminal in the graphical desktop session, without sudo." >&2
    exit 1
fi

state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/dreamsync"
state_file="$state_dir/pipewire-route-state"

mode="live-learning"
teardown=false
if [[ "${1:-}" == "--teardown" ]]; then
    teardown=true
    shift
elif [[ "${1:-}" == "live-learning" || "${1:-}" == "spotify-queue" ]]; then
    mode="$1"
    shift
fi

sink_exists() {
    pactl list short sinks | awk -v sink="$1" '$2 == sink { found = 1 } END { exit !found }'
}

if [[ "$teardown" == true ]]; then
    if [[ -r "$state_file" ]]; then
        # shellcheck disable=SC1090
        source "$state_file"
    fi
    while IFS=$'\t' read -r module_id module_name module_args; do
        if [[ "$module_name" =~ ^module-(null|combine)-sink$ && "$module_args" == *"sink_name=dreamsync_"*"_capture"* ]]; then
            pactl unload-module "$module_id" || true
        fi
    done < <(pactl list modules short)
    if [[ -n "${dreamsync_previous_default:-}" ]] && sink_exists "$dreamsync_previous_default"; then
        pactl set-default-sink "$dreamsync_previous_default"
        echo "DreamSync route removed; restored default sink: $dreamsync_previous_default"
    else
        echo "DreamSync route removed."
    fi
    rm -f "$state_file"
    exit 0
fi

default_sink="$(pactl get-default-sink)"
saved_default=""
saved_physical=""
if [[ -r "$state_file" ]]; then
    # State is written by this helper using %q, so it can safely retain sink
    # names containing punctuation or spaces between idempotent setup calls.
    # shellcheck disable=SC1090
    source "$state_file"
    saved_default="${dreamsync_previous_default:-}"
    saved_physical="${dreamsync_physical_sink:-}"
fi
restore_default="$default_sink"
if [[ "$default_sink" == dreamsync_*_capture && -n "$saved_default" ]]; then
    restore_default="$saved_default"
fi
if [[ $# -gt 0 ]]; then
    physical_sink="$1"
elif [[ "$default_sink" != dreamsync_*_capture ]]; then
    physical_sink="$default_sink"
elif [[ -n "$saved_physical" ]] && sink_exists "$saved_physical"; then
    physical_sink="$saved_physical"
else
    # A prior route can make the virtual sink the Pulse default. Prefer a
    # non-HDMI ALSA output, then any remaining non-DreamSync sink.
    physical_sink="$(pactl list short sinks | awk '$2 !~ /^dreamsync_.*_capture$/ && $2 ~ /^alsa_output/ && $2 !~ /hdmi/ { print $2; exit }')"
    if [[ -z "$physical_sink" ]]; then
        physical_sink="$(pactl list short sinks | awk '$2 !~ /^dreamsync_.*_capture$/ { print $2; exit }')"
    fi
fi

if ! sink_exists "$physical_sink"; then
    echo "Physical sink not found: $physical_sink" >&2
    echo "Available sinks:" >&2
    pactl list short sinks >&2
    exit 1
fi

mkdir -p "$state_dir"
printf 'dreamsync_previous_default=%q\n' "$restore_default" > "$state_file"
printf 'dreamsync_physical_sink=%q\n' "$physical_sink" >> "$state_file"

# Remove only routes created by a prior run of this helper.  Loopbacks are
# removed before their source sink so PipeWire can tear down cleanly.
while IFS=$'\t' read -r module_id module_name module_args; do
    if [[ "$module_name" =~ ^module-(null|combine)-sink$ && "$module_args" == *"sink_name=dreamsync_"*"_capture"* ]]; then
        pactl unload-module "$module_id" || true
    fi
done < <(pactl list modules short)

if [[ "$mode" == "live-learning" ]]; then
    browser_sink="dreamsync_live_capture"
    pactl load-module module-combine-sink \
        sink_name="$browser_sink" sinks="$physical_sink" rate=48000 channels=2 \
        sink_properties=device.description=DreamSync_Live_Capture >/dev/null
else
    browser_sink="dreamsync_queue_capture"
    # A null sink has a monitor source but no physical-sink slave: browser
    # audio is capture-only until DreamSync replays the completed track.
    pactl load-module module-null-sink \
        sink_name="$browser_sink" rate=48000 channels=2 \
        sink_properties=device.description=DreamSync_Queue_Capture >/dev/null
fi

echo "DreamSync PipeWire route is ready."
echo "  Mode:     $mode"
echo "  Browser:  $browser_sink"
echo "  Capture:  $browser_sink.monitor"
echo "  Playback: $physical_sink"
echo
echo "Verify with: pactl list short sinks; pactl list short sources"
