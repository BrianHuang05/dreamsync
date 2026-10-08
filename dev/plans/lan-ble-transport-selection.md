# LAN/BLE identity and selection — 2026-10-08

Production BLE output is capped at 3 Hz, including old configs requesting a
higher rate. Lower device limits are honored. All eight configured BLE devices
now request 3 Hz; the default and production BLE probes use the ceiling too.
Explicit `test_ble_update_rates.py` diagnostics can exceed it for investigation.
Bulb RGB remains 0D. This caps frame frequency, not every constituent packet;
brightness changes and segment frames can require additional writes.

Validation of the new 4 Hz rule: 395 integration tests passed, followed by 49
focused policy/routing tests including six added identity, session-wrapper and
handoff-recovery checks. No hardware validation of automatic switching has been
performed yet. See `lighting-remaining-tasks.md` for the current assessment.

## Identity and preference

One config entry now supports confirmed `device_id`, `lan_address`, and
`ble_address`. Its original `address` remains the stable renderer/spatial key.
The three LAN strip pairings already recorded in inventory comments are now
explicit config fields. No new lights or speculative H6006 LAN endpoints were
added. Existing placements, groups, enabled flags and notes are preserved.

The default `transport_policy: fixed` retains the existing configured path.
Opt-in `transport_policy: auto` builds one logical renderer and one transport
owner. It requires both confirmed endpoint addresses, physical LAN device ID,
explicit LAN encoder and BLE protocol, and at least one validated rate.
Duplicate identities/endpoints across entries are rejected before hardware I/O.

Automatic mode prefers validated LAN whenever available, with validated BLE
as fallback. `lan_validated_fps` and `ble_validated_fps` are hardware observations,
not values derived from UDP send or BLE write duration. Unknown/unvalidated
paths cannot be selected. Output honors the configured cap and validated rate;
BLE additionally stays below the global 3 Hz ceiling.

These LAN commands use local Wi-Fi and do not require internet access. Slow
internet or an internet outage alone does not trigger a switch. A local firewall,
Wi-Fi/router reachability problem, or unreliable local device responses can.

## Fallback behavior and limits

Activation verifies the physical ID in a direct LAN discovery reply before
sending lighting commands. Wrong/stale IP identity cannot receive automatic
LAN output. If LAN is unavailable, a validated BLE path can be started instead.
One path owns the light. The old path releases streaming/stops its writer before
the next starts. Failure to stop a BLE worker retains ownership and blocks a
second writer.

A background monitor checks every five seconds. During active LAN output, the
only LAN-to-BLE switching trigger is confirmed usable delivery below **4 Hz**
over three contiguous observation windows, each at least five seconds long.
Exactly 4 Hz retains LAN. A complete confirmed delivery failure counts as 0 Hz.
A brief dip or recovered window resets the streak. BLE fallback must already
have a validated rate and remains capped at 3 Hz.

Observation windows must describe active changing-frame demand of at least
4 Hz within the configured output cap; static scenes, deliberate low rates and
pauses are not link failures. Windows must have finite monotonic timestamps,
valid frame counts and a consistent source. Duplicate/overlapping windows,
gaps, stale data and prior-activation data cannot establish persistence.
The latest observation must end within the past five seconds. Identity and LAN
endpoint must match the active owner. Recovery arriving before handoff cancels
the switch without consuming the BLE fallback candidate.

`LanDeliverySample` in `output/lan_rate_policy.py` accepts `device_ack` or
`visual` frame-count evidence. Report it through
`SessionService.report_lan_delivery_sample(address, sample)` or the equivalent
multi-adapter API. Session and preview wrappers forward to the same owner.
Health snapshots expose the fresh delivery rate/source, or `unknown`.

Missing scan replies and local send errors still report LAN degradation, but
they no longer independently initiate a switch or get converted to a delivered
rate. Slow/inconsistent scan RTTs are not a throughput measurement. Query
exceptions report unavailable monitoring, not proof of device failure. LAN
query ownership is serialized within one process because replies target UDP
4002; unrelated processes must still be stopped.

**Current acquisition limitation:** no production component currently produces
confirmed LAN frame-delivery counts. The predicate and observation-routing API
are implemented, but exact live throughput switching awaits a dependable rate
source. UDP send success, scan response timing and renderer frame traces cannot
fill that gap. A device can reply while its streaming output is frozen. Unknown
delivery keeps LAN; do not enable switching by substituting host-send metrics.

Fallback is held for the remainder of the activation. Each route is attempted
once; there is no automatic return to LAN during a show. This prevents transport
flapping while the policy is being validated. A new activation evaluates LAN
again. BLE gets a connection-setup grace period before disconnected checks count.
If no validated fallback exists, health reports degradation and keeps the
existing owner's reconnect/recovery behavior.

## Current hardware evidence and accepted scope

Couch BLE 3 Hz screen (2026-10-08): user observed two freezes. Telemetry shows
72 successful color writes in 30 seconds (~2.40/sec), two write errors and two
disconnects, maximum gap 3.43 seconds. Reconnect setups were at ~11.37 and
24.37 seconds after measurement start. This is a failed fallback screen; retain
Couch on LAN and do not set `ble_validated_fps: 3.0` for it.
The solid-color workload groups all 12 segments into one color packet per frame,
so this failure cannot be attributed to 12 color packets per frame. The report's
`bulb_color_command: 0d` is unused for `protocol: segment`.

LAN strips were visually validated through 30 Hz. Couch, Blinds and Overhead
have `lan_validated_fps: 30.0`; their existing caps and fixed selection remain.
Their BLE command behavior and stability are not yet validated, so no
`ble_validated_fps` is asserted and automatic fallback is not enabled for them.
Floor Lamp H6006 passed 1 Hz for 90 seconds and 2–4 Hz short screens, with
repeated disconnects at 5 Hz. This evidence does not validate other strips' BLE.

User decision (2026-10-08): stop Couch BLE testing and proceed with its verified
LAN path. Lower-rate BLE screens are optional future work only if the user wants
a Couch BLE fallback; they are not prerequisites for using verified LAN devices.
Keep LAN strips on LAN and existing BLE-only devices capped at 3 Hz. The ceiling
does not imply every BLE model is reliable at that rate.

If a LAN path fails without a validated alternative, report degraded/unavailable
operation and allow LAN recovery. Do not silently select the failed Couch BLE
path or claim automatic fallback is hardware-verified. Identity metadata remains
useful without enabling alternate transport writers.

Next practical validation is ordinary DreamSync playback with the existing LAN
strips and BLE-only devices at the accepted caps. Observe fleet freezes and health
reporting; retain the established LAN/BLE assignments. Automatic fallback stays
opt-in, with hardware validation pending for any device that needs that feature.
