#!/usr/bin/env bash
# Create DreamSync's audible PipeWire capture route for the current desktop session.
#
# Usage:
#   ./dev/scripts/setup_linux_pipewire_capture.sh [physical-sink-name]
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
state_file="$state_dir/pipewire-capture-sink"
default_sink="$(pactl get-default-sink)"

sink_exists() {
    pactl list short sinks | awk -v sink="$1" '$2 == sink { found = 1 } END { exit !found }'
}

if [[ $# -gt 0 ]]; then
    physical_sink="$1"
elif [[ "$default_sink" != "dreamsync_capture" ]]; then
    physical_sink="$default_sink"
elif [[ -r "$state_file" ]] && sink_exists "$(<"$state_file")"; then
    physical_sink="$(<"$state_file")"
else
    # A prior route can make the virtual sink the Pulse default. Prefer a
    # non-HDMI ALSA output, then any remaining non-DreamSync sink.
    physical_sink="$(pactl list short sinks | awk '$2 != "dreamsync_capture" && $2 ~ /^alsa_output/ && $2 !~ /hdmi/ { print $2; exit }')"
    if [[ -z "$physical_sink" ]]; then
        physical_sink="$(pactl list short sinks | awk '$2 != "dreamsync_capture" { print $2; exit }')"
    fi
fi

if ! sink_exists "$physical_sink"; then
    echo "Physical sink not found: $physical_sink" >&2
    echo "Available sinks:" >&2
    pactl list short sinks >&2
    exit 1
fi

mkdir -p "$state_dir"
printf '%s\n' "$physical_sink" > "$state_file"

# Remove only routes created by a prior run of this helper.  Loopbacks are
# removed before their source sink so PipeWire can tear down cleanly.
while IFS=$'\t' read -r module_id module_name module_args; do
    if [[ "$module_name" == "module-loopback" && "$module_args" == *"source=dreamsync_capture.monitor"* ]]; then
        pactl unload-module "$module_id"
    fi
done < <(pactl list modules short)

while IFS=$'\t' read -r module_id module_name module_args; do
    if [[ "$module_name" =~ ^module-(null|combine)-sink$ && "$module_args" == *"sink_name=dreamsync_capture"* ]]; then
        pactl unload-module "$module_id"
    fi
done < <(pactl list modules short)

# A combine sink is both the app-facing capture endpoint and the audible route
# to the chosen physical sink. Its monitor remains available to DreamSync.
pactl load-module module-combine-sink \
    sink_name=dreamsync_capture \
    sinks="$physical_sink" \
    rate=48000 \
    channels=2 \
    sink_properties=device.description=DreamSync_Capture >/dev/null

echo "DreamSync PipeWire route is ready."
echo "  Apps:    route them to DreamSync Capture"
echo "  Capture: dreamsync_capture.monitor"
echo "  Speakers: $physical_sink"
echo
echo "Verify with: pactl list short sinks; pactl list short sources"
