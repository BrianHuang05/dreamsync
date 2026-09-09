# Linux ALSA Loopback for Spotify Queue Capture

## Status

Implementation exists and manual acceptance testing is in progress. The
physical USB playback monitor and the ALSA Loopback endpoints have passed
stereo tone tests. End-to-end Spotify capture, compilation, and delayed replay
remain to be validated.

## Validated Host Topology (2026-09-09)

The Linux host is a Surface Pro 6 running PipeWire. Reliable remote shell
access has been established independently of RDP:

- OpenSSH and `tailscaled` are enabled at boot.
- SSH through the Surface's Tailscale address succeeds with a dedicated key.
- Cold-boot SSH succeeds before graphical login.
- Suspend/hibernate is disabled, Wi-Fi autoconnect is enabled, and Tailscale
  key expiry is disabled for this trusted unattended machine.

Validated playback sink:

```text
alsa_output.usb-Generic_AB13X_USB_Audio_20210726905926-00.analog-stereo
```

Validated USB playback-monitor source:

```text
alsa_output.usb-Generic_AB13X_USB_Audio_20210726905926-00.analog-stereo.monitor
```

Both USB monitor channels passed a 48 kHz stereo tone test.

Validated ALSA Loopback endpoints after loading `snd_aloop`:

```text
Playback sink:
alsa_output.platform-snd_aloop.0.dreamsync-playback

Preferred capture source:
alsa_output.platform-snd_aloop.0.dreamsync-playback.monitor

Additional device input exposed by PipeWire:
alsa_input.platform-snd_aloop.0.dreamsync-capture
```

DreamSync should prefer the playback sink's `.monitor` source. This is the
source proven to carry the PCM written to the selected Loopback playback sink.

PortAudio currently exposes physical PipeWire playback through its generic
`pulse` output rather than listing the USB sink by name. It was device ID `6`
during the last inspection, but numeric PortAudio IDs are boot-dependent and
must be rediscovered before being persisted.

### Critical Remote-Safety Finding

Changing the global PipeWire/Pulse default sink during an RDP session caused
the Surface to become unreachable through both RDP and SSH until physical
recovery. Do not run `pactl set-default-sink` during remote testing.

The unattended launcher must ultimately route streams individually:

```text
Spotify process  --PULSE_SINK--> ALSA Loopback playback sink
FFmpeg capture   --explicit source--> Loopback playback monitor
DreamSync player --PULSE_SINK--> USB physical sink
```

The existing launcher still temporarily changes the desktop default sink to
launch the source application. Harden that launcher to use per-process routing
before performing another unattended or RDP-based acceptance run.

## Resume Testing Instructions

Use SSH, not RDP, for the next test session. Keep the graphical RDP session
disconnected while changing or inspecting audio routes.

1. Reconfirm the recovery channel from Windows:

   ```powershell
   tailscale ping <surface-tailscale-name>
   ssh surface-linux
   ```

2. On Linux, confirm that PipeWire and ALSA Loopback survived the most recent
   boot:

   ```bash
   systemctl --user is-active pipewire pipewire-pulse wireplumber
   lsmod | grep snd_aloop
   pactl list short sinks
   pactl list short sources
   ```

   Stop if `snd_aloop`, the `dreamsync-playback` sink, or its `.monitor`
   source is missing. Do not substitute a microphone or physical USB input.

3. Re-list DreamSync/PortAudio devices and record the current ID of the
   generic `pulse` output:

   ```bash
   .venv/bin/python -m dreamsync devices --json
   ```

4. Before an end-to-end remote run, inspect and harden
   `dev/scripts/start_linux_dreamsync.sh`: remove the temporary global-default
   switch used to launch Spotify. Launch only the Spotify/browser subprocess
   with `PULSE_SINK` set to the Loopback playback sink, and launch only the
   DreamSync subprocess with `PULSE_SINK` set to the USB sink. Preserve the
   explicit Loopback `.monitor` capture source.

5. Add or update automated launcher tests proving that Queue startup never
   calls `pactl set-default-sink` and supplies distinct per-process capture and
   playback targets. Run the focused Linux routing/launcher tests before any
   manual audio run.

6. Run the Queue workflow over SSH. While Spotify is actively playing, inspect
   routing without changing it:

   ```bash
   pactl list short sink-inputs
   pactl list short source-outputs
   wpctl status
   ```

   Expected routing:

   - Spotify sink input -> `dreamsync-playback` Loopback sink.
   - FFmpeg source output <- `dreamsync-playback.monitor`.
   - DreamSync delayed replay sink input -> USB physical sink.

7. Capture at least two complete Spotify tracks. Verify that boundaries create
   MP3 and JSON sidecar files, that the MP3s are non-silent, and that delayed
   replay reaches only the USB sink. Spotify must not be directly audible on
   the USB sink during capture.

8. Stop the session cleanly and verify that the desktop default sink was never
   changed by comparing `pactl get-default-sink` before and after the run.

## Problem

The current Linux Spotify Queue route creates `dreamsync_queue_capture` with
PipeWire/PulseAudio `module-null-sink`. The sink and its monitor source work:
FFmpeg can send a test tone into the sink and record non-silent samples from
the monitor.

Spotify Desktop is incompatible with this topology. When Spotify is routed to
the null sink, it displays **"Can't play the current song"**, emits no useful
PCM, and the Queue capture monitor records digital silence (`-91 dB`). The
same Spotify client works normally when routed to the real HDA output. This
rules out DreamSync's encoder, FFmpeg capture, PipeWire monitor discovery, and
RDP audio forwarding as the primary cause.

The existing Linux Queue design is intentionally capture-only: it prevents
Spotify from being heard immediately and lets DreamSync replay the completed,
compiled track later. Switching Queue to the audible `live-learning` route
would defeat that intentional delay and is not an acceptable resolution.

## Why ALSA Loopback Is Needed

Windows uses VB-Cable, which presents a normal virtual playback device to
Spotify and exposes a paired recording endpoint to DreamSync. The current
PipeWire null sink is not behaviorally equivalent: it is an unbacked virtual
sink with no paired device-level capture endpoint.

Linux's `snd-aloop` (ALSA Loopback) is the intended equivalent. It provides a
virtual ALSA sound card with paired playback and capture endpoints. PipeWire
can expose its playback side as a selectable output for Spotify and its paired
capture side as a source for FFmpeg/DreamSync, with no path to physical
speakers during capture.

```text
Spotify Desktop
  -> PipeWire sink backed by ALSA Loopback playback endpoint
  -> paired ALSA Loopback capture endpoint
  -> DreamSync FFmpeg capture -> MP3 -> compile
  -> delayed Queue replay -> selected physical output
```

This preserves the Queue model: Spotify is not directly audible while its
track is captured and compiled; only DreamSync's later Queue replay reaches
the physical output.

## Scope and Constraints

- Change Linux `spotify-queue` routing only.
- Preserve Windows VB-Cable behavior and Linux `live-learning` behavior.
- Do not retain `module-null-sink` as the Queue capture source.
- Do not replay live captured PCM to speakers.
- Keep Queue replay on a selected physical output and reject capture endpoints
  as replay targets.
- Do not load a kernel module, install packages, or persist a system startup
  configuration without explicit user authorization.
- Preserve unrelated working-tree changes.

## Existing Implementation to Review

- `dev/scripts/setup_linux_pipewire_capture.sh`
- `dev/scripts/start_linux_dreamsync.sh`
- `dev/scripts/start_linux_spotify_queue.sh`
- `src/dreamsync/audio/route.py`
- `src/dreamsync/capture/ffmpeg_device.py`
- GUI capture-source discovery, settings, and help text
- Linux routing and launcher tests under `dev/tests/`
- `dev/plans/linux-spotify-queue-audio-port.md`
- `dev/plans/linux-pipewire-capture-port.md`

The important existing distinction is:

| Mode | Current route | Required outcome |
| --- | --- | --- |
| `live-learning` | Physical-backed PipeWire route | Immediate audible source audio |
| `spotify-queue` | Capture-only null sink | Replace with ALSA Loopback-backed capture without immediate audible audio |

## Phase 1: Manual ALSA Loopback Proof of Concept

Do not modify production code until the host proves that Spotify accepts an
ALSA Loopback-backed route.

1. Check whether ALSA Loopback is already available:

   ```bash
   aplay -l
   arecord -l
   lsmod | grep snd_aloop
   ```

2. If it is unavailable, stop and request authorization before loading it:

   ```bash
   sudo modprobe snd-aloop
   ```

3. Discover the actual PipeWire/Pulse endpoint names. Do not assume names or
   numeric IDs:

   ```bash
   pactl list short sinks
   pactl list short sources
   wpctl status
   ```

4. Identify the Loopback playback sink and its paired capture source. Send a
   test tone to the playback sink using FFmpeg's Pulse **`-device`** output
   option; the output filename is only a stream name and does not select a
   sink:

   ```bash
   ffmpeg -hide_banner -re \
     -f lavfi -i sine=frequency=440:sample_rate=48000 \
     -t 20 -ac 2 -ar 48000 \
     -f pulse -device <loopback-playback-sink> \
     "DreamSync loopback test tone"
   ```

5. Record the paired capture source to WAV and inspect it with
   `volumedetect`. It must be non-silent.

6. Route Spotify Desktop to the Loopback playback sink. Confirm that Spotify
   plays without its current-song error and that the paired capture source has
   non-silent samples.

If Spotify still fails with an ALSA Loopback-backed endpoint, stop and report
the evidence rather than implementing speculative routing changes.

## Phase 2: Queue Route Implementation

Replace the Linux Queue route setup with an ALSA Loopback-backed route that:

1. Detects the required Loopback playback and paired capture endpoints.
2. Fails clearly when `snd-aloop` or its PipeWire endpoints are unavailable.
3. Routes only the Spotify Desktop/browser stream launched by DreamSync to the
   Loopback playback sink.
4. Restores the user's physical default sink immediately after launching the
   source app.
5. Supplies the paired Loopback capture source to `CaptureOrchestrator` and
   FFmpeg.
6. Keeps Queue replay on the configured physical playback device only.
7. Cleans up only temporary DreamSync route state; it must not unload the
   shared ALSA Loopback kernel device on ordinary Queue shutdown.

Do not automatically run `modprobe`. A missing kernel module should result in
an actionable diagnostic explaining the manual prerequisite.

## Phase 3: GUI, Settings, and Documentation

Update the GUI to:

- enumerate and persist the exact Loopback capture source;
- label it as the Queue capture input, distinct from Queue replay output;
- preserve the advanced manual-source override;
- display a useful unavailable-Loopback diagnostic.

Update help and README documentation to distinguish:

- Windows Queue: VB-Cable;
- Linux Queue: ALSA Loopback-backed capture;
- Linux Live Learning: existing physical-backed PipeWire route.

Document persistent `snd-aloop` loading only after the manually validated path
works, and present it as an explicit system-administration choice.

## Phase 4: Automated Tests

Add or update focused coverage for:

- Queue route resolution rejects a null-sink-only configuration.
- ALSA Loopback endpoint discovery resolves exact paired playback/capture
  endpoints.
- The Queue launcher supplies the Loopback playback target and capture source.
- Queue replay rejects both Loopback endpoints as output targets.
- Missing `snd-aloop`, playback endpoint, or capture source reports an
  actionable error.
- Existing Windows and `live-learning` behavior remains unchanged.
- Setup, teardown, and restoration of the desktop default sink remain safe.

Run focused tests first, then relevant broader routing, capture, GUI, and
session coverage.

## Phase 5: Manual Acceptance Test

Perform the following over RDP without relying on forwarded audible audio:

1. Start the Queue launcher and verify the ALSA Loopback route exists.
2. Confirm Spotify targets the Loopback playback sink and plays without error.
3. Record the paired capture source to WAV and verify non-silent
   `volumedetect` results.
4. Start DreamSync capture and capture a complete Spotify track.
5. Verify the generated MP3 is non-silent with `volumedetect`.
6. Confirm the MP3 compiles and the Queue replay uses only the chosen physical
   output.
7. Confirm no direct Spotify audio reaches physical speakers during capture.
8. Stop cleanly and confirm the normal desktop default output is restored.

## Definition of Done

- Spotify Desktop can play into the Linux Queue capture route without error.
- DreamSync records non-silent Queue MP3s from that route.
- Queue retains its intentional capture/compile/replay delay.
- Spotify is not directly audible during Queue capture.
- Windows VB-Cable and Linux Live Learning behavior are unchanged.
- Documentation, automated tests, and a safe post-validation startup setup
  path are included.
