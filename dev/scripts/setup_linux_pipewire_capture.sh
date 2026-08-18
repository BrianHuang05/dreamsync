#!/usr/bin/env bash
# Create DreamSync's audible PipeWire capture route for the current desktop session.
#
# Usage:
#   ./dev/scripts/setup_linux_pipewire_capture.sh [physical-sink-name]
#
# Run this as the logged-in desktop user, never through sudo.  If the optional
# physical sink name is omitted, the current default sink is used.

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

physical_sink="${1:-$(pactl get-default-sink)}"
if [[ "$physical_sink" == "dreamsync_capture" ]]; then
    echo "The selected output is already DreamSync Capture, not a physical sink." >&2
    echo "Pass the physical sink explicitly; find it with: pactl list short sinks" >&2
    exit 1
fi

if ! pactl list short sinks | awk -v sink="$physical_sink" '$2 == sink { found = 1 } END { exit !found }'; then
    echo "Physical sink not found: $physical_sink" >&2
    echo "Available sinks:" >&2
    pactl list short sinks >&2
    exit 1
fi

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
