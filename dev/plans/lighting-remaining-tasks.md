# Lighting task assessment — 2026-10-08

## Completed implementation

| Work | Status and evidence |
|---|---|
| LAN packet encoder selection | Razer visually correct on Couch/Blinds/Overhead; LAN strips validated through 30 Hz. |
| LAN discovery/firewall diagnosis | Resolved incoming UDP 4002 block; do not repeat LAN-support investigation. |
| Persistent production UDP sockets | Implemented, lifecycle/error recovery tested; local median send overhead 184.33 to 11.12 microseconds. This is host overhead, not device latency. |
| Truthful local UDP errors | Frame sends return False on local failure; control sends propagate errors and update health. |
| BLE queue waits and pacing | Event loop yields while idle; newest-frame coalescing, deadlines including write duration and overrun skipping implemented. |
| BLE production ceiling | 3 Hz across defaults, production writer/probes and eight configured BLE entries; diagnostic sweeps can exceed it explicitly. Bulb RGB remains 0D. |
| Physical identity metadata | Confirmed LAN ID/IP/BLE address on three strip entries, stable renderer key, duplicate ownership rejection, config/editor metadata preservation. |
| LAN-first selection and ownership | Opt-in, validated routes only, stop/release old writer before new writer, no repeated transport flapping within activation. |
| Sustained below-4-Hz policy | Implemented: three contiguous >=5-second windows of confirmed delivery <4 Hz under >=4 Hz demand; 4 Hz passes. Freshness, idle/pause, identity/session, recovery and malformed-evidence protections tested. |
| Delivery observation routing | Session, preview and multi-adapter interfaces route `LanDeliverySample` to the owning light; health exposes rate/source or unknown. |

Verification: 395 integration tests passed, then 49 focused threshold/routing
tests passed including six additional integration/boundary checks. Automatic
transport handoff has not been hardware-verified.

## Remaining required validation and measurement

1. **Normal mixed-fleet playback at the accepted caps.** This is the immediate
   practical next step: existing LAN strips on LAN, BLE-only devices at <=3 Hz.
   Floor Lamp short-rate screens do not validate the entire fleet or dynamic
   brightness/multicolor effects. Record freezes and output health during normal
   use. No new Couch BLE rate tests are requested.
2. **Dependable LAN delivery-rate acquisition before enabling the exact switching
   rule.** Current protocol/runtime code has no per-frame delivery acknowledgments
   or confirmed visible frame counter. Review existing protocol response/counter
   capabilities first. Do not infer delivery from send speed, discovery RTT or
   producer output frequency, or require new sniffing/hardware as the first step.
   The policy is ready to consume confirmed observations, but acquisition is
   unimplemented. A missing rate remains unknown, not 0 Hz.
3. **Hardware handoff verification only for a device with a wanted, validated
   alternate path.** Current LAN strips have no accepted BLE fallback. Couch
   BLE failed at 3 Hz and the user declined further testing. Keep those routes
   fixed; report degradation when no validated alternative exists. A validated
   alternative is necessary before claiming automatic switching works in practice.

## Deferred scope and known limits

- Root cause of BLE link loss is unresolved. Output optimizations did not cure
  Floor Lamp's 5 Hz failures. Couch's 3 Hz failure shows the ceiling is not a
  universal reliability guarantee; its BLE investigation is out of current scope.
- A 3 Hz frame cap is not a 3-packet/sec budget. Brightness changes and segment
  effects can add writes; further packet-budget work depends on normal-use results.
- LAN recovery during a show does not automatically switch back from BLE. A new
  activation prefers LAN again; live return with hysteresis remains unimplemented.
- LAN IP refresh following DHCP changes is manual. Explicit physical ID prevents
  automatic output to a mismatched IP; automatic endpoint refresh remains future work.
- Discovery success does not prove frame delivery, so silent frame-loss detection
  remains missing until a delivery observer is available.
- Ethernet is unavailable due to damaged apartment ports. LAN here uses local
  Wi-Fi; internet outages/speed alone are not transport-switch conditions.

Keep existing layout, groups, enabled flags, inventory notes and the accepted
transport assignments. Do not label the whole reliability investigation resolved
or enable unvalidated alternatives because policy unit tests pass.
