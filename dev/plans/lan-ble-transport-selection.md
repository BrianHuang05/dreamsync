# LAN/BLE identity and selection — 2026-10-08

Production BLE output is capped at 3 Hz, including old configs requesting a
higher rate. Lower device limits are honored. All eight configured BLE devices
now request 3 Hz; the default and production BLE probes use the ceiling too.
Explicit `test_ble_update_rates.py` diagnostics can exceed it for investigation.
Bulb RGB remains 0D. This caps frame frequency, not every constituent packet;
brightness changes and segment frames can require additional writes.

Validation: 368 automated tests passed across transport policy, BLE/LAN output,
config/discovery, health, connection coordination, playback and sessions. No
hardware validation of automatic switching has been performed yet.

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

A background monitor checks every five seconds. Three consecutive missing
identity replies, replies slower than 200 ms, or changes between consecutive
reply RTTs larger than 100 ms trigger fallback. Three actual local send errors
also trigger fallback. A rate-limited frame is not a new error. Query exceptions
(such as failure to obtain a listener) report unavailable monitoring rather than
proving the device offline. LAN query ownership is serialized within one process
because all replies target UDP 4002; unrelated processes must still be stopped.

The response-time thresholds are conservative policy defaults awaiting hardware
validation. They measure local discovery reply consistency, not optical latency.
A device can reply while its streaming output is frozen; a firewall can allow
discovery yet block frame packets. Silent frame loss is not detected by these
checks. Reliable visible operation still requires hardware observation.

Fallback is held for the remainder of the activation. Each route is attempted
once; there is no automatic return to LAN during a show. This prevents transport
flapping while the policy is being validated. A new activation evaluates LAN
again. BLE gets a connection-setup grace period before disconnected checks count.
If no validated fallback exists, health reports degradation and keeps the
existing owner's reconnect/recovery behavior.

## Current hardware evidence and next test

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

With DreamSync/DreamView and Govee Home inactive, screen Couch's confirmed BLE
endpoint at 1 and 2 Hz in separate sessions. The global 3 Hz cap is a ceiling,
not a reliability guarantee for every model; a fallback may need a lower rate.

```bash
for rate in 1 2; do
  .venv/bin/python -u dev/scripts/test_ble_update_rates.py \
    --address DD:6E:05:86:6A:53 --protocol segment --segments 12 \
    --rates "$rate" --seconds 30 --session-variants baseline \
    --output "couch-ble-${rate}hz.json" 2>&1 | tee "couch-ble-${rate}hz.log"
done
```

Watch for correct color changes, freezes and jumps; share JSON/logs. A clean
short screen must be followed by longer validation before treating it as a
reliable fallback. Preserve the config until that evidence is available.

Once the BLE endpoint is visibly correct and stable at a tested rate, the same
Couch entry can opt in. Example below uses 1 Hz and is not yet validated:

```yaml
transport_policy: auto
protocol: segment
ble_validated_fps: 1.0
```

That entry already has the confirmed addresses and LAN ID/rate. Do not create
a second BLE entry for the same strip. No need to change Ethernet setup or buy
hardware. Controlled fallback verification remains pending hardware results.
