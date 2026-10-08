# Output optimization results — 2026-10-07

LAN adapters now reuse a lazily opened UDP socket for control and frame packets.
An OSError closes the failed socket, marks local send health as failed, and
returns False for frames or propagates for control commands. The next send can
reopen the socket. Multi-adapter deactivate/shutdown releases owned sockets;
injected transports stay caller-owned. Successful UDP sends remain unconfirmed
delivery, not evidence that a light is online.

BLE idle waits yield to asyncio every at most 10 ms instead of blocking the
event loop in Queue.get for 500 ms. Frame deadlines include write duration;
overruns skip slots, and frame selection still coalesces after pacing waits.
The accepted bulb RGB command remains 0D. Configuration and FPS caps are unchanged.

## Local measurements

Run `.venv/Scripts/python.exe dev/scripts/benchmark_udp_socket_reuse.py` on Windows
or `.venv/bin/python dev/scripts/benchmark_udp_socket_reuse.py` on the Surface.
Six alternating-order trials of 1,000 loopback sends with 300-byte payloads:

| Transport | Median host time per send |
|---|---:|
| Per-packet socket | 184.33 microseconds |
| Persistent socket | 11.12 microseconds |

This measures socket/send overhead only. The loopback receive buffer may drop
packets. It does not benchmark Wi-Fi, lights, delivery or visible latency.

Deterministic tests exercise the production BLE loop at a 5 Hz cap with a
simulated 40 ms write: starts remain 200 ms apart (previous completion-based
pacing would require 240 ms). A 250 ms write skips to the next available slot,
giving 400 ms start gaps without catch-up bursts. A real asyncio callback queues
an idle frame after 20 ms and the waiter completes within 150 ms; cancellation
also completes. These are simulated I/O checks, not hardware throughput claims.

167 focused tests passed across LAN, BLE, connection coordination, diagnostic
script and new optimization tests.
On 2026-10-08, 107 additional device-health, playback and session integration
tests passed, bringing verification to 274 tests.

## Next hardware check

Keep other lighting controllers inactive. On the updated Surface checkout:

```bash
.venv/bin/python -u dev/scripts/test_ble_update_rates.py \
  --address D0:C9:07:95:15:DB --protocol bulb \
  --rates 5 --seconds 90 --session-variants baseline \
  --bulb-color-command 0d --output bulb-optimized-5hz.json \
  2>&1 | tee bulb-optimized-5hz.log
```

Compare visible behavior, disconnect count, errors, write throughput and maximum
gap against the supplied baseline (305 writes, ~3.39 writes/sec, 4 disconnects,
4 errors, 9.56-second maximum gap). Repeat before drawing reliability conclusions.
The LAN rate diagnostic already used a persistent socket, so it cannot by itself
measure the production change; validate LAN through normal DreamSync playback.
Ethernet is unavailable. LAN means the existing Wi-Fi path in these tests.

Identity unification and automatic transport choice are not implemented by this
change. They require confirmed LAN/BLE endpoint pairing, comparable stability
measurements and fallback behavior that prevents competing writers or repeated
transport switching. Host UDP timings alone cannot choose a reliable transport.
