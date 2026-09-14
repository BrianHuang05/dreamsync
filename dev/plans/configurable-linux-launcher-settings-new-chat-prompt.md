# New chat prompt: configurable Linux launcher settings and Queue validation

Work in `C:\Users\brian\dreamsync`. Do not SSH into, RDP into, or otherwise
operate the remote Linux Surface. Provide the user with evidence-gated terminal
instructions for commands that must run there.

## Objective

Implement configurable Linux launcher settings in DreamSync’s GUI, update the
Linux boot/login launcher command accordingly, and prepare/execute only the
next justified Queue-mode validation step.

Read `dev/plans/configurable-linux-launcher-settings-plan.md` in full first.
Treat it as the scope and acceptance criteria.

## Current state

- Branch: `codex/linux-alsa-loopback-spotify-queue`.
- Recent pushed commits:
  - `6841930 fix: route Linux browser audio to capture sink`
  - `ca40579 feat: launch DreamSync Firefox profile`
  - `a80b381 feat: show live capture signal status`
  - `ca3ac94 fix: show capture signal in Live Learning`
- Browser routing has now passed preliminary user testing.
- The persistent Firefox profile is named `DreamSync`.
- Spotify Web Player must be selected manually in Spotify Connect as **Firefox
  audio output**; do not attempt browser automation for this authenticated UI
  choice.
- AB13X USB Audio is a real USB audio device used as the desired physical
  analog output. Its known current PipeWire sink is:

  ```text
  alsa_output.usb-Generic_AB13X_USB_Audio_20210726905926-00.analog-stereo
  ```

- Native Spotify Desktop playback is unreliable in this xrdp/local setup. The
  browser route is the MVP path, but retain Spotify Desktop as a configurable
  fallback.
- Direct monitor capture and real DreamSync capture have been proven
  nonsilent only after Firefox was routed to the DreamSync virtual sink. Do
  not infer audio validity from MP3 file size, beat detection, or lighting
  animation.

## Required design constraints

1. Launcher settings are host/user-specific. Do not put physical sink names,
   browser commands, or URLs in `dev/devices.yaml` or repository defaults.
2. The launcher runs before the GUI and therefore needs a safe,
   launcher-readable settings bridge. Do not source a shell settings file or
   execute a user-provided arbitrary command string.
3. Model structured values: route mode, audio-source kind, physical sink,
   browser executable/profile/URL, and Spotify executable.
4. Explicit CLI flags override saved launcher settings, which override current
   script defaults.
5. Preserve and test the current behavior: Firefox is launched in its own
   `DreamSync` profile, its active sink-input is watched and moved to the
   capture sink, and the final DreamSync process receives `PULSE_SINK` for the
   physical output plus `PULSE_SOURCE` for the matching monitor.
6. Do not alter xrdp, Tailscale, UFW, GNOME remote desktop, PipeWire global
   policy, or `snd_aloop` module loading.

## User-facing execution rules

- Explain every Linux-side command before asking the user to run it.
- Do not propose the next test until the previous test has positive evidence.
- Prefer SSH instructions over xrdp instructions when either can run a
  terminal-only diagnostic, but never SSH yourself.
- Preserve existing untracked plan files and unrelated user changes.
- Use `apply_patch` for repository edits, run targeted tests, inspect the diff,
  commit only task files, and push only after the user asks.

## Current explicit Queue boot command, until settings are implemented

```bash
cd /home/brian/~src/dreamsync && exec ./dev/scripts/start_linux_dreamsync.sh \
  --route-mode spotify-queue \
  --audio-source browser \
  --browser-url https://open.spotify.com/ \
  --physical-sink 'alsa_output.usb-Generic_AB13X_USB_Audio_20210726905926-00.analog-stereo' \
  -- python -m dreamsync gui --config dev/devices.yaml
```

The GUI must separately have **Config → Live startup screen → Queue** saved.
That setting determines the visible panel; it does not configure external
PipeWire routing.

## First action

Inspect the existing GUI settings model, persistence service, GUI Config form,
and both launcher scripts. Produce a concise implementation plan tied to the
repository’s actual code before making changes. Then implement the plan in
small, tested commits when authorized.
