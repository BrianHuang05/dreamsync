# Runtime Device Health Indicator

## Goal

Make hardware connectivity visible while any DreamSync output session is
playing, without treating the absence of a protocol ACK as proof of failure.

## Design

- Keep the detailed configured-device rows in the existing Devices/Config
  health surface; discovery remains a separate manual operation. Populate the
  Devices table from configured devices when needed, then update its Status
  cells without rescanning.
- Add a compact, permanent status-bar summary visible from every tab while
  hardware output is active.
- Merge passive LAN probes with the active output adapter's observations:
  BLE uses its existing `connected` and reconnect-attempt state; LAN exposes
  local send errors only and remains `unknown` when no stronger probe result
  is available.
- Refresh at the ordinary cadence when idle and at a short bounded cadence
  during active hardware playback. Health failure must never stop playback or
  create a second BLE connection/probe loop.

## Acceptance criteria

1. Playback from Shows and Live displays the same global hardware summary.
2. The detailed device-health label updates without a manual discovery scan.
3. A disconnected/reconnecting BLE follower is reported degraded/offline from
   its existing worker state.
4. LAN transport failures are reported as degraded without claiming confirmed
   device receipt.
5. Regression tests cover active adapter observations and the health merge.
