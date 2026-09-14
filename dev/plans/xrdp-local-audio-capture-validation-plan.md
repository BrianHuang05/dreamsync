# Plan: Validate Dreamsync Audio Capture in the Active xrdp Session

## Purpose and scope

This plan validates a **Surface-local audio pipeline launched from the active
xrdp desktop**. It does **not** claim to control, share, or validate a
pre-existing physical GNOME-console session.

The current desktop is xrdp's separate Xorg session (`DISPLAY=:10.0`). It can
nevertheless use the real Surface audio hardware after `brian` was granted
membership in the existing `audio` group and the machine was rebooted. The
validated signal path must remain entirely on the Surface:

```text
Spotify Desktop (xrdp-launched process)
  -> DreamSync Live Capture combine sink
     -> AB13X USB Audio physical sink
     -> DreamSync Live Capture monitor source
        -> Dreamsync recorder
        -> saved MP3
```

Windows RDP audio playback/recording is out of scope and must remain disabled
for this test. It neither creates nor validates the local capture path.

## Current confirmed state

- The Surface is Ubuntu 22.04.5; xrdp is active and reachable only over the
  intended Tailscale path.
- Native GNOME Desktop Sharing is disabled/masked; xrdp owns TCP 3389.
- `brian` is in the `audio` group. After reboot, the xrdp session can read and
  write `/dev/snd` and `aplay`/`arecord` enumerate the AB13X USB Audio card.
- PipeWire exposes an `alsa_output...AB13X...` sink in xrdp.
- Dreamsync's GUI capture source was saved as
  `dreamsync_live_capture.monitor`.
- A prior test was **not** a capture pass: the `dreamsync_live_capture` sink
  and monitor were `IDLE`, while Spotify sink-inputs targeted the AB13X sink
  directly. Lighting changes and an MP3 file alone are not evidence of capture.

## Guardrails

- Run every audio command below in an xrdp terminal as `brian`, never with
  `sudo`.
- Do not add PulseAudio/xrdp forwarding modules, change UFW/Tailscale/SSH, or
  alter sleep/hibernate masking.
- Do not change the shared launcher while a capture is running.
- Treat Spotify, PipeWire, and Dreamsync as separate processes. Every link in
  the signal path needs explicit evidence.

## Phase 1: establish the session and endpoints

In the xrdp terminal, record:

```bash
echo "$DISPLAY"
echo "$XDG_SESSION_TYPE"
id -nG
pactl list short sinks
pactl list short sources
```

Expected: `DISPLAY` is `:10.0` (or another xrdp display), the AB13X physical
sink is present, and the capture monitor appears only after the route is made.
A monitor is a **source**, not a sink or sink-input.

Set the exact names discovered above. Do not guess or use `auto_null`:

```bash
PHYSICAL='EXACT_alsa_output_NAME_FOR_AB13X'
CAPTURE_SINK='dreamsync_live_capture'
CAPTURE_SOURCE='dreamsync_live_capture.monitor'
```

## Phase 2: start a clean Live Learning route

Finish any active capture cleanly first. Fully quit Spotify Desktop; merely
closing its window may leave its single-instance process running. Confirm no
Spotify process remains:

```bash
ps -eo pid,comm,args | grep -i '[s]potify'
```

From the actual Dreamsync checkout, start a **Live Learning** run. Replace the
checkout path and physical sink name with the known local values:

```bash
cd /PATH/TO/dreamsync
./dev/scripts/start_linux_dreamsync.sh \
  --route-mode live-learning \
  --audio-source spotify-desktop \
  --physical-sink "$PHYSICAL" \
  -- python -m dreamsync gui --config dev/devices.yaml
```

This intentionally specifies `--route-mode live-learning`: the current script
defaults to `spotify-queue`, which is a different ALSA-loopback workflow.

If Spotify is already alive, the launcher may focus/reuse it rather than start
a process with `PULSE_SINK=dreamsync_live_capture`. In that case it must be
fully exited and relaunched, or its active stream must be deliberately moved.

## Phase 3: prove Spotify is routed into the capture sink

With Spotify playing, collect:

```bash
pactl list short sinks
pactl list short sources
pactl list sink-inputs
pactl list sink-inputs
```

Use the full (non-`short`) `pactl list sink-inputs` output to identify the
Spotify block by `application.name`, `application.process.binary`, or
`media.name`. Its `Sink:` must be the numeric ID assigned to
`dreamsync_live_capture`, not the AB13X physical sink.

Equivalent UI check: on Spotify's pavucontrol Playback row, the selected
output must read **DreamSync Live Capture**, not **AB13X USB Audio Analog
Stereo**.

If Spotify is on the wrong sink, use pavucontrol to select DreamSync Live
Capture, then repeat the checks. Do not continue while it targets AB13X
directly.

Pass criteria:

- `dreamsync_live_capture` is `RUNNING`.
- `dreamsync_live_capture.monitor` is `RUNNING`.
- Spotify's stream targets the capture sink.

## Phase 4: prove the monitor has non-muted PCM

Check the relevant controls:

```bash
pactl get-sink-mute "$PHYSICAL"
pactl get-sink-volume "$PHYSICAL"
pactl get-sink-mute "$CAPTURE_SINK"
pactl get-sink-volume "$CAPTURE_SINK"
pactl get-source-mute "$CAPTURE_SOURCE"
pactl get-source-volume "$CAPTURE_SOURCE"
```

All mute results must be `no`. Record a short independent sample from the
monitor and measure it:

```bash
ffmpeg -hide_banner -f pulse -i "$CAPTURE_SOURCE" -t 10 \
  -c:a pcm_s16le /tmp/dreamsync-monitor-test.wav
ffprobe -hide_banner /tmp/dreamsync-monitor-test.wav
ffmpeg -hide_banner -i /tmp/dreamsync-monitor-test.wav \
  -af volumedetect -f null -
```

Pass: a non-zero duration and `mean_volume`/`max_volume` values other than
`-inf`. This is the proof that Spotify is delivering actual PCM to the monitor;
pavucontrol's visual meters are supplementary only.

## Phase 5: prove Dreamsync consumes and saves that monitor

Stop the temporary FFmpeg probe. In Dreamsync, confirm the selected capture
source remains `dreamsync_live_capture.monitor`, then begin one controlled
capture. While it is recording, run:

```bash
pactl list short source-outputs
pactl list source-outputs
```

A recorder appears under **source-outputs**, not sink-inputs. Identify the
Dreamsync/FFmpeg recorder and confirm its `Source:` is the numeric ID of
`dreamsync_live_capture.monitor`.

Let a complete track finish and locate the final MP3. Validate it:

```bash
ffprobe -hide_banner 'PATH_TO_CAPTURED_MP3'
ffmpeg -hide_banner -i 'PATH_TO_CAPTURED_MP3' -af volumedetect -f null -
```

Final pass criteria:

1. The output MP3 has a non-zero duration.
2. Volume analysis is not silent (`-inf`).
3. Spotify targets the capture sink while recording.
4. Dreamsync's recorder targets the capture monitor.
5. The MP3 audibly contains the expected Spotify segment when played locally.

Only after all five pass should concurrent pipeline playback be tested.

## Phase 6: launcher improvement, after validation

Do not hardcode the AB13X sink in a shared project default. After a successful
validation, decide separately whether to:

1. Create a local, untracked wrapper with this machine's preferred flags; or
2. Change the launcher to persist/configure an explicit route mode and fail
   clearly when an already-running Spotify Desktop process cannot inherit the
   requested `PULSE_SINK`.

The second option is a project behavior change and should have tests. The
desired behavior must be selected deliberately: Live Learning and Spotify
Queue have intentionally different capture semantics.

## Failure handling

- Capture sink or monitor `IDLE`: Spotify is not routed to it; fix the stream
  route before testing Dreamsync.
- Monitor WAV silent: stop; diagnose Spotify stream routing/mute first.
- Monitor WAV non-silent but Dreamsync MP3 silent: diagnose Dreamsync's
  selected source and its source-output attachment.
- MP3 is audible but pipeline playback later fails: keep the verified capture
  evidence and test playback as a separate stage.

## Cleanup

Stop the launcher with `Ctrl+C` after a test. Its cleanup removes only the
temporary Dreamsync capture route. Do not remove xrdp, change RDP audio
settings, or undo the `audio` group membership as part of audio-test cleanup.
