# Lighting task assessment — updated 2026-10-10

Latest user result: precompiled show playback through the Surface GUI passes
with no notable issues. Next: add a newly purchased device using the GUI, then
validate simultaneous Queue capture/playback. Use
`new-device-queue-testing-new-chat-prompt.md` for the current handoff sequence.

## Implemented

- Mixed-fleet LAN/BLE playback already existed. BLE production output, defaults,
  probes and eight configured BLE entries are now capped at 3 Hz; bulb RGB
  remains 0D. No new mixed-fleet playback implementation is needed.
- Persistent UDP sockets, truthful local send errors, nonblocking BLE queue
  waits and deadline pacing are implemented. Local socket overhead improved;
  these changes did not cure Floor Lamp's 5 Hz link loss.
- Benchmark host measurements are integrated into app runtime health: rolling
  send/write rate, write p95, maximum gap and errors. The health tooltip labels
  them host measurements with delivery unconfirmed. LAN counts frame sends;
  BLE counts color writes, which can exceed frame counts.
- Opt-in automatic mode unifies confirmed LAN/BLE endpoints under one renderer
  and one writer, prefers LAN and falls back to a validated BLE route after
  three contiguous >=5-second windows below 4 successful host frame sends/sec
  under >=4 Hz offered demand. Exactly 4 Hz passes; idle/low demand and pauses
  reset the streak. This uses host rate by the user's explicit choice.
- While BLE is active, paired LAN identity checks retry after a cooldown;
  three successful checks authorize a return-to-LAN trial. Cooldown starts at
  30 seconds and increases up to 120 seconds on failed recovery attempts.
  BLE stops before LAN starts; failed LAN activation restores BLE. Subsequent
  low LAN rate can trigger another fallback.
- Confirmed delivery observation APIs remain available separately. UDP success
  is never displayed as confirmed light delivery.

Verification: 394 affected integration tests passed, including runtime host
measurements, strict threshold decisions and recovery ownership/safety cases.
All hardware transports were mocked; no physical handoff is claimed verified.

## Remaining

1. Precompiled GUI playback has passed the user's functional/visual test.
   Configure the new device through the GUI and validate its inclusion next;
   then test simultaneous Queue capture/playback. Numerical host-health results
   and quantified long-run fleet reliability were not supplied. Existing LAN
   strips stay on LAN; no further Couch BLE screens are requested.
2. Automatic handoff/recovery needs hardware validation for any device whose
   alternate route is wanted and validated. Current LAN strips remain fixed:
   no accepted BLE fallback rate exists, and Couch BLE failed at 3 Hz. Automated
   state-machine tests do not establish physical handoff reliability.
3. Silent packet loss and device freezes remain undetectable when local UDP
   sends succeed. Host measurements cannot establish optical delivery or latency.
   A true device/visual counter would be future work, not a prerequisite for
   the user's selected host-rate rule.

## Known limits

- BLE link-loss cause remains unresolved. A 3 Hz frame cap is not a packet/sec
  budget: brightness and multicolor segment frames can add writes.
- Physical identity prevents output to a mismatched LAN IP; automatic DHCP
  endpoint refresh remains unimplemented.
- LAN uses local Wi-Fi. Internet outages/speed alone do not determine its health;
  Ethernet is unavailable due to damaged apartment ports.

Keep existing placements, groups, enabled flags, inventory notes and transport
assignments. Do not enable Couch's failed BLE path or claim hardware verification.
