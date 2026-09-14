# New-chat prompt: validate Dreamsync capture in xrdp

Read `dev/plans/xrdp-local-audio-capture-validation-plan.md` in full and guide
me through it one checkpoint at a time.

Important context: I am currently working **inside an xrdp Xorg session** on
my Ubuntu Surface, not in the physical GNOME console session. xrdp is the
active GUI route and `DISPLAY` is `:10.0`. The goal is to validate a
Surface-local pipeline launched in that xrdp session:

```text
Spotify Desktop -> DreamSync Live Capture -> AB13X USB Audio physical sink
                   -> DreamSync Live Capture monitor -> Dreamsync recorder
                   -> saved MP3
```

No RDP audio forwarding is required or desired. Do not claim that xrdp shares
or validates an old physical-console GUI session. It now has direct access to
the real AB13X hardware because `brian` was added to the `audio` group and the
machine rebooted.

Prior observations:

- `aplay` and `arecord` now enumerate AB13X in xrdp.
- PipeWire exposes the AB13X `alsa_output...` sink.
- Dreamsync has `dreamsync_live_capture.monitor` saved as its capture source.
- A previous attempted Live Learning test failed: Spotify was still routed
  directly to AB13X, and `dreamsync_live_capture` plus its monitor were IDLE.
  Lights changing and an MP3 being created were not accepted as proof.

Do not make any changes yourself over SSH or RDP; give me commands and wait
for their output. Do not change UFW, Tailscale, SSH, sleep/hibernate masking,
xrdp, or the audio stack. Do not modify the Dreamsync launcher while a capture
test is active.

Require evidence for each link: unmuted sinks, Spotify stream attached to the
DreamSync capture sink, non-silent monitor PCM, Dreamsync recorder attached to
that monitor, and a non-silent saved MP3. Stop and explain any failed
checkpoint before proposing a change. After capture is proven, separately
recommend whether a local wrapper or a tested project-level launcher
improvement is appropriate.
