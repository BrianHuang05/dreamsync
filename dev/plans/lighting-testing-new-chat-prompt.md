# Prompt for the next chat

Continue DreamSync LAN/BLE reliability and latency testing. Inspect the current
checkout and relevant source before proposing changes. I run hardware tests
over SSH on a Surface Pro 6; you work in the Windows repository. I will attach
the newest JSON/logs and visual observations after this prompt. Analyze those
first and continue the testing sequence below. Do not treat host send timing as
visible latency, infer a firmware fix without evidence, or ask for sniffing/new
hardware before exhausting existing protocol implementations.

## Repository and workflow

- Windows repository: `C:\Users\brian\dreamsync`.
- Linux working directory shown by my shell: `~/~src/dreamsync`.
- Branch: `codex/linux-launcher-settings`; remote:
  `https://github.com/BrianHuang05/dreamsync.git`.
- Python: `.venv/Scripts/python.exe` on Windows; `.venv/bin/python` on Linux.
- Config: `dev/devices.yaml`. Preserve my local device placement, groups, enabled
  flags and notes. Linux may contain local commits/edits, so `git pull --ff-only`
  can fail. Inspect status/history first; preserve changes before rebasing. Never
  blindly reset or choose an entire conflict side.
- Commit and push tested changes needed for my SSH tests. Report what is and
  isn't hardware-verified. Local automated tests cannot validate the lights.

## Devices

Only these three LAN devices belong in this room/config:

| Device | LAN IP | Segments | Transport | BLE hardware/software | Wi-Fi hardware/software |
|---|---|---|---|---|---|
| Couch H612F | 10.126.166.180 | 12 | razer | 3.05.00 / 1.03.01 | 1.04.01 / 1.06.24 |
| Blinds H612F | 10.126.166.155 | 12 | razer | 3.05.00 / 1.03.01 | 1.04.01 / 1.03.08 |
| Overhead H808A | 10.126.166.156 | 25 | razer | 3.05.00 / 1.00.23 | 1.04.01 / 1.03.08 |

Couch and Blinds previously used ptReal, producing incorrect black-clear/walk
behavior. Both walk correctly with razer; config was changed accordingly.
Overhead also walks correctly at 25 segments. All runtime config caps remain
5 FPS pending stability results.

H6004 at 10.126.166.142 is a newer bulb in ANOTHER ROOM: intentionally excluded.
Its BLE and Wi-Fi hardware are 1.07.02, software 1.01.25. It isn't an H6006.

Counter strip H617A is configured on BLE C7:90:80:C6:44:74, 15 segments; no LAN
response observed. TV backlight H6097 is BLE D7:01:86:46:44:59, 15 segments.
Six H6006 bulbs use protocol `bulb`; fleet-reported hardware 1.02.00, software
1.00.59. Separate radio versions unavailable; no H6006 LAN reply observed.
Bulb addresses/names are in YAML; Floor Lamp is D0:C9:07:95:15:DB.

## Network issue already resolved

Surface Wi-Fi is wlp1s0, IP 10.126.166.154/26, gateway 10.126.166.129. Tailscale
is present but routes to strips go directly through Wi-Fi. LAN discovery and
devStatus appeared completely broken despite successful pings. tcpdump proved
all three strips replied from varying high UDP ports to host UDP 4002. The host
firewall blocked those replies. Allowing incoming UDP 4002 from the local
10.126.166.128/26 subnet on wlp1s0 restored discovery. Do not repeat the claim
that LAN was disabled or unsupported. ARP `DELAY` is not a latency measurement.

Kernel 6.8.0-136-generic, BlueZ 5.64; Marvell USB 1286:204c, hci0
C4:9D:ED:A8:F7:AA. Historical hardware/wakeup errors exist, but haven't been
correlated to BLE test failures. Do not assume the Surface is the root cause.

## Current LAN investigation

Update from user (2026-10-07): all LAN strips visually validated through 30 Hz.
Fixed-5-Hz restart tests passed all devices in all six trials for both legacy and
settled activation. Both ended green; no advantage for settled activation was
demonstrated and the prior startup failure was not reproduced. User wants to
move to BLE; do not repeat these LAN tests. Green at release alone is not evidence
of an in-stream failure; its cause has not been established.

`dev/scripts/test_lan_update_rates.py` measures host UDP send rate/gaps/lateness,
skipped slots and errors, with changing markers. It uses production packet
encoders, an absolute-deadline scheduler and persistent socket. It does NOT
measure delivery or optical latency. The old CLI `govee-test --pattern walk`
always waits 300 ms, so raising its --fps does not provide a rate benchmark.

The first benchmark restarted streaming at EVERY rate: power-on immediately
followed by one enable, then brightness, then frames; disable after the stage.
Visual trials (P/F applies to a whole stage):

| Stage | Trial 1 at 5/10/20/30 Hz | Trial 2 at 5/10/20/30 Hz |
|---|---|---|
| Couch | P F F F | P P F F |
| Blinds | P P F P | P F F F |
| Overhead | P F F F | P F F F |
| Together | P F P F | P F P F |

Together failures at 10/30 particularly leave Couch nonresponsive. A stage tends
to work throughout or fail throughout, not randomly drop individual steps. This
suggests session startup/state may confound frequency. Razer has a documented
enable packet `BB 00 01 B1 01 0A`; its loss, ordering, or delayed prior disable
is a hypothesis, not confirmed.

The latest benchmark revision adds:
- `--session-mode continuous` default: one session per device group across rates.
- `--session-mode restart`: separate session for each rate.
- `--activation settled` default: power, wait, brightness, wait, then three
  enables spaced 200 ms after the whole fleet finishes setup.
- `--activation legacy`: original power/enable/brightness ordering.
- Both modes wait 500 ms after session release; legacy isn't an exact recreation
  of the old zero-cooldown boundary.
- `--trials` and `--rate-order given|reverse|alternate`.
- Trial/session IDs and timestamped setup/release payloads/errors in JSON.
No periodic enables are injected during measurement. No configuration is edited.
Lights run at 30% brightness, streaming is released afterward; prior state is
not restored. Disable other controllers/DreamView before tests.

First run:
```bash
.venv/bin/python -u dev/scripts/test_lan_update_rates.py \
  --config dev/devices.yaml --mode together --rates 5 10 20 30 \
  --seconds 30 --trials 2 --rate-order alternate \
  --session-mode continuous --activation settled \
  --output lan-continuous.json 2>&1 | tee lan-continuous.log
```
Then compare startup at fixed 5 Hz:
```bash
for activation in legacy settled; do
  .venv/bin/python -u dev/scripts/test_lan_update_rates.py \
    --config dev/devices.yaml --mode together --rates 5 --seconds 15 \
    --trials 6 --session-mode restart --activation "$activation" \
    --output "lan-startup-$activation.json" \
    2>&1 | tee "lan-startup-$activation.log"
done
```
Record visual results per device/trial/rate. If needed, repeat activation order
reversed, or compare continuous legacy against continuous settled to separate
session persistence from setup changes. Only promote startup changes into normal
output after evidence; current experimental changes are in the test script.

## BLE investigation still pending

Update (2026-10-08): user accepts 0D and chooses a production BLE ceiling of
3 Hz. Floor Lamp passed 1 Hz for 90 seconds and 2–4 Hz short screens; 5 Hz
repeatedly lost the connection. Production cap/defaults/config are now 3 Hz;
explicit diagnostics can exceed the cap. User prefers 30-second screening
tests, with longer validation after a promising result.
User preference: validated LAN first, BLE fallback when local LAN fails or
becomes unreliable. LAN is local Wi-Fi, not the internet connection.
Explicit identity metadata and opt-in automatic selection/fallback are now
implemented; hardware fallback validation is pending. See
`dev/plans/lan-ble-transport-selection.md` for policy, limits and next Couch BLE test.

Couch BLE 3 Hz/30-second screen failed: two visible freezes, two disconnects and
write errors, 72 successful color writes (~2.40/sec), 3.43-second maximum gap.
The uniform 12-segment workload sends one color packet per frame. Couch remains
fixed LAN; no validated BLE fallback rate is asserted. Next: separate 1 and 2 Hz
30-second screens, then longer validation if a rate passes. The 3 Hz production
ceiling does not imply all BLE devices are reliable at that rate.

Latest decision (2026-10-07): retain `0D` as the accepted RGB command. The
comparison showed visible updates with 0D, but supplied telemetry still recorded
4 disconnects/errors and a 9.56-second maximum gap. Both 02 runs stayed connected
at ~4.90 host writes/sec but did not change colors. Do not adopt 02 or assume
older firmware explains this. User chose to proceed to output optimizations.
Ethernet is unavailable because apartment ports are damaged; use Wi-Fi/BLE.

Production optimizations now implemented: lazy persistent LAN UDP sockets,
local send errors propagated to health/return values, socket release on multi-
adapter deactivate/shutdown, async BLE queue polling (10 ms maximum poll delay),
and frame deadlines that include write time and skip overruns. RGB stays 0D.
Hardware validation is pending. See `dev/plans/output-optimization-results.md`.
The subsequent identity/selection implementation is opt-in and requires confirmed
endpoints plus validated rates. UDP send speed is not a reliability score.

Already implemented: newest-frame coalescing after rate-limit waits, shared
discovery cache and serialized connection setup, with discovered BLEDevice
objects. Counter handled ~18.6 host writes/sec at requested 20 without errors;
this was not visible-latency verification. H6006s/TV reconnect repeatedly even
at 5 Hz. One-bulb 90s trials: ~3.4 writes/sec, four disconnects, ~9.5s max gaps.
Nearby placement did not materially improve that result.

btmon showed connection timeout 0x08, 45 ms connection interval, Linux-requested
420 ms supervision timeout, and two lifetimes ~17.48s. Service Discovery errors
occur after loss, not proof of disabled radio. No parameter updates seen.

`test_ble_update_rates.py --session-variants baseline notify notify-query`
tested notifications and AA01 queries every ~2s during streaming. All variants
still had four disconnects/90s; subscription worked, query replies arrived.
This does not reproduce or rule out every Govee session requirement.

Next source-backed comparison (now implemented in the diagnostic script): DreamSync bulb RGB uses
`33 05 0D`, while the hardware-verified H6006 implementation below uses
`33 05 02`. Compare with everything else held constant before more guessed
keepalives. Reference also polls AA01/AA04/AA05. Do not blindly replace command
bytes across all models. I use iPhone and prefer existing open-source protocols
over mandatory capture or buying another controller.

Use `--bulb-color-command 0d|02` on an explicit H6006 with baseline session setup.
See `dev/scripts/ble-update-rates.md` for the 90-second, 5-Hz 0D/02/02/0D sequence.
Results: 0D changes colors but disconnects at 5 Hz; 02 stayed connected without
visible updates. Keep 0D. Do not repeat the comparison unless new evidence warrants it.

Sources:
- https://github.com/flippinhutt/govee-H6006-HA/blob/main/custom_components/govee_h6006/protocols/h6006.py
- https://github.com/flippinhutt/govee-H6006-HA/blob/main/custom_components/govee_h6006/api.py
- https://github.com/bochelork/govee_razer_led/blob/main/PROTOCOL.md
- https://github.com/Jaano/govee_lights/blob/master/GOVEE_BLE.md

Actual TV/HDMI Sync Center Movie DreamView is reliable in my setup. Its exact
center-to-follower transport has NOT been established; LAN desktop alternatives
aren't proof of that protocol.

## Remaining work after current results

1. Production persistent UDP sockets, truthful send-error reporting, BLE
   nonblocking queue waits and deadline pacing are implemented and locally
   benchmarked. Hardware results show 5 Hz BLE link loss persists.
2. BLE is capped at 3 Hz by user decision. Longer and fleet validation remain.
3. Identity unification and automatic transport selection/fallback are implemented
   as opt-in. Validate Couch BLE first, then controlled fallback. Keep configured
   LAN paths fixed until their paired BLE paths have dependable evidence.
4. Automatic return to LAN during a show and silent frame-loss detection remain
   outside the current conservative fallback policy.

Begin by checking the current branch and reading the supplied latest results.
Explain what the evidence supports and what remains uncertain, then carry out
the next justified implementation/test step. Do not redo solved discovery work.
