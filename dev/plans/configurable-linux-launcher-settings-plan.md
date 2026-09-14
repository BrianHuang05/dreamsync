# Plan: Configure the Linux audio launcher from DreamSync GUI settings

## Goal

Make the Linux startup launcher read user-configurable, persisted settings for
the audio source and routing it must establish *before* the GUI starts. Keep
the command line as an explicit diagnostic override. Update the user’s
autostart command to be small and deterministic, then complete Queue-mode
end-to-end validation.

This plan concerns the logged-in Linux desktop session only. It must not add
remote-control behavior, alter xrdp, load kernel modules, or change system
audio defaults outside the existing launcher lifecycle.

## Confirmed starting point

- Branch: `codex/linux-alsa-loopback-spotify-queue`.
- The browser route is implemented and pushed through `ca3ac94`.
- `start_linux_dreamsync.sh` currently defaults to Queue route mode and a
  browser source, starts Firefox with the persistent `DreamSync` profile,
  watches its PipeWire stream, routes it to the capture sink, and runs
  DreamSync with `PULSE_SINK` set to the specified physical output and
  `PULSE_SOURCE` set to the matching capture monitor.
- The GUI persists its own startup Live screen, playback-device, and input
  selection in `~/.dreamsync/gui-settings.json`. It does **not** persist
  launcher route mode, source choice, physical PipeWire sink, browser command,
  profile, URL, or Spotify command.
- Live Learning capture now displays raw-PCM status in the Live toolbar. A
  green-light condition is `Capture audio: signal detected`; file size alone
  is never evidence of audio.

## Design decisions to make before implementation

1. **Settings ownership:** store launcher preferences with GUI settings rather
   than in the repository or `dev/devices.yaml`. They are host/user specific.
2. **No unrestricted shell command:** expose structured fields, not an
   arbitrary shell snippet. An arbitrary command makes quoting, process
   ownership, cleanup, and accidental command execution ambiguous.
3. **Structured source choices:**
   - `browser`: executable, Firefox profile, initial URL;
   - `spotify-desktop`: executable;
   - route mode: `spotify-queue` or `live-learning`;
   - physical PipeWire sink: exact string.
4. **CLI precedence:** explicit launcher flags override persisted settings;
   persisted settings override script defaults. This preserves a clear escape
   hatch for diagnostics.
5. **Persisted values are validated at use time:** the launcher must verify
   its selected physical sink and Queue loopback endpoints against the active
   PipeWire session. It must fail with the existing actionable endpoint list,
   not silently substitute a different device.

## Implementation phases

### 1. Model, migration, and GUI form

1. Add a `LinuxLauncherSettings` model to the existing GUI settings schema:
   `route_mode`, `audio_source`, `physical_sink`, `browser_command`,
   `browser_profile`, `browser_url`, and `spotify_command`.
2. Supply backward-compatible defaults matching the current launcher:
   Queue, browser, Firefox, `DreamSync`, and an empty URL/physical sink.
3. Add a **Linux launcher** group to Config, clearly labeled as settings used
   at the *next launcher start*, not changes to the running audio route.
4. Use a dropdown for source and route mode. Disable irrelevant inputs rather
   than dropping their stored values.
5. Validate locally:
   - executable fields must be non-empty;
   - URL is blank or `http`/`https`;
   - a physical sink is blank or a single PipeWire endpoint name, never an
     arbitrary command;
   - no field is interpolated into shell code.
6. Save only after the existing Save Configuration action succeeds.

### 2. Launcher-readable configuration bridge

The launcher cannot read a Python GUI settings model before it creates the
route unless it has a stable, non-shell configuration format.

1. Add a small, documented launcher settings file under the user config/state
   directory, for example `~/.config/dreamsync/linux-launcher.json`.
2. Have GUI Save Configuration write it atomically with restrictive user-file
   permissions where practical. It should contain only the structured fields
   above.
3. Make `start_linux_dreamsync.sh` parse it using an available deterministic
   parser (prefer `python3`/the project venv; do not `source` an untrusted
   file).
4. Preserve current command-line options. Resolve settings in this order:

   ```text
   explicit CLI flag → launcher settings file → script default
   ```

5. Print a concise startup summary of the resolved source, route, capture
   endpoint, physical sink, browser/profile/URL (excluding secrets), and the
   source of each value when helpful.
6. Do not write settings or alter GUI state from the shell script.

### 3. Queue wrapper and documentation

1. Update `start_linux_spotify_queue.sh` to use the same resolved settings.
   Its current defaults favor Spotify Desktop; this must not override a saved
   browser choice silently.
2. Retain `--browser-url` and `--spotify-command` as explicit overrides.
3. Update README examples for both Live Learning and Queue workflows, making
   clear that the GUI startup screen controls which panel opens while route
   mode controls the external audio topology.
4. Document the one non-automatable step: Spotify Connect’s selected playback
   device is an authenticated Spotify Web Player choice. The launcher opens
   the persistent browser profile but does not impersonate site interaction.

### 4. Automated tests

Add focused tests before changing the production scripts:

1. Settings serialization/deserialization and migration from existing
   `gui-settings.json`.
2. GUI form round-trip, disabled-field behavior, and validation errors.
3. Launcher precedence for every field.
4. Exact command construction for browser and Spotify Desktop—especially
   spaces/quoting in paths and profiles.
5. Rejection of invalid URL, missing executable, absent physical sink, and
   invalid Queue loopback pairing.
6. Confirm no test expects the old Queue wrapper desktop-Spotify default once
   a saved browser choice exists.
7. Keep the existing launcher static/routing tests and add a regression that
   the final DreamSync process receives the resolved `PULSE_SINK` and
   `PULSE_SOURCE`.

## Updated autostart command after implementation

Once the settings file is implemented and saved with Queue/browser/AB13X
values, the boot command should become only:

```bash
cd /home/brian/~src/dreamsync && exec ./dev/scripts/start_linux_dreamsync.sh \
  -- python -m dreamsync gui --config dev/devices.yaml
```

Until that implementation is complete, use the explicit Queue/browser command
below. The `--route-mode` and `--audio-source` flags are current defaults, but
keeping them makes the desired topology visible and prevents a future default
change from changing the boot route:

```bash
cd /home/brian/~src/dreamsync && exec ./dev/scripts/start_linux_dreamsync.sh \
  --route-mode spotify-queue \
  --audio-source browser \
  --browser-url https://open.spotify.com/ \
  --physical-sink 'alsa_output.usb-Generic_AB13X_USB_Audio_20210726905926-00.analog-stereo' \
  -- python -m dreamsync gui --config dev/devices.yaml
```

In the GUI, set **Config → Live startup screen → Queue** and save it. This
selects the initial GUI panel; it does not replace the launcher's routing work.

## Evidence-gated remaining tests

### A. Nonsilent capture gate (before Queue playback)

1. Pull the current branch and restart DreamSync through the explicit Queue
   launcher command above.
2. In the persistent `DreamSync` Firefox profile, ensure Spotify Web Player is
   logged in and Spotify Connect is set to **Firefox audio output**.
3. In the GUI, start capture and play an ordinary track. Capture status must
   become `Signal detected` (Queue Runtime) or, if using Live Learning,
   `Capture audio: signal detected` (Live toolbar).
4. Stop capture after a controlled segment. Run `volumedetect` on the saved
   MP3 once as independent confirmation. It must not report silence.
5. If either check fails, stop here and collect full `pactl list sink-inputs`
   and `pactl list source-outputs`; do not begin Queue playback.

### B. Queue end-to-end gate

1. Confirm the configured Queue capture source is the monitor paired with the
   ALSA Loopback playback sink, not a physical microphone.
2. Start Queue capture with Firefox playing. Verify Firefox's complete
   sink-input block points to the Loopback playback sink and the recorder's
   complete source-output block points to its paired monitor.
3. Allow one track to become a ready compiled show. Verify its MP3 is
   nonsilent before starting Queue playback.
4. Start Queue playback. Confirm its audio routes only to AB13X and that the
   source browser stream remains confined to Loopback capture.
5. Let a second track transition. Verify the first compiled show finishes,
   the next capture begins, no audio ownership conflict is reported, and the
   Queue remains usable.
6. Stop using the app control/launcher `Ctrl+C`; confirm the launcher removes
   only DreamSync route modules and leaves the system defaults intact.

### C. Cold-start test

After A and B pass in the current xrdp session, run the actual boot/login
command once. Confirm the same endpoint names resolve, Firefox launches the
`DreamSync` profile, the GUI opens Queue, and the first capture meets gate A.

## Out of scope

- Automatically clicking or changing Spotify Connect device selection on the
  website.
- SSHing into or programmatically driving the Surface.
- Changing xrdp, GNOME Remote Desktop, Tailscale, firewall, kernel modules,
  or global PipeWire policy.
