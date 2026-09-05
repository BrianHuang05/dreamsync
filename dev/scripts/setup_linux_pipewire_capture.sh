#!/usr/bin/env bash
# Create or remove DreamSync's PipeWire capture route for the desktop session.
#
# Usage:
#   ./dev/scripts/setup_linux_pipewire_capture.sh [live-learning|spotify-queue] [physical-sink-name]
#   ./dev/scripts/setup_linux_pipewire_capture.sh --teardown
#
# Run this as the logged-in desktop user, never through sudo. If the optional
# physical sink name is omitted, the helper remembers the last physical sink
# used and falls back to the current default sink on its first run. Queue mode
# requires an already-loaded ALSA Loopback (snd-aloop); this script never loads
# modules or changes system startup configuration.

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

endpoint_exists() {
    local kind="$1"
    local endpoint="$2"
    pactl list short "$kind" | awk -v endpoint="$endpoint" '$2 == endpoint { found = 1 } END { exit !found }'
}

loopback_candidates() {
    local kind="$1"
    pactl list short "$kind" | awk '
        { name = tolower($2) }
        name ~ /(snd[_-]?aloop|alsa[_-]?loopback|loopback)/ { print $2 }
    '
}

require_alsa_loopback() {
    local loaded=false
    if command -v lsmod >/dev/null && lsmod | awk '$1 == "snd_aloop" { found = 1 } END { exit !found }'; then
        loaded=true
    elif command -v aplay >/dev/null && aplay -l 2>/dev/null | grep -qi 'loopback'; then
        loaded=true
    fi
    if [[ "$loaded" != true ]]; then
        echo "ALSA Loopback (snd-aloop) is unavailable; Spotify Queue cannot use a null sink." >&2
        echo "Check: aplay -l; arecord -l; lsmod | grep snd_aloop" >&2
        echo "If it is absent, ask an administrator to run: sudo modprobe snd-aloop" >&2
        echo "DreamSync does not load kernel modules automatically." >&2
        exit 1
    fi
}

resolve_loopback_endpoint() {
    local kind="$1"
    local requested="$2"
    local -a candidates=()
    mapfile -t candidates < <(loopback_candidates "$kind")
    if [[ -n "$requested" ]]; then
        if endpoint_exists "$kind" "$requested"; then
            printf '%s\n' "$requested"
            return
        fi
        echo "Configured ALSA Loopback $kind not found: $requested" >&2
    elif [[ ${#candidates[@]} -eq 1 ]]; then
        printf '%s\n' "${candidates[0]}"
        return
    elif [[ ${#candidates[@]} -eq 0 ]]; then
        echo "No ALSA Loopback $kind endpoint is exposed by PipeWire/PulseAudio." >&2
    else
        echo "Multiple ALSA Loopback $kind endpoints found; select one explicitly." >&2
    fi
    echo "Available Loopback $kind endpoints: ${candidates[*]:-(none)}" >&2
    echo "Inspect exact names with: pactl list short sinks; pactl list short sources; wpctl status" >&2
    exit 1
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

# Remove only routes created by a prior Live Learning run. ALSA Loopback is a
# shared kernel device and must never be unloaded by Queue teardown.
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
    require_alsa_loopback
    browser_sink="$(resolve_loopback_endpoint sinks "${DREAMSYNC_ALOOP_PLAYBACK_SINK:-}")"
    if [[ -z "${DREAMSYNC_ALOOP_CAPTURE_SOURCE:-}" ]] && endpoint_exists sources "$browser_sink.monitor"; then
        # PipeWire commonly exposes snd-aloop device 0 as both a sink and an
        # unrelated input. The monitor belonging to the ALSA-backed sink is
        # the source proven to contain the playback PCM on that topology.
        capture_source="$browser_sink.monitor"
    else
        capture_source="$(resolve_loopback_endpoint sources "${DREAMSYNC_ALOOP_CAPTURE_SOURCE:-}")"
    fi
    printf 'dreamsync_browser_sink=%q\n' "$browser_sink" >> "$state_file"
    printf 'dreamsync_capture_source=%q\n' "$capture_source" >> "$state_file"
fi

echo "DreamSync PipeWire route is ready."
echo "  Mode:     $mode"
echo "  Browser:  $browser_sink"
echo "  Capture:  ${capture_source:-$browser_sink.monitor}"
echo "  Playback: $physical_sink"
echo
echo "Verify with: pactl list short sinks; pactl list short sources"
