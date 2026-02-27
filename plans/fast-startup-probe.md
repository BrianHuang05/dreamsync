# Fast Startup Probe

Reduce session startup time from ~3+ minutes to ~2-4 seconds by probing devices in parallel and using fewer packets.

**Current state:** `detect_all_devices()` probes each device **sequentially** with **100 packets at 5 Hz** (20 seconds per device). With 10 devices, startup takes ~200 seconds — mostly wasted, since LAN devices are always classified `realtime` and BLE latency stabilizes within ~10 packets.

---

## Problem Breakdown

| Bottleneck | Current | Why it's wasteful |
|---|---|---|
| LAN probe count | 100 packets × 5 Hz = 20s each | LAN role is hardcoded `realtime` — probe only confirms reachability |
| BLE probe count | 100 packets × 5 Hz = 20s each | Median latency stabilizes after ~10 packets (measured 4-5ms median) |
| BLE connect overhead | ~2-5s per device | Each BLE device requires full GATT connect/disconnect |
| Sequential execution | Total = N × 20s | Devices are independent — no reason to probe one at a time |
| LAN port contention | All LAN probes share listener port | `probe_lan_device` binds to `LISTEN_PORT` — concurrent LAN probes would conflict |

---

## Solution

### Step 1 — Reduce packet counts

**File:** `src/dreamsync/output/auto_detect.py`

Change `detect_all_devices()` to use different packet counts by device type:

```python
def detect_all_devices(
    configs: list[DeviceConfig],
    num_packets: int = 100,       # legacy default (unused below)
    rate_hz: float = 5.0,
    *,
    lan_packets: int = 5,         # reachability check only
    ble_packets: int = 10,        # enough for stable median
    lan_rate_hz: float = 20.0,    # faster probe rate for LAN (UDP is cheap)
    ble_rate_hz: float = 10.0,    # BLE writes are ~4ms, 10 Hz is safe
) -> list[DetectedDevice]:
```

**Timing per device after this change:**

| Type | Packets | Rate | Time |
|---|---|---|---|
| LAN | 5 | 20 Hz | **0.25s** (was 20s) |
| BLE | 10 | 10 Hz | **1.0s** + connect (~3s) = **~4s** (was 23s) |

This is safe because:
- LAN devices are always `realtime` — 5 packets is enough to confirm the device responds. The health monitor re-probes at runtime with 5 packets and that works fine.
- BLE classification thresholds are 20ms and 200ms. Measured BLE latency is 4-5ms with very low variance. 10 samples give a reliable median well within those thresholds.
- `--probe-packets` CLI flag is preserved for users who want the full 100-packet deep probe.

### Step 2 — Parallel probing with ThreadPoolExecutor

**File:** `src/dreamsync/output/auto_detect.py`

Probe all devices concurrently using `concurrent.futures.ThreadPoolExecutor`:

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def detect_all_devices(
    configs: list[DeviceConfig],
    ...
    parallel: bool = True,
) -> list[DetectedDevice]:
    if not parallel or len(configs) <= 1:
        return _detect_sequential(configs, ...)

    return _detect_parallel(configs, ...)
```

#### LAN parallel probing — port contention fix

`probe_lan_device` binds to a fixed `LISTEN_PORT` for receiving responses. Two concurrent LAN probes would fight over the same port. Two options:

**Option A — Single shared listener (recommended):**

Open one listener socket before the thread pool, pass it to all LAN probe calls. Each probe sends to a different IP and filters responses by source address (already done on line 165). UDP responses from different devices arrive on the same socket — the existing `addr[0] == ip` filter correctly routes them.

```python
def _probe_lan_batch(
    ips: list[str],
    num_packets: int = 5,
    rate_hz: float = 20.0,
) -> dict[str, LatencyStats]:
    """Probe multiple LAN devices concurrently using a single shared listener."""
    from dreamsync.output.discovery import _SCAN_MSG, LISTEN_PORT, MCAST_PORT

    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("", LISTEN_PORT))
    listener.settimeout(0.5)

    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)

    samples: dict[str, list[float]] = {ip: [] for ip in ips}
    interval = 1.0 / max(0.1, rate_hz)

    try:
        for _ in range(num_packets):
            # Send to all devices in rapid succession
            send_times: dict[str, float] = {}
            for ip in ips:
                send_times[ip] = time.monotonic()
                sender.sendto(_SCAN_MSG, (ip, MCAST_PORT))

            # Collect responses (wait up to timeout for all)
            deadline = time.monotonic() + 0.5
            received: set[str] = set()
            while len(received) < len(ips) and time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                listener.settimeout(remaining)
                try:
                    _data, addr = listener.recvfrom(4096)
                    resp_ip = addr[0]
                    if resp_ip in send_times and resp_ip not in received:
                        rtt = (time.monotonic() - send_times[resp_ip]) * 1000.0
                        samples[resp_ip].append(rtt)
                        received.add(resp_ip)
                except socket.timeout:
                    break

            # Rate limit between rounds
            time.sleep(max(0, interval - (time.monotonic() - min(send_times.values()))))
    finally:
        sender.close()
        listener.close()

    return {ip: LatencyStats(samples=s) for ip, s in samples.items()}
```

This probes all LAN devices in a single pass — N devices × 5 packets takes the same time as 1 device × 5 packets (~0.25s total).

**Option B — Ephemeral ports:**

Each thread binds to port 0 (OS-assigned). Simpler per-thread code but requires the device to respond to the sender's ephemeral port rather than `LISTEN_PORT`. This depends on Govee firmware behavior and may not work — Option A is safer.

#### BLE parallel probing

BLE probes are already self-contained (each creates its own `BleakClient`). They can run in parallel via the thread pool with no shared state:

```python
with ThreadPoolExecutor(max_workers=min(len(ble_configs), 4)) as pool:
    futures = {
        pool.submit(probe_ble_device, cfg.address, cfg.protocol, ble_packets, ble_rate_hz): cfg
        for cfg in ble_configs
    }
    for future in as_completed(futures):
        cfg = futures[future]
        stats = future.result()
        ...
```

Cap at 4 concurrent BLE connections to avoid Bluetooth adapter saturation (Windows BLE stack handles ~4-7 concurrent connections reliably).

### Step 3 — Orchestrate LAN batch + BLE parallel

```python
def _detect_parallel(configs, ...):
    lan_configs = [c for c in configs if is_lan(c)]
    ble_configs = [c for c in configs if is_ble(c)]

    # Phase 1: LAN batch (single-socket, ~0.25s for all LAN devices)
    lan_results = {}
    if lan_configs:
        lan_ips = [c.address for c in lan_configs]
        lan_results = _probe_lan_batch(lan_ips, lan_packets, lan_rate_hz)

    # Phase 2: BLE parallel (thread pool, ~4s limited by slowest connection)
    ble_results = {}
    if ble_configs:
        with ThreadPoolExecutor(max_workers=min(len(ble_configs), 4)) as pool:
            futures = {
                pool.submit(probe_ble_device, c.address, c.protocol, ble_packets, ble_rate_hz): c
                for c in ble_configs
            }
            for future in as_completed(futures):
                cfg = futures[future]
                try:
                    ble_results[cfg.address] = future.result()
                except Exception as exc:
                    _logger.warning("BLE probe failed for %s: %s", cfg.address, exc)
                    ble_results[cfg.address] = LatencyStats(samples=[])

    # Phase 3: Classify and build DetectedDevice list (preserves config order)
    detected = []
    for cfg in configs:
        if cfg.address in lan_results:
            stats = lan_results[cfg.address]
            ...
        elif cfg.address in ble_results:
            stats = ble_results[cfg.address]
            ...
        else:
            # unreachable
            ...
    return detected
```

LAN and BLE phases can also overlap (run LAN batch in a thread while BLE pool runs), but the LAN batch is so fast (~0.25s) that the added complexity isn't worth it.

---

## Step 4 — CLI integration

**File:** `src/dreamsync/cli.py`

The existing `--probe-packets` and `--probe-rate` flags on the `session` subcommand become the **override** for deep probing. Default startup uses the fast path:

```python
session.add_argument(
    "--probe-packets", type=int, default=None,
    help="Override probe packet count (default: 5 LAN, 10 BLE). Set to 100 for deep probe.",
)
session.add_argument(
    "--no-parallel-probe", action="store_true", default=False,
    help="Disable parallel device probing (probe sequentially).",
)
```

When `--probe-packets` is explicitly set, it overrides both `lan_packets` and `ble_packets` (backward-compatible behavior). When unset, the fast defaults apply.

---

## Step 5 — Tests

**File:** `tests/test_auto_detect.py`

| Test | What it validates |
|---|---|
| `test_probe_lan_batch_all_respond` | All IPs get latency samples from shared socket |
| `test_probe_lan_batch_one_unreachable` | Unreachable IP gets empty samples, others unaffected |
| `test_probe_lan_batch_filters_by_ip` | Response from IP A not attributed to IP B |
| `test_detect_parallel_matches_sequential` | Same classification results in parallel vs sequential mode |
| `test_ble_parallel_max_workers` | Thread pool caps at 4 workers |
| `test_ble_probe_failure_isolated` | One BLE failure doesn't crash other probes |
| `test_fast_defaults_lan` | Default LAN probe uses 5 packets at 20 Hz |
| `test_fast_defaults_ble` | Default BLE probe uses 10 packets at 10 Hz |
| `test_probe_packets_override` | `--probe-packets 100` overrides both LAN and BLE counts |

All tests use mock sockets and mock `BleakClient` — no real hardware.

---

## Expected Timing

| Fleet | Before | After |
|---|---|---|
| 2 LAN only | 40s | 0.25s |
| 2 LAN + 8 BLE | 200s | ~4s (BLE connect is the bottleneck) |
| 2 LAN + 8 BLE (1 unreachable) | 210s+ (10s BLE timeout) | ~10s (timeout runs in parallel) |

---

## Implementation Order

| Phase | What | Files |
|---|---|---|
| 1 | `_probe_lan_batch()` function | `auto_detect.py` |
| 2 | Reduce default packet counts | `auto_detect.py` |
| 3 | `_detect_parallel()` with ThreadPoolExecutor for BLE | `auto_detect.py` |
| 4 | Wire into `detect_all_devices()` with `parallel=True` default | `auto_detect.py` |
| 5 | CLI flag updates (`--probe-packets` default None, `--no-parallel-probe`) | `cli.py` |
| 6 | Tests | `tests/test_auto_detect.py` |
| 7 | Update `session.py` to pass new params | `session.py` |

---

## What this does NOT cover

- **Caching probe results across sessions** — Possible future optimization: write last-known latency/role to `devices.yaml` or a sidecar file, skip probing devices that were recently seen. Adds file I/O complexity and staleness risk. The parallel fast probe is fast enough that caching isn't needed yet.
- **BLE connection pooling** — Keeping BLE connections open across probe and runtime. The BLE adapter already handles its own connection lifecycle; changing that is a larger refactor.
- **Adaptive probe count** — Probe more packets if variance is high. Over-engineered for the current fleet; the health monitor handles runtime reclassification.
