# Linux simultaneous capture and playback routing fixes

Implemented October 4, 2026, from committed baseline `8dbb07d`
(`feat: unify audio routing configuration`). The tracked working tree was clean
before these changes.

## Identifiers and Ubuntu behavior

Keep these three identifier types separate:

| Identifier | Example | How DreamSync uses it |
| --- | --- | --- |
| Browser/Spotify sink-input index | `51` | Discover the current application streams on every watcher pass; never persist it. |
| Pulse sink name and runtime index | `alsa_output.usb-…analog-stereo`, currently `42` | Persist the endpoint name. Resolve its current index for comparisons with sink inputs. Move streams by destination name. |
| PortAudio device index | `6`, currently the ALSA `pulse` adapter | Rediscover the Pulse output when opening a player; this is not a Pulse sink index. |

The documented browser behavior concerns streams being recreated with new
IDs. A physical sink name normally remains stable for the same hardware and
profile; it does not need replacing whenever a browser stream changes.
The runtime numeric index of a sink can change if the server recreates the
endpoint, so comparisons resolve that name again each pass. Changing hardware
or profiles can also change endpoint names; an unavailable saved physical
output must be explicitly replaced in Config rather than silently reassigned.

The project's [validated Linux host topology](linux-alsa-loopback-spotify-queue-plan.md)
already distinguishes the USB sink name from PortAudio's generic Pulse adapter
and records that the latter's index must be rediscovered. The
[pactl command reference](https://manpages.debian.org/bookworm/pulseaudio-utils/pactl.1.en.html)
defines a sink-input index as the stream identifier and accepts a sink name or
index as its move destination. PipeWire's
[node property reference](https://docs.pipewire.org/page_man_pipewire-props_7.html)
describes `node.name` as the name used to identify sink/source targets.

## Corrected behavior

1. The launcher's watcher previously compared the numeric sink field returned
   by `pactl list short sink-inputs` with the configured capture sink name.
   They never matched, so correctly attached streams received another move
   request every second. It now resolves the current capture sink index and
   compares two numeric indices. Stream discovery uses the C locale so the
   parser can recognize the `pactl` headings on localized Ubuntu desktops.
   Missing sinks are retried on the next pass. A move failure caused by a
   vanished stream does not prevent routing the remaining source streams.
2. `AudioPlayer` previously honored Linux `PULSE_SINK` only when its numeric
   device argument was absent. An explicit PortAudio argument could bypass
   the named physical output and open hardware or Loopback directly. Whenever
   Linux `PULSE_SINK` is set, the player now opens the currently discovered
   Pulse ALSA output adapter. The Pulse destination has precedence over the
   numeric argument. Missing Pulse support fails explicitly before opening
   an output stream. Without a Linux Pulse route, explicit device selection
   retains its existing behavior. Playback status labels follow the same
   precedence so they report the named physical output actually in use.
3. The daily Queue launcher rejects `--playback-device` with instructions to
   use `--physical-sink NAME`. This prevents a second playback selection that
   conflicts with Config's named physical output. Direct standalone CLI runs
   without `PULSE_SINK` still support numeric IDs and the interactive picker.

The baseline commit already discards saved Linux GUI playback-device indices
and makes the active launcher own the capture source. No additional settings
migration or hardware renaming is required by these fixes. The scripts use
process-scoped routing and do not change the desktop default sink.

## Automated validation

The actual Bash routing functions are exercised with changing server snapshots:
already-correct streams, recreated browser/Spotify streams, recreated sinks,
temporary sink absence, multiple source streams, a vanished stream, and an
unrelated Python playback stream. Player tests cover differing Pulse adapter
indices, numeric overrides, missing Pulse output support, and standalone
explicit-device selection. Launcher execution tests verify rejection of the
legacy Queue playback override before starting the source or DreamSync.

The combined launcher, settings, routing, input, device-picker, and player test
run returned **81 passed, 5 failed**. All five failures are existing
clock/finished-state assertions in `test_show_player.py`. Running that file
against the unmodified player source from `8dbb07d` reproduced the same five
failures (11 passed, 5 failed). The routing checks passed, including all 13
real-launcher/function execution cases. Bash syntax and `git diff --check`
also passed.

## Linux acceptance check

1. Finish the current capture and exit the launcher cleanly. In the active
   desktop session, inspect `pactl list short sinks` and select the exact
   physical output name in Config. Start the updated Queue launcher, or use
   `./dev/scripts/start_linux_spotify_queue.sh --physical-sink NAME`.
2. Play a source track, then inspect `pactl list short sinks`,
   `pactl list sink-inputs`, and `pactl list source-outputs`. Browser/Spotify
   sink inputs must target the Loopback sink, and FFmpeg must record its
   monitor. Once delayed playback starts, Python's playback stream must
   target the physical sink. Resolve the displayed numeric sink IDs through
   the current sink list; they are not saved configuration values.
3. Confirm the saved recording is nonsilent and delayed playback is audible
   while the next track is being captured. Change or restart source playback
   to exercise recreated browser/Spotify streams. Correctly attached streams
   should not produce repeated `Routed …` messages every second.
4. Confirm the named physical output remains selected across those source
   changes. If the output is silent, collect sink-input destinations and
   physical sink/stream mute and volume state while Python is actually
   playing. A nonempty recording verifies capture but does not establish
   the playback stream's destination or audible output.

Automated tests exercise routing decisions without an Ubuntu audio server.
They do not confirm whether these defects caused the reported hardware silence;
the concurrent Linux acceptance check remains necessary for that conclusion.
