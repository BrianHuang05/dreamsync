# New chat prompt: add a new device in the GUI, then validate simultaneous Queue capture/playback

Continue DreamSync work in `C:\Users\brian\dreamsync`. I operate the Linux
Surface Pro desktop myself. Give me exact desktop-terminal commands and GUI
instructions; do not SSH/RDP into the Surface yourself. Inspect the current
code and status before proposing changes. Work through the remaining steps
below in order, fixing concrete failures as they arise.

## Latest result and immediate objective — 2026-10-10

I tested a precompiled show through the GUI on the Surface: **passes with no
notable issues**. Treat this playback path as passed; do not ask me to repeat
the same baseline test. This is a visual/functional observation, not measured
optical latency, proof of every device's reliability, or Queue capture validation.

Next, help me **configure a newly purchased device using DreamSync's GUI**.
Its model/SKU, addresses, intended name and placement have not been supplied.
Then test **simultaneous capture and playback in Queue**, including a second
track transition. Do not reopen unrelated BLE rate investigations first.

## Repository and execution context

- Windows repo: `C:\Users\brian\dreamsync`.
- Surface repo: `/home/brian/~src/dreamsync` (shell: `~/~src/dreamsync`).
- Branch used for this work: `codex/linux-launcher-settings`. Inspect actual
  branch/history on both machines; the Surface has local merge history.
- Remote: `https://github.com/BrianHuang05/dreamsync.git`.
- Latest implementation pushed in this chat: `0c8630e` (host measurements and
  LAN recovery), following `431f564` (rate policy/reporting interfaces).
- Python: `.venv/Scripts/python.exe` on Windows; `.venv/bin/python` on Linux.
- Device config: `dev/devices.yaml`. Preserve existing placements, sections,
  groups, enabled flags, identities, names and inventory notes.
- Inspect local changes before editing, committing or updating Git. Never
  blindly reset, overwrite a full config with a remote version, or apply a
  whole conflict side. Commit/push tested fixes needed on the Surface using
  the established branch; stage only task files.
- A regression test was added in `dev/tests/test_auto_detect.py` that loads
  the real shipped YAML with the production loader and checks BLE caps.
  At handoff it is an uncommitted task change; preserve it. Its test module
  passed all 50 tests. `.pytest-tmp/` is pre-existing untracked test output.

## Resolved startup problem and still-open audio prerequisite

The Surface's YAML contained unresolved **stash conflict markers**, including:
`<<<<<<< Updated upstream`, `max_fps: 3.0`, `=======`, `max_fps: 20.0`,
`>>>>>>> Stashed changes`. Those caused the YAML error around line 463.
The shipped Windows config parsed correctly. A backup-and-repair command was
provided to retain only the agreed 3 Hz rate and preserve other edits. GUI
precompiled playback now passes; do not diagnose that old parse error again
unless it actually recurs. Validate the real config after any new edit, not
only mocked fixtures or Python compilation.

The full Linux launcher previously failed with **no ALSA Loopback endpoint
exposed by PipeWire/PulseAudio**. The user saw an `alsa_output...` sink marked
`SUSPENDED`. The route helper selects Loopback by endpoint name, not sink state;
a physical ALSA output is not necessarily an ALSA Loopback endpoint. No output
proving this Queue prerequisite repaired has been supplied.

The direct GUI launch supplied to bypass that launcher error was:

```bash
cd ~/~src/dreamsync
set -o pipefail
.venv/bin/python -u -m dreamsync gui --config dev/devices.yaml \
  2>&1 | tee desktop-lighting-test.log
```

This does not establish the launcher's Queue capture route. Before Queue testing,
obtain the exact current launcher error, if any, and desktop-user outputs:

```bash
pactl list short sinks
pactl list short sources
cat /proc/asound/cards
lsmod | grep snd_aloop
```

Diagnose from those results before choosing endpoint names or changing routing.
Do not run the launcher as root or casually alter PipeWire global policy,
xrdp, Tailscale, firewall rules, or persistent kernel-module configuration.

## Step 1: add the new device through the GUI

Read the current GUI discovery/configuration code before giving button names:
`src/dreamsync/gui/widgets/device_discovery_panel.py`,
`src/dreamsync/gui/services/device_discovery_service.py`,
`src/dreamsync/gui/services/device_service.py`, and relevant handlers in
`src/dreamsync/gui/main_window.py`.

Ask for the new device's model/SKU and intended name/placement; use discovery
results for addresses and only ask for remaining missing facts. Establish
whether it is already paired in Govee Home and whether LAN Control is available.
Use GUI discovery, assignment and save operations first; do not start by
instructing me to hand-edit YAML. If the GUI cannot express a necessary setting,
inspect and fix that concrete limitation, with appropriate validation.

Choose verified LAN when available; use BLE at <=3 Hz if BLE is the confirmed
path. Do not infer protocol, segment count or LAN/BLE pairing from similar names.
One physical device must have one logical config/renderer entry. Confirm its
placement, orientation, group membership, enabled state and saved persistence.
Validate the resulting config and reopen it to verify persistence. Test the
new device's basic power/color/segment behavior and inclusion in playback.
Repeat playback only as needed to validate the newly added device. Do not
overwrite the existing fleet's assignments or silently fall back to simulation.

## Step 2: simultaneous Queue capture/playback

Read `dev/plans/configurable-linux-launcher-settings-plan.md`, especially the
capture and end-to-end gates, plus the current launcher/route scripts. That
plan contains historical context: configurable launcher settings are already
implemented. Do not treat older prompts' branches or implementation TODOs as
current instructions. Adapt old xrdp instructions to the actual local desktop.

Current scripts: `dev/scripts/start_linux_dreamsync.sh`,
`dev/scripts/start_linux_spotify_queue.sh`,
`dev/scripts/setup_linux_pipewire_capture.sh`.
The normal launcher reads saved host settings; CLI overrides take precedence.
Queue capture requires real ALSA Loopback, not the Live Learning null/combine
route. The saved startup screen and external audio route are separate settings.

Prior audio context: the MVP source is Firefox's persistent `DreamSync` profile
with Spotify Web; native Spotify Desktop was unreliable. Spotify Connect's
**Firefox audio output** must be selected manually. Known prior physical output
was AB13X USB Audio, but re-enumerate its current sink rather than assuming its
old name. Existing host-specific preferences belong in saved launcher settings,
not `dev/devices.yaml` or repository defaults.

Proceed through these gates, with evidence before advancing:

1. Resolve the Loopback endpoint prerequisite and launch in `spotify-queue`
   mode. Verify the selected physical playback sink, Loopback playback sink,
   and paired capture monitor/source from the actual session.
2. Start capture with the browser playing. Verify `Signal detected` and one
   independently nonsilent saved recording (e.g. ffmpeg `volumedetect`). File
   size, beat detection and animated lights alone do not prove captured audio.
   If silent, collect complete `pactl list sink-inputs` and
   `pactl list source-outputs` blocks and fix routing before continuing.
3. Let one captured track become a ready compiled show. Keep capture running
   for the next track while playing the ready show through the GUI Queue.
   Source browser audio must remain confined to Loopback capture; DreamSync
   playback must go to the physical sink without feeding back into capture.
   Confirm audible playback, continuing nonsilent capture and lighting output,
   including the newly added device.
4. Exercise a second track boundary and queued-show transition. Verify capture,
   compilation readiness and playback continue without an audio ownership
   conflict, missing/duplicate track or growing backlog. Collect timestamped
   logs and routing evidence if any failure occurs, then fix that failure.
5. Stop via app controls and verify capture/playback workers and owned routes
   stop cleanly. Preserve system defaults and shared Loopback devices.
6. Only after the simultaneous test passes, validate saved settings and the
   normal login/launcher start once if that remains in scope.

## Lighting decisions to preserve

- LAN discovery/firewall issue was resolved; Couch H612F, Blinds H612F and
  Overhead H808A use Razer and were visually validated through 30 Hz.
- Mixed-fleet playback already existed. Production BLE is capped at 3 Hz;
  accepted bulb RGB command remains 0D. Frame caps are not packet budgets.
- Persistent UDP sockets, truthful local errors, async BLE queue waits and
  deadline pacing are implemented. They did not cure higher-rate BLE link loss.
- App health includes host rate, write p95, gaps and errors. Successful UDP
  sends/write-without-response do not confirm reception or optical latency.
- Opt-in automatic mode prefers validated LAN. Its below-4-Hz rule uses
  successful host frame sends by explicit user choice: three contiguous
  >=5-second windows under >=4 Hz demand; exactly 4 Hz passes. It retries
  paired LAN identity while on BLE, using cooldown and three successful probes
  before a LAN trial; failed activation restores BLE ownership.
- Automatic handoff/recovery is not hardware-verified. Existing LAN strips
  remain fixed LAN with no accepted BLE fallback. Couch BLE failed at 3 Hz;
  I explicitly declined more Couch BLE rate testing. Do not request it again.
- Silent loss/device freezes with successful UDP sends remain undetectable.
  True delivery counters and automatic DHCP endpoint refresh are future work,
  not prerequisites for adding the new device and testing Queue.
- LAN is local Wi-Fi, not internet throughput. Ethernet is unavailable due to
  damaged apartment ports. Do not propose Ethernet as the immediate solution.

Reference details: `dev/plans/lighting-testing-new-chat-prompt.md`,
`dev/plans/lighting-remaining-tasks.md`,
`dev/plans/lan-ble-transport-selection.md`,
`dev/plans/output-optimization-results.md`.

Start by inspecting repository status and the existing GUI device workflow,
then obtain the new device's missing identity/placement details. Keep hardware
tests short for iteration, preserve passed gates, and provide one justified
next action at a time rather than another full lighting investigation.
